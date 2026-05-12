"""Tests for the pipeline behaviors added in qx-cqrs.

Covers: ValidationBehavior, TransactionBehavior, RetryBehavior,
IdempotencyBehavior, AuthorizationBehavior, and the @requires decorator.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call
from uuid import UUID, uuid4

import pytest

from qx.core import (
    InfrastructureError,
    NotFoundError,
    Result,
    UnauthorizedError,
    ForbiddenError,
    ValidationError,
    request_scope,
    RequestContext,
    set_context,
    reset_context,
)
from qx.cqrs import (
    AuthorizationBehavior,
    Command,
    IdempotencyBehavior,
    Query,
    RetryBehavior,
    TransactionBehavior,
    ValidationBehavior,
    get_current_uow,
    requires,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _OkCmd(Command[str]):
    value: str = "ok"


class _OkQuery(Query[str]):
    value: str = "ok"


async def _ok_next(msg: Any) -> Result[str]:
    return Result.success("done")


async def _fail_next(msg: Any) -> Result[str]:
    return Result.failure(NotFoundError(code="x.not_found", message="not found"))


# ---------------------------------------------------------------------------
# ValidationBehavior
# ---------------------------------------------------------------------------


class TestValidationBehavior:
    @pytest.mark.asyncio
    async def test_passes_through_when_no_validate_method(self):
        behavior = ValidationBehavior()
        result = await behavior.handle(_OkCmd(), _ok_next)
        assert result.is_success

    @pytest.mark.asyncio
    async def test_calls_validate_business_on_message(self):
        class _ValidatedCmd(Command[str]):
            should_fail: bool = False

            async def validate_business(self) -> Result[None]:
                if self.should_fail:
                    return Result.failure(
                        ValidationError(code="v.fail", message="bad input")
                    )
                return Result.success(None)

        behavior = ValidationBehavior()

        ok = await behavior.handle(_ValidatedCmd(should_fail=False), _ok_next)
        assert ok.is_success

        fail = await behavior.handle(_ValidatedCmd(should_fail=True), _ok_next)
        assert fail.is_failure
        assert fail.error.code == "v.fail"

    @pytest.mark.asyncio
    async def test_extra_validators_run(self):
        called: list[Any] = []

        async def extra(msg: Any) -> Result[None]:
            called.append(msg)
            return Result.success(None)

        behavior = ValidationBehavior(extra)
        cmd = _OkCmd()
        await behavior.handle(cmd, _ok_next)
        assert called == [cmd]

    @pytest.mark.asyncio
    async def test_extra_validator_short_circuits(self):
        handler_called = False

        async def blocking_next(msg: Any) -> Result[str]:
            nonlocal handler_called
            handler_called = True
            return Result.success("done")

        async def reject(msg: Any) -> Result[None]:
            return Result.failure(ValidationError(code="v.reject", message="rejected"))

        behavior = ValidationBehavior(reject)
        result = await behavior.handle(_OkCmd(), blocking_next)

        assert result.is_failure
        assert result.error.code == "v.reject"
        assert not handler_called


# ---------------------------------------------------------------------------
# TransactionBehavior
# ---------------------------------------------------------------------------


class _FakeUoW:
    def __init__(self) -> None:
        self.committed = False
        self.entered = False
        self.exited = False

    async def __aenter__(self) -> "_FakeUoW":
        self.entered = True
        return self

    async def __aexit__(self, *_: Any) -> None:
        self.exited = True

    async def commit(self) -> None:
        self.committed = True


class TestTransactionBehavior:
    @pytest.mark.asyncio
    async def test_commits_on_success(self):
        uow = _FakeUoW()
        behavior = TransactionBehavior(uow_factory=lambda: uow)
        result = await behavior.handle(_OkCmd(), _ok_next)
        assert result.is_success
        assert uow.committed

    @pytest.mark.asyncio
    async def test_no_commit_on_failure(self):
        uow = _FakeUoW()
        behavior = TransactionBehavior(uow_factory=lambda: uow)
        result = await behavior.handle(_OkCmd(), _fail_next)
        assert result.is_failure
        assert not uow.committed
        assert uow.exited  # context manager was still exited

    @pytest.mark.asyncio
    async def test_skips_queries(self):
        uow = _FakeUoW()
        behavior = TransactionBehavior(uow_factory=lambda: uow)
        result = await behavior.handle(_OkQuery(), _ok_next)
        assert result.is_success
        assert not uow.entered  # UoW not touched for queries

    @pytest.mark.asyncio
    async def test_get_current_uow_returns_active_uow(self):
        uow = _FakeUoW()
        captured: list[Any] = []

        async def capturing_next(msg: Any) -> Result[str]:
            captured.append(get_current_uow())
            return Result.success("done")

        behavior = TransactionBehavior(uow_factory=lambda: uow)
        await behavior.handle(_OkCmd(), capturing_next)
        assert captured == [uow]

    def test_get_current_uow_raises_outside_transaction(self):
        with pytest.raises(RuntimeError, match="No active unit-of-work"):
            get_current_uow()


# ---------------------------------------------------------------------------
# RetryBehavior
# ---------------------------------------------------------------------------


class TestRetryBehavior:
    @pytest.mark.asyncio
    async def test_returns_success_immediately(self):
        calls = 0

        async def next_(msg: Any) -> Result[str]:
            nonlocal calls
            calls += 1
            return Result.success("done")

        behavior = RetryBehavior(max_attempts=3)
        result = await behavior.handle(_OkCmd(), next_)
        assert result.is_success
        assert calls == 1

    @pytest.mark.asyncio
    async def test_retries_on_infrastructure_error(self):
        calls = 0

        async def next_(msg: Any) -> Result[str]:
            nonlocal calls
            calls += 1
            if calls < 3:
                return Result.failure(
                    InfrastructureError(code="db.timeout", message="timeout")
                )
            return Result.success("done")

        behavior = RetryBehavior(max_attempts=3, base_delay_seconds=0.0, jitter=0.0)
        result = await behavior.handle(_OkCmd(), next_)
        assert result.is_success
        assert calls == 3

    @pytest.mark.asyncio
    async def test_exhausts_attempts_and_returns_last_failure(self):
        async def always_fail(msg: Any) -> Result[str]:
            return Result.failure(
                InfrastructureError(code="db.error", message="persistent error")
            )

        behavior = RetryBehavior(max_attempts=2, base_delay_seconds=0.0, jitter=0.0)
        result = await behavior.handle(_OkCmd(), always_fail)
        assert result.is_failure
        assert result.error.code == "db.error"

    @pytest.mark.asyncio
    async def test_does_not_retry_domain_errors(self):
        calls = 0

        async def next_(msg: Any) -> Result[str]:
            nonlocal calls
            calls += 1
            return Result.failure(NotFoundError(code="x.not_found", message="not found"))

        behavior = RetryBehavior(max_attempts=3)
        result = await behavior.handle(_OkCmd(), next_)
        assert result.is_failure
        assert calls == 1

    @pytest.mark.asyncio
    async def test_retryable_codes_filter(self):
        calls = 0

        async def next_(msg: Any) -> Result[str]:
            nonlocal calls
            calls += 1
            return Result.failure(
                InfrastructureError(code="db.lock", message="lock")
            )

        behavior = RetryBehavior(
            max_attempts=3,
            base_delay_seconds=0.0,
            jitter=0.0,
            retryable_codes=frozenset({"db.timeout"}),  # "db.lock" not in set
        )
        result = await behavior.handle(_OkCmd(), next_)
        assert result.is_failure
        assert calls == 1  # no retry


# ---------------------------------------------------------------------------
# IdempotencyBehavior
# ---------------------------------------------------------------------------


class _FakeIdempotencyStore:
    def __init__(self, *, existing_response: Any = None) -> None:
        self._existing = existing_response
        self.begin_calls: list[tuple] = []
        self.complete_calls: list[tuple] = []

    async def begin(
        self, tenant: str, idem_key: str, payload: Any, *, ttl_seconds: int | None = None
    ) -> Result[Any]:
        self.begin_calls.append((tenant, idem_key, payload))
        return Result.success(self._existing)  # None = first call, value = replay

    async def complete(
        self, tenant: str, idem_key: str, response: Any, *, ttl_seconds: int | None = None
    ) -> None:
        self.complete_calls.append((tenant, idem_key, response))


class _IdempotentCmd(Command[str]):
    idempotency_key: str | None = None


class TestIdempotencyBehavior:
    @pytest.mark.asyncio
    async def test_passes_through_query(self):
        store = _FakeIdempotencyStore()
        behavior = IdempotencyBehavior(store)
        result = await behavior.handle(_OkQuery(), _ok_next)
        assert result.is_success
        assert not store.begin_calls

    @pytest.mark.asyncio
    async def test_passes_through_command_without_key(self):
        store = _FakeIdempotencyStore()
        behavior = IdempotencyBehavior(store)
        result = await behavior.handle(_IdempotentCmd(idempotency_key=None), _ok_next)
        assert result.is_success
        assert not store.begin_calls

    @pytest.mark.asyncio
    async def test_first_call_runs_handler_and_stores_response(self):
        store = _FakeIdempotencyStore(existing_response=None)
        behavior = IdempotencyBehavior(store)
        result = await behavior.handle(_IdempotentCmd(idempotency_key="key-1"), _ok_next)
        assert result.is_success
        assert result.value == "done"
        assert len(store.complete_calls) == 1
        assert store.complete_calls[0][2] == "done"

    @pytest.mark.asyncio
    async def test_replay_skips_handler(self):
        store = _FakeIdempotencyStore(existing_response="cached-response")
        handler_called = False

        async def counting_next(msg: Any) -> Result[str]:
            nonlocal handler_called
            handler_called = True
            return Result.success("fresh")

        behavior = IdempotencyBehavior(store)
        result = await behavior.handle(
            _IdempotentCmd(idempotency_key="key-1"), counting_next
        )
        assert result.is_success
        assert result.value == "cached-response"
        assert not handler_called

    @pytest.mark.asyncio
    async def test_store_failure_propagates(self):
        class _FailStore:
            async def begin(self, tenant, idem_key, payload, *, ttl_seconds=None):
                return Result.failure(
                    InfrastructureError(code="cache.unavailable", message="down")
                )

            async def complete(self, tenant, idem_key, response, *, ttl_seconds=None):
                pass

        behavior = IdempotencyBehavior(_FailStore())
        result = await behavior.handle(_IdempotentCmd(idempotency_key="k"), _ok_next)
        assert result.is_failure
        assert result.error.code == "cache.unavailable"


# ---------------------------------------------------------------------------
# AuthorizationBehavior + @requires
# ---------------------------------------------------------------------------


class _AllowPolicy:
    async def evaluate(self, principal: Any, message: Any) -> Result[None]:
        return Result.success(None)


class _DenyPolicy:
    name = "test-deny"

    async def evaluate(self, principal: Any, message: Any) -> Result[None]:
        return Result.failure(
            ForbiddenError(code="authz.denied", message="Denied.", details={})
        )


class TestAuthorizationBehavior:
    def _ctx_with_principal(self, principal: Any) -> RequestContext:
        return RequestContext(attributes={"qx.principal": principal})

    @pytest.mark.asyncio
    async def test_passes_through_when_no_policies(self):
        behavior = AuthorizationBehavior()
        result = await behavior.handle(_OkCmd(), _ok_next)
        assert result.is_success

    @pytest.mark.asyncio
    async def test_returns_unauthorized_when_no_principal(self):
        behavior = AuthorizationBehavior(policies=[_AllowPolicy()])
        result = await behavior.handle(_OkCmd(), _ok_next)
        assert result.is_failure
        assert isinstance(result.error, UnauthorizedError)

    @pytest.mark.asyncio
    async def test_allow_policy_passes_through(self):
        ctx = self._ctx_with_principal("user-1")
        token = set_context(ctx)
        try:
            behavior = AuthorizationBehavior(policies=[_AllowPolicy()])
            result = await behavior.handle(_OkCmd(), _ok_next)
            assert result.is_success
        finally:
            reset_context(token)

    @pytest.mark.asyncio
    async def test_deny_policy_returns_forbidden(self):
        ctx = self._ctx_with_principal("user-1")
        token = set_context(ctx)
        try:
            behavior = AuthorizationBehavior(policies=[_DenyPolicy()])
            result = await behavior.handle(_OkCmd(), _ok_next)
            assert result.is_failure
            assert isinstance(result.error, ForbiddenError)
        finally:
            reset_context(token)

    @pytest.mark.asyncio
    async def test_callable_policy_shape(self):
        ctx = self._ctx_with_principal("user-1")
        token = set_context(ctx)
        try:
            async def callable_policy(principal: Any, message: Any) -> Result[None]:
                return Result.success(None)

            behavior = AuthorizationBehavior(policies=[callable_policy])
            result = await behavior.handle(_OkCmd(), _ok_next)
            assert result.is_success
        finally:
            reset_context(token)

    @pytest.mark.asyncio
    async def test_requires_decorator_policies_evaluated(self):
        @requires(_DenyPolicy())
        class _SecureCmd(Command[str]):
            pass

        ctx = self._ctx_with_principal("user-1")
        token = set_context(ctx)
        try:
            behavior = AuthorizationBehavior()  # no constructor policies
            result = await behavior.handle(_SecureCmd(), _ok_next)
            assert result.is_failure
            assert isinstance(result.error, ForbiddenError)
        finally:
            reset_context(token)

    @pytest.mark.asyncio
    async def test_constructor_and_requires_policies_combined(self):
        called_policies: list[str] = []

        class _TrackPolicy:
            def __init__(self, name: str) -> None:
                self._name = name

            async def evaluate(self, principal: Any, message: Any) -> Result[None]:
                called_policies.append(self._name)
                return Result.success(None)

        @requires(_TrackPolicy("class-policy"))
        class _TrackCmd(Command[str]):
            pass

        ctx = self._ctx_with_principal("user-1")
        token = set_context(ctx)
        try:
            behavior = AuthorizationBehavior(policies=[_TrackPolicy("ctor-policy")])
            await behavior.handle(_TrackCmd(), _ok_next)
            assert called_policies == ["ctor-policy", "class-policy"]
        finally:
            reset_context(token)
