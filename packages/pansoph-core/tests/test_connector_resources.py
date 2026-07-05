"""Resource discovery and credential status helpers, plus the draft lifecycle in
validate_connector_config. Hermetic: uses the registered in-memory connector and no
Postgres or network. No em dashes anywhere.
"""

from __future__ import annotations

import pytest
from pansoph_core.connector_resources import (
    ResourceDiscoveryError,
    instance_credential_status,
    list_instance_resources,
)
from pansoph_core.connectors import STATUS_ACTIVE, STATUS_DRAFT, validate_connector_config


def _instance(config: dict | None = None) -> dict:
    return {
        "id": "inst-1",
        "connector": "memory",
        "name": "mem",
        "tenant_id": "default",
        "config": config or {},
        "status": STATUS_ACTIVE,
    }


# --- resource discovery ---


async def test_list_instance_resources_top_level() -> None:
    page = await list_instance_resources(None, _instance())  # type: ignore[arg-type]
    assert {r.id for r in page.items} == {"folder-a", "folder-b"}
    assert page.next_cursor is None


async def test_list_instance_resources_children_by_parent() -> None:
    page = await list_instance_resources(
        None,  # type: ignore[arg-type]
        _instance(),
        kind="item",
        parent_id="folder-a",
    )
    assert [r.id for r in page.items] == ["item-a1"]


async def test_list_instance_resources_server_side_query_filter() -> None:
    # memory has no native search, so q filters the page here by label/id.
    page = await list_instance_resources(None, _instance(), query="folder-b")  # type: ignore[arg-type]
    assert [r.id for r in page.items] == ["folder-b"]


async def test_list_instance_resources_400_when_no_discovery(monkeypatch: object) -> None:
    # A connector whose capabilities lack resource_discovery raises a 400-mapped error.
    from pansoph_core import connector_resources as cr

    class _Caps:
        resource_discovery = False

    monkeypatch.setattr(cr, "_instance_capabilities", lambda inst: _Caps())  # type: ignore[attr-defined]
    with pytest.raises(ResourceDiscoveryError) as exc:
        await list_instance_resources(None, _instance())  # type: ignore[arg-type]
    assert exc.value.status_code == 400


async def test_list_instance_resources_502_on_upstream_error(monkeypatch: object) -> None:
    from pansoph_core import connector_resources as cr

    class _Boom:
        meta = type("M", (), {"capabilities": type("C", (), {"resource_discovery": True})()})()

        async def list_resources(self, **_: object) -> object:
            raise RuntimeError("source 503")

    monkeypatch.setattr(cr, "_build_connector", lambda inst: _Boom())  # type: ignore[attr-defined]
    with pytest.raises(ResourceDiscoveryError) as exc:
        await list_instance_resources(None, _instance())  # type: ignore[arg-type]
    assert exc.value.status_code == 502


# --- credential status ---


def test_credential_status_not_connected_needs_reauth() -> None:
    status = instance_credential_status(_instance())
    assert status.connected is False
    assert status.has_refresh is False
    assert status.needs_reauth is True


def test_credential_status_connected_with_refresh() -> None:
    config = {
        "oauth_token": {"_secret": "inst-1/oauth_token"},
        "oauth_token__refresh_token": {"_secret": "inst-1/oauth_token__refresh_token"},
    }
    status = instance_credential_status(_instance(config))
    assert status.connected is True
    assert status.has_refresh is True
    # A present credential with a refresh token never needs reauth.
    assert status.needs_reauth is False


def test_credential_status_connected_no_refresh_no_known_expiry() -> None:
    config = {"oauth_token": {"_secret": "inst-1/oauth_token"}}
    status = instance_credential_status(_instance(config))
    assert status.connected is True
    assert status.has_refresh is False
    # Unknown expiry is not treated as expired, so a connected token is not reauth.
    assert status.needs_reauth is False


def test_credential_status_expired_without_refresh_needs_reauth() -> None:
    config = {
        "oauth_token": {"_secret": "inst-1/oauth_token"},
        "oauth_token__expires_at": "2000-01-01T00:00:00+00:00",
    }
    status = instance_credential_status(_instance(config))
    assert status.connected is True
    assert status.needs_reauth is True


# --- draft lifecycle validation ---


def test_validate_skips_resource_validation_for_draft(monkeypatch: object) -> None:
    # Prove the draft path never runs validate: a connector whose validate reports a
    # missing resource passes as a draft (validation skipped) but fails as active.
    from pansoph_connector_sdk import ValidationResult
    from pansoph_core import connectors as conns

    class _NeedsRepo:
        def __init__(self, instance_id: str, config: dict) -> None:
            self._config = config

        def validate(self, config: dict) -> ValidationResult:
            if not config.get("repo"):
                return ValidationResult(ok=False, error="missing config: repo")
            return ValidationResult(ok=True)

    monkeypatch.setattr(conns, "load_adapter", lambda group, name: _NeedsRepo)  # type: ignore[attr-defined]

    # Draft: resource validation is skipped, so an empty config is accepted.
    validate_connector_config("github", "inst-1", {}, status=STATUS_DRAFT)
    # Active: the same empty config is rejected with the connector's own error.
    with pytest.raises(ValueError, match="missing config: repo"):
        validate_connector_config("github", "inst-1", {}, status=STATUS_ACTIVE)


def test_validate_draft_unknown_connector_still_fails() -> None:
    # A draft still has to load the connector: an unknown one is a ValueError even for
    # a draft, so a typo cannot slip through the two-phase path.
    with pytest.raises(ValueError, match="unknown or unloadable"):
        validate_connector_config("does-not-exist", "inst-1", {}, status=STATUS_DRAFT)
