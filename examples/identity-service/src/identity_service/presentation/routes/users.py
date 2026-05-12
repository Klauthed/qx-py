"""HTTP routes for User: POST /users, GET /users/{id}, GET /users."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, status

from qx.cqrs import Mediator
from qx.http import Inject, envelope_success, unwrap

from identity_service.application.commands.change_email import ChangeEmailCommand
from identity_service.application.commands.create_user import (
    CreateUserCommand,
    CreateUserDto,
)
from identity_service.application.queries.get_user import GetUserQuery, UserDto
from identity_service.application.queries.list_users import ListUsersQuery

router = APIRouter(prefix="/users", tags=["users"])


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_user(
    cmd: CreateUserCommand,
    mediator: Mediator = Inject(Mediator),
) -> dict:
    """Register a new user.

    Returns 201 with the new user envelope on success; 409 if the email is
    already in use; 422 on domain validation failures.
    """
    result = await mediator.send(cmd)
    return envelope_success(unwrap(result))


@router.get("/{user_id}")
async def get_user(
    user_id: UUID,
    mediator: Mediator = Inject(Mediator),
) -> dict:
    """Fetch a user by id. 404 if not found."""
    result = await mediator.send(GetUserQuery(user_id=user_id))
    return envelope_success(unwrap(result))


@router.get("")
async def list_users(
    page: int = 1,
    page_size: int = 20,
    mediator: Mediator = Inject(Mediator),
) -> dict:
    """List users with offset pagination."""
    result = await mediator.send(ListUsersQuery(page=page, page_size=page_size))
    page_obj = unwrap(result)
    return envelope_success(
        [u.model_dump(mode="json") for u in page_obj.items],
        page=page_obj.page,
        page_size=page_obj.page_size,
        total=page_obj.total,
    )


@router.patch("/{user_id}/email", status_code=status.HTTP_204_NO_CONTENT)
async def change_email(
    user_id: UUID,
    cmd: ChangeEmailCommand,
    mediator: Mediator = Inject(Mediator),
) -> None:
    """Change a user's email address."""
    # FastAPI binds the body to ``cmd``; we honor the path id over the body
    # id so it's unambiguous what the call targets.
    actual_cmd = cmd.model_copy(update={"user_id": user_id})
    unwrap(await mediator.send(actual_cmd))
