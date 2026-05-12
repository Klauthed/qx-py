"""Tests for the Result type. These pin behavior that the rest of the framework relies on."""

from __future__ import annotations

import pytest

from qx.core import (
    Failure,
    InfrastructureError,
    NotFoundError,
    Result,
    Success,
    ValidationError,
)


class TestResultConstruction:
    def test_success_carries_value(self) -> None:
        r = Result[int].success(42)
        assert r.is_success
        assert not r.is_failure
        assert r.value == 42

    def test_failure_carries_error(self) -> None:
        err = NotFoundError(code="user.not_found", message="nope")
        r: Result[int] = Result.failure(err)
        assert r.is_failure
        assert r.error.code == "user.not_found"

    def test_from_optional_lifts_some(self) -> None:
        r = Result.from_optional(
            "value",
            on_none=NotFoundError(code="x", message="x"),
        )
        assert r.is_success

    def test_from_optional_lifts_none(self) -> None:
        r = Result.from_optional(
            None,
            on_none=NotFoundError(code="x", message="x"),
        )
        assert r.is_failure


class TestResultAccessors:
    def test_value_on_failure_raises(self) -> None:
        r: Result[int] = Result.failure(NotFoundError(code="x", message="x"))
        with pytest.raises(ValueError, match="Failure"):
            _ = r.value

    def test_error_on_success_raises(self) -> None:
        r = Result[int].success(1)
        with pytest.raises(ValueError, match="Success"):
            _ = r.error

    def test_truthiness_is_forbidden(self) -> None:
        """We forbid `if result:` — too many bugs hide there."""
        r = Result[int].success(1)
        with pytest.raises(TypeError):
            bool(r)


class TestResultCombinators:
    def test_map_transforms_success(self) -> None:
        r = Result[int].success(10).map(lambda x: x * 2)
        assert r.value == 20

    def test_map_skips_failure(self) -> None:
        called: list[int] = []
        r: Result[int] = Result.failure(NotFoundError(code="x", message="x"))
        r.map(lambda x: called.append(x) or x)
        assert called == []

    def test_bind_chains_results(self) -> None:
        def half(x: int) -> Result[float]:
            return Result.success(x / 2)

        r = Result[int].success(10).bind(half)
        assert r.value == 5.0

    def test_bind_short_circuits_on_failure(self) -> None:
        called = False

        def explode(_: int) -> Result[int]:
            nonlocal called
            called = True
            return Result.success(0)

        r: Result[int] = Result.failure(NotFoundError(code="x", message="x"))
        r.bind(explode)
        assert called is False

    async def test_map_async(self) -> None:
        async def double(x: int) -> int:
            return x * 2

        r = await Result[int].success(5).map_async(double)
        assert r.value == 10

    async def test_bind_async(self) -> None:
        async def lift(x: int) -> Result[int]:
            return Result.success(x + 1)

        r = await Result[int].success(5).bind_async(lift)
        assert r.value == 6

    def test_map_error_transforms_failure(self) -> None:
        r: Result[int] = Result.failure(
            NotFoundError(code="x", message="orig")
        )
        r2 = r.map_error(
            lambda e: InfrastructureError(code="wrapped", message=e.message)
        )
        assert r2.error.code == "wrapped"


class TestResultEscapeHatches:
    def test_unwrap_or_returns_default_on_failure(self) -> None:
        r: Result[int] = Result.failure(NotFoundError(code="x", message="x"))
        assert r.unwrap_or(99) == 99

    def test_unwrap_or_else_invokes_callback(self) -> None:
        r: Result[int] = Result.failure(NotFoundError(code="x", message="x"))
        assert r.unwrap_or_else(lambda e: -1) == -1

    def test_unwrap_or_raise_on_failure(self) -> None:
        r: Result[int] = Result.failure(NotFoundError(code="x", message="boom"))
        with pytest.raises(Exception) as exc_info:
            r.unwrap_or_raise()
        assert "boom" in str(exc_info.value)


class TestResultMatch:
    def test_match_routes_success(self) -> None:
        r = Result[int].success(7)
        out = r.match(on_success=lambda x: f"got {x}", on_failure=lambda _: "no")
        assert out == "got 7"

    def test_match_routes_failure(self) -> None:
        r: Result[int] = Result.failure(ValidationError(code="x", message="bad"))
        out = r.match(on_success=lambda _: "ok", on_failure=lambda e: e.message)
        assert out == "bad"


class TestResultRepr:
    def test_success_repr(self) -> None:
        assert "success" in repr(Result[int].success(1)).lower()

    def test_failure_repr_includes_code(self) -> None:
        r: Result[int] = Result.failure(NotFoundError(code="abc.def", message="x"))
        assert "abc.def" in repr(r)


class TestVariants:
    """Sanity tests on the underlying Success/Failure dataclasses."""

    def test_success_is_frozen(self) -> None:
        s = Success(value=1)
        with pytest.raises(Exception):
            s.value = 2  # type: ignore[misc]

    def test_failure_is_frozen(self) -> None:
        f = Failure(error=NotFoundError(code="x", message="x"))
        with pytest.raises(Exception):
            f.error = NotFoundError(code="y", message="y")  # type: ignore[misc]
