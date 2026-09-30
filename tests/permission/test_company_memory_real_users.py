"""The real-user profile: configured identities, `everyone`, and the `it-support` scope.

Identities come from the committed `users.example.json` (placeholder emails only, never
real ones), in their own tenant, through the same configured-user identity adapter,
MCP transport, publish policy, and SpiceDB path as the deployment. Synthetic data only.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _company_memory_demo import (  # noqa: E402
    REPO,
    STRANGER,
    USERS_EXAMPLE,
    build_demo,
    mcp_http,
    mcp_session,
    tool_json,
)
from company_memory_mcp import real_users as ru  # noqa: E402
from company_memory_mcp import service  # noqa: E402
from company_memory_mcp.publish_policy import PolicyError, parse_policy  # noqa: E402

pytestmark = pytest.mark.permission

PUBLISHER = "it-publisher@example.com"
READER = "reader@example.com"
TENANT = "door3"
GUIDE = (REPO / "deploy/company-memory/demo-input/it-support-quick-guide.md").read_text()
GUIDE_TITLE = "DOOR3 IT Support Quick Guide"
VPN_QUERY = "What is the name of the IT Support demo VPN profile?"
ADD = "add_company_memory_knowledge"
_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


async def _call(demo: Any, user: str, tool: str, args: dict[str, Any]) -> dict[str, Any]:
    async with mcp_session(demo, user) as s:
        return tool_json(await s.call_tool(tool, args))


async def _publish_guide(demo: Any, user: str = PUBLISHER, **kw: Any) -> dict[str, Any]:
    args = {"title": GUIDE_TITLE, "content": GUIDE, "scope": "it-support", **kw}
    return await _call(demo, user, ADD, args)


async def _search_ids(demo: Any, user: str, query: str = VPN_QUERY) -> set[str]:
    found = await _call(demo, user, "search_company_memory", {"query": query, "limit": 20})
    return {r["source_id"] for r in found["results"]}


def _manual_rows(demo: Any) -> list[Any]:
    return [r for r in demo.store.rows if r.doc_id.startswith("cm_manual:")]


# ---------------------------------------------------------------- identity


async def test_principal_is_the_verified_email_and_tenant_comes_from_the_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    demo = await build_demo(monkeypatch, tmp_path)
    # The IdP returns mixed case; the principal is always the canonical lowercase email.
    demo.identity.users[f"tok-{PUBLISHER}"] = demo.identity.users[f"tok-{PUBLISHER}"].model_copy(
        update={"email": "IT-Publisher@Example.COM", "id": "kc-uuid-1"}
    )
    me = await _call(demo, PUBLISHER, "get_company_memory_identity", {})
    assert me["principal"] == PUBLISHER and me["tenant"] == TENANT
    assert me["publishable_scopes"] == ["private", "it-support"]
    reader = await _call(demo, READER, "get_company_memory_identity", {})
    assert reader["principal"] == READER and reader["publishable_scopes"] == ["private"]
    added = await _publish_guide(demo)
    record = next(iter(demo.knowledge.records.values()))
    assert added["created_by"] == record.created_by == PUBLISHER
    assert record.tenant_id == TENANT


async def test_tool_arguments_cannot_change_creator_email_or_tenant(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    demo = await build_demo(monkeypatch, tmp_path)
    spoof = {
        "title": GUIDE_TITLE,
        "content": GUIDE,
        "scope": "it-support",
        "created_by": READER,
        "email": READER,
        "tenant_id": "default",
        "user": READER,
        "principal": READER,
    }
    assert (await _call(demo, PUBLISHER, ADD, spoof))["status"] == "added"
    record = next(iter(demo.knowledge.records.values()))
    assert record.created_by == PUBLISHER and record.tenant_id == TENANT
    assert {r.tenant_id for r in _manual_rows(demo)} == {TENANT}


# ---------------------------------------------------------------- authentication, everyone


async def test_unauthenticated_client_cannot_read_it_support(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    demo = await build_demo(monkeypatch, tmp_path)
    await _publish_guide(demo)
    call = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {"name": "search_company_memory", "arguments": {"query": VPN_QUERY}},
    }
    async with mcp_http(demo) as http:
        for headers in (_HEADERS, dict(_HEADERS, Authorization="Bearer forged")):
            resp = await http.post("/mcp", json=call, headers=headers)
            assert resp.status_code == 401 and "NORTHSTAR" not in resp.text
    rest = demo.rest.post("/api/search", json={"query": VPN_QUERY, "final_k": 20})
    assert rest.status_code == 401


@pytest.mark.parametrize("authz_only", [False, True], ids=["prefilter", "authz-only"])
async def test_every_configured_user_reads_it_support_and_rest_agrees(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, authz_only: bool
) -> None:
    demo = await build_demo(monkeypatch, tmp_path)
    demo.store.ignore_group_prefilter = authz_only
    doc = (await _publish_guide(demo))["source_id"]
    for user in (PUBLISHER, READER):
        found = await _call(demo, user, "search_company_memory", {"query": VPN_QUERY, "limit": 20})
        hit = next(r for r in found["results"] if r["source_id"] == doc)
        assert hit["source_type"] == "manual_mcp"
        source = await _call(demo, user, "get_company_memory_source", {"source_id": doc})
        assert "NORTHSTAR" in source["content"]
        async with mcp_session(demo, user) as s:
            evidence = tool_json(
                await s.call_tool(
                    "search_company_memory_evidence", {"query": VPN_QUERY, "limit": 20}
                )
            )
        assert {c["source_id"] for c in evidence["chunks"]} == demo.rest_ids(user, VPN_QUERY)


async def test_it_support_is_granted_to_the_everyone_group_not_to_the_tenant_or_public(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    demo = await build_demo(monkeypatch, tmp_path)
    doc = (await _publish_guide(demo))["source_id"]
    record = next(iter(demo.knowledge.records.values()))
    assert record.acl.allow_groups == ["everyone"] and record.acl.public is False
    readers = {t for t in demo.authz.readers if t[2] == doc}
    assert readers == {("group", "door3_everyone", doc)}
    # Synthetic users are also in a group named `everyone`, but in another tenant.
    for user in ("alice", "bob", "carol", "dave", "eve"):
        assert doc not in await _search_ids(demo, user)
        gone = await _call(demo, user, "get_company_memory_source", {"source_id": doc})
        assert gone["error"] == service.NOT_FOUND


async def test_authenticated_but_unconfigured_user_is_rejected_everywhere(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    demo = await build_demo(monkeypatch, tmp_path)
    await _publish_guide(demo)
    # A valid IdP account in the door3 tenant, not in `everyone` (even with a publisher
    # group): treated like an invalid token on MCP and on REST.
    init = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "t", "version": "0"},
        },
    }
    async with mcp_http(demo) as http:
        resp = await http.post(
            "/mcp", json=init, headers=dict(_HEADERS, Authorization=f"Bearer tok-{STRANGER}")
        )
        assert resp.status_code == 401
    rest = demo.rest.post(
        "/api/search",
        json={"query": VPN_QUERY, "final_k": 20},
        headers={"Authorization": f"Bearer tok-{STRANGER}"},
    )
    assert rest.status_code == 401
    # Removing a configured user from `everyone` ends their access on the next request.
    demo.set_groups(READER, [])
    rest = demo.rest.post(
        "/api/search",
        json={"query": VPN_QUERY, "final_k": 20},
        headers={"Authorization": f"Bearer tok-{READER}"},
    )
    assert rest.status_code == 401


@dataclass
class _Caller:
    principal: str
    groups: list[str] = field(default_factory=list)
    tenant_id: str = TENANT


async def test_even_past_the_gate_the_acl_requires_everyone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Defense in depth: were the configured-user gate misconfigured, a door3 account
    without `everyone` still could not read it-support (the SpiceDB check denies)."""
    demo = await build_demo(monkeypatch, tmp_path)
    doc = (await _publish_guide(demo))["source_id"]
    stranger = _Caller(STRANGER, ["it-support-publishers"])
    demo.store.ignore_group_prefilter = True
    found = await service.search(stranger, VPN_QUERY, 20)
    assert doc not in {r["source_id"] for r in found["results"]}
    assert (await service.get_source(stranger, doc))["error"] == service.NOT_FOUND


# ---------------------------------------------------------------- publishing


async def test_reader_cannot_publish_to_it_support_and_publisher_can(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    demo = await build_demo(monkeypatch, tmp_path)
    denied = await _publish_guide(demo, READER)
    assert denied["status"] == "denied"
    assert demo.knowledge.records == {} and _manual_rows(demo) == []
    assert (await _publish_guide(demo))["status"] == "added"
    # Revoking the publisher grant stops the next write, and keeps reading intact.
    demo.set_groups(PUBLISHER, ["everyone"])
    again = await _publish_guide(demo, title="Another guide")
    assert again["status"] == "denied"
    assert len(demo.knowledge.records) == 1


@pytest.mark.parametrize(
    "scope", ["leadership", "people-strategy", "project-atlas", "everyone", "engineering"]
)
async def test_it_support_publisher_cannot_publish_to_other_scopes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, scope: str
) -> None:
    demo = await build_demo(monkeypatch, tmp_path)
    # Even holding the group itself does not make an unlisted scope publishable.
    demo.set_groups(PUBLISHER, ["everyone", "it-support-publishers", scope])
    result = await _publish_guide(demo, scope=scope)
    assert result["status"] == "denied" and "source_id" not in result
    assert demo.knowledge.records == {} and _manual_rows(demo) == []


async def test_synthetic_tenant_users_cannot_publish_to_it_support(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    demo = await build_demo(monkeypatch, tmp_path)
    demo.set_groups("alice", ["everyone", "it-support-publishers"])
    assert (await _publish_guide(demo, "alice"))["status"] == "denied"


# ---------------------------------------------------------------- private knowledge


async def test_private_knowledge_stays_creator_only_although_everyone_is_shared(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    demo = await build_demo(monkeypatch, tmp_path)
    await _publish_guide(demo)
    private = await _call(
        demo, PUBLISHER, ADD, {"title": "My VPN notes", "content": "Personal NORTHSTAR notes."}
    )
    assert private["scope"] == "private"
    pid = private["source_id"]
    assert pid in await _search_ids(demo, PUBLISHER)
    assert pid not in await _search_ids(demo, READER)
    assert pid not in demo.rest_ids(READER, VPN_QUERY)
    denied = await _call(demo, READER, "get_company_memory_source", {"source_id": pid})
    assert denied["error"] == service.NOT_FOUND
    record = demo.knowledge.records[(TENANT, pid.rsplit(":", 1)[-1])]
    assert record.acl.allow_users == [PUBLISHER] and record.acl.allow_groups == []


# ---------------------------------------------------------------- configuration


def test_example_profile_uses_placeholder_emails_and_everyone() -> None:
    profile = ru.parse_profile(json.loads(USERS_EXAMPLE.read_text()))
    assert profile.tenant == TENANT
    assert all(u.email.endswith("@example.com") for u in profile.users)
    assert all(u.groups[0] == "everyone" for u in profile.users)
    assert "password" not in USERS_EXAMPLE.read_text().lower().replace("no passwords", "")


@pytest.mark.parametrize(
    "bad",
    [
        {"tenant": "default", "users": []},
        {"tenant": "door3", "users": [{"email": "not-an-email"}]},
        {
            "tenant": "door3",
            "users": [{"email": "a@example.com", "groups": ["cm-service-accounts"]}],
        },
        {"tenant": "door3", "users": [{"email": "a@example.com"}, {"email": "A@example.com"}]},
    ],
)
def test_profile_validation_rejects_bad_config(bad: dict[str, Any]) -> None:
    with pytest.raises(ru.UserConfigError):
        ru.parse_profile(bad)


def test_every_configured_user_is_put_in_everyone_and_rendered_with_the_tenant() -> None:
    profile = ru.parse_profile(
        {"tenant": "door3", "users": [{"email": "New.User@Example.com", "groups": ["x-team"]}]}
    )
    user = profile.users[0]
    assert user.email == "new.user@example.com" and user.groups == ["everyone", "x-team"]
    passwords = ru.ensure_passwords(profile, {})
    realm = ru.merge_into_realm({"groups": [], "users": []}, profile, passwords)
    rep = realm["users"][0]
    assert rep["username"] == rep["email"] == "new.user@example.com"
    assert rep["emailVerified"] is True and rep["attributes"] == {"tenant": ["door3"]}
    assert set(rep["groups"]) == {"/everyone", "/x-team"}
    assert {g["name"] for g in realm["groups"]} == {"everyone", "x-team"}
    assert ru.ensure_passwords(profile, passwords) == passwords  # stable across renders


def test_local_profile_and_generated_passwords_are_gitignored() -> None:
    ignore = (REPO / "deploy/company-memory/.gitignore").read_text()
    assert "users.local.json" in ignore and ".secrets/" in ignore
    docker = (REPO / "deploy/company-memory/Dockerfile.dockerignore").read_text()
    assert "users.local.json" not in docker.replace("#", "")  # whitelist: never included


def test_realm_tenant_attribute_is_admin_only_and_mapped_into_the_token() -> None:
    realm = json.loads(
        (REPO / "deploy/company-memory/keycloak/realm-company-memory.template.json").read_text()
    )
    up = json.loads(
        realm["components"]["org.keycloak.userprofile.UserProfileProvider"][0]["config"][
            "kc.user.profile.config"
        ][0]
    )
    tenant = next(a for a in up["attributes"] if a["name"] == "tenant")
    assert tenant["permissions"]["edit"] == ["admin"]  # users cannot change their tenant
    cli = next(c for c in realm["clients"] if c["clientId"] == "cm-cli")
    mapper = next(m for m in cli["protocolMappers"] if m["name"] == "tenant")
    assert mapper["config"]["user.attribute"] == "tenant"
    assert realm["registrationAllowed"] is False


def test_policy_everyone_may_read_but_never_publish() -> None:
    deployed = parse_policy(
        json.loads((REPO / "deploy/company-memory/publish-policy.json").read_text())
    )
    rule = deployed.scopes[TENANT]["it-support"]
    assert rule.read_groups == ("everyone",)
    assert rule.publisher_groups == frozenset({"it-support-publishers"})
    for bad in (
        {"it-support": {"publisher_groups": ["everyone"], "read_groups": ["everyone"]}},
        {"it-support": {"publisher_groups": ["p"], "read_groups": ["p"]}},
        {"it-support": {"publisher_groups": ["p"], "read_groups": ["__pansoph_everyone"]}},
        {"it-support": {"publisher_groups": ["p"], "read_groups": ["public"]}},
        {"it-support": {"publisher_groups": ["p"], "read_groups": []}},
    ):
        with pytest.raises(PolicyError):
            parse_policy({"version": 1, "tenants": {TENANT: bad}})
