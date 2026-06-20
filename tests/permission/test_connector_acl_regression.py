"""Regression: an oversharing connector ACL mapping must fail the permission gate.

If this test ever passes silently (no AssertionError), the permission category has
regressed and oversharing would ship. Named so the bug cannot return unnoticed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pansoph_connector_sdk import InMemoryConnector
from pansoph_connector_sdk.verify.categories.permissions import check_permissions
from pansoph_types import AccessControl


@pytest.mark.permission
async def test_oversharing_acl_fails_the_gate(tmp_path: Path) -> None:
    c = InMemoryConnector(instance_id="mem")
    c.put("doc1", "secret", AccessControl(public=True))  # connector overshares
    (tmp_path / "s.json").write_text("{}")
    (tmp_path / "p.json").write_text(json.dumps([{"user_id": "bob", "groups": []}]))
    (tmp_path / "e.json").write_text(
        json.dumps(
            {"doc1": {"public": False, "allow_groups": ["eng"], "allow_users": [], "deny": []}}
        )
    )
    with pytest.raises(AssertionError):
        await check_permissions(
            c,
            source_acl=tmp_path / "s.json",
            principals=tmp_path / "p.json",
            expected=tmp_path / "e.json",
        )
