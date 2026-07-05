"""The resource-discovery contract: a discovery-capable connector implements
list_resources and returns a ResourcePage, and assert_conforms pairs the capability
with the method exactly like deletions. Fully offline. No em dashes anywhere.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from pansoph_connector_sdk import (
    ConnectorMeta,
    InMemoryConnector,
    Resource,
    ResourcePage,
    ValidationResult,
    assert_conforms,
)
from pansoph_types import AccessControl, AuthScheme, Capabilities, DocRef, RawDoc


async def test_list_resources_returns_top_level_then_children() -> None:
    c = InMemoryConnector(instance_id="mem")

    top = await c.list_resources()
    assert isinstance(top, ResourcePage)
    assert {r.id for r in top.items} == {"folder-a", "folder-b"}
    assert all(r.kind == "folder" for r in top.items)
    assert top.next_cursor is None

    children = await c.list_resources(kind="item", parent_id="folder-a")
    assert [r.id for r in children.items] == ["item-a1"]
    assert children.items[0].parent_id == "folder-a"
    assert children.items[0].selectable is True


async def test_resource_shape_round_trips() -> None:
    r = Resource(id="r1", kind="repo", label="Repo One", parent_id="org1", extra={"private": "1"})
    assert r.selectable is True
    dumped = r.model_dump()
    assert dumped["id"] == "r1"
    assert dumped["parent_id"] == "org1"
    assert dumped["extra"] == {"private": "1"}


async def test_default_capabilities_omit_discovery() -> None:
    # The flag is additive: a connector must opt in, so nothing regresses.
    assert Capabilities().resource_discovery is False


async def test_conformance_passes_for_discovery_capable_connector() -> None:
    c = InMemoryConnector(instance_id="mem")
    c.put("a", "doc a", AccessControl(public=True))
    # InMemoryConnector declares resource_discovery and implements list_resources,
    # so the paired discovery check passes.
    await assert_conforms(c)


class _DiscoveryLiar:
    """Declares resource_discovery but does not implement list_resources: the exact
    defect the paired conformance check must catch, mirroring the deletions liar."""

    def __init__(self) -> None:
        self.instance_id = "liar"
        self._rev = 0
        self.meta = ConnectorMeta(
            name="liar",
            auth=AuthScheme.none,
            capabilities=Capabilities(resource_discovery=True),
        )

    def validate(self, config: dict[str, Any]) -> ValidationResult:
        return ValidationResult(ok=True)

    async def list_changes(self, cursor: str | None) -> AsyncIterator[tuple[DocRef, str]]:
        yield DocRef(source_doc_id="a", instance_id="liar"), "1"

    async def fetch(self, ref: DocRef) -> tuple[RawDoc, AccessControl]:
        raw = RawDoc(source_doc_id="a", instance_id="liar", mime="text/plain", text="a")
        return raw, AccessControl(public=True)

    async def list_acls(self, ref: DocRef) -> AccessControl:
        return AccessControl(public=True)


async def test_conformance_fails_when_discovery_declared_but_unimplemented() -> None:
    with pytest.raises(AssertionError, match="resource_discovery"):
        await assert_conforms(_DiscoveryLiar())
