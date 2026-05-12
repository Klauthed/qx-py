# qx-observability

One-call observability setup for Qx services — structured logging (structlog), distributed tracing (OpenTelemetry), Prometheus metrics, and health probes.

## What lives here

- **`qx.observability.setup_observability`** — configures logging and tracing, returns a `(Metrics, HealthRegistry)` pair. This is the only call most services need.
- **`qx.observability.Metrics`** — Prometheus counter/histogram wrapper. Pass a custom `CollectorRegistry` in tests to avoid collisions.
- **`qx.observability.HealthRegistry`** — register named health checks; aggregates their results for `/healthz` and `/readyz`.
- **`qx.observability.configure_logging`** — structlog pipeline with JSON output in production and pretty console output in development.
- **`qx.observability.configure_tracing`** — OpenTelemetry SDK setup; exports to an OTLP endpoint (Tempo, Jaeger, etc.) or console.
- **`qx.observability.get_logger`** — returns a bound structlog logger for a given name.
- **`qx.observability.get_tracer`** — returns an OTel `Tracer`; use with `trace_span`.
- **`qx.observability.trace_span`** — async context manager that wraps a block in an OTel span.
- **`qx.observability.inject_context` / `extract_context`** — propagate trace context through message headers (W3C TraceContext format).
- **`qx.observability.MetricsBehavior` / `TracingBehavior`** — Mediator pipeline behaviors that instrument every command/query automatically.

## Usage

```python
from qx.observability import setup_observability

# In application bootstrap
metrics, health = setup_observability(
    settings,
    otlp_endpoint="http://tempo:4317",
)

# Register a health check
@health.check("database")
async def _db_check() -> bool:
    return await db.ping()

# In any module
from qx.observability import get_logger, trace_span

log = get_logger(__name__)

async def do_work() -> None:
    async with trace_span("my-operation"):
        log.info("doing work", key="value")
```

## Mediator pipeline wiring

```python
from qx.cqrs import compose
from qx.observability import MetricsBehavior, TracingBehavior

pipeline = compose(
    TracingBehavior(get_tracer("mediator")),
    MetricsBehavior(metrics),
)
mediator = Mediator(container, pipeline=pipeline)
```

## Design rules

- Logging is structured (key-value) everywhere — no f-string log messages in framework code.
- `setup_observability()` is idempotent in tests when a fresh `CollectorRegistry` is passed via `metrics_registry`.
- Trace context is propagated automatically via `RequestContextMiddleware` on ingress and `inject_context` on egress (outbox relay, NATS publisher).
