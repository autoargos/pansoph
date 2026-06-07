"""Conformance test for the confluence connector.

Once the connector is implemented, seed it and assert it conforms. Until then this
test documents the expectation. Run with: pytest (from this package).
"""

from __future__ import annotations

import pytest
from pansoph_connector_confluence.connector import ConfluenceConnector
from pansoph_connector_sdk import assert_conforms


@pytest.mark.skip(reason="implement confluence against its latest API docs, then enable")
async def test_confluence_conforms() -> None:
    connector = ConfluenceConnector(instance_id="test")
    # TODO seed the connector or point it at a recorded fixture, then:
    await assert_conforms(connector)
