"""Hermetic conformance for the SharePoint connector, driven by respx mocks.

No live credentials: every Microsoft Graph call the connector makes is mocked with a
representative payload. Discovery and change tracking run against the delta feed
(GET /drives/{id}/root/delta), which walks the full hierarchy, carries the durable
@odata.deltaLink cursor, and surfaces removals through the "deleted" facet. The
permission category asserts a driveItem's ACL maps its grantedToV2 user grants to
allow_users and group grants to allow_groups, namespaced by the instance. respx matches
on path only (query strings are ignored), so the @odata pagination and delta tokens do
not affect the matches.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx
from pansoph_connector_sdk.conformance import assert_conforms
from pansoph_connector_sdk.verify.categories.contract import check_discovery, check_spec
from pansoph_connector_sdk.verify.categories.permissions import check_permissions
from pansoph_connector_sdk.verify.principals import Principal, can_see
from pansoph_connector_sharepoint.connector import _ORG_MEMBER_GROUP, SharepointConnector
from pansoph_types import DocRef

_FIX = Path(__file__).parent / "fixtures" / "permissions"
_CONFIG = {"token": "x", "drive_id": "D1", "base_url": "https://graph.microsoft.com"}

# A single terminal delta page: one live file plus one tombstone, with the deltaLink
# present and no nextLink, so every yielded ref carries a non-empty durable cursor.
_DELTA_TERMINAL = {
    "value": [
        {
            "id": "I1",
            "name": "a.docx",
            "file": {
                "mimeType": (
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                )
            },
        },
        {"id": "GONE", "name": "old.txt", "deleted": {"state": "deleted"}},
    ],
    "@odata.deltaLink": "https://graph.microsoft.com/v1.0/drives/D1/root/delta?token=NEXT",
}


def _factory() -> SharepointConnector:
    return SharepointConnector(instance_id="contoso", config=_CONFIG)


# The change set returned when the saved deltaLink (token=NEXT) is replayed: empty, since
# nothing changed since the seed page. This is how a real delta resume behaves and lets
# the incremental replay assert no already-seen id is re-emitted.
_DELTA_REPLAY_EMPTY = {
    "value": [],
    "@odata.deltaLink": "https://graph.microsoft.com/v1.0/drives/D1/root/delta?token=NEXT2",
}


def _mock_delta(respx_mock: respx.MockRouter) -> None:
    # The initial enumeration (no token) returns the seed page. Replaying the deltaLink
    # (token=NEXT) returns an empty change set. respx picks the most specific route first,
    # so the token=NEXT match takes precedence over the token-less initial route.
    respx_mock.get(url__regex=r".*/v1\.0/drives/D1/root/delta\?token=NEXT$").mock(
        return_value=httpx.Response(200, json=_DELTA_REPLAY_EMPTY)
    )
    respx_mock.get(path="/v1.0/drives/D1/root/delta").mock(
        return_value=httpx.Response(200, json=_DELTA_TERMINAL)
    )


def _mock_item_and_permissions(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(path="/v1.0/drives/D1/items/I1").mock(
        return_value=httpx.Response(
            200,
            json={"id": "I1", "name": "a.docx", "webUrl": "https://x/a.docx", "file": {}},
        )
    )
    respx_mock.get(path="/v1.0/drives/D1/items/I1/permissions").mock(
        return_value=httpx.Response(
            200,
            json={
                "value": [
                    {"grantedToV2": {"user": {"id": "alice@x.com"}}},
                    {"grantedToV2": {"group": {"id": "eng"}}},
                ]
            },
        )
    )


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_sharepoint_discovery_and_spec(respx_mock: respx.MockRouter) -> None:
    _mock_delta(respx_mock)
    conn = _factory()
    check_spec(conn)
    refs = await check_discovery(conn)
    assert [r.source_doc_id for r in refs] == ["I1", "GONE"]


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_sharepoint_assert_conforms(respx_mock: respx.MockRouter) -> None:
    """The shared contract: seeded delta feed, fetch, list_acls, and the incremental
    replay must all hold. This is the gate that catches a false capability flag."""
    _mock_delta(respx_mock)
    _mock_item_and_permissions(respx_mock)
    conn = _factory()
    await assert_conforms(conn)


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_sharepoint_deletions_emit_tombstone(respx_mock: respx.MockRouter) -> None:
    """A delta item carrying the "deleted" facet is yielded as a tombstone (deleted=True),
    and the durable cursor is the @odata.deltaLink. This pins the deletions=True claim."""
    _mock_delta(respx_mock)
    conn = _factory()
    results = [(ref, cursor) async for ref, cursor in conn.list_changes(None)]

    by_id = {ref.source_doc_id: ref for ref, _ in results}
    assert by_id["I1"].deleted is False
    assert by_id["GONE"].deleted is True
    # Every yielded item on the terminal page carries the deltaLink as its durable cursor.
    assert all(
        cursor == "https://graph.microsoft.com/v1.0/drives/D1/root/delta?token=NEXT"
        for _, cursor in results
    )


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_sharepoint_incremental_uses_saved_cursor(respx_mock: respx.MockRouter) -> None:
    """When a cursor is supplied, list_changes resumes from the saved deltaLink URL rather
    than starting a fresh root enumeration, so incremental=True is honest."""
    saved = "https://graph.microsoft.com/v1.0/drives/D1/root/delta?token=SAVED"
    route = respx_mock.get(url=saved).mock(
        return_value=httpx.Response(
            200,
            json={
                "value": [{"id": "I2", "name": "b.docx", "file": {}}],
                "@odata.deltaLink": (
                    "https://graph.microsoft.com/v1.0/drives/D1/root/delta?token=NEXT"
                ),
            },
        )
    )
    conn = _factory()
    refs = [ref async for ref, _ in conn.list_changes(saved)]

    assert route.called, "incremental sync must GET the saved deltaLink, not root/delta"
    assert [r.source_doc_id for r in refs] == ["I2"]


@pytest.mark.conformance
@pytest.mark.permission
@respx.mock(assert_all_called=False)
async def test_sharepoint_org_scope_link_does_not_leak_to_all_tenant_members(
    respx_mock: respx.MockRouter,
) -> None:
    """Regression: an "organization" scope sharing link must NOT map to public=True.

    Org scope means "tenant members WHO HOLD THE LINK" (excluding B2B guests), not all
    tenant members. The prior mapping set public=True, which let can_see() return True for
    every principal in the namespace, including a tenant member who never received the link
    and a guest. This pins the fail-closed mapping: the item is granted only to the
    namespaced membership group _ORG_MEMBER_GROUP (which under-shares until the identity
    layer materializes it), public stays False, and a link-less principal is DENIED. If
    org-scope ever regresses to public=True this test fails, so the leak cannot return.
    """
    respx_mock.get(path="/v1.0/drives/D1/items/ORG/permissions").mock(
        return_value=httpx.Response(
            200,
            json={"value": [{"link": {"scope": "organization", "type": "view"}}]},
        )
    )
    conn = _factory()
    acl = await conn.list_acls(DocRef(source_doc_id="ORG", instance_id="contoso"))

    # The over-share this fix closes: org-scope must never be world/tenant public.
    assert acl.public is False
    # It fails closed to the stable namespaced membership group, nothing broader.
    assert acl.allow_groups == [_ORG_MEMBER_GROUP]
    assert acl.allow_users == []

    # A tenant member who lacks the link (and thus lacks the materialized group) is DENIED,
    # which is exactly the principal the previous public=True mapping leaked to.
    link_less_member = Principal(user_id="bob@contoso.com", groups=[])
    assert can_see(acl, link_less_member) is False
    # A B2B guest, whom org scope excludes entirely, is likewise DENIED.
    guest = Principal(user_id="guest@partner.com", groups=[])
    assert can_see(acl, guest) is False
    # Only a principal the identity layer has placed in the org-members group can see it.
    materialized_member = Principal(user_id="alice@contoso.com", groups=[_ORG_MEMBER_GROUP])
    assert can_see(acl, materialized_member) is True


@pytest.mark.conformance
@pytest.mark.permission
@respx.mock(assert_all_called=False)
async def test_sharepoint_anonymous_scope_link_stays_public(
    respx_mock: respx.MockRouter,
) -> None:
    """Guard the other side: "anonymous" scope is genuinely world-readable, so it must
    still map to public=True. This keeps the fail-closed org-scope fix from over-correcting
    anonymous links into an under-share."""
    respx_mock.get(path="/v1.0/drives/D1/items/PUB/permissions").mock(
        return_value=httpx.Response(
            200,
            json={"value": [{"link": {"scope": "anonymous", "type": "view"}}]},
        )
    )
    conn = _factory()
    acl = await conn.list_acls(DocRef(source_doc_id="PUB", instance_id="contoso"))

    assert acl.public is True
    assert _ORG_MEMBER_GROUP not in acl.allow_groups


@pytest.mark.conformance
@pytest.mark.permission
@respx.mock(assert_all_called=False)
async def test_sharepoint_permissions(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(path="/v1.0/drives/D1/items/I1/permissions").mock(
        return_value=httpx.Response(
            200,
            json={
                "value": [
                    {"grantedToV2": {"user": {"id": "alice@x.com"}}},
                    {"grantedToV2": {"group": {"id": "eng"}}},
                ]
            },
        )
    )
    conn = _factory()
    await check_permissions(
        conn,
        source_acl=_FIX / "principals.json",
        principals=_FIX / "principals.json",
        expected=_FIX / "expected.json",
    )
