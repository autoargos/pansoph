"""Resolve the configured adapter for each port and assemble the pipelines.

The core never imports a concrete adapter directly. It loads the class registered
under the port's entry point group, then builds it with config read from the
environment. This keeps every port replaceable: swapping an adapter is a config
change plus a package that registers the entry point, never a code change here.
"""

from __future__ import annotations

import os
from typing import Any

from .registry import load_adapter


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default)


def _postgres_dsn() -> str:
    return (
        f"postgresql://{_env('POSTGRES_USER', 'pansoph')}:{_env('POSTGRES_PASSWORD', 'pansoph')}"
        f"@{_env('POSTGRES_HOST', 'postgres')}:{_env('POSTGRES_PORT', '5432')}"
        f"/{_env('POSTGRES_DB', 'pansoph')}"
    )


# Per-adapter constructor kwargs, read from the environment. Adding a new adapter
# means registering its entry point and adding its kwargs here (or shipping its own
# config convention).
_EMBEDDER_KWARGS: dict[str, Any] = {
    "tei": lambda: {
        "url": _env("PANSOPH_EMBEDDER_URL", "http://tei-embed:80"),
        "model": _env("PANSOPH_EMBEDDER_MODEL", "BAAI/bge-m3"),
        "dim": int(_env("PANSOPH_EMBEDDER_DIM", "1024")),
    },
    "openai_compatible": lambda: {
        "base_url": _env("PANSOPH_EMBEDDER_URL"),
        "model": _env("PANSOPH_EMBEDDER_MODEL"),
        "api_key": _env("PANSOPH_EMBEDDER_API_KEY"),
        "dim": int(_env("PANSOPH_EMBEDDER_DIM", "1024")),
    },
}
_RERANKER_KWARGS: dict[str, Any] = {
    "tei": lambda: {
        "url": _env("PANSOPH_RERANKER_URL", "http://tei-rerank:80"),
        "model": _env("PANSOPH_RERANKER_MODEL", "BAAI/bge-reranker-v2-m3"),
    },
}
_VECTOR_KWARGS: dict[str, Any] = {
    "pgvectorscale": lambda: {
        "dsn": _postgres_dsn(),
        "dim": int(_env("PANSOPH_EMBEDDER_DIM", "1024")),
    },
    "qdrant": lambda: {
        "url": _env("QDRANT_URL", "http://qdrant:6333"),
        "api_key": _env("QDRANT_API_KEY"),
        "dim": int(_env("PANSOPH_EMBEDDER_DIM", "1024")),
    },
}
_AUTHZ_KWARGS: dict[str, Any] = {
    "spicedb": lambda: {
        "endpoint": _env("SPICEDB_ENDPOINT", "spicedb:50051"),
        "preshared_key": _env("SPICEDB_PRESHARED_KEY"),
    },
    "openfga": lambda: {
        "api_url": _env("OPENFGA_API_URL"),
        "store_id": _env("OPENFGA_STORE_ID"),
    },
}
_LLM_KWARGS: dict[str, Any] = {
    "openai_compatible": lambda: {
        "base_url": _env("PANSOPH_LLM_BASE_URL"),
        "model": _env("PANSOPH_LLM_MODEL"),
        "api_key": _env("PANSOPH_LLM_API_KEY"),
    },
}


def _build(group: str, name: str, kwargs_map: dict[str, Any]) -> Any:
    cls = load_adapter(group, name)
    factory = kwargs_map.get(name)
    return cls(**factory()) if factory else cls()


def build_embedder() -> Any:
    return _build("pansoph.embedders", _env("PANSOPH_EMBEDDER_ADAPTER", "tei"), _EMBEDDER_KWARGS)


def build_reranker() -> Any:
    name = _env("PANSOPH_RERANKER_ADAPTER")
    if not name:
        return None
    return _build("pansoph.rerankers", name, _RERANKER_KWARGS)


def build_vector_store() -> Any:
    return _build(
        "pansoph.vectorstores", _env("PANSOPH_VECTOR_ADAPTER", "pgvectorscale"), _VECTOR_KWARGS
    )


def build_authz() -> Any:
    return _build("pansoph.authz", _env("PANSOPH_AUTHZ_ADAPTER", "spicedb"), _AUTHZ_KWARGS)


def build_llm() -> Any:
    return _build("pansoph.llms", _env("PANSOPH_LLM_ADAPTER", "openai_compatible"), _LLM_KWARGS)


def build_parser() -> Any:
    return _build("pansoph.parsers", _env("PANSOPH_PARSER_ADAPTER", "docling"), {})
