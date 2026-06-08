"""Management-role authorization: the pure decision that gates admin actions.

Roles are orthogonal to groups. Groups gate document retrieval; roles gate what
management actions a caller may perform. This tests the decision in isolation, with
no token and no IdP, so it stays adapter agnostic.
"""

from __future__ import annotations

from pansoph_core.routes import (
    ROLE_ADMIN,
    ROLE_CONNECTOR_ADMIN,
    ROLE_MEMBER,
    has_required_role,
)
from pansoph_identity.port import User


def test_admin_passes_any_requirement() -> None:
    assert has_required_role([ROLE_ADMIN], (ROLE_CONNECTOR_ADMIN,))
    assert has_required_role([ROLE_ADMIN], ())


def test_matching_role_passes() -> None:
    assert has_required_role([ROLE_CONNECTOR_ADMIN], (ROLE_CONNECTOR_ADMIN,))


def test_member_cannot_manage_connectors() -> None:
    assert not has_required_role([ROLE_MEMBER], (ROLE_CONNECTOR_ADMIN,))


def test_no_roles_is_denied() -> None:
    assert not has_required_role([], (ROLE_CONNECTOR_ADMIN,))
    assert not has_required_role(None, (ROLE_CONNECTOR_ADMIN,))


def test_user_roles_default_empty_and_orthogonal_to_groups() -> None:
    u = User(id="alice", group_ids=["g1"])
    assert u.roles == []
    u2 = User(id="bob", roles=[ROLE_CONNECTOR_ADMIN])
    assert u2.roles == [ROLE_CONNECTOR_ADMIN] and u2.group_ids == []
