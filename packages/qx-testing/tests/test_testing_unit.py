"""Tests for the testing helpers themselves (doubles must behave like the real thing)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar
from uuid import uuid4

import pytest

from qx.core import Entity, Identifier, Result, aggregate
from qx.cqrs import Command, Query, command_handler, query_handler
from qx.testing import MediatorStub, RepositoryStub


@aggregate
class _User(Entity[Identifier]):
    email: str = ""
    name: str = ""


# ---- Repository stub ----


async def test_stub_repo_add_and_get() -> None:
    repo = RepositoryStub[_User]()
    u = _User(id=Identifier(uuid4()), email="a@b", name="A")
    add_result = await repo.add(u)
    assert add_result.is_success
    get_result = await repo.get(u.id)
    assert get_result.is_success
    assert get_result.value.email == "a@b"


async def test_stub_repo_get_missing() -> None:
    repo = RepositoryStub[_User]()
    result = await repo.get(Identifier(uuid4()))
    assert result.is_failure
    assert result.error.http_status == 404


async def test_stub_repo_optimistic_concurrency() -> None:
    repo = RepositoryStub[_User]()
    u = _User(id=Identifier(uuid4()), version=1, email="a@b", name="A")
    await repo.preload(u) if False else await repo.add(u)
    # Construct a stale copy.
    stale = _User(id=u.id, version=0, email="x@y", name="X")
    r = await repo.save(stale)
    assert r.is_failure
    assert r.error.http_status == 409


async def test_stub_repo_soft_delete_hides() -> None:
    repo = RepositoryStub[_User]()
    u = _User(id=Identifier(uuid4()), email="a@b", name="A")
    await repo.add(u)
    await repo.soft_delete(u.id)
    r = await repo.get(u.id)
    assert r.is_failure


# ---- Mediator stub ----


class GreetCommand(Command[str]):
    name: str


class _GreetHandler:
    async def handle(self, cmd: GreetCommand) -> Result[str]:
        return Result.success(f"hi {cmd.name}")


async def test_mediator_stub_routes_command() -> None:
    m = MediatorStub()
    m.register_command(GreetCommand, _GreetHandler())
    r = await m.send(GreetCommand(name="ada"))
    assert r.is_success
    assert r.value == "hi ada"


async def test_mediator_stub_records_published_events() -> None:
    from qx.core import DomainEvent

    class _E(DomainEvent):
        event_name: ClassVar[str] = "x.e"

    m = MediatorStub()
    await m.publish(_E())
    assert len(m.published_domain) == 1
