"""Flag client unit tests — uses InMemoryProvider, no external services."""

from __future__ import annotations

import pytest
from openfeature import api as of_api
from openfeature.flag_evaluation import FlagResolutionDetails, Reason
from openfeature.provider.in_memory_provider import InMemoryFlag, InMemoryProvider
from qx.core import RequestContext, set_context
from qx.flags import FlagClient


@pytest.fixture(autouse=True)
def reset_openfeature():
    """Reset OpenFeature provider state between tests."""
    yield
    of_api.clear_providers()


def _provider(*flags: tuple[str, bool]) -> InMemoryProvider:
    return InMemoryProvider(
        {
            name: InMemoryFlag("on" if val else "off", {"on": True, "off": False})
            for name, val in flags
        }
    )


# ---- bool ----


async def test_bool_returns_true_when_flag_on() -> None:
    FlagClient.configure(_provider(("my-flag", True)))
    client = FlagClient()
    result = await client.bool("my-flag", default=False)
    assert result is True


async def test_bool_returns_false_when_flag_off() -> None:
    FlagClient.configure(_provider(("my-flag", False)))
    client = FlagClient()
    result = await client.bool("my-flag", default=True)
    assert result is False


async def test_bool_returns_default_for_unknown_flag() -> None:
    FlagClient.configure(_provider())
    client = FlagClient()
    result = await client.bool("missing-flag", default=True)
    assert result is True


# ---- string ----


async def test_string_returns_variant_value() -> None:
    provider = InMemoryProvider(
        {"theme": InMemoryFlag("dark", {"dark": "dark-theme", "light": "light-theme"})}
    )
    FlagClient.configure(provider)
    client = FlagClient()
    result = await client.string("theme", default="light-theme")
    assert result == "dark-theme"


# ---- int ----


async def test_int_returns_variant_value() -> None:
    provider = InMemoryProvider({"batch-size": InMemoryFlag("large", {"small": 10, "large": 100})})
    FlagClient.configure(provider)
    client = FlagClient()
    result = await client.int("batch-size", default=10)
    assert result == 100


# ---- float ----


async def test_float_returns_variant_value() -> None:
    provider = InMemoryProvider({"sample-rate": InMemoryFlag("high", {"low": 0.1, "high": 1.0})})
    FlagClient.configure(provider)
    client = FlagClient()
    result = await client.float("sample-rate", default=0.1)
    assert result == 1.0


# ---- bool_details ----


async def test_bool_details_includes_variant() -> None:
    FlagClient.configure(_provider(("feature-x", True)))
    client = FlagClient()
    details = await client.bool_details("feature-x", default=False)
    assert details.value is True
    assert details.variant == "on"


# ---- context injection ----


async def test_context_evaluator_receives_tenant_id() -> None:
    """Flags with a context_evaluator get the tenant_id from RequestContext."""
    captured: dict = {}

    def tenant_evaluator(flag, ctx):
        captured["tenant"] = ctx.attributes.get("tenant_id") if ctx else None
        return FlagResolutionDetails(value=True, reason=Reason.TARGETING_MATCH)

    provider = InMemoryProvider(
        {
            "tenant-flag": InMemoryFlag(
                "on",
                {"on": True, "off": False},
                context_evaluator=tenant_evaluator,
            )
        }
    )
    FlagClient.configure(provider)
    client = FlagClient()

    with set_context(RequestContext(tenant_id="tenant-42")):
        await client.bool("tenant-flag", default=False)

    assert captured.get("tenant") == "tenant-42"
