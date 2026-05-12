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
    AuthorizationBehavior,
    Behavior,
    BehaviorChain,
    ExceptionTranslationBehavior,
    IdempotencyBehavior,
    LoggingBehavior,
    RetryBehavior,
    TransactionBehavior,
    ValidationBehavior,
    compose,
    get_current_uow,
)

__version__ = "0.1.0"

__all__ = [
    "AuthorizationBehavior",
    # Pipeline
    "Behavior",
    "BehaviorChain",
    # Messages
    "Command",
    # Handlers
    "CommandHandler",
    "EventHandler",
    "ExceptionTranslationBehavior",
    "IdempotencyBehavior",
    "IntegrationEventHandler",
    "LoggingBehavior",
    # Mediator
    "Mediator",
    "MediatorError",
    "NotificationHandler",
    "Query",
    "QueryHandler",
    "Request",
    "RetryBehavior",
    "TResult",
    "TransactionBehavior",
    "ValidationBehavior",
    "__version__",
    # Decorators
    "command_handler",
    "compose",
    "event_handler",
    "get_current_uow",
    "integration_event_handler",
    "notification_handler",
    "query_handler",
    "requires",
]
