"""End-to-end conformance for the in-memory reference connector, fully offline."""

from __future__ import annotations

from pathlib import Path

import pytest
from pansoph_connector_sdk import InMemoryConnector, run_conformance
from pansoph_types import AccessControl

_CONF = Path(__file__).parent / "conformance" / "memory" / "conformance.yaml"


def _factory(instance_id: str, config: dict) -> InMemoryConnector:
    c = InMemoryConnector(instance_id=instance_id)
    c.put("handbook", "public handbook", AccessControl(public=True))
    c.put("secret", "eng only", AccessControl(allow_groups=["eng"]))
    return c


@pytest.mark.conformance
@pytest.mark.permission
async def test_memory_conforms() -> None:
    result = await run_conformance(_CONF, _factory)
    assert result.passed
    assert set(result.ran) >= {"spec", "connection", "discovery", "incremental", "permissions"}
