"""Tests for the extended manifest schema."""

from __future__ import annotations

from pansoph_connector_sdk.manifest.schema import (
    AclMapping,
    ConnectorManifest,
    ListSpec,
    PaginatorSpec,
)


def test_paginator_defaults_to_none() -> None:
    spec = ListSpec(path="/items")
    assert spec.paginator is None


def test_paginator_link_header() -> None:
    spec = ListSpec(path="/items", paginator=PaginatorSpec(strategy="link_header"))
    assert spec.paginator is not None
    assert spec.paginator.strategy == "link_header"


def test_acl_mapping_has_deny() -> None:
    m = AclMapping.model_validate({"from": "restrictions", "deny": "$.deny[*].id"})
    assert m.from_ == "restrictions"
    assert m.deny == "$.deny[*].id"


def test_manifest_round_trips() -> None:
    m = ConnectorManifest.model_validate(
        {
            "connector": "demo",
            "auth": {"type": "api_token"},
            "capabilities": {"incremental": True, "permission_sync": True},
            "base_url": "https://api.demo.test",
            "streams": [
                {
                    "name": "pages",
                    "list": {
                        "path": "/pages",
                        "paginator": {"strategy": "page", "page_param": "page"},
                    },
                    "fetch": {"path": "/pages/{id}"},
                    "acl": {"from": "perms", "groups": "$.groups[*]", "public": "$.is_public"},
                }
            ],
        }
    )
    assert m.streams[0].list.paginator is not None
    assert m.streams[0].list.paginator.page_param == "page"
