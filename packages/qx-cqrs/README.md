# qx-cqrs

CQRS and Mediator implementation for the Qx framework. Dispatches commands, queries, domain events, integration events, and notifications through a composable pipeline of behaviors.

## What lives here

- **`qx.cqrs.Mediator`** — central dispatcher. Resolves handlers via the DI container; supports three registration modes: decorator scan, explicit `register()`, and type-based lookup.
- **`qx.cqrs.Command` / `Query`** — base Pydantic models for all messages. `Command[TResult]` mutates state; `Query[TResult]` is read-only.
- **`qx.cqrs.command_handler` / `query_handler` / `event_handler`** — decorators that bind a handler class to its message type.
- **`qx.cqrs.Behavior`** — single-method interface (`handle(request, next)`) for pipeline stages.
- **Built-in behaviors**: `LoggingBehavior`, `TracingBehavior`, `MetricsBehavior`, `IdempotencyBehavior`, `ExceptionTranslationBehavior`, `TransactionBehavior`, `ValidationBehavior`, `RetryBehavior`, `AuthorizationBehavior`.
- **`qx.cqrs.compose`** — assemble a `BehaviorChain` from an ordered list of behaviors.

## Usage

```python
from qx.cqrs import Command, Mediator, command_handler
from qx.core import Result

class CreateUserCommand(Command[UserDto]):
    email: str
    name: str

@command_handler(CreateUserCommand)
class CreateUserHandler:
    def __init__(self, repo: UserRepository, uow: UnitOfWork) -> None: ...

    async def handle(self, cmd: CreateUserCommand) -> Result[UserDto]:
        result = User.register(cmd.email, cmd.name)
        ...
        return Result.success(dto)

# Bootstrap
mediator = Mediator(container)
mediator.register_decorated("myapp.application")

# Dispatch (in a route or test)
result = await mediator.send(CreateUserCommand(email="a@b.com", name="Ada"), scope=scope)
```

## Pipeline composition

```python
from qx.cqrs import compose, LoggingBehavior, TracingBehavior, ExceptionTranslationBehavior

pipeline = compose(
    LoggingBehavior(),
    TracingBehavior(tracer),
    ExceptionTranslationBehavior(),
)
mediator = Mediator(container, pipeline=pipeline)
```

## Design rules

- Handlers return `Result[T]` — they never raise. `ExceptionTranslationBehavior` converts unexpected exceptions to `InfrastructureError` before they escape the pipeline.
- Commands and queries are plain Pydantic models; validation happens automatically before `handle()` is called.
- The mediator resolves handlers lazily per `send()` call so the DI scope for that request is used correctly.
