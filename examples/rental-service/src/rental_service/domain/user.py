"""User aggregate."""

from __future__ import annotations

from typing import ClassVar
from uuid import UUID  # noqa: TC003

from qx.core import AggregateRoot, DomainError, DomainEvent, Identifier, Result, aggregate


class UserRegistered(DomainEvent):
    event_name: ClassVar[str] = "rental.user.registered"

    user_id: UUID
    email: str
    name: str


@aggregate
class User(AggregateRoot[Identifier]):
    """A registered user."""

    email: str = ""
    name: str = ""

    @classmethod
    def register(cls, email: str, name: str) -> Result[User]:
        email = email.strip().lower()
        if not email or "@" not in email:
            return Result.failure(
                DomainError(code="user.invalid_email", message="email must be valid")
            )
        if not name.strip():
            return Result.failure(DomainError(code="user.invalid_name", message="name is required"))
        user = cls(id=Identifier.new(), email=email, name=name.strip())
        user.record_event(UserRegistered(user_id=user.id.value, email=email, name=user.name))
        return Result.success(user)
