"""Infrastructure layer for identity-service.

Holds the SQLAlchemy MetaData, the outbox table registration, and an import
hook that pulls in all aggregate mappings so they bind to MetaData before
alembic autogenerate runs.
"""

from __future__ import annotations

from qx.db import make_metadata
from qx.db.outbox import include_outbox_table

metadata = make_metadata()
include_outbox_table(metadata)

# Importing the persistence modules has the side effect of binding aggregates
# to the metadata. Order doesn't matter — registry.map_imperatively is
# idempotent per (class, table).
from identity_service.infrastructure.persistence import user  # noqa: E402,F401
