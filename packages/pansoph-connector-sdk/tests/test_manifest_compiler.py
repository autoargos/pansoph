"""Tests for compile_manifest, exercised against a respx-mocked REST source."""

from __future__ import annotations

import httpx
import respx
from pansoph_connector_sdk.manifest import compile_manifest
from pansoph_connector_sdk.manifest.schema import ConnectorManifest

_MANIFEST = ConnectorManifest.model_validate(
    {
        "connector": "demo",
        "auth": {"type": "none"},
        "capabilities": {"incremental": False, "permission_sync": True},
        "base_url": "https://api.demo.test",
        "streams": [
            {
                "name": "pages",
                "list": {"path": "/pages", "records": "$.items[*]", "id_field": "id"},
                "fetch": {"path": "/pages/{id}", "text": "$.body"},
                "acl": {"from": "acl", "groups": "$.acl.groups[*]", "public": "$.acl.public"},
            }
        ],
    }
)


@respx.mock
async def test_compiled_connector_lists_and_fetches() -> None:
    respx.get("https://api.demo.test/pages").mock(
        return_value=httpx.Response(200, json={"items": [{"id": "p1"}, {"id": "p2"}]})
    )
    respx.get("https://api.demo.test/pages/p1").mock(
        return_value=httpx.Response(
            200, json={"body": "hello", "acl": {"public": False, "groups": ["eng"]}}
        )
    )
    compiled = compile_manifest(_MANIFEST)
    conn = compiled(instance_id="demo", config={})
    refs = [ref async for ref, _ in conn.list_changes(None)]
    assert [r.source_doc_id for r in refs] == ["p1", "p2"]
    raw, acl = await conn.fetch(refs[0])
    assert raw.text == "hello"
    assert acl.allow_groups == ["eng"] and acl.public is False
    assert conn.meta.name == "demo"
