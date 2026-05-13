"""SagaStore — load/save saga instance rows."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from qx.core import ConflictError, Result
from sqlalchemy import select, update

if TYPE_CHECKING:
    from sqlalchemy import Table
    from sqlalchemy.ext.asyncio import AsyncSession

__all__ = ["SagaStore"]

_RUNNING = "running"
_COMPLETED = "completed"
_FAILED = "failed"
_COMPENSATED = "compensated"


class SagaInstance:
    """In-memory representation of a persisted saga row."""

    __slots__ = (
        "correlation_key",
        "created_at",
        "id",
        "saga_type",
        "state_data",
        "status",
        "updated_at",
        "version",
    )

    def __init__(
        self,
        *,
        id: UUID,
        saga_type: str,
        correlation_key: str,
        status: str,
        state_data: dict[str, Any],
        version: int,
        created_at: datetime,
        updated_at: datetime,
    ) -> None:
        self.id = id
        self.saga_type = saga_type
        self.correlation_key = correlation_key
        self.status = status
        self.state_data = state_data
        self.version = version
        self.created_at = created_at
        self.updated_at = updated_at

    @property
    def is_terminal(self) -> bool:
        return self.status in (_COMPLETED, _FAILED, _COMPENSATED)


class SagaStore:
    """Postgres-backed store for saga instance rows.

    Uses optimistic concurrency: each save increments ``version`` and
    checks the previous value. A mismatch means another process updated
    the row first — the manager should reload and retry.
    """

    def __init__(self, session: AsyncSession, table: Table) -> None:
        self._session = session
        self._table = table

    async def load(
        self,
        saga_type: str,
        correlation_key: str,
    ) -> Result[SagaInstance | None]:
        """Load an existing instance, or return ``None`` if it doesn't exist yet."""
        row = await self._session.execute(
            select(self._table).where(
                self._table.c.saga_type == saga_type,
                self._table.c.correlation_key == correlation_key,
            )
        )
        r = row.mappings().first()
        if r is None:
            return Result.success(None)
        return Result.success(self._from_row(dict(r)))

    async def create(
        self,
        saga_type: str,
        correlation_key: str,
        state_data: dict[str, Any],
    ) -> Result[SagaInstance]:
        """Insert a new running instance. Fails if one already exists."""
        instance = SagaInstance(
            id=uuid4(),
            saga_type=saga_type,
            correlation_key=correlation_key,
            status=_RUNNING,
            state_data=state_data,
            version=0,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        try:
            await self._session.execute(
                self._table.insert().values(
                    id=instance.id,
                    saga_type=instance.saga_type,
                    correlation_key=instance.correlation_key,
                    status=instance.status,
                    state=instance.state_data,
                    version=str(instance.version),
                    created_at=instance.created_at,
                    updated_at=instance.updated_at,
                )
            )
        except Exception as exc:
            if "unique" in str(exc).lower():
                return Result.failure(
                    ConflictError(
                        code="saga.already_exists",
                        message=f"saga {saga_type}:{correlation_key} already exists",
                    )
                )
            raise
        return Result.success(instance)

    async def save(
        self,
        instance: SagaInstance,
        *,
        new_state: dict[str, Any],
        new_status: str,
    ) -> Result[None]:
        """Update state and status with optimistic concurrency check."""
        new_version = instance.version + 1
        cursor = await self._session.execute(
            update(self._table)
            .where(
                self._table.c.id == instance.id,
                self._table.c.version == str(instance.version),
            )
            .values(
                state=new_state,
                status=new_status,
                version=str(new_version),
                updated_at=datetime.now(UTC),
            )
        )
        if cursor.rowcount == 0:  # type: ignore[attr-defined]
            return Result.failure(
                ConflictError(
                    code="saga.version_conflict",
                    message=f"saga {instance.id} was modified concurrently",
                )
            )
        instance.state_data = new_state
        instance.status = new_status
        instance.version = new_version
        return Result.success(None)

    async def load_timed_out(
        self,
        saga_type: str,
        threshold: datetime,
    ) -> list[SagaInstance]:
        """Return running instances whose ``updated_at`` is before ``threshold``."""
        rows = await self._session.execute(
            select(self._table).where(
                self._table.c.saga_type == saga_type,
                self._table.c.status == _RUNNING,
                self._table.c.updated_at < threshold,
            )
        )
        return [self._from_row(dict(r)) for r in rows.mappings()]

    @staticmethod
    def _from_row(row: dict[str, Any]) -> SagaInstance:
        return SagaInstance(
            id=row["id"],
            saga_type=row["saga_type"],
            correlation_key=row["correlation_key"],
            status=row["status"],
            state_data=row["state"] if isinstance(row["state"], dict) else {},
            version=int(row["version"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
