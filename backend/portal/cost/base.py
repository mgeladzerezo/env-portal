from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from portal.catalog import TemplateSpec
from portal.errors import PortalError


@dataclass(frozen=True)
class CostItem:
    name: str
    hourly_usd: Decimal


@dataclass(frozen=True)
class CostEstimate:
    hourly_usd: Decimal
    items: tuple[CostItem, ...]
    source: str

    def total_for(self, ttl_hours: int) -> Decimal:
        return (self.hourly_usd * ttl_hours).quantize(Decimal("0.01"))


class CostEstimationError(PortalError):
    pass


class CostEstimator(Protocol):
    def estimate(self, template: TemplateSpec, size: str) -> CostEstimate: ...
