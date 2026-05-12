"""User aggregate."""

from __future__ import annotations

from typing import ClassVar
from uuid import UUID  # noqa: TC003

from qx.core import (
    AggregateRoot,
    DomainError,
    DomainEvent,
    Identifier,
    IntegrationEvent,
    Result,
    aggregate,
)


class UserRegistered(DomainEvent):
    event_name: ClassVar[str] = "identity.user.registered"

    user_id: UUID
    email: str
    name: str


class UserRegisteredIntegration(IntegrationEvent):
    event_name: ClassVar[str] = "identity.user.registered"
    event_version: ClassVar[int] = 1

    user_id: UUID
    email: str
    name: str


class UserEmailChanged(IntegrationEvent):
    event_name: ClassVar[str] = "identity.user.email_changed"
    event_version: ClassVar[int] = 1

    user_id: UUID
    old_email: str
    new_email: str


@aggregate
class User(AggregateRoot[Identifier]):
    """A user account."""

    email: str = ""
    name: str = ""
    is_active: bool = True

    @classmethod
    def register(cls, email: str, name: str) -> Result[User]:
        email = email.strip().lower()
        if not email or "@" not in email:
            return Result.failure(
                DomainError(code="user.invalid_email", message="email is required")
            )
        if not name.strip():
            return Result.failure(DomainError(code="user.invalid_name", message="name is required"))

        user = cls(
            id=Identifier.new(),
            email=email,
            name=name.strip(),
            is_active=True,
        )
        user.record_event(UserRegistered(user_id=user.id.value, email=email, name=user.name))
        user.record_event(
            UserRegisteredIntegration(user_id=user.id.value, email=email, name=user.name)
        )
        return Result.success(user)

    def change_email(self, new_email: str) -> Result[None]:
        new_email = new_email.strip().lower()
        if "@" not in new_email:
            return Result.failure(
                DomainError(code="user.invalid_email", message="email is required")
            )
        if new_email == self.email:
            return Result.success(None)
        old = self.email
        self.email = new_email
        self.record_event(
            UserEmailChanged(user_id=self.id.value, old_email=old, new_email=new_email)
        )
        return Result.success(None)

    def deactivate(self) -> None:
        self.is_active = False
