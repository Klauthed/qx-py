"""User HTTP routes."""

from __future__ import annotations

from uuid import UUID  # noqa: TC003

from fastapi import APIRouter
from pydantic import BaseModel
from qx.cqrs import Mediator
from qx.http import Inject, unwrap

from rental_service.application.user.commands.register_user import RegisterUserCommand, RegisterUserDto
from rental_service.application.user.queries.get_user_profile import GetUserProfileQuery, UserProfileDto

router = APIRouter(prefix="/users", tags=["users"])


class RegisterUserRequest(BaseModel):
    email: str
    name: str


@router.post("", response_model=RegisterUserDto, status_code=201)
async def register_user(
    body: RegisterUserRequest,
    mediator: Mediator = Inject(Mediator),  # noqa: B008
) -> RegisterUserDto:
    result = await mediator.send(RegisterUserCommand(email=body.email, name=body.name))
    return unwrap(result)  # type: ignore[no-any-return]


@router.get("/{user_id}", response_model=UserProfileDto)
async def get_user_profile(
    user_id: UUID,
    mediator: Mediator = Inject(Mediator),  # noqa: B008
) -> UserProfileDto:
    result = await mediator.send(GetUserProfileQuery(user_id=user_id))
    return unwrap(result)  # type: ignore[no-any-return]
