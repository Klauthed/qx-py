"""Event base types.

We distinguish three flavors of "something happened" messaging:

1. **DomainEvent** — in-process, fired inside a transaction, processed by other
   parts of the same service. Handlers run synchronously with the writing
   transaction so failures roll back. Example: "user registered" updating a
   read model.

2. **IntegrationEvent** — cross-process, published to other services via the
   outbox. Never dispatched in-process; always durable. Example: "user
   registered" notifying the billing service.

3. **Notification** — in-process, fire-and-forget, side-channel. Used for
   logging, audit, metrics. Failures in notification handlers should not affect
   the originating command. Example: "audit log this admin action".

The three types share base fields but the dispatcher treats them differently.
Mixing them up is the most common CQRS mistake — when in doubt, choose the more
restrictive option.
"""

from __future__ import annotations

from typing import Any, ClassVar
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field
from qx.core.utils.time import utcnow

__all__ = [
    "DomainEvent",
    "Event",
    "IntegrationEvent",
    "Notification",
]


class Event(BaseModel):
    """Base event. Carries identity, timing, causation, correlation.

    Subclasses set ``event_name`` as a stable string used by routers and the
    outbox schema. The ``version`` field lets the same logical event evolve
    without breaking subscribers — bump it when the payload shape changes in a
    non-additive way.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    event_id: UUID = Field(default_factory=uuid4)
    event_name: ClassVar[str] = ""  # subclasses must override
    event_version: ClassVar[int] = 1

    occurred_at: Any = Field(default_factory=utcnow)
    correlation_id: UUID | None = None
    causation_id: UUID | None = None
    tenant_id: UUID | None = None
    actor_id: UUID | None = None

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        # Enforce that concrete events define event_name. Abstract intermediates
        # (DomainEvent, IntegrationEvent, Notification) are skipped.
        if cls.__name__ in {"DomainEvent", "IntegrationEvent", "Notification"}:
            return
        if not cls.event_name:
            raise TypeError(
                f"{cls.__qualname__} must define a non-empty class-level "
                "`event_name` for routing and outbox storage."
            )

    def envelope(self) -> dict[str, Any]:
        """Serialization envelope used by outbox and message bus."""
        return {
            "event_id": str(self.event_id),
            "event_name": self.event_name,
            "event_version": self.event_version,
            "occurred_at": self.occurred_at.isoformat(),
            "correlation_id": str(self.correlation_id) if self.correlation_id else None,
            "causation_id": str(self.causation_id) if self.causation_id else None,
            "tenant_id": str(self.tenant_id) if self.tenant_id else None,
            "actor_id": str(self.actor_id) if self.actor_id else None,
            "payload": self.model_dump(
                mode="json",
                exclude={
                    "event_id",
                    "occurred_at",
                    "correlation_id",
                    "causation_id",
                    "tenant_id",
                    "actor_id",
                },
            ),
        }


class DomainEvent(Event):
    """In-process event. Handlers run in the writing transaction."""


class IntegrationEvent(Event):
    """Cross-process event. Published via the transactional outbox.

    Subclasses should consider their payload a public schema contract: subscribers
    in other services rely on field stability. Bump ``event_version`` for breaking
    changes and keep both versions live during the migration window.
    """


class Notification(Event):
    """In-process fire-and-forget event. Failures do not affect the originator."""
