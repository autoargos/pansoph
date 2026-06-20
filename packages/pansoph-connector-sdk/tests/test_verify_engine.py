"""Tests for the conformance engine orchestrator."""

from __future__ import annotations

import json
from pathlib import Path

from pansoph_connector_sdk import InMemoryConnector, run_conformance
from pansoph_types import AccessControl


async def test_run_conformance_for_memory(tmp_path: Path) -> None:
    (tmp_path / "fixtures" / "permissions").mkdir(parents=True)
    (tmp_path / "fixtures" / "permissions" / "principals.json").write_text(
        json.dumps([{"user_id": "alice", "groups": ["eng"]}])
    )
    (tmp_path / "fixtures" / "permissions" / "expected.json").write_text(
        json.dumps({"doc1": {"public": True, "allow_groups": [], "allow_users": [], "deny": []}})
    )
    (tmp_path / "fixtures" / "permissions" / "source.json").write_text("{}")
    (tmp_path / "conformance.yaml").write_text(
        "connector: memory\n"
        "categories:\n"
        "  spec: {}\n"
        "  connection: { expect_ok: true }\n"
        "  discovery: {}\n"
        "  incremental: { enabled: true }\n"
        "  permissions:\n"
        "    source_acl: fixtures/permissions/source.json\n"
        "    principals: fixtures/permissions/principals.json\n"
        "    expected: fixtures/permissions/expected.json\n"
    )

    def factory(instance_id: str, config: dict) -> InMemoryConnector:
        c = InMemoryConnector(instance_id=instance_id)
        c.put("doc1", "hello", AccessControl(public=True))
        return c

    result = await run_conformance(tmp_path / "conformance.yaml", factory)
    assert result.passed
    assert "spec" in result.ran and "permissions" in result.ran
