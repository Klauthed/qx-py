"""Worker error types for controlling NATS ack/nak behaviour."""

from __future__ import annotations

__all__ = ["PermanentWorkerError", "TransientWorkerError"]


class PermanentWorkerError(Exception):
    """Raise inside an integration-event handler to ack and discard the message.

    Use when the failure is deterministic and retrying would never succeed:
    invalid payload, unsupported schema version, violated invariant that won't
    change.  JetStream will *not* redeliver the message; it is counted as
    consumed and removed from the stream.

    If you need the message archived for manual inspection, configure a
    dead-letter subject on the NATS stream before raising this error.
    """


class TransientWorkerError(Exception):
    """Raise to explicitly signal a recoverable failure.

    The worker naks the message and lets JetStream retry up to ``max_deliver``.
    This is identical to raising any other exception — the class exists so
    handlers can be explicit about intent rather than relying on the catch-all.
    """
