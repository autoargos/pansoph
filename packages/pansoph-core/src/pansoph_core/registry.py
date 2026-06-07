"""Plugin registry: resolve a port's configured adapter from entry points.

Adapters across all packages register under namespaced entry point groups, one
group per port (pansoph.connectors, pansoph.authz, pansoph.vectorstores,
pansoph.embedders, pansoph.rerankers, pansoph.parsers, pansoph.llms). This is how
the platform discovers community implementations without importing them directly,
following the LangChain and LlamaIndex integration pattern.
"""

from __future__ import annotations

from importlib.metadata import entry_points
from typing import Any


def load_adapter(group: str, name: str) -> Any:
    """Return the class or factory registered as `name` in entry point `group`.

    Raises a clear error listing what is available if the name is not found, so a
    misconfigured adapter fails fast and legibly.
    """
    eps = entry_points(group=group)
    for ep in eps:
        if ep.name == name:
            return ep.load()
    available = sorted(ep.name for ep in eps)
    raise LookupError(
        f"no adapter named {name!r} in group {group!r}. available: {available or 'none'}"
    )


def list_adapters(group: str) -> list[str]:
    """List the adapter names available for a port group."""
    return sorted(ep.name for ep in entry_points(group=group))
