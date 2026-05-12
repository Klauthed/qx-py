"""Liveness probe for the worker runtime.

Expose ``WorkerHealth.is_healthy()`` to a Kubernetes liveness endpoint::

    @app.get("/healthz/live")
    async def liveness(health: WorkerHealth = Depends(...)):
        if not health.is_healthy():
            raise HTTPException(503)
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime

__all__ = ["WorkerHealth"]

_DEFAULT_STALL_SECONDS = 30.0


@dataclass
class WorkerHealth:
    """Thread-safe liveness state updated by ``WorkerRuntime``."""

    stall_threshold_seconds: float = _DEFAULT_STALL_SECONDS

    _running: bool = field(default=False, init=False, repr=False)
    _last_activity_at: datetime | None = field(default=None, init=False, repr=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)

    def is_healthy(self) -> bool:
        """Return ``True`` when the worker is running and not stalled.

        A worker is considered stalled when it has not successfully fetched a
        batch (or received a fetch-timeout, which resets the clock) for longer
        than ``stall_threshold_seconds``.  This catches deadlocks and
        connection drops that don't surface as exceptions.
        """
        if not self._running:
            return False
        if self._last_activity_at is None:
            return True  # still starting up — not yet stalled
        age = (datetime.now(UTC) - self._last_activity_at).total_seconds()
        return age < self.stall_threshold_seconds

    # --- internal API used by WorkerRuntime ---

    def mark_started(self) -> None:
        self._running = True
        self._touch()

    def mark_stopped(self) -> None:
        self._running = False

    def mark_activity(self) -> None:
        self._touch()

    def _touch(self) -> None:
        self._last_activity_at = datetime.now(UTC)
