"""The connector specs endpoints: /api/connectors/specs serves every connector's config
schema so the UI can render the add-connector form dynamically, and /api/connectors/{name}/spec
serves a single one. Unauthenticated, like the plain /api/connectors list. Offline: the specs
are static classmethods, no network or Postgres. No em dashes anywhere.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from pansoph_core.app import create_app


def _client() -> TestClient:
    return TestClient(create_app())


def _field(spec: dict[str, Any], key: str) -> dict[str, Any]:
    return next(f for f in spec["fields"] if f["key"] == key)


def _by_name(connectors: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {c["name"]: c for c in connectors}


def test_specs_lists_connectors_sorted_by_title_with_expected_field_kinds() -> None:
    resp = _client().get("/api/connectors/specs")
    assert resp.status_code == 200
    connectors = resp.json()["connectors"]

    titles = [c["title"] for c in connectors]
    assert titles == sorted(titles)

    by_name = _by_name(connectors)
    # Both a credentialed and a credential-free connector are present.
    assert "notion" in by_name
    assert "weblink" in by_name

    # Notion's integration token is a secret kind (sealed in the secret store, not plaintext).
    notion_token = _field(by_name["notion"], "token")
    assert notion_token["kind"] == "secret"

    # weblink's urls is a list kind (many pages entered one per line).
    weblink_urls = _field(by_name["weblink"], "urls")
    assert weblink_urls["kind"] == "list"


def test_single_spec_returns_one_connector() -> None:
    resp = _client().get("/api/connectors/notion/spec")
    assert resp.status_code == 200
    spec = resp.json()
    assert spec["name"] == "notion"
    assert spec["title"] == "Notion"
    assert _field(spec, "token")["kind"] == "secret"


def test_single_spec_unknown_connector_is_404() -> None:
    resp = _client().get("/api/connectors/does_not_exist/spec")
    assert resp.status_code == 404
