"""pansoph connector SDK: one contract, three tiers, many instances."""

from __future__ import annotations

from .contract import (
    ChangeEvent,
    Connector,
    ConnectorMeta,
    ValidationResult,
)

__all__ = [
    "Connector",
    "ConnectorMeta",
    "ValidationResult",
    "ChangeEvent",
]
