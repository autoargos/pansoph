"""Tests for the spec, connection, and discovery categories."""

from __future__ import annotations

from pansoph_connector_sdk import InMemoryConnector
from pansoph_connector_sdk.verify.categories.contract import (
    check_connection,
    check_discovery,
    check_spec,
)
from pansoph_types import AccessControl


def _seeded() -> InMemoryConnector:
    c = InMemoryConnector(instance_id="mem")
    c.put("doc1", "hello", AccessControl(public=True))
    return c


def test_spec_passes_for_valid_meta() -> None:
    check_spec(_seeded())  # must not raise


def test_connection_passes() -> None:
    check_connection(_seeded(), config={})


async def test_discovery_collects_refs() -> None:
    refs = await check_discovery(_seeded())
    assert refs and refs[0].source_doc_id == "doc1"
    assert all(r.instance_id for r in refs)
