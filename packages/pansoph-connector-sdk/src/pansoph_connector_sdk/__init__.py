"""pansoph connector SDK: one contract, three tiers, many instances."""

from __future__ import annotations

from .conformance import assert_conforms
from .contract import (
    ChangeEvent,
    Connector,
    ConnectorMeta,
    ValidationResult,
)
from .memory import InMemoryConnector

__all__ = [
    "Connector",
    "ConnectorMeta",
    "ValidationResult",
    "ChangeEvent",
    "InMemoryConnector",
    "assert_conforms",
]
