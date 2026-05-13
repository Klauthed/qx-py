# qx-regions

Multi-region support for Qx services: tenant home-region resolution, write
routing with automatic redirect, and cross-region event replication.

## Concepts

- **Home region** — the Postgres write primary for a given tenant. All writes
  must reach the home region; reads can come from any local replica.
- **RegionResolver** — maps `tenant_id → region_name`. Use `StaticRegionResolver`
  for tests/simple deployments, `DbRegionResolver` for production.
- **RegionRouter** — given a request's tenant and path, returns a redirect URL
  when the current region is not the tenant's home region.
- **RegionReplicator** — background worker that forwards published outbox events
  to a remote region's NATS cluster (application-level fallback when JetStream
  mirroring is not configured).

## Usage

```python
from qx.regions import (
    RegionConfig,
    RegionRouter,
    StaticRegionResolver,
    RegionReplicator,
    include_region_tables,
)

# Startup
config = RegionConfig()                   # reads QX_REGION__NAME, QX_REGION__URLS
resolver = StaticRegionResolver(          # or DbRegionResolver for production
    {"tenant-abc": "eu-west-1"},
    default=config.name,
)
router = RegionRouter(resolver, config)

# Per-request — redirect writes to the home region
redirect = await router.get_redirect_url(tenant_id, request.path)
if redirect:
    return Response(status=307, headers={"Location": redirect})

# Cross-region replication worker
replicator = RegionReplicator(
    engine, remote_publisher,
    target_region="eu-west-1",
    events_table=events_t,
    checkpoints_table=checkpoints_t,
)
await replicator.run()   # blocks; call replicator.stop() from signal handler
```
