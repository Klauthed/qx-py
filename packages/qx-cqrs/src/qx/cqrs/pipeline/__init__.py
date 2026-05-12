"""Pipeline behaviors.

A *behavior* is middleware around a handler invocation. Behaviors compose into
a pipeline that wraps every command/query handler call.

**Built-in behaviors**

- ``LoggingBehavior`` — structured log of message envelope, duration, outcome
- ``ExceptionTranslationBehavior`` — catch bare exceptions, convert to ``Result.failure``
- ``ValidationBehavior`` — call ``validate_business()`` on the message + extra validators
- ``TransactionBehavior`` — open/commit/rollback a unit-of-work around commands
- ``RetryBehavior`` — retry transient ``InfrastructureError`` / ``TimeoutError`` with backoff
- ``IdempotencyBehavior`` — short-circuit duplicate command replays
- ``AuthorizationBehavior`` — enforce RBAC/policy before reaching the handler

**Observability behaviors** (require ``qx-observability``):

- ``TracingBehavior`` — OpenTelemetry span per dispatch
- ``MetricsBehavior`` — Prometheus counter + histogram per dispatch

**Recommended pipeline order** (outermost → innermost)::

    LoggingBehavior
    TracingBehavior
    MetricsBehavior
    ValidationBehavior
    AuthorizationBehavior
    IdempotencyBehavior
    TransactionBehavior
    RetryBehavior
    ExceptionTranslationBehavior
    ─── handler ───

Outer behaviors see all inner outcomes, so:
- Logging captures the final result (success *or* failure after all retries).
- Transaction commits only after AuthZ/Validation pass.
- Retry gets fresh transactions on each attempt.
- ExceptionTranslation sits just inside Retry so exceptions become Results before
  Retry decides whether to re-run.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
import random
import time
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Any, Protocol, runtime_checkable

from qx.core import (
    DomainError,
    Error,
    ForbiddenError,
    InfrastructureError,
    Result,
    TimeoutError,
    UnauthorizedError,
    current_context,
)
from qx.cqrs.messages import Command

__all__ = [
    "AuthorizationBehavior",
    # Protocol + plumbing
    "Behavior",
    "BehaviorChain",
    "ExceptionTranslationBehavior",
    "IdempotencyBehavior",
    # Built-in behaviors
    "LoggingBehavior",
    "Next",
    "RetryBehavior",
    "TransactionBehavior",
    "ValidationBehavior",
    "compose",
    # Context-var accessors
    "get_current_uow",
]

# ---------------------------------------------------------------------------
# Core types
# ---------------------------------------------------------------------------

Next = Callable[[Any], Awaitable[Result[Any]]]


@runtime_checkable
class Behavior(Protocol):
    """Pipeline behavior — wraps a handler invocation."""

    async def handle(self, message: Any, next_: Next) -> Result[Any]: ...


class BehaviorChain:
    """Compose behaviors into a single callable around a terminal handler.

    Built inside-out: the last behavior in the list is the innermost wrapper.
    """

    def __init__(
        self,
        behaviors: tuple[Behavior, ...],
        terminal: Callable[[Any], Awaitable[Result[Any]]],
    ) -> None:
        self._behaviors = behaviors
        self._terminal = terminal

    async def execute(self, message: Any) -> Result[Any]:
        next_callable: Next = self._terminal
        for behavior in reversed(self._behaviors):
            current = behavior
            previous = next_callable

            async def runner(msg: Any, b: Behavior = current, n: Next = previous) -> Result[Any]:
                return await b.handle(msg, n)

            next_callable = runner
        return await next_callable(message)


def compose(
    behaviors: tuple[Behavior, ...],
    terminal: Callable[[Any], Awaitable[Result[Any]]],
) -> Callable[[Any], Awaitable[Result[Any]]]:
    """Build a behavior chain and return its ``execute`` method."""
    return BehaviorChain(behaviors, terminal).execute


# ---------------------------------------------------------------------------
# LoggingBehavior (original)
# ---------------------------------------------------------------------------


class LoggingBehavior:
    """Log every dispatch with timing and outcome.

    Uses Python's standard ``logging`` module with a structured ``extra``
    payload. In production, ``qx-observability``'s structlog integration
    replaces the stdlib handler and emits JSON.
    """

    def __init__(self, logger_name: str = "qx.cqrs") -> None:
        self._log = logging.getLogger(logger_name)

    async def handle(self, message: Any, next_: Next) -> Result[Any]:
        ctx = current_context()
        msg_type = type(message).__name__
        start = time.perf_counter()
        try:
            result = await next_(message)
        except BaseException:
            self._log.exception(
                "%s raised unexpectedly",
                msg_type,
                extra={
                    "message_type": msg_type,
                    "correlation_id": str(ctx.correlation_id),
                    "duration_ms": (time.perf_counter() - start) * 1000,
                },
            )
            raise
        elapsed_ms = (time.perf_counter() - start) * 1000
        if result.is_success:
            self._log.info(
                "%s ok in %.1fms",
                msg_type,
                elapsed_ms,
                extra={
                    "message_type": msg_type,
                    "outcome": "success",
                    "correlation_id": str(ctx.correlation_id),
                    "duration_ms": elapsed_ms,
                },
            )
        else:
            err = result.error
            level = "warning" if isinstance(err, DomainError) else "error"
            getattr(self._log, level)(
                "%s failed: %s in %.1fms",
                msg_type,
                err.code,
                elapsed_ms,
                extra={
                    "message_type": msg_type,
                    "outcome": "failure",
                    "error_code": err.code,
                    "error_category": err.category,
                    "correlation_id": str(ctx.correlation_id),
                    "duration_ms": elapsed_ms,
                },
            )
        return result


# ---------------------------------------------------------------------------
# ExceptionTranslationBehavior (original)
# ---------------------------------------------------------------------------


class ExceptionTranslationBehavior:
    """Catch unexpected exceptions and wrap in ``InfrastructureError`` Results.

    Position: just inside ``RetryBehavior`` so retries see ``Result.failure``
    rather than propagating exceptions.
    """

    async def handle(self, message: Any, next_: Next) -> Result[Any]:
        try:
            return await next_(message)
        except BaseException as exc:
            if isinstance(exc, Error):
                return Result.failure(exc)
            return Result.failure(
                InfrastructureError(
                    code="handler.unexpected_exception",
                    message=f"unexpected {type(exc).__name__}: {exc}",
                    cause=exc,
                )
            )


# ---------------------------------------------------------------------------
# ValidationBehavior
# ---------------------------------------------------------------------------


class ValidationBehavior:
    """Run application-level validation beyond Pydantic field constraints.

    Two extension points:

    1. **Per-message**: define ``async def validate_business(self) -> Result[None]``
       on the Command/Query class. Use for async business-rule checks (e.g.,
       "email not already taken") that need a DB or cache lookup.

    2. **Pipeline-level**: pass extra validator callables at construction. Each
       receives the message and must return ``Result[None]``.

    Pydantic validates field types at object construction — this behavior is for
    the layer of validation that needs async I/O or cross-field business logic.

    Example::

        class RegisterUserCommand(Command[UserDto]):
            email: str

            async def validate_business(self) -> Result[None]:
                # called by ValidationBehavior before the handler runs
                if not self.email.endswith("@example.com"):
                    return Result.failure(
                        ValidationError(code="email.domain", message="...")
                    )
                return Result.success(None)
    """

    def __init__(
        self,
        *extra_validators: Callable[[Any], Awaitable[Result[None]] | Result[None]],
    ) -> None:
        self._extras = extra_validators

    async def handle(self, message: Any, next_: Next) -> Result[Any]:
        # Per-message hook
        validate = getattr(message, "validate_business", None)
        if validate is not None and callable(validate):
            out: Any = validate()
            if inspect.isawaitable(out):
                out = await out
            if isinstance(out, Result) and out.is_failure:
                return out

        # Pipeline-level validators
        for validator in self._extras:
            v_out: Any = validator(message)
            if inspect.isawaitable(v_out):
                v_out = await v_out
            if isinstance(v_out, Result) and v_out.is_failure:
                return v_out

        return await next_(message)


# ---------------------------------------------------------------------------
# TransactionBehavior
# ---------------------------------------------------------------------------

# Context var so get_current_uow() works inside any code that runs within
# a transactional pipeline — not just the handler itself but also domain
# services called from the handler.
_active_uow: ContextVar[Any] = ContextVar("qx_active_uow", default=None)


def get_current_uow() -> Any:
    """Return the unit-of-work opened by ``TransactionBehavior``.

    Call from command handlers or domain services invoked within a transactional
    pipeline. Raises ``RuntimeError`` if called outside a transactional context.
    """
    uow = _active_uow.get()
    if uow is None:
        raise RuntimeError(
            "No active unit-of-work. Ensure TransactionBehavior is in the "
            "command pipeline and this is called from within a command handler."
        )
    return uow


class TransactionBehavior:
    """Open, commit, or rollback a unit-of-work around each command.

    Queries pass through without opening a transaction (reads are never wrapped).

    The UoW is stored in a context variable for the duration of the handler
    call. Handlers (and domain code they call) can access it via
    ``get_current_uow()`` or through the DI-injected instance — both refer to
    the same object when the factory produces the same instance.

    ``uow_factory`` must be a zero-argument callable (sync or async) that
    returns a UoW-compatible object — one that is an async context manager and
    exposes ``async def commit() -> None``.

    Example bootstrap::

        from qx.db import UnitOfWork

        behavior = TransactionBehavior(
            uow_factory=lambda: UnitOfWork(
                session_factory=session_factory,
                dispatcher=dispatcher,
                outbox=outbox_recorder,
            )
        )
        mediator = Mediator(
            container,
            command_behaviors=(
                LoggingBehavior(),
                TransactionBehavior(uow_factory=...),
                RetryBehavior(),
                ExceptionTranslationBehavior(),
            ),
        )
    """

    def __init__(
        self,
        uow_factory: Callable[[], Awaitable[Any] | Any],
    ) -> None:
        self._factory = uow_factory

    async def handle(self, message: Any, next_: Next) -> Result[Any]:
        if not isinstance(message, Command):
            return await next_(message)

        raw = self._factory()
        uow = await raw if inspect.isawaitable(raw) else raw

        token = _active_uow.set(uow)
        try:
            async with uow:
                result = await next_(message)
                if result.is_success:
                    await uow.commit()
                return result
        finally:
            _active_uow.reset(token)


# ---------------------------------------------------------------------------
# RetryBehavior
# ---------------------------------------------------------------------------

_RETRYABLE_TYPES = (InfrastructureError, TimeoutError)


class RetryBehavior:
    """Retry the handler on transient failures with exponential back-off + jitter.

    **What gets retried**: ``Result.failure`` carrying an ``InfrastructureError``
    or ``TimeoutError``. Domain/client errors (``NotFoundError``,
    ``ValidationError``, ``ForbiddenError``, …) are returned immediately —
    retrying them would be wrong and wasteful.

    **Position**: place *inside* ``TransactionBehavior`` so each retry attempt
    gets a fresh transaction, and *outside* ``ExceptionTranslationBehavior`` so
    translated exceptions become Results before Retry evaluates them::

        TransactionBehavior → RetryBehavior → ExceptionTranslationBehavior → Handler

    **Back-off formula**: ``min(base * 2^attempt, max_delay) + uniform(0, jitter)``

    Args:
        max_attempts: Total attempts including the first (default 3).
        base_delay_seconds: Delay after the first failure (default 0.1 s).
        max_delay_seconds: Cap on the computed delay (default 5 s).
        jitter: Upper bound of the random jitter added to each delay (default 0.05 s).
        retryable_codes: If given, only retry errors whose ``code`` is in this set.
    """

    def __init__(
        self,
        *,
        max_attempts: int = 3,
        base_delay_seconds: float = 0.1,
        max_delay_seconds: float = 5.0,
        jitter: float = 0.05,
        retryable_codes: frozenset[str] | None = None,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        self._max = max_attempts
        self._base = base_delay_seconds
        self._max_delay = max_delay_seconds
        self._jitter = jitter
        self._codes = retryable_codes

    def _should_retry(self, result: Result[Any]) -> bool:
        if not result.is_failure:
            return False
        err = result.error
        if not isinstance(err, _RETRYABLE_TYPES):
            return False
        return not (self._codes is not None and err.code not in self._codes)

    def _delay(self, attempt: int) -> float:
        raw: float = self._base * (2**attempt) + random.uniform(0, self._jitter)
        return min(raw, self._max_delay)

    async def handle(self, message: Any, next_: Next) -> Result[Any]:
        last: Result[Any] | None = None
        for attempt in range(self._max):
            result = await next_(message)
            if not self._should_retry(result):
                return result
            last = result
            if attempt < self._max - 1:
                await asyncio.sleep(self._delay(attempt))
        assert last is not None  # loop runs at least once
        return last


# ---------------------------------------------------------------------------
# IdempotencyBehavior
# ---------------------------------------------------------------------------


@runtime_checkable
class _IdempotencyStoreProto(Protocol):
    """Structural interface for ``qx.cache.IdempotencyStore``.

    Keeping this as a protocol avoids a hard dependency on ``qx-cache`` from
    ``qx-cqrs`` while still being fully type-safe at the call sites.
    """

    async def begin(
        self,
        tenant: str,
        idem_key: str,
        payload: Any,
        *,
        ttl_seconds: int | None = None,
    ) -> Result[Any]: ...

    async def complete(
        self,
        tenant: str,
        idem_key: str,
        response: Any,
        *,
        ttl_seconds: int | None = None,
    ) -> None: ...


class IdempotencyBehavior:
    """Short-circuit duplicate command invocations using an idempotency store.

    Workflow:

    1. **First call** — claim the key, run the handler, persist the response.
    2. **Replay** — key already claimed and completed: return the stored response
       immediately. The handler is *not* called.
    3. **In-progress collision** — a previous request with the same key is still
       running: return ``ConflictError``. Caller should retry after a short wait.
    4. **Payload mismatch** — same key but different body: return
       ``PreconditionFailedError``. Clients should never reuse keys across
       different requests.

    Only applies to Commands whose payload includes an ``idempotency_key``
    field (string, non-empty). Queries always pass through unchanged.

    The idempotency key is the caller's responsibility — typically the
    ``Idempotency-Key`` HTTP header copied into the command::

        class RegisterUserCommand(Command[UserDto]):
            email: str
            idempotency_key: str | None = None

    The response stored in the idempotency log is the raw ``Result.value``.
    On replay it is returned wrapped in ``Result.success``. If your handler
    returns a non-serializable object, override ``_serialize_response`` on a
    subclass.
    """

    def __init__(
        self,
        store: _IdempotencyStoreProto,
        *,
        ttl_seconds: int | None = None,
    ) -> None:
        self._store = store
        self._ttl = ttl_seconds

    async def handle(self, message: Any, next_: Next) -> Result[Any]:
        if not isinstance(message, Command):
            return await next_(message)

        idem_key: str | None = getattr(message, "idempotency_key", None)
        if not idem_key:
            return await next_(message)

        ctx = current_context()
        tenant = str(ctx.tenant_id) if ctx.tenant_id else "global"
        payload = message.model_dump(mode="json")

        claim = await self._store.begin(tenant, idem_key, payload, ttl_seconds=self._ttl)
        if claim.is_failure:
            return claim

        stored = claim.value
        if stored is not None:
            # Replay: reconstruct a success result from the stored response.
            return Result.success(stored)

        # First execution: run the handler and persist the result.
        result = await next_(message)
        if result.is_success:
            with contextlib.suppress(Exception):
                await self._store.complete(tenant, idem_key, result.value, ttl_seconds=self._ttl)
        return result


# ---------------------------------------------------------------------------
# AuthorizationBehavior
# ---------------------------------------------------------------------------


class AuthorizationBehavior:
    """Evaluate authorization policies before the handler runs.

    The active ``Principal`` is read from ``RequestContext.attributes``
    under the key ``"qx.principal"``. The JWT middleware (``qx-auth``) sets
    this when it validates an inbound token. If it is absent, the behavior
    returns ``UnauthorizedError``.

    Two ways to attach policies:

    1. **Pipeline-wide defaults** — pass at construction; applied to every
       command/query that passes through the mediator::

           AuthorizationBehavior(policies=[require_permission("api.access")])

    2. **Per-message** — use the ``@requires`` decorator on the Command/Query
       class; these are evaluated in addition to any defaults::

           @requires(require_permission("user.write"))
           class RegisterUserCommand(Command[UserDto]):
               email: str

    Policy objects can be anything that implements one of these duck-type shapes:

    - ``policy.evaluate(principal, message) -> Awaitable[Result[None]]``
      (``qx-auth``'s ``PolicyEvaluator`` pattern).
    - ``policy.rule(principal, message) -> Awaitable[PolicyResult]``
      (``qx-auth``'s ``Policy`` dataclass pattern; ``PolicyResult.decision``
      must be the string ``"deny"`` when denying).
    - Any async or sync callable ``(principal, message) -> Result[None]``.

    The ``principal_key`` constructor arg (default ``"qx.principal"``) lets you
    override where the principal is stored in ``RequestContext.attributes``,
    useful when integrating a custom auth middleware.
    """

    def __init__(
        self,
        *,
        policies: list[Any] | None = None,
        principal_key: str = "qx.principal",
    ) -> None:
        self._policies = list(policies or [])
        self._principal_key = principal_key

    async def handle(self, message: Any, next_: Next) -> Result[Any]:
        message_policies: list[Any] = list(getattr(message, "__qx_policies__", []))
        all_policies = self._policies + message_policies

        if not all_policies:
            return await next_(message)

        ctx = current_context()
        principal = ctx.attributes.get(self._principal_key)

        if principal is None:
            return Result.failure(
                UnauthorizedError(
                    code="authz.not_authenticated",
                    message="This operation requires authentication.",
                )
            )

        for policy in all_policies:
            outcome = await self._evaluate_one(policy, principal, message)
            if outcome.is_failure:
                return outcome

        return await next_(message)

    @staticmethod
    async def _evaluate_one(
        policy: Any,
        principal: Any,
        message: Any,
    ) -> Result[None]:
        # Shape 1: evaluate(principal, resource) → Result[None]
        if hasattr(policy, "evaluate") and callable(policy.evaluate):
            return await policy.evaluate(principal, message)  # type: ignore[no-any-return]

        # Shape 2: rule(principal, resource) → PolicyResult  (qx-auth Policy dataclass)
        if hasattr(policy, "rule") and callable(policy.rule):
            policy_result = policy.rule(principal, message)
            if inspect.isawaitable(policy_result):
                policy_result = await policy_result
            # Inspect .decision — works for both the enum value and its string repr.
            decision = getattr(policy_result, "decision", None)
            decision_str = (
                decision.value
                if decision is not None and hasattr(decision, "value")
                else str(decision)
            ).lower()
            if decision_str == "deny":
                reason: str = getattr(policy_result, "reason", None) or "Denied."
                policy_name: str = getattr(policy, "name", "unnamed-policy")
                return Result.failure(
                    ForbiddenError(
                        code="authz.denied",
                        message=reason,
                        details={"policy": policy_name},
                    )
                )
            return Result.success(None)

        # Shape 3: callable (principal, message) → Result[None]
        if callable(policy):
            out: Any = policy(principal, message)
            if inspect.isawaitable(out):
                out = await out
            if isinstance(out, Result):
                return out

        # Unknown shape — treat as no-opinion (allow).
        return Result.success(None)
