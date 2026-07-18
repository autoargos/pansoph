"""Authz adapter unit logic: id encoding and the no-op engine."""

from __future__ import annotations

import pytest
from pansoph_authz.adapters.noop import NoOpAuthz
from pansoph_authz.adapters.openfga import _fga_user
from pansoph_authz.adapters.spicedb import _safe_ns_id, _safe_obj
from pansoph_authz.port import Principal


def test_spicedb_safe_obj_encodes_disallowed_chars() -> None:
    # ':' (0x3a) and '.' (0x2e) are not valid SpiceDB object-id characters.
    assert _safe_obj("github:inst:README.md") == "github=3ainst=3aREADME=2emd"
    assert _safe_obj("plain_id-1") == "plain_id-1"
    # Injective: distinct ids never collide to the same encoding.
    assert _safe_obj("a:b") != _safe_obj("a.b")


def test_spicedb_ns_id_namespacing() -> None:
    # A plain namespaced principal keeps the readable ns_id shape; the wildcard is not
    # namespaced. Underscores inside a component are escaped so the join stays unambiguous.
    assert _safe_ns_id(Principal(type="user", id="alice", ns="inst")) == "inst_alice"
    assert _safe_ns_id(Principal(type="user", id="alice")) == "alice"
    assert _safe_ns_id(Principal(type="user", id="*", ns="inst")) == "*"  # wildcard not namespaced


def test_openfga_user_encoding() -> None:
    assert _fga_user(Principal(type="user", id="alice", ns="inst")) == "user:inst_alice"
    assert _fga_user(Principal(type="group", id="eng", ns="inst")) == "group:inst_eng#member"
    assert _fga_user(Principal(type="user", id="*")) == "user:*"


@pytest.mark.permission
async def test_noop_allows_everything_and_handles_empty() -> None:
    n = NoOpAuthz()
    subject = Principal(type="user", id="anyone")
    assert await n.bulk_check(subject, "read", ["a", "b"]) == {"a", "b"}
    assert await n.bulk_check(subject, "read", []) == set()
