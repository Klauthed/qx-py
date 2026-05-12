"""Tests for the DI container.

These pin behaviors the rest of the framework relies on: lifetime semantics,
async resolution, override-for-test, cycle detection, async disposal.
"""

from __future__ import annotations

import pytest

from qx.di import (
    Container,
    Lifetime,
    RegistrationError,
    ResolutionError,
    scoped,
    singleton,
    transient,
)


# ---- Test fixtures: simple type graph ----


class Clock:
    def now(self) -> str:
        return "now"


class Db:
    def __init__(self) -> None:
        self.queries: list[str] = []


class UserRepo:
    def __init__(self, db: Db, clock: Clock) -> None:
        self.db = db
        self.clock = clock


class CreateUser:
    def __init__(self, repo: UserRepo) -> None:
        self.repo = repo


class AsyncResource:
    def __init__(self) -> None:
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


class SyncResource:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


# ---- Basic resolution ----


class TestBasicResolution:
    async def test_singleton_returns_same_instance(self) -> None:
        c = Container()
        c.register_singleton(Clock, Clock)
        a = await c.resolve(Clock)
        b = await c.resolve(Clock)
        assert a is b

    async def test_transient_returns_new_instance(self) -> None:
        c = Container()
        c.register_transient(Clock, Clock)
        a = await c.resolve(Clock)
        b = await c.resolve(Clock)
        assert a is not b

    async def test_scoped_returns_same_within_scope(self) -> None:
        c = Container()
        c.register_scoped(Db, Db)
        async with c.scope() as s:
            a = await c.resolve(Db, scope=s)
            b = await c.resolve(Db, scope=s)
            assert a is b

    async def test_scoped_isolates_across_scopes(self) -> None:
        c = Container()
        c.register_scoped(Db, Db)
        async with c.scope() as s1:
            a = await c.resolve(Db, scope=s1)
        async with c.scope() as s2:
            b = await c.resolve(Db, scope=s2)
        assert a is not b

    async def test_scoped_requires_active_scope(self) -> None:
        c = Container()
        c.register_scoped(Db, Db)
        with pytest.raises(ResolutionError, match="scoped"):
            await c.resolve(Db)

    async def test_dependency_chain_resolves(self) -> None:
        c = Container()
        c.register_singleton(Clock, Clock)
        c.register_singleton(Db, Db)
        c.register_transient(UserRepo, UserRepo)
        c.register_transient(CreateUser, CreateUser)
        cmd = await c.resolve(CreateUser)
        assert isinstance(cmd.repo, UserRepo)
        assert isinstance(cmd.repo.db, Db)
        assert isinstance(cmd.repo.clock, Clock)

    async def test_implicit_resolution_of_unregistered_concrete_class(self) -> None:
        c = Container()
        c.register_singleton(Clock, Clock)
        c.register_singleton(Db, Db)
        # UserRepo isn't registered but its deps are; auto-resolve as transient.
        repo = await c.resolve(UserRepo)
        assert isinstance(repo, UserRepo)

    async def test_register_instance(self) -> None:
        c = Container()
        clock = Clock()
        c.register_instance(Clock, clock)
        assert (await c.resolve(Clock)) is clock


# ---- Async factories ----


class TestAsyncFactories:
    async def test_async_factory_resolves(self) -> None:
        async def factory() -> Clock:
            return Clock()

        c = Container()
        c.register(Clock, factory, lifetime=Lifetime.SINGLETON)
        assert isinstance(await c.resolve(Clock), Clock)

    async def test_resolve_sync_rejects_async(self) -> None:
        async def factory() -> Clock:
            return Clock()

        c = Container()
        c.register(Clock, factory, lifetime=Lifetime.SINGLETON)
        with pytest.raises(ResolutionError, match="async"):
            c.resolve_sync(Clock)


# ---- Overrides ----


class TestOverrides:
    async def test_override_within_block(self) -> None:
        c = Container()
        c.register_singleton(Clock, Clock)
        original = await c.resolve(Clock)

        class FakeClock(Clock):
            def now(self) -> str:
                return "fake"

        with c.override(Clock, FakeClock()):
            assert (await c.resolve(Clock)).now() == "fake"

        # Restored
        restored = await c.resolve(Clock)
        assert restored is original

    async def test_override_with_factory(self) -> None:
        c = Container()
        c.register_singleton(Clock, Clock)
        with c.override(Clock, Clock, lifetime=Lifetime.TRANSIENT):
            a = await c.resolve(Clock)
            b = await c.resolve(Clock)
            assert a is not b  # overridden as transient


# ---- Cycle detection ----


class _CycleA:
    def __init__(self, b: "_CycleB") -> None: ...


class _CycleB:
    def __init__(self, a: _CycleA) -> None: ...


class TestCycleDetection:
    async def test_direct_cycle_raises(self) -> None:
        c = Container()
        c.register_transient(_CycleA, _CycleA)
        c.register_transient(_CycleB, _CycleB)
        with pytest.raises(ResolutionError, match="[Cc]yclic"):
            await c.resolve(_CycleA)


# ---- Disposal ----


class TestDisposal:
    async def test_async_resource_disposed_on_scope_exit(self) -> None:
        c = Container()
        c.register_scoped(AsyncResource, AsyncResource)
        async with c.scope() as s:
            res = await c.resolve(AsyncResource, scope=s)
            assert not res.closed
        assert res.closed

    async def test_sync_resource_disposed_on_scope_exit(self) -> None:
        c = Container()
        c.register_scoped(SyncResource, SyncResource)
        async with c.scope() as s:
            res = await c.resolve(SyncResource, scope=s)
        assert res.closed

    async def test_disposal_runs_in_reverse_order(self) -> None:
        order: list[str] = []

        class A:
            async def aclose(self) -> None:
                order.append("A")

        class B:
            async def aclose(self) -> None:
                order.append("B")

        c = Container()
        c.register_scoped(A, A)
        c.register_scoped(B, B)
        async with c.scope() as s:
            await c.resolve(A, scope=s)
            await c.resolve(B, scope=s)
        assert order == ["B", "A"]

    async def test_container_aclose_disposes_singletons(self) -> None:
        c = Container()
        c.register_singleton(AsyncResource, AsyncResource)
        res = await c.resolve(AsyncResource)
        await c.aclose()
        assert res.closed


# ---- Decorators + scan ----


class TestDecorators:
    async def test_singleton_decorator_stamps_metadata(self) -> None:
        @singleton()
        class Tagged:
            pass

        from qx.di import get_metadata

        meta = get_metadata(Tagged)
        assert meta is not None
        assert meta.lifetime is Lifetime.SINGLETON

    async def test_decorator_with_key_routes_abstract_to_impl(self) -> None:
        class Repo: ...

        @singleton(key=Repo)
        class RealRepo(Repo): ...

        c = Container()
        # Manually use the decorator metadata (scan does this)
        from qx.di import get_metadata

        meta = get_metadata(RealRepo)
        assert meta is not None
        c.register(meta.key or RealRepo, RealRepo, lifetime=meta.lifetime)
        r = await c.resolve(Repo)
        assert isinstance(r, RealRepo)


# ---- Tags ----


class TestTags:
    async def test_providers_with_tag(self) -> None:
        c = Container()
        c.register_singleton(Clock, Clock, tags=("util",))
        c.register_singleton(Db, Db, tags=("util", "io"))
        util_providers = c.providers_with_tag("util")
        assert len(util_providers) == 2

    async def test_tag_only_returns_correct_providers(self) -> None:
        c = Container()
        c.register_singleton(Clock, Clock, tags=("a",))
        c.register_singleton(Db, Db, tags=("b",))
        a_providers = c.providers_with_tag("a")
        assert len(a_providers) == 1


# ---- Hierarchy ----


class TestHierarchy:
    async def test_child_falls_back_to_parent(self) -> None:
        parent = Container()
        parent.register_singleton(Clock, Clock)
        child = parent.create_child("child")
        assert isinstance(await child.resolve(Clock), Clock)

    async def test_child_shadows_parent(self) -> None:
        parent = Container()
        parent.register_singleton(Clock, Clock)

        class OtherClock(Clock): ...

        child = parent.create_child("child")
        child.register_singleton(Clock, OtherClock)
        assert isinstance(await child.resolve(Clock), OtherClock)
        # Parent unchanged
        assert type(await parent.resolve(Clock)) is Clock


# ---- Registration errors ----


class TestRegistrationErrors:
    def test_bare_value_with_non_singleton_lifetime_raises(self) -> None:
        c = Container()
        with pytest.raises(RegistrationError):
            c.register(Clock, Clock(), lifetime=Lifetime.TRANSIENT)
