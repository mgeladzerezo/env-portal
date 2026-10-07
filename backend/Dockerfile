# syntax=docker/dockerfile:1

# The CLI tools the worker shells out to, taken from their publishers' images so the
# versions are pinned in one place. Terraform matches the version the tests run against.
FROM hashicorp/terraform:1.9.8 AS terraform
FROM openpolicyagent/conftest:v0.56.0 AS conftest
FROM infracost/infracost:ci-0.10 AS infracost

FROM python:3.12-slim AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /src
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY backend/pyproject.toml ./
COPY backend/portal ./portal
RUN pip install .

FROM python:3.12-slim AS base
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PATH="/opt/venv/bin:$PATH"
RUN groupadd --gid 10001 portal \
    && useradd --uid 10001 --gid portal --home-dir /app --no-create-home portal
WORKDIR /app
COPY --from=build /opt/venv /opt/venv
COPY backend/alembic.ini ./
COPY backend/alembic ./alembic
ENV PORTAL_MODULES_DIR=/app/modules \
    PORTAL_POLICY_DIR=/app/policies \
    PORTAL_WORKSPACE_DIR=/var/lib/portal/workspaces \
    PORTAL_LOCAL_STATE_DIR=/var/lib/portal/state
RUN mkdir -p /var/lib/portal && chown portal:portal /var/lib/portal

FROM base AS api
COPY --from=infracost /usr/bin/infracost /usr/local/bin/infracost
COPY modules ./modules
USER 10001
EXPOSE 8000
CMD ["uvicorn", "portal.main:app_factory", "--factory", "--host", "0.0.0.0", "--port", "8000"]

FROM base AS worker
COPY --from=terraform /bin/terraform /usr/local/bin/terraform
COPY --from=conftest /usr/local/bin/conftest /usr/local/bin/conftest
COPY modules ./modules
COPY policies ./policies
# Providers are downloaded once per pod into this cache instead of once per environment.
ENV TF_PLUGIN_CACHE_DIR=/var/lib/portal/plugin-cache
USER 10001
CMD ["celery", "--app", "portal.worker.tasks:celery_app", "worker", "--loglevel", "INFO", "--concurrency", "2"]
