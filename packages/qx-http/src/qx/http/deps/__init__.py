"""FastAPI dependency helpers wired to the Qx DI container.

FastAPI's ``Depends`` is independent of our container. We provide a small
bridge so handlers can take Qx-managed services without explicit wiring:

    async def create_user(
        cmd: CreateUserCommand,
        mediator: Mediator = Inject(Mediator),  # noqa: B008
    ):
        result = await mediator.send(cmd)
        return unwrap(result)

``Inject`` resolves from a per-request scope established by middleware. The
scope is closed on response, disposing any scoped instances.

``unwrap`` takes a ``Result`` and either returns the value (success) or raises
the error as an exception so the framework's exception handlers serialize a
failure envelope with the right status code.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypeVar, cast

from fastapi import Depends, FastAPI, Request

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from qx.core import Result
    from qx.di import Container, Scope

__all__ = ["Inject", "attach_container", "get_container", "scope_dep", "unwrap"]

T = TypeVar("T")


_APP_CONTAINER_ATTR = "qx_container"


def attach_container(app: FastAPI, container: Container) -> None:
    """Store the container on the app for ``Inject`` to retrieve."""
    setattr(app.state, _APP_CONTAINER_ATTR, container)


def get_container(request: Request) -> Container:
    container = getattr(request.app.state, _APP_CONTAINER_ATTR, None)
    if container is None:
        raise RuntimeError(
            "No container attached to the app. Call attach_container(app, container) at startup."
        )
    return cast("Container", container)


async def scope_dep(
    container: Container = Depends(get_container),  # noqa: B008
) -> AsyncIterator[Scope]:
    """Yield a per-request DI scope. Closed automatically on response.

    Use as a dependency::

        async def endpoint(scope: Scope = Depends(scope_dep)):  # noqa: B008
            ...
    """
    async with container.scope("http") as scope:
        yield scope


def Inject[T](key: type[T]) -> Any:  # noqa: N802 — intentional alias for FastAPI Depends style
    """Resolve ``key`` from the DI container inside the request scope.

    Usage::

        async def handler(repo: UserRepository = Inject(UserRepository)):  # noqa: B008
            ...
    """

    async def resolver(
        container: Container = Depends(get_container),  # noqa: B008
        scope: Scope = Depends(scope_dep),  # noqa: B008
    ) -> T:
        return await container.resolve(key, scope=scope)

    return Depends(resolver)


def unwrap[T](result: Result[T]) -> T:
    """Return the success value or raise the error as an exception.

    The framework's exception handlers translate the exception into a failure
    envelope with the right status code.
    """
    if result.is_failure:
        raise result.error.as_exception()
    return result.value
