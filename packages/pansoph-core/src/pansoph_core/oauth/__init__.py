"""The in-app OAuth "Connect a source" subsystem.

An operator connects an oauth-capable connector through a provider consent screen
instead of pasting a token. This package is the reusable, provider-agnostic core:
  - providers/base.py   the OAuthProvider seam every provider module implements.
  - providers/*.py      the four first-party providers (google, slack, atlassian,
                        github); follow-up agents complete their scope/parse work.
  - registry.py         resolve a provider by its URL slug.
  - state.py            single-use, expiring CSRF state records (Postgres or memory).
  - flow.py             authorize() -> consent URL; handle_callback() -> seal tokens.

The routes in pansoph_core.routes expose GET /api/oauth/{provider}/authorize and
GET /api/oauth/{provider}/callback over this. No em dashes anywhere.
"""

from __future__ import annotations

from .errors import (
    CodeExchangeError,
    OAuthError,
    ProviderNotConfiguredError,
    StateError,
    UnknownProviderError,
)
from .flow import OAuthFlow
from .providers.base import OAuthProvider, TokenResult
from .registry import get_provider, provider_names
from .state import InMemoryStateStore, OAuthState, PostgresStateStore, StateStore

__all__ = [
    "OAuthError",
    "UnknownProviderError",
    "ProviderNotConfiguredError",
    "StateError",
    "CodeExchangeError",
    "OAuthProvider",
    "TokenResult",
    "OAuthFlow",
    "OAuthState",
    "StateStore",
    "InMemoryStateStore",
    "PostgresStateStore",
    "get_provider",
    "provider_names",
]
