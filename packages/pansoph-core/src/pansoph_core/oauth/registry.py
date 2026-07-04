"""Collect the OAuth provider instances by name.

The four provider modules each export a `PROVIDER`. This module gathers them into
one lookup so the flow and the routes resolve a provider by its URL slug without
importing each module directly. A missing provider raises `UnknownProviderError`,
which the route maps to 404.

Kept deliberately simple (a static import of the four in-tree providers) rather
than entry-point discovery: these four are first-party and shipped with the core,
unlike connectors, which are pluggable. If third-party OAuth providers become a
need, this is the one place to switch to entry-point discovery.

No em dashes anywhere.
"""

from __future__ import annotations

from functools import cache

from .errors import UnknownProviderError
from .providers import atlassian, github, google, slack
from .providers.base import OAuthProvider


@cache
def _registry() -> dict[str, OAuthProvider]:
    """Build the name -> provider map once. Cached, since the set is static."""
    providers = (google.PROVIDER, slack.PROVIDER, atlassian.PROVIDER, github.PROVIDER)
    return {p.name: p for p in providers}


def get_provider(name: str) -> OAuthProvider:
    """Return the provider registered under `name`, or raise UnknownProviderError.

    The error lists the available provider names so a bad slug fails legibly, the
    same fail-fast contract the adapter registry uses.
    """
    provider = _registry().get(name)
    if provider is None:
        available = sorted(_registry())
        raise UnknownProviderError(f"no oauth provider named {name!r}; available: {available}")
    return provider


def provider_names() -> list[str]:
    """The registered provider slugs, sorted. Used by discovery and tests."""
    return sorted(_registry())
