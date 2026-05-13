"""Background worker that consumes integration events and dispatches to handlers.

Architecture:

1. Pull a batch of messages from a NATS JetStream durable consumer.
2. For each message:
   a. Decode envelope, resolve concrete ``IntegrationEvent`` class via
      ``EventRegistry``.
   b. Open a request scope (so handlers can use scoped dependencies).
   c. Set ``RequestContext`` with correlation/tenant from the event envelope.
   d. Open an OTel span linked to the incoming trace context.
   e. Call ``Mediator.consume_integration(event, scope=...)``.
   f. On success or ``PermanentWorkerError``: ack.
      On any other failure: nak with delay (let JetStream retry).
3. On stop: drain all in-flight messages before returning.

Backoff: NATS handles retry semantics (we set ``max_deliver`` on the consumer).
The worker itself only decides ack vs nak — it does not implement an
in-process retry loop.

Single-instance is **not** required; multiple worker pods process disjoint
messages from the same durable consumer (NATS load-balances).
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from qx.core import RequestContext, request_scope
from qx.events import EventRegistry, EventTypeNotRegistered, NatsConsumer
from qx.observability import get_logger, trace_span
from qx.worker.errors import PermanentWorkerError
from qx.worker.health import WorkerHealth

if TYPE_CHECKING:
    from qx.cqrs import Mediator
    from qx.di import Container
    from qx.worker.dlq import DeadLetterStore

# ---------------------------------------------------------------------------
# Optional Prometheus metrics
# ---------------------------------------------------------------------------
try:
    from prometheus_client import Counter, Gauge, Histogram

    _messages_total = Counter(
        "qx_worker_messages_total",
        "Total messages processed by the worker",
        ["event_name", "result"],  # result: ack | nak | drop | dlq
    )
    _dlq_total = Counter(
        "qx_worker_dlq_total",
        "Messages moved to the dead letter queue",
        ["event_name"],
    )
    _message_duration = Histogram(
        "qx_worker_message_duration_seconds",
        "Time spent processing a single message",
        ["event_name"],
        buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
    )
    _inflight_gauge = Gauge(
        "qx_worker_messages_inflight",
        "Number of messages currently being processed",
    )
    _consumer_lag = Gauge(
        "qx_worker_consumer_lag",
        "JetStream num_pending — messages in the stream not yet delivered to this consumer.",
        ["consumer"],
    )
    _HAS_PROMETHEUS = True
except Exception:  # pragma: no cover
    _HAS_PROMETHEUS = False
    _dlq_total = None  # type: ignore[assignment]

__all__ = ["WorkerRuntime"]

_Result = str  # "ack" | "nak" | "drop" | "dlq"


def _is_last_attempt(msg: Any, consumer: Any) -> bool:
    """Return True when the message has exhausted all redelivery attempts."""
    meta = getattr(msg, "metadata", None)
    num_delivered = int(getattr(meta, "num_delivered", 0) or 0)
    max_deliver = int(getattr(consumer, "_max_deliver", 0) or 0)
    return max_deliver > 0 and num_delivered >= max_deliver


class WorkerRuntime:
    """Run a consumer loop until ``stop()`` is called.

    Shutdown is graceful: the loop stops accepting new batches but waits for
    all in-flight ``_handle_one`` tasks to complete before returning.

    Error classification
    -------------------
    - ``PermanentWorkerError`` raised by a handler → ack (discard; no retry).
    - ``EventTypeNotRegistered`` from the registry → ack (drop unknown event).
    - Any other exception → nak (JetStream retries up to ``max_deliver``).
    """

    def __init__(
        self,
        container: Container,
        consumer: NatsConsumer,
        registry: EventRegistry,
        mediator: Mediator,
        *,
        concurrency: int = 4,
        health: WorkerHealth | None = None,
        lag_poll_interval: float = 15.0,
        dlq: DeadLetterStore | None = None,
    ) -> None:
        self._container = container
        self._consumer = consumer
        self._registry = registry
        self._mediator = mediator
        self._concurrency = concurrency
        self._health = health or WorkerHealth()
        self._stop = asyncio.Event()
        self._log = get_logger("qx.worker")
        self._lag_poll_interval = lag_poll_interval
        self._dlq = dlq

    @property
    def health(self) -> WorkerHealth:
        return self._health

    async def stop(self) -> None:
        """Signal the run loop to stop accepting new batches."""
        self._stop.set()

    async def _poll_consumer_lag(self) -> None:
        """Periodically update the consumer lag gauge from JetStream num_pending."""
        consumer_label = getattr(self._consumer, "_durable", "unknown")
        while True:
            try:
                await asyncio.sleep(self._lag_poll_interval)
            except asyncio.CancelledError:
                break
            if _HAS_PROMETHEUS:
                pending = await self._consumer.num_pending()
                if pending is not None:
                    _consumer_lag.labels(consumer=consumer_label).set(pending)

    async def run(self) -> None:
        self._health.mark_started()
        self._log.info("worker starting", concurrency=self._concurrency)

        inflight: set[asyncio.Task[None]] = set()
        lag_task = asyncio.create_task(self._poll_consumer_lag())

        try:
            async with self._consumer.messages() as sub:
                while not self._stop.is_set():
                    # Reap completed tasks.
                    done = {t for t in inflight if t.done()}
                    inflight -= done

                    # Back-pressure: don't fetch more than concurrency allows.
                    available = self._concurrency - len(inflight)
                    if available <= 0:
                        await asyncio.sleep(0.01)
                        continue

                    try:
                        batch = await sub.fetch(available, timeout=5.0)
                        self._health.mark_activity()
                    except TimeoutError:
                        self._health.mark_activity()
                        continue
                    except Exception as exc:
                        self._log.error("worker fetch error", exc_info=True, error=str(exc))
                        await asyncio.sleep(1.0)
                        continue

                    for msg in batch:
                        task = asyncio.create_task(self._handle_one(msg))
                        inflight.add(task)

            # Graceful drain.
            if inflight:
                self._log.info("draining in-flight messages", count=len(inflight))
                await asyncio.gather(*inflight, return_exceptions=True)

        finally:
            lag_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await lag_task
            self._health.mark_stopped()
            self._log.info("worker stopped")

    async def _handle_one(self, msg: Any) -> None:
        headers = msg.headers or {}
        event_name = headers.get("qx.event_name", "<unknown>")

        if _HAS_PROMETHEUS:
            _inflight_gauge.inc()

        try:
            result = await self._process(msg, event_name)
        finally:
            if _HAS_PROMETHEUS:
                _inflight_gauge.dec()

        if _HAS_PROMETHEUS:
            _messages_total.labels(event_name=event_name, result=result).inc()

    async def _process(self, msg: Any, event_name: str) -> _Result:
        correlation = (msg.headers or {}).get("qx.correlation_id")
        tenant = (msg.headers or {}).get("qx.tenant_id")

        # --- Decode ---
        try:
            event = self._consumer.parse_message(msg)
        except EventTypeNotRegistered:
            self._log.warning("unknown event type; dropping", event_name=event_name)
            await msg.ack()
            return "drop"
        except Exception as exc:
            self._log.error("malformed payload; naking", event_name=event_name, error=str(exc))
            await msg.nak()
            return "nak"

        # --- Build request context ---
        def _uuid(v: str | None) -> UUID:
            try:
                return UUID(v) if v else uuid4()
            except ValueError:
                return uuid4()

        ctx = RequestContext(
            correlation_id=_uuid(correlation),
            tenant_id=UUID(tenant) if tenant else None,
            actor_kind="system",
            attributes={"event_name": event_name},
        )

        # --- Dispatch ---
        with (
            request_scope(ctx),
            trace_span(
                f"consume.{event_name}",
                attributes={
                    "messaging.system": "nats",
                    "messaging.destination.name": event_name,
                    "qx.event_name": event_name,
                },
            ),
        ):
            if _HAS_PROMETHEUS:
                timer = _message_duration.labels(event_name=event_name).time()
                timer.__enter__()  # type: ignore[no-untyped-call]

            try:
                async with self._container.scope("worker") as scope:
                    await self._mediator.consume_integration(event, scope=scope)
                await msg.ack()
                if _HAS_PROMETHEUS:
                    timer.__exit__(None, None, None)  # type: ignore[no-untyped-call]
                return "ack"

            except PermanentWorkerError as exc:
                # Deterministic failure — ack to prevent redelivery.
                self._log.warning(
                    "permanent handler failure; acking to prevent retry",
                    event_name=event_name,
                    error=str(exc),
                )
                await msg.ack()
                if _HAS_PROMETHEUS:
                    timer.__exit__(None, None, None)  # type: ignore[no-untyped-call]
                return "drop"

            except Exception as exc:
                if _HAS_PROMETHEUS:
                    timer.__exit__(type(exc), exc, exc.__traceback__)  # type: ignore[no-untyped-call]
                if self._dlq is not None and _is_last_attempt(msg, self._consumer):
                    self._log.error(
                        "handler failed on last attempt; routing to DLQ",
                        event_name=event_name,
                        error=str(exc),
                        exc_info=True,
                    )
                    try:
                        await self._dlq.persist(msg, error=str(exc))
                    except Exception:
                        self._log.error(
                            "DLQ persist failed; message will be lost",
                            event_name=event_name,
                            exc_info=True,
                        )
                    await msg.ack()
                    if _HAS_PROMETHEUS:
                        _dlq_total.labels(event_name=event_name).inc()
                    return "dlq"
                self._log.error(
                    "handler failed; naking for retry",
                    event_name=event_name,
                    error=str(exc),
                    exc_info=True,
                )
                await msg.nak()
                return "nak"
