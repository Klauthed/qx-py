"""FlagClient — thin OpenFeature wrapper that injects tenant/user evaluation context."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from openfeature import api as of_api
from openfeature.evaluation_context import EvaluationContext
from qx.core import current_context

if TYPE_CHECKING:
    import builtins

    from openfeature.client import OpenFeatureClient
    from openfeature.flag_evaluation import FlagEvaluationDetails

__all__ = ["FlagClient"]


class FlagClient:
    """Evaluates feature flags via OpenFeature with automatic request-context targeting.

    The client forwards the current ``RequestContext`` (tenant_id, user_id) as
    OpenFeature targeting attributes so providers like Unleash or FlagD can apply
    per-tenant or per-user targeting rules without callers needing to pass context
    manually::

        flags = FlagClient()

        if await flags.bool("new-checkout-flow", default=False):
            return await new_checkout(order)
        return await legacy_checkout(order)

    **Setup** — configure the provider once at startup::

        from openfeature.provider.in_memory_provider import InMemoryProvider
        FlagClient.configure(InMemoryProvider({"flag": InMemoryFlag("on", {"on": True})}))

    **Evaluation context** — pass extra context to narrow targeting::

        enabled = await flags.bool("beta-feature", default=False, targeting_key="user-123")
    """

    _client: OpenFeatureClient | None = None

    @classmethod
    def configure(cls, provider: Any, *, domain: str = "qx") -> None:
        """Set the OpenFeature provider. Call once at service startup."""
        of_api.set_provider(provider, domain=domain)
        cls._client = of_api.get_client(domain=domain)

    def _get_client(self) -> OpenFeatureClient:
        if self._client is None:
            self._client = of_api.get_client()
        return self._client

    def _build_context(self, targeting_key: str | None, extra: dict[str, Any]) -> EvaluationContext:
        ctx = current_context()
        attributes: dict[str, Any] = {}
        if ctx.tenant_id:
            attributes["tenant_id"] = ctx.tenant_id
        if ctx.user_id:
            attributes["user_id"] = ctx.user_id
        attributes.update(extra)

        raw_key = targeting_key or ctx.user_id or ctx.tenant_id
        key = str(raw_key) if raw_key else ""
        return EvaluationContext(targeting_key=key, attributes=attributes)

    async def bool(
        self,
        flag: str,
        *,
        default: bool,
        targeting_key: str | None = None,
        **extra: Any,
    ) -> bool:
        ctx = self._build_context(targeting_key, extra)
        return await self._get_client().get_boolean_value_async(flag, default, ctx)

    async def string(
        self,
        flag: str,
        *,
        default: str,
        targeting_key: str | None = None,
        **extra: Any,
    ) -> str:
        ctx = self._build_context(targeting_key, extra)
        return await self._get_client().get_string_value_async(flag, default, ctx)

    async def int(
        self,
        flag: str,
        *,
        default: int,
        targeting_key: str | None = None,
        **extra: Any,
    ) -> int:
        ctx = self._build_context(targeting_key, extra)
        return await self._get_client().get_integer_value_async(flag, default, ctx)

    async def float(
        self,
        flag: str,
        *,
        default: float,
        targeting_key: str | None = None,
        **extra: Any,
    ) -> float:
        ctx = self._build_context(targeting_key, extra)
        return await self._get_client().get_float_value_async(flag, default, ctx)

    async def bool_details(
        self,
        flag: str,
        *,
        default: builtins.bool,
        targeting_key: str | None = None,
        **extra: Any,
    ) -> FlagEvaluationDetails[builtins.bool]:
        ctx = self._build_context(targeting_key, extra)
        return await self._get_client().get_boolean_details_async(flag, default, ctx)
