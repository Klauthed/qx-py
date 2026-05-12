"""Qx CQRS / Mediator.

Public surface — import from ``qx.cqrs``.

Quick start::

    from qx.cqrs import Command, Query, Mediator, command_handler

    class CreateUserCommand(Command[UserDto]):
        email: str
        name: str

    @command_handler(CreateUserCommand)
    class CreateUserHandler:
        def __init__(self, repo: UserRepository, uow: UnitOfWork): ...
        async def handle(self, cmd):
            ...

    # In bootstrap:
    mediator = Mediator(container)
    mediator.register_decorated(myapp.application.commands)

    # In an HTTP route:
    result = await mediator.send(CreateUserCommand(email=..., name=...))
"""

from __future__ import annotations

from qx.cqrs.decorators import (
    command_handler,
    event_handler,
    integration_event_handler,
    notification_handler,
    query_handler,
    requires,
)
from qx.cqrs.handlers import (
    CommandHandler,
    EventHandler,
    IntegrationEventHandler,
    NotificationHandler,
    QueryHandler,
)
from qx.cqrs.mediator import Mediator, MediatorError
from qx.cqrs.messages import Command, Query, Request, TResult
from qx.cqrs.pipeline import (
    Behavior,
    BehaviorChain,
    ExceptionTranslationBehavior,
    LoggingBehavior,
    ValidationBehavior,
    TransactionBehavior,
    RetryBehavior,
    IdempotencyBehavior,
    AuthorizationBehavior,
    compose,
    get_current_uow,
)

__version__ = "0.1.0"

__all__ = [
    # Messages
    "Command",
    "Query",
    "Request",
    "TResult",
    # Handlers
    "CommandHandler",
    "QueryHandler",
    "EventHandler",
    "IntegrationEventHandler",
    "NotificationHandler",
    # Decorators
    "command_handler",
    "query_handler",
    "event_handler",
    "integration_event_handler",
    "notification_handler",
    "requires",
    # Mediator
    "Mediator",
    "MediatorError",
    # Pipeline
    "Behavior",
    "BehaviorChain",
    "compose",
    "LoggingBehavior",
    "ExceptionTranslationBehavior",
    "ValidationBehavior",
    "TransactionBehavior",
    "RetryBehavior",
    "IdempotencyBehavior",
    "AuthorizationBehavior",
    "get_current_uow",
    "__version__",
]
