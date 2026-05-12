"""RBAC primitives.

Permissions are dotted strings::

    organization.read
    organization.users.invite
    billing.invoice.refund

Convention:
- First segment = resource scope (organization, billing, …)
- Last segment = action (read, write, delete, invite, refund, …)
- Wildcards allowed at any segment: ``billing.*`` (anything on billing),
  ``organization.users.*`` (anything on users in an organization).

Roles are named bundles of permissions. The policy layer is permission-
centric, not role-centric — roles are user-facing labels; permissions are
what we actually check.

Matching:
- ``user.permissions`` resolves any wildcards at issue-time (JWT contains
  expanded permissions, not roles → permissions; the IdP does that). The
  matcher here just checks whether ``required`` is in the set.
- Policy code calls ``Permission.matches(required, granted_set)`` which
  handles literal + wildcard matches.

Why not a generic ABAC engine here? ABAC is great but overkill for V1; you
implement it via a custom ``Behavior`` that pulls request attributes and runs
your own rules. The RBAC primitive is the 80% case.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["Permission", "Role"]


@dataclass(frozen=True)
class Permission:
    """A required permission name."""

    name: str

    def matches(self, granted: frozenset[str]) -> bool:
        """Check if any granted permission satisfies this requirement.

        ``granted`` is the principal's expanded permission set. Wildcards in
        granted permissions (``billing.*``) match any required permission
        starting with the same prefix.
        """
        if self.name in granted:
            return True
        # Wildcard matching — walk granted set looking for ``prefix.*`` patterns.
        for g in granted:
            if g.endswith(".*"):
                prefix = g[:-2]
                if self.name == prefix or self.name.startswith(prefix + "."):
                    return True
            if g == "*":
                return True
        return False


@dataclass(frozen=True)
class Role:
    """A named bundle of permissions.

    The role-to-permissions mapping lives in the IdP (Qx itself). This
    type is a convenience for downstream code that wants to reason about role
    names — but actual authorization decisions check permissions, not roles.
    """

    name: str
    permissions: frozenset[Permission] = frozenset()
