"""demo-reset and demo-check: deterministic reset, idempotency, and a read only preflight.

The reset core (`demo_state.reset`) runs against the hermetic demo stack: the real
ingestion pipeline, the real MCP app, and the shared fakes. The preflight's decisions
(`demo_check.evaluate`) are fed facts gathered from that same stack through MCP, then
broken one at a time. Placeholder identities only (users.example.json).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _company_memory_demo import (  # noqa: E402
    REPO,
    USERS_EXAMPLE,
    build_demo,
    mcp_session,
    tool_json,
)
from company_memory_mcp import cli_demo, demo_check, demo_state  # noqa: E402
from company_memory_mcp import real_users as ru  # noqa: E402
from company_memory_mcp.knowledge_store import CONNECTOR  # noqa: E402
from pansoph_core import assembly  # noqa: E402
from pansoph_retrieval.ports import SearchHit  # noqa: E402
from pansoph_types import AccessControl  # noqa: E402

pytestmark = pytest.mark.permission

PUBLISHER = "it-publisher@example.com"
ALL_INTERFACES = "0.0.0.0"  # noqa: S104  the unsafe binding the check must reject
READER = "reader@example.com"
GUIDE = (REPO / "deploy/company-memory/demo-input/it-support-quick-guide.md").read_text()
PROFILE = ru.parse_profile(json.loads(USERS_EXAMPLE.read_text()))
ADD = "add_company_memory_knowledge"


async def _call(demo: Any, user: str, tool: str, args: dict[str, Any]) -> dict[str, Any]:
    async with mcp_session(demo, user) as s:
        return tool_json(await s.call_tool(tool, args))


async def _publish_baseline(demo: Any) -> str:
    result = await _call(
        demo,
        PUBLISHER,
        ADD,
        {
            "title": demo_check.GUIDE_TITLE,
            "content": GUIDE,
            "description": cli_demo.GUIDE_DESCRIPTION,
            "tags": cli_demo.GUIDE_TAGS,
            "scope": "it-support",
            "original_filename": "it-support-quick-guide.md",
        },
    )
    assert result["status"] in ("added", "unchanged")
    return str(result["source_id"])


def _ports(demo: Any) -> demo_state.DemoPorts:
    memberships = assembly.build_membership_store()  # the harness's in-memory store

    async def chunk_ids() -> list[str]:
        return sorted({r.doc_id for r in demo.store.rows if r.doc_id.startswith("cm_manual:")})

    async def rows() -> list[tuple[str, str, set[str]]]:
        return [(t, s, set(g)) for (t, s), g in memberships._by_subject.items()]

    async def delete_row(tenant: str, subject: str) -> None:
        memberships._by_subject.pop((tenant, subject), None)

    return demo_state.DemoPorts(
        store=demo.knowledge,
        vector_store=demo.store,
        authz=demo.authz,
        acl_log=demo.acl_log,
        list_manual_chunk_doc_ids=chunk_ids,
        list_membership_rows=rows,
        delete_membership_row=delete_row,
    )


def _subjects() -> list[demo_state.Subject]:
    return [
        demo_state.Subject(s["subject"], s["tenant"], tuple(s["groups"]))
        for s in cli_demo.baseline_subjects(PROFILE)
    ]


async def _snapshot(demo: Any) -> dict[str, Any]:
    """Everything reset must converge: records, chunks, tuples, ACL log, memberships.
    Excludes write timestamps and engine tokens, which change on every write."""
    memberships = assembly.build_membership_store()
    acl_ids = await demo.acl_log.doc_ids_with_prefix("")
    return {
        "records": sorted(
            (r.doc_id, r.tenant_id, r.created_by, r.scope, r.updated_at.isoformat(), r.title)
            for r in demo.knowledge.records.values()
        ),
        "chunks": sorted((r.chunk_id, r.tenant_id, tuple(r.allow_groups)) for r in demo.store.rows),
        "readers": sorted(demo.authz.readers),
        "members": sorted(demo.authz.members),
        "acl_log": sorted(acl_ids),
        "memberships": sorted((k, tuple(sorted(v))) for k, v in memberships._by_subject.items()),
    }


async def _dirty_demo(demo: Any) -> str:
    """A stack after a messy live demo: interactive notes, temporary grants, a leftover
    verification user, and orphans from an interrupted write."""
    baseline = await _publish_baseline(demo)
    await _call(
        demo,
        PUBLISHER,
        ADD,
        {"title": "Printer", "content": "ORION-PRINT queue", "scope": "it-support"},
    )
    await _call(demo, PUBLISHER, ADD, {"title": "Desk", "content": "desk 4-117"})
    await _call(demo, READER, ADD, {"title": "Reader note", "content": "reader private"})
    await _call(
        demo,
        "alice",
        ADD,
        {"title": "Atlas auth", "content": "HARBOR-KEY", "scope": "project-atlas"},
    )
    await _call(demo, "eve", ADD, {"title": "t2", "content": "other tenant note"})
    # A temporary grant, used once (so SpiceDB holds bob in project-atlas).
    demo.set_groups("bob", ["everyone", "engineering", "project-beacon", "project-atlas"])
    await _call(demo, "bob", "search_company_memory", {"query": "atlas"})
    # A verification user that used the system and was then deleted from the IdP.
    from pansoph_identity.port import User

    temp = "cm-verify-reader-abc@company-memory.test"
    demo.identity.users[f"tok-{temp}"] = User(
        id="tmp", email=temp, group_ids=["everyone"], tenant_id="door3"
    )
    await _call(demo, temp, "search_company_memory", {"query": "vpn"})
    del demo.identity.users[f"tok-{temp}"]
    demo.set_groups("bob", ["everyone", "engineering", "project-beacon"])  # IdP reset
    # Orphans: a chunk and an ACL record with no source record behind them.
    orphan = "cm_manual:manual:k00000000000000000000dead"
    demo.store.rows.append(
        SearchHit(doc_id=orphan, chunk_id=f"{orphan}#0", score=0, tenant_id="door3", text="x")
    )
    await demo.acl_log.put(
        "cm_manual:manual:k00000000000000000000beef",
        AccessControl(allow_groups=["everyone"]),
        "door3",
        1,
    )
    return baseline


# ---------------------------------------------------------------- reset


async def test_reset_restores_the_known_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    demo = await build_demo(monkeypatch, tmp_path)
    corpus_before = sorted(r.chunk_id for r in demo.store.rows)
    baseline = await _dirty_demo(demo)
    summary = await demo_state.reset(_ports(demo), keep={baseline}, subjects=_subjects())
    assert len(summary["removed_manual_documents"]) == 5
    # Forgotten: the deleted verification user (it had queried, so it held memberships).
    assert summary["removed_orphans"] == 2 and summary["forgotten_subjects"] == 1
    # Manual knowledge: exactly the baseline, with its chunks and one reader tuple.
    assert [r.doc_id for r in demo.knowledge.records.values()] == [baseline]
    manual_rows = [r for r in demo.store.rows if r.doc_id.startswith("cm_manual:")]
    assert {r.doc_id for r in manual_rows} == {baseline}
    manual_readers = {t for t in demo.authz.readers if t[2].startswith("cm_manual:")}
    assert manual_readers == {("group", "door3_everyone", baseline)}
    assert await demo.acl_log.doc_ids_with_prefix("cm_manual:") == [baseline]
    # The synthetic corpus is untouched.
    corpus_after = sorted(
        r.chunk_id for r in demo.store.rows if r.doc_id.startswith("cm_synthetic:")
    )
    assert corpus_after == corpus_before
    # SpiceDB memberships: bob's temporary Atlas grant and the temp user are gone.
    members = demo.authz.members
    assert ("default_bob@company-memory.test", "default_project-atlas") not in members
    assert not any(u.startswith("door3_cm-verify-reader") for u, _ in members)
    assert ("door3_reader@example.com", "door3_everyone") in members
    assert ("default_alice@company-memory.test", "default_project-atlas") in members
    # And retrieval agrees: the reader still gets the guide, not the deleted notes.
    found = json.dumps(
        await _call(demo, READER, "search_company_memory", {"query": "vpn", "limit": 20})
    )
    assert "NORTHSTAR" in found and "ORION-PRINT" not in found and "4-117" not in found


async def test_reset_twice_gives_the_identical_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    demo = await build_demo(monkeypatch, tmp_path)
    await _dirty_demo(demo)
    # Exactly what `demo-reset` does each run: publish the baseline (idempotent), reset.
    first = await _publish_baseline(demo)
    await demo_state.reset(_ports(demo), keep={first}, subjects=_subjects())
    snap1 = await _snapshot(demo)
    second = await _publish_baseline(demo)
    summary = await demo_state.reset(_ports(demo), keep={second}, subjects=_subjects())
    snap2 = await _snapshot(demo)
    assert first == second
    assert snap1 == snap2
    assert summary["removed_manual_documents"] == [] and summary["removed_orphans"] == 0
    assert summary["forgotten_subjects"] == 0
    assert len([c for c in snap2["chunks"] if c[0].startswith("cm_manual:")]) == len(
        {c[0] for c in snap2["chunks"] if c[0].startswith("cm_manual:")}
    )  # no duplicate chunks


async def test_reset_without_a_publisher_removes_all_manual_knowledge(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    demo = await build_demo(monkeypatch, tmp_path)
    await _dirty_demo(demo)
    await demo_state.reset(_ports(demo), keep=set(), subjects=_subjects())
    assert demo.knowledge.records == {}
    assert not [r for r in demo.store.rows if r.doc_id.startswith("cm_manual:")]
    assert any(r.doc_id.startswith("cm_synthetic:") for r in demo.store.rows)


def test_reset_refuses_outside_the_local_demo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CM_DEMO_ENV", raising=False)
    with pytest.raises(demo_state.NotDemoEnvironment):
        demo_state.main(["reset"])
    with pytest.raises(demo_state.NotDemoEnvironment):
        demo_state.require_demo_environment({"CM_DEMO_ENV": "production"})
    demo_state.require_demo_environment({"CM_DEMO_ENV": "local-poc"})
    compose = (REPO / "deploy/company-memory/compose.yaml").read_text()
    assert "CM_DEMO_ENV: local-poc" in compose


def test_reset_keeps_configured_and_service_accounts_and_drops_temporary_users() -> None:
    names = [
        "alice",
        "bob",
        "carol",
        "dave",
        PUBLISHER,
        READER,
        "service-account-cm-seeder",
        "cm-verify-reader-1a2b3c@company-memory.test",
        "someone-added-by-hand@example.com",
    ]
    assert cli_demo.stale_users(names, [PUBLISHER, READER]) == [
        "cm-verify-reader-1a2b3c@company-memory.test",
        "someone-added-by-hand@example.com",
    ]
    subjects = cli_demo.baseline_subjects(PROFILE)
    assert {"subject": READER, "tenant": "door3", "groups": ["everyone"]} in subjects
    assert all(s["tenant"] == "default" for s in subjects if s["subject"].endswith(".test"))
    publisher = cli_demo._publisher(PROFILE)
    assert publisher is not None and publisher.email == PUBLISHER


def test_reset_never_writes_the_real_user_profile() -> None:
    source = (REPO / "packages/company-memory-mcp/src/company_memory_mcp/cli_demo.py").read_text()
    assert "save_profile" not in source and "USERS_LOCAL" not in source


# ---------------------------------------------------------------- check


async def test_inspect_is_read_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    demo = await build_demo(monkeypatch, tmp_path)
    await _dirty_demo(demo)
    before = await _snapshot(demo)
    report = await demo_state.inspect(_ports(demo))
    assert await _snapshot(demo) == before
    assert report["private_default_ok"] is True and report["spicedb_ok"] is True
    assert report["policy"]["door3"]["it-support"]["read_groups"] == ["everyone"]
    assert "HARBOR-KEY" not in json.dumps(report)  # no document text, only metadata


async def _healthy_facts(demo: Any, monkeypatch: pytest.MonkeyPatch) -> demo_check.Facts:
    monkeypatch.setenv("CM_DEMO_ENV", "local-poc")
    baseline = await _publish_baseline(demo)
    await demo_state.reset(_ports(demo), keep={baseline}, subjects=_subjects())
    inspect = await demo_state.inspect(_ports(demo))
    inspect["guard_reasons"] = []  # the test process is not the configured container
    async with mcp_session(demo, PUBLISHER) as s:
        listed = (await s.list_tools()).tools
        identity = tool_json(await s.call_tool("get_company_memory_identity", {}))
        found = tool_json(
            await s.call_tool(
                "search_company_memory",
                {"query": "What is the name of the IT Support demo VPN profile?", "limit": 5},
            )
        )
        hit = next(r for r in found["results"] if r["title"] == demo_check.GUIDE_TITLE)
        source = tool_json(
            await s.call_tool("get_company_memory_source", {"source_id": hit["source_id"]})
        )
    loopback = [{"URL": "127.0.0.1", "PublishedPort": 0, "TargetPort": 1}]
    containers = {
        name: {"state": "running", "health": "healthy", "publishers": []}
        for name in demo_check.REQUIRED_SERVICES
    }
    for name, port in demo_check.PUBLISHED.items():
        containers[name]["publishers"] = [{"URL": "127.0.0.1", "PublishedPort": port}]
    containers["postgres"]["publishers"] = loopback
    return demo_check.Facts(
        api_ok=True,
        mcp_status=401,
        mcp_resource_metadata=True,
        keycloak_ok=True,
        embedder_ok=True,
        containers=containers,
        inspect=inspect,
        agora_guard_reasons=[],
        grab_status=404,
        docs_status=404,
        user=PUBLISHER,
        configured_emails=[PUBLISHER, READER],
        configured_groups=["everyone", "it-support-publishers"],
        expected_tenant="door3",
        token_ok=True,
        claims={"email": PUBLISHER, "email_verified": True, "tenant": "door3"},
        live_groups=["everyone", "it-support-publishers"],
        mcp_identity=identity,
        tools={t.name: sorted((t.inputSchema or {}).get("properties") or {}) for t in listed},
        tools_unauthenticated_status=401,
        search_hit=hit,
        source=source,
    )


def _failed(facts: demo_check.Facts) -> set[str]:
    return {c.name for c in demo_check.evaluate(facts) if not c.ok}


async def test_check_passes_on_a_healthy_demo_stack(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    demo = await build_demo(monkeypatch, tmp_path)
    facts = await _healthy_facts(demo, monkeypatch)
    checks = demo_check.evaluate(facts)
    assert demo_check.ready(checks), [c for c in checks if not c.ok]
    text = demo_check.render(checks, PUBLISHER)
    assert text.rstrip().endswith("DEMO READY") and "tok-" not in text


@pytest.mark.parametrize(
    ("breakage", "expected"),
    [
        (
            lambda f: f.inspect.update(guard_reasons=["PANSOPH_REQUIRE_AUTH must be true"]),
            "Auth and identity config",
        ),
        (
            lambda f: setattr(
                f, "agora_guard_reasons", ["PANSOPH_IDENTITY_ADAPTER must be company_memory"]
            ),
            "Auth and identity config",
        ),
        (
            lambda f: f.containers["mcp"].update(
                publishers=[{"URL": ALL_INTERFACES, "PublishedPort": 8765}]
            ),
            "mcp loopback only",
        ),
        (
            lambda f: f.containers["agora"].update(
                publishers=[{"URL": ALL_INTERFACES, "PublishedPort": 8080}]
            ),
            "agora loopback only",
        ),
        (
            lambda f: f.containers["postgres"].update(
                publishers=[{"URL": "127.0.0.1", "PublishedPort": 5432}]
            ),
            "postgres not published",
        ),
        (lambda f: f.inspect.update(manual=[]), "IT Support baseline"),
        (lambda f: setattr(f, "user", "stranger@example.com"), "Signed-in real user"),
        (lambda f: setattr(f, "user", None), "Signed-in real user"),
        (
            lambda f: f.tools.update(
                add_company_memory_knowledge=["content", "tenant_id", "title"]
            ),
            "Tool credentials",
        ),
        (
            lambda f: f.tools.update(search_company_memory=["query", "bearer_token"]),
            "Tool credentials",
        ),
        (lambda f: f.tools.pop("get_company_memory_identity"), "MCP tools present"),
        (lambda f: setattr(f, "grab_status", 200), "Unsafe grab route"),
        (lambda f: setattr(f, "weak_local_secrets", ["KC_ADMIN_PASSWORD"]), "Generated secrets"),
        (lambda f: setattr(f, "live_groups", ["everyone"]), "IT Support publish"),
        (lambda f: f.claims.update(email_verified=False), "Verified email"),
        (lambda f: f.claims.update(tenant="default"), "Tenant door3"),
        (lambda f: f.inspect.update(policy_error="unreadable"), "Publish policy loaded"),
        (lambda f: f.inspect.update(private_default_ok=False), "Private default"),
        (lambda f: f.inspect["manual"][0]["indexed_acl"].update(public=True), "Not public"),
        (lambda f: setattr(f, "tools_unauthenticated_status", 200), "MCP needs a token"),
    ],
)
async def test_check_fails_on_each_unsafe_or_missing_condition(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, breakage: Any, expected: str
) -> None:
    demo = await build_demo(monkeypatch, tmp_path)
    facts = await _healthy_facts(demo, monkeypatch)
    breakage(facts)
    assert expected in _failed(facts)
    text = demo_check.render(demo_check.evaluate(facts), PUBLISHER)
    assert "DEMO NOT READY" in text and "fix:" in text


async def test_check_flags_leftover_demo_notes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    demo = await build_demo(monkeypatch, tmp_path)
    facts = await _healthy_facts(demo, monkeypatch)
    await _call(
        demo, PUBLISHER, ADD, {"title": "Printer", "content": "ORION-PRINT", "scope": "it-support"}
    )
    facts.inspect = await demo_state.inspect(_ports(demo))
    facts.inspect["guard_reasons"] = []
    assert _failed(facts) == {"Clean state"}


def test_check_command_exit_code_and_no_secrets(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli_demo, "_gather", lambda: demo_check.Facts(user=None))
    with pytest.raises(SystemExit) as exit_info:
        cli_demo.cmd_demo_check(None)  # type: ignore[arg-type]
    assert exit_info.value.code == 1
    out = capsys.readouterr().out
    assert "DEMO NOT READY" in out and "uv run company-memory login" in out
    assert "not checked: nobody signed in" in out
    assert "exposes identity in tool arguments" not in out  # not blamed on the server
    assert "Bearer" not in out and "password" not in out.lower()


def test_check_gathering_performs_no_writes() -> None:
    """The preflight never calls a write tool or an admin write."""
    source = (REPO / "packages/company-memory-mcp/src/company_memory_mcp/cli_demo.py").read_text()
    check_part = source[
        source.index(
            "# ---------------------------------------------------------------- demo-check"
        ) :
    ]
    for write in (
        "add_company_memory_knowledge",
        "delete_company_memory_knowledge",
        "_kc_apply_user",
        "_set_membership",
        '_state("reset"',
        '_state("forget"',
        "_store_tokens",
        "_login(",
        "kc.put",
        "kc.post",
        "kc.delete",
        ".write_text",
        "unlink",
    ):
        assert write not in check_part.split("def cmd_demo_ready")[0], write
    assert CONNECTOR == "cm_manual"
