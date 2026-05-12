# qx-grpc

gRPC server factory and interceptor suite for the Qx framework — `RequestContext` propagation, Prometheus metrics, and `Result`-to-gRPC-status mapping.

## What lives here

- **`qx.grpc.create_grpc_server`** — builds an async gRPC server with the standard Qx interceptor stack pre-installed. Accepts a list of servicer registrations and optional port configuration.
- **`qx.grpc.RequestContextInterceptor`** — extracts `request_id`, `correlation_id`, `tenant_id`, and OTel trace context from incoming gRPC metadata and populates `RequestContext` via `contextvars`, matching the behaviour of `RequestContextMiddleware` in `qx-http`.
- **`qx.grpc.MetricsInterceptor`** — records per-method request counts and durations as Prometheus metrics.
- **`qx.grpc.ExceptionInterceptor`** — catches `ErrorException` (and unexpected exceptions) and converts them to the appropriate gRPC `StatusCode` using `status_from_error`.
- **`qx.grpc.status_from_error`** — maps Qx `Error` subclasses to gRPC status codes (`NotFoundError` → `NOT_FOUND`, `ConflictError` → `ALREADY_EXISTS`, `ValidationError` → `INVALID_ARGUMENT`, etc.).
- **`qx.grpc.abort_with_error`** — convenience helper that calls `status_from_error` and raises the appropriate `grpc.aio.AbortError`.

## Usage

```python
from qx.grpc import create_grpc_server
import grpc
from myapp.grpc import user_pb2_grpc, UserServicer

server = create_grpc_server(
    port=50051,
    metrics=metrics,
    servicers=[
        (user_pb2_grpc.add_UserServiceServicer_to_server, UserServicer(container)),
    ],
)

await server.start()
await server.wait_for_termination()
```

### Servicer using the Mediator

```python
from qx.grpc import abort_with_error
from qx.cqrs import Mediator

class UserServicer(UserServiceServicer):
    def __init__(self, container: Container) -> None:
        self._mediator = container.resolve_sync(Mediator)

    async def GetUser(self, request, context):
        result = await self._mediator.send(GetUserQuery(user_id=UUID(request.user_id)))
        if not result.is_success:
            abort_with_error(context, result.error)
        return UserResponse(id=str(result.value.id), email=result.value.email)
```

## Design rules

- Interceptors mirror the HTTP middleware stack so gRPC and HTTP endpoints have identical observability and context propagation behaviour.
- `status_from_error` is the single mapping point between Qx errors and gRPC statuses — extend it for domain-specific status codes rather than mapping in individual servicers.
- This package is a V2 scope item; the API is stable but the servicer helpers are intentionally thin. Heavy gRPC-specific logic belongs in the service layer, not the framework.
