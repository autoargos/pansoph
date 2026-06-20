"""Tests for the read category."""

from __future__ import annotations

from pansoph_connector_sdk import InMemoryConnector
from pansoph_connector_sdk.verify.categories.contract import check_discovery
from pansoph_connector_sdk.verify.categories.read import check_read, normalize_record
from pansoph_types import AccessControl, RawDoc


async def test_read_returns_normalized_records() -> None:
    c = InMemoryConnector(instance_id="mem")
    c.put("doc1", "hello world", AccessControl(public=True))
    refs = await check_discovery(c)
    records = await check_read(c, refs)
    assert records == [{"source_doc_id": "doc1", "mime": "text/plain", "text": "hello world"}]


def test_normalize_strips_volatile_fields() -> None:
    raw = RawDoc(
        source_doc_id="d", instance_id="i", mime="text/plain", text="x", source_url="http://h"
    )
    rec = normalize_record(raw)
    assert "instance_id" not in rec and "source_url" not in rec
    assert rec == {"source_doc_id": "d", "mime": "text/plain", "text": "x"}
