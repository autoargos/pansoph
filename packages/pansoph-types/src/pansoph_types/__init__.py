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
    """A lightweight reference to a source document, returned by list_changes."""

    source_doc_id: str
    instance_id: str
    updated_at: datetime | None = None
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
