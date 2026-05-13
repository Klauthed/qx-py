"""SagaManager — routes integration events to saga instances."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from qx.saga.store import _COMPENSATED, _COMPLETED, _RUNNING, SagaStore

if TYPE_CHECKING:
    from qx.core import IntegrationEvent
    from qx.cqrs import Mediator
    from qx.saga.saga import Saga
    from sqlalchemy.ext.asyncio import AsyncSession

__all__ = ["SagaManager"]


class SagaManager:
    """Routes integration events to registered saga classes.

    One manager instance is shared across all saga types. Register saga
    classes at startup::

        manager = SagaManager(mediator=mediator, table=saga_table)
        manager.register(OrderFulfillmentSaga, correlation_key=lambda ev: ev.order_id)

    Then wire the manager as an integration event handler in the worker —
    the worker calls ``manager.handle(event, session=...)`` for each message.

    **Correlation key** is how the manager maps an inbound event to a specific
    saga instance (e.g. ``order_id``). The lambda receives the raw event and
    returns a string.

    **Lifecycle per event**

    1. Load the instance by ``(saga_type, correlation_key)``.
    2. If none exists and the saga handles this event type → create a new instance.
    3. If the instance is already terminal → skip (idempotency).
    4. Hydrate a saga object, call ``handle(event)``.
    5. If the handler sets ``mark_complete()`` → persist as completed.
    6. If the handler raises or calls ``mark_failed()`` → run ``compensate()``
       then persist as compensated.
    7. Otherwise persist updated state and keep status as running.
    """

    def __init__(self, mediator: Mediator, table: Any) -> None:
        self._mediator = mediator
        self._table = table
        self._registrations: list[tuple[type[Saga[Any]], Any]] = []

    def register(
        self,
        saga_class: type[Saga[Any]],
        *,
        correlation_key: Any,
    ) -> None:
        """Register a saga class with a correlation-key extractor.

        ``correlation_key`` must be a callable ``(event) -> str``. It is
        called for every inbound event — return ``None`` to skip the event
        for this saga class.
        """
        self._registrations.append((saga_class, correlation_key))

    async def handle(self, event: IntegrationEvent, *, session: AsyncSession) -> None:
        """Process one integration event against all registered saga classes."""
        store = SagaStore(session, self._table)
        for saga_class, key_fn in self._registrations:
            if not saga_class.handles(type(event)):
                continue
            corr_key: str | None = key_fn(event)
            if corr_key is None:
                continue
            await self._dispatch_to(
                saga_class=saga_class,
                correlation_key=corr_key,
                event=event,
                store=store,
            )

    async def tick_timeouts(self, *, session: AsyncSession) -> None:
        """Check for timed-out sagas and invoke their timeout handlers.

        Call periodically from a background task or scheduled job::

            while True:
                async with session_factory() as session:
                    await manager.tick_timeouts(session=session)
                    await session.commit()
                await asyncio.sleep(60)
        """
        store = SagaStore(session, self._table)
        now = datetime.now(UTC)
        for saga_class, _ in self._registrations:
            timeout_after = saga_class._timeout_after
            if timeout_after is None or saga_class._timeout_handler is None:
                continue
            threshold = datetime.fromtimestamp(
                now.timestamp() - timeout_after.total_seconds(), tz=UTC
            )
            saga_type = _saga_type_key(saga_class)
            instances = await store.load_timed_out(saga_type, threshold)
            for instance in instances:
                saga = self._hydrate(saga_class, instance.state_data)
                try:
                    await saga_class._timeout_handler(saga)
                    new_status = (
                        _COMPLETED
                        if saga._completed
                        else (_COMPENSATED if saga._failed else _RUNNING)
                    )
                except Exception:
                    await saga.compensate()
                    new_status = _COMPENSATED
                await store.save(
                    instance,
                    new_state=saga.state.model_dump(mode="json"),
                    new_status=new_status,
                )

    async def _dispatch_to(
        self,
        *,
        saga_class: type[Saga[Any]],
        correlation_key: str,
        event: IntegrationEvent,
        store: SagaStore,
    ) -> None:
        saga_type = _saga_type_key(saga_class)
        result = await store.load(saga_type, correlation_key)
        if result.is_failure:
            return

        instance = result.value

        if instance is None:
            # First event for this correlation key — create the instance
            state_data = saga_class.state_type().model_dump(mode="json")
            create_result = await store.create(saga_type, correlation_key, state_data)
            if create_result.is_failure:
                return
            instance = create_result.value

        if instance.is_terminal:
            return  # idempotent — already done

        saga = self._hydrate(saga_class, instance.state_data)

        try:
            await saga.handle(event)
            if saga._failed:
                await saga.compensate()
                new_status = _COMPENSATED
            elif saga._completed:
                new_status = _COMPLETED
            else:
                new_status = _RUNNING
        except Exception:
            await saga.compensate()
            new_status = _COMPENSATED

        await store.save(
            instance,
            new_state=saga.state.model_dump(mode="json"),
            new_status=new_status,
        )

    def _hydrate(self, saga_class: type[Saga[Any]], state_data: dict[str, Any]) -> Saga[Any]:
        saga = object.__new__(saga_class)
        saga.state = saga_class.state_type.model_validate(state_data)
        saga._mediator = self._mediator
        saga._completed = False
        saga._failed = False
        return saga


def _saga_type_key(saga_class: type[Any]) -> str:
    return f"{saga_class.__module__}.{saga_class.__qualname__}"
