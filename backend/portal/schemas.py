from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from portal.models import OperationKind, OperationStatus
from portal.state_machine import EnvironmentStatus


class SizeOut(BaseModel):
    name: str
    description: str
    hourly_usd: float


class TemplateOut(BaseModel):
    name: str
    title: str
    description: str
    module_version: str
    sizes: list[SizeOut]
    default_ttl_hours: int
    max_ttl_hours: int


class EstimateRequest(BaseModel):
    template: str
    size: str
    ttl_hours: int = Field(ge=1)


class CostItemOut(BaseModel):
    name: str
    hourly_usd: float


class EstimateOut(BaseModel):
    template: str
    size: str
    ttl_hours: int
    hourly_usd: float
    total_usd: float
    items: list[CostItemOut]
    source: str


class EnvironmentCreate(BaseModel):
    # Becomes part of cloud resource names, hence the tight pattern.
    name: str = Field(pattern=r"^[a-z][a-z0-9-]{1,22}[a-z0-9]$")
    template: str
    size: str
    ttl_hours: int = Field(ge=1)


class OperationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: OperationKind
    status: OperationStatus
    requested_by: str
    error_code: str | None
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class ViolationOut(BaseModel):
    rule: str
    resource: str
    message: str


class LockOut(BaseModel):
    id: str
    who: str = ""
    operation: str = ""
    created: str = ""
    path: str = ""


class EnvironmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    owner: str
    template: str
    module_version: str
    size: str
    ttl_hours: int
    status: EnvironmentStatus
    hourly_cost_usd: float | None
    outputs: dict[str, Any]
    policy_violations: list[ViolationOut]
    error: str | None
    lock_info: LockOut | None
    created_at: datetime
    updated_at: datetime
    expires_at: datetime
    destroyed_at: datetime | None
    active_operation: OperationOut | None
    last_operation: OperationOut | None


class UnlockRequest(BaseModel):
    lock_id: str = Field(min_length=1)


class LogLineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    seq: int
    operation_id: int
    message: str
    created_at: datetime


class ErrorOut(BaseModel):
    detail: str
