"""Rental infrastructure — SQLAlchemy table + repository."""

from __future__ import annotations

from qx.db import Repository
from sqlalchemy import Column, Date, Integer, String, Table, Uuid

from rental_service.domain.rental import Rental
from rental_service.shared import metadata

rentals_table: Table = Table(
    "rental_bookings",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("user_id", Uuid, nullable=False),
    Column("house_id", Uuid, nullable=False),
    Column("check_in", Date, nullable=False),
    Column("check_out", Date, nullable=False),
    Column("total_cents", Integer, nullable=False),
    Column("status", String(20), nullable=False, server_default="pending"),
)


class RentalRepository(Repository[Rental]):
    entity_cls = Rental
    table = rentals_table
