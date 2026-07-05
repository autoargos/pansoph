"""Hermetic conformance for the salesforce connector, driven by respx mocks.

No live credentials: every Salesforce REST call the connector makes is mocked with a
representative payload. Because respx returns canned JSON regardless of the SOQL
string, a naive single mock cannot catch a wrong field name (the "passes in tests,
breaks live" trap). So the mock here inspects the actual SOQL and routes on it, and a
dedicated test pins the exact field names the connector must emit:

  - the listing query must SELECT SystemModstamp (the incremental cursor), and
  - the ACL query must read the correctly named "{Object}AccessLevel" column from
    "{Object}Share" (CaseShare.CaseAccessLevel), never a bare "AccessLevel", which
    exists only on custom objects and would return HTTP 400 on a standard object.

The permission category asserts the fail-closed ACL maps each share row to a
principal, a "005" prefixed UserOrGroupId to a user and any other id to a group.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from pansoph_connector_salesforce.connector import SalesforceConnector
from pansoph_connector_sdk import assert_conforms
from pansoph_connector_sdk.verify.categories.contract import check_spec
from pansoph_connector_sdk.verify.categories.permissions import check_permissions
from pansoph_types import DocRef

_QUERY_PATH = "/services/data/v67.0/query/"
_FIX = Path(__file__).parent / "fixtures" / "permissions"
_CONFIG = {
    "instance_url": "https://acme.my.salesforce.com",
    "token": "t",
    "object": "Case",
}
_DOC_ID = "500ABC000000000000"
_MODSTAMP = "2026-07-05T12:00:00.000+0000"
_SHARE_ROWS = [
    {"UserOrGroupId": "005alice0000000000", "CaseAccessLevel": "Read", "RowCause": "Manual"},
    {"UserOrGroupId": "00Gengteam00000000", "CaseAccessLevel": "Read", "RowCause": "Rule"},
]


def _factory() -> SalesforceConnector:
    return SalesforceConnector(instance_id="acme", config=_CONFIG)


def _query_router(seen: list[str] | None = None) -> Any:
    """Route a mocked SOQL query on its actual content, capturing each `q` seen."""

    def handler(request: httpx.Request) -> httpx.Response:
        soql = request.url.params["q"]
        if seen is not None:
            seen.append(soql)
        if "Share" in soql:  # the ACL query
            return httpx.Response(200, json={"records": _SHARE_ROWS, "done": True})
        if "SystemModstamp >" in soql:  # an incremental replay: nothing new
            return httpx.Response(200, json={"records": [], "done": True})
        return httpx.Response(  # the initial listing
            200,
            json={
                "records": [
                    {
                        "Id": _DOC_ID,
                        "SystemModstamp": _MODSTAMP,
                        "Subject": "s",
                        "Description": "d",
                    }
                ],
                "done": True,
            },
        )

    return handler


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_salesforce_conforms(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(path=_QUERY_PATH).mock(side_effect=_query_router())
    respx_mock.get(url__regex=r".*/sobjects/Case/.*").mock(
        return_value=httpx.Response(200, json={"Subject": "s", "Description": "d"})
    )
    await assert_conforms(_factory())


@pytest.mark.conformance
def test_salesforce_spec() -> None:
    check_spec(_factory())


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_salesforce_soql_field_names(respx_mock: respx.MockRouter) -> None:
    # Pin the exact SOQL the connector emits so a regression to the wrong field name
    # (the live-only INVALID_FIELD bug) fails here rather than silently in production.
    seen: list[str] = []
    respx_mock.get(path=_QUERY_PATH).mock(side_effect=_query_router(seen))
    conn = _factory()

    refs = [ref async for ref, _ in conn.list_changes(None)]
    await conn.list_acls(refs[0])

    listing = next(q for q in seen if "Share" not in q)
    acl = next(q for q in seen if "Share" in q)
    assert "SystemModstamp" in listing, "listing must select SystemModstamp for the cursor"
    assert "FROM CaseShare" in acl, "ACL must read the CaseShare table"
    assert "CaseAccessLevel" in acl, "ACL must read CaseAccessLevel, not a bare AccessLevel"
    # A bare "AccessLevel" token (the custom-object field) must never appear on a
    # standard-object share query; only the qualified CaseAccessLevel may.
    assert " AccessLevel" not in acl.replace("CaseAccessLevel", ""), (
        "standard-object share query must not reference a bare AccessLevel field"
    )


@pytest.mark.conformance
async def test_salesforce_yields_durable_cursor() -> None:
    # The cursor is the record's normalized SystemModstamp, non-empty and reusable.
    with respx.mock(assert_all_called=False) as respx_mock:
        respx_mock.get(path=_QUERY_PATH).mock(side_effect=_query_router())
        cursors = [cur async for _, cur in _factory().list_changes(None)]
    assert cursors == ["2026-07-05T12:00:00Z"]


@pytest.mark.conformance
async def test_salesforce_incremental_replay_is_empty() -> None:
    # Replaying with the emitted cursor issues a "SystemModstamp >" query, which the
    # router answers empty: no already-synced document is re-yielded.
    with respx.mock(assert_all_called=False) as respx_mock:
        seen: list[str] = []
        respx_mock.get(path=_QUERY_PATH).mock(side_effect=_query_router(seen))
        again = [ref async for ref, _ in _factory().list_changes("2026-07-05T12:00:00Z")]
    assert again == []
    assert any("SystemModstamp > 2026-07-05T12:00:00Z" in q for q in seen)


@pytest.mark.conformance
def test_salesforce_rejects_unmodeled_object() -> None:
    # Knowledge has no share-row ACL model, so it must be rejected, not silently
    # ingested with a wrong ACL.
    cfg: dict[str, Any] = {**_CONFIG, "object": "KnowledgeArticleVersion"}
    vr = SalesforceConnector(instance_id="acme", config=cfg).validate(cfg)
    assert not vr.ok
    assert "KnowledgeArticleVersion" in (vr.error or "")


@pytest.mark.conformance
async def test_salesforce_rejects_non_id() -> None:
    # A non-id source_doc_id must never be interpolated into SOQL.
    conn = _factory()
    with pytest.raises(ValueError, match="Salesforce record id"):
        await conn.list_acls(DocRef(source_doc_id="'; DROP--", instance_id="acme"))


@pytest.mark.conformance
@pytest.mark.permission
@respx.mock(assert_all_called=False)
async def test_salesforce_regress_2026_07_05_none_access_level_not_mapped(
    respx_mock: respx.MockRouter,
) -> None:
    # Regression for the over-share leak: the {Object}AccessLevel value must gate the
    # mapping. A share row whose level is "None" (a documented valid picklist value
    # that grants NO read to this object; it exists only to carry a sibling column's
    # implicit access) must NOT be mapped to an allow principal. The pre-fix loop read
    # only UserOrGroupId and dropped the level, so a "None" row leaked read access.
    leaked_user = "005leak00000000000"
    leaked_group = "00Gleakgroup000000"
    read_user = "005alice0000000000"
    rows = [
        # The leak: read/implicit-only rows that grant no read to the Case itself.
        {"UserOrGroupId": leaked_user, "CaseAccessLevel": "None", "RowCause": "ImplicitChild"},
        {"UserOrGroupId": leaked_group, "CaseAccessLevel": "None", "RowCause": "Rule"},
        # A genuine read grant must still be mapped.
        {"UserOrGroupId": read_user, "CaseAccessLevel": "Read", "RowCause": "Manual"},
    ]
    respx_mock.get(path=_QUERY_PATH).mock(
        return_value=httpx.Response(200, json={"records": rows, "done": True})
    )
    acl = await _factory().list_acls(DocRef(source_doc_id=_DOC_ID, instance_id="acme"))
    # The previously-leaked principals are now denied (absent from every allow list).
    assert leaked_user not in acl.allow_users, "None-level user must not be granted read"
    assert leaked_group not in acl.allow_groups, "None-level group must not be granted read"
    # The real read grant survives, so this is not a blanket deny.
    assert acl.allow_users == [read_user]
    assert acl.allow_groups == []
    assert acl.public is False


@pytest.mark.conformance
@pytest.mark.permission
@respx.mock(assert_all_called=False)
async def test_salesforce_permissions(respx_mock: respx.MockRouter) -> None:
    # list_acls issues only the {Object}Share query, so the share-row payload serves
    # it. A "005" prefixed principal is a user, any other id a group.
    respx_mock.get(path=_QUERY_PATH).mock(
        return_value=httpx.Response(200, json={"records": _SHARE_ROWS, "done": True})
    )
    await check_permissions(
        _factory(),
        source_acl=_FIX / "principals.json",
        principals=_FIX / "principals.json",
        expected=_FIX / "expected.json",
    )
