"""Hermetic conformance for the notion connector, driven by respx mocks.

No live credentials: the Notion REST calls the connector makes are mocked with
representative payloads. The permission category asserts the connector reports the
INTEGRATION-SCOPED ACL (only the integration principal can see a page), which is the
faithful minimal grant, since Notion exposes no per-page principal readout but search
is scoped to what the integration can reach (see the connector module header).

The factory pins integration_principal so the ACL is deterministic without a live
GET /v1/users/me; a separate test exercises the /v1/users/me resolution path.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx
from pansoph_connector_notion.connector import NotionConnector
from pansoph_connector_sdk import assert_conforms
from pansoph_connector_sdk.verify.categories.contract import check_discovery, check_spec
from pansoph_connector_sdk.verify.categories.permissions import check_permissions
from pansoph_connector_sdk.verify.categories.sync import check_deletions

_FIX = Path(__file__).parent / "fixtures" / "permissions"


def _factory() -> NotionConnector:
    return NotionConnector(
        instance_id="acme",
        config={"token": "x", "integration_principal": "bot-acme"},
    )


def _seed_one_page(respx_mock: respx.MockRouter) -> None:
    """One live shared page, its block children, and the bot identity."""
    respx_mock.post(path="/v1/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {"id": "p1", "object": "page", "in_trash": False, "is_archived": False}
                ],
                "has_more": False,
                "next_cursor": None,
            },
        )
    )
    respx_mock.get(path="/v1/blocks/p1/children").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {
                        "type": "paragraph",
                        "paragraph": {"rich_text": [{"plain_text": "hello"}]},
                    }
                ],
                "has_more": False,
                "next_cursor": None,
            },
        )
    )
    respx_mock.get(path="/v1/users/me").mock(
        return_value=httpx.Response(200, json={"object": "user", "id": "bot-acme", "type": "bot"})
    )


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_notion_assert_conforms(respx_mock: respx.MockRouter) -> None:
    _seed_one_page(respx_mock)
    conn = _factory()
    await assert_conforms(conn)


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_notion_discovery_and_spec(respx_mock: respx.MockRouter) -> None:
    respx_mock.post(path="/v1/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [{"id": "p1", "object": "page"}],
                "has_more": False,
                "next_cursor": None,
            },
        )
    )
    conn = _factory()
    check_spec(conn)
    refs = await check_discovery(conn)
    assert [r.source_doc_id for r in refs] == ["p1"]


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_notion_paginates_all_pages(respx_mock: respx.MockRouter) -> None:
    respx_mock.post(path="/v1/search").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "results": [{"id": "p1", "object": "page"}],
                    "has_more": True,
                    "next_cursor": "NC",
                },
            ),
            httpx.Response(
                200,
                json={
                    "results": [{"id": "p2", "object": "page"}],
                    "has_more": False,
                    "next_cursor": None,
                },
            ),
        ]
    )
    conn = _factory()
    ids = [ref.source_doc_id async for ref, _ in conn.list_changes(None)]
    assert ids == ["p1", "p2"]


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_notion_trashed_pages_surface_as_tombstones(respx_mock: respx.MockRouter) -> None:
    """in_trash / is_archived pages become deleted DocRefs, not live docs."""
    respx_mock.post(path="/v1/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {"id": "live", "object": "page", "in_trash": False, "is_archived": False},
                    {"id": "trashed", "object": "page", "in_trash": True, "is_archived": False},
                    {"id": "archived", "object": "page", "in_trash": False, "is_archived": True},
                ],
                "has_more": False,
                "next_cursor": None,
            },
        )
    )
    conn = _factory()
    seen = {ref.source_doc_id: ref.deleted async for ref, _ in conn.list_changes(None)}
    assert seen == {"live": False, "trashed": True, "archived": True}


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_notion_deletions_category(respx_mock: respx.MockRouter) -> None:
    """The shared deletions check: a trashed page must surface as a tombstone."""
    respx_mock.post(path="/v1/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [{"id": "gone", "object": "page", "in_trash": True}],
                "has_more": False,
                "next_cursor": None,
            },
        )
    )
    conn = _factory()
    await check_deletions(conn, deleted_state=None)


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_notion_full_scan_cursor_is_a_noop_replay(respx_mock: respx.MockRouter) -> None:
    """Replaying the full-scan sentinel yields nothing and hits no network."""
    route = respx_mock.post(path="/v1/search").mock(
        return_value=httpx.Response(200, json={"results": [], "has_more": False})
    )
    conn = _factory()
    yielded = [ref async for ref, _ in conn.list_changes("notion:full-scan")]
    assert yielded == []
    assert not route.called


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_notion_resolves_bot_principal_from_users_me(respx_mock: respx.MockRouter) -> None:
    """With no config override, the ACL principal is the bot id from /v1/users/me."""
    respx_mock.get(path="/v1/users/me").mock(
        return_value=httpx.Response(200, json={"object": "user", "id": "bot-live", "type": "bot"})
    )
    conn = NotionConnector(instance_id="acme", config={"token": "x"})
    acl = await conn.list_acls(conn._ref("p1"))
    assert acl.public is False
    assert acl.allow_users == ["bot-live"]
    assert acl.principal_ns == "acme"


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_notion_public_override_is_explicit(respx_mock: respx.MockRouter) -> None:
    """public=True is an explicit audited override, never the default."""
    default = NotionConnector(
        instance_id="acme", config={"token": "x", "integration_principal": "bot-acme"}
    )
    default_acl = await default.list_acls(default._ref("p1"))
    assert default_acl.public is False
    assert default_acl.allow_users == ["bot-acme"]

    override = NotionConnector(
        instance_id="acme",
        config={"token": "x", "integration_principal": "bot-acme", "public": True},
    )
    override_acl = await override.list_acls(override._ref("p1"))
    assert override_acl.public is True


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_notion_error_envelope_is_surfaced(respx_mock: respx.MockRouter) -> None:
    """A 404 object_not_found surfaces the Notion code, not an opaque error."""
    respx_mock.get(path="/v1/blocks/gone/children").mock(
        return_value=httpx.Response(
            404,
            json={"object": "error", "status": 404, "code": "object_not_found", "message": "nope"},
        )
    )
    conn = _factory()
    with pytest.raises(httpx.HTTPStatusError) as excinfo:
        await conn.fetch(conn._ref("gone"))
    assert "object_not_found" in str(excinfo.value)


@pytest.mark.conformance
@pytest.mark.permission
@respx.mock(assert_all_called=False)
async def test_notion_permissions(respx_mock: respx.MockRouter) -> None:
    # No HTTP for /v1/users/me: the factory pins integration_principal, so the ACL is
    # the integration-scoped grant (only the bot principal), deterministically.
    conn = _factory()
    await check_permissions(
        conn,
        source_acl=_FIX / "principals.json",
        principals=_FIX / "principals.json",
        expected=_FIX / "expected.json",
    )
