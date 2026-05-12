"""Result pattern: an algebraic success/failure type.

Handlers return ``Result[T]`` rather than raising for business failures. Exceptions
are reserved for genuine bugs and infrastructure faults. The HTTP/gRPC layer maps
``Result.failure`` outcomes to transport-appropriate responses.

Why a Result type and not exceptions?

- Forces handlers to *enumerate* their failure modes in their signature.
- Removes the ambiguity between "expected business outcome" and "unexpected fault".
- Composes cleanly under ``map`` / ``bind`` without ``try`` cascades.
- Plays well with structured concurrency: a ``Result.failure`` does not poison a task group.

This module is dependency-free; it imports nothing outside the standard library and
``typing_extensions``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import (
    Any,
    NoReturn,
    Self,
    TypeVar,
    cast,
    overload,
)

from qx.core.errors import Error

__all__ = ["Failure", "Result", "ResultLike", "Success"]

T = TypeVar("T")
U = TypeVar("U")
E = TypeVar("E", bound=Error)

_MISSING: Any = object()


@dataclass(frozen=True, slots=True)
class Success[T]:
    """Success variant of a ``Result``. Carries the value and arbitrary metadata."""

    value: T
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Failure:
    """Failure variant of a ``Result``. Carries a structured ``Error`` and metadata."""

    error: Error
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Result[T]:
    """A tagged union of ``Success[T]`` or ``Failure``.

    Construct via the class methods, never directly::

        Result.success(user)
        Result.failure(NotFoundError("user.not_found", "User does not exist"))

    Inspect via ``is_success`` / ``is_failure`` or pattern-match on ``.outcome``.
    """

    outcome: Success[T] | Failure

    # ---- constructors ----

    @classmethod
    def success(cls, value: T, /, **metadata: Any) -> Result[T]:
        return cls(outcome=Success(value=value, metadata=metadata))

    @classmethod
    def failure(cls, error: Error, /, **metadata: Any) -> Result[T]:
        return cls(outcome=Failure(error=error, metadata=metadata))

    @classmethod
    def from_optional(
        cls,
        value: T | None,
        /,
        *,
        on_none: Error,
        **metadata: Any,
    ) -> Result[T]:
        """Lift an ``Optional[T]`` into a ``Result[T]`` using ``on_none`` if absent."""
        if value is None:
            return cls.failure(on_none, **metadata)
        return cls.success(value, **metadata)

    # ---- predicates ----

    @property
    def is_success(self) -> bool:
        return isinstance(self.outcome, Success)

    @property
    def is_failure(self) -> bool:
        return isinstance(self.outcome, Failure)

    # ---- accessors ----

    @property
    def value(self) -> T:
        """The success value. Raises ``ValueError`` if called on a failure.

        Prefer ``unwrap_or`` / ``match`` for safety. This accessor exists for use
        sites that have already proved success via ``is_success``.
        """
        if isinstance(self.outcome, Failure):
            raise ValueError(f"Called .value on a Failure result: {self.outcome.error.code}")
        return self.outcome.value

    @property
    def error(self) -> Error:
        """The failure error. Raises ``ValueError`` if called on a success."""
        if isinstance(self.outcome, Success):
            raise ValueError("Called .error on a Success result")
        return self.outcome.error

    @property
    def metadata(self) -> dict[str, Any]:
        return self.outcome.metadata

    # ---- combinators ----

    def map(self, fn: Callable[[T], U], /) -> Result[U]:
        """Apply ``fn`` to a success value; pass failures through unchanged."""
        if isinstance(self.outcome, Failure):
            return cast("Result[U]", self)
        return Result.success(fn(self.outcome.value), **self.outcome.metadata)

    def bind(self, fn: Callable[[T], Result[U]], /) -> Result[U]:
        """Monadic bind: chain ``Result``-returning functions."""
        if isinstance(self.outcome, Failure):
            return cast("Result[U]", self)
        return fn(self.outcome.value)

    async def map_async(self, fn: Callable[[T], Awaitable[U]], /) -> Result[U]:
        if isinstance(self.outcome, Failure):
            return cast("Result[U]", self)
        return Result.success(await fn(self.outcome.value), **self.outcome.metadata)

    async def bind_async(self, fn: Callable[[T], Awaitable[Result[U]]], /) -> Result[U]:
        if isinstance(self.outcome, Failure):
            return cast("Result[U]", self)
        return await fn(self.outcome.value)

    def map_error(self, fn: Callable[[Error], Error], /) -> Self:
        """Transform the error on a failure; pass successes through."""
        if isinstance(self.outcome, Success):
            return self
        return cast("Self", Result.failure(fn(self.outcome.error), **self.outcome.metadata))

    # ---- escape hatches ----

    @overload
    def unwrap_or(self, default: T, /) -> T: ...
    @overload
    def unwrap_or(self, default: U, /) -> T | U: ...
    def unwrap_or(self, default: Any, /) -> Any:
        if isinstance(self.outcome, Failure):
            return default
        return self.outcome.value

    def unwrap_or_else(self, fn: Callable[[Error], T], /) -> T:
        if isinstance(self.outcome, Failure):
            return fn(self.outcome.error)
        return self.outcome.value

    def unwrap_or_raise(self) -> T:
        """Return the value, or raise the error's underlying exception.

        Designed for adapters at the edge (CLI commands, scripts) where converting
        ``Failure`` back to an exception is appropriate. Do **not** use inside
        handlers — return the Result directly instead.
        """
        if isinstance(self.outcome, Failure):
            raise self.outcome.error.as_exception()
        return self.outcome.value

    # ---- pattern matching support ----

    def match(
        self,
        *,
        on_success: Callable[[T], U],
        on_failure: Callable[[Error], U],
    ) -> U:
        if isinstance(self.outcome, Success):
            return on_success(self.outcome.value)
        return on_failure(self.outcome.error)

    # ---- representations ----

    def __repr__(self) -> str:
        if isinstance(self.outcome, Success):
            return f"Result.success({self.outcome.value!r})"
        return f"Result.failure({self.outcome.error.code!r})"

    def __bool__(self) -> NoReturn:
        # Forbid truthiness checks; callers must use is_success / is_failure
        # to avoid the trap of `if result:` silently being always-true.
        raise TypeError(
            "Result objects do not support truthiness. Use .is_success or .is_failure explicitly."
        )


type ResultLike[T] = Result[T] | Awaitable[Result[T]]
"""Marker type for APIs that accept either sync or async-returning Results."""
