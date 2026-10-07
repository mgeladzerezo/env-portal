import uuid
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from portal.catalog import Catalog
from portal.clock import Clock
from portal.cost.base import CostEstimationError, CostEstimator
from portal.errors import LockMismatch, NotFound, OperationInProgress
from portal.models import Environment, Operation, OperationKind, OperationStatus
from portal.state_machine import EnvironmentStatus, IllegalTransition, can_transition

S = EnvironmentStatus


def get_environment(session: Session, environment_id: str) -> Environment:
    environment = session.get(
        Environment, environment_id, options=[selectinload(Environment.operations)]
    )
    if environment is None:
        raise NotFound(f"environment {environment_id} not found")
    return environment


def list_environments(
    session: Session, *, owner: str | None = None, include_destroyed: bool = True
) -> list[Environment]:
    query = (
        select(Environment)
        .options(selectinload(Environment.operations))
        .order_by(Environment.created_at.desc(), Environment.id)
    )
    if owner:
        query = query.where(Environment.owner == owner)
    if not include_destroyed:
        query = query.where(Environment.status != S.DESTROYED)
    return list(session.scalars(query))


def create_environment(
    session: Session,
    *,
    catalog: Catalog,
    estimator: CostEstimator,
    clock: Clock,
    owner: str,
    name: str,
    template: str,
    size: str,
    ttl_hours: int,
    idempotency_key: str | None = None,
) -> tuple[Environment, Operation | None]:
    """Create an environment and its first provision operation.

    Returns (environment, None) when the idempotency key matches an earlier request.
    """
    spec = catalog.validate_request(template, size, ttl_hours)
    if idempotency_key and (existing := _find_by_key(session, owner, idempotency_key)):
        return existing, None

    now = clock.now()
    environment = Environment(
        id=str(uuid.uuid4()),
        name=name,
        owner=owner,
        template=spec.name,
        module_version=spec.module_version,
        size=size,
        ttl_hours=ttl_hours,
        status=S.REQUESTED,
        hourly_cost_usd=_estimate_or_none(estimator, catalog, template, size),
        outputs={},
        policy_violations=[],
        idempotency_key=idempotency_key,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(hours=ttl_hours),
    )
    session.add(environment)
    operation = _new_operation(environment, OperationKind.PROVISION, owner, clock)
    session.add(operation)
    try:
        session.commit()
    except IntegrityError:
        # Two requests with the same key raced; the other one won.
        session.rollback()
        existing = _find_by_key(session, owner, idempotency_key) if idempotency_key else None
        if existing is None:
            raise
        return existing, None
    return environment, operation


def request_retry(
    session: Session, environment: Environment, requested_by: str, clock: Clock
) -> Operation:
    """Queue again whatever failed last: a destroy stays a destroy, anything else re-plans."""
    if environment.active_operation is not None:
        raise OperationInProgress("an operation is already in progress for this environment")
    if environment.status not in (S.FAILED, S.BLOCKED_BY_POLICY):
        raise IllegalTransition(environment.status, S.PLANNING)
    kind = _last_infrastructure_kind(environment)
    return _start_operation(session, environment, kind, requested_by, clock)


def request_destroy(
    session: Session, environment: Environment, requested_by: str, clock: Clock
) -> Operation | None:
    """Queue a destroy. Asking twice is harmless.

    Returns the destroy that is already queued or running if there is one, and None for
    an environment that is already destroyed.
    """
    if environment.status is S.DESTROYED:
        return None
    active = environment.active_operation
    if active is not None:
        if active.kind is OperationKind.DESTROY:
            return active
        raise OperationInProgress("an operation is already in progress for this environment")
    if not can_transition(environment.status, S.DESTROYING):
        raise IllegalTransition(environment.status, S.DESTROYING)
    return _start_operation(session, environment, OperationKind.DESTROY, requested_by, clock)


def request_unlock(
    session: Session, environment: Environment, lock_id: str, requested_by: str, clock: Clock
) -> Operation:
    """Queue a force-unlock, but only for the exact lock the caller was shown."""
    recorded = (environment.lock_info or {}).get("id")
    if recorded is None:
        raise LockMismatch("this environment has no recorded state lock")
    if recorded != lock_id:
        raise LockMismatch("lock_id does not match the lock recorded for this environment")
    return _start_operation(
        session, environment, OperationKind.UNLOCK, requested_by, clock, lock_id=lock_id
    )


def _find_by_key(session: Session, owner: str, key: str) -> Environment | None:
    return session.scalar(
        select(Environment)
        .options(selectinload(Environment.operations))
        .where(Environment.owner == owner, Environment.idempotency_key == key)
    )


def _estimate_or_none(
    estimator: CostEstimator, catalog: Catalog, template: str, size: str
) -> Decimal | None:
    try:
        return estimator.estimate(catalog.get(template), size).hourly_usd
    except CostEstimationError:
        # A pricing outage should not stop people from getting environments.
        return None


def _last_infrastructure_kind(environment: Environment) -> OperationKind:
    for operation in reversed(environment.operations):
        if operation.kind is not OperationKind.UNLOCK:
            return operation.kind
    return OperationKind.PROVISION


def _new_operation(
    environment: Environment,
    kind: OperationKind,
    requested_by: str,
    clock: Clock,
    lock_id: str | None = None,
) -> Operation:
    return Operation(
        environment=environment,
        kind=kind,
        status=OperationStatus.QUEUED,
        requested_by=requested_by,
        lock_id=lock_id,
        created_at=clock.now(),
    )


def _start_operation(
    session: Session,
    environment: Environment,
    kind: OperationKind,
    requested_by: str,
    clock: Clock,
    lock_id: str | None = None,
) -> Operation:
    operation = _new_operation(environment, kind, requested_by, clock, lock_id)
    session.add(operation)
    try:
        session.commit()
    except IntegrityError:
        # The partial unique index rejected a second active operation: another request
        # got in between our check and this insert.
        session.rollback()
        raise OperationInProgress(
            "an operation is already in progress for this environment"
        ) from None
    return operation
