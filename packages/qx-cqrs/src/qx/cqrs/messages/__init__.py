"""Command and Query base types.

Commands mutate state and return a ``Result[TResult]``. Queries read state and
return a ``Result[TResult]``. The CQRS distinction matters more than people
think: it lets the framework apply different cross-cutting concerns to each
flavor (commands get transactional outbox + idempotency; queries get read
replicas + caching).

Events live in ``qx-core`` because the entity layer needs to emit them
without depending on the mediator package.

The generic parameter ``TResult`` lets type-based handler discovery introspect
``__orig_bases__`` on the request to find the bound result type.
"""

from __future__ import annotations

from typing import Any, ClassVar, Generic, TypeVar

from pydantic import BaseModel, ConfigDict

__all__ = ["Command", "Query", "Request", "TResult"]

TResult = TypeVar("TResult")


class _MessageBase(BaseModel, Generic[TResult]):
    """Common base for commands and queries.

    Frozen because immutability eliminates a class of bugs (handler mutates the
    incoming command and the next pipeline step sees a different object). Use
    ``model_copy(update={...})`` if you genuinely need a derived form.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(
        frozen=True,
        extra="forbid",
        arbitrary_types_allowed=True,
    )

    def envelope(self) -> dict[str, Any]:
        """Serialization envelope for logging/tracing. Override for custom redaction."""
        return self.model_dump(mode="json")


# Distinguishing types is intentional even though the bodies look identical.
# (a) Type-based dispatch needs to tell them apart; (b) pipeline behaviors
# branch on Command vs Query (transactions, caching, etc.); (c) anyone reading
# the code instantly knows whether a unit of work mutates state.


class Command(_MessageBase[TResult]):
    """A request that mutates state. Handlers return ``Result[TResult]``."""


class Query(_MessageBase[TResult]):
    """A request that reads state. Handlers return ``Result[TResult]``."""


# Convenience alias for code that wants to talk about either.
Request = Command | Query
