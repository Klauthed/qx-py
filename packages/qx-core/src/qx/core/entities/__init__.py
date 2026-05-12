"""Domain entity base types.

``Entity`` carries identity, auditing, optimistic concurrency, and soft-delete.
``AggregateRoot`` extends it with a domain-event buffer (events are released
by the repository / unit-of-work when the aggregate is persisted, never by
the entity itself).

``ValueObject`` is the equality-by-value counterpart: two value objects with the
same attribute payload are equal regardless of construction site.

These types are intentionally **not** SQLAlchemy mapped. Persistence mapping lives
in ``qx-db``; this package stays pure.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, ClassVar, Generic, TypeVar, overload
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict

from qx.core.domain.events import DomainEvent

__all__ = [
    "Identifier",
    "Entity",
    "AggregateRoot",
    "ValueObject",
    "entity",
    "aggregate",
]

# We allow either a UUID-backed identifier or a string-backed one (for external ids,
# slugs, vendor-issued tokens). The generic parameter lets aggregates pin their own.
TId = TypeVar("TId")


def _utcnow() -> datetime:
    """Single source of truth for "now" inside the framework.

    Tests should monkeypatch ``qx.core.entities._utcnow`` to control time;
    avoid scattering ``datetime.now`` calls across the codebase.
    """
    return datetime.now(UTC)


@dataclass(frozen=True)
class Identifier:
    """Wrapper for entity identifiers. Defaults to a UUID4-backed value.

    Subclass to introduce typed ids (``UserId``, ``OrderId``) — typed ids prevent
    a whole class of bugs where a user id gets passed to an order lookup. Cost is
    minimal because the framework's repository layer is generic in the id type.
    """

    value: UUID = field(default_factory=uuid4)

    @classmethod
    def new(cls) -> "Identifier":
        return cls(value=uuid4())

    @classmethod
    def parse(cls, raw: str | UUID) -> "Identifier":
        return cls(value=raw if isinstance(raw, UUID) else UUID(raw))

    def __str__(self) -> str:
        return str(self.value)


@dataclass(eq=False, kw_only=True)
class Entity(Generic[TId]):
    """Mutable, identity-bearing domain object.

    Equality is by ``id``, not by attribute value. This is the whole point of an
    entity vs. a value object — same id means same conceptual thing, even if
    attributes have drifted.

    Lifecycle fields (``created_at``, ``updated_at``, ``deleted_at`` and their
    actor counterparts) are written by the persistence layer, not by hand. The
    ``version`` field is the optimistic-concurrency token; repositories bump it
    on save and reject stale writes with ``ConflictError``.
    """

    id: TId

    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime | None = None
    deleted_at: datetime | None = None

    created_by: UUID | None = None
    updated_by: UUID | None = None
    deleted_by: UUID | None = None

    version: int = 1

    # Multi-tenancy: optional, populated when the framework is configured for it.
    # Kept on the base so every repository can filter without per-aggregate logic.
    tenant_id: UUID | None = None

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    def mark_deleted(self, *, by: UUID | None = None, at: datetime | None = None) -> None:
        """Soft-delete. Persistence layer translates to whatever underlying mechanism."""
        self.deleted_at = at or _utcnow()
        self.deleted_by = by

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Entity):
            return NotImplemented
        return type(self) is type(other) and self.id == other.id

    def __hash__(self) -> int:
        return hash((type(self), self.id))


@dataclass(eq=False, kw_only=True)
class AggregateRoot(Entity[TId]):
    """An entity that owns a consistency boundary and emits domain events.

    Aggregates are the only thing repositories should load and save directly. Use
    ``record_event`` from inside aggregate methods when domain state changes that
    other parts of the system need to react to. Events sit in the aggregate's
    buffer until the repository drains them (via ``pull_events``) at save time;
    the unit-of-work then dispatches them in the same transaction as the state
    change (in-process) or via the outbox (cross-process).

    Aggregates never publish events themselves and never call infrastructure.
    """

    _pending_events: list[DomainEvent] = field(default_factory=list, repr=False)

    def record_event(self, event: DomainEvent) -> None:
        self._pending_events.append(event)

    def pull_events(self) -> list[DomainEvent]:
        """Drain and return pending events. Called by the persistence layer."""
        events = list(self._pending_events)
        self._pending_events.clear()
        return events

    @property
    def has_pending_events(self) -> bool:
        return bool(self._pending_events)


class ValueObject(BaseModel):
    """Immutable, equality-by-value domain primitive.

    Examples: ``Email``, ``Money``, ``Address``, ``PhoneNumber``. Value objects
    should validate themselves in ``__init__`` (via Pydantic validators) and
    never have an identity.

    Implemented atop Pydantic for free validation and JSON serialization. Frozen
    by default — mutation produces a new instance via ``model_copy``.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(
        frozen=True,
        extra="forbid",
        strict=True,
    )

    def with_changes(self, **changes: Any) -> "ValueObject":
        return self.model_copy(update=changes)


# -------- Decorator helpers --------

_TCls = TypeVar("_TCls", bound=type)


@overload
def entity(cls: _TCls) -> _TCls: ...
@overload
def entity(*, slots: bool = False) -> Callable[[_TCls], _TCls]: ...
def entity(cls: _TCls | None = None, *, slots: bool = False) -> Any:
    """Mark a class as a Qx entity.

    Applies ``@dataclass(eq=False, kw_only=True)`` so the class:

    - Gets auto-generated ``__init__``, ``__repr__`` for ergonomic use,
    - Keeps the identity-based ``__eq__`` and ``__hash__`` inherited from ``Entity``
      (we disable dataclass's field-by-field eq, which would re-introduce
      attribute-based equality and silently break aggregate identity semantics).

    Always prefer this over decorating with bare ``@dataclass``.

    Usage::

        @entity
        class User(Entity[UUID]):
            email: str
            name: str
    """

    def wrap(klass: _TCls) -> _TCls:
        return dataclass(eq=False, kw_only=True, slots=slots)(klass)  # type: ignore[return-value]

    if cls is None:
        return wrap
    return wrap(cls)


@overload
def aggregate(cls: _TCls) -> _TCls: ...
@overload
def aggregate(*, slots: bool = False) -> Callable[[_TCls], _TCls]: ...
def aggregate(cls: _TCls | None = None, *, slots: bool = False) -> Any:
    """Mark a class as a Qx aggregate root. See ``@entity``.

    Functionally identical to ``@entity`` today, but kept as a separate name
    because aggregates and entities are conceptually different and we may
    diverge their decorators in future (e.g., aggregate-only enforcement of
    invariants, automatic event-buffer wiring).
    """
    return entity(cls, slots=slots) if cls is not None else entity(slots=slots)
