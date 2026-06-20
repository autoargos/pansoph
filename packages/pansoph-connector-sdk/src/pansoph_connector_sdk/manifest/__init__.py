"""Declarative connector manifests: schema and compiler."""

from __future__ import annotations

from .compiler import compile_manifest
from .schema import (
    AclMapping,
    AuthSpec,
    ConnectorManifest,
    FetchSpec,
    ListSpec,
    PaginatorSpec,
    StreamSpec,
)

__all__ = [
    "AclMapping",
    "AuthSpec",
    "ConnectorManifest",
    "FetchSpec",
    "ListSpec",
    "PaginatorSpec",
    "StreamSpec",
    "compile_manifest",
]
