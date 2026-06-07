"""Authz adapter unit logic: id encoding and the no-op engine."""

from __future__ import annotations

import pytest
from pansoph_authz.adapters.noop import NoOpAuthz
from pansoph_authz.adapters.openfga import _fga_user
from pansoph_authz.adapters.spicedb import _ns_id, _safe_obj
from pansoph_authz.port import Principal


def test_spicedb_safe_obj_replaces_colons() -> None:
    assert _safe_obj("github:org1:42") == "github/org1/42"
    assert _safe_obj("noColons") == "noColons"


def test_spicedb_ns_id_namespacing() -> None:
    assert _ns_id(Principal(type="user", id="alice", ns="inst")) == "inst_alice"
    assert _ns_id(Principal(type="user", id="alice")) == "alice"
    assert _ns_id(Principal(type="user", id="*", ns="inst")) == "*"  # wildcard not namespaced


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
