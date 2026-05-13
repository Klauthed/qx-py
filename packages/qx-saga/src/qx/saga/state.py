"""SagaState — user-defined mutable state persisted as JSONB."""

from __future__ import annotations

from pydantic import BaseModel


class SagaState(BaseModel):
    """Base class for saga state objects.

    Subclass and add fields for each piece of data the saga needs to carry
    between event steps::

        class OrderFulfillmentState(SagaState):
            order_id: str = ""
            reservation_id: str = ""
            payment_id: str = ""

    The manager serialises state to JSONB via ``model_dump(mode="json")``
    and deserialises via ``model_validate``. All fields must be
    JSON-serialisable. Use ``str`` for UUIDs.

    State is mutable — handlers update it in place and the manager persists
    the new value after each successful handler run.
    """

    model_config = {"extra": "allow"}
