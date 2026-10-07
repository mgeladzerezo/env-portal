from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from portal.db import Base, UTCDateTime
from portal.state_machine import EnvironmentStatus


class OperationKind(StrEnum):
    PROVISION = "provision"
    DESTROY = "destroy"
    UNLOCK = "unlock"


class OperationStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


ACTIVE_OPERATION_STATUSES = (OperationStatus.QUEUED, OperationStatus.RUNNING)


def _string_enum(enum_class: type[StrEnum]) -> Enum:
    return Enum(
        enum_class,
        native_enum=False,
        length=32,
        values_callable=lambda members: [member.value for member in members],
    )


class Environment(Base):
    __tablename__ = "environments"
    __table_args__ = (
        UniqueConstraint("owner", "idempotency_key", name="uq_environments_owner_idempotency_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    owner: Mapped[str] = mapped_column(String(128), index=True)
    template: Mapped[str] = mapped_column(String(64))
    module_version: Mapped[str] = mapped_column(String(16))
    size: Mapped[str] = mapped_column(String(16))
    ttl_hours: Mapped[int] = mapped_column(Integer)
    status: Mapped[EnvironmentStatus] = mapped_column(_string_enum(EnvironmentStatus), index=True)
    hourly_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    outputs: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    policy_violations: Mapped[list[dict[str, str]]] = mapped_column(JSON, default=list)
    error: Mapped[str | None] = mapped_column(Text)
    lock_info: Mapped[dict[str, str] | None] = mapped_column(JSON(none_as_null=True))
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    destroyed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    operations: Mapped[list["Operation"]] = relationship(
        back_populates="environment", order_by="Operation.id"
    )

    @property
    def active_operation(self) -> "Operation | None":
        return next((op for op in self.operations if op.status in ACTIVE_OPERATION_STATUSES), None)

    @property
    def last_operation(self) -> "Operation | None":
        return self.operations[-1] if self.operations else None


class Operation(Base):
    __tablename__ = "operations"
    __table_args__ = (
        # The database, not application code, guarantees that an environment never has
        # two operations in flight.
        Index(
            "uq_operations_one_active_per_environment",
            "environment_id",
            unique=True,
            sqlite_where=text("status IN ('queued', 'running')"),
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    environment_id: Mapped[str] = mapped_column(ForeignKey("environments.id"), index=True)
    kind: Mapped[OperationKind] = mapped_column(_string_enum(OperationKind))
    status: Mapped[OperationStatus] = mapped_column(_string_enum(OperationStatus), index=True)
    requested_by: Mapped[str] = mapped_column(String(128))
    error_code: Mapped[str | None] = mapped_column(String(32))
    error: Mapped[str | None] = mapped_column(Text)
    # Only set for unlock operations: the lock the user confirmed they want released.
    lock_id: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    heartbeat_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    environment: Mapped[Environment] = relationship(back_populates="operations")


class LogLine(Base):
    __tablename__ = "log_lines"
    __table_args__ = (
        UniqueConstraint("environment_id", "seq", name="uq_log_lines_environment_seq"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    environment_id: Mapped[str] = mapped_column(ForeignKey("environments.id"))
    operation_id: Mapped[int] = mapped_column(ForeignKey("operations.id"))
    # Gapless per environment, so a client can tell whether it has seen every line.
    seq: Mapped[int] = mapped_column(Integer)
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
