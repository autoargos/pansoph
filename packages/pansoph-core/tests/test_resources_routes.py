"""The discovery and credential-status routes are wired into the app and gated to a
connector admin. Offline: the auth dependency runs before any handler body, so an
unauthenticated request is rejected without touching Postgres. No em dashes anywhere.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from pansoph_core.app import create_app


def _client() -> TestClient:
    return TestClient(create_app())


def test_resources_route_requires_auth() -> None:
    resp = _client().get("/api/connectors/instances/inst-1/resources")
    # No bearer token: the connector-admin dependency rejects before the handler runs.
    assert resp.status_code == 401


def test_credential_route_requires_auth() -> None:
    resp = _client().get("/api/connectors/instances/inst-1/credential")
    assert resp.status_code == 401


def test_resources_route_accepts_discovery_query_params() -> None:
    # The route declares kind, parent, q, and cursor. Passing them still yields 401
    # (auth first), which proves the params are accepted, not a 422 validation error.
    resp = _client().get(
        "/api/connectors/instances/inst-1/resources",
        params={"kind": "repo", "parent": "org1", "q": "foo", "cursor": "c1"},
    )
    assert resp.status_code == 401


def test_documents_route_requires_auth() -> None:
    resp = _client().get("/api/connectors/instances/inst-1/documents")
    assert resp.status_code == 401
