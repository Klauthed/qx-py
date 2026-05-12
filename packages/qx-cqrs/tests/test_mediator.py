"""Mediator tests: dispatch, three registration modes, pipeline behaviors."""

from __future__ import annotations

from typing import ClassVar
from uuid import uuid4

import pytest

from qx.core import DomainError, DomainEvent, NotFoundError, Result
from qx.cqrs import (
    Behavior,
    Command,
    CommandHandler,
    ExceptionTranslationBehavior,
    Mediator,
    MediatorError,
    Query,
    QueryHandler,
    command_handler,
    event_handler,
    query_handler,
)
from qx.di import Container


# ---- Test domain ----


class CreateUserCommand(Command[str]):
    email: str


class GetUserQuery(Query[str]):
    user_id: str


class UserCreated(DomainEvent):
    event_name: ClassVar[str] = "user.created"
    user_id: str


# ---- Decorator-mode handlers ----


@command_handler(CreateUserCommand)
class _DecoratedCreateUser:
    async def handle(self, cmd: CreateUserCommand) -> Result[str]:
        if "@" not in cmd.email:
            return Result.failure(DomainError(code="bad_email", message="bad"))
        return Result.success(f"user:{cmd.email}")


@query_handler(GetUserQuery)
class _DecoratedGetUser:
    async def handle(self, q: GetUserQuery) -> Result[str]:
        if q.user_id == "missing":
            return Result.failure(NotFoundError(code="user.missing", message="x"))
        return Result.success(f"id={q.user_id}")


@event_handler(UserCreated)
class _DecoratedUserCreatedHandler:
    received: list[str] = []  # noqa: RUF012

    async def handle(self, event: UserCreated) -> None:
        self.received.append(event.user_id)


# ---- Explicit-mode handlers ----


class _ExplicitCreateUser:
    async def handle(self, cmd: CreateUserCommand) -> Result[str]:
        return Result.success(f"explicit:{cmd.email}")


# ---- Type-based-mode handler ----


class _TypedCreateUser(CommandHandler[CreateUserCommand, str]):
    async def handle(self, cmd: CreateUserCommand) -> Result[str]:
        return Result.success(f"typed:{cmd.email}")


# ---- Fixtures ----


@pytest.fixture
def container() -> Container:
    return Container()


@pytest.fixture
def mediator(container: Container) -> Mediator:
    return Mediator(container)


# ---- Decorator + scan registration ----


class TestDecoratorRegistration:
    async def test_register_decorated_module(self, mediator: Mediator) -> None:
        # Pass classes directly (avoiding module-walk subtleties for this unit test)
        n = mediator.register_decorated(_DecoratedCreateUser, _DecoratedGetUser)
        assert n == 2
        assert CreateUserCommand in mediator.registered_commands
        assert GetUserQuery in mediator.registered_queries

    async def test_decorated_command_dispatches(self, mediator: Mediator) -> None:
        mediator.register_decorated(_DecoratedCreateUser)
        result = await mediator.send(CreateUserCommand(email="a@b.com"))
        assert result.is_success
        assert result.value == "user:a@b.com"

    async def test_decorated_command_returns_failure(self, mediator: Mediator) -> None:
        mediator.register_decorated(_DecoratedCreateUser)
        result = await mediator.send(CreateUserCommand(email="invalid"))
        assert result.is_failure
        assert result.error.code == "bad_email"

    async def test_decorated_query_dispatches(self, mediator: Mediator) -> None:
        mediator.register_decorated(_DecoratedGetUser)
        result = await mediator.send(GetUserQuery(user_id="42"))
        assert result.is_success
        assert result.value == "id=42"


# ---- Explicit registration ----


class TestExplicitRegistration:
    async def test_explicit_command_dispatches(self, mediator: Mediator) -> None:
        mediator.register_command(CreateUserCommand, _ExplicitCreateUser)
        result = await mediator.send(CreateUserCommand(email="x@y"))
        assert result.is_success
        assert result.value == "explicit:x@y"

    async def test_duplicate_command_registration_raises(
        self, mediator: Mediator
    ) -> None:
        mediator.register_command(CreateUserCommand, _ExplicitCreateUser)
        with pytest.raises(MediatorError, match="already has handler"):
            mediator.register_command(CreateUserCommand, _ExplicitCreateUser)


# ---- Type-based auto registration ----


class TestTypeBasedRegistration:
    async def test_typed_command_dispatches(self, mediator: Mediator) -> None:
        mediator.register_typed(_TypedCreateUser)
        result = await mediator.send(CreateUserCommand(email="t@y"))
        assert result.is_success
        assert result.value == "typed:t@y"

    async def test_typed_without_protocol_generic_raises(
        self, mediator: Mediator
    ) -> None:
        class Unbound:
            async def handle(self, cmd: CreateUserCommand) -> Result[str]:
                return Result.success("x")

        with pytest.raises(MediatorError, match="does not declare"):
            mediator.register_typed(Unbound)


# ---- Events ----


class TestEventDispatch:
    async def test_event_fans_out(self, mediator: Mediator) -> None:
        _DecoratedUserCreatedHandler.received.clear()
        mediator.register_decorated(_DecoratedUserCreatedHandler)
        await mediator.publish(UserCreated(user_id="abc"))
        assert _DecoratedUserCreatedHandler.received == ["abc"]

    async def test_unknown_event_no_handlers_silent(self, mediator: Mediator) -> None:
        # Publishing with no handlers registered is a no-op.
        await mediator.publish(UserCreated(user_id="x"))

    async def test_event_handler_failure_aggregates(self, mediator: Mediator) -> None:
        @event_handler(UserCreated)
        class FailingHandler:
            async def handle(self, _event: UserCreated) -> None:
                raise RuntimeError("boom")

        mediator.register_decorated(FailingHandler)
        with pytest.raises(BaseExceptionGroup):
            await mediator.publish(UserCreated(user_id="x"))


# ---- Pipeline behaviors ----


class TestPipelineBehaviors:
    async def test_behavior_wraps_dispatch(self, container: Container) -> None:
        order: list[str] = []

        class TraceBehavior:
            async def handle(self, msg, next_):
                order.append("before")
                r = await next_(msg)
                order.append("after")
                return r

        m = Mediator(container, command_behaviors=(TraceBehavior(),))
        m.register_decorated(_DecoratedCreateUser)
        result = await m.send(CreateUserCommand(email="a@b"))
        assert result.is_success
        assert order == ["before", "after"]

    async def test_exception_translation_behavior(self, container: Container) -> None:
        @command_handler(CreateUserCommand)
        class Boom:
            async def handle(self, _cmd: CreateUserCommand) -> Result[str]:
                raise ValueError("ka-boom")

        m = Mediator(
            container,
            command_behaviors=(ExceptionTranslationBehavior(),),
        )
        m.register_decorated(Boom)
        result = await m.send(CreateUserCommand(email="a@b"))
        assert result.is_failure
        assert "ka-boom" in result.error.message


class TestUnknownDispatch:
    async def test_send_with_no_handler_raises(self, mediator: Mediator) -> None:
        with pytest.raises(MediatorError, match="No command handler"):
            await mediator.send(CreateUserCommand(email="a@b"))
