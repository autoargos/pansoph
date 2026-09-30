"""A hermetic agora harness for route level permission tests.

It mounts the real agora REST router and the real MCP tool on top of the shared in-memory
fakes, so a test exercises the production identity resolution, group alias expansion,
membership sync, retrieval enforcement, and response shaping, with no Postgres, SpiceDB,
or model server. Only the assembly builders are swapped, which is the seam agora itself
uses to resolve every adapter.

Added by the DOOR3 Company Memory fork (docs/company-memory/SECURITY_AND_FIT_REVIEW.md).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest
from _fakes import FakeAuthz, FakeEmbedder, FakeVectorStore
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pansoph_connector_sdk import InMemoryConnector
from pansoph_core import assembly, routes
from pansoph_core.memberships import InMemoryMembershipStore
from pansoph_identity.port import User
from pansoph_ingestion.acl_log import InMemoryAclLog
from pansoph_ingestion.adapters_parser import DoclingParser
from pansoph_ingestion.pipeline import IngestionPipeline
from pansoph_types import AccessControl

DEFAULT_TENANT = "default"
OTHER_TENANT = "t2"

# Marker strings that must never appear in a response to a caller not allowed to read them.
ALICE_SECRET = "ALICE_SECRET compensation review notes"
ENG_SECRET = "ENG_SECRET database failover runbook"
T2_SECRET = "T2_SECRET other tenant roadmap"
HANDBOOK = "company handbook holiday policy"


class FakeIdentity:
    """Maps opaque bearer tokens to users, like a validated OIDC token would."""

    def __init__(self, users: dict[str, User]) -> None:
        self.users = users

    async def authenticate(self, token: str) -> User | None:
        return self.users.get(token)


class FakeAliasStore:
    def __init__(self) -> None:
        self.aliases: dict[str, dict[str, set[str]]] = {}

    async def aliases_for_tenant(self, tenant_id: str) -> dict[str, set[str]]:
        return self.aliases.get(tenant_id, {})


class FakeLLM:
    """Records every prompt, so a test can prove what reached the model."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def complete(self, prompt: str, cache: bool = False) -> str:
        self.prompts.append(prompt)
        return "ok"


@dataclass
class Harness:
    store: FakeVectorStore
    authz: FakeAuthz
    identity: FakeIdentity
    aliases: FakeAliasStore
    llm: FakeLLM
    acl_log: InMemoryAclLog
    client: TestClient
    connectors: dict[str, InMemoryConnector] = field(default_factory=dict)

    async def sync(self, instance: str, tenant: str) -> None:
        pipeline = IngestionPipeline(
            connector=self.connectors[instance],
            parser=DoclingParser(),
            embedder=FakeEmbedder(),
            vector_store=self.store,
            authz=self.authz,
            acl_log=self.acl_log,
        )
        await pipeline.sync(connector_name="memory", tenant_id=tenant)

    def search(self, token: str | None, **body: Any) -> Any:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        return self.client.post("/api/search", json={"query": "q", **body}, headers=headers)

    def search_ids(self, token: str | None, **body: Any) -> set[str]:
        resp = self.search(token, **body)
        assert resp.status_code == 200, resp.text
        return {r["doc_id"] for r in resp.json()["results"]}

    def chat(self, token: str | None, **body: Any) -> Any:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        return self.client.post("/api/chat", json={"query": "q", **body}, headers=headers)

    async def mcp_search(self, token: str) -> str:
        """Call the upstream `pansoph_search` MCP tool as the transport would: the token is
        verified first and only the resulting principal reaches the tool (never the token)."""
        from mcp.server.auth.middleware.auth_context import auth_context_var
        from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
        from pansoph_core.mcp_auth import IdentityTokenVerifier
        from pansoph_core.mcp_server import build_mcp

        verifier = IdentityTokenVerifier(lambda: self.identity)
        access = await verifier.verify_token(token)
        if access is None:
            return "Authentication required."
        server = build_mcp(auth=_TEST_AUTH, token_verifier=verifier)
        reset = auth_context_var.set(AuthenticatedUser(access))
        try:
            result = await server.call_tool("pansoph_search", {"query": "q"})
        finally:
            auth_context_var.reset(reset)
        # FastMCP returns either a content list or (content list, structured result).
        content = result[0] if isinstance(result, tuple) else result
        return "\n".join(getattr(c, "text", "") for c in content)


def _auth_settings() -> Any:
    from mcp.server.auth.settings import AuthSettings

    return AuthSettings(
        issuer_url="https://idp.example.test/realms/test",  # type: ignore[arg-type]
        resource_server_url="http://localhost:8765/mcp",  # type: ignore[arg-type]
    )


_TEST_AUTH = _auth_settings()


def doc_ids_in(text: str) -> set[str]:
    """Doc ids mentioned in an MCP tool response (each hit is `[n] {doc_id}`)."""
    ids: set[str] = set()
    for line in text.splitlines():
        if line.startswith("[") and "] " in line:
            ids.add(line.split("] ", 1)[1].strip())
    return ids


def sse_events(resp: Any) -> list[dict[str, Any]]:
    return [
        json.loads(line[len("data:") :].strip())
        for line in resp.text.splitlines()
        if line.startswith("data:")
    ]


async def build_harness(monkeypatch: pytest.MonkeyPatch) -> Harness:
    """Seed two tenants with restricted, group, and public documents, and wire agora."""
    store, authz, llm = FakeVectorStore(), FakeAuthz(), FakeLLM()
    identity = FakeIdentity(
        {
            "tok-alice": User(id="alice", email="alice@door3.test", group_ids=["eng"]),
            "tok-bob": User(id="bob", email="bob@door3.test", group_ids=[]),
            "tok-eve": User(id="eve", email="eve@t2.test", group_ids=["eng"], tenant_id="t2"),
        }
    )
    aliases = FakeAliasStore()
    memberships = InMemoryMembershipStore()
    for name, value in {
        "build_membership_store": lambda: memberships,
        "build_authz": lambda: authz,
        "build_vector_store": lambda: store,
        "build_embedder": FakeEmbedder,
        "build_reranker": lambda: None,
        "build_group_alias_store": lambda: aliases,
        "build_llm": lambda: llm,
        "build_identity": lambda: identity,
        "build_parser": DoclingParser,
    }.items():
        monkeypatch.setattr(assembly, name, value)
    monkeypatch.delenv("PANSOPH_REQUIRE_AUTH", raising=False)

    app = FastAPI()
    app.include_router(routes.router)
    harness = Harness(
        store=store,
        authz=authz,
        identity=identity,
        aliases=aliases,
        llm=llm,
        acl_log=InMemoryAclLog(),
        client=TestClient(app),
    )

    main = InMemoryConnector(instance_id="main")
    main.put("alice-private", ALICE_SECRET, AccessControl(allow_users=["alice@door3.test"]))
    main.put("eng-runbook", ENG_SECRET, AccessControl(allow_groups=["eng"]))
    main.put("handbook", HANDBOOK, AccessControl(public=True))
    harness.connectors["main"] = main
    await harness.sync("main", DEFAULT_TENANT)

    other = InMemoryConnector(instance_id="other")
    other.put("t2-roadmap", T2_SECRET, AccessControl(public=True))
    harness.connectors["other"] = other
    await harness.sync("other", OTHER_TENANT)
    return harness


ALICE_DOC = "memory:main:alice-private"
ENG_DOC = "memory:main:eng-runbook"
HANDBOOK_DOC = "memory:main:handbook"
T2_DOC = "memory:other:t2-roadmap"
