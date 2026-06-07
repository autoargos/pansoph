# pansoph-retrieval

ACL aware hybrid retrieval as a standalone library. Bring a vector store, an embedder, a reranker, and an authz port, and get permission correct results.

## The pipeline

1. Embed the query.
2. Hybrid search: dense plus sparse with a coarse pre filter on tenant id and allow_groups, fused with reciprocal rank fusion (k equals 60).
3. Overfetch the top candidates 3 to 5 times over.
4. One bulk check against the authz port at a freshness of at_least_as_fresh, keep the allowed subset.
5. Rerank the allowed subset with a cross encoder.
6. Return chunks with citations that deep link to the exact passage.

The pre filter gives speed and recall. The authz check is the source of truth and closes the leak that metadata alone cannot. For small corpora (under roughly 200k tokens) the whole corpus can be passed to the model and retrieval skipped.

## Ports

- `VectorStore`: upsert and hybrid search with metadata filtering. Defaults: pgvectorscale (local), Qdrant (scale).
- `Embedder`: text to vectors, model agnostic. Default served via HF TEI.
- `Reranker`: rerank candidates, model agnostic. Default served via HF TEI. Optional, config gated.

The authz port comes from `pansoph-authz`.
