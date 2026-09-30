# Company Memory POC image: agora (hardened wrapper) and the Company Memory MCP server.
# Strictly locked: `uv sync --frozen` with no unlocked fall back (unlike the upstream
# Dockerfile.agora, CM-H10). Non root at runtime. Build context is the repo root; the
# sibling Dockerfile.dockerignore keeps secrets, state, and tests out of the context.

# uv is pinned to the version that wrote uv.lock, copied from its official image.
FROM ghcr.io/astral-sh/uv:0.11.27 AS uv

FROM python:3.12-slim-bookworm AS build
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
COPY pyproject.toml uv.lock ./
COPY packages ./packages
COPY connectors ./connectors
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --package company-memory-mcp

FROM python:3.12-slim-bookworm AS runtime
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin pansoph
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
USER 10001
EXPOSE 8080 8765
