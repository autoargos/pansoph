"""Reciprocal rank fusion. Three lines of real logic, kept honest and testable."""

from __future__ import annotations

from collections.abc import Sequence


def reciprocal_rank_fusion(
    ranked_lists: Sequence[Sequence[str]], k: int = 60
) -> list[tuple[str, float]]:
    """Fuse multiple ranked id lists into one, by RRF with constant k.

    Returns (id, score) sorted by descending score. k=60 is the standard default.
    """
    scores: dict[str, float] = {}
    for ranking in ranked_lists:
        for rank, doc_id in enumerate(ranking):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
