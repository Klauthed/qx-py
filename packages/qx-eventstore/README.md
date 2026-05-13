# qx-eventstore

Event-sourced aggregates for the Qx framework. Aggregates are persisted as an immutable event
stream in Postgres, with optional periodic snapshots for efficient replay.

## Usage

```python
from dataclasses import dataclass, field
from qx.eventstore import EventSourcedAggregate, EventStore, include_eventstore_tables

class MoneyDeposited(DomainEvent):
    event_name = "account.money_deposited"
    amount: int

@dataclass
class Account(EventSourcedAggregate[str]):
    balance: int = field(default=0)

    def deposit(self, amount: int) -> None:
        self.record_event(MoneyDeposited(amount=amount))

    def apply_moneydeposited(self, ev: MoneyDeposited) -> None:
        self.balance += ev.amount
```
