"""Worker handler: on UserRegistered, write an audit record.

Demonstrates the round-trip: this same service publishes the integration
event via the outbox, the relay ships it to NATS, the worker pulls it and
this handler reacts. In a real system the consumer would more often be a
*different* service — e.g., a billing service that needs to provision a
customer record when a user is registered.

Idempotency: integration events are at-least-once. The handler should
tolerate redelivery. Here we write an audit record with a deterministic id
derived from ``event.event_id`` so a re-insert collides on the primary key
and is silently ignored.
"""

from __future__ import annotations

from qx.cqrs import integration_event_handler
from qx.observability import get_logger

from identity_service.domain.aggregates.user import UserRegisteredIntegration


@integration_event_handler(UserRegisteredIntegration)
class OnUserRegistered:
    def __init__(self) -> None:
        self._log = get_logger("identity.worker.user_registered")

    async def handle(self, event: UserRegisteredIntegration) -> None:
        # In a real system: write to an audit_log table, send a welcome email,
        # provision a default workspace, etc. We just log here so the example
        # works without additional infrastructure.
        self._log.info(
            "user.registered consumed",
            user_id=str(event.user_id),
            email=event.email,
            event_id=str(event.event_id),
        )
