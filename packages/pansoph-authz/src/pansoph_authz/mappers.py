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

# The reserved group every user is a member of within their tenant. A "public"
# document grants read to this group (tenant-scoped), so "public" means "everyone in
# this tenant" and the authz engine enforces tenant isolation, rather than relying on
# a global user:* wildcard that any user in any tenant would match. The double
# underscore prefix avoids colliding with a real source group name.
TENANT_EVERYONE = "__pansoph_everyone"


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
    group_ns = tenant_id if tenant_id is not None else (acl.principal_ns or instance_id)
    out: list[Tuple] = []
    if acl.public:
        # Grant to the tenant's everyone group (tenant-scoped via group_ns), not a
        # global user:* wildcard. Every user is made a member of this group at
        # retrieval, so the authz engine, not just the vector pre-filter, keeps a
        # public document from crossing the tenant boundary.
        out.append(_reader(Principal(type="group", id=TENANT_EVERYONE, ns=group_ns), object_id))
    for group in acl.allow_groups:
        out.append(_reader(Principal(type="group", id=group, ns=group_ns), object_id))
    for user in acl.allow_users:
        # Users are namespaced by tenant, exactly like groups and the query subject. A
        # person is one principal per tenant (not per instance), so a direct-user grant
        # resolves against the same `user:{tenant}_id` the retrieval path checks.
        out.append(_reader(Principal(type="user", id=user, ns=group_ns), object_id))
    return out


def narrow(acl: AccessControl, scope_type: str, scope_ref: str) -> AccessControl:
    """Intersect a source ACL with an instance's management scope. Scope can only ever
    NARROW the audience, never widen it, so a guarded knowledge base (ADR-0001) holds
    even when the source is more permissive than the operator intends.

    - org (or no scope_ref): the source ACL governs unchanged.
    - individual: a personal connector whose credential belongs to scope_ref. The source
      enforces access at fetch time (a personal token only returns what its owner may
      read), so every doc the credential returned is one scope_ref is already allowed to
      see. The source ACL principals are source-native ids (an Atlassian accountId, a
      Slack member id) in a different namespace than the pansoph scope_ref, so a direct
      "scope_ref in allow_users" membership test is a category error that always fails
      (it would grant the owner NONE of their own synced docs). The doc is therefore
      granted to scope_ref alone, in their private namespace (no groups, never public),
      which can only narrow, never widen, and never leaks to another user.
    - group: effective audience = source ACL intersected with the group. Reachable when
      the source doc is public or already grants that group.

    `deny` is always preserved; scope never relaxes a deny.
    """
    if scope_type == "org" or not scope_ref:
        return acl
    if scope_type == "individual":
        return AccessControl(
            public=False,
            allow_groups=[],
            allow_users=[scope_ref],
            deny=list(acl.deny),
            principal_ns=acl.principal_ns,
        )
    if scope_type == "group":
        reachable = acl.public or scope_ref in acl.allow_groups
        return AccessControl(
            public=False,
            allow_users=[],
            allow_groups=[scope_ref] if reachable else [],
            deny=list(acl.deny),
            principal_ns=acl.principal_ns,
        )
    return acl


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
            # The member subject is namespaced by tenant too, so it matches both the
            # tenant-scoped user reader tuples and the query subject (all `user:{ns}_id`).
            subject=Principal(type="user", id=user_id, ns=ns),
            relation="member",
            object_type="group",
            # The group object carries its namespace separately (not pre-combined), so each
            # adapter encodes (object_ns, object_id) exactly as it encodes a group subject.
            # Pre-combining as `f"{ns}_{group}"` here would diverge from the adapter's
            # subject encoding once that encoding escapes the separator inside a component.
            object_id=group,
            object_ns=ns,
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


def _key(t: Tuple) -> tuple[str, str, str, str, str, str, str]:
    return (
        t.subject.type,
        t.subject.id,
        t.subject.ns,
        t.relation,
        t.object_type,
        t.object_id,
        t.object_ns,
    )
