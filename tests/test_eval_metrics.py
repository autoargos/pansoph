"""Retrieval eval metrics: known values and edge cases."""

from __future__ import annotations

from pansoph_retrieval.eval import ndcg_at_k, recall_at_k, reciprocal_rank


def test_recall_at_k() -> None:
    assert recall_at_k(["a", "b", "c"], ["a", "c"], 8) == 1.0
    assert recall_at_k(["a", "x"], ["a", "c"], 8) == 0.5
    assert recall_at_k(["a"], [], 8) == 0.0  # no relevant -> 0, never divide by zero


def test_recall_respects_k() -> None:
    # The relevant item sits at rank 3, so recall@2 misses it.
    assert recall_at_k(["x", "y", "a"], ["a"], 2) == 0.0
    assert recall_at_k(["x", "y", "a"], ["a"], 3) == 1.0


def test_reciprocal_rank() -> None:
    assert reciprocal_rank(["a"], ["a"]) == 1.0
    assert reciprocal_rank(["x", "a"], ["a"]) == 0.5
    assert reciprocal_rank(["x", "y"], ["a"]) == 0.0


def test_ndcg() -> None:
    assert ndcg_at_k(["a", "x"], ["a"], 2) == 1.0  # relevant first -> ideal
    assert round(ndcg_at_k(["x", "a"], ["a"], 2), 4) == 0.6309
    assert ndcg_at_k(["x", "y"], ["a"], 2) == 0.0
    assert ndcg_at_k(["a"], [], 2) == 0.0
