"""The contract categories: spec, connection, discovery.

These exercise the parts of the Connector protocol that need no fixtures beyond a
seeded or reachable connector. discovery returns the collected DocRefs so later
categories (read, incremental) can reuse them.
"""

from __future__ import annotations

from typing import Any

from pansoph_types import DocRef

from ...contract import Connector, ValidationResult


def check_spec(connector: Connector) -> None:
    meta = connector.meta
    assert meta.name, "connector meta must have a name"
    assert meta.version, "connector meta must have a version"
    assert meta.auth is not None, "connector meta must declare an auth scheme"


def check_connection(connector: Connector, config: dict[str, Any]) -> None:
    vr = connector.validate(config)
    assert isinstance(vr, ValidationResult), "validate must return a ValidationResult"
    assert vr.ok, f"connection check failed: {vr.error}"


async def check_discovery(connector: Connector) -> list[DocRef]:
    refs: list[DocRef] = []
    async for ref, cursor in connector.list_changes(None):
        assert isinstance(ref, DocRef), "list_changes must yield DocRef"
        assert isinstance(cursor, str), "list_changes must yield a string cursor"
        assert ref.instance_id, "every DocRef must carry its instance_id for isolation"
        refs.append(ref)
    assert refs, "seed the connector before conformance: list_changes returned nothing"
    return refs
