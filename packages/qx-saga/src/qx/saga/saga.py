"""Saga base class."""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING, Any, ClassVar, Generic, TypeVar, cast, get_args

from qx.saga.decorators import _ON_ATTR, _TIMEOUT_ATTR
from qx.saga.state import SagaState

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import timedelta

    from qx.core import IntegrationEvent
    from qx.cqrs import Mediator

TSagaState = TypeVar("TSagaState", bound=SagaState)

_MISSING = object()


class SagaMeta(type):
    """Collect @on and @on_timeout handlers at class-definition time."""

    def __new__(
        mcs,
        name: str,
        bases: tuple[type, ...],
        namespace: dict[str, Any],
    ) -> Any:
        handlers: dict[type[Any], Callable[..., Any]] = {}
        timeout_handler: Callable[..., Any] | None = None
        timeout_after: timedelta | None = None

        for attr in namespace.values():
            event_type = getattr(attr, _ON_ATTR, _MISSING)
            if event_type is not _MISSING:
                handlers[cast("type[Any]", event_type)] = attr

            timeout = getattr(attr, _TIMEOUT_ATTR, _MISSING)
            if timeout is not _MISSING:
                timeout_handler = attr
                timeout_after = cast("timedelta", timeout)

        # Inherit from base saga classes
        for base in bases:
            for et, fn in getattr(base, "_handlers", {}).items():
                handlers.setdefault(et, fn)
            if timeout_handler is None:
                timeout_handler = getattr(base, "_timeout_handler", None)
                timeout_after = getattr(base, "_timeout_after", None)

        cls = super().__new__(mcs, name, bases, namespace)
        cls._handlers = handlers  # type: ignore[attr-defined]
        cls._timeout_handler = timeout_handler  # type: ignore[attr-defined]
        cls._timeout_after = timeout_after  # type: ignore[attr-defined]
        return cls


class Saga(Generic[TSagaState], metaclass=SagaMeta):  # noqa: UP046
    """Base class for all sagas.

    Subclass, declare ``state_type``, and decorate methods with ``@on``::

        class MyState(SagaState):
            order_id: str = ""

        class MyWorkflow(Saga[MyState]):
            state_type = MyState

            @on(OrderPlaced)
            async def start(self, ev: OrderPlaced) -> None:
                self.state.order_id = ev.order_id
                await self.dispatch(DoSomethingCommand(order_id=ev.order_id))

    The manager sets ``state``, ``mediator``, and ``correlation_key`` before
    calling any handler. Do not override ``__init__``.
    """

    state_type: ClassVar[type[SagaState]]

    # Set by SagaManager before handler is called — not part of user API
    state: TSagaState
    _mediator: Mediator
    _completed: bool
    _failed: bool

    # Collected by SagaMeta
    _handlers: ClassVar[dict[type[Any], Callable[..., Any]]]
    _timeout_handler: ClassVar[Callable[..., Any] | None]
    _timeout_after: ClassVar[timedelta | None]

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        # Auto-derive state_type from Generic[TSagaState] if not set explicitly
        if "state_type" not in cls.__dict__:
            for base in getattr(cls, "__orig_bases__", []):
                args = get_args(base)
                if args and inspect.isclass(args[0]) and issubclass(args[0], SagaState):
                    cls.state_type = args[0]
                    break

    async def dispatch(self, command: Any) -> Any:
        """Dispatch a command through the mediator."""
        return await self._mediator.send(command)

    def mark_complete(self) -> None:
        """Signal that this saga instance has reached its final state."""
        self._completed = True

    def mark_failed(self) -> None:
        """Signal that this saga has failed and needs compensation."""
        self._failed = True

    async def compensate(self) -> None:
        """Override to undo steps completed before a failure.

        Called automatically by the manager when ``mark_failed()`` is set
        or when an unhandled exception escapes a handler.
        """

    @classmethod
    def handles(cls, event_type: type[Any]) -> bool:
        return event_type in cls._handlers

    async def handle(self, event: IntegrationEvent) -> None:
        handler = self._handlers.get(type(event))
        if handler is None:
            return
        await handler(self, event)
