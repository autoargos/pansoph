# deploy

How pansoph ships.

## Local and demo: Docker Compose

```bash
cp .env.example .env   # then edit, or use the prefilled local .env
docker compose --profile core --profile workers --profile models up
```

Profiles: `core` (postgres, valkey, object store, spicedb, agora, lens), `workers` (scribe), `models` (ollama, tei-embed, tei-rerank), `observability` (otel collector), `secrets` (openbao). Bring up only what you need.

Images built here:

- `deploy/Dockerfile.agora` -> `cyproto/pansoph-agora` (backend)
- `deploy/Dockerfile.scribe` -> `cyproto/pansoph-scribe` (workers)
- `deploy/Dockerfile.lens` -> `cyproto/pansoph-lens` (web UI)

All run as a non root user, and agora and lens expose healthchecks.

## CI and CD (GitHub Actions)

- `.github/workflows/ci.yml` runs ruff, mypy (informational for now), and pytest on every push and PR, plus a separate permission red team gate.
- `.github/workflows/publish-dev.yml` builds and pushes the rolling `:dev` images (the tag compose references) to Docker Hub, multi arch. Trigger it manually with the "Run workflow" button at any time, or opt into automatic publishing on every push to `main` by setting the repository variable `PUBLISH_DEV_IMAGES` to `true`. It runs no supply chain gate, so it stays fast.
- `.github/workflows/release.yml` builds and pushes the three images to Docker Hub on a `v*` tag, multi arch (amd64 and arm64), tagged with the semver version and `latest`, with an SBOM (CycloneDX via Syft), a Grype scan that fails on a fixable High or Critical, and keyless cosign signatures.

Required repository secrets for both publish workflows:

- `DOCKER_USERNAME` the Docker Hub user (cyproto).
- `DOCKER_PAT` a Docker Hub access token with push scope.

cosign signing uses GitHub OIDC, so no signing key secret is needed. Until these secrets are set, `publish-dev` stays dormant (it never fails a build), and cutting a `v*` tag is what produces the signed release images and the `:latest` tag.

To pull the prebuilt images instead of building locally:

```bash
docker compose pull
docker compose --profile core --profile workers --profile models up
```

## Kubernetes: Helm

A Helm chart lives in [`deploy/helm/pansoph`](helm/pansoph/README.md). It deploys the core application tier (agora, scribe, lens) and its backing data services (Postgres with pgvector and pgvectorscale, Valkey, an S3 object store, and SpiceDB), at a single node scale. Identity and models are bring your own, and any bundled data service can be disabled in favor of an external managed one.

```bash
helm install pansoph deploy/helm/pansoph --namespace pansoph --create-namespace
```

The chart is validated with `helm lint`, `kubeconform --strict` over the rendered manifests, and a live kind install where all seven workloads reach Ready and the lens to agora proxy answers end to end. See the chart README for configuration.

Still ahead for the scale tier (not claimed by the chart yet): Postgres HA via Patroni, a Qdrant and a SpiceDB cluster, horizontal scribe autoscaling with an HPA, OpenBao for secrets, and egress allowlists enforced with network policies.

## Hardening (planned, task #20)

Untrusted connector and MCP code runs out of process in a gVisor or Firecracker sandbox, never in agora. Egress is allowlisted (only provider APIs and configured sources, metadata IP blocked). Postgres row level security with FORCE per tenant. Per connector credential isolation with short lived scoped tokens. Immutable audit log.
