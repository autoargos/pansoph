"""Tests for the manifest runtime components."""

from __future__ import annotations

from pansoph_connector_sdk.manifest.components import build_acl, extract
from pansoph_connector_sdk.manifest.schema import AclMapping
from pansoph_types import AccessControl


def test_extract_jsonpath_list() -> None:
    payload = {"results": [{"id": "a"}, {"id": "b"}]}
    assert extract(payload, "$.results[*].id") == ["a", "b"]


def test_build_acl_public_true() -> None:
    mapping = AclMapping.model_validate({"from": "x", "public": "$.is_public"})
    acl = build_acl({"is_public": True}, mapping, principal_ns="inst")
    assert acl == AccessControl(public=True, principal_ns="inst")


def test_build_acl_groups_and_deny() -> None:
    mapping = AclMapping.model_validate(
        {"from": "x", "groups": "$.read.groups[*]", "deny": "$.blocked[*]"}
    )
    acl = build_acl(
        {"read": {"groups": ["eng", "sales"]}, "blocked": ["mallory"]},
        mapping,
        principal_ns="inst",
    )
    assert acl.allow_groups == ["eng", "sales"]
    assert acl.deny == ["mallory"]
    assert acl.public is False
