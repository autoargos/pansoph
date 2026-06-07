"""ACL-to-tuple mappers: the security-critical translation."""

from __future__ import annotations

from pansoph_authz import diff_acls, tuples_for_acl
from pansoph_authz.mappers import tuples_for_document
from pansoph_types import AccessControl, Document


def _keys(tuples):
    return {(t.subject.type, t.subject.id, t.subject.ns, t.object_id) for t in tuples}


def test_public_maps_to_user_wildcard() -> None:
    ts = tuples_for_acl("doc1", "inst", AccessControl(public=True))
    assert _keys(ts) == {("user", "*", "inst", "doc1")}


def test_groups_and_users_map_in_instance_namespace() -> None:
    acl = AccessControl(allow_groups=["eng"], allow_users=["alice"])
    ts = tuples_for_acl("doc1", "inst", acl)
    assert _keys(ts) == {("group", "eng", "inst", "doc1"), ("user", "alice", "inst", "doc1")}


def test_explicit_principal_ns_overrides_instance() -> None:
    acl = AccessControl(allow_groups=["eng"], principal_ns="other")
    ts = tuples_for_acl("doc1", "inst", acl)
    assert next(iter(ts)).subject.ns == "other"


def test_deny_is_never_expressed_as_a_tuple() -> None:
    acl = AccessControl(allow_users=["alice"], deny=["bob"])
    ts = tuples_for_acl("doc1", "inst", acl)
    assert all(t.subject.id != "bob" for t in ts)
    assert len(ts) == 1


def test_tuples_for_document_uses_global_id() -> None:
    doc = Document(id="github:org1:42", connector="github", instance_id="org1", tenant_id="t")
    doc.acl = AccessControl(allow_groups=["team"])
    ts = tuples_for_document(doc)
    assert all(t.object_id == "github:org1:42" for t in ts)


def test_diff_acls_separates_grants_from_revocations() -> None:
    old = AccessControl(allow_groups=["eng", "ops"], allow_users=["alice"])
    new = AccessControl(allow_groups=["eng"], allow_users=["alice", "bob"])
    to_write, to_delete = diff_acls("doc1", "inst", old, new)
    assert {(t.subject.type, t.subject.id) for t in to_write} == {("user", "bob")}
    assert {(t.subject.type, t.subject.id) for t in to_delete} == {("group", "ops")}


def test_diff_acls_no_change_is_empty() -> None:
    acl = AccessControl(allow_groups=["eng"])
    to_write, to_delete = diff_acls("doc1", "inst", acl, acl)
    assert to_write == [] and to_delete == []
