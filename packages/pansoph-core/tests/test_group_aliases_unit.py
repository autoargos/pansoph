"""Unit behavior of the identity-group alias expansion and the admin routes.

expand_groups is the pure core of stream 1 (identity materialization): it turns a user's
IdP groups into the effective set including any operator declared connector groups, and it
must never widen beyond a declared edge. The route tests confirm the admin surface is
gated. No live Postgres here (the store is exercised live against real Postgres and in the
permission suite). No em dashes anywhere.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from pansoph_core.app import create_app
from pansoph_core.group_aliases import expand_groups


def test_expand_groups_adds_only_declared_connector_groups() -> None:
    aliases = {"engineering": {"slack:member:acme", "jira:licensed"}}
    out = expand_groups(["engineering"], aliases)
    # The identity group plus every group the operator aliased it to, deduped and sorted.
    assert out == ["engineering", "jira:licensed", "slack:member:acme"]


def test_expand_groups_leaves_unmapped_group_as_itself() -> None:
    # An identity group with no alias contributes only itself, so a connector group is
    # never granted implicitly. This is the fail-closed property: no edge, no reach.
    aliases = {"engineering": {"slack:member:acme"}}
    out = expand_groups(["marketing"], aliases)
    assert out == ["marketing"]
    assert "slack:member:acme" not in out


def test_expand_groups_unions_across_multiple_identity_groups() -> None:
    aliases = {"eng": {"slack:member:a"}, "lead": {"jira:licensed"}}
    out = expand_groups(["eng", "lead"], aliases)
    assert out == ["eng", "jira:licensed", "lead", "slack:member:a"]


def test_expand_groups_empty_is_empty() -> None:
    assert expand_groups([], {"eng": {"x"}}) == []


def _client() -> TestClient:
    return TestClient(create_app())


def test_group_alias_routes_require_auth() -> None:
    # Every alias route is gated to a connector admin; an unauthenticated request is
    # rejected before any handler body, so it never touches Postgres.
    c = _client()
    assert c.get("/api/authz/group-aliases").status_code == 401
    assert c.get("/api/authz/connector-groups").status_code == 401
    assert (
        c.post(
            "/api/authz/group-aliases",
            json={"identity_group": "eng", "connector_group": "slack:member:acme"},
        ).status_code
        == 401
    )
    assert (
        c.request(
            "DELETE",
            "/api/authz/group-aliases",
            json={"identity_group": "eng", "connector_group": "slack:member:acme"},
        ).status_code
        == 401
    )
