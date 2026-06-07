"""Reciprocal rank fusion properties."""

from __future__ import annotations

from pansoph_retrieval.rrf import reciprocal_rank_fusion


def test_consensus_across_lists_outranks_a_single_list_leader() -> None:
    fused = reciprocal_rank_fusion([["a", "b"], ["b", "a"], ["b", "c"]])
    assert fused[0][0] == "b"


def test_scores_are_descending() -> None:
    fused = reciprocal_rank_fusion([["a", "b", "c"], ["a", "b", "c"]])
    scores = [s for _, s in fused]
    assert scores == sorted(scores, reverse=True)


def test_smaller_k_increases_top_rank_separation() -> None:
    # With smaller k, the rank-1 item's score is higher relative to later items.
    low_k = dict(reciprocal_rank_fusion([["a", "b"]], k=1))
    high_k = dict(reciprocal_rank_fusion([["a", "b"]], k=1000))
    assert low_k["a"] - low_k["b"] > high_k["a"] - high_k["b"]


def test_item_in_more_lists_accumulates_more_score() -> None:
    fused = dict(reciprocal_rank_fusion([["a"], ["a"], ["b"]]))
    assert fused["a"] > fused["b"]


def test_empty_and_mixed_empty_lists() -> None:
    assert reciprocal_rank_fusion([]) == []
    assert reciprocal_rank_fusion([[], []]) == []
    assert reciprocal_rank_fusion([[], ["a"]]) == [("a", 1.0 / 61)]
