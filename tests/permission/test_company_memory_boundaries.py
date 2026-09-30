"""Company Memory security boundaries, end to end through the agora REST and MCP surfaces.

The upstream red team exercises `retrieve()` directly. These go one level up and drive the
real routes and the real MCP tool (with in-memory fakes behind the assembly seam), because
that is where identity is resolved and where a response, a citation, or an LLM prompt can
leak. Each test pins an invariant Company Memory depends on. The known gaps these surfaced
live in test_company_memory_known_gaps.py.

See docs/company-memory/SECURITY_AND_FIT_REVIEW.md.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _agora_harness import (  # noqa: E402
    ALICE_DOC,
    ALICE_SECRET,
    ENG_DOC,
    ENG_SECRET,
    HANDBOOK_DOC,
    T2_DOC,
    T2_SECRET,
    build_harness,
    doc_ids_in,
    sse_events,
)
from pansoph_authz import Principal  # noqa: E402
from pansoph_identity.port import User  # noqa: E402
from pansoph_types import AccessControl  # noqa: E402

_SECRETS = (ALICE_SECRET, ENG_SECRET, T2_SECRET)

pytestmark = pytest.mark.permission


async def test_user_cannot_retrieve_another_users_restricted_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = await build_harness(monkeypatch)
    assert h.search_ids("tok-alice") == {ALICE_DOC, ENG_DOC, HANDBOOK_DOC}
    resp = h.search("tok-bob")
    assert {r["doc_id"] for r in resp.json()["results"]} == {HANDBOOK_DOC}
    # Nothing about the restricted documents leaks through the response body: no text, no
    # id (which carries the source path), and no count beyond what bob may read.
    for marker in (*_SECRETS, "alice-private", "eng-runbook", "t2-roadmap"):
        assert marker not in resp.text


async def test_chat_never_exposes_unauthorized_text_to_the_llm_or_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = await build_harness(monkeypatch)
    resp = h.chat("tok-bob")
    assert resp.status_code == 200
    sources = {e["doc_id"] for e in sse_events(resp) if e["type"] == "source"}
    assert sources == {HANDBOOK_DOC}, "citations are only for documents bob may read"
    # Authorization happens before the model sees anything: the prompt holds no restricted text.
    assert h.llm.prompts, "the LLM was called"
    for marker in _SECRETS:
        assert all(marker not in p for p in h.llm.prompts)
        assert marker not in resp.text


async def test_authenticated_tenant_is_bound_to_the_token_end_to_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = await build_harness(monkeypatch)
    # eve belongs to tenant t2 and shares the group name "eng" with alice's tenant. Asking
    # for the default tenant in the body must change nothing.
    ids = h.search_ids("tok-eve", tenant_id="default", groups=["eng"], user="alice@door3.test")
    assert ids == {T2_DOC}
    assert T2_DOC not in h.search_ids("tok-alice", tenant_id="t2")


async def test_mcp_enforces_exactly_the_rest_authorization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = await build_harness(monkeypatch)
    for token in ("tok-alice", "tok-bob", "tok-eve"):
        rest = h.search_ids(token)
        mcp_text = await h.mcp_search(token)
        assert doc_ids_in(mcp_text) == rest, f"MCP and REST disagree for {token}"
    bob_text = await h.mcp_search("tok-bob")
    for marker in _SECRETS:
        assert marker not in bob_text


@pytest.mark.parametrize("token", ["", "not-a-real-token"])
async def test_mcp_rejects_missing_or_invalid_token(
    monkeypatch: pytest.MonkeyPatch, token: str
) -> None:
    h = await build_harness(monkeypatch)
    text = await h.mcp_search(token)
    assert text.startswith("Authentication required")
    for marker in (*_SECRETS, "handbook"):
        assert marker not in text


@pytest.mark.parametrize("header", [None, "not-a-real-token"])
async def test_require_auth_mode_rejects_tokenless_and_invalid_tokens(
    monkeypatch: pytest.MonkeyPatch, header: str | None
) -> None:
    """With PANSOPH_REQUIRE_AUTH=true there is no body identity fall back (fail closed)."""
    h = await build_harness(monkeypatch)
    monkeypatch.setenv("PANSOPH_REQUIRE_AUTH", "true")
    before = len(h.authz.members)
    body = {"user": "alice@door3.test", "groups": ["eng"], "tenant_id": "default"}
    assert h.search(header, **body).status_code == 401
    assert h.chat(header, **body).status_code == 401
    assert len(h.authz.members) == before, "a rejected request must not write memberships"
    # A valid token still works in this mode.
    assert h.search_ids("tok-alice") == {ALICE_DOC, ENG_DOC, HANDBOOK_DOC}


async def test_leaving_a_group_removes_access_on_the_next_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = await build_harness(monkeypatch)
    assert ENG_DOC in h.search_ids("tok-alice")
    h.identity.users["tok-alice"] = User(id="alice", email="alice@door3.test", group_ids=[])
    ids = h.search_ids("tok-alice")
    assert ENG_DOC not in ids
    assert ALICE_DOC in ids, "a direct grant is unaffected by group removal"


async def test_acl_revocation_propagates_to_the_same_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = await build_harness(monkeypatch)
    assert ENG_DOC in h.search_ids("tok-alice")
    # The source narrows the runbook to one person. A re-sync must revoke the group grant.
    h.connectors["main"].put(
        "eng-runbook", ENG_SECRET, AccessControl(allow_users=["carol@door3.test"])
    )
    await h.sync("main", "default")
    resp = h.search("tok-alice")
    assert ENG_DOC not in {r["doc_id"] for r in resp.json()["results"]}
    assert ENG_SECRET not in resp.text


async def test_deleted_source_content_is_not_retrievable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = await build_harness(monkeypatch)
    assert ALICE_DOC in h.search_ids("tok-alice")
    h.connectors["main"].remove("alice-private")
    await h.sync("main", "default")
    resp = h.search("tok-alice")
    assert ALICE_DOC not in {r["doc_id"] for r in resp.json()["results"]}
    assert ALICE_SECRET not in resp.text
    assert ALICE_SECRET not in await h.mcp_search("tok-alice")


# Formerly strict xfail gaps, fixed in the fork (see UPSTREAM_STRATEGY.md patch register).


async def test_cm_h1_authz_engine_forgets_a_removed_group_membership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = await build_harness(monkeypatch)
    h.search_ids("tok-alice")
    h.identity.users["tok-alice"] = User(id="alice", email="alice@door3.test", group_ids=[])
    h.search_ids("tok-alice")
    # The REST result is correct today only because the vector pre-filter drops the doc.
    # The authz engine, the declared source of truth, still says alice may read it.
    allowed = await h.authz.bulk_check(
        subject=Principal(type="user", id="alice@door3.test", ns="default"),
        permission="read",
        object_ids=[ENG_DOC],
    )
    assert ENG_DOC not in allowed


async def test_cm_h2_revocation_applies_even_when_new_content_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = await build_harness(monkeypatch)
    assert ENG_DOC in h.search_ids("tok-alice")
    # The source revokes the group AND the document now parses to nothing (emptied, or an
    # unparseable binary). The revocation must still land.
    h.connectors["main"].put("eng-runbook", "   ", AccessControl(allow_users=["carol@x.test"]))
    await h.sync("main", "default")
    resp = h.search("tok-alice")
    assert ENG_SECRET not in resp.text


async def test_cm_h3_removed_text_is_not_retrievable_after_resync(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = await build_harness(monkeypatch)
    filler = " ".join(["word"] * 600)
    h.connectors["main"].put(
        "policy",
        f"# Intro\n{filler}\n# Retired\nREMOVED_SECRET old credential rotation note",
        AccessControl(public=True),
    )
    await h.sync("main", "default")
    assert "REMOVED_SECRET" in h.search("tok-bob").text
    h.connectors["main"].put("policy", "# Intro\nshort policy", AccessControl(public=True))
    await h.sync("main", "default")
    assert "REMOVED_SECRET" not in h.search("tok-bob").text
