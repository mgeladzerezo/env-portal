from portal.catalog import TemplateSpec
from portal.cost.base import CostEstimate, CostItem


class StaticCostEstimator:
    """Prices from the catalog. Good enough to compare sizes; not a quote."""

    def estimate(self, template: TemplateSpec, size: str) -> CostEstimate:
        spec = template.size(size)
        item = CostItem(name=f"{template.title} ({spec.description})", hourly_usd=spec.hourly_usd)
        return CostEstimate(hourly_usd=spec.hourly_usd, items=(item,), source="static")
