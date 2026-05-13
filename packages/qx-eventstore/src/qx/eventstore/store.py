"""EventStore — append and replay aggregate event streams."""

from __future__ import annotations

import dataclasses
import importlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from qx.core import ConflictError, Result
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

# Optional Prometheus metrics — no hard dep on prometheus_client.
try:
    from prometheus_client import Counter

    _version_conflicts_total = Counter(
        "qx_eventstore_version_conflicts_total",
        "Optimistic concurrency conflicts detected during event append.",
        ["aggregate_type"],
    )
    _HAS_PROMETHEUS = True
except Exception:  # pragma: no cover
    _HAS_PROMETHEUS = False

if TYPE_CHECKING:
    from qx.core.domain.events import DomainEvent
    from qx.eventstore.aggregate import EventSourcedAggregate
    from sqlalchemy import Table
    from sqlalchemy.ext.asyncio import AsyncSession

__all__ = ["EventStore"]


class EventStore:
    """Postgres-backed event log for event-sourced aggregates.

    Append events in a single call; load them back for replay. Optionally
    reads/writes snapshots to avoid full-stream replay on large aggregates.

    Usage::

        store = EventStore(session, events_table, snapshots_table)

        # append events after handling a command
        await store.append(account, aggregate_type="accounts.Account")

        # load and replay
        account = await store.load(
            Account,
            aggregate_id=str(account_id),
            aggregate_type="accounts.Account",
        )
    """

    def __init__(
        self,
        session: AsyncSession,
        events_table: Table,
        snapshots_table: Table,
        *,
        snapshot_every: int = 50,
    ) -> None:
        self._session = session
        self._events = events_table
        self._snapshots = snapshots_table
        self._snapshot_every = snapshot_every

    async def append(
        self,
        aggregate: EventSourcedAggregate[Any],
        *,
        aggregate_type: str,
        tenant_id: str | None = None,
    ) -> Result[int]:
        """Drain pending events from ``aggregate`` and append them to the log.

        Returns the new version (last sequence number written).
        Raises on optimistic concurrency conflict (duplicate sequence).
        """
        events = aggregate.pull_events()
        if not events:
            return Result.success(aggregate.version)

        base_seq = aggregate.version
        now = datetime.now(UTC)
        rows = []
        for i, event in enumerate(events, start=1):
            rows.append(
                {
                    "id": uuid4(),
                    "aggregate_type": aggregate_type,
                    "aggregate_id": str(aggregate.id),
                    "sequence": base_seq + i,
                    "event_type": f"{type(event).__module__}.{type(event).__qualname__}",
                    "event_name": event.event_name,
                    "payload": event.model_dump(mode="json"),
                    "occurred_at": now,
                    "tenant_id": tenant_id,
                }
            )

        try:
            await self._session.execute(self._events.insert(), rows)
        except Exception as exc:
            if "unique" in str(exc).lower():
                if _HAS_PROMETHEUS:
                    _version_conflicts_total.labels(aggregate_type=aggregate_type).inc()
                return Result.failure(
                    ConflictError(
                        code="eventstore.version_conflict",
                        message=f"{aggregate_type}:{aggregate.id} was modified concurrently",
                    )
                )
            raise

        new_version = base_seq + len(events)
        aggregate.version = new_version

        if new_version % self._snapshot_every == 0:
            await self._save_snapshot(aggregate, aggregate_type=aggregate_type, tenant_id=tenant_id)

        return Result.success(new_version)

    async def load(
        self,
        cls: type[EventSourcedAggregate[Any]],
        *,
        aggregate_id: str,
        aggregate_type: str,
    ) -> Result[EventSourcedAggregate[Any] | None]:
        """Load an aggregate by replaying its event stream.

        If a snapshot exists, replay only events after the snapshot version.
        Returns ``None`` if no events (and no snapshot) exist for this id.
        """
        snapshot_state: dict[str, Any] | None = None
        snapshot_version = 0

        snap_row = await self._session.execute(
            select(self._snapshots).where(
                self._snapshots.c.aggregate_type == aggregate_type,
                self._snapshots.c.aggregate_id == aggregate_id,
            )
        )
        snap = snap_row.mappings().first()
        if snap is not None:
            snapshot_state = dict(snap["state"]) if isinstance(snap["state"], dict) else {}
            snapshot_version = int(snap["version"])

        rows = await self._session.execute(
            select(self._events)
            .where(
                self._events.c.aggregate_type == aggregate_type,
                self._events.c.aggregate_id == aggregate_id,
                self._events.c.sequence > snapshot_version,
            )
            .order_by(self._events.c.sequence)
        )
        event_rows = list(rows.mappings())

        if snapshot_state is None and not event_rows:
            return Result.success(None)

        events = [self._deserialize_event(dict(r)) for r in event_rows]
        final_version = int(event_rows[-1]["sequence"]) if event_rows else snapshot_version

        if snapshot_state is not None:
            aggregate = _restore_from_snapshot(cls, aggregate_id, snapshot_state, snapshot_version)
            aggregate._replay(events, version=final_version)
        else:
            aggregate = cls._from_events(aggregate_id, events, version=final_version)

        return Result.success(aggregate)

    async def _save_snapshot(
        self,
        aggregate: EventSourcedAggregate[Any],
        *,
        aggregate_type: str,
        tenant_id: str | None,
    ) -> None:
        """Upsert a snapshot row for the current aggregate state."""
        state = _serialize_aggregate(aggregate)
        now = datetime.now(UTC)
        stmt = (
            pg_insert(self._snapshots)
            .values(
                id=uuid4(),
                aggregate_type=aggregate_type,
                aggregate_id=str(aggregate.id),
                state=state,
                version=aggregate.version,
                taken_at=now,
                tenant_id=tenant_id,
            )
            .on_conflict_do_update(
                index_elements=["aggregate_type", "aggregate_id"],
                set_={"state": state, "version": aggregate.version, "taken_at": now},
            )
        )
        await self._session.execute(stmt)

    @staticmethod
    def _deserialize_event(row: dict[str, Any]) -> DomainEvent:
        """Reconstruct a DomainEvent from a stored event row."""
        event_type_path: str = row["event_type"]
        module_path, _, class_name = event_type_path.rpartition(".")
        module = importlib.import_module(module_path)
        event_cls = getattr(module, class_name)
        return event_cls.model_validate(row["payload"])  # type: ignore[no-any-return]


def _serialize_aggregate(aggregate: EventSourcedAggregate[Any]) -> dict[str, Any]:
    """Best-effort dict serialization for snapshot state."""
    if dataclasses.is_dataclass(aggregate):
        return {k: v for k, v in dataclasses.asdict(aggregate).items() if not k.startswith("_")}
    return {}


def _restore_from_snapshot(
    cls: type[EventSourcedAggregate[Any]],
    aggregate_id: str,
    state: dict[str, Any],
    version: int,
) -> EventSourcedAggregate[Any]:
    """Build a partial aggregate from snapshot state (before replaying tail events)."""
    instance: EventSourcedAggregate[Any] = object.__new__(cls)
    for f in dataclasses.fields(instance):
        if f.name.startswith("_"):
            continue
        if f.name == "id":
            object.__setattr__(instance, "id", aggregate_id)
        elif f.name == "version":
            object.__setattr__(instance, "version", version)
        elif f.name in state:
            object.__setattr__(instance, f.name, state[f.name])
        elif f.default is not dataclasses.MISSING:
            object.__setattr__(instance, f.name, f.default)
        elif f.default_factory is not dataclasses.MISSING:
            object.__setattr__(instance, f.name, f.default_factory())
    object.__setattr__(instance, "_pending_events", [])
    return instance
