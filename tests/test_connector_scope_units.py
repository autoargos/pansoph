"""Connector-config validation at instance creation, the scope building block.

A connector instance is validated against the connector's own contract before it is
persisted, resolved through the registry so the core never imports a connector. This
covers the two decision paths without a database.
"""

from __future__ import annotations

import pytest
from pansoph_core.connectors import _SCOPE_TYPES, validate_connector_config


def test_known_connector_with_ok_config_validates() -> None:
    # The in-memory reference connector is registered and accepts any config.
    validate_connector_config("memory", "inst", {})


def test_unknown_connector_raises() -> None:
    with pytest.raises(ValueError, match="unknown or unloadable connector"):
        validate_connector_config("does_not_exist", "inst", {})


def test_scope_types_are_org_group_individual() -> None:
    assert _SCOPE_TYPES == {"org", "group", "individual"}
