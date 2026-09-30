"""The live demo preflight, as pure decisions over gathered facts.

`company-memory demo-check` gathers facts (read only: health endpoints, `docker compose
ps`, the container's `demo_state inspect`, the signed-in user's token, userinfo, and MCP
reads) and hands them to `evaluate`, which decides READY or NOT READY per item and says
why it matters and how to fix it. Keeping the decisions pure makes every failure mode
testable without a running stack. Nothing here prints a token, password, or secret.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

GUIDE_TITLE = "DOOR3 IT Support Quick Guide"
GUIDE_MARKER = "NORTHSTAR"
IT_SUPPORT = "it-support"
PUBLISHERS = "it-support-publishers"
EVERYONE = "everyone"
EXPECTED_TOOLS = frozenset(
    {
        "search_company_memory",
        "search_company_memory_evidence",
        "get_company_memory_source",
        "get_company_memory_identity",
        "add_company_memory_knowledge",
        "list_my_company_memory_knowledge",
        "delete_company_memory_knowledge",
    }
)
# No tool argument may carry a credential, an identity, a tenant, or an ACL.
FORBIDDEN_ARG_WORDS = (
    "token",
    "bearer",
    "auth",
    "credential",
    "password",
    "secret",
    "user",
    "tenant",
    "principal",
    "created_by",
    "owner",
    "acl",
    "allow",
    "deny",
    "email",
)
LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost"})
PUBLISHED = {"agora": 8080, "mcp": 8765, "keycloak": 8090}
PRIVATE_SERVICES = ("postgres", "spicedb")
REQUIRED_SERVICES = ("agora", "mcp", "keycloak", "postgres", "spicedb", "tei-embed")


@dataclass
class Check:
    section: str
    name: str
    ok: bool
    detail: str = ""
    why: str = ""
    fix: str = ""


@dataclass
class Facts:
    api_ok: bool = False
    mcp_status: int = 0
    mcp_resource_metadata: bool = False
    keycloak_ok: bool = False
    embedder_ok: bool = False
    containers: dict[str, dict[str, Any]] = field(default_factory=dict)
    inspect: dict[str, Any] | None = None
    agora_guard_reasons: list[str] | None = None
    grab_status: int = 0
    docs_status: int = 0
    weak_local_secrets: list[str] = field(default_factory=list)
    # Identity of the signed-in user.
    user: str | None = None
    configured_emails: list[str] = field(default_factory=list)
    configured_groups: list[str] = field(default_factory=list)
    expected_tenant: str = ""
    token_ok: bool = False
    claims: dict[str, Any] = field(default_factory=dict)
    live_groups: list[str] | None = None
    mcp_identity: dict[str, Any] | None = None
    # MCP reads as that user.
    tools: dict[str, list[str]] | None = None
    tools_unauthenticated_status: int = 0
    search_hit: dict[str, Any] | None = None
    source: dict[str, Any] | None = None


def _login_fix(facts: Facts) -> str:
    who = facts.configured_emails[0] if facts.configured_emails else "<your-email>"
    return f"uv run company-memory login {who}"


def _baseline(facts: Facts) -> list[dict[str, Any]]:
    manual = (facts.inspect or {}).get("manual", [])
    return [
        m
        for m in manual
        if m.get("title") == GUIDE_TITLE
        and m.get("tenant") == facts.expected_tenant
        and m.get("scope") == f"group:{IT_SUPPORT}"
    ]


def evaluate(facts: Facts) -> list[Check]:
    out: list[Check] = []
    add = out.append
    reset_fix = "uv run company-memory demo-reset"
    up_fix = "uv run company-memory up"

    # ------------------------------------------------------------ services
    c = facts.containers
    running = {s for s, v in c.items() if v.get("state") == "running"}
    add(Check("Services", "API", facts.api_ok, "", "Claude and REST reach nothing", up_fix))
    add(
        Check(
            "Services",
            "MCP",
            facts.mcp_status == 401 and facts.mcp_resource_metadata,
            "" if facts.mcp_status else "unreachable",
            "Claude Code connects here; it must demand a token (401)",
            up_fix,
        )
    )
    add(Check("Services", "Keycloak", facts.keycloak_ok, "", "nobody can log in", up_fix))
    inspect = facts.inspect
    add(
        Check(
            "Services",
            "SpiceDB",
            "spicedb" in running and bool(inspect and inspect.get("spicedb_ok")),
            "",
            "every read is authorized there; without it nothing is returned",
            up_fix,
        )
    )
    add(
        Check(
            "Services",
            "pgvector",
            "postgres" in running and inspect is not None,
            "",
            "the index and the manual knowledge live there",
            up_fix,
        )
    )
    add(
        Check(
            "Services",
            "Embedder",
            facts.embedder_ok and "tei-embed" in running,
            "",
            "search cannot embed the question",
            up_fix,
        )
    )

    # ------------------------------------------------------------ identity
    claims = facts.claims
    email = str(claims.get("email") or "")
    configured = facts.user is not None and facts.user in facts.configured_emails
    add(
        Check(
            "Identity",
            "Signed-in real user",
            configured,
            facts.user or "nobody",
            "the real-user demo runs as a configured DOOR3 identity",
            _login_fix(facts)
            if facts.configured_emails
            else "uv run company-memory user add <your-email> --group it-support-publishers",
        )
    )
    add(
        Check(
            "Identity",
            "Valid token",
            configured and facts.token_ok,
            "",
            "Claude Code sends this token through the headersHelper",
            _login_fix(facts),
        )
    )
    add(
        Check(
            "Identity",
            "Verified email",
            facts.token_ok and claims.get("email_verified") is True and email == facts.user,
            email,
            "the email is the principal; an unverified one is rejected",
            _login_fix(facts),
        )
    )
    add(
        Check(
            "Identity",
            f"Tenant {facts.expected_tenant or '?'}",
            facts.token_ok and claims.get("tenant") == facts.expected_tenant,
            str(claims.get("tenant") or "none"),
            "real users must not share the synthetic tenant",
            "uv run company-memory demo-reset",
        )
    )
    live = facts.live_groups or []
    add(
        Check(
            "Identity",
            "In everyone",
            EVERYONE in live,
            "",
            "only configured users (in everyone) authenticate and read it-support",
            reset_fix,
        )
    )
    mcp_id = facts.mcp_identity or {}
    add(
        Check(
            "Identity",
            "MCP principal",
            bool(email)
            and mcp_id.get("principal") == email
            and mcp_id.get("tenant") == facts.expected_tenant,
            str(mcp_id.get("principal") or "not seen"),
            "the server must see exactly this identity",
            _login_fix(facts),
        )
    )

    # ------------------------------------------------------------ permissions
    policy = (inspect or {}).get("policy", {})
    rule = policy.get(facts.expected_tenant, {}).get(IT_SUPPORT)
    add(
        Check(
            "Permissions",
            "Publish policy loaded",
            bool(rule) and not (inspect or {}).get("policy_error"),
            (inspect or {}).get("policy_error", ""),
            "without it no shared publishing works",
            "check deploy/company-memory/publish-policy.json, then uv run company-memory up",
        )
    )
    add(
        Check(
            "Permissions",
            "IT Support read",
            bool(rule) and rule.get("read_groups") == [EVERYONE] and EVERYONE in live,
            "",
            "every configured user must read it-support, nobody else",
            reset_fix,
        )
    )
    is_publisher = PUBLISHERS in live
    add(
        Check(
            "Permissions",
            "IT Support publish",
            bool(rule)
            and rule.get("publisher_groups") == [PUBLISHERS]
            and EVERYONE not in rule.get("publisher_groups", [])
            and is_publisher
            and IT_SUPPORT in mcp_id.get("publishable_scopes", []),
            "" if is_publisher else "signed-in user is reader only",
            "demo 2 publishes to it-support as this user",
            f"uv run company-memory user grant {facts.user or '<email>'} {PUBLISHERS}",
        )
    )
    add(
        Check(
            "Permissions",
            "Private default",
            bool(inspect and inspect.get("private_default_ok")),
            "",
            "knowledge added without a scope must stay creator only",
            "do not demo: the server image does not enforce private by default",
        )
    )

    # ------------------------------------------------------------ knowledge
    baseline = _baseline(facts)
    one = baseline[0] if len(baseline) == 1 else None
    acl = (one or {}).get("indexed_acl") or {}
    add(
        Check(
            "Knowledge",
            "IT Support baseline",
            one is not None and one.get("chunks", 0) > 0,
            f"{len(baseline)} found",
            "demo 1 answers NORTHSTAR from it",
            reset_fix,
        )
    )
    add(
        Check(
            "Knowledge",
            "Not public",
            one is not None
            and acl.get("public") is False
            and acl.get("allow_groups") == [EVERYONE]
            and not acl.get("allow_users"),
            "",
            "it-support must need a configured login, never be tenant or anonymous public",
            reset_fix,
        )
    )
    hit = facts.search_hit or {}
    add(
        Check(
            "Knowledge",
            "Retrieval as user",
            one is not None
            and hit.get("source_id") == one.get("doc_id")
            and GUIDE_MARKER in str(hit),
            "",
            "the signed-in user must find the guide through search",
            reset_fix,
        )
    )
    src = facts.source or {}
    add(
        Check(
            "Knowledge",
            "Citation",
            bool(hit.get("source_id"))
            and bool(hit.get("chunk_ids"))
            and GUIDE_MARKER in str(src.get("content", ""))
            and (src.get("provenance") or {}).get("scope") == f"group:{IT_SUPPORT}",
            "",
            "answers must cite a source id the user can open",
            reset_fix,
        )
    )
    leftovers = [m for m in (inspect or {}).get("manual", []) if m not in baseline]
    orphans = (inspect or {}).get("orphan_chunk_doc_ids", []) + (inspect or {}).get(
        "orphan_acl_doc_ids", []
    )
    add(
        Check(
            "Knowledge",
            "Clean state",
            inspect is not None and not leftovers and not orphans,
            f"{len(leftovers)} extra manual doc(s), {len(orphans)} orphan(s)",
            "old demo notes would show up in answers",
            reset_fix,
        )
    )

    # ------------------------------------------------------------ security
    guards = list((inspect or {}).get("guard_reasons", ["inspect failed"])) + list(
        facts.agora_guard_reasons if facts.agora_guard_reasons is not None else ["unknown"]
    )
    add(
        Check(
            "Security",
            "Auth and identity config",
            not guards,
            "; ".join(guards)[:200],
            "require auth, the company_memory identity adapter, no default secrets",
            "fix deploy/company-memory/compose.yaml, then uv run company-memory up",
        )
    )
    add(
        Check(
            "Security",
            "Demo marker",
            (inspect or {}).get("demo_env") == "local-poc",
            "",
            "reset only runs against the local demo",
            up_fix,
        )
    )
    for service, port in PUBLISHED.items():
        pubs = [p for p in c.get(service, {}).get("publishers", []) if p.get("PublishedPort")]
        ok = bool(pubs) and all(p.get("URL") in LOOPBACK for p in pubs)
        ok = ok and all(p.get("PublishedPort") == port for p in pubs)
        add(
            Check(
                "Security",
                f"{service} loopback only",
                ok,
                ", ".join(f"{p.get('URL')}:{p.get('PublishedPort')}" for p in pubs)
                or "not published",
                "nothing may listen beyond this laptop",
                "bind it to 127.0.0.1 in compose.yaml, then uv run company-memory up",
            )
        )
    for service in PRIVATE_SERVICES:
        pubs = [p for p in c.get(service, {}).get("publishers", []) if p.get("PublishedPort")]
        add(
            Check(
                "Security",
                f"{service} not published",
                not pubs,
                "",
                "datastores are reachable only inside the compose network",
                "remove its ports in compose.yaml, then uv run company-memory up",
            )
        )
    add(
        Check(
            "Security",
            "Unsafe grab route",
            facts.grab_status == 404 and facts.docs_status == 404,
            "disabled" if facts.grab_status == 404 else f"HTTP {facts.grab_status}",
            "/api/grab and the API docs must not be mounted",
            "run agora as company_memory_mcp.agora_app (compose.yaml)",
        )
    )
    add(
        Check(
            "Security",
            "Generated secrets",
            not facts.weak_local_secrets,
            ", ".join(facts.weak_local_secrets),
            "default or short credentials would make the stack guessable",
            "uv run company-memory init --force (then down --volumes, up, seed)",
        )
    )
    tools = facts.tools or {}
    missing = sorted(EXPECTED_TOOLS - set(tools))
    add(
        Check(
            "Security",
            "MCP tools present",
            facts.tools is not None and not missing,
            ", ".join(missing),
            "the demo uses search, source, identity, add, list, and delete",
            up_fix,
        )
    )
    exposed = sorted(
        f"{name}.{arg}"
        for name, args in tools.items()
        for arg in args
        if any(w in arg.lower() for w in FORBIDDEN_ARG_WORDS)
    )
    add(
        Check(
            "Security",
            "Tool credentials",
            facts.tools is not None and not exposed,
            ", ".join(exposed) or "none",
            "a token, identity, tenant, or ACL argument would be model controlled",
            "do not demo: the server image exposes identity in tool arguments",
        )
    )
    add(
        Check(
            "Security",
            "MCP needs a token",
            facts.tools_unauthenticated_status == 401,
            "",
            "an unauthenticated client must see no tools",
            up_fix,
        )
    )
    return _explain_missing_login(facts, out)


# Checks that can only be evaluated with the signed-in user's token.
_NEEDS_USER = frozenset(
    {
        "Valid token",
        "Verified email",
        "In everyone",
        "MCP principal",
        "IT Support read",
        "IT Support publish",
        "Retrieval as user",
        "Citation",
        "MCP tools present",
        "Tool credentials",
    }
)


def _explain_missing_login(facts: Facts, checks: list[Check]) -> list[Check]:
    """Without a usable token the user dependent checks were not run: say so, and point
    at the one command that fixes all of them, instead of blaming the server."""
    if facts.token_ok:
        return checks
    for c in checks:
        if not c.ok and (c.name in _NEEDS_USER or c.name.startswith("Tenant ")):
            c.detail = "not checked: nobody signed in"
            c.fix = _login_fix(facts)
    return checks


def ready(checks: list[Check]) -> bool:
    return all(c.ok for c in checks)


def render(checks: list[Check], principal: str | None) -> str:
    lines = ["Company Memory Demo Preflight", ""]
    section = ""
    for c in checks:
        if c.section != section:
            section = c.section
            lines.append(section)
            if section == "Identity" and principal:
                lines.append(f"  principal           {principal}")
        state = "READY" if c.ok else "NOT READY"
        detail = f"  ({c.detail})" if c.detail and (not c.ok or section == "Identity") else ""
        lines.append(f"  {c.name:<26} {state}{detail}")
    lines.append("")
    failed = [c for c in checks if not c.ok]
    if not failed:
        lines.append("DEMO READY")
        return "\n".join(lines)
    lines.append(f"DEMO NOT READY ({len(failed)} item(s))")
    for c in failed:
        lines.append(f"  - {c.section} / {c.name}: {c.why}")
        if c.fix:
            lines.append(f"      fix: {c.fix}")
    return "\n".join(lines)
