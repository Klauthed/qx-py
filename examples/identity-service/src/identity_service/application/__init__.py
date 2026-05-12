"""Application layer.

Command/query handlers, application DTOs. Two public hooks consumed by
``main.py``:

- ``register_events(registry)`` — tells the worker which IntegrationEvent
  types this service produces or consumes, by class.
- ``register_handlers(mediator, container)`` — discovers every
  ``@command_handler`` / ``@query_handler`` / ``@integration_event_handler``
  in the application package and wires them into the mediator.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from identity_service.domain.aggregates.user import (
    UserEmailChanged,
    UserRegisteredIntegration,
)

if TYPE_CHECKING:
    from qx.cqrs import Mediator
    from qx.di import Container
    from qx.events import EventRegistry


def register_events(registry: EventRegistry) -> None:
    """Register every IntegrationEvent class this service produces or consumes.

    Used by the worker to deserialize incoming NATS messages and by tests to
    enumerate the service's event surface.
    """
    registry.register(UserRegisteredIntegration)
    registry.register(UserEmailChanged)


def register_handlers(mediator: Mediator, _container: Container) -> int:
    """Discover and register every CQRS handler under this package.

    Imports the handler submodules so their decorators run, then passes the
    modules to ``register_decorated`` for the scan. Returns the count of
    handlers registered.
    """
    from identity_service.application.commands import change_email, create_user  # noqa: PLC0415
    from identity_service.application.integration_handlers import (  # noqa: PLC0415
        on_user_registered,
    )
    from identity_service.application.queries import get_user, list_users  # noqa: PLC0415

    return mediator.register_decorated(
        create_user,
        change_email,
        get_user,
        list_users,
        on_user_registered,
    )
