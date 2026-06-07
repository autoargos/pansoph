"""The conformance suite must reject connectors that violate the contract."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from pansoph_connector_sdk import (
    ConnectorMeta,
    InMemoryConnector,
    ValidationResult,
    assert_conforms,
)
from pansoph_types import AccessControl, AuthScheme, Capabilities, DocRef, RawDoc


async def test_unseeded_connector_fails() -> None:
    # No documents means list_changes returns nothing, which is a contract violation
    # for the suite (the caller must seed first).
    with pytest.raises(AssertionError):
        await assert_conforms(InMemoryConnector())


class _BadFetchConnector:
    """Yields a valid change but fetch returns the wrong type."""

    def __init__(self) -> None:
        self.meta = ConnectorMeta(
            name="bad", auth=AuthScheme.none, capabilities=Capabilities(incremental=True)
        )

    def validate(self, config: dict[str, Any]) -> ValidationResult:
        return ValidationResult(ok=True)

    async def list_changes(self, cursor: str | None) -> AsyncIterator[tuple[DocRef, str]]:
        yield DocRef(source_doc_id="x", instance_id="i"), "1"

    async def fetch(self, ref: DocRef) -> tuple[RawDoc, AccessControl]:
        return "not a rawdoc", AccessControl()  # type: ignore[return-value]

    async def list_acls(self, ref: DocRef) -> AccessControl:
        return AccessControl()

    async def on_webhook(self, event: dict[str, Any]) -> list[Any]:
        return []


async def test_wrong_fetch_type_fails() -> None:
    with pytest.raises(AssertionError):
        await assert_conforms(_BadFetchConnector())
