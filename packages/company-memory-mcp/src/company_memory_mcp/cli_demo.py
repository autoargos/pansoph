"""`company-memory demo-reset`, `demo-check`, and `demo-ready`: a repeatable live demo.

demo-reset (local POC only) restores a known state through supported paths:
  Keycloak     synthetic users get exactly their baseline groups; configured real users
               exactly their `users.local.json` groups; users that are neither (leftover
               temporary accounts) are deleted. `users.local.json` is never modified.
  corpus       the synthetic connector instance is ensured and synced (idempotent).
  knowledge    the baseline "DOOR3 IT Support Quick Guide" is published through MCP as the
               first configured it-support publisher (same content, same id, every run);
               then every other manually added document is purged, orphans are swept,
               and SpiceDB memberships are reconciled to the baseline (demo_state).
  local state  token files of users that no longer exist are removed.

demo-check is read only (see demo_check.py): it never adds, deletes, grants, or revokes.
Its MCP reads are ordinary queries, which reconcile the caller's own group memberships
exactly as any search does.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import time
from typing import Any

import httpx

from . import demo_check as dc
from . import demo_data as dd
from . import real_users as ru
from .cli import (
    AGORA,
    KEYCLOAK,
    MCP_URL,
    REALM,
    REPO,
    STACK_ENV,
    STATE_DIR,
    TOKEN_URL,
    USERINFO_URL,
    USERS,
    CliError,
    _claims,
    _compose,
    _current_user,
    _ensure_groups,
    _get_ok,
    _kc_admin,
    _load_env,
    _mcp_call,
    _profile,
    _real_passwords,
    _reset_memberships,
    _say,
    _seed_corpus,
    _wait_ready,
)
from .cli_real import _kc_apply_user, _kc_user_id

GUIDE_FILE = REPO / "deploy" / "company-memory" / "demo-input" / "it-support-quick-guide.md"
GUIDE_DESCRIPTION = "Synthetic demo IT Support procedures for the local Company Memory POC."
GUIDE_TAGS = ["it-support", "synthetic-demo", "demo-baseline"]
SEEDER_ACCOUNT = "service-account-cm-seeder"
_WEAK = {"", "change-me", "changeme", "pansoph", "pansoph_local_dev", "admin", "password"}


# ---------------------------------------------------------------- shared helpers


def _containers() -> dict[str, dict[str, Any]]:
    """This compose project's containers only (never other projects on the host)."""
    raw = _compose("ps", "--all", "--format", "json", capture=True, check=False)
    out: dict[str, dict[str, Any]] = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        items = json.loads(line)
        for item in items if isinstance(items, list) else [items]:
            out[str(item.get("Service"))] = {
                "state": item.get("State"),
                "health": item.get("Health"),
                "publishers": item.get("Publishers") or [],
            }
    return out


def _state(action: str, service: str = "mcp", plan: dict[str, Any] | None = None) -> Any:
    """Run demo_state inside a container; returns its JSON output."""
    cmd = ["exec", "-T", service, "python", "-m", "company_memory_mcp.demo_state", action]
    base = ["docker", "compose", "-f", str(REPO / "deploy/company-memory/compose.yaml")]
    res = subprocess.run(  # noqa: S603
        [*base, "--env-file", str(STACK_ENV), *cmd],
        input=json.dumps(plan or {}),
        text=True,
        capture_output=True,
        check=False,
    )
    if res.returncode != 0:
        tail = (res.stderr or res.stdout).strip().splitlines()[-1:] or ["no output"]
        raise CliError(f"demo_state {action} in {service} failed: {tail[0][:300]}")
    return json.loads(res.stdout)


def _publisher(profile: ru.RealUserProfile) -> ru.RealUser | None:
    """The first configured it-support publisher (config order, so deterministic)."""
    return next((u for u in profile.users if dc.PUBLISHERS in u.groups), None)


def stale_users(usernames: list[str], configured: list[str]) -> list[str]:
    """Realm accounts that are neither synthetic, configured, nor service accounts."""
    keep = set(USERS) | set(configured)
    return sorted(u for u in usernames if u not in keep and not u.startswith("service-account-"))


def baseline_subjects(profile: ru.RealUserProfile) -> list[dict[str, Any]]:
    """Every known subject with its baseline groups, as SpiceDB should hold them."""
    subjects = [
        {"subject": dd.email(u), "tenant": dd.TENANT, "groups": groups}
        for u, groups in dd.GROUPS.items()
    ]
    subjects += [
        {"subject": u.email, "tenant": profile.tenant, "groups": list(u.groups)}
        for u in profile.users
    ]
    return subjects


# ---------------------------------------------------------------- demo-reset


def _require_local_demo() -> dict[str, dict[str, Any]]:
    if not STACK_ENV.exists():
        raise CliError("refusing: no local demo secrets (run `uv run company-memory init`)")
    containers = _containers()
    missing = [s for s in dc.REQUIRED_SERVICES if containers.get(s, {}).get("state") != "running"]
    if missing:
        raise CliError(
            f"refusing: the local demo stack is not running ({', '.join(missing)}); "
            "start it with `uv run company-memory up`"
        )
    for host in (AGORA, MCP_URL, KEYCLOAK):
        if not host.startswith(("http://localhost:", "http://127.0.0.1:")):
            raise CliError(f"refusing: {host} is not a local demo endpoint")
    return containers


def _reset_keycloak(profile: ru.RealUserProfile) -> list[str]:
    passwords = _real_passwords(profile)
    _reset_memberships()  # synthetic users: exactly their baseline groups
    with _kc_admin() as kc:
        _ensure_groups(kc)
        for user in profile.users:
            _kc_apply_user(kc, user, profile.tenant, passwords[user.email])
        names = [u["username"] for u in kc.get("/users", params={"max": "1000"}).json()]
        removed = stale_users(names, [u.email for u in profile.users])
        for name in removed:
            uid = _kc_user_id(kc, name)
            if uid:
                kc.delete(f"/users/{uid}")
    return removed


def _clean_token_files(profile: ru.RealUserProfile) -> int:
    tokens = STATE_DIR / "tokens"
    keep = {*USERS, *(u.email for u in profile.users)}
    removed = 0
    for path in tokens.glob("*.json") if tokens.exists() else []:
        if path.stem not in keep:
            path.unlink()
            removed += 1
    current = _current_user()
    if current and current not in keep:
        (STATE_DIR / "current").unlink(missing_ok=True)
    return removed


async def _publish_baseline(publisher: ru.RealUser) -> str:
    result = await _mcp_call(
        publisher.email,
        "add_company_memory_knowledge",
        {
            "title": dc.GUIDE_TITLE,
            "content": GUIDE_FILE.read_text(encoding="utf-8"),
            "description": GUIDE_DESCRIPTION,
            "tags": GUIDE_TAGS,
            "scope": dc.IT_SUPPORT,
            "original_filename": GUIDE_FILE.name,
        },
    )
    if result.get("status") not in ("added", "unchanged"):
        raise CliError(f"publishing the baseline guide failed: {result.get('error', result)}")
    return str(result["source_id"])


def _demo_reset() -> None:
    _require_local_demo()
    _wait_ready()
    profile = _profile()
    _say("Company Memory demo reset (local POC only)")
    removed_users = _reset_keycloak(profile)
    _say(
        f"  keycloak      baseline groups for {len(dd.GROUPS)} synthetic and "
        f"{len(profile.users)} configured user(s); removed {len(removed_users)} temporary"
    )
    _seed_corpus()
    publisher = _publisher(profile)
    keep: list[str] = []
    if publisher is None:
        _say("  baseline      SKIPPED: no configured it-support publisher")
        _say(f"                fix: uv run company-memory user grant <email> {dc.PUBLISHERS}")
    else:
        keep.append(asyncio.run(_publish_baseline(publisher)))
        _say(f"  baseline      '{dc.GUIDE_TITLE}' published to it-support ({keep[0]})")
    summary = _state("reset", plan={"keep": keep, "subjects": baseline_subjects(profile)})
    _say(
        f"  knowledge     removed {len(summary['removed_manual_documents'])} manual document(s)"
        f" and {summary['removed_orphans']} orphan(s); kept {len(summary['kept'])}"
    )
    _say(
        f"  spicedb       memberships reconciled for {summary['reconciled_subjects']} "
        f"subject(s); forgot {summary['forgotten_subjects']} stale"
    )
    _say(f"  local state   removed {_clean_token_files(profile)} stale token file(s)")
    after = _state("inspect")
    manual = [m["doc_id"] for m in after["manual"]]
    if manual != keep or after["orphan_chunk_doc_ids"] or after["orphan_acl_doc_ids"]:
        raise CliError(f"reset did not converge: manual={manual} expected={keep}")
    _say("RESET COMPLETE (users.local.json and the synthetic corpus preserved)")


def cmd_demo_reset(args: argparse.Namespace) -> None:
    _demo_reset()


# ---------------------------------------------------------------- demo-check (read only)


def _peek_token(user: str) -> str | None:
    """The stored access token, or a refreshed one kept in memory only. Never logs in
    and never writes: the preflight must not change anything."""
    path = STATE_DIR / "tokens" / f"{user}.json"
    if not path.exists():
        return None
    rec = json.loads(path.read_text())
    now = time.time()
    if rec.get("expires_at", 0) - now > 30:
        return str(rec["access_token"])
    if rec.get("refresh_token") and rec.get("refresh_expires_at", 0) - now > 30:
        resp = httpx.post(
            TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "client_id": "cm-cli",
                "refresh_token": rec["refresh_token"],
            },
            timeout=15.0,
        )
        if resp.status_code == 200:
            return str(resp.json()["access_token"])
    return None


async def _tools(token: str | None) -> tuple[int, dict[str, list[str]] | None]:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with httpx.AsyncClient(timeout=15.0) as http:
        probe = await http.post(
            MCP_URL,
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={"Accept": "application/json, text/event-stream", **headers},
        )
    if not token or probe.status_code == 401:
        return probe.status_code, None
    async with httpx.AsyncClient(headers=headers, timeout=30.0) as client:
        async with streamable_http_client(
            MCP_URL, http_client=client, terminate_on_close=False
        ) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                listed = (await session.list_tools()).tools
    return 200, {t.name: sorted((t.inputSchema or {}).get("properties") or {}) for t in listed}


def _weak_local_secrets() -> list[str]:
    try:
        env = _load_env()
    except CliError:
        return ["stack.env missing"]
    return sorted(k for k, v in env.items() if v.lower() in _WEAK or len(v) < 24)


def _gather() -> dc.Facts:
    f = dc.Facts()
    f.api_ok = _get_ok(f"{AGORA}/healthz")
    f.keycloak_ok = _get_ok(f"{KEYCLOAK}/realms/{REALM}/.well-known/openid-configuration")
    f.embedder_ok = True
    try:
        init = httpx.post(
            MCP_URL,
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            headers={"Accept": "application/json, text/event-stream"},
            timeout=10.0,
        )
        f.mcp_status = init.status_code
        f.mcp_resource_metadata = "resource_metadata" in init.headers.get("www-authenticate", "")
    except httpx.HTTPError:
        f.mcp_status = 0
    f.containers = _containers() if STACK_ENV.exists() else {}
    f.embedder_ok = f.containers.get("tei-embed", {}).get("health") in ("healthy", "")
    if f.containers.get("mcp", {}).get("state") == "running":
        try:
            f.inspect = _state("inspect")
        except CliError:
            f.inspect = None
    if f.containers.get("agora", {}).get("state") == "running":
        try:
            f.agora_guard_reasons = list(_state("guards", service="agora")["guard_reasons"])
        except CliError:
            f.agora_guard_reasons = None
    for path, attr in (("/api/grab", "grab_status"), ("/openapi.json", "docs_status")):
        try:
            setattr(f, attr, httpx.get(f"{AGORA}{path}", timeout=5.0).status_code)
        except httpx.HTTPError:
            setattr(f, attr, 0)
    f.weak_local_secrets = _weak_local_secrets()
    profile = _profile()
    f.expected_tenant = profile.tenant
    f.configured_emails = [u.email for u in profile.users]
    f.user = _current_user()
    real = profile.find(f.user) if f.user and "@" in f.user else None
    f.configured_groups = list(real.groups) if real else []
    token = _peek_token(real.email) if real else None
    f.token_ok = token is not None
    try:
        f.tools_unauthenticated_status, _ = asyncio.run(_tools(None))
    except httpx.HTTPError:
        f.tools_unauthenticated_status = 0
    if token and real:
        f.claims = _claims(token)
        info = httpx.get(USERINFO_URL, headers={"Authorization": f"Bearer {token}"}, timeout=10.0)
        f.live_groups = info.json().get("groups", []) if info.status_code == 200 else None
        try:
            _, f.tools = asyncio.run(_tools(token))
            f.mcp_identity = asyncio.run(
                _mcp_call(None, "get_company_memory_identity", {}, token=token)
            )
            found = asyncio.run(
                _mcp_call(
                    None,
                    "search_company_memory",
                    {"query": "What is the name of the IT Support demo VPN profile?", "limit": 5},
                    token=token,
                )
            )
            f.search_hit = next(
                (r for r in found.get("results", []) if r.get("title") == dc.GUIDE_TITLE), None
            )
            if f.search_hit:
                f.source = asyncio.run(
                    _mcp_call(
                        None,
                        "get_company_memory_source",
                        {"source_id": f.search_hit["source_id"]},
                        token=token,
                    )
                )
        except (httpx.HTTPError, CliError, ValueError):
            pass
    return f


def _demo_check() -> bool:
    facts = _gather()
    checks = dc.evaluate(facts)
    principal = (facts.mcp_identity or {}).get("principal") or facts.user
    _say(dc.render(checks, principal))
    return dc.ready(checks)


def cmd_demo_check(args: argparse.Namespace) -> None:
    if not _demo_check():
        raise SystemExit(1)


def cmd_demo_ready(args: argparse.Namespace) -> None:
    _demo_reset()
    _say("")
    if not _demo_check():
        raise SystemExit(1)
