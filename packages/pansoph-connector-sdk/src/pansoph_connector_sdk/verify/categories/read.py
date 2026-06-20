"""The read category: fetch each discovered ref and normalize it for snapshotting.

normalize_record drops fields that vary between runs or hosts (instance_id, urls)
so a committed snapshot stays stable. The full snapshot comparison against an
expected_records file is done in the engine.
"""

from __future__ import annotations

from typing import Any

from pansoph_types import AccessControl, DocRef, RawDoc

from ...contract import Connector


def normalize_record(raw: RawDoc) -> dict[str, Any]:
    rec: dict[str, Any] = {"source_doc_id": raw.source_doc_id, "mime": raw.mime}
    if raw.title is not None:
        rec["title"] = raw.title
    if raw.text is not None:
        rec["text"] = raw.text
    return rec


async def check_read(connector: Connector, refs: list[DocRef]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for ref in refs:
        raw, acl = await connector.fetch(ref)
        assert isinstance(raw, RawDoc), "fetch must return a RawDoc"
        assert isinstance(acl, AccessControl), "fetch must return an AccessControl"
        assert raw.source_doc_id == ref.source_doc_id, "fetched doc id must match the ref"
        records.append(normalize_record(raw))
    return records
