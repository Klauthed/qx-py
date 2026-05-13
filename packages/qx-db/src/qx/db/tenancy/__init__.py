"""Multi-tenancy isolation strategies for qx-db.

Three strategies, pick one per service:

- **Row-Level Security** — shared schema, Postgres RLS policies enforce isolation.
  Use ``RlsPolicyManager`` to set up policies; ``open_rls_session`` for requests.

- **Schema-per-tenant** — one Postgres schema per tenant in a shared database.
  Use ``TenantSchemaManager`` to provision; ``open_schema_session`` for requests.

- **Database-per-tenant** — one Postgres database per tenant.
  Use ``TenantDatabaseManager`` to provision; ``TenantEngineRouter`` +
  ``open_db_session`` for requests.
"""

from qx.db.tenancy.database import TenantDatabaseManager, TenantEngineRouter, open_db_session
from qx.db.tenancy.manager import TenantSchemaManager
from qx.db.tenancy.rls import RlsPolicyManager, open_rls_session
from qx.db.tenancy.session import open_schema_session

__all__ = [
    "RlsPolicyManager",
    "TenantDatabaseManager",
    "TenantEngineRouter",
    "TenantSchemaManager",
    "open_db_session",
    "open_rls_session",
    "open_schema_session",
]
