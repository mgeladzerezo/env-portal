from collections.abc import Iterator
from functools import partial
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from portal.container import Container
from portal.models import Environment, Operation
from portal.queue import dispatch
from portal.schemas import (
    EnvironmentCreate,
    EnvironmentOut,
    ErrorOut,
    EstimateOut,
    EstimateRequest,
    LogLineOut,
    SizeOut,
    TemplateOut,
    UnlockRequest,
)
from portal.services import environments
from portal.services.logs import read_logs
from portal.services.streaming import environment_events, read_snapshot

router = APIRouter(prefix="/api")

CONFLICT = {status.HTTP_409_CONFLICT: {"model": ErrorOut}}
NOT_FOUND = {status.HTTP_404_NOT_FOUND: {"model": ErrorOut}}


def get_container(request: Request) -> Container:
    return request.app.state.container


ContainerDep = Annotated[Container, Depends(get_container)]


def get_session(container: ContainerDep) -> Iterator[Session]:
    with container.sessions() as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]


def current_user(request: Request, container: ContainerDep) -> str:
    user = request.headers.get(container.settings.user_header) or container.settings.dev_user
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "no authenticated user")
    return user


UserDep = Annotated[str, Depends(current_user)]


def load_environment(environment_id: str, session: SessionDep) -> Environment:
    return environments.get_environment(session, environment_id)


EnvironmentDep = Annotated[Environment, Depends(load_environment)]


def _respond(
    container: Container, environment: Environment, operation: Operation | None
) -> Environment:
    if operation is not None:
        dispatch(container.queue, operation.id)
    return environment


@router.get("/templates", response_model=list[TemplateOut])
def list_templates(container: ContainerDep) -> list[TemplateOut]:
    return [
        TemplateOut(
            name=template.name,
            title=template.title,
            description=template.description,
            module_version=template.module_version,
            sizes=[
                SizeOut(name=s.name, description=s.description, hourly_usd=float(s.hourly_usd))
                for s in template.sizes
            ],
            default_ttl_hours=template.default_ttl_hours,
            max_ttl_hours=template.max_ttl_hours,
        )
        for template in container.catalog.all()
    ]


@router.post("/estimates", response_model=EstimateOut)
def estimate_cost(body: EstimateRequest, container: ContainerDep, user: UserDep) -> EstimateOut:
    template = container.catalog.validate_request(body.template, body.size, body.ttl_hours)
    estimate = container.estimator.estimate(template, body.size)
    return EstimateOut(
        template=body.template,
        size=body.size,
        ttl_hours=body.ttl_hours,
        hourly_usd=float(estimate.hourly_usd),
        total_usd=float(estimate.total_for(body.ttl_hours)),
        items=[{"name": i.name, "hourly_usd": float(i.hourly_usd)} for i in estimate.items],
        source=estimate.source,
    )


@router.post(
    "/environments",
    response_model=EnvironmentOut,
    status_code=status.HTTP_201_CREATED,
    responses={status.HTTP_200_OK: {"model": EnvironmentOut}},
)
def create_environment(
    body: EnvironmentCreate,
    response: Response,
    container: ContainerDep,
    session: SessionDep,
    user: UserDep,
    idempotency_key: Annotated[str | None, Header(max_length=128)] = None,
) -> Environment:
    environment, operation = environments.create_environment(
        session,
        catalog=container.catalog,
        estimator=container.estimator,
        clock=container.clock,
        owner=user,
        name=body.name,
        template=body.template,
        size=body.size,
        ttl_hours=body.ttl_hours,
        idempotency_key=idempotency_key,
    )
    if operation is None:
        # Replay of an earlier request with the same Idempotency-Key.
        response.status_code = status.HTTP_200_OK
    return _respond(container, environment, operation)


@router.get("/environments", response_model=list[EnvironmentOut])
def list_environments(
    session: SessionDep,
    user: UserDep,
    mine: bool = False,
    include_destroyed: bool = True,
) -> list[Environment]:
    return environments.list_environments(
        session, owner=user if mine else None, include_destroyed=include_destroyed
    )


@router.get("/environments/{environment_id}", response_model=EnvironmentOut, responses=NOT_FOUND)
def get_environment(environment: EnvironmentDep, user: UserDep) -> Environment:
    return environment


@router.post(
    "/environments/{environment_id}/retry",
    response_model=EnvironmentOut,
    status_code=status.HTTP_202_ACCEPTED,
    responses=NOT_FOUND | CONFLICT,
)
def retry_environment(
    environment: EnvironmentDep, container: ContainerDep, session: SessionDep, user: UserDep
) -> Environment:
    operation = environments.request_retry(session, environment, user, container.clock)
    return _respond(container, environment, operation)


@router.post(
    "/environments/{environment_id}/destroy",
    response_model=EnvironmentOut,
    status_code=status.HTTP_202_ACCEPTED,
    responses=NOT_FOUND | CONFLICT,
)
def destroy_environment(
    environment: EnvironmentDep, container: ContainerDep, session: SessionDep, user: UserDep
) -> Environment:
    already_active = environment.active_operation
    operation = environments.request_destroy(session, environment, user, container.clock)
    is_new = operation is not None and operation is not already_active
    return _respond(container, environment, operation if is_new else None)


@router.post(
    "/environments/{environment_id}/unlock",
    response_model=EnvironmentOut,
    status_code=status.HTTP_202_ACCEPTED,
    responses=NOT_FOUND | CONFLICT,
)
def unlock_environment(
    body: UnlockRequest,
    environment: EnvironmentDep,
    container: ContainerDep,
    session: SessionDep,
    user: UserDep,
) -> Environment:
    operation = environments.request_unlock(
        session, environment, body.lock_id, user, container.clock
    )
    return _respond(container, environment, operation)


@router.get(
    "/environments/{environment_id}/logs", response_model=list[LogLineOut], responses=NOT_FOUND
)
def get_logs(
    environment: EnvironmentDep,
    session: SessionDep,
    user: UserDep,
    after: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=1000)] = 500,
) -> list[LogLineOut]:
    return read_logs(session, environment.id, after=after, limit=limit)


@router.get("/environments/{environment_id}/logs/stream", responses=NOT_FOUND)
def stream_logs(
    environment: EnvironmentDep,
    container: ContainerDep,
    user: UserDep,
    after: Annotated[int, Query(ge=0)] = 0,
    last_event_id: Annotated[int | None, Header()] = None,
) -> StreamingResponse:
    read = partial(read_snapshot, container.sessions, environment.id)
    events = environment_events(
        read,
        after=last_event_id if last_event_id is not None else after,
        poll_interval=container.settings.sse_poll_interval_seconds,
    )
    return StreamingResponse(
        events,
        media_type="text/event-stream",
        # X-Accel-Buffering stops nginx from holding events back.
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
