"""Smoke tests for the pure-logic building blocks.

These are intentionally small. The comprehensive, power-packed suite is task #21.
They exist so CI collects real assertions today and the core math stays honest.
"""

from __future__ import annotations

from pansoph_ingestion.chunking import chunk_sections
from pansoph_retrieval.rrf import reciprocal_rank_fusion
from pansoph_types import Document, Section


def test_rrf_ranks_consensus_above_single_list_leader() -> None:
    # "b" is near the top of both lists; "a" tops only the first. Consensus wins.
    fused = reciprocal_rank_fusion([["a", "b", "c"], ["b", "c", "a"]], k=60)
    order = [doc_id for doc_id, _ in fused]
    assert order[0] == "b"
    assert set(order) == {"a", "b", "c"}


def test_rrf_empty_input_is_empty() -> None:
    assert reciprocal_rank_fusion([]) == []


def test_chunking_keeps_small_sections_together_and_splits_on_budget() -> None:
    sections = [
        Section(text="alpha " * 10),
        Section(text="beta " * 10),
        Section(text="gamma " * 200),
    ]
    chunks = chunk_sections(sections, target_tokens=50)
    # The two small sections fit in one chunk; the oversized section is split into bounded
    # pieces (no chunk exceeds the target) rather than becoming one giant chunk, and no
    # content is lost. Splitting oversized sections is the fix for the raw-page mega-chunk.
    assert "alpha" in chunks[0] and "beta" in chunks[0]
    assert all(len(c.split()) <= 50 for c in chunks)
    assert sum(c.count("gamma") for c in chunks) == 200


def test_document_id_is_stable_and_namespaced() -> None:
    assert Document.make_id("github", "org1", "42") == "github:org1:42"
