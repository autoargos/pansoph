"""pansoph connector SDK: one contract, three tiers, many instances."""

from __future__ import annotations

from pansoph_types import Resource, ResourcePage

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
from .spec import ConfigField, ConnectorSpec, FieldKind, looks_like_credential
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
    # Connector config spec (onboarding + secret sealing).
    "ConnectorSpec",
    "ConfigField",
    "FieldKind",
    "looks_like_credential",
    # Resource discovery (connect-then-pick).
    "Resource",
    "ResourcePage",
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
