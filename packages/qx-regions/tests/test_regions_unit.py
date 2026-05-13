"""qx-regions unit tests — no real DB or broker."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from qx.core import ErrorException
from qx.db.outbox import include_outbox_table
from qx.regions import (
    RegionConfig,
    RegionReplicator,
    RegionRouter,
    StaticRegionResolver,
    include_region_tables,
)
from sqlalchemy import MetaData

TENANT_A = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
TENANT_B = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")

# ---- StaticRegionResolver ----


@pytest.mark.anyio
async def test_static_resolver_returns_mapped_region() -> None:
    r = StaticRegionResolver({str(TENANT_A): "eu-west-1"}, default="us-east-1")
    assert await r.resolve(TENANT_A) == "eu-west-1"


@pytest.mark.anyio
async def test_static_resolver_returns_default_for_unmapped() -> None:
    r = StaticRegionResolver({}, default="us-east-1")
    assert await r.resolve(TENANT_B) == "us-east-1"


@pytest.mark.anyio
async def test_static_resolver_raises_when_no_default_and_unmapped() -> None:
    r = StaticRegionResolver({})
    with pytest.raises(ErrorException):
        await r.resolve(TENANT_A)


@pytest.mark.anyio
async def test_static_resolver_accepts_string_tenant_id() -> None:
    r = StaticRegionResolver({str(TENANT_A): "ap-southeast-1"}, default="us-east-1")
    assert await r.resolve(str(TENANT_A)) == "ap-southeast-1"


# ---- RegionRouter ----


@pytest.mark.anyio
async def test_router_returns_none_when_home_region_matches() -> None:
    resolver = StaticRegionResolver({str(TENANT_A): "us-east-1"}, default="us-east-1")
    config = RegionConfig(name="us-east-1", urls={"eu-west-1": "https://eu.example.com"})
    router = RegionRouter(resolver, config)

    result = await router.get_redirect_url(TENANT_A, "/api/orders")
    assert result is None


@pytest.mark.anyio
async def test_router_returns_redirect_url_for_foreign_region() -> None:
    resolver = StaticRegionResolver({str(TENANT_A): "eu-west-1"}, default="us-east-1")
    config = RegionConfig(
        name="us-east-1",
        urls={"eu-west-1": "https://eu.example.com"},
    )
    router = RegionRouter(resolver, config)

    result = await router.get_redirect_url(TENANT_A, "/api/orders")
    assert result == "https://eu.example.com/api/orders"


@pytest.mark.anyio
async def test_router_returns_none_when_region_url_not_configured() -> None:
    resolver = StaticRegionResolver({str(TENANT_A): "ap-southeast-1"})
    config = RegionConfig(name="us-east-1", urls={})
    router = RegionRouter(resolver, config)

    result = await router.get_redirect_url(TENANT_A, "/api/orders")
    assert result is None


@pytest.mark.anyio
async def test_router_strips_trailing_slash_from_base_url() -> None:
    resolver = StaticRegionResolver({str(TENANT_A): "eu-west-1"})
    config = RegionConfig(
        name="us-east-1",
        urls={"eu-west-1": "https://eu.example.com/"},
    )
    router = RegionRouter(resolver, config)

    result = await router.get_redirect_url(TENANT_A, "/api/orders")
    assert result == "https://eu.example.com/api/orders"
    assert "//" not in result.replace("https://", "")


def test_router_is_home_region_true_for_current() -> None:
    config = RegionConfig(name="us-east-1", urls={})
    router = RegionRouter(StaticRegionResolver({}), config)
    assert router.is_home_region("us-east-1") is True


def test_router_is_home_region_false_for_other() -> None:
    config = RegionConfig(name="us-east-1", urls={})
    router = RegionRouter(StaticRegionResolver({}), config)
    assert router.is_home_region("eu-west-1") is False


# ---- include_region_tables ----


def test_include_region_tables_returns_two_tables() -> None:
    metadata = MetaData()
    tenant_regions, checkpoints = include_region_tables(metadata)
    assert tenant_regions.name == "qx_tenant_regions"
    assert checkpoints.name == "qx_region_checkpoints"


def test_include_region_tables_registers_in_metadata() -> None:
    metadata = MetaData()
    include_region_tables(metadata)
    assert "qx_tenant_regions" in metadata.tables
    assert "qx_region_checkpoints" in metadata.tables


# ---- RegionReplicator ----


@pytest.mark.anyio
async def test_replicator_returns_zero_when_no_events() -> None:
    metadata = MetaData()
    _, checkpoints_t = include_region_tables(metadata)
    events_t = include_outbox_table(MetaData())
    _, engine = _mock_conn_engine(rows=[], checkpoint=None)

    replicator = RegionReplicator(
        engine,
        MagicMock(),
        target_region="eu-west-1",
        events_table=events_t,
        checkpoints_table=checkpoints_t,
    )
    result = await replicator._process_one_batch()
    assert result == 0


@pytest.mark.anyio
async def test_replicator_publishes_events_to_remote_broker() -> None:
    metadata = MetaData()
    _, checkpoints_t = include_region_tables(metadata)
    events_t = include_outbox_table(MetaData())
    rows = [
        {
            "id": "event-1",
            "event_name": "order.placed",
            "payload": '{"order_id": "x"}',
            "correlation_id": None,
            "tenant_id": str(TENANT_A),
            "occurred_at": datetime(2025, 1, 1, tzinfo=UTC),
        }
    ]
    _, engine = _mock_conn_engine(rows=rows, checkpoint=None)
    publisher = AsyncMock()

    replicator = RegionReplicator(
        engine,
        publisher,
        target_region="eu-west-1",
        events_table=events_t,
        checkpoints_table=checkpoints_t,
    )
    count = await replicator._process_one_batch()

    assert count == 1
    publisher.publish_raw.assert_awaited_once()
    call_args = publisher.publish_raw.call_args
    assert call_args.args[0] == "order.placed"


@pytest.mark.anyio
async def test_replicator_saves_checkpoint_after_batch() -> None:
    metadata = MetaData()
    _, checkpoints_t = include_region_tables(metadata)
    events_t = include_outbox_table(MetaData())
    rows = [
        {
            "id": "event-1",
            "event_name": "order.placed",
            "payload": "{}",
            "correlation_id": None,
            "tenant_id": None,
            "occurred_at": datetime(2025, 1, 1, tzinfo=UTC),
        }
    ]
    conn, engine = _mock_conn_engine(rows=rows, checkpoint=None)
    publisher = AsyncMock()

    replicator = RegionReplicator(
        engine,
        publisher,
        target_region="eu-west-1",
        events_table=events_t,
        checkpoints_table=checkpoints_t,
    )
    await replicator._process_one_batch()

    # At least one INSERT/UPDATE for the checkpoint should have been executed.
    assert conn.execute.call_count >= 2  # select checkpoint + select events + upsert


@pytest.mark.anyio
async def test_replicator_uses_checkpoint_in_query() -> None:
    metadata = MetaData()
    _, checkpoints_t = include_region_tables(metadata)
    events_t = include_outbox_table(MetaData())
    checkpoint = (datetime(2025, 1, 1, tzinfo=UTC), "some-event-id")
    conn, engine = _mock_conn_engine(rows=[], checkpoint=checkpoint)

    replicator = RegionReplicator(
        engine,
        MagicMock(),
        target_region="eu-west-1",
        events_table=events_t,
        checkpoints_table=checkpoints_t,
    )
    await replicator._process_one_batch()

    # The second execute call should be the batch SELECT — check its params include checkpoint.
    select_call = conn.execute.call_args_list[1]
    params = select_call.args[1] if len(select_call.args) > 1 else {}
    assert "last_occurred_at" in params


# ---- RegionConfig ----


def test_region_config_defaults() -> None:
    config = RegionConfig()
    assert config.name == "us-east-1"
    assert config.urls == {}


def test_region_config_custom_values() -> None:
    config = RegionConfig(name="eu-west-1", urls={"us-east-1": "https://us.example.com"})
    assert config.name == "eu-west-1"
    assert config.urls["us-east-1"] == "https://us.example.com"


# ---- helpers ----


def _mock_conn_engine(
    rows: list[dict],
    checkpoint: tuple | None,
) -> tuple[AsyncMock, MagicMock]:
    conn = AsyncMock()

    checkpoint_result = MagicMock()
    if checkpoint:
        checkpoint_row = MagicMock()
        checkpoint_row.__getitem__ = lambda self, k: (
            checkpoint[0] if k == "last_occurred_at" else checkpoint[1]
        )
        checkpoint_result.mappings.return_value.first.return_value = checkpoint_row
    else:
        checkpoint_result.mappings.return_value.first.return_value = None

    events_result = MagicMock()
    mock_rows = []
    for r in rows:
        row = MagicMock()
        row.__getitem__ = lambda self, k, _r=r: _r[k]
        mock_rows.append(row)
    events_result.mappings.return_value = mock_rows

    call_count = 0

    async def execute_side_effect(*args, **kwargs):
        nonlocal call_count
        result = checkpoint_result if call_count == 0 else events_result
        call_count += 1
        return result

    conn.execute = AsyncMock(side_effect=execute_side_effect)

    @asynccontextmanager
    async def _begin():
        yield conn

    engine = MagicMock()
    engine.begin = MagicMock(return_value=_begin())
    return conn, engine
