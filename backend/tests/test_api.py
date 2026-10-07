from pathlib import Path

from fastapi.testclient import TestClient

from portal.container import build_container
from portal.db import Base
from portal.main import create_app
from portal.terraform.base import LockInfo
from tests.conftest import USER, Portal, make_settings
from tests.fakes import FakeTerraformRunner, RecordingQueue

REQUEST = {"name": "checkout-pr-42", "template": "vm-database", "size": "medium", "ttl_hours": 8}


def create(client: TestClient, **overrides) -> dict:
    response = client.post("/api/environments", json={**REQUEST, **overrides})
    assert response.status_code == 201, response.text
    return response.json()


def test_templates_lists_the_three_cloud_templates(client: TestClient) -> None:
    templates = {template["name"]: template for template in client.get("/api/templates").json()}

    assert {"vm-database", "static-site", "k8s-namespace"} <= set(templates)
    assert [size["name"] for size in templates["vm-database"]["sizes"]] == [
        "small",
        "medium",
        "large",
    ]
    assert templates["vm-database"]["max_ttl_hours"] == 168


def test_estimate_multiplies_the_hourly_price_by_the_ttl(client: TestClient) -> None:
    response = client.post(
        "/api/estimates", json={"template": "vm-database", "size": "small", "ttl_hours": 10}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["hourly_usd"] == 0.25
    assert body["total_usd"] == 2.5
    assert body["source"] == "fake"
    assert body["items"] == [{"name": "vm-database/small", "hourly_usd": 0.25}]


def test_create_queues_a_provision_operation(client: TestClient, portal: Portal) -> None:
    body = create(client)

    assert body["status"] == "requested"
    assert body["owner"] == USER
    assert body["module_version"] == "v1"
    assert body["hourly_cost_usd"] == 0.25
    assert body["active_operation"]["kind"] == "provision"
    assert body["active_operation"]["status"] == "queued"
    assert portal.queue.pending == [body["active_operation"]["id"]]
    expires_in = portal.env(body["id"]).expires_at - portal.clock.now()
    assert expires_in.total_seconds() == 8 * 3600


def test_created_environment_becomes_ready_once_the_worker_ran(
    client: TestClient, portal: Portal
) -> None:
    environment_id = create(client)["id"]
    portal.run_pending()

    body = client.get(f"/api/environments/{environment_id}").json()

    assert body["status"] == "ready"
    assert body["active_operation"] is None
    assert body["last_operation"]["status"] == "succeeded"
    assert body["outputs"] == {"resources": ["network", "compute", "database"]}


def test_invalid_requests_are_rejected_before_anything_is_queued(
    client: TestClient, portal: Portal
) -> None:
    invalid = [
        {"template": "mainframe"},
        {"size": "gigantic"},
        {"ttl_hours": 0},
        {"ttl_hours": 169},
        {"name": "Has Spaces"},
        {"name": "-leading-dash"},
    ]
    for overrides in invalid:
        response = client.post("/api/environments", json={**REQUEST, **overrides})
        assert response.status_code == 422, overrides

    assert portal.queue.enqueued == []
    assert client.get("/api/environments").json() == []


def test_requests_without_an_identity_are_rejected(tmp_path: Path) -> None:
    container = build_container(
        make_settings(tmp_path, dev_user=None),
        runner=FakeTerraformRunner(),
        queue=RecordingQueue(),
    )
    Base.metadata.create_all(container.engine)
    anonymous = TestClient(create_app(container))

    assert anonymous.get("/api/environments").status_code == 401
    assert anonymous.post("/api/environments", json=REQUEST).status_code == 401
    authenticated = anonymous.get("/api/environments", headers={"X-Forwarded-User": "bob"})
    assert authenticated.status_code == 200
    container.engine.dispose()


def test_repeating_a_request_with_the_same_idempotency_key_creates_nothing_new(
    client: TestClient, portal: Portal
) -> None:
    headers = {"Idempotency-Key": "form-submit-1"}
    first = client.post("/api/environments", json=REQUEST, headers=headers)
    second = client.post("/api/environments", json=REQUEST, headers=headers)

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    assert len(portal.queue.enqueued) == 1
    assert len(client.get("/api/environments").json()) == 1


def test_list_can_be_limited_to_the_callers_environments(
    client: TestClient, portal: Portal
) -> None:
    mine = create(client)["id"]
    portal.request(owner="bob@example.com", name="other")

    everything = client.get("/api/environments").json()
    only_mine = client.get("/api/environments", params={"mine": True}).json()

    assert len(everything) == 2
    assert [environment["id"] for environment in only_mine] == [mine]


def test_list_can_hide_destroyed_environments(client: TestClient, portal: Portal) -> None:
    gone = portal.ready(name="gone")
    portal.destroy(gone)
    portal.run_pending()
    alive = portal.ready(name="alive")

    visible = client.get("/api/environments", params={"include_destroyed": False}).json()

    assert [environment["id"] for environment in visible] == [alive]


def test_unknown_environment_returns_404(client: TestClient) -> None:
    response = client.get("/api/environments/does-not-exist")

    assert response.status_code == 404
    assert response.json() == {"detail": "environment does-not-exist not found"}


def test_destroy_is_accepted_and_safe_to_repeat(client: TestClient, portal: Portal) -> None:
    environment_id = portal.ready()

    first = client.post(f"/api/environments/{environment_id}/destroy")
    second = client.post(f"/api/environments/{environment_id}/destroy")

    assert first.status_code == 202
    assert second.status_code == 202
    operation_id = first.json()["active_operation"]["id"]
    assert first.json()["active_operation"]["kind"] == "destroy"
    assert second.json()["active_operation"]["id"] == operation_id
    assert portal.queue.enqueued.count(operation_id) == 1

    portal.run_pending()
    third = client.post(f"/api/environments/{environment_id}/destroy")
    assert third.status_code == 202
    assert third.json()["status"] == "destroyed"
    assert portal.runner.commands().count("destroy") == 1


def test_destroy_during_provisioning_is_a_conflict(client: TestClient) -> None:
    environment_id = create(client)["id"]

    response = client.post(f"/api/environments/{environment_id}/destroy")

    assert response.status_code == 409
    assert "already in progress" in response.json()["detail"]


def test_retry_of_a_healthy_environment_is_a_conflict(client: TestClient, portal: Portal) -> None:
    environment_id = portal.ready()

    response = client.post(f"/api/environments/{environment_id}/retry")

    assert response.status_code == 409
    assert response.json()["detail"] == "cannot move environment from ready to planning"


def test_retry_of_a_failed_environment_is_accepted(client: TestClient, portal: Portal) -> None:
    portal.runner.apply_fails_after = 0
    environment_id = portal.ready()

    response = client.post(f"/api/environments/{environment_id}/retry")
    portal.run_pending()

    assert response.status_code == 202
    assert response.json()["active_operation"]["kind"] == "provision"
    assert client.get(f"/api/environments/{environment_id}").json()["status"] == "ready"


def test_state_lock_is_visible_and_unlock_needs_the_right_id(
    client: TestClient, portal: Portal
) -> None:
    environment_id = create(client)["id"]
    portal.runner.locks[portal.state_key(environment_id)] = LockInfo(id="lock-1", who="ci@runner")
    portal.run_pending()

    body = client.get(f"/api/environments/{environment_id}").json()
    assert body["status"] == "failed"
    assert body["last_operation"]["error_code"] == "state_locked"
    assert body["lock_info"]["id"] == "lock-1"
    assert body["lock_info"]["who"] == "ci@runner"

    wrong = client.post(f"/api/environments/{environment_id}/unlock", json={"lock_id": "lock-2"})
    assert wrong.status_code == 409

    right = client.post(f"/api/environments/{environment_id}/unlock", json={"lock_id": "lock-1"})
    assert right.status_code == 202
    portal.run_pending()
    assert client.get(f"/api/environments/{environment_id}").json()["lock_info"] is None


def test_blocked_environment_exposes_its_violations(client: TestClient, portal: Portal) -> None:
    from portal.policy.base import Violation

    portal.policy.violations = [Violation("private-database", "aws_db_instance.this", "public")]
    environment_id = create(client)["id"]
    portal.run_pending()

    body = client.get(f"/api/environments/{environment_id}").json()

    assert body["status"] == "blocked_by_policy"
    assert body["policy_violations"] == [
        {"rule": "private-database", "resource": "aws_db_instance.this", "message": "public"}
    ]


def test_a_failed_enqueue_does_not_fail_the_request(client: TestClient, portal: Portal) -> None:
    portal.queue.fail_next = True

    body = create(client)

    assert body["active_operation"]["status"] == "queued"
    assert portal.queue.enqueued == []


def test_health_endpoints(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/readyz").json() == {"status": "ready"}


def test_metrics_report_environments_operations_and_requests(
    client: TestClient, portal: Portal
) -> None:
    portal.ready(ttl_hours=1)
    portal.request(name="waiting", ttl_hours=1)
    client.get("/api/templates")
    portal.clock.advance(hours=2)

    text = client.get("/metrics").text

    assert 'portal_environments{status="ready"} 1.0' in text
    assert 'portal_environments{status="requested"} 1.0' in text
    assert 'portal_operations{kind="provision",status="succeeded"} 1.0' in text
    assert 'portal_operations{kind="provision",status="queued"} 1.0' in text
    assert "portal_environments_overdue 2.0" in text
    assert 'portal_http_requests_total{method="GET",path="/api/templates",status="200"} 1.0' in text
