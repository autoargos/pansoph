"""The gated permission category.

For every document in the expected file: the connector's produced AccessControl must
exactly equal the expected one (set equality on public, allow_groups, allow_users,
deny), and the resolved visibility against the principal roster must match what the
expected ACL implies for every principal, including explicit negatives. Oversharing
(a superset) fails as hard as under-sharing. This is the source boundary that
protects the whole ReBAC guarantee.
"""

from __future__ import annotations

import json
from pathlib import Path

from pansoph_types import AccessControl, DocRef

from ...contract import Connector
from ..principals import Principal, can_see


def _acl_key(acl: AccessControl) -> tuple[bool, frozenset[str], frozenset[str], frozenset[str]]:
    return (
        bool(acl.public),
        frozenset(acl.allow_groups),
        frozenset(acl.allow_users),
        frozenset(acl.deny),
    )


async def check_permissions(
    connector: Connector, source_acl: Path, principals: Path, expected: Path
) -> None:
    roster = [Principal.model_validate(p) for p in json.loads(principals.read_text())]
    expected_map = {
        doc_id: AccessControl.model_validate(acl)
        for doc_id, acl in json.loads(expected.read_text()).items()
    }
    assert expected_map, "permission fixture must assert at least one document"

    instance_id = getattr(connector, "instance_id", "test")
    for doc_id, want in expected_map.items():
        ref = DocRef(source_doc_id=doc_id, instance_id=instance_id)
        got = await connector.list_acls(ref)
        assert _acl_key(got) == _acl_key(want), (
            f"ACL set equality failed for {doc_id}: got {got!r}, want {want!r}"
        )
        for principal in roster:
            want_see = can_see(want, principal)
            got_see = can_see(got, principal)
            assert got_see == want_see, (
                f"visibility mismatch for {principal.user_id} on {doc_id}: "
                f"got {got_see}, want {want_see}"
            )
