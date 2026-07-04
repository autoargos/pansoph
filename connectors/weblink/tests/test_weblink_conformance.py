"""Hermetic conformance for the weblink connector (public-only, no network needed)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx
from pansoph_connector_sdk.verify.categories.contract import check_discovery, check_spec
from pansoph_connector_sdk.verify.categories.permissions import check_permissions
from pansoph_connector_weblink.connector import DEFAULT_USER_AGENT, WeblinkConnector
from pansoph_types import DocRef

_FIX = Path(__file__).parent / "fixtures" / "permissions"


def _factory() -> WeblinkConnector:
    return WeblinkConnector(instance_id="web", config={"urls": ["https://example.test/a"]})


@pytest.mark.conformance
async def test_weblink_discovery() -> None:
    conn = _factory()
    check_spec(conn)
    refs = await check_discovery(conn)
    assert refs[0].source_doc_id == "https://example.test/a"


@pytest.mark.conformance
@respx.mock
async def test_weblink_sends_descriptive_user_agent(respx_mock: respx.MockRouter) -> None:
    # Regression for the 403 seen against Wikipedia: a generic bot agent was rejected.
    # Every fetch must carry the honest, descriptive default agent (name plus contact URL).
    route = respx_mock.get("https://example.test/a").mock(
        return_value=httpx.Response(200, text="hello", headers={"content-type": "text/plain"})
    )
    conn = _factory()
    await conn.fetch(DocRef(source_doc_id="https://example.test/a", instance_id="web"))
    sent = route.calls.last.request
    assert sent.headers["user-agent"] == DEFAULT_USER_AGENT
    assert "pansoph" in sent.headers["user-agent"]
    assert "github.com/autoargos/pansoph" in sent.headers["user-agent"]
    assert sent.headers["accept"].startswith("text/html")


@pytest.mark.conformance
@respx.mock
async def test_weblink_user_agent_is_configurable(respx_mock: respx.MockRouter) -> None:
    # An operator override (their own contact per the Wikimedia policy) must win.
    custom = "acme-kb/2.0 (+https://acme.example/bots)"
    route = respx_mock.get("https://example.test/a").mock(
        return_value=httpx.Response(200, text="x")
    )
    conn = WeblinkConnector(
        instance_id="web", config={"urls": ["https://example.test/a"], "user_agent": custom}
    )
    await conn.fetch(DocRef(source_doc_id="https://example.test/a", instance_id="web"))
    assert route.calls.last.request.headers["user-agent"] == custom


@pytest.mark.conformance
@pytest.mark.permission
async def test_weblink_permissions_public_only() -> None:
    conn = _factory()
    await check_permissions(
        conn,
        source_acl=_FIX / "principals.json",
        principals=_FIX / "principals.json",
        expected=_FIX / "expected.json",
    )
