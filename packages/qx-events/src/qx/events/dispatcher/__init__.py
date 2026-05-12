"""Bridge between ``qx-db.UnitOfWork`` and ``qx-cqrs.Mediator``.

The UoW depends on a thin ``EventDispatcher`` protocol (one method,
``publish(event)``). The Mediator is a richer object. This module supplies a
trivial adapter so wiring in service bootstrap is one line::

    container.register_singleton(
        EventDispatcher,
        lambda m: MediatorEventDispatcher(m),
    )
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from qx.core import DomainEvent
    from qx.cqrs import Mediator

__all__ = ["MediatorEventDispatcher"]


class MediatorEventDispatcher:
    """Adapt ``Mediator.publish`` to the ``EventDispatcher`` protocol."""

    def __init__(self, mediator: Mediator) -> None:
        self._m = mediator

    async def publish(self, event: DomainEvent) -> None:
        await self._m.publish(event)
