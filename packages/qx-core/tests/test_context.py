"""Tests for RequestContext / request_scope."""

from __future__ import annotations

import asyncio
from uuid import uuid4

from qx.core import RequestContext, current_context, request_scope


def test_default_context_is_empty() -> None:
    ctx = current_context()
    # Default context has synthesized request/correlation ids but no actor
    assert ctx.user_id is None
    assert ctx.tenant_id is None


def test_request_scope_sets_and_resets() -> None:
    user = uuid4()
    outside = current_context()
    with request_scope(user_id=user):
        inside = current_context()
        assert inside.user_id == user
    after = current_context()
    assert after is outside


def test_request_scope_with_explicit_context() -> None:
    ctx = RequestContext(user_id=uuid4(), tenant_id=uuid4())
    with request_scope(ctx):
        assert current_context() is ctx


def test_nested_scopes_isolate() -> None:
    u1, u2 = uuid4(), uuid4()
    with request_scope(user_id=u1):
        assert current_context().user_id == u1
        with request_scope(user_id=u2):
            assert current_context().user_id == u2
        assert current_context().user_id == u1


async def test_async_tasks_have_isolated_contexts() -> None:
    """The single most important property: concurrent tasks don't leak context."""
    seen: dict[str, RequestContext] = {}

    async def run(label: str, user_id) -> None:
        with request_scope(user_id=user_id):
            await asyncio.sleep(0.01)  # yield, let other task run
            seen[label] = current_context()

    u1, u2 = uuid4(), uuid4()
    await asyncio.gather(run("a", u1), run("b", u2))
    assert seen["a"].user_id == u1
    assert seen["b"].user_id == u2


def test_child_context_preserves_correlation_freshens_request() -> None:
    parent = RequestContext()
    child = parent.child()
    assert child.correlation_id == parent.correlation_id
    assert child.request_id != parent.request_id


def test_with_changes_is_immutable() -> None:
    ctx = RequestContext()
    new_user = uuid4()
    ctx2 = ctx.with_changes(user_id=new_user)
    assert ctx.user_id is None
    assert ctx2.user_id == new_user
