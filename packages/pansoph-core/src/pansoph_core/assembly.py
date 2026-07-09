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
from .secrets import PostgresSecretBackend


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
_IDENTITY_KWARGS: dict[str, Any] = {
    "builtin": lambda: {},
    "oidc": lambda: {
        "issuer_url": _env("OIDC_ISSUER_URL"),
        "client_id": _env("OIDC_CLIENT_ID"),
        "groups_claim": _env("OIDC_GROUPS_CLAIM", "groups"),
        "jwks_url": _env("OIDC_JWKS_URL"),
        "roles_claim": _env("OIDC_ROLES_CLAIM", "roles"),
    },
}


_SECRET_KWARGS: dict[str, Any] = {
    # Envelope encryption, root-key based, no external service. The root key is
    # supplied as raw bytes from the environment (an age key or a KMS export).
    "envelope": lambda: {
        "root_key": _env("PANSOPH_ENVELOPE_ROOT_KEY").encode("utf-8"),
        # Durable Postgres backend so sealed secrets survive an agora restart.
        "backend": PostgresSecretBackend(_postgres_dsn()),
    },
    # OpenBao (the open-source Vault). Self-persisting and natively async, the
    # production default for centralized, audited, rotatable secret storage.
    "openbao": lambda: {
        "addr": _env("OPENBAO_ADDR", "http://openbao:8200"),
        "token": _env("OPENBAO_TOKEN"),
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


def build_acl_log() -> Any:
    """Build the durable AclLog: the last-written ACL per document, for revocation
    diffing on re-sync. Postgres-backed, keyed by doc_id, never scanned, so it adds no
    hot-path cost. Imported directly (not via the registry) because it is an internal
    ingestion detail, not a user-swappable port surface like the vector store or authz.
    """
    from pansoph_ingestion.adapters_acl_log import PostgresAclLog

    return PostgresAclLog(_postgres_dsn())


def build_group_alias_store() -> Any:
    """Build the identity-group alias store: the operator declared edges from an IdP
    group to a connector source group. Read at query time to expand a subject's effective
    groups. Postgres-backed and connector agnostic (opaque group id strings)."""
    from .group_aliases import GroupAliasStore

    return GroupAliasStore(_postgres_dsn())


def build_llm() -> Any:
    return _build("pansoph.llms", _env("PANSOPH_LLM_ADAPTER", "openai_compatible"), _LLM_KWARGS)


def build_parser() -> Any:
    return _build("pansoph.parsers", _env("PANSOPH_PARSER_ADAPTER", "docling"), {})


def build_identity() -> Any:
    return _build("pansoph.identity", _env("PANSOPH_IDENTITY_ADAPTER", "builtin"), _IDENTITY_KWARGS)


def build_secret_store() -> Any:
    """Build the configured SecretStore (envelope by default, openbao for production).

    Both satisfy the same SecretStore port, so the credential provider and the
    connectors never know which is in use. Selected by PANSOPH_SECRET_ADAPTER. Built
    lazily, only when a connector actually declares an AuthRequirement, so a
    deployment with no auto-refresh connector needs no secret backend configured.
    """
    return _build(
        "pansoph.secretstores", _env("PANSOPH_SECRET_ADAPTER", "envelope"), _SECRET_KWARGS
    )


def build_oauth_flow() -> Any:
    """Build the OAuthFlow used by the connect-a-source routes.

    Wires the durable Postgres state store, the configured secret store, a fresh
    httpx client, and the connector manager, using the public base URL an operator
    configures so the redirect_uri matches what is registered with each provider.
    Built per call (cheap) so a config change is picked up without a restart.
    """
    import httpx

    from .connectors import ConnectorManager
    from .oauth import OAuthFlow, PostgresStateStore

    dsn = _postgres_dsn()
    return OAuthFlow(
        manager=ConnectorManager(dsn),
        state_store=PostgresStateStore(dsn),
        secret_store=build_secret_store(),
        http=httpx.AsyncClient(),
        public_base_url=_env("PANSOPH_PUBLIC_BASE_URL", "http://localhost:8080"),
    )


_credential_provider: Any = None


def build_credential_provider() -> Any:
    """Return the process-wide CredentialProvider, building it on first use.

    One provider per process so its token cache and single-flight refresh locks are
    shared across every connector instance, holding one httpx client.
    """
    global _credential_provider
    if _credential_provider is None:
        import httpx
        from pansoph_connector_sdk import CredentialProvider

        _credential_provider = CredentialProvider(build_secret_store(), httpx.AsyncClient())
    return _credential_provider
