# pansoph Helm chart

Deploys the pansoph core application tier (agora, scribe, lens) and its backing
data services (Postgres with pgvector and pgvectorscale, Valkey, an S3 object
store, and SpiceDB) to Kubernetes.

This chart deploys everything at a single node scale (one replica each). It is a
real, deployable starting point, not a high availability topology: Postgres HA
(Patroni), a SpiceDB cluster, and a Qdrant cluster are on the roadmap and are not
claimed here. In the spirit of pansoph, every part is decoupled: disable any
bundled data service and point at your own managed equivalent, and bring your own
identity provider and your own models.

## What it deploys

| Component | Kind | Notes |
|---|---|---|
| agora | Deployment + Service | the API, MCP, and SSE surface |
| lens | Deployment + Service + ConfigMap | the web UI, nginx proxies /api to agora |
| scribe | Deployment | ingestion and permission sync workers |
| postgres | StatefulSet + Service + PVC | timescaledb-ha, ships pgvector and pgvectorscale |
| valkey | Deployment + Service | cache and queue |
| objectstore | StatefulSet + Service + PVC | SeaweedFS, S3 API |
| spicedb | Deployment + Service | authz, schema migrated by an init container |

Identity and models are not deployed by this chart. Point the config at your OIDC
issuer and your OpenAI compatible model endpoints (see values.yaml).

## Install

```bash
helm install pansoph deploy/helm/pansoph \
  --namespace pansoph --create-namespace \
  --set secret.postgresPassword=$(openssl rand -hex 16) \
  --set secret.spicedbPresharedKey=$(openssl rand -hex 16) \
  --set secret.s3AccessKey=$(openssl rand -hex 12) \
  --set secret.s3SecretKey=$(openssl rand -hex 24)
```

Then open lens:

```bash
kubectl -n pansoph port-forward svc/pansoph-lens 3000:8080
# browse http://localhost:3000/
```

## Common configuration

Point at your own models (bring your own model, any OpenAI compatible endpoint):

```yaml
config:
  llm:      { baseUrl: "http://my-vllm.models.svc:8000/v1", model: "..." }
  embedder: { url: "http://my-tei.models.svc:80/v1", model: "bge-m3", dim: "1024" }
```

Federate identity to your IdP:

```yaml
config:
  identityAdapter: oidc
  oidc: { issuerUrl: "https://id.example.com/realms/main", clientId: "pansoph" }
secret:
  oidcClientSecret: "..."
```

Use an external managed data service instead of a bundled one (any subset):

```yaml
postgres: { enabled: false }
externalPostgres: { host: my-postgres.rds.amazonaws.com, port: 5432 }
```

When a bundled data service is disabled the chart expects the matching external
host, and any credentials it needs, to be provided (via secret values or an
existing secret).

Production secrets: set `secret.existingSecret` to a Secret you manage (for
example via External Secrets or Sealed Secrets) and leave the inline secret values
empty. That secret must carry the same keys the chart would generate, including
`SPICEDB_DATASTORE_CONN_URI` (SpiceDB is distroless and cannot assemble the URI at
runtime).

Expose it with an Ingress:

```yaml
ingress:
  enabled: true
  className: nginx
  host: pansoph.example.com
config:
  publicBaseUrl: "https://pansoph.example.com"
  publicAppUrl: "https://pansoph.example.com"
```

## Validation

This chart is validated three ways: `helm lint`, `helm template` piped through
`kubeconform --strict` (all rendered objects schema check against the Kubernetes
API), and a live install on a kind cluster where all seven workloads reach Ready,
Postgres reports pgvector and pgvectorscale, SpiceDB migrates its schema, and the
lens to agora `/api` proxy returns a healthy response end to end.

See `values.yaml` for the full set of options.
