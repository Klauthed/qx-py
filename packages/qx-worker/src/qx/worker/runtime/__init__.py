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
   f. On success: ack. On failure: nak with delay (let JetStream retry).
3. Repeat.

Backoff: NATS handles retry semantics (we set ``max_deliver`` on the consumer).
The worker itself only decides ack vs nak — it does not implement an
in-process retry loop.

Single-instance is **not** required; multiple worker pods process disjoint
messages from the same durable consumer (NATS load-balances).
"""

from __future__ import annotations

import asyncio
from typing import Any

from qx.core import RequestContext, request_scope
from qx.cqrs import Mediator
from qx.di import Container
from qx.events import EventRegistry, EventTypeNotRegistered, NatsConsumer
from qx.observability import get_logger, trace_span

__all__ = ["WorkerRuntime"]


class WorkerRuntime:
    """Run a consumer loop until ``stop`` is set."""

    def __init__(
        self,
        container: Container,
        consumer: NatsConsumer,
        registry: EventRegistry,
        mediator: Mediator,
        *,
        concurrency: int = 4,
    ) -> None:
        self._container = container
        self._consumer = consumer
        self._registry = registry
        self._mediator = mediator
        self._concurrency = concurrency
        self._stop = asyncio.Event()
        self._log = get_logger("qx.worker")

    async def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        self._log.info("worker starting", concurrency=self._concurrency)
        async with self._consumer.messages() as sub:
            while not self._stop.is_set():
                try:
                    batch = await sub.fetch(self._concurrency, timeout=5.0)
                except asyncio.TimeoutError:
                    # No messages available right now; loop back.
                    continue
                except Exception as exc:  # noqa: BLE001
                    self._log.error("worker fetch error: %s", exc, exc_info=True)
                    await asyncio.sleep(1.0)
                    continue
                await asyncio.gather(
                    *(self._handle_one(m) for m in batch),
                    return_exceptions=True,
                )
        self._log.info("worker stopped")

    async def _handle_one(self, msg: Any) -> None:
        headers = msg.headers or {}
        event_name = headers.get("qx.event_name", "<unknown>")
        correlation = headers.get("qx.correlation_id")
        tenant = headers.get("qx.tenant_id")

        # Decode payload + resolve event class.
        try:
            event = self._consumer.parse_message(msg)
        except EventTypeNotRegistered as exc:
            # Unknown event type — terminate this delivery with ack so it
            # doesn't redeliver. The original publisher should monitor
            # consumer-side dead-letter signals for these.
            self._log.warning("unknown event type %s; acking and dropping", event_name)
            await msg.ack()
            return
        except Exception as exc:  # noqa: BLE001
            # Malformed payload. Nak with backoff so ops can investigate.
            self._log.error(
                "failed to parse event %s: %s",
                event_name,
                exc,
                exc_info=True,
            )
            await msg.nak()
            return

        # Build request context from the envelope.
        from uuid import UUID, uuid4

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

        with request_scope(ctx), trace_span(
            f"consume.{event_name}",
            attributes={
                "messaging.system": "nats",
                "messaging.destination.name": event_name,
                "qx.event_name": event_name,
            },
        ):
            try:
                async with self._container.scope("worker") as scope:
                    await self._mediator.consume_integration(event, scope=scope)
                await msg.ack()
            except Exception as exc:  # noqa: BLE001
                self._log.error(
                    "handler failed for %s: %s",
                    event_name,
                    exc,
                    exc_info=True,
                )
                # nak with default delay; JetStream retries up to max_deliver.
                await msg.nak()
