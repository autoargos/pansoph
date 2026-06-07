"""Retrieval quality evaluation.

Information-retrieval metrics computed on a golden set: recall@k, mean reciprocal
rank, and NDCG@k. These are corpus-specific and the thing to trust, since public
benchmark rankings do not predict a given corpus. RAGAS and DeepEval (optional
extras) add LLM-judged answer-quality metrics on top; this module is the IR core and
the CI gate.
"""

from __future__ import annotations

import math
from collections.abc import Awaitable, Callable, Sequence

from pydantic import BaseModel, Field


def recall_at_k(retrieved: Sequence[str], relevant: Sequence[str], k: int) -> float:
    rel = set(relevant)
    if not rel:
        return 0.0
    hits = sum(1 for d in retrieved[:k] if d in rel)
    return hits / len(rel)


def reciprocal_rank(retrieved: Sequence[str], relevant: Sequence[str]) -> float:
    rel = set(relevant)
    for i, doc in enumerate(retrieved):
        if doc in rel:
            return 1.0 / (i + 1)
    return 0.0


def ndcg_at_k(retrieved: Sequence[str], relevant: Sequence[str], k: int) -> float:
    rel = set(relevant)
    dcg = sum(1.0 / math.log2(i + 2) for i, doc in enumerate(retrieved[:k]) if doc in rel)
    ideal = sum(1.0 / math.log2(i + 2) for i in range(min(len(rel), k)))
    return dcg / ideal if ideal > 0 else 0.0


class GoldenItem(BaseModel):
    """One evaluation case: a query, who is asking, and the documents that should
    come back. relevant_doc_ids are the ground-truth ids for this subject."""

    query: str
    relevant_doc_ids: list[str]
    user: str = "eval"
    groups: list[str] = Field(default_factory=list)
    tenant_id: str = "default"


async def evaluate(
    retrieve_fn: Callable[[GoldenItem], Awaitable[Sequence[str]]],
    golden: Sequence[GoldenItem],
    k: int = 8,
) -> dict[str, float]:
    """Run retrieve_fn over the golden set and return aggregate metrics.

    retrieve_fn maps a GoldenItem to the ordered doc ids it retrieved, so the harness
    is agnostic to how retrieval is wired (real adapters, or fakes in a test).
    """
    recalls: list[float] = []
    rrs: list[float] = []
    ndcgs: list[float] = []
    for item in golden:
        retrieved = list(await retrieve_fn(item))
        recalls.append(recall_at_k(retrieved, item.relevant_doc_ids, k))
        rrs.append(reciprocal_rank(retrieved, item.relevant_doc_ids))
        ndcgs.append(ndcg_at_k(retrieved, item.relevant_doc_ids, k))
    n = len(golden) or 1
    return {
        "recall_at_k": sum(recalls) / n,
        "mrr": sum(rrs) / n,
        "ndcg_at_k": sum(ndcgs) / n,
        "n": float(len(golden)),
    }
