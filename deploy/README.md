# deploy

How pansoph ships.

## Local and demo: Docker Compose

```bash
cp .env.example .env   # then edit, or use the prefilled local .env
docker compose --profile core,workers,models up
```

Profiles: `core` (postgres, valkey, object store, spicedb, agora, lens), `workers` (scribe), `models` (ollama, tei-embed, tei-rerank), `observability` (otel collector), `secrets` (openbao). Bring up only what you need.

Images built here:

- `deploy/Dockerfile.agora` -> `cyproto/pansoph-agora` (backend)
- `deploy/Dockerfile.scribe` -> `cyproto/pansoph-scribe` (workers)
- `deploy/Dockerfile.lens` -> `cyproto/pansoph-lens` (web UI)

All run as a non root user, and agora and lens expose healthchecks.

## CI and CD (GitHub Actions)

- `.github/workflows/ci.yml` runs ruff, mypy (informational for now), and pytest on every push and PR, plus a separate permission red team gate.
- `.github/workflows/release.yml` builds and pushes the three images to Docker Hub on a `v*` tag, multi arch (amd64 and arm64), with an SBOM (CycloneDX via Syft) and keyless cosign signatures.

Required repository secrets for release:

- `DOCKER_USERNAME` the Docker Hub user (cyproto).
- `DOCKER_PAT` a Docker Hub access token with push scope.

cosign signing uses GitHub OIDC, so no signing key secret is needed.

## Scale: Helm (planned, task #18)

A Helm chart for Kubernetes will split the services, scale scribe workers horizontally with an HPA, run Qdrant and a SpiceDB cluster, use OpenBao for secrets, and enforce egress allowlists with network policies. Postgres HA via Patroni.

## Hardening (planned, task #20)

Untrusted connector and MCP code runs out of process in a gVisor or Firecracker sandbox, never in agora. Egress is allowlisted (only provider APIs and configured sources, metadata IP blocked). Postgres row level security with FORCE per tenant. Per connector credential isolation with short lived scoped tokens. Immutable audit log.
