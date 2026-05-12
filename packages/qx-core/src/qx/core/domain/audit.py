"""Audit log primitive.

Distinct from the per-entity audit *fields* (created_by, updated_by, ...) on
``Entity``. Those describe the *current* state; this is the *history* of state
changes, written as an append-only stream. The two are complementary:

- Entity audit fields are cheap to query ("who last touched this user?")
- Audit log entries are expensive to query but exhaustive ("show me every
  permission change for this user in 2024")

Audit entries should be written by infrastructure (repository decorators,
mediator behaviors), never by domain code. The domain doesn't know it's being
audited — that's the point.
"""

from __future__ import annotations

from typing import Any, ClassVar
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from qx.core.utils.time import utcnow

__all__ = ["AuditAction", "AuditEntry"]

AuditAction = str  # Keep as free-form string; conventions: "user.created", "policy.updated"


class AuditEntry(BaseModel):
    """A single audit log row.

    ``before`` and ``after`` are arbitrary JSON-serializable snapshots. For large
    aggregates, prefer storing a diff rather than full snapshots — the storage
    layer can compute that. ``correlation_id`` ties multiple entries together
    when one user action triggers cascading changes.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: UUID = Field(default_factory=uuid4)
    action: AuditAction
    entity_type: str
    entity_id: UUID

    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None

    actor_id: UUID | None = None
    actor_kind: str | None = None
    tenant_id: UUID | None = None

    correlation_id: UUID | None = None
    trace_id: str | None = None
    ip_address: str | None = None
    user_agent: str | None = None

    occurred_at: Any = Field(default_factory=utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)
