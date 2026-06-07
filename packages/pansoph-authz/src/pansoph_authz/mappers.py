"""Source ACL to tuple mapping. This is part of what pansoph owns.

Given a document and its AccessControl (as imported by a connector), produce the
relationship tuples to write, and, on a permission change, the tuples to add and the
tuples to delete. The object id is the document's stable global id; principals are
namespaced by the connector instance so two instances never collide.
"""

from __future__ import annotations

from pansoph_types import AccessControl, Document

from .port import Principal, Tuple

READER = "reader"


def tuples_for_acl(object_id: str, instance_id: str, acl: AccessControl) -> list[Tuple]:
    """Produce the reader tuples implied by an ACL for one document.

    Public documents get a wildcard reader so the bulk check can short circuit them.
    Group and user grants become explicit reader tuples in the instance namespace.
    `deny` is intentionally not expressed as tuples, since exclusion operators wreck
    bulk check latency; denies are applied as a final filter in the enforcement path.
    """
    ns = acl.principal_ns or instance_id
    out: list[Tuple] = []
    if acl.public:
        out.append(_reader(Principal(type="user", id="*", ns=ns), object_id))
    for group in acl.allow_groups:
        out.append(_reader(Principal(type="group", id=group, ns=ns), object_id))
    for user in acl.allow_users:
        out.append(_reader(Principal(type="user", id=user, ns=ns), object_id))
    return out


def tuples_for_document(doc: Document) -> list[Tuple]:
    """Reader tuples for a document, from its stable id and imported ACL."""
    return tuples_for_acl(doc.id, doc.instance_id, doc.acl)


def diff_acls(
    object_id: str, instance_id: str, old: AccessControl, new: AccessControl
) -> tuple[list[Tuple], list[Tuple]]:
    """Return (to_write, to_delete) for a permission change.

    Entries present in new but not old are written. Entries present in old but not
    new are deleted: these are the revocations, the dangerous case, and the caller
    applies them first.
    """
    old_t = {_key(t): t for t in tuples_for_acl(object_id, instance_id, old)}
    new_t = {_key(t): t for t in tuples_for_acl(object_id, instance_id, new)}
    to_write = [t for k, t in new_t.items() if k not in old_t]
    to_delete = [t for k, t in old_t.items() if k not in new_t]
    return to_write, to_delete


def _reader(subject: Principal, object_id: str) -> Tuple:
    return Tuple(subject=subject, relation=READER, object_type="document", object_id=object_id)


def _key(t: Tuple) -> tuple[str, str, str, str, str, str]:
    return (t.subject.type, t.subject.id, t.subject.ns, t.relation, t.object_type, t.object_id)
