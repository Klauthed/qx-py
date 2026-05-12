# qx-di

Async dependency-injection container for the Qx framework. Supports `SINGLETON`, `SCOPED`, and `TRANSIENT` lifetimes with full async lifecycle, generic-aware resolution, and decorator-based registration.

## What lives here

- **`qx.di.Container`** — resolves, caches, and scopes dependencies. Supports `register_singleton`, `register_scoped`, `register_transient`, `register_instance`, and `override`.
- **`qx.di.Scope`** — per-request child scope. Scoped registrations live exactly as long as their scope.
- **`qx.di.Lifetime`** — `SINGLETON` / `SCOPED` / `TRANSIENT` enum.
- **`qx.di.singleton` / `scoped` / `transient`** — class decorators that embed registration metadata without touching the class interface.
- **`qx.di.scan`** — discover and register all decorated classes in a package tree.
- **`qx.di.injectable`** — low-level decorator for explicit key/lifetime/factory overrides.

## Usage

```python
from qx.di import Container, Scope, scoped, singleton, scan

@singleton()
class Database:
    def __init__(self, url: str) -> None: ...

@scoped(key=UserRepository)
class PgUserRepository(UserRepository):
    def __init__(self, db: Database) -> None: ...

container = Container()
scan(container, "myapp.infrastructure")
container.register_instance(str, "postgresql+asyncpg://...")

async with container.scope() as scope:
    repo = await container.resolve(UserRepository, scope=scope)
```

## Design rules

- **Annotation-driven** — `_call_factory` inspects `get_type_hints()` to resolve constructor parameters. No manual wiring for the common case.
- **Scope propagation** — pass the active `Scope` into `mediator.send()` and it threads through all transient factory resolutions so that `SCOPED` dependencies (e.g. `UnitOfWork`) are reachable from handlers.
- **Cycle detection** — circular dependencies raise `ResolutionError` at first resolution, not at registration time.
- **Override** — `container.override(key, instance)` replaces any registration; used in tests to swap real infrastructure for fakes.
- No reflection at module-import time. `scan()` defers all class inspection to the first `resolve()` call.
