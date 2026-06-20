"""The declarative manifest schema, close to Airbyte's low code CDK.

A simple REST source is described here; only awkward sources drop to Python. The
runtime owns pagination, retries, backoff, and incremental cursors, so a manifest
only describes how to talk to the source for one page or one document.
"""

from __future__ import annotations

from typing import Literal

from pansoph_types import AuthScheme, Capabilities
from pydantic import BaseModel, Field


class AuthSpec(BaseModel):
    type: AuthScheme
    scopes: list[str] = Field(default_factory=list)


class AclMapping(BaseModel):
    """How to extract an ACL from a fetched payload, as JSONPath expressions."""

    from_: str = Field(alias="from")
    groups: str | None = None
    users: str | None = None
    public: str | None = None
    deny: str | None = None

    model_config = {"populate_by_name": True}


class PaginatorSpec(BaseModel):
    """One of four strategies. page/offset use the param names; cursor reads a body
    field; link_header follows the RFC 5988 next link."""

    strategy: Literal["page", "offset", "cursor", "link_header"]
    page_param: str = "page"
    size_param: str = "per_page"
    page_size: int = 100
    cursor_path: str | None = None  # JSONPath to the next cursor in the body
    cursor_param: str | None = None  # query param to send the cursor back in


class ListSpec(BaseModel):
    path: str
    since: str | None = None  # JSONPath to the change timestamp
    cursor: str | None = None  # cursor field name
    records: str | None = None  # JSONPath to the record array, default whole body
    id_field: str = "id"  # field on each record that becomes source_doc_id
    paginator: PaginatorSpec | None = None


class FetchSpec(BaseModel):
    path: str
    text: str | None = None  # JSONPath to the document text in the payload


class StreamSpec(BaseModel):
    name: str
    list: ListSpec
    fetch: FetchSpec
    acl: AclMapping | None = None


class ConnectorManifest(BaseModel):
    connector: str
    auth: AuthSpec
    capabilities: Capabilities
    streams: list[StreamSpec]
    base_url: str | None = None
