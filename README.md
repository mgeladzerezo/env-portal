# Environment portal

A self-service portal for short-lived environments. A developer picks a template
(VM + database, static site, or Kubernetes namespace), a size and a time to live, sees a
cost estimate, and confirms. The portal runs Terraform for them, streams the output to
the browser, and destroys the environment when its TTL runs out.

## Architecture

```
browser ── nginx (static files, /api proxy)
              │
           FastAPI ──────────── PostgreSQL (environments, operations, log_lines)
              │ enqueue              ▲
            Redis                    │ status, outputs, log lines
              │                      │
        Celery worker ── terraform init / plan / show / apply / destroy
              │              │
        Celery beat      one generated root module per environment, calling a
        (maintenance)    versioned module from modules/, state in S3 under
                         environments/<id>/terraform.tfstate, lock in DynamoDB
```

- **API** (`backend/portal/api.py`) validates requests, writes an `operations` row and
  hands its id to the queue. It never runs Terraform.
- **Worker** (`backend/portal/services/executor.py`) claims the operation, renders a root
  module for the environment (`terraform/workspace.py`), and runs Terraform as a
  subprocess (`terraform/subprocess_runner.py`). Each output line is committed to
  `log_lines` as it is printed.
- **Maintenance** (`services/maintenance.py`, triggered by Celery beat every minute) does
  three things: fails operations whose worker stopped heart-beating, re-sends operations
  that were never picked up, and queues a destroy for every expired environment.
- **Log streaming** (`services/streaming.py`) is server-sent events. The endpoint polls
  the database, so any API replica can serve any stream.
- **Frontend** is a thin React client. Its types are generated from the API's OpenAPI
  document (`npm run gen:api`), and CI fails when they drift.

### Environment state machine

| From | Allowed next states |
| --- | --- |
| `requested` | `planning`, `destroying` |
| `planning` | `applying`, `blocked_by_policy`, `failed` |
| `blocked_by_policy` | `planning` (retry), `destroying` |
| `applying` | `ready`, `failed` |
| `ready` | `destroying` |
| `failed` | `planning` (retry), `destroying` |
| `destroying` | `destroyed`, `failed` |
| `destroyed` | none |

The table lives in `backend/portal/state_machine.py`; anything else raises
`IllegalTransition` (HTTP 409 at the API). Every state can still reach `destroyed`. Retrying an
environment whose last failure was a destroy queues another destroy.

### How operations stay safe

| Concern | Mechanism |
| --- | --- |
| Two operations on one environment | Partial unique index on `operations(environment_id) WHERE status IN ('queued','running')`. The second request gets `409`. They are never queued behind each other. |
| Task delivered twice | The worker claims an operation with `UPDATE ... WHERE status = 'queued'`. The second delivery updates zero rows and returns. |
| Worker dies mid-run | Log writes refresh `heartbeat_at`. Maintenance fails operations with a stale heartbeat and moves the environment to `failed`. |
| A "dead" worker comes back | Every status change first runs `UPDATE operations ... WHERE status = 'running'`. If that touches no row the worker drops its result. |
| Broker down at request time | The row is committed first; maintenance re-sends operations that stay `queued` too long. |
| Stuck Terraform state lock | Terraform runs with `-lock-timeout`. A lock error is parsed and stored on the environment (id, holder, time) and the operation fails with `state_locked`. The portal never runs `force-unlock` on its own; a user has to call `POST /unlock` with the exact lock id. |
| Failed apply | State keeps what was created. Retry plans against it and applies the rest; destroy removes it. |
| Plan vs. apply drift | The worker applies the saved plan file, i.e. exactly what the policy check saw. |
| Double form submit | `Idempotency-Key` header; the same key returns the first environment. |

### TTL reaper

`expires_at` is fixed at request time (`created_at + ttl_hours`). On each pass the reaper
looks at every expired environment that is not destroyed or being destroyed:

- operation in flight (queued, planning, applying): **deferred**, reaped on a later pass;
- otherwise a destroy operation is queued with `requested_by = reaper`;
- after three failed reaper destroys it **gives up** and leaves the environment in
  `failed` for a person. `portal_environments_overdue` on `/metrics` counts these.

### Templates and IAM

```
modules/<template>/v1/                the module environments are created from
modules/<template>/provisioner-role/  the IAM role the worker assumes for that template
modules/local-demo/v1/                terraform_data only, for local runs and tests
```

Every module takes `environment_id`, `name`, `owner`, `expires_at`, `size` and tags its
resources with `owner`, `expires-at` and `portal-environment-id` (labels and annotations
for Kubernetes). An environment records the module version it was created with; retries
and destroys keep using that version.

The worker pod's own role (IRSA, `deploy/terraform/iam.tf`) can only read and write
`environments/*` in the state bucket, use the lock table and assume the three
provisioner roles. Each generated root module sets `assume_role` to its template's role,
so a static-site run cannot touch EC2 or RDS. The provisioner policies require the
`portal-template` tag on create and on modify/delete.

### Policy and cost

Both sit behind small interfaces (`policy/base.py`, `cost/base.py`).

- Policy: `ConftestPolicyChecker` runs `conftest` with the Rego rules in `policies/`
  against the plan JSON. `BuiltinPolicyChecker` implements the same four rules in Python
  for setups without conftest. A violation moves the environment to `blocked_by_policy`
  and nothing is applied. A checker that crashes counts as "not approved".
- Cost: `InfracostEstimator` renders the root module and runs `infracost breakdown`;
  `StaticCostEstimator` uses fixed hourly prices from `catalog.py`.

## Running locally

### Without Docker (API only, no Redis)

Needs Python 3.12, Node 22+ and a `terraform` binary (1.9 or newer).

```sh
cd backend
python -m venv .venv && . .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

export PORTAL_QUEUE_BACKEND=inline              # operations run on threads in the API
export PORTAL_ENABLE_DEMO_TEMPLATE=true         # adds the terraform_data template
export PORTAL_DEV_USER=dev@example.com
export PORTAL_TERRAFORM_BIN=terraform
alembic upgrade head                            # SQLite file ./portal.db by default
uvicorn portal.main:app_factory --factory --port 8000

cd ../frontend
npm ci
npm run dev                                     # proxies /api to localhost:8000
```

Request a "Local demo" environment to watch a real plan/apply/destroy without any cloud
account. TTL expiry is not enforced in this mode because nothing runs the maintenance
task; that needs Celery beat.

### With Docker Compose

```sh
docker compose up --build
```

Starts PostgreSQL, Redis, a migration job, API, worker, beat and the frontend on
http://localhost:8080. State uses the local backend on a volume. The AWS templates are
listed but need credentials and `PORTAL_TEMPLATE_INPUTS` to apply.

### Configuration

All settings are `PORTAL_*` environment variables, defined in
`backend/portal/config.py`. The ones that matter in production:

| Variable | Purpose |
| --- | --- |
| `PORTAL_DATABASE_URL`, `PORTAL_CELERY_BROKER_URL` | PostgreSQL and Redis |
| `PORTAL_STATE_BACKEND=s3`, `PORTAL_STATE_BUCKET`, `PORTAL_STATE_LOCK_TABLE` | remote state |
| `PORTAL_PROVISIONER_ROLE_ARNS` | JSON map template -> role ARN |
| `PORTAL_TEMPLATE_INPUTS` | JSON map template -> platform-owned module variables (VPC, subnets, instance profile) |
| `PORTAL_EKS_CLUSTER_NAME` | cluster for the k8s-namespace template |
| `PORTAL_POLICY_CHECKER=conftest`, `PORTAL_COST_ESTIMATOR=infracost` | real checkers |
| `PORTAL_USER_HEADER` | header carrying the authenticated user |

## Tests

```sh
cd backend
ruff check . && ruff format --check .
pytest                      # SQLite, fake Terraform runner, fake clock; no services needed
TERRAFORM_BIN=/path/to/terraform pytest tests/test_integration_terraform.py

cd frontend
npm run lint && npm run typecheck && npm run test && npm run build
```

- `test_state_machine.py`: all 64 state pairs against a hand-written list of allowed ones.
- `test_operations.py`: failed apply then retry or destroy, stuck lock, duplicate
  delivery, concurrent requests and workers, lost worker, re-queue.
- `test_reaper.py`: TTL handling with a fake clock, including expiry mid-plan and
  mid-apply.
- `test_logs.py`: ordering, gapless sequence numbers, resume, and a stream that follows
  an operation while it runs.
- `test_integration_terraform.py`: the real runner against `modules/local-demo`
  (apply, outputs, idempotent re-plan, destroy) and the whole portal flow with the real
  binary. Skipped unless `TERRAFORM_BIN` is set or `terraform` is on `PATH`.

Infrastructure checks:

```sh
terraform fmt -check -recursive modules deploy/terraform
terraform -chdir=<dir> init -backend=false && terraform -chdir=<dir> validate
helm lint deploy/helm/env-portal --strict -f deploy/helm/env-portal/ci/test-values.yaml
helm template portal deploy/helm/env-portal -f deploy/helm/env-portal/ci/test-values.yaml
```

## Deploying

1. `deploy/terraform`: state bucket and lock table, ECR repositories, the worker role,
   the three provisioner roles and an optional GitHub Actions role for pushing images.
   Copy `backend.hcl.example` and `terraform.tfvars.example`, then
   `terraform init -backend-config=backend.hcl && terraform apply`.
2. Create a secret named `env-portal` with `database-url` and `broker-url`.
3. `helm upgrade --install portal deploy/helm/env-portal -n env-portal -f values.yaml`
   with the Terraform outputs filled in (`config.stateBucket`, `config.stateLockTable`,
   `config.provisionerRoleArns`, `worker.serviceAccount.roleArn`).

The chart runs migrations as a pre-upgrade hook, gives only the worker a service account
with an IAM role, and runs exactly one beat pod.

CI (`.github/workflows/ci.yml`): ruff and pytest, ESLint/tsc/vitest/build, an OpenAPI
drift check, `terraform validate` per module, `helm lint`, a Trivy config scan, then
image builds with a Trivy image scan and a push to ECR from `main`.

## Decisions and trade-offs

- **Reject instead of queue.** A second operation on a busy environment gets a 409
  instead of waiting in line. It is easier to reason about, and the UI can simply
  disable the buttons. The reaper copes by deferring.
- **Database polling for SSE** instead of Redis pub/sub. Half a second of latency, but
  no ordering problems, resume by sequence number comes for free, and the log is durable.
  One commit per log line is fine at Terraform's output rate; it would not be for a
  chatty build log.
- **Metrics from the database.** Celery prefork workers cannot share in-process counters
  with the API, so `/metrics` computes environment and operation gauges with two
  `GROUP BY` queries per scrape.
- **Terraform 1.9 and a DynamoDB lock table.** Tests and the worker image use 1.9.8.
  From 1.10 on the S3 backend can lock by itself (`use_lockfile`), which would remove
  the table.
- **TTL counts from the request**, not from "ready", so the deadline is known up front.
- **Two implementations of the policy rules** (Rego and Python) can drift. The Python
  one exists so the portal is usable without conftest.
- **Generated JSON root module** per environment instead of Terraform workspaces: the
  backend key, provider role and module version differ per environment, and a plain
  directory is easy to inspect when something goes wrong.

## Known limitations

- No authentication of its own. The API trusts a header set by a proxy
  (`PORTAL_USER_HEADER`), and any authenticated user can act on any environment.
- The portal's PostgreSQL and Redis are not created by `deploy/terraform`.
- The AWS modules, the IAM policies, the Rego rules, the Dockerfiles, the compose file
  and the GitHub workflow have not been run against real services; see the table below.
- Tests use SQLite. The queries and the migration are written for PostgreSQL too
  (partial index, `FOR UPDATE SKIP LOCKED`) but have not run against it.
- The maintenance task is not leader-elected; the chart runs one beat pod instead.
- No TTL extension, no per-user quotas, no pagination on the environment list, and the
  log table is never pruned.
- A Terraform command that hits `command_timeout_seconds` is killed and may leave a
  state lock behind; that surfaces as `state_locked` on the next run.
- Static cost figures are rough on-demand prices, not quotes.

### What was verified where

| Area | Checked with |
| --- | --- |
| Backend logic | pytest (SQLite, fakes) |
| Real Terraform subprocess path | integration tests with Terraform 1.9.8, `terraform_data`, local state |
| API + SSE end to end | manual run of the API with the inline queue and the demo template |
| Modules, provisioner roles, `deploy/terraform`, generated root modules | `terraform fmt -check`, `terraform validate` (never planned or applied) |
| Helm chart | `helm lint --strict`, `helm template` |
| Rego policies, conftest and infracost invocations | not executed; the Python wrappers are tested against canned tool output |
| Dockerfiles, docker-compose, GitHub Actions | written, not built or run |
