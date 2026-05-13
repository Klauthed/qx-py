"""qx-eventstore: event-sourced aggregates with Postgres-backed event log."""

from qx.eventstore.aggregate import EventSourcedAggregate
from qx.eventstore.store import EventStore
from qx.eventstore.table import (
    AGGREGATE_EVENTS_TABLE_NAME,
    AGGREGATE_SNAPSHOTS_TABLE_NAME,
    include_eventstore_tables,
)

__all__ = [
    "AGGREGATE_EVENTS_TABLE_NAME",
    "AGGREGATE_SNAPSHOTS_TABLE_NAME",
    "EventSourcedAggregate",
    "EventStore",
    "include_eventstore_tables",
]
