"""Hermetic Company Memory demo stack for tests.

Loads the real synthetic corpus (`deploy/company-memory/corpus/`) through the real
synthetic connector and ingestion pipeline (ACL diff, tuples, chunks) into the shared
in-memory fakes, then serves the real Company Memory MCP app over an in-process ASGI
transport, with the MCP SDK's bearer auth middleware and the pansoph token verifier in
front, exactly as in the container. Identity is a fake IdP whose groups can change between
requests, like Keycloak's userinfo after an admin removes a group.

Added by the DOOR3 Company Memory fork. Synthetic data only.
"""

from __future__ import annotations

import contextlib
import json
import shutil
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest
from _agora_harness import FakeAliasStore, FakeIdentity, FakeLLM
from _fakes import FakeAuthz, FakeEmbedder, FakeVectorStore
from company_memory_mcp import knowledge_store
from company_memory_mcp.demo_data import (  # noqa: F401  re-exported for the tests
    DEMO_QUERY,
    EXPECTED,
    FORBIDDEN,
    GROUPS,
    INSTANCE,
    MARKERS,
    doc_id,
)
from company_memory_mcp.identity import ConfiguredUserIdentity
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pansoph_connector_cm_synthetic.connector import SyntheticCorpusConnector
from pansoph_core import assembly, routes
from pansoph_core.mcp_auth import IdentityTokenVerifier
from pansoph_core.memberships import InMemoryMembershipStore
from pansoph_identity.port import User
from pansoph_ingestion.acl_log import InMemoryAclLog
from pansoph_ingestion.adapters_parser import DoclingParser
from pansoph_ingestion.pipeline import IngestionPipeline

REPO = Path(__file__).resolve().parents[1]
CORPUS = REPO / "deploy" / "company-memory" / "corpus"
PUBLISH_POLICY = REPO / "deploy" / "company-memory" / "publish-policy.json"
USERS_EXAMPLE = REPO / "deploy" / "company-memory" / "users.example.json"
# The deployment's configured-user rule (compose CM_REQUIRED_GROUPS).
REQUIRED_GROUPS = frozenset({"everyone", "cm-service-accounts"})
# An authenticated account of the real-user tenant that nobody configured (no everyone).
STRANGER = "stranger@example.com"
MCP_URL = "http://localhost:8765/mcp"


def stems(ids: set[str]) -> set[str]:
    return {i.rsplit(":", 1)[-1] for i in ids}


def token_for(user: str) -> str:
    return f"tok-{user}"


@dataclass
class Demo:
    store: FakeVectorStore
    authz: FakeAuthz
    identity: FakeIdentity
    corpus: Path
    rest: TestClient
    acl_log: InMemoryAclLog = field(default_factory=InMemoryAclLog)
    knowledge: knowledge_store.InMemoryKnowledgeStore = field(
        default_factory=knowledge_store.InMemoryKnowledgeStore
    )
    cursor: str | None = None

    def set_groups(self, user: str, groups: list[str]) -> None:
        """Change a user's directory groups, as an IdP admin would."""
        current = self.identity.users[token_for(user)]
        self.identity.users[token_for(user)] = current.model_copy(update={"group_ids": groups})

    async def sync(self) -> None:
        pipeline = IngestionPipeline(
            connector=SyntheticCorpusConnector(INSTANCE, {"corpus_dir": str(self.corpus)}),
            parser=DoclingParser(),
            embedder=FakeEmbedder(),
            vector_store=self.store,
            authz=self.authz,
            acl_log=self.acl_log,
        )
        self.cursor = await pipeline.sync(
            connector_name="cm_synthetic", tenant_id="default", cursor=self.cursor
        )

    def rest_ids(self, user: str, query: str = DEMO_QUERY, k: int = 20) -> set[str]:
        resp = self.rest.post(
            "/api/search",
            json={"query": query, "final_k": k},
            headers={"Authorization": f"Bearer {token_for(user)}"},
        )
        assert resp.status_code == 200, resp.text
        return {r["doc_id"] for r in resp.json()["results"]}


def _test_auth() -> Any:
    from mcp.server.auth.settings import AuthSettings

    return AuthSettings(
        issuer_url="https://idp.example.test/realms/company-memory",  # type: ignore[arg-type]
        resource_server_url=MCP_URL,  # type: ignore[arg-type]
    )


async def build_demo(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Demo:
    corpus = tmp_path / "corpus"
    shutil.copytree(CORPUS, corpus)
    store, authz = FakeVectorStore(), FakeAuthz()
    users = {
        token_for(name): User(id=name, email=f"{name}@company-memory.test", group_ids=groups)
        for name, groups in GROUPS.items()
    }
    users["tok-eve"] = User(
        id="eve", email="eve@other.test", group_ids=["everyone", "engineering"], tenant_id="t2"
    )
    # The real-user profile, from the committed example (placeholder emails only).
    example = json.loads(USERS_EXAMPLE.read_text())
    for entry in example["users"]:
        users[token_for(entry["email"])] = User(
            id=entry["email"].split("@")[0],
            email=entry["email"],
            group_ids=list(entry["groups"]),
            tenant_id=example["tenant"],
        )
    users[token_for(STRANGER)] = User(
        id="stranger", email=STRANGER, group_ids=["it-support-publishers"], tenant_id="door3"
    )
    identity = FakeIdentity(users)
    configured = ConfiguredUserIdentity(identity, required_groups=REQUIRED_GROUPS)
    memberships = InMemoryMembershipStore()
    aliases = FakeAliasStore()
    llm = FakeLLM()
    acl_log = InMemoryAclLog()
    manual = knowledge_store.InMemoryKnowledgeStore()
    monkeypatch.setattr(knowledge_store, "build_knowledge_store", lambda: manual)
    monkeypatch.setenv("CM_PUBLISH_POLICY_FILE", str(PUBLISH_POLICY))
    for name, value in {
        "build_acl_log": lambda: acl_log,
        "build_authz": lambda: authz,
        "build_vector_store": lambda: store,
        "build_embedder": FakeEmbedder,
        "build_reranker": lambda: None,
        "build_group_alias_store": lambda: aliases,
        "build_membership_store": lambda: memberships,
        "build_llm": lambda: llm,
        "build_identity": lambda: configured,
        "build_parser": DoclingParser,
    }.items():
        monkeypatch.setattr(assembly, name, value)
    monkeypatch.setenv("PANSOPH_REQUIRE_AUTH", "true")
    app = FastAPI()
    app.include_router(routes.router)
    demo = Demo(
        store=store,
        authz=authz,
        identity=identity,
        corpus=corpus,
        rest=TestClient(app),
        acl_log=acl_log,
        knowledge=manual,
    )
    await demo.sync()
    return demo


def build_mcp_server(demo: Demo) -> Any:
    from company_memory_mcp.server import build_server

    return build_server(
        token_verifier=IdentityTokenVerifier(
            lambda: ConfiguredUserIdentity(demo.identity, required_groups=REQUIRED_GROUPS)
        ),
        auth=_test_auth(),
        allowed_hosts=["localhost:8765"],
    )


@contextlib.asynccontextmanager
async def mcp_http(demo: Demo) -> AsyncIterator[httpx.AsyncClient]:
    """A raw HTTP client wired to the real MCP ASGI app (auth middleware included)."""
    server = build_mcp_server(demo)
    app = server.streamable_http_app()
    async with server.session_manager.run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://localhost:8765") as c:
            yield c


@contextlib.asynccontextmanager
async def mcp_session(demo: Demo, user: str | None) -> AsyncIterator[Any]:
    """An MCP ClientSession over streamable HTTP to the real app, as `user` (or no auth)."""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    server = build_mcp_server(demo)
    app = server.streamable_http_app()
    headers = {"Authorization": f"Bearer {token_for(user)}"} if user else {}

    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), headers=headers)
    async with server.session_manager.run(), client:
        async with streamable_http_client(
            MCP_URL, http_client=client, terminate_on_close=False
        ) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session


def tool_json(result: Any) -> dict[str, Any]:
    """The structured result of a tool call (falls back to parsing the text content)."""
    data = getattr(result, "structuredContent", None) or json.loads(
        "".join(getattr(c, "text", "") for c in result.content)
    )
    if set(data) == {"result"} and isinstance(data["result"], dict):
        data = data["result"]
    return dict(data)
