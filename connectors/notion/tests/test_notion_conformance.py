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
async def test_notion_search_results_are_all_live_refs(respx_mock: respx.MockRouter) -> None:
    """POST /v1/search returns only live pages, so every ref is live (never deleted).

    The real Notion search endpoint EXCLUDES trashed pages, so a result set never
    carries in_trash=True. The connector must therefore emit only live refs and never
    a tombstone from a poll. See test_notion_conformance regression below.
    """
    respx_mock.post(path="/v1/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {"id": "a", "object": "page"},
                    {"id": "b", "object": "page"},
                ],
                "has_more": False,
                "next_cursor": None,
            },
        )
    )
    conn = _factory()
    seen = {ref.source_doc_id: ref.deleted async for ref, _ in conn.list_changes(None)}
    assert seen == {"a": False, "b": False}


@pytest.mark.conformance
async def test_notion_does_not_declare_deletions_capability() -> None:
    """deletions is honestly False: Notion exposes no usable bulk deletion feed."""
    conn = _factory()
    assert conn.meta.capabilities.deletions is False


@pytest.mark.conformance
@pytest.mark.permission
@respx.mock(assert_all_called=False)
async def test_regress_2026_07_05_notion_no_phantom_deletion_tombstone(
    respx_mock: respx.MockRouter,
) -> None:
    """Pins the deletion-honesty non-leak from the 2026-07-05 over-share audit.

    The leak: capabilities.deletions was True, backed only by an _is_trashed check on
    POST /v1/search results. But the real Notion API EXCLUDES trashed pages from
    search (https://developers.notion.com/reference/post-search, confirmed by
    makenotion/notion-sdk-js issue #524), so the tombstone path was dead in
    production, True-but-never-firing. A page trashed or un-shared in Notion would
    just vanish from search, emit no tombstone, and persist in the index under its old
    grant, so a revoked document could still be retrieved. That violates pansoph
    non-negotiable rule 1 (revocations must propagate; a stale read must never leak a
    revoked document).

    This test reconstructs the exact scenario against the REAL API behavior and pins
    the fix so the phantom capability cannot silently return:

    1. Run 1 lists p_live and p_gone as live. p_gone is then trashed in Notion.
    2. Run 2 models the real search response: the trashed page is ABSENT (not present
       with in_trash=True). The connector yields only p_live, no tombstone, because it
       does not claim a capability the API cannot back.
    3. capabilities.deletions is False, so the runtime knows it must reconcile the
       full-scan listing to purge p_gone rather than trust a dead tombstone feed.
    4. Fail-closed guard: even before reconciliation, the stale p_gone doc carries
       only the integration-scoped grant, so no end-user principal is granted it. Any
       principal that is not the integration bot is DENIED. It under-shares (safe),
       never over-shares.
    """
    conn = _factory()

    # Run 1: both pages live.
    run1 = respx_mock.post(path="/v1/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {"id": "p_live", "object": "page"},
                    {"id": "p_gone", "object": "page"},
                ],
                "has_more": False,
                "next_cursor": None,
            },
        )
    )
    run1_ids = [ref.source_doc_id async for ref, _ in conn.list_changes(None)]
    assert run1_ids == ["p_live", "p_gone"]

    # Run 2: p_gone is trashed, so the real search response OMITS it entirely. It is
    # never returned carrying in_trash=True.
    run1.mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [{"id": "p_live", "object": "page"}],
                "has_more": False,
                "next_cursor": None,
            },
        )
    )
    run2 = [(ref.source_doc_id, ref.deleted) async for ref, _ in conn.list_changes(None)]

    # The trashed page produces NO tombstone: the connector never fabricates a delete
    # the API cannot signal. It is simply absent, to be purged by reconciliation.
    assert run2 == [("p_live", False)]
    assert all(not deleted for _, deleted in run2), "no phantom tombstone may be emitted"

    # The capability is honestly False, so the runtime reconciles instead of trusting
    # a dead tombstone feed.
    assert conn.meta.capabilities.deletions is False

    # Fail-closed: the stale p_gone doc, if left in the index, grants only the
    # integration principal. A real end user (not the bot) is DENIED, so it
    # under-shares rather than leaking a revoked document.
    stale_acl = await conn.list_acls(conn._ref("p_gone"))
    assert stale_acl.public is False
    assert stale_acl.allow_users == ["bot-acme"]
    assert "end-user-eve" not in stale_acl.allow_users


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
