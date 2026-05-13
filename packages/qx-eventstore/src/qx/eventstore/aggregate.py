"""EventSourcedAggregate — base class for event-sourced aggregates."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, TypeVar

if TYPE_CHECKING:
    from qx.core.domain.events import DomainEvent

__all__ = ["EventSourcedAggregate"]

TId = TypeVar("TId")

_APPLY_PREFIX = "apply_"


@dataclass
class EventSourcedAggregate[TId]:
    """Base class for aggregates persisted as an event stream.

    Subclass and implement ``apply_<EventClassName>`` methods — they are the
    *only* state mutators. Command methods call ``record_event`` which buffers
    the event and immediately applies it::

        class Account(EventSourcedAggregate[Identifier]):
            balance: int = field(default=0)

            def deposit(self, amount: int) -> None:
                self.record_event(MoneyDeposited(amount=amount))

            def apply_money_deposited(self, ev: MoneyDeposited) -> None:
                self.balance += ev.amount

    The repository loads the event stream, calls ``_replay`` to restore
    state, then drains pending events on save.
    """

    id: TId
    version: int = field(default=0, compare=False)
    _pending_events: list[DomainEvent] = field(
        default_factory=list, repr=False, compare=False, init=False
    )

    def record_event(self, event: DomainEvent) -> None:
        """Buffer an event and apply it immediately to update local state."""
        self._pending_events.append(event)
        self._apply(event)

    def pull_events(self) -> list[DomainEvent]:
        """Drain and return pending events. Called by the repository at save time."""
        events = list(self._pending_events)
        self._pending_events.clear()
        return events

    @property
    def has_pending_events(self) -> bool:
        return bool(self._pending_events)

    def _apply(self, event: DomainEvent) -> None:
        """Dispatch to the appropriate apply_* method if it exists."""
        method_name = f"{_APPLY_PREFIX}{type(event).__name__.lower()}"
        handler = getattr(self, method_name, None)
        if handler is not None:
            handler(event)

    def _replay(self, events: list[DomainEvent], *, version: int) -> None:
        """Restore state by replaying a sequence of persisted events.

        Called by the repository after loading the event stream. Does NOT
        buffer events into ``_pending_events``; they are already persisted.
        """
        for event in events:
            self._apply(event)
        self.version = version

    @classmethod
    def _from_events(
        cls,
        id: TId,
        events: list[DomainEvent],
        *,
        version: int,
    ) -> Any:
        """Reconstruct an instance from its full event history."""
        instance: EventSourcedAggregate[TId] = object.__new__(cls)
        instance.id = id
        instance.version = 0
        instance._pending_events = []
        instance._replay(events, version=version)
        return instance
