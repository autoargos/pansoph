"""Built-in identity store: nested group resolution, the part easy to get wrong."""

from __future__ import annotations

from pansoph_identity.adapters.builtin import BuiltinIdentity
from pansoph_identity.port import Group, User


def _store() -> BuiltinIdentity:
    idp = BuiltinIdentity()
    return idp


async def test_direct_membership() -> None:
    idp = _store()
    idp.add_group(Group(id="eng"))
    idp.add_user(User(id="alice", group_ids=["eng"]))
    assert await idp.groups_for_user("alice") == {"eng"}


async def test_nested_membership_is_transitive() -> None:
    idp = _store()
    idp.add_group(Group(id="company"))
    idp.add_group(Group(id="eng", parent_ids=["company"]))
    idp.add_group(Group(id="backend", parent_ids=["eng"]))
    idp.add_user(User(id="alice", group_ids=["backend"]))
    assert await idp.groups_for_user("alice") == {"backend", "eng", "company"}


async def test_diamond_membership_dedupes() -> None:
    idp = _store()
    idp.add_group(Group(id="all"))
    idp.add_group(Group(id="a", parent_ids=["all"]))
    idp.add_group(Group(id="b", parent_ids=["all"]))
    idp.add_user(User(id="alice", group_ids=["a", "b"]))
    assert await idp.groups_for_user("alice") == {"a", "b", "all"}


async def test_cycle_is_safe() -> None:
    idp = _store()
    idp.add_group(Group(id="g1", parent_ids=["g2"]))
    idp.add_group(Group(id="g2", parent_ids=["g1"]))
    idp.add_user(User(id="alice", group_ids=["g1"]))
    assert await idp.groups_for_user("alice") == {"g1", "g2"}


async def test_unknown_user_has_no_groups() -> None:
    idp = _store()
    assert await idp.groups_for_user("nobody") == set()
    assert await idp.get_user("nobody") is None
