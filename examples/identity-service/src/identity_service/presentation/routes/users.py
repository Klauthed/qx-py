"""HTTP routes for User: POST /users, GET /users/{id}, GET /users."""

from typing import Any
from uuid import UUID  # noqa: TC003

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from qx.cqrs import Mediator
from qx.di import Scope  # noqa: TC002
from qx.http import Inject, envelope_success, scope_dep, unwrap

from identity_service.application.commands.change_email import ChangeEmailCommand
from identity_service.application.commands.create_user import CreateUserCommand  # noqa: TC001
from identity_service.application.queries.get_user import GetUserQuery
from identity_service.application.queries.get_user_profile import GetUserProfileQuery
from identity_service.application.queries.list_users import ListUsersQuery


class _ChangeEmailBody(BaseModel):
    new_email: str


router = APIRouter(prefix="/users", tags=["users"])


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_user(
    cmd: CreateUserCommand,
    mediator: Mediator = Inject(Mediator),  # noqa: B008
    scope: Scope = Depends(scope_dep),  # noqa: B008
) -> dict[str, Any]:
    """Register a new user.

    Returns 201 with the new user envelope on success; 409 if the email is
    already in use; 422 on domain validation failures.
    """
    result = await mediator.send(cmd, scope=scope)
    return envelope_success(unwrap(result))


@router.get("/{user_id}")
async def get_user(
    user_id: UUID,
    mediator: Mediator = Inject(Mediator),  # noqa: B008
    scope: Scope = Depends(scope_dep),  # noqa: B008
) -> dict[str, Any]:
    """Fetch a user by id. 404 if not found."""
    result = await mediator.send(GetUserQuery(user_id=user_id), scope=scope)
    return envelope_success(unwrap(result))


@router.get("")
async def list_users(
    page: int = 1,
    page_size: int = 20,
    mediator: Mediator = Inject(Mediator),  # noqa: B008
    scope: Scope = Depends(scope_dep),  # noqa: B008
) -> dict[str, Any]:
    """List users with offset pagination."""
    result = await mediator.send(ListUsersQuery(page=page, page_size=page_size), scope=scope)
    page_obj = unwrap(result)
    return envelope_success(
        [u.model_dump(mode="json") for u in page_obj.items],
        page=page_obj.page,
        page_size=page_obj.page_size,
        total=page_obj.total,
    )


@router.get("/{user_id}/profile")
async def get_user_profile(
    user_id: UUID,
    mediator: Mediator = Inject(Mediator),  # noqa: B008
    scope: Scope = Depends(scope_dep),  # noqa: B008
) -> dict[str, Any]:
    """Fetch a user's profile.

    Returns ``is_active`` only when the ``identity.enhanced-profile`` feature
    flag is enabled, demonstrating flag-gated response shaping.
    """
    result = await mediator.send(GetUserProfileQuery(user_id=user_id), scope=scope)
    return envelope_success(unwrap(result).model_dump(mode="json", exclude_none=True))


@router.patch("/{user_id}/email", status_code=status.HTTP_204_NO_CONTENT)
async def change_email(
    user_id: UUID,
    body: _ChangeEmailBody,
    mediator: Mediator = Inject(Mediator),  # noqa: B008
    scope: Scope = Depends(scope_dep),  # noqa: B008
) -> None:
    """Change a user's email address."""
    cmd = ChangeEmailCommand(user_id=user_id, new_email=body.new_email)
    unwrap(await mediator.send(cmd, scope=scope))
