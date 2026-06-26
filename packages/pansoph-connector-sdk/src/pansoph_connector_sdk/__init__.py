"""pansoph connector SDK: one contract, three tiers, many instances."""

from __future__ import annotations

from .auth import CredentialProvider
from .auth_types import (
    AccessToken,
    AuthError,
    AuthRequirement,
    GrantType,
    ReauthRequired,
    SecretRef,
    SecretStore,
    TokenRefreshError,
    TokenSource,
)
from .conformance import assert_conforms
from .contract import (
    ChangeEvent,
    Connector,
    ConnectorMeta,
    ValidationResult,
)
from .memory import InMemoryConnector
from .verify.engine import ConformanceResult, run_conformance

__all__ = [
    "Connector",
    "ConnectorMeta",
    "ValidationResult",
    "ChangeEvent",
    "InMemoryConnector",
    "assert_conforms",
    "run_conformance",
    "ConformanceResult",
    # Auth subsystem (auto-refresh credentials).
    "AccessToken",
    "AuthError",
    "AuthRequirement",
    "CredentialProvider",
    "GrantType",
    "ReauthRequired",
    "SecretRef",
    "SecretStore",
    "TokenRefreshError",
    "TokenSource",
]
