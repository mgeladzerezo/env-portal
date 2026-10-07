import time
from collections.abc import Awaitable, Callable, Iterator

from prometheus_client import CollectorRegistry, Counter, Histogram
from prometheus_client.core import GaugeMetricFamily
from prometheus_client.registry import Collector
from sqlalchemy import func, select
from starlette.requests import Request
from starlette.responses import Response

from portal.clock import Clock
from portal.db import SessionFactory
from portal.models import Environment, Operation
from portal.services.maintenance import count_overdue


class DatabaseCollector(Collector):
    """Gauges computed from the database on every scrape.

    Workers run in separate (prefork) processes, so in-process counters there would
    never reach the API's /metrics. The tables already hold the truth, and reading them
    at scrape time gives the same numbers no matter which replica is scraped.
    """

    def __init__(self, sessions: SessionFactory, clock: Clock) -> None:
        self._sessions = sessions
        self._clock = clock

    def collect(self) -> Iterator[GaugeMetricFamily]:
        environments = GaugeMetricFamily(
            "portal_environments", "Environments by status", labels=["status"]
        )
        operations = GaugeMetricFamily(
            "portal_operations", "Operations by kind and status", labels=["kind", "status"]
        )
        with self._sessions() as session:
            for status, count in session.execute(
                select(Environment.status, func.count()).group_by(Environment.status)
            ):
                environments.add_metric([status.value], count)
            for kind, status, count in session.execute(
                select(Operation.kind, Operation.status, func.count()).group_by(
                    Operation.kind, Operation.status
                )
            ):
                operations.add_metric([kind.value, status.value], count)
        overdue = GaugeMetricFamily(
            "portal_environments_overdue",
            "Environments past their TTL that are not destroyed yet",
            value=count_overdue(self._sessions, self._clock),
        )
        yield environments
        yield operations
        yield overdue


class HttpMetrics:
    def __init__(self, registry: CollectorRegistry) -> None:
        self._requests = Counter(
            "portal_http_requests",
            "HTTP requests",
            ["method", "path", "status"],
            registry=registry,
        )
        self._duration = Histogram(
            "portal_http_request_duration_seconds",
            "HTTP request duration",
            ["method", "path"],
            registry=registry,
        )

    async def middleware(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        started = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            # The route template keeps cardinality bounded; unmatched paths share one label.
            route = request.scope.get("route")
            path = getattr(route, "path", "unmatched")
            self._requests.labels(request.method, path, str(status)).inc()
            self._duration.labels(request.method, path).observe(time.perf_counter() - started)
