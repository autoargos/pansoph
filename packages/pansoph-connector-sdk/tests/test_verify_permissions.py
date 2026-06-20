"""Tests for the gated permission category."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pansoph_connector_sdk import InMemoryConnector
from pansoph_connector_sdk.verify.categories.permissions import check_permissions
from pansoph_types import AccessControl


def _write(tmp_path: Path) -> tuple[Path, Path, Path]:
    principals = tmp_path / "principals.json"
    principals.write_text(
        json.dumps(
            [
                {"user_id": "alice", "groups": ["eng"]},
                {"user_id": "bob", "groups": ["sales"]},
            ]
        )
    )
    expected = tmp_path / "expected.json"
    expected.write_text(
        json.dumps(
            {"doc1": {"public": False, "allow_groups": ["eng"], "allow_users": [], "deny": []}}
        )
    )
    source = tmp_path / "source.json"
    source.write_text("{}")
    return source, principals, expected


async def test_permissions_pass_on_correct_mapping(tmp_path: Path) -> None:
    c = InMemoryConnector(instance_id="mem")
    c.put("doc1", "secret", AccessControl(allow_groups=["eng"]))
    src, pr, exp = _write(tmp_path)
    await check_permissions(c, source_acl=src, principals=pr, expected=exp)  # must not raise


async def test_permissions_fail_on_oversharing(tmp_path: Path) -> None:
    c = InMemoryConnector(instance_id="mem")
    c.put("doc1", "secret", AccessControl(public=True))  # oversharing vs expected eng-only
    src, pr, exp = _write(tmp_path)
    with pytest.raises(AssertionError, match="set equality"):
        await check_permissions(c, source_acl=src, principals=pr, expected=exp)


async def test_permissions_fail_when_expected_deny_not_applied(tmp_path: Path) -> None:
    c = InMemoryConnector(instance_id="mem")
    c.put("doc1", "secret", AccessControl(allow_groups=["eng"]))
    src = tmp_path / "s.json"
    src.write_text("{}")
    pr = tmp_path / "p.json"
    pr.write_text(json.dumps([{"user_id": "alice", "groups": ["eng"]}]))
    exp = tmp_path / "e.json"
    # Expected claims alice is denied, but the connector grants her via group: mismatch.
    exp.write_text(
        json.dumps(
            {
                "doc1": {
                    "public": False,
                    "allow_groups": ["eng"],
                    "allow_users": [],
                    "deny": ["alice"],
                }
            }
        )
    )
    with pytest.raises(AssertionError, match="set equality"):
        await check_permissions(c, source_acl=src, principals=pr, expected=exp)
