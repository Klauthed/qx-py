"""Time utilities.

We funnel all "now" calls through ``utcnow`` so tests can freeze time without
patching the whole world. Any code that calls ``datetime.now()`` directly is a
test-flakiness liability and should be flagged in review.
"""

from __future__ import annotations

from datetime import UTC, datetime

__all__ = ["utcnow"]


def utcnow() -> datetime:
    """Return the current time in UTC with tzinfo set.

    Always tz-aware — naive datetimes are a foot-gun for serialization and
    comparison and we refuse to emit them.
    """
    return datetime.now(UTC)
