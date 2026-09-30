"""The Company Memory MCP: transport authentication and permission enforcement.

Every test drives the real MCP ASGI app over HTTP (the MCP SDK's bearer middleware plus
the pansoph token verifier), with the real synthetic corpus ingested through the real
connector and pipeline. These are the Phase 11 properties the live demo relies on.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _company_memory_demo import (  # noqa: E402
    DEMO_QUERY,
    EXPECTED,
    FORBIDDEN,
    MARKERS,
    build_demo,
    doc_id,
    mcp_http,
    mcp_session,
    stems,
    tool_json,
)
from pansoph_authz import Principal  # noqa: E402

pytestmark = pytest.mark.permission

_INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "0"},
    },
}
_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
_CREDENTIAL_WORDS = ("token", "bearer", "auth", "credential", "password", "secret", "key")


async def _search(demo, user: str, query: str = DEMO_QUERY, limit: int = 20) -> dict:  # type: ignore[no-untyped-def]
    async with mcp_session(demo, user) as s:
        return tool_json(
            await s.call_tool("search_company_memory", {"query": query, "limit": limit})
        )


async def _source(demo, user: str, source_id: str) -> dict:  # type: ignore[no-untyped-def]
    async with mcp_session(demo, user) as s:
        return tool_json(await s.call_tool("get_company_memory_source", {"source_id": source_id}))


@pytest.mark.parametrize("auth", [None, "Bearer not-a-valid-token", "Basic YWxpY2U6YWxpY2U="])
async def test_unauthenticated_or_invalid_mcp_request_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, auth: str | None
) -> None:
    demo = await build_demo(monkeypatch, tmp_path)
    headers = dict(_HEADERS, **({"Authorization": auth} if auth else {}))
    async with mcp_http(demo) as http:
        resp = await http.post("/mcp", json=_INIT, headers=headers)
        assert resp.status_code == 401
        assert "resource_metadata" in resp.headers.get("www-authenticate", "")
        tools = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
        assert (await http.post("/mcp", json=tools, headers=headers)).status_code == 401
    for marker in MARKERS["atlas"] + MARKERS["leadership"]:
        assert marker not in resp.text


async def test_no_tool_schema_has_a_credential_parameter(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    demo = await build_demo(monkeypatch, tmp_path)
    async with mcp_session(demo, "alice") as s:
        tools = (await s.list_tools()).tools
    names = {t.name for t in tools}
    assert names == {
        "search_company_memory",
        "search_company_memory_evidence",
        "get_company_memory_source",
        "add_company_memory_knowledge",
        "list_my_company_memory_knowledge",
        "delete_company_memory_knowledge",
        "get_company_memory_identity",
    }
    for tool in tools:
        for prop in tool.inputSchema.get("properties") or {}:
            assert not any(w in prop.lower() for w in _CREDENTIAL_WORDS), (tool.name, prop)
    # The upstream pansoph MCP tool no longer takes a token either (CM-H6).
    from mcp.server.auth.settings import AuthSettings
    from pansoph_core.mcp_server import build_mcp

    upstream = build_mcp(
        auth=AuthSettings(
            issuer_url="https://idp.example.test/r",  # type: ignore[arg-type]
            resource_server_url="http://localhost:8000/mcp",  # type: ignore[arg-type]
        ),
        token_verifier=object(),
    )
    for tool in await upstream.list_tools():
        assert set(tool.inputSchema["properties"]) == {"query"}


@pytest.mark.parametrize("user", ["alice", "bob", "carol", "dave"])
@pytest.mark.parametrize("authz_only", [False, True], ids=["prefilter", "authz-only"])
async def test_same_query_returns_only_each_users_authorized_sources(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, authz_only: bool, user: str
) -> None:
    demo = await build_demo(monkeypatch, tmp_path)
    demo.store.ignore_group_prefilter = authz_only
    result = await _search(demo, user)
    assert result["principal"] == f"{user}@company-memory.test"
    assert stems({r["source_id"] for r in result["results"]}) == EXPECTED[user]
    text = json.dumps(result)
    for marker in FORBIDDEN[user]:
        assert marker not in text, f"{user} received {marker!r}"


@pytest.mark.parametrize("user", ["alice", "bob", "carol", "dave"])
@pytest.mark.parametrize("authz_only", [False, True], ids=["prefilter", "authz-only"])
async def test_mcp_and_rest_produce_the_same_authorization_result(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, authz_only: bool, user: str
) -> None:
    demo = await build_demo(monkeypatch, tmp_path)
    demo.store.ignore_group_prefilter = authz_only
    async with mcp_session(demo, user) as s:
        evidence = tool_json(
            await s.call_tool("search_company_memory_evidence", {"query": DEMO_QUERY, "limit": 20})
        )
    mcp_ids = {c["source_id"] for c in evidence["chunks"]}
    assert mcp_ids == demo.rest_ids(user) == {doc_id(s) for s in EXPECTED[user]}


async def test_alice_cannot_retrieve_bob_only_content(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    demo = await build_demo(monkeypatch, tmp_path)
    result = await _search(demo, "alice", "Beacon database incident INC-4471 replication lag")
    text = json.dumps(result)
    assert "beacon-postmortem" not in text and "INC-4471" not in text


async def test_document_id_does_not_bypass_the_acl(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    demo = await build_demo(monkeypatch, tmp_path)
    own = await _source(demo, "alice", doc_id("atlas-adr-007-authentication"))
    assert "PASSKEY-FIRST" in own["content"]
    denied = await _source(demo, "alice", doc_id("leadership-fy27-financial-plan"))
    missing = await _source(demo, "alice", doc_id("no-such-document"))
    # Denied and nonexistent are indistinguishable: no existence oracle, no metadata.
    assert denied["error"] == missing["error"]
    assert set(denied) == {"source_id", "error"}
    assert "7.3M" not in json.dumps(denied)


@pytest.mark.parametrize("authz_only", [False, True], ids=["prefilter", "authz-only"])
async def test_revoked_group_membership_removes_access_on_the_same_query(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, authz_only: bool
) -> None:
    demo = await build_demo(monkeypatch, tmp_path)
    demo.store.ignore_group_prefilter = authz_only
    query = "What authentication architecture does Project Atlas use?"
    atlas = doc_id("atlas-adr-007-authentication")
    before = await _search(demo, "alice", query)
    assert atlas in {r["source_id"] for r in before["results"]}
    # An admin removes alice from project-atlas in the IdP. Same token, same query.
    demo.set_groups("alice", ["everyone", "engineering"])
    after = await _search(demo, "alice", query)
    text = json.dumps(after)
    for marker in MARKERS["atlas"]:
        assert marker not in text
    assert "atlas-adr-007" not in text and "atlas-architecture" not in text
    assert (await _source(demo, "alice", atlas))["error"]
    assert atlas not in demo.rest_ids("alice", query)
    # The authz engine itself forgot the membership, not just the vector pre-filter (CM-H1).
    allowed = await demo.authz.bulk_check(
        Principal(type="user", id="alice@company-memory.test", ns="default"), "read", [atlas]
    )
    assert atlas not in allowed


async def test_source_acl_change_propagates_after_resync(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    demo = await build_demo(monkeypatch, tmp_path)
    path = demo.corpus / "atlas-adr-007-authentication.md"
    path.write_text(
        path.read_text().replace("allow_groups: project-atlas", "allow_groups: leadership")
    )
    await demo.sync()
    atlas = doc_id("atlas-adr-007-authentication")
    assert atlas not in {r["source_id"] for r in (await _search(demo, "alice"))["results"]}
    assert "error" in await _source(demo, "alice", atlas)
    assert "PASSKEY-FIRST" in (await _source(demo, "carol", atlas))["content"]


async def test_deleted_content_cannot_be_retrieved(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    demo = await build_demo(monkeypatch, tmp_path)
    beacon = doc_id("beacon-postmortem-inc-4471")
    assert "INC-4471" in (await _source(demo, "bob", beacon))["content"]
    (demo.corpus / "beacon-postmortem-inc-4471.md").unlink()
    await demo.sync()
    assert "INC-4471" not in json.dumps(await _search(demo, "bob", "database incident"))
    assert "error" in await _source(demo, "bob", beacon)
    assert beacon not in demo.rest_ids("bob")


async def test_cross_tenant_access_is_denied(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    demo = await build_demo(monkeypatch, tmp_path)
    # eve holds the same group names (everyone, engineering) but in tenant t2.
    result = await _search(demo, "eve")
    assert result["results"] == []
    assert "error" in await _source(demo, "eve", doc_id("engineering-guidelines"))


async def test_audit_records_who_what_and_decision_without_tokens(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    demo = await build_demo(monkeypatch, tmp_path)
    with caplog.at_level(logging.INFO, logger="pansoph.audit"):
        await _search(demo, "bob")
        await _source(demo, "bob", doc_id("leadership-fy27-financial-plan"))
    events = [json.loads(r.getMessage()) for r in caplog.records if r.name == "pansoph.audit"]
    tools = [e for e in events if e["action"] == "mcp_tool"]
    search = next(e for e in tools if e["tool"] == "search_company_memory")
    assert search["actor"] == "bob@company-memory.test" and search["tenant"] == "default"
    assert set(stems(set(search["returned"]))) == EXPECTED["bob"]
    denied = next(e for e in tools if e["tool"] == "get_company_memory_source")
    assert denied["decision"] == "denied" and denied["returned"] == []
    assert all("ts" in e for e in events)
    assert "tok-bob" not in caplog.text
