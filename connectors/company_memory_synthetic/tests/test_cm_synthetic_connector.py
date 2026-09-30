"""The synthetic corpus connector: contract, incremental cursor, tombstones, and safety."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from pansoph_connector_cm_synthetic.connector import (
    CorpusError,
    SyntheticCorpusConnector,
    parse_document,
)
from pansoph_connector_sdk import assert_conforms
from pansoph_types import DocRef

CORPUS = Path(__file__).resolve().parents[3] / "deploy" / "company-memory" / "corpus"


def _conn(path: Path) -> SyntheticCorpusConnector:
    return SyntheticCorpusConnector("synthetic", {"corpus_dir": str(path)})


async def _changes(conn: SyntheticCorpusConnector, cursor: str | None) -> tuple[list[DocRef], str]:
    refs, last = [], cursor or ""
    async for ref, cur in conn.list_changes(cursor):
        refs.append(ref)
        last = cur
    return refs, last


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    dst = tmp_path / "corpus"
    shutil.copytree(CORPUS, dst)
    return dst


@pytest.mark.conformance
async def test_conforms(corpus: Path) -> None:
    await assert_conforms(_conn(corpus))


async def test_every_demo_document_parses_with_a_grant(corpus: Path) -> None:
    conn = _conn(corpus)
    refs, _ = await _changes(conn, None)
    assert len(refs) == 11
    for ref in refs:
        raw, acl = await conn.fetch(ref)
        assert raw.text and raw.text.startswith("Title: ")
        assert not acl.public, "the demo never uses tenant wide public"
        assert acl.allow_groups, f"{ref.source_doc_id} would reach nobody"


async def test_incremental_cursor_acl_change_and_tombstone(corpus: Path) -> None:
    conn = _conn(corpus)
    _, cursor = await _changes(conn, None)
    assert (await _changes(conn, cursor))[0] == [], "an unchanged corpus yields nothing"
    path = corpus / "atlas-adr-007-authentication.md"
    path.write_text(path.read_text().replace("project-atlas", "leadership"))
    (corpus / "it-onboarding.md").unlink()
    refs, cursor2 = await _changes(conn, cursor)
    assert {(r.source_doc_id, r.deleted) for r in refs} == {
        ("atlas-adr-007-authentication", False),
        ("it-onboarding", True),
    }
    _, acl = await conn.fetch(DocRef(source_doc_id="atlas-adr-007-authentication", instance_id="s"))
    assert acl.allow_groups == ["leadership"]
    assert (await _changes(conn, cursor2))[0] == []


def test_front_matter_is_strict() -> None:
    meta, body = parse_document("---\ntitle: T\nallow_groups: a, b\n---\nbody")
    assert meta["allow_groups"] == ["a", "b"] and body == "body"
    for bad in ("no front matter", "---\ntitle: T\n", "---\nfoo: x\ntitle: T\n---\n"):
        with pytest.raises(CorpusError):
            parse_document(bad)


async def test_path_traversal_and_symlinks_are_refused(corpus: Path, tmp_path: Path) -> None:
    secret = tmp_path / "outside.md"
    secret.write_text("---\ntitle: x\nallow_groups: everyone\n---\nOUTSIDE")
    (corpus / "link.md").symlink_to(secret)
    conn = _conn(corpus)
    refs, _ = await _changes(conn, None)
    assert "link" not in {r.source_doc_id for r in refs}
    for stem in ("../outside", "link", "UPPER"):
        with pytest.raises(CorpusError):
            await conn.fetch(DocRef(source_doc_id=stem, instance_id="s"))


def test_corrupt_cursor_is_refused_not_ignored(corpus: Path) -> None:
    conn = _conn(corpus)

    async def run() -> None:
        async for _ in conn.list_changes("not-base64-json"):
            pass

    import asyncio

    with pytest.raises(CorpusError):
        asyncio.run(run())
