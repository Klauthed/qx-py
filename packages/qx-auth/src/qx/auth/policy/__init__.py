"""Policy evaluator.

The policy layer answers: "is this principal allowed to perform this action
on this resource?" Two flavors:

- ``require_permission(perm)`` — the bread-and-butter RBAC check.
- ``Policy`` — a composable predicate over (Principal, Resource) for cases
  where pure RBAC isn't enough (e.g., "user can edit a comment if they own
  it or have the ``moderate`` permission").

Decisions are ``Allow`` / ``Deny``. Deny wins on conflict — this is the
standard ABAC / Zanzibar convention; it's also less surprising for
developers reading auth code.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

from qx.core import ForbiddenError, Result

from qx.auth.jwt import Principal
from qx.auth.rbac import Permission

__all__ = [
    "Decision",
    "PolicyResult",
    "Policy",
    "require_permission",
    "require_any_permission",
    "require_all_permissions",
    "PolicyEvaluator",
]


class Decision(Enum):
    ALLOW = "allow"
    DENY = "deny"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True)
class PolicyResult:
    decision: Decision
    reason: str | None = None


@dataclass(frozen=True)
class Policy:
    """A named policy with an async predicate."""

    name: str
    rule: Callable[[Principal, Any], Awaitable[PolicyResult]]


def require_permission(permission: str) -> Policy:
    """Policy that allows iff the principal has the named permission."""
    perm = Permission(name=permission)

    async def _rule(p: Principal, _resource: Any) -> PolicyResult:
        if perm.matches(p.permissions):
            return PolicyResult(Decision.ALLOW)
        return PolicyResult(
            Decision.DENY, reason=f"missing required permission: {permission}"
        )

    return Policy(name=f"require_permission({permission})", rule=_rule)


def require_any_permission(*permissions: str) -> Policy:
    perms = [Permission(name=p) for p in permissions]

    async def _rule(p: Principal, _resource: Any) -> PolicyResult:
        if any(perm.matches(p.permissions) for perm in perms):
            return PolicyResult(Decision.ALLOW)
        return PolicyResult(
            Decision.DENY,
            reason=f"missing any of: {', '.join(permissions)}",
        )

    return Policy(name=f"require_any({permissions})", rule=_rule)


def require_all_permissions(*permissions: str) -> Policy:
    perms = [Permission(name=p) for p in permissions]

    async def _rule(p: Principal, _resource: Any) -> PolicyResult:
        missing = [perm.name for perm in perms if not perm.matches(p.permissions)]
        if missing:
            return PolicyResult(
                Decision.DENY,
                reason=f"missing required permissions: {', '.join(missing)}",
            )
        return PolicyResult(Decision.ALLOW)

    return Policy(name=f"require_all({permissions})", rule=_rule)


class PolicyEvaluator:
    """Compose policies and evaluate them against a principal."""

    def __init__(self, *policies: Policy) -> None:
        self._policies = policies

    async def evaluate(
        self,
        principal: Principal,
        resource: Any = None,
    ) -> Result[None]:
        """Return success if all policies allow; failure otherwise.

        Deny semantics: as soon as any policy returns DENY, we short-circuit
        with that reason. NOT_APPLICABLE policies are skipped (a policy that
        doesn't have an opinion on this principal is treated as silent).
        """
        for policy in self._policies:
            outcome = await policy.rule(principal, resource)
            if outcome.decision is Decision.DENY:
                return Result.failure(
                    ForbiddenError(
                        code="authz.denied",
                        message=outcome.reason or f"denied by {policy.name}",
                        details={"policy": policy.name},
                    )
                )
        return Result.success(None)
