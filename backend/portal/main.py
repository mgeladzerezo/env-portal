import logging

from fastapi import FastAPI, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, generate_latest
from sqlalchemy import text

from portal import api
from portal.container import Container, get_container
from portal.errors import (
    InvalidRequest,
    LockMismatch,
    NotFound,
    OperationInProgress,
    PortalError,
)
from portal.metrics import DatabaseCollector, HttpMetrics
from portal.state_machine import IllegalTransition

ERROR_STATUS: dict[type[PortalError], int] = {
    NotFound: status.HTTP_404_NOT_FOUND,
    InvalidRequest: 422,
    OperationInProgress: status.HTTP_409_CONFLICT,
    IllegalTransition: status.HTTP_409_CONFLICT,
    LockMismatch: status.HTTP_409_CONFLICT,
}


def create_app(container: Container | None = None) -> FastAPI:
    container = container or get_container()
    app = FastAPI(title="Environment portal", version="0.1.0")
    app.state.container = container

    registry = CollectorRegistry()
    registry.register(DatabaseCollector(container.sessions, container.clock))
    app.middleware("http")(HttpMetrics(registry).middleware)

    if container.settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=container.settings.cors_origins,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.exception_handler(PortalError)
    def handle_portal_error(request: Request, exc: PortalError) -> JSONResponse:
        code = ERROR_STATUS.get(type(exc), status.HTTP_500_INTERNAL_SERVER_ERROR)
        return JSONResponse({"detail": str(exc)}, status_code=code)

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    def readyz() -> dict[str, str]:
        with container.sessions() as session:
            session.execute(text("SELECT 1"))
        return {"status": "ready"}

    @app.get("/metrics", include_in_schema=False)
    def metrics() -> Response:
        return Response(generate_latest(registry), media_type=CONTENT_TYPE_LATEST)

    app.include_router(api.router)
    return app


def app_factory() -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    return create_app()
