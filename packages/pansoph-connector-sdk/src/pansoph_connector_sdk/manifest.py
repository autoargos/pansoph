"""Declarative connector manifests.

A simple REST source is described by a manifest, and only unusual sources drop to
code. The manifest schema is intentionally close to Airbyte's low code CDK so the
runtime can implement pagination, incremental cursors, and ACL mapping generically.

Example (Confluence):

    connector: confluence
    auth: { type: oauth2, scopes: [read:confluence-content] }
    capabilities: { incremental: true, permission_sync: true, deletions: true }
    streams:
      - name: pages
        list:  { path: /content, since: "$.results[*].version.when", cursor: lastModified }
        fetch: { path: /content/{id}?expand=body.storage,restrictions }
        acl:
          from: restrictions
          groups: "$.read.group[*].name"
          users: "$.read.user[*].accountId"

This module defines the manifest model and the entry point that compiles a
manifest into a `Connector`. The compiler is part of the runtime work, not the
scaffold; this is the schema it will target.
"""

from __future__ import annotations

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


class ListSpec(BaseModel):
    path: str
    since: str | None = None  # JSONPath to the change timestamp
    cursor: str | None = None  # cursor field name


class FetchSpec(BaseModel):
    path: str


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


def compile_manifest(manifest: ConnectorManifest) -> object:
    """Compile a manifest into a Connector. Implemented by the runtime; stub for now."""
    raise NotImplementedError("manifest compiler lands with the runtime, see ADR-0002")
