"""Runtime components a compiled manifest is built from.

extract evaluates a JSONPath against a payload. build_acl turns an AclMapping plus a
payload into an AccessControl. The Requester, Paginator, and Cursor behaviors are
expressed as small helpers the compiled connector composes; the durable runtime
still owns the actual retry and checkpoint loop.
"""

from __future__ import annotations

from typing import Any

from jsonpath_ng.ext import parse as jsonpath_parse
from pansoph_types import AccessControl

from .schema import AclMapping


def extract(payload: Any, expr: str) -> list[Any]:
    """Return all values matching a JSONPath expression."""
    return [m.value for m in jsonpath_parse(expr).find(payload)]


def extract_one(payload: Any, expr: str | None, default: Any = None) -> Any:
    if not expr:
        return default
    values = extract(payload, expr)
    return values[0] if values else default


def build_acl(payload: Any, mapping: AclMapping, principal_ns: str) -> AccessControl:
    groups = extract(payload, mapping.groups) if mapping.groups else []
    users = extract(payload, mapping.users) if mapping.users else []
    deny = extract(payload, mapping.deny) if mapping.deny else []
    public = bool(extract_one(payload, mapping.public, default=False))
    return AccessControl(
        public=public,
        allow_groups=[str(g) for g in groups],
        allow_users=[str(u) for u in users],
        deny=[str(d) for d in deny],
        principal_ns=principal_ns,
    )
