"""The AclLog port: the last-written ACL and acl_version for each document.

Revocation is a contraction of an ACL, and to compute a contraction the pipeline
must know what was written last time. `diff_acls` needs the prior ACL, so the
pipeline persists the narrowed ACL it wrote, keyed by the stable global doc_id, and
reads it back on the next sync to diff against the new one.

Why a dedicated small table instead of reading it off the chunks: the enforcement
path never lists chunks by document, and adding a doc-scoped list to the VectorStore
or a list call to the AuthzPort would put a scan on a hot port. A tiny key-value
table keyed by doc_id keeps both hot ports minimal (the non-negotiable in ADR-0002),
gives an O(1) point read on the ingest path, and stores the ACL once per document
rather than duplicated across every chunk row. It also carries the monotonic
acl_version so cache invalidation is meaningful (ADR-0001: acl_version is monotonic).

This is a port like every other role, so it runs with the Postgres adapter in
production and an in-memory fake in tests, and stays fully replaceable.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pansoph_types import AccessControl


class AclRecord:
    """The last ACL written for a document, its tenant, and the version stamp.

    `tenant_id` is stored so a later deletion can reconstruct exactly the tuples that
    were written (the mapper namespaces by tenant), and so the record is self
    contained on purge, when the connector no longer supplies content.
    """

    __slots__ = ("acl", "tenant_id", "acl_version")

    def __init__(self, acl: AccessControl, tenant_id: str, acl_version: int) -> None:
        self.acl = acl
        self.tenant_id = tenant_id
        self.acl_version = acl_version


@runtime_checkable
class AclLog(Protocol):
    """Durable record of the ACL last written per document, for revocation diffing.

    Point-keyed by doc_id. No list or scan call, so it never lands on a hot path.
    """

    async def get(self, doc_id: str) -> AclRecord | None:
        """Return the last-written ACL, tenant, and version for a document, or None if
        never written (a first-time ingest, which is a plain write, not a diff)."""
        ...

    async def put(self, doc_id: str, acl: AccessControl, tenant_id: str, acl_version: int) -> None:
        """Record the ACL, tenant, and version just written for a document."""
        ...

    async def delete(self, doc_id: str) -> None:
        """Forget a document's ACL record (called when the document is purged). Idempotent."""
        ...


class InMemoryAclLog:
    """A process-local AclLog for tests and single-process demos. Not durable."""

    def __init__(self) -> None:
        self._by_doc: dict[str, AclRecord] = {}

    async def get(self, doc_id: str) -> AclRecord | None:
        return self._by_doc.get(doc_id)

    async def put(self, doc_id: str, acl: AccessControl, tenant_id: str, acl_version: int) -> None:
        self._by_doc[doc_id] = AclRecord(acl, tenant_id, acl_version)

    async def delete(self, doc_id: str) -> None:
        self._by_doc.pop(doc_id, None)

    async def doc_ids_with_prefix(self, prefix: str) -> list[str]:
        return sorted(d for d in self._by_doc if d.startswith(prefix))
