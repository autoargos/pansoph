"""ACL aware hybrid retrieval."""

from __future__ import annotations

from .eval import GoldenItem, evaluate, ndcg_at_k, recall_at_k, reciprocal_rank
from .ports import Embedder, Reranker, SearchHit, VectorStore
from .rrf import reciprocal_rank_fusion

__all__ = [
    "VectorStore",
    "Embedder",
    "Reranker",
    "SearchHit",
    "reciprocal_rank_fusion",
    "GoldenItem",
    "evaluate",
    "recall_at_k",
    "reciprocal_rank",
    "ndcg_at_k",
]
