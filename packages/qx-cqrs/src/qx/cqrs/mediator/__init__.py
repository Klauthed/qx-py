"""The Mediator: dispatch hub for commands, queries, and events.

The mediator decouples senders from handlers. Three things use it:

1. HTTP controllers: ``await mediator.send(CreateUserCommand(...))``
2. Domain code: ``await mediator.publish(UserRegistered(...))``
3. Workers: integration events arrive on the broker → mediator dispatches
   to integration handlers.

Three registration modes (per the project decision):

A) **Decorator + scan** (default)::

       @command_handler(CreateUserCommand)
       class CreateUserHandler:
           async def handle(self, cmd): ...

       mediator.register_decorated(some_module)  # walks and registers

B) **Explicit**::

       mediator.register_command(CreateUserCommand, CreateUserHandler)

C) **Type-based auto** (introspect ``__orig_bases__``)::

       class CreateUserHandler(CommandHandler[CreateUserCommand, UserDto]):
           async def handle(self, cmd): ...

       mediator.register_typed(CreateUserHandler)

All three coexist. Decorators are encouraged in feature code; explicit is for
test wiring and exotic cases; type-based is convenient when the handler
already has the protocol generic parameters spelled out for static checking.

Handlers are resolved from the DI container at dispatch time, so they can
themselves have constructor dependencies that flow through normally.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import Any, get_args, get_origin, get_type_hints

from qx.core import (
    DomainEvent,
    Error,
    IntegrationEvent,
    Notification,
    Result,
)
from qx.di import Container, Scope

from qx.cqrs.handlers import (
    CommandHandler,
    EventHandler,
    IntegrationEventHandler,
    NotificationHandler,
    QueryHandler,
)
from qx.cqrs.messages import Command, Query
from qx.cqrs.pipeline import Behavior, compose

__all__ = ["Mediator", "MediatorError"]


class MediatorError(Exception):
    """Raised when dispatch can't proceed (no handler, type mismatch)."""


class Mediator:
    """The central dispatcher.

    The mediator is a singleton in the DI container. It holds *registrations*
    (mapping from message type to handler type) but resolves the handler
    instance per-dispatch from the container, so handler scope/lifetime is
    governed by the container.
    """

    def __init__(
        self,
        container: Container,
        *,
        command_behaviors: tuple[Behavior, ...] = (),
        query_behaviors: tuple[Behavior, ...] = (),
    ) -> None:
        self._container = container
        self._command_behaviors = command_behaviors
        self._query_behaviors = query_behaviors

        # message_type → handler_type
        self._command_handlers: dict[type[Command[Any]], type[Any]] = {}
        self._query_handlers: dict[type[Query[Any]], type[Any]] = {}
        # Events fan out: list of handler types per event type.
        self._event_handlers: dict[type[DomainEvent], list[type[Any]]] = {}
        self._integration_handlers: dict[type[IntegrationEvent], list[type[Any]]] = {}
        self._notification_handlers: dict[type[Notification], list[type[Any]]] = {}

    # ============================================================
    # Registration — explicit
    # ============================================================

    def register_command(
        self,
        message: type[Command[Any]],
        handler: type[Any],
    ) -> "Mediator":
        if message in self._command_handlers:
            raise MediatorError(
                f"Command {message.__name__} already has handler "
                f"{self._command_handlers[message].__name__}"
            )
        self._command_handlers[message] = handler
        # Ensure the handler can be resolved from DI even if not explicitly registered.
        if handler not in self._container._providers:  # noqa: SLF001
            self._container.register_transient(handler, handler)
        return self

    def register_query(
        self,
        message: type[Query[Any]],
        handler: type[Any],
    ) -> "Mediator":
        if message in self._query_handlers:
            raise MediatorError(
                f"Query {message.__name__} already has handler "
                f"{self._query_handlers[message].__name__}"
            )
        self._query_handlers[message] = handler
        if handler not in self._container._providers:  # noqa: SLF001
            self._container.register_transient(handler, handler)
        return self

    def register_event(
        self,
        event: type[DomainEvent],
        handler: type[Any],
    ) -> "Mediator":
        self._event_handlers.setdefault(event, []).append(handler)
        if handler not in self._container._providers:  # noqa: SLF001
            self._container.register_transient(handler, handler)
        return self

    def register_integration(
        self,
        event: type[IntegrationEvent],
        handler: type[Any],
    ) -> "Mediator":
        self._integration_handlers.setdefault(event, []).append(handler)
        if handler not in self._container._providers:  # noqa: SLF001
            self._container.register_transient(handler, handler)
        return self

    def register_notification(
        self,
        notification: type[Notification],
        handler: type[Any],
    ) -> "Mediator":
        self._notification_handlers.setdefault(notification, []).append(handler)
        if handler not in self._container._providers:  # noqa: SLF001
            self._container.register_transient(handler, handler)
        return self

    # ============================================================
    # Registration — decorator + scan
    # ============================================================

    def register_decorated(self, *modules_or_classes: Any) -> int:
        """Register every class stamped by a CQRS decorator.

        Pass either modules (we walk their direct members, classes only — we
        don't recurse into other modules to avoid following arbitrary import
        graphs) or classes directly. Returns the number of registrations.
        """
        from qx.cqrs.decorators import HANDLER_MARKER, HandlerKind

        n = 0
        seen: set[int] = set()

        def _process_class(obj: type) -> None:
            nonlocal n
            if id(obj) in seen:
                return
            seen.add(id(obj))
            meta = getattr(obj, HANDLER_MARKER, None)
            if meta is None or HANDLER_MARKER not in obj.__dict__:
                return
            kind: HandlerKind = meta.kind
            target: type = meta.target
            if kind == "command":
                self.register_command(target, obj)  # type: ignore[arg-type]
            elif kind == "query":
                self.register_query(target, obj)  # type: ignore[arg-type]
            elif kind == "event":
                self.register_event(target, obj)  # type: ignore[arg-type]
            elif kind == "integration":
                self.register_integration(target, obj)  # type: ignore[arg-type]
            elif kind == "notification":
                self.register_notification(target, obj)  # type: ignore[arg-type]
            else:
                raise MediatorError(f"Unknown handler kind: {kind!r}")
            n += 1

        for obj in modules_or_classes:
            if inspect.isclass(obj):
                _process_class(obj)
                continue
            if inspect.ismodule(obj):
                # Walk *direct* class members defined in this module only —
                # importing other modules' classes is normal and we don't
                # want to register them again here.
                for _name, member in inspect.getmembers(obj, inspect.isclass):
                    if getattr(member, "__module__", None) == obj.__name__:
                        _process_class(member)
                continue
            # Fallback: ignore other kinds of objects silently.
        return n

    # ============================================================
    # Registration — type-based auto
    # ============================================================

    def register_typed(self, *handler_classes: type[Any]) -> int:
        """Inspect handler classes for protocol generics and register accordingly.

        E.g., a class declared as ``CommandHandler[CreateUserCommand, UserDto]``
        gets registered as the command handler for ``CreateUserCommand``.
        """
        registered = 0
        for cls in handler_classes:
            kind, target = self._inspect_handler(cls)
            if kind is None:
                raise MediatorError(
                    f"{cls.__name__} does not declare a handler protocol "
                    f"(CommandHandler[X, R] / QueryHandler[X, R] / EventHandler[X] / "
                    f"IntegrationEventHandler[X] / NotificationHandler[X])."
                )
            if kind == "command":
                self.register_command(target, cls)
            elif kind == "query":
                self.register_query(target, cls)
            elif kind == "event":
                self.register_event(target, cls)
            elif kind == "integration":
                self.register_integration(target, cls)
            elif kind == "notification":
                self.register_notification(target, cls)
            registered += 1
        return registered

    @staticmethod
    def _inspect_handler(cls: type[Any]) -> tuple[str | None, Any]:
        """Walk ``__orig_bases__`` to find the handler protocol the class implements."""
        for base in getattr(cls, "__orig_bases__", ()):
            origin = get_origin(base)
            if origin is None:
                continue
            args = get_args(base)
            if origin is CommandHandler and len(args) >= 1:
                return "command", args[0]
            if origin is QueryHandler and len(args) >= 1:
                return "query", args[0]
            if origin is EventHandler and len(args) >= 1:
                return "event", args[0]
            if origin is IntegrationEventHandler and len(args) >= 1:
                return "integration", args[0]
            if origin is NotificationHandler and len(args) >= 1:
                return "notification", args[0]
        return None, None

    # ============================================================
    # Dispatch
    # ============================================================

    async def send(
        self,
        message: Command[Any] | Query[Any],
        *,
        scope: Scope | None = None,
    ) -> Result[Any]:
        """Dispatch a command or query and return its Result."""
        if isinstance(message, Command):
            return await self._dispatch_request(
                message,
                self._command_handlers,
                self._command_behaviors,
                scope=scope,
                kind="command",
            )
        if isinstance(message, Query):
            return await self._dispatch_request(
                message,
                self._query_handlers,
                self._query_behaviors,
                scope=scope,
                kind="query",
            )
        raise MediatorError(
            f"send() requires a Command or Query, got {type(message).__name__}"
        )

    async def _dispatch_request(
        self,
        message: Any,
        registry: dict[type, type[Any]],
        behaviors: tuple[Behavior, ...],
        *,
        scope: Scope | None,
        kind: str,
    ) -> Result[Any]:
        msg_type = type(message)
        handler_type = registry.get(msg_type)
        if handler_type is None:
            raise MediatorError(
                f"No {kind} handler registered for {msg_type.__name__}"
            )

        async def terminal(m: Any) -> Result[Any]:
            handler = await self._container.resolve(handler_type, scope=scope)
            return await handler.handle(m)

        execute = compose(behaviors, terminal)
        return await execute(message)

    async def publish(
        self,
        event: DomainEvent | Notification,
        *,
        scope: Scope | None = None,
    ) -> None:
        """Fan out an in-process event to every registered handler.

        DomainEvent handlers run sequentially in registration order, in the
        same scope as the publisher. Their failures are aggregated into a
        ``BaseExceptionGroup``.

        Notification handlers run with failures suppressed (logged but not
        raised) — that's the contract: notifications never break the
        publisher.
        """
        if isinstance(event, DomainEvent):
            handlers = self._event_handlers.get(type(event), [])
            errors: list[BaseException] = []
            for ht in handlers:
                handler = await self._container.resolve(ht, scope=scope)
                try:
                    await handler.handle(event)
                except BaseException as exc:  # noqa: BLE001
                    errors.append(exc)
            if errors:
                raise BaseExceptionGroup(
                    f"{len(errors)} handler(s) failed for {type(event).__name__}",
                    errors,
                )
            return

        if isinstance(event, Notification):
            import logging

            log = logging.getLogger("qx.cqrs.notification")
            for ht in self._notification_handlers.get(type(event), []):
                try:
                    handler = await self._container.resolve(ht, scope=scope)
                    await handler.handle(event)
                except BaseException as exc:  # noqa: BLE001
                    log.warning(
                        "notification handler %s failed for %s: %s",
                        ht.__name__,
                        type(event).__name__,
                        exc,
                    )
            return

        raise MediatorError(
            f"publish() requires DomainEvent or Notification, got {type(event).__name__}"
        )

    async def consume_integration(
        self,
        event: IntegrationEvent,
        *,
        scope: Scope | None = None,
    ) -> None:
        """Dispatch an incoming integration event to its handlers.

        Called by the worker runtime, never by application code directly.
        Failures propagate so the worker can nack and retry.
        """
        handlers = self._integration_handlers.get(type(event), [])
        if not handlers:
            raise MediatorError(
                f"No integration handler registered for {type(event).__name__}"
            )
        for ht in handlers:
            handler = await self._container.resolve(ht, scope=scope)
            await handler.handle(event)

    # ============================================================
    # Introspection
    # ============================================================

    @property
    def registered_commands(self) -> list[type]:
        return list(self._command_handlers)

    @property
    def registered_queries(self) -> list[type]:
        return list(self._query_handlers)

    @property
    def registered_events(self) -> list[type]:
        return list(self._event_handlers)

    @property
    def registered_integrations(self) -> list[type]:
        return list(self._integration_handlers)

    @property
    def registered_notifications(self) -> list[type]:
        return list(self._notification_handlers)
