"""Source ACL to tuple mapping. This is part of what pansoph owns.

Given a document and its AccessControl (as imported by a connector), produce the
relationship tuples to write, and the tuples to delete when permissions contract.
The object id is the document's stable global id; principals are namespaced by the
connector instance so two instances never collide.
"""

from __future__ import annotations

from pansoph_types import AccessControl, Document

from .port import Principal, Tuple

READER = "reader"


def tuples_for_document(doc: Document) -> list[Tuple]:
    """Produce the reader tuples implied by a document's ACL.

    Public documents are represented with a wildcard reader so the bulk check can
    short circuit them. Group and user grants become explicit reader tuples in the
    instance namespace. `deny` is intentionally not expressed as tuples here, since
    exclusion operators wreck bulk check latency; denies are applied as a final
    filter in the enforcement path instead.
    """
    ns = doc.acl.principal_ns or doc.instance_id
    out: list[Tuple] = []

    if doc.acl.public:
        out.append(
            Tuple(
                subject=Principal(type="user", id="*", ns=ns),
                relation=READER,
                object_type="document",
                object_id=doc.id,
            )
        )

    for group in doc.acl.allow_groups:
        out.append(
            Tuple(
                subject=Principal(type="group", id=group, ns=ns),
                relation=READER,
                object_type="document",
                object_id=doc.id,
            )
        )
    for user in doc.acl.allow_users:
        out.append(
            Tuple(
                subject=Principal(type="user", id=user, ns=ns),
                relation=READER,
                object_type="document",
                object_id=doc.id,
            )
        )
    return out


def diff_acls(
    doc_id: str, instance_id: str, old: AccessControl, new: AccessControl
) -> tuple[list[Tuple], list[Tuple]]:
    """Return (to_write, to_delete) tuples for a permission change.

    Revocations (entries present in old but not new) become deletes and must be
    applied promptly, since a contraction is the dangerous case. Stub: the full
    diff lands with the ingestion workflow.
    """
    raise NotImplementedError("ACL diff lands with the ingestion workflow, see ADR-0002")
