"""House infrastructure — SQLAlchemy table + repository."""

from __future__ import annotations

from qx.db import Repository
from sqlalchemy import Boolean, Column, Integer, String, Table, Uuid

from rental_service.domain.house import House
from rental_service.shared import metadata

houses_table: Table = Table(
    "rental_houses",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("address", String(500), nullable=False),
    Column("price_per_night_cents", Integer, nullable=False),
    Column("available", Boolean, nullable=False, server_default="true"),
)


class HouseRepository(Repository[House]):
    entity_cls = House
    table = houses_table
