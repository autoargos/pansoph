"""Shared data shapes for pansoph.

These types are the common vocabulary every pansoph package speaks. The package
has no pansoph dependencies, so any building block can be used standalone.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

__all__ = [
    "AuthScheme",
    "Capabilities",
    "AccessControl",
    "Section",
    "DocRef",
    "RawDoc",
    "Document",
    "Resource",
    "ResourcePage",
]


class AuthScheme(StrEnum):
    oauth2 = "oauth2"
    api_token = "api_token"  # noqa: S105  enum value, not a credential
    service_account = "service_account"
    none = "none"


class Capabilities(BaseModel):
    """What a connector can do. The runtime adapts behavior to these."""

    incremental: bool = False
    permission_sync: bool = False
    deletions: bool = False
    slim_listing: bool = False
    resource_discovery: bool = False


class AccessControl(BaseModel):
    """Document level access control, as imported from a source or set locally.

    `deny` is evaluated last. `principal_ns` is the per connector instance
    namespace these principals live in, so two instances never collide.
    """

    public: bool = False
    allow_groups: list[str] = Field(default_factory=list)
    allow_users: list[str] = Field(default_factory=list)
    deny: list[str] = Field(default_factory=list)
    principal_ns: str = ""


class Section(BaseModel):
    """An ordered text block within a document, with optional heading and offsets."""

    text: str
    heading: str | None = None
    start_offset: int | None = None
    end_offset: int | None = None


class DocRef(BaseModel):
    """A lightweight reference to a source document, returned by list_changes.

    `deleted` is how a connector signals a removal during a poll-based sync: it
    yields a DocRef with deleted=True for a source document that no longer exists
    (a tombstone), and the runtime purges that document and its access tuples. A
    deleted DocRef still carries source_doc_id and instance_id so the runtime can
    identify what to remove; its content is never fetched.
    """

    source_doc_id: str
    instance_id: str
    updated_at: datetime | None = None
    deleted: bool = False
    extra: dict[str, str] = Field(default_factory=dict)


class RawDoc(BaseModel):
    """The raw payload fetched from a source, before parsing and chunking."""

    source_doc_id: str
    instance_id: str
    mime: str
    content: bytes | None = None
    text: str | None = None
    title: str | None = None
    source_url: str | None = None
    lang: str | None = None
    extra: dict[str, str] = Field(default_factory=dict)


class Resource(BaseModel):
    """One selectable thing a connected credential can reach in its source.

    A resource is what a `ConfigField` bound to a picker resolves to: a github org
    or repo, a drive or folder, a confluence space, a slack channel. `id` is the
    opaque value the connector needs (owner/repo slug, folder id, space key), and
    `label` is the human name the picker shows. `kind` names the level so the UI can
    drill hierarchically (org to repo, drive to folder, workspace to channel), and
    `parent_id` links a child to the resource above it. `selectable` is False for a
    grouping node the user can expand but not choose (an org header, say). `extra`
    carries source-specific hints the picker or the connector may use.

    A resource is only ever what the configured credential can already reach, at the
    source's granularity, so discovery is a read the credential permits and never a
    scope escalation.
    """

    id: str
    kind: str
    label: str
    parent_id: str | None = None
    selectable: bool = True
    extra: dict[str, str] = Field(default_factory=dict)


class ResourcePage(BaseModel):
    """One page of resources plus the cursor for the next page.

    `next_cursor` is None when the listing is exhausted. The cursor is opaque to the
    caller and passed back verbatim to `list_resources` to fetch the following page,
    so pagination is the source's own model (a page token, a Link header offset, a
    delta link), never reconstructed by the runtime.
    """

    items: list[Resource] = Field(default_factory=list)
    next_cursor: str | None = None


class Document(BaseModel):
    """The indexed unit. `id` is the stable global id "{connector}:{instance}:{source_doc_id}".

    `acl_version` is monotonic and used for cache invalidation. `tenant_id` is the
    hard isolation boundary.
    """

    id: str
    connector: str
    instance_id: str
    tenant_id: str
    title: str | None = None
    source_url: str | None = None
    sections: Sequence[Section] = Field(default_factory=list)
    mime: str | None = None
    lang: str | None = None
    content_hash: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    acl: AccessControl = Field(default_factory=AccessControl)
    acl_version: int = 0
    extra: dict[str, str] = Field(default_factory=dict)

    @staticmethod
    def make_id(connector: str, instance_id: str, source_doc_id: str) -> str:
        return f"{connector}:{instance_id}:{source_doc_id}"
