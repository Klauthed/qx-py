# qx-flags

Feature flag evaluation for Qx services via OpenFeature. Automatically injects
tenant_id and user_id from `RequestContext` into every flag evaluation.

## Usage

```python
from qx.flags import FlagClient, InMemoryFlag, InMemoryProvider

# Configure once at startup (swap for Unleash/FlagD in production)
FlagClient.configure(InMemoryProvider({
    "new-checkout": InMemoryFlag("on", {"on": True, "off": False}),
}))

# Evaluate anywhere — context is injected automatically
flags = FlagClient()

if await flags.bool("new-checkout", default=False):
    return await new_checkout(order)
```
