"""Benchmark: Mediator command and query dispatch throughput.

Measures the pure framework overhead — handler resolution, pipeline
traversal, and result unwrapping — with a no-op handler that returns
immediately.  No database or network I/O involved.

Run with: uv run pytest tests/benchmarks/test_mediator_dispatch.py -v -s -m slow
"""

from __future__ import annotations

import pytest
from prometheus_client import CollectorRegistry
from qx.core import Result
from qx.cqrs import (
    Command,
    ExceptionTranslationBehavior,
    LoggingBehavior,
    Mediator,
    Query,
    command_handler,
    query_handler,
)
from qx.di import Container
from qx.observability import Metrics

from tests.benchmarks.conftest import measure, report

pytestmark = pytest.mark.slow

N = 10_000


# ---------------------------------------------------------------------------
# No-op domain objects
# ---------------------------------------------------------------------------


class PingCommand(Command[str]):
    pass


class PingQuery(Query[str]):
    pass


@command_handler(PingCommand)
class PingCommandHandler:
    async def handle(self, cmd: PingCommand) -> Result[str]:
        return Result.success("pong")


@query_handler(PingQuery)
class PingQueryHandler:
    async def handle(self, query: PingQuery) -> Result[str]:
        return Result.success("pong")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _build_mediator(*, behaviors: bool = False) -> Mediator:
    registry = CollectorRegistry()
    metrics = Metrics(registry=registry)
    container = Container()
    container.register_instance(Metrics, metrics)
    behaviors_tuple = (LoggingBehavior(), ExceptionTranslationBehavior()) if behaviors else ()
    mediator = Mediator(
        container,
        command_behaviors=behaviors_tuple,
        query_behaviors=behaviors_tuple,
    )
    mediator.register_command(PingCommand, PingCommandHandler)
    mediator.register_query(PingQuery, PingQueryHandler)
    return mediator


# ---------------------------------------------------------------------------
# Benchmarks
# ---------------------------------------------------------------------------


async def test_bench_command_dispatch_no_behaviors() -> None:
    mediator = _build_mediator(behaviors=False)
    cmd = PingCommand()
    ops, ms = await measure(N, lambda: mediator.send(cmd))
    report("command dispatch (no behaviors)", ops, ms, N)


async def test_bench_command_dispatch_with_behaviors() -> None:
    mediator = _build_mediator(behaviors=True)
    cmd = PingCommand()
    ops, ms = await measure(N, lambda: mediator.send(cmd))
    report("command dispatch (logging + exception translation)", ops, ms, N)


async def test_bench_query_dispatch_no_behaviors() -> None:
    mediator = _build_mediator(behaviors=False)
    query = PingQuery()
    ops, ms = await measure(N, lambda: mediator.send(query))
    report("query dispatch (no behaviors)", ops, ms, N)
