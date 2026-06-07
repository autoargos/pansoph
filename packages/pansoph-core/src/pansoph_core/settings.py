"""Configuration. Local first defaults, cloud opt in. Mirrors .env.example."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PANSOPH_", env_file=".env", extra="ignore")

    env: str = "local"
    log_level: str = "info"
    api_host: str = "0.0.0.0"  # noqa: S104  binding all interfaces is intended for the container
    api_port: int = 8080

    # Port adapter selection. Each names an entry point in its group.
    vector_adapter: str = "pgvectorscale"
    authz_adapter: str = "spicedb"
    identity_adapter: str = "builtin"
    llm_adapter: str = "openai_compatible"
    embedder_adapter: str = "tei"
    reranker_adapter: str = "tei"
    parser_adapter: str = "docling"
    object_adapter: str = "s3"
    secret_adapter: str = "envelope"  # noqa: S105  adapter name, not a credential

    def is_cloud_model(self) -> bool:
        """True when the LLM base url is not a loopback or in cluster address.

        Used to show the visible cloud banner required by the model agnostic rule.
        """
        import os

        base = os.getenv("PANSOPH_LLM_BASE_URL", "")
        return not any(h in base for h in ("localhost", "127.0.0.1", "ollama", "vllm"))
