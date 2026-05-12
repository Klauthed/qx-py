"""Handler protocols.

Each kind of message has a corresponding handler protocol. Handlers are async,
return ``Result[T]`` (except event/notification handlers which return ``None``),
and live as classes (not bare functions) so the DI container can manage their
construction and dependencies.

Why classes not functions? Two reasons:

1. Constructor injection is a clearer story than function-parameter injection
   for handlers that have many dependencies. ``CreateUserHandler(repo, uow,
   policy, mailer)`` reads better than a 5-arg async def.

2. State that's expensive to construct (compiled JSON schemas, cached
   PolicyEvaluator graphs) is naturally hung off ``self``.

The trade-off is one indirection (``handler.handle(cmd)`` vs ``handler(cmd)``);
worth it.
"""

from __future__ import annotations

from typing import Generic, Protocol, TypeVar, runtime_checkable

from qx.core import DomainEvent, IntegrationEvent, Notification, Result

from qx.cqrs.messages import Command, Query

TCommand = TypeVar("TCommand", bound=Command)
TQuery = TypeVar("TQuery", bound=Query)
TEvent = TypeVar("TEvent", bound=DomainEvent)
TIntegration = TypeVar("TIntegration", bound=IntegrationEvent)
TNotification = TypeVar("TNotification", bound=Notification)
TResult = TypeVar("TResult")

__all__ = [
    "CommandHandler",
    "QueryHandler",
    "EventHandler",
    "IntegrationEventHandler",
    "NotificationHandler",
]


@runtime_checkable
class CommandHandler(Protocol, Generic[TCommand, TResult]):
    """Handle a command. Return a ``Result[TResult]``.

    Handlers MUST NOT raise for business failures — return ``Result.failure``
    instead. Exceptions should be reserved for genuine bugs and infrastructure
    faults; the mediator translates them into ``InfrastructureError`` results
    so the pipeline can decide on retry policy uniformly.
    """

    async def handle(self, command: TCommand) -> Result[TResult]: ...


@runtime_checkable
class QueryHandler(Protocol, Generic[TQuery, TResult]):
    """Handle a query. Return a ``Result[TResult]`` reading without side effects."""

    async def handle(self, query: TQuery) -> Result[TResult]: ...


@runtime_checkable
class EventHandler(Protocol, Generic[TEvent]):
    """Handle a domain event. In-process. Failures roll back the transaction.

    Events may have multiple handlers; the mediator dispatches to all and
    aggregates failures via ``BaseExceptionGroup`` so partial failures are
    visible.
    """

    async def handle(self, event: TEvent) -> None: ...


@runtime_checkable
class IntegrationEventHandler(Protocol, Generic[TIntegration]):
    """Handle a cross-process integration event (consumed from the broker).

    Distinct from ``EventHandler`` because the lifecycle differs: integration
    events arrive via the message broker, get acked/nacked, and have at-least-
    once semantics. Handlers must be idempotent.
    """

    async def handle(self, event: TIntegration) -> None: ...


@runtime_checkable
class NotificationHandler(Protocol, Generic[TNotification]):
    """Handle a notification. Fire-and-forget; failures don't affect the source."""

    async def handle(self, notification: TNotification) -> None: ...
