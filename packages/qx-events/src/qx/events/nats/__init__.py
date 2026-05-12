"""NATS JetStream integration.

Two responsibilities:

- ``NatsPublisher``: durable publish to a JetStream subject. Used by the outbox
  relay (and only the outbox relay — application code never publishes
  integration events directly; that's the whole point of the outbox).

- ``NatsConsumer``: durable pull-subscription wrapper. Used by worker runtimes
  to receive integration events with at-least-once semantics, ack/nack control,
  and per-message tracing/logging.

NATS connection management:
- Single connection per process (singleton in DI).
- Auto-reconnect on disconnect (NATS client default).
- JetStream context obtained from the connection lazily.

Subject convention: ``<service>.<event_name>``, e.g., ``identity.user.registered``.
Streams should be configured at deployment (declarative), not at runtime; this
module assumes the stream exists.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, ClassVar

import nats
from nats.aio.client import Client as NatsClient
from nats.aio.msg import Msg
from nats.js import JetStreamContext
from nats.js.api import AckPolicy, ConsumerConfig, DeliverPolicy
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from qx.core import IntegrationEvent
from qx.events.registry import EventRegistry, EventTypeNotRegistered

__all__ = [
    "NatsSettings",
    "NatsPublisher",
    "NatsConsumer",
    "create_nats_connection",
]


class NatsSettings(BaseSettings):
    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_prefix="QX_NATS__",
        extra="ignore",
    )

    servers: tuple[str, ...] = ("nats://localhost:4222",)
    max_reconnect_attempts: int = 60
    reconnect_time_wait_seconds: float = 2.0
    connect_timeout_seconds: float = 5.0
    name: str | None = None  # client name shown in `nats-server -m monitoring`


async def create_nats_connection(settings: NatsSettings) -> NatsClient:
    """Connect to NATS with sensible reconnect behavior."""
    return await nats.connect(
        servers=list(settings.servers),
        max_reconnect_attempts=settings.max_reconnect_attempts,
        reconnect_time_wait=settings.reconnect_time_wait_seconds,
        connect_timeout=settings.connect_timeout_seconds,
        name=settings.name,
        # Don't drain on close — we want to surface drops as errors.
        drain_timeout=10,
    )


# ============================================================
# Publisher
# ============================================================


class NatsPublisher:
    """Publish ``IntegrationEvent`` payloads to JetStream.

    Subject is computed as ``f"{prefix}.{event_name}"``. The ``prefix`` is
    typically the service name; configure once at construction.
    """

    def __init__(
        self,
        connection: NatsClient,
        *,
        subject_prefix: str,
    ) -> None:
        self._nc = connection
        self._prefix = subject_prefix
        self._js: JetStreamContext | None = None

    def _jetstream(self) -> JetStreamContext:
        if self._js is None:
            self._js = self._nc.jetstream()
        return self._js

    def _subject(self, event_name: str) -> str:
        return f"{self._prefix}.{event_name}"

    async def publish(self, event: IntegrationEvent) -> None:
        """Publish a single event. Waits for JetStream ack."""
        subject = self._subject(event.event_name)
        payload = json.dumps(event.envelope()).encode()
        # Headers carry W3C trace context and the event metadata in a way that
        # consumers can read without deserializing the body. Useful for routing
        # and observability sidecars.
        headers = {
            "qx.event_name": event.event_name,
            "qx.event_version": str(event.event_version),
            "qx.event_id": str(event.event_id),
        }
        if event.correlation_id:
            headers["qx.correlation_id"] = str(event.correlation_id)
        if event.tenant_id:
            headers["qx.tenant_id"] = str(event.tenant_id)
        await self._jetstream().publish(subject, payload, headers=headers)

    async def publish_raw(
        self,
        event_name: str,
        envelope: dict[str, Any],
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        """Publish a pre-serialized envelope. Used by the outbox relay."""
        subject = self._subject(event_name)
        hdrs = dict(headers or {})
        hdrs.setdefault("qx.event_name", event_name)
        await self._jetstream().publish(
            subject, json.dumps(envelope).encode(), headers=hdrs
        )


# ============================================================
# Consumer
# ============================================================


class NatsConsumer:
    """Durable pull-subscription for an integration-event stream.

    Typical usage in the worker runtime::

        async for msg, event in consumer.messages():
            try:
                await dispatch(event)
                await msg.ack()
            except Exception:
                await msg.nak()
    """

    def __init__(
        self,
        connection: NatsClient,
        registry: EventRegistry,
        *,
        stream: str,
        durable_name: str,
        subject_filter: str,
        batch_size: int = 32,
        fetch_timeout_seconds: float = 5.0,
        max_deliver: int = 5,
        ack_wait_seconds: int = 30,
    ) -> None:
        self._nc = connection
        self._registry = registry
        self._stream = stream
        self._durable = durable_name
        self._subject_filter = subject_filter
        self._batch_size = batch_size
        self._fetch_timeout = fetch_timeout_seconds
        self._max_deliver = max_deliver
        self._ack_wait = ack_wait_seconds
        self._js: JetStreamContext | None = None

    async def _ensure_consumer(self) -> Any:
        """Create or attach to the durable pull consumer."""
        if self._js is None:
            self._js = self._nc.jetstream()
        config = ConsumerConfig(
            durable_name=self._durable,
            filter_subject=self._subject_filter,
            ack_policy=AckPolicy.EXPLICIT,
            deliver_policy=DeliverPolicy.ALL,
            max_deliver=self._max_deliver,
            ack_wait=self._ack_wait,
        )
        # add_consumer is idempotent if the config matches.
        await self._js.add_consumer(self._stream, config=config)
        return await self._js.pull_subscribe(
            self._subject_filter,
            durable=self._durable,
            stream=self._stream,
        )

    @asynccontextmanager
    async def messages(self) -> AsyncIterator[Any]:
        """Yield batches of ``(msg, IntegrationEvent)`` tuples.

        Caller is responsible for ack/nak. Failures during fetch propagate;
        the worker runtime catches and re-tries with backoff.
        """
        sub = await self._ensure_consumer()
        try:
            yield sub
        finally:
            await sub.unsubscribe()

    def parse_message(self, msg: Msg) -> IntegrationEvent:
        """Deserialize a NATS message into a concrete IntegrationEvent."""
        envelope = json.loads(msg.data.decode())
        event_name = envelope.get("event_name") or msg.headers.get(
            "qx.event_name", ""
        )
        event_version = int(envelope.get("event_version", 1))
        try:
            cls = self._registry.lookup(event_name, event_version)
        except EventTypeNotRegistered:
            raise
        # The envelope format puts business payload under "payload".
        payload = envelope.get("payload", {})
        # Rebuild the event with its meta fields.
        return cls.model_validate(
            {
                **payload,
                "event_id": envelope.get("event_id"),
                "occurred_at": envelope.get("occurred_at"),
                "correlation_id": envelope.get("correlation_id"),
                "causation_id": envelope.get("causation_id"),
                "tenant_id": envelope.get("tenant_id"),
                "actor_id": envelope.get("actor_id"),
            }
        )
