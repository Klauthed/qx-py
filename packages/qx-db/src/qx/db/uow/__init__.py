"""Unit of Work.

Drives the transactional boundary around a command. Responsibilities:

1. Open an ``AsyncSession`` from the factory.
2. Begin a transaction.
3. Track aggregates loaded/added so their pending domain events can be drained.
4. On commit: drain events, dispatch in-process handlers inside the transaction,
   write integration events to the outbox table (atomic with state change).
5. Commit. On any failure, roll back; pending events are discarded.

Usage::

    async with uow_factory() as uow:
        repo = UserRepository(uow.session)
        user = User.create(...)
        await repo.add(user)
        await uow.commit()
        # events have now been dispatched/outboxed; transaction committed.

The dispatcher abstraction (``EventDispatcher``) is a protocol so the
unit-of-work doesn't pull a hard dependency on the mediator. The wiring layer
(usually ``qx.bootstrap``) provides the concrete implementation.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable

from sqlalchemy.ext.asyncio import AsyncSession

from qx.core import AggregateRoot, DomainEvent, IntegrationEvent, current_context
from qx.db.engine import SessionFactory
from qx.db.outbox import OutboxRecorder

__all__ = ["UnitOfWork", "EventDispatcher"]


@runtime_checkable
class EventDispatcher(Protocol):
    """Protocol the UoW uses to dispatch in-process domain events.

    Concretely implemented over the mediator's ``publish`` in service bootstrap.
    Kept as a protocol here to keep ``qx-db`` independent of
    ``qx-cqrs`` at the type level (it's still in deps for testing
    convenience).
    """

    async def publish(self, event: DomainEvent) -> None: ...


class UnitOfWork:
    """Transactional unit-of-work scoped to one logical operation."""

    def __init__(
        self,
        session_factory: SessionFactory,
        dispatcher: EventDispatcher | None,
        outbox: OutboxRecorder | None = None,
    ) -> None:
        self._factory = session_factory
        self._dispatcher = dispatcher
        self._outbox = outbox
        self._session: AsyncSession | None = None
        self._tracked: list[AggregateRoot[Any]] = []
        self._committed = False
        self._rolled_back = False

    @property
    def session(self) -> AsyncSession:
        if self._session is None:
            raise RuntimeError(
                "UnitOfWork session accessed outside of `async with` block"
            )
        return self._session

    def track(self, aggregate: AggregateRoot[Any]) -> None:
        """Register an aggregate so its domain events get drained on commit.

        Repositories that follow ``qx-db`` conventions call this from
        ``add`` and ``save`` automatically. Aggregates loaded for read can also
        be tracked if their events need to be dispatched (rare).
        """
        if aggregate not in self._tracked:
            self._tracked.append(aggregate)

    async def commit(self) -> None:
        """Drain events, route to in-process + outbox, then commit the SQL transaction.

        All event work happens **inside** the open transaction. If the dispatcher
        fails (an in-process handler raised), or the outbox insert fails, the
        commit doesn't happen — the whole unit-of-work rolls back. This is the
        outbox pattern's atomic guarantee.
        """
        if self._session is None:
            raise RuntimeError("UnitOfWork.commit called outside async with block")
        if self._committed or self._rolled_back:
            return

        # Drain events from every tracked aggregate.
        domain_events: list[DomainEvent] = []
        integration_events: list[IntegrationEvent] = []
        for agg in self._tracked:
            for ev in agg.pull_events():
                # Stamp correlation/tenant from the active request context so
                # downstream consumers can correlate. Aggregates don't see the
                # request context directly — UoW is the right place to enrich.
                ctx = current_context()
                enriched = ev.model_copy(
                    update={
                        "correlation_id": ev.correlation_id or ctx.correlation_id,
                        "tenant_id": ev.tenant_id or ctx.tenant_id,
                        "actor_id": ev.actor_id or ctx.user_id,
                    }
                )
                if isinstance(enriched, IntegrationEvent):
                    integration_events.append(enriched)
                elif isinstance(enriched, DomainEvent):
                    domain_events.append(enriched)

        # Dispatch in-process domain events first (still inside txn).
        if self._dispatcher is not None:
            for ev in domain_events:
                await self._dispatcher.publish(ev)

        # Write integration events to outbox (still inside txn).
        if self._outbox is not None and integration_events:
            await self._outbox.record(self._session, integration_events)

        await self._session.commit()
        self._committed = True

    async def rollback(self) -> None:
        if self._session is None or self._rolled_back or self._committed:
            return
        await self._session.rollback()
        self._rolled_back = True

    async def __aenter__(self) -> "UnitOfWork":
        self._session = self._factory()
        await self._session.begin()
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        try:
            if exc is not None:
                await self.rollback()
            elif not self._committed and not self._rolled_back:
                # No explicit commit — implicit rollback. Forces callers to be
                # deliberate; silent commits on `async with` exit hide bugs.
                await self.rollback()
        finally:
            if self._session is not None:
                await self._session.close()
                self._session = None
