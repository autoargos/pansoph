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


def tuples_for_acl(
    object_id: str,
    instance_id: str,
    acl: AccessControl,
    tenant_id: str | None = None,
) -> list[Tuple]:
    """Produce the reader tuples implied by an ACL for one document.

    Two-level namespace (ADR-0001 "each instance has its own ACL namespace" with
    ADR-0002 "map IdP groups to ReBAC group tuples so one nested group machinery
    serves both human auth and connector ACLs"):

      - group reader tuples are tenant scoped, so a user's identity groups (also
        tenant scoped, see `membership_tuples`) resolve against any instance's docs;
      - user and wildcard reader tuples are instance scoped, so two instances that
        each have a same-named collaborator never collide.

    When `tenant_id` is omitted, groups fall back to the user namespace (the legacy
    single-level behavior). `deny` is intentionally not expressed as tuples, since
    exclusion operators wreck bulk check latency; denies are applied as a final
    filter in the enforcement path.
    """
    user_ns = acl.principal_ns or instance_id
    group_ns = tenant_id if tenant_id is not None else user_ns
    out: list[Tuple] = []
    if acl.public:
        out.append(_reader(Principal(type="user", id="*", ns=user_ns), object_id))
    for group in acl.allow_groups:
        out.append(_reader(Principal(type="group", id=group, ns=group_ns), object_id))
    for user in acl.allow_users:
        out.append(_reader(Principal(type="user", id=user, ns=user_ns), object_id))
    return out


def tuples_for_document(doc: Document) -> list[Tuple]:
    """Reader tuples for a document, from its stable id and imported ACL."""
    return tuples_for_acl(doc.id, doc.instance_id, doc.acl, tenant_id=doc.tenant_id)


def membership_tuples(user_id: str, groups: list[str], ns: str) -> list[Tuple]:
    """Group membership tuples for a user, mapping identity groups into the ReBAC
    graph so the engine can resolve nested-group document access. The group object
    id is namespaced the same way the document reader tuples namespace their groups,
    so a user's membership matches the documents shared with that group.
    """
    return [
        Tuple(
            subject=Principal(type="user", id=user_id),
            relation="member",
            object_type="group",
            object_id=f"{ns}_{group}",
        )
        for group in groups
    ]


def diff_acls(
    object_id: str,
    instance_id: str,
    old: AccessControl,
    new: AccessControl,
    tenant_id: str | None = None,
) -> tuple[list[Tuple], list[Tuple]]:
    """Return (to_write, to_delete) for a permission change.

    Entries present in new but not old are written. Entries present in old but not
    new are deleted: these are the revocations, the dangerous case, and the caller
    applies them first.
    """
    old_t = {_key(t): t for t in tuples_for_acl(object_id, instance_id, old, tenant_id)}
    new_t = {_key(t): t for t in tuples_for_acl(object_id, instance_id, new, tenant_id)}
    to_write = [t for k, t in new_t.items() if k not in old_t]
    to_delete = [t for k, t in old_t.items() if k not in new_t]
    return to_write, to_delete


def _reader(subject: Principal, object_id: str) -> Tuple:
    return Tuple(subject=subject, relation=READER, object_type="document", object_id=object_id)


def _key(t: Tuple) -> tuple[str, str, str, str, str, str]:
    return (t.subject.type, t.subject.id, t.subject.ns, t.relation, t.object_type, t.object_id)
