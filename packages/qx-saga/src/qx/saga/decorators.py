"""Decorators for wiring saga event handlers and timeouts."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import timedelta

_ON_ATTR = "__qx_saga_on__"
_TIMEOUT_ATTR = "__qx_saga_timeout__"


def on(event_type: type[Any]) -> Callable[[Any], Any]:
    """Mark a method as the handler for a specific integration-event type.

    The method receives ``(self, event: EventType) -> None``. Only one
    ``@on`` handler per event type is allowed per saga class.

    Example::

        @on(OrderPlaced)
        async def start(self, ev: OrderPlaced) -> None:
            self.state.order_id = ev.order_id
            await self.dispatch(ReserveInventoryCommand(order_id=ev.order_id))
    """

    def decorator(fn: Any) -> Any:
        setattr(fn, _ON_ATTR, event_type)
        return fn

    return decorator


def on_timeout(*, after: timedelta) -> Callable[[Any], Any]:
    """Mark a method as the timeout handler.

    The saga manager compares ``updated_at`` against ``after`` and calls
    this method when the threshold is exceeded. Only one timeout handler
    is allowed per saga class.

    Example::

        @on_timeout(after=timedelta(hours=1))
        async def handle_timeout(self) -> None:
            await self.compensate()
    """

    def decorator(fn: Any) -> Any:
        setattr(fn, _TIMEOUT_ATTR, after)
        return fn

    return decorator
