"""Qx events: outbox relay, NATS publisher/consumer, mediator bridge."""

from __future__ import annotations

from qx.events.dispatcher import MediatorEventDispatcher
from qx.events.nats import (
    NatsConsumer,
    NatsPublisher,
    NatsSettings,
    create_nats_connection,
)
from qx.events.outbox_relay import OutboxRelay
from qx.events.registry import EventRegistry, EventTypeNotRegistered

__version__ = "0.2.0"

__all__ = [
    "EventRegistry",
    "EventTypeNotRegistered",
    "MediatorEventDispatcher",
    "NatsConsumer",
    "NatsPublisher",
    "NatsSettings",
    "OutboxRelay",
    "__version__",
    "create_nats_connection",
]
