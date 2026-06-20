"""Hermetic conformance for the salesforce connector, driven by respx mocks.

No live credentials: every Salesforce REST call the connector makes is mocked with a
representative payload. The permission category asserts the best effort ACL maps each
{Object}Share row to a principal, a "005" prefixed UserOrGroupId to a user and any
other id to a group.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx
from pansoph_connector_salesforce.connector import SalesforceConnector
from pansoph_connector_sdk.verify.categories.contract import check_discovery, check_spec
from pansoph_connector_sdk.verify.categories.permissions import check_permissions

_FIX = Path(__file__).parent / "fixtures" / "permissions"
_CONFIG = {
    "instance_url": "https://acme.my.salesforce.com",
    "token": "t",
    "object": "Case",
}


def _factory() -> SalesforceConnector:
    return SalesforceConnector(instance_id="acme", config=_CONFIG)


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_salesforce_discovery_and_spec(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(path="/services/data/v60.0/query/").mock(
        return_value=httpx.Response(200, json={"records": [{"Id": "500ABC"}], "done": True})
    )
    conn = _factory()
    check_spec(conn)
    refs = await check_discovery(conn)
    assert [r.source_doc_id for r in refs] == ["500ABC"]


@pytest.mark.conformance
@pytest.mark.permission
@respx.mock(assert_all_called=False)
async def test_salesforce_permissions(respx_mock: respx.MockRouter) -> None:
    # list_acls issues only the {Object}Share query against the query path, so a
    # single mock returning the share rows serves it.
    respx_mock.get(path="/services/data/v60.0/query/").mock(
        return_value=httpx.Response(
            200,
            json={
                "records": [
                    {"UserOrGroupId": "005alice", "AccessLevel": "Read"},
                    {"UserOrGroupId": "00Gengteam", "AccessLevel": "Read"},
                ],
                "done": True,
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
