"""A manifest-compiled connector passes the same conformance suite, incl permissions."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx
from pansoph_connector_sdk import run_conformance
from pansoph_connector_sdk.manifest import compile_manifest
from pansoph_connector_sdk.manifest.schema import ConnectorManifest

_MANIFEST = ConnectorManifest.model_validate(
    {
        "connector": "demo",
        "auth": {"type": "none"},
        "capabilities": {"permission_sync": True},
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


@pytest.mark.conformance
@pytest.mark.permission
@respx.mock
async def test_compiled_connector_conforms(tmp_path: Path) -> None:
    respx.get("https://api.demo.test/pages").mock(
        return_value=httpx.Response(200, json={"items": [{"id": "p1"}]})
    )
    respx.get("https://api.demo.test/pages/p1").mock(
        return_value=httpx.Response(
            200, json={"body": "hi", "acl": {"public": False, "groups": ["eng"]}}
        )
    )
    perms = tmp_path / "fixtures" / "permissions"
    perms.mkdir(parents=True)
    (perms / "source.json").write_text("{}")
    (perms / "principals.json").write_text(json.dumps([{"user_id": "alice", "groups": ["eng"]}]))
    (perms / "expected.json").write_text(
        json.dumps(
            {"p1": {"public": False, "allow_groups": ["eng"], "allow_users": [], "deny": []}}
        )
    )
    (tmp_path / "conformance.yaml").write_text(
        "connector: demo\n"
        "categories:\n"
        "  spec: {}\n"
        "  connection: { expect_ok: true }\n"
        "  discovery: {}\n"
        "  permissions:\n"
        "    source_acl: fixtures/permissions/source.json\n"
        "    principals: fixtures/permissions/principals.json\n"
        "    expected: fixtures/permissions/expected.json\n"
    )
    compiled = compile_manifest(_MANIFEST)
    result = await run_conformance(tmp_path / "conformance.yaml", lambda i, c: compiled(i, c))
    assert result.passed and "permissions" in result.ran
