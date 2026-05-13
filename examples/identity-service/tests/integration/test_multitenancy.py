"""Multi-tenancy E2E integration tests.

Covers both isolation strategies supported by qx-db:

**Row-level security (RLS)**
    Single shared table; Postgres enforces a per-row tenant filter via the
    ``app.current_tenant_id`` GUC.  ``open_rls_session`` sets the GUC;
    ``FORCE ROW LEVEL SECURITY`` ensures the policy applies even to the
    table owner.  Tests switch to a non-superuser role via ``SET LOCAL ROLE``
    so Postgres actually enforces the policy (superusers bypass RLS by default).

**Schema-per-tenant**
    Each tenant gets a dedicated Postgres schema created by
    ``TenantSchemaManager.provision()``.  ``open_schema_session`` sets
    ``search_path`` to the tenant schema; unqualified table references resolve
    there, so repositories need no modification.

Both suites run against a real Postgres container via testcontainers. The
``pg``, ``db_url``, and ``engine`` fixtures are session-scoped in conftest.py.

Transaction note: both ``open_rls_session`` and ``open_schema_session`` execute
a statement inside the context (``set_config`` / ``SET search_path``), which
triggers SQLAlchemy's autobegin and starts an implicit transaction.  Do NOT call
``sess.begin()`` inside these contexts — use the session directly, and call
``await sess.commit()`` before scope exit when writes need to be persisted.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from identity_service.domain.aggregates.user import User
from identity_service.infrastructure import metadata as service_metadata
from identity_service.infrastructure.persistence.user.mapping import users_table
from identity_service.infrastructure.persistence.user.repository import UserRepository
from qx.core import Identifier, RequestContext, request_scope, utcnow
from qx.db import (
    RlsPolicyManager,
    TenantSchemaManager,
    make_session_factory,
    open_rls_session,
    open_schema_session,
)
from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_RLS_ROLE = "qx_rls_test_role"


def _user_row(*, email: str, tenant_id: object) -> dict:
    return {
        "id": uuid4(),
        "email": email,
        "name": email.split("@", maxsplit=1)[0].capitalize(),
        "is_active": True,
        "version": 1,
        "tenant_id": tenant_id,
        "created_at": utcnow(),
    }


# ---------------------------------------------------------------------------
# Module-scoped fixtures: RLS role + policy
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def rls_role(engine: AsyncEngine) -> None:
    """Create a non-superuser Postgres role used by RLS enforcement tests.

    ``FORCE ROW LEVEL SECURITY`` applies to the table owner but Postgres still
    skips RLS for superusers.  Switching to this non-superuser role inside a
    transaction (``SET LOCAL ROLE``) makes the policy visible in tests.
    """

    async def _setup() -> None:
        async with engine.begin() as conn:
            await conn.execute(text(f"DROP ROLE IF EXISTS {_RLS_ROLE}"))
            await conn.execute(text(f"CREATE ROLE {_RLS_ROLE}"))
            await conn.execute(
                text(
                    f"GRANT SELECT, INSERT, UPDATE, DELETE"
                    f" ON ALL TABLES IN SCHEMA public TO {_RLS_ROLE}"
                )
            )

    async def _teardown() -> None:
        async with engine.begin() as conn:
            # Revoke all granted privileges before dropping — Postgres rejects
            # DROP ROLE when privilege dependencies exist on the role.
            await conn.execute(
                text(f"REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM {_RLS_ROLE}")
            )
            await conn.execute(text(f"DROP ROLE IF EXISTS {_RLS_ROLE}"))

    asyncio.run(_setup())
    yield
    asyncio.run(_teardown())


@pytest.fixture(scope="module")
def rls_policy(engine: AsyncEngine, rls_role: None) -> None:
    """Enable ``FORCE ROW LEVEL SECURITY`` on the users table for this module."""

    async def _enable() -> None:
        async with engine.begin() as conn:
            await RlsPolicyManager().enable(conn, "users")

    async def _disable() -> None:
        async with engine.begin() as conn:
            await RlsPolicyManager().disable(conn, "users")

    asyncio.run(_enable())
    yield
    asyncio.run(_disable())


# ---------------------------------------------------------------------------
# RLS — policy lifecycle
# ---------------------------------------------------------------------------


def test_rls_policy_visible_in_pg_policies(engine: AsyncEngine, rls_policy: None) -> None:
    """RlsPolicyManager.enable creates the expected row in pg_policies."""

    async def _check() -> None:
        async with engine.connect() as conn:
            row = await conn.execute(
                text(
                    "SELECT policyname FROM pg_policies"
                    " WHERE tablename = 'users' AND policyname = 'qx_tenant_users'"
                )
            )
            assert row.first() is not None, "expected policy 'qx_tenant_users' in pg_policies"

    asyncio.run(_check())


def test_rls_policy_enable_is_idempotent(engine: AsyncEngine, rls_policy: None) -> None:
    """Calling enable() a second time does not raise."""

    async def _reapply() -> None:
        async with engine.begin() as conn:
            await RlsPolicyManager().enable(conn, "users")

    asyncio.run(_reapply())


# ---------------------------------------------------------------------------
# RLS — session GUC
# ---------------------------------------------------------------------------


def test_rls_open_session_sets_guc(engine: AsyncEngine) -> None:
    """open_rls_session sets app.current_tenant_id in the active transaction."""
    tid = uuid4()
    sf = make_session_factory(engine)

    async def _check() -> None:
        async with open_rls_session(sf, tid) as sess:
            row = await sess.execute(text("SELECT current_setting('app.current_tenant_id', true)"))
            assert row.scalar() == str(tid)

    asyncio.run(_check())


def test_rls_guc_does_not_leak_across_sessions(engine: AsyncEngine) -> None:
    """The GUC set by one session is not visible after the session closes."""
    tid = uuid4()
    sf = make_session_factory(engine)

    async def _check() -> None:
        async with open_rls_session(sf, tid) as _:
            pass  # context exits → session.close() → transaction rolled back

        # A fresh plain connection should have no tenant GUC
        async with engine.connect() as conn:
            val = (
                await conn.execute(text("SELECT current_setting('app.current_tenant_id', true)"))
            ).scalar()
        assert val in (None, ""), f"GUC leaked to next connection: {val!r}"

    asyncio.run(_check())


# ---------------------------------------------------------------------------
# RLS — tenant isolation (enforced by Postgres, not the Python layer)
# ---------------------------------------------------------------------------


def test_rls_enforces_row_isolation(engine: AsyncEngine, rls_policy: None) -> None:
    """Postgres RLS: tenant A cannot read tenant B's rows in a bare SELECT.

    ``SET LOCAL ROLE`` switches to the non-superuser role so the policy is
    enforced (superusers bypass RLS even with FORCE ROW LEVEL SECURITY).
    Both the GUC setting and the role switch are transaction-local and
    automatically reverted when the session closes.
    """
    tenant_a, tenant_b = uuid4(), uuid4()
    sf = make_session_factory(engine)

    async def _run() -> None:
        # Insert one user per tenant as superuser (bypasses RLS — safe for setup)
        async with engine.begin() as conn:
            await conn.execute(
                users_table.insert().values(_user_row(email="alice@a.com", tenant_id=tenant_a))
            )
            await conn.execute(
                users_table.insert().values(_user_row(email="bob@b.com", tenant_id=tenant_b))
            )

        # open_rls_session already started an implicit transaction via set_config;
        # do NOT call sess.begin() here — just execute within the active transaction.

        async with open_rls_session(sf, tenant_a) as sess:
            await sess.execute(text(f"SET LOCAL ROLE {_RLS_ROLE}"))
            emails_a = {r[0] for r in (await sess.execute(text("SELECT email FROM users"))).all()}

        async with open_rls_session(sf, tenant_b) as sess:
            await sess.execute(text(f"SET LOCAL ROLE {_RLS_ROLE}"))
            emails_b = {r[0] for r in (await sess.execute(text("SELECT email FROM users"))).all()}

        assert "alice@a.com" in emails_a, "Tenant A cannot see its own row"
        assert "bob@b.com" not in emails_a, "Tenant A must NOT see Tenant B's row"
        assert "bob@b.com" in emails_b, "Tenant B cannot see its own row"
        assert "alice@a.com" not in emails_b, "Tenant B must NOT see Tenant A's row"

    asyncio.run(_run())


def test_rls_repository_stamps_and_scopes_by_tenant(engine: AsyncEngine, rls_policy: None) -> None:
    """Repository.add() stamps tenant_id from RequestContext; the written row
    is visible in the same tenant's RLS session and invisible in another's."""
    tenant_a, tenant_b = uuid4(), uuid4()
    sf = make_session_factory(engine)
    new_user = User(id=Identifier.new(), email="carol@tenanta.com", name="Carol", is_active=True)

    async def _run() -> None:
        # Write inside tenant A's context — repo stamps tenant_id automatically
        async with open_rls_session(sf, tenant_a) as sess:
            with request_scope(RequestContext(tenant_id=tenant_a)):
                repo = UserRepository(sess)
                await repo.add(new_user)
            await sess.commit()  # persist before the session closes

        # Tenant A can read Carol back (via RLS-enforced session)
        async with open_rls_session(sf, tenant_a) as sess:
            await sess.execute(text(f"SET LOCAL ROLE {_RLS_ROLE}"))
            rows_a = (
                await sess.execute(
                    text("SELECT email FROM users WHERE email = 'carol@tenanta.com'")
                )
            ).all()

        # Tenant B cannot see Carol
        async with open_rls_session(sf, tenant_b) as sess:
            await sess.execute(text(f"SET LOCAL ROLE {_RLS_ROLE}"))
            rows_b = (
                await sess.execute(
                    text("SELECT email FROM users WHERE email = 'carol@tenanta.com'")
                )
            ).all()

        assert len(rows_a) == 1, "Tenant A should see its own user"
        assert len(rows_b) == 0, "Tenant B must NOT see Tenant A's user"

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Schema-per-tenant — provisioning
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def schema_manager(engine: AsyncEngine) -> TenantSchemaManager:
    return TenantSchemaManager(engine, service_metadata)


def test_schema_provision_creates_schema_and_tables(
    engine: AsyncEngine, schema_manager: TenantSchemaManager
) -> None:
    """provision() creates the schema and materialises all tables from metadata."""
    tenant_id = uuid4()

    async def _run() -> None:
        await schema_manager.provision(tenant_id)
        schema = schema_manager.schema_name(tenant_id)
        try:
            async with engine.connect() as conn:
                # Schema must exist
                row = await conn.execute(
                    text(
                        "SELECT schema_name FROM information_schema.schemata"
                        " WHERE schema_name = :name"
                    ),
                    {"name": schema},
                )
                assert row.first() is not None, f"schema {schema!r} not found"

                # users table must exist inside the tenant schema
                row = await conn.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables"
                        " WHERE table_schema = :schema AND table_name = 'users'"
                    ),
                    {"schema": schema},
                )
                assert row.first() is not None, "users table not found in tenant schema"
        finally:
            await schema_manager.drop(tenant_id)

    asyncio.run(_run())


def test_schema_provision_is_idempotent(
    engine: AsyncEngine, schema_manager: TenantSchemaManager
) -> None:
    """Calling provision() a second time on the same tenant does not raise."""
    tenant_id = uuid4()

    async def _run() -> None:
        try:
            await schema_manager.provision(tenant_id)
            await schema_manager.provision(tenant_id)  # must not raise
        finally:
            await schema_manager.drop(tenant_id)

    asyncio.run(_run())


def test_schema_list_returns_provisioned_tenants(
    engine: AsyncEngine, schema_manager: TenantSchemaManager
) -> None:
    """list_schemas() returns a name for every provisioned tenant."""
    t1, t2 = uuid4(), uuid4()

    async def _run() -> None:
        try:
            await schema_manager.provision(t1)
            await schema_manager.provision(t2)
            schemas = await schema_manager.list_schemas()
            assert schema_manager.schema_name(t1) in schemas
            assert schema_manager.schema_name(t2) in schemas
        finally:
            await schema_manager.drop(t1)
            await schema_manager.drop(t2)

    asyncio.run(_run())


def test_schema_drop_removes_schema(
    engine: AsyncEngine, schema_manager: TenantSchemaManager
) -> None:
    """drop() removes the tenant schema; list_schemas() no longer returns it."""
    tenant_id = uuid4()

    async def _run() -> None:
        await schema_manager.provision(tenant_id)
        await schema_manager.drop(tenant_id)
        schemas = await schema_manager.list_schemas()
        assert schema_manager.schema_name(tenant_id) not in schemas

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Schema-per-tenant — isolation
# ---------------------------------------------------------------------------


def test_schema_isolates_tenants(engine: AsyncEngine, schema_manager: TenantSchemaManager) -> None:
    """UserRepository in tenant A's schema cannot see tenant B's rows.

    open_schema_session executes ``SET search_path`` which triggers autobegin;
    do NOT call sess.begin() inside the context — commit explicitly when needed.
    """
    tenant_a, tenant_b = uuid4(), uuid4()
    sf = make_session_factory(engine)

    async def _run() -> None:
        await schema_manager.provision(tenant_a)
        await schema_manager.provision(tenant_b)
        try:
            # Write a user into tenant A's schema
            async with open_schema_session(sf, tenant_a) as sess:
                await sess.execute(
                    users_table.insert().values(
                        _user_row(email="dana@schema-a.com", tenant_id=tenant_a)
                    )
                )
                await sess.commit()

            # Write a different user into tenant B's schema
            async with open_schema_session(sf, tenant_b) as sess:
                await sess.execute(
                    users_table.insert().values(
                        _user_row(email="evan@schema-b.com", tenant_id=tenant_b)
                    )
                )
                await sess.commit()

            # Tenant A sees only Dana
            async with open_schema_session(sf, tenant_a) as sess:
                emails_a = {
                    r[0] for r in (await sess.execute(text("SELECT email FROM users"))).all()
                }

            # Tenant B sees only Evan
            async with open_schema_session(sf, tenant_b) as sess:
                emails_b = {
                    r[0] for r in (await sess.execute(text("SELECT email FROM users"))).all()
                }

            assert "dana@schema-a.com" in emails_a
            assert "evan@schema-b.com" not in emails_a, "Tenant A must NOT see Tenant B"
            assert "evan@schema-b.com" in emails_b
            assert "dana@schema-a.com" not in emails_b, "Tenant B must NOT see Tenant A"
        finally:
            await schema_manager.drop(tenant_a)
            await schema_manager.drop(tenant_b)

    asyncio.run(_run())


def test_schema_search_path_resets_after_session_close(
    engine: AsyncEngine, schema_manager: TenantSchemaManager
) -> None:
    """open_schema_session resets search_path to public on exit."""
    tenant_id = uuid4()
    sf = make_session_factory(engine)

    async def _run() -> None:
        await schema_manager.provision(tenant_id)
        try:
            async with open_schema_session(sf, tenant_id) as _:
                pass  # immediately exit

            async with engine.connect() as conn:
                path: str = (await conn.execute(text("SHOW search_path"))).scalar() or ""
            assert "tenant_" not in path, f"search_path leaked: {path!r}"
        finally:
            await schema_manager.drop(tenant_id)

    asyncio.run(_run())
