# qx-projections

Projection runner for Qx services. Reads from the `qx_aggregate_events` event log and drives
incremental or full-rebuild updates of read models.

## Usage

```python
from qx.projections import Projection, ProjectionRunner, include_projection_tables

class OrderCountProjection(Projection):
    name = "order_count"

    async def on_orderplaced(self, ev: OrderPlaced) -> None:
        await self._session.execute(
            "INSERT INTO order_counts (id, count) VALUES (:id, 1) "
            "ON CONFLICT (id) DO UPDATE SET count = count + 1",
            {"id": ev.tenant_id},
        )

runner = ProjectionRunner(checkpoints_table, events_table)
runner.register(OrderCountProjection())

# Incremental (call periodically)
async with session_factory() as session:
    await runner.run_once(session)

# Full rebuild
async with session_factory() as session:
    await runner.rebuild(session, projection_name="order_count")
```
