"""GetRental query + handler."""

from __future__ import annotations

from datetime import date  # noqa: TC003
from uuid import UUID  # noqa: TC003

from pydantic import BaseModel
from qx.core import NotFoundError, Result
from qx.cqrs import Query, query_handler
from qx.db import SessionFactory  # noqa: TC002

from rental_service.infrastructure.rental import RentalRepository


class RentalDto(BaseModel):
    rental_id: UUID
    user_id: UUID
    house_id: UUID
    check_in: date
    check_out: date
    total_cents: int
    status: str


class GetRentalQuery(Query[RentalDto]):
    rental_id: UUID


@query_handler(GetRentalQuery)
class GetRentalHandler:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._sf = session_factory

    async def handle(self, query: GetRentalQuery) -> Result[RentalDto]:
        async with self._sf() as session:
            repo = RentalRepository(session)
            result = await repo.get(query.rental_id)
            if result.is_failure:
                return Result.failure(
                    NotFoundError(
                        code="rental.not_found",
                        message=f"rental {query.rental_id} not found",
                    )
                )
        rental = result.value
        return Result.success(
            RentalDto(
                rental_id=rental.id.value,
                user_id=rental.user_id,
                house_id=rental.house_id,
                check_in=rental.check_in,
                check_out=rental.check_out,
                total_cents=rental.total_cents,
                status=rental.status,
            )
        )
