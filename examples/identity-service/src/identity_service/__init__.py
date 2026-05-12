"""Identity service: a reference Qx service.

Demonstrates the full vertical slice — HTTP → command handler → repository →
outbox → worker handler that consumes the integration event back in.
"""

__version__ = "0.1.0"
