"""Tests for the synthetic principal roster and the visibility resolver."""

from __future__ import annotations

from pansoph_connector_sdk.verify.principals import Principal, can_see
from pansoph_types import AccessControl


def test_public_doc_visible_to_everyone() -> None:
    acl = AccessControl(public=True)
    assert can_see(acl, Principal(user_id="anyone", groups=[]))


def test_group_member_sees_group_doc() -> None:
    acl = AccessControl(allow_groups=["eng"])
    assert can_see(acl, Principal(user_id="alice", groups=["eng"]))
    assert not can_see(acl, Principal(user_id="bob", groups=["sales"]))


def test_user_grant() -> None:
    acl = AccessControl(allow_users=["alice"])
    assert can_see(acl, Principal(user_id="alice", groups=[]))
    assert not can_see(acl, Principal(user_id="bob", groups=[]))


def test_deny_overrides_group_grant() -> None:
    acl = AccessControl(allow_groups=["eng"], deny=["alice"])
    assert not can_see(acl, Principal(user_id="alice", groups=["eng"]))
    assert can_see(acl, Principal(user_id="carol", groups=["eng"]))


def test_deny_overrides_public() -> None:
    acl = AccessControl(public=True, deny=["alice"])
    assert not can_see(acl, Principal(user_id="alice", groups=[]))
    assert can_see(acl, Principal(user_id="bob", groups=[]))
