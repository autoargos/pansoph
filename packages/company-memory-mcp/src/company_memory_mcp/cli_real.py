"""`company-memory user ...` and `company-memory verify-real`: the real-user local POC.

Real identities, synthetic knowledge. Users come from the gitignored
`deploy/company-memory/users.local.json` (see real_users.py); passwords are generated into
`.secrets/` and never printed. Nothing here connects to a real DOOR3 system.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import secrets
from typing import Any

import httpx

from . import real_users as ru
from .cli import (
    KEYCLOAK,
    MCP_URL,
    REALM,
    REPO,
    USERS_LOCAL,
    CliError,
    _Checks,
    _get_ok,
    _kc_admin,
    _mcp_call,
    _profile,
    _real_passwords,
    _render_realm,
    _say,
    _wait_ready,
)
from .identity import canonical_email

# ---------------------------------------------------------------- real users


def _kc_up() -> bool:
    return _get_ok(f"{KEYCLOAK}/realms/{REALM}")


def _kc_group_id(kc: httpx.Client, group: str) -> str:
    found = [g for g in kc.get("/groups", params={"search": group}).json() if g["name"] == group]
    if found:
        return str(found[0]["id"])
    resp = kc.post("/groups", json={"name": group})
    if resp.status_code not in (201, 204):
        raise CliError(f"creating group {group} failed: HTTP {resp.status_code}")
    return _kc_group_id(kc, group)


def _kc_user_id(kc: httpx.Client, email: str) -> str | None:
    users = kc.get("/users", params={"username": email, "exact": "true"}).json()
    return str(users[0]["id"]) if users else None


def _kc_apply_user(kc: httpx.Client, user: ru.RealUser, tenant: str, password: str) -> None:
    """Create or update a configured user in the running realm, exactly as the rendered
    realm would (tenant attribute, verified email, password, groups)."""
    rep = ru.keycloak_user(user, tenant, password)
    uid = _kc_user_id(kc, user.email)
    body = {k: v for k, v in rep.items() if k not in ("groups", "realmRoles")}
    if uid is None:
        resp = kc.post("/users", json=body)
        if resp.status_code not in (201, 204):
            raise CliError(f"creating {user.email} failed: HTTP {resp.status_code}")
        uid = _kc_user_id(kc, user.email)
    else:
        body.pop("credentials")
        kc.put(f"/users/{uid}", json=body)
        kc.put(
            f"/users/{uid}/reset-password",
            json={"type": "password", "value": password, "temporary": False},
        )
    if uid is None:
        raise CliError(f"{user.email} was not created")
    stored = kc.get(f"/users/{uid}").json().get("attributes", {}).get("tenant")
    if stored != [tenant]:
        # The realm predates the tenant attribute (user profile). Fail closed: without it
        # the user would land in the synthetic default tenant.
        raise CliError(
            "the running realm cannot store the tenant attribute; reset it: "
            "`company-memory down && company-memory up && company-memory seed`"
        )
    role = kc.get("/roles/member")
    if role.status_code == 200:
        kc.post(f"/users/{uid}/role-mappings/realm", json=[role.json()])
    current = {g["name"] for g in kc.get(f"/users/{uid}/groups").json()}
    for group in sorted(set(user.groups) - current):
        kc.put(f"/users/{uid}/groups/{_kc_group_id(kc, group)}")
    for group in sorted(current - set(user.groups)):
        kc.delete(f"/users/{uid}/groups/{_kc_group_id(kc, group)}")


def _apply_profile_change(profile: ru.RealUserProfile, email: str) -> None:
    ru.save_profile(profile, USERS_LOCAL)
    _render_realm()
    if not _kc_up():
        _say("  keycloak is not running: the change applies on the next `company-memory up`")
        return
    user = profile.find(email)
    with _kc_admin() as kc:
        if user is None:
            uid = _kc_user_id(kc, email)
            if uid:
                kc.delete(f"/users/{uid}")
        else:
            _kc_apply_user(kc, user, profile.tenant, _real_passwords(profile)[user.email])
    _say("  applied to the running Keycloak (takes effect on the user's next request)")


def cmd_user(args: argparse.Namespace) -> None:
    profile = _profile()
    if args.action == "list":
        _say(f"tenant: {profile.tenant}   ({USERS_LOCAL.relative_to(REPO)})")
        for u in profile.users:
            _say(f"  {u.email:<40} {', '.join(u.groups)}")
        if not profile.users:
            _say("  (no real users configured: `company-memory user add <email>`)")
        return
    email = canonical_email(args.email or "")
    if email is None:
        raise CliError(f"not an email address: {args.email!r}")
    user = profile.find(email)
    if args.action == "add":
        if user is not None:
            raise CliError(f"{email} is already configured")
        groups = [ru.check_group(g) for g in args.group or []]
        profile.users.append(ru.RealUser(email, list(dict.fromkeys([ru.EVERYONE, *groups]))))
        _say(f"added {email} to tenant {profile.tenant} with groups {profile.find(email).groups}")  # type: ignore[union-attr]
    elif user is None:
        raise CliError(f"{email} is not configured (company-memory user add {email})")
    elif args.action == "remove":
        profile.users.remove(user)
        _say(f"removed {email}")
    elif args.action in ("grant", "revoke"):
        group = ru.check_group(args.target_group or "")
        if args.action == "revoke" and group == ru.EVERYONE:
            # Every configured user is in `everyone`; ending access is `user remove`.
            raise CliError(
                f"`everyone` is what configured means: `company-memory user remove {email}`"
            )
        if args.action == "grant":
            user.groups = list(dict.fromkeys([*user.groups, group]))
        else:
            user.groups = [g for g in user.groups if g != group]
        _say(f"{email}: groups now {user.groups}")
    _apply_profile_change(profile, email)


# ---------------------------------------------------------------- live real-user check

IT_SUPPORT = "it-support"
GUIDE_FILE = "deploy/company-memory/demo-input/it-support-quick-guide.md"
GUIDE_MARKER = "NORTHSTAR"
AGORA_SEARCH = "http://localhost:8080/api/search"


def _password_token(email: str, password: str) -> str:
    from .cli import _post_token

    tok = _post_token(
        {
            "grant_type": "password",
            "client_id": "cm-cli",
            "username": email,
            "password": password,
            "scope": "openid",
        }
    )
    return str(tok["access_token"])


def _ids(found: dict[str, Any]) -> set[str]:
    return {r["source_id"] for r in found.get("results", [])}


async def _rest_ids(token: str, query: str) -> tuple[int, set[str]]:
    async with httpx.AsyncClient(timeout=30.0) as http:
        resp = await http.post(
            AGORA_SEARCH,
            json={"query": query, "final_k": 20},
            headers={"Authorization": f"Bearer {token}"},
        )
    if resp.status_code != 200:
        return resp.status_code, set()
    return 200, {r["doc_id"] for r in resp.json()["results"]}


async def _mcp_status(token: str) -> int:
    init = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "verify-real", "version": "0"},
        },
    }
    async with httpx.AsyncClient(timeout=15.0) as http:
        resp = await http.post(
            MCP_URL,
            json=init,
            headers={
                "Accept": "application/json, text/event-stream",
                "Authorization": f"Bearer {token}",
            },
        )
    return resp.status_code


async def _verify_real(publisher: str) -> int:
    from .service import NOT_FOUND

    c = _Checks()
    profile = _profile()
    tenant = profile.tenant
    me = await _mcp_call(publisher, "get_company_memory_identity", {})
    c.check(me.get("principal") == publisher, f"MCP principal is the verified email {publisher}")
    c.check(me.get("tenant") == tenant, f"tenant comes from the token: {tenant}")
    c.check(
        IT_SUPPORT in me.get("publishable_scopes", []), "the publisher may publish to it-support"
    )
    vpn = await _mcp_call(
        publisher,
        "search_company_memory",
        {"query": "What is the name of the IT Support demo VPN profile?", "limit": 5},
    )
    guide = [r for r in vpn.get("results", []) if GUIDE_MARKER in json.dumps(r)]
    c.check(bool(guide), f"the IT Support guide answers the VPN question ({GUIDE_MARKER})")
    if not guide:
        _say(f"        publish it: company-memory add {GUIDE_FILE} --scope it-support")
    alice = await _mcp_call("alice", "search_company_memory", {"query": GUIDE_MARKER, "limit": 20})
    c.check(
        GUIDE_MARKER not in json.dumps(alice),
        "a synthetic user (tenant default, also in a group named everyone) cannot read it",
    )

    # A throwaway configured reader: in `everyone`, no publisher grant. Removed at the end.
    reader = ru.RealUser(f"cm-verify-reader-{secrets.token_hex(3)}@company-memory.test")
    password = secrets.token_urlsafe(24)
    probe = f"VERIFY-REAL-{secrets.token_hex(4).upper()}"
    created: list[str] = []
    with _kc_admin() as kc:
        _kc_apply_user(kc, reader, tenant, password)
    try:
        rtok = _password_token(reader.email, password)
        seen = await _mcp_call(None, "get_company_memory_identity", {}, token=rtok)
        c.check(
            seen.get("principal") == reader.email and seen.get("publishable_scopes") == ["private"],
            "a reader-only user is authenticated as their email and may publish privately only",
        )
        if guide:
            got = await _mcp_call(
                None, "search_company_memory", {"query": "VPN profile", "limit": 10}, token=rtok
            )
            c.check(guide[0]["source_id"] in _ids(got), "every configured user reads it-support")
        shared = await _mcp_call(
            publisher,
            "add_company_memory_knowledge",
            {
                "title": f"IT probe {probe}",
                "content": f"Shared IT note {probe}.",
                "scope": IT_SUPPORT,
            },
        )
        c.check(shared.get("status") == "added", "the publisher publishes to it-support")
        created.append(shared.get("source_id", ""))
        q = {"query": f"note {probe}", "limit": 10}
        c.check(
            shared.get("source_id")
            in _ids(await _mcp_call(None, "search_company_memory", q, token=rtok)),
            "the reader retrieves the new it-support note",
        )
        status, rest = await _rest_ids(rtok, f"note {probe}")
        evidence = await _mcp_call(
            None, "search_company_memory_evidence", {**q, "limit": 20}, token=rtok
        )
        c.check(
            status == 200 and rest == {x["source_id"] for x in evidence.get("chunks", [])},
            "MCP and REST return the same sources for the reader",
        )
        denied = await _mcp_call(
            None,
            "add_company_memory_knowledge",
            {"title": f"x {probe}", "content": "x", "scope": IT_SUPPORT},
            token=rtok,
        )
        c.check(denied.get("status") == "denied", "the reader cannot publish to it-support")
        before = await _mcp_call(publisher, "list_my_company_memory_knowledge", {"limit": 50})
        lead = await _mcp_call(
            publisher,
            "add_company_memory_knowledge",
            {"title": f"x {probe}", "content": "x", "scope": "leadership"},
        )
        after = await _mcp_call(publisher, "list_my_company_memory_knowledge", {"limit": 50})
        c.check(
            lead.get("status") == "denied" and len(after["knowledge"]) == len(before["knowledge"]),
            "the publisher cannot publish to leadership (denied, nothing stored)",
        )
        private = await _mcp_call(
            publisher,
            "add_company_memory_knowledge",
            {"title": f"Private probe {probe}", "content": f"Private note {probe}."},
        )
        created.append(private.get("source_id", ""))
        c.check(private.get("scope") == "private", "knowledge without a scope is private")
        pid = private.get("source_id", "")
        c.check(
            pid in _ids(await _mcp_call(publisher, "search_company_memory", q)),
            "the creator reads it",
        )
        rsearch = await _mcp_call(None, "search_company_memory", q, token=rtok)
        rsource = await _mcp_call(None, "get_company_memory_source", {"source_id": pid}, token=rtok)
        c.check(
            pid not in _ids(rsearch) and rsource.get("error") == NOT_FOUND,
            "the reader (also in everyone) cannot see the private note, even by id",
        )
        # Unconfigure the reader: out of `everyone`, the same token stops working at all.
        with _kc_admin() as kc:
            reader.groups = []
            _kc_apply_user(kc, reader, tenant, password)
        c.check(await _mcp_status(rtok) == 401, "an unconfigured user is rejected by MCP (401)")
        c.check((await _rest_ids(rtok, "VPN"))[0] == 401, "and by REST (401)")
    finally:
        for source_id in created:
            if source_id:
                await _mcp_call(
                    publisher, "delete_company_memory_knowledge", {"source_id": source_id}
                )
        with _kc_admin() as kc:
            uid = _kc_user_id(kc, reader.email)
            if uid:
                kc.delete(f"/users/{uid}")
        # The reader's SpiceDB memberships outlive the Keycloak account (it can no longer
        # query, so nothing reconciles them): delete them too, leaving nothing behind.
        from .cli_demo import _state

        _state("forget", plan={"subjects": [{"tenant": tenant, "subject": reader.email}]})
    gone = await _mcp_call(publisher, "search_company_memory", q)
    c.check(probe not in json.dumps(gone), "probes deleted: nothing left to retrieve")
    return c.failed


def cmd_verify_real(args: argparse.Namespace) -> None:
    from .cli import _resolve_user

    publisher = _resolve_user(args.as_user)
    if "@" not in publisher:
        raise CliError("verify-real runs as a configured real user email")
    _wait_ready()
    _say(f"live real-user check as {publisher} (tenant {_profile().tenant}):")
    failed = asyncio.run(_verify_real(publisher))
    _say("ALL CHECKS PASSED" if not failed else f"{failed} CHECK(S) FAILED")
    if failed:
        raise SystemExit(1)
