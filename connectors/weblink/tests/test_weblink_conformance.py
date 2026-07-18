"""Hermetic conformance for the weblink connector (public-only, no network needed)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx
from pansoph_connector_sdk.verify.categories.contract import check_discovery, check_spec
from pansoph_connector_sdk.verify.categories.permissions import check_permissions
from pansoph_connector_weblink import connector as weblink_connector
from pansoph_connector_weblink.connector import (
    DEFAULT_USER_AGENT,
    BlockedUrlError,
    WeblinkConnector,
    _assert_public_url,
)
from pansoph_types import DocRef

_FIX = Path(__file__).parent / "fixtures" / "permissions"


def _factory() -> WeblinkConnector:
    return WeblinkConnector(instance_id="web", config={"urls": ["https://example.test/a"]})


@pytest.fixture
def _no_ssrf_check(monkeypatch: pytest.MonkeyPatch) -> None:
    # The user-agent tests fetch a reserved test domain that does not resolve; bypass the
    # SSRF guard (which would block an unresolvable host) so they test only fetch behavior.
    async def _ok(_url: str) -> None:
        return None

    monkeypatch.setattr(weblink_connector, "_assert_public_url", _ok)


@pytest.mark.conformance
async def test_weblink_discovery() -> None:
    conn = _factory()
    check_spec(conn)
    refs = await check_discovery(conn)
    assert refs[0].source_doc_id == "https://example.test/a"


@pytest.mark.conformance
@respx.mock
async def test_weblink_sends_descriptive_user_agent(
    respx_mock: respx.MockRouter, _no_ssrf_check: None
) -> None:
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
async def test_weblink_user_agent_is_configurable(
    respx_mock: respx.MockRouter, _no_ssrf_check: None
) -> None:
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


@pytest.mark.permission
async def test_weblink_ssrf_blocks_internal_and_metadata_addresses() -> None:
    # SSRF guard: literal internal/metadata/loopback IPs and non-http schemes are rejected
    # before any request. Literal IPs resolve without DNS, so this is hermetic.
    for url in (
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata (link-local)
        "http://127.0.0.1:8080/",  # loopback
        "http://10.0.0.5/internal",  # RFC1918 private
        "http://192.168.1.1/",  # RFC1918 private
        "http://[::1]/",  # IPv6 loopback
        "file:///etc/passwd",  # non-http scheme
        "gopher://evil/",  # non-http scheme
    ):
        with pytest.raises(BlockedUrlError):
            await _assert_public_url(url)

    # A literal public address is allowed (example.com's documented address).
    await _assert_public_url("http://93.184.216.34/")


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
