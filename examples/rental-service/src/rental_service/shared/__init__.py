"""Shared infrastructure — the one MetaData instance for this service.

Every slice's infrastructure module imports and extends this metadata:

    from rental_service.shared import metadata
    users_table = Table("users", metadata, ...)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from qx.db import make_metadata
from qx.db.outbox import include_outbox_table

if TYPE_CHECKING:
    from sqlalchemy import MetaData

metadata: MetaData = make_metadata()
include_outbox_table(metadata)
