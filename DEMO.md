# pansoph demo runbook (5 minutes, mixed audience)

**The one line:** pansoph is a self-hostable knowledge platform where a user can only
ever retrieve what they are already allowed to see, across every connected source,
with document-level permissions enforced at retrieval and given away under Apache-2.0.

**The headline moment you are building to:** Alice and Bob ask the same question of the
same assistant. Alice gets the confidential roadmap with a citation. Bob, asking the
identical question, provably cannot see it. Same system, same data, different
permitted view, enforced by the engine, not by hiding a button.

---

## 0. Pre-flight (do this ~10 minutes before, not live)

From the repo root (`pansoph/`):

```bash
# 1. Provide an EMBEDDER. Ingestion embeds every chunk, so a reachable embedding model
#    is required or nothing indexes (you would see "All connection attempts failed" on
#    every sync). The default .env points the embedder at a local Ollama with bge-m3:
ollama serve &            # if not already running (the Ollama app also does this)
ollama pull bge-m3        # one-time, ~1.2 GB
#    Alternatively, bring the model servers up in-compose with the `models` profile and
#    repoint PANSOPH_EMBEDDER_URL at the in-cluster TEI/Ollama.

# 2. Bring up the stack (first run pulls images; allow a few minutes).
#    The chat LLM defaults to cloud Gemini (in .env) for answer quality; the embedder is
#    the local Ollama above. Both are swappable, pansoph is model-agnostic.
docker compose --profile core --profile workers up -d

# 3. Wait for green, then confirm agora is healthy.
curl -s localhost:8080/api/health | python3 -m json.tool   # expect "ok": true
```

> The chat model is cloud Gemini here purely for demo answer quality; the embedder is a
> local Ollama (bge-m3). pansoph is model-agnostic and one env var flips either to a
> different provider. A banner in the System screen shows when a cloud model is active.

Seed the demo corpus if this is a fresh stack (idempotent; skips if already present):

```bash
./deploy/seed-demo.sh
```

Confirm the connector instances and their scopes are present (the story depends on
these): a public company wiki, six department knowledge bases each scoped to a group,
and the public GitHub repo.

```bash
curl -s localhost:8080/api/connectors/instances | python3 -c '
import sys, json
for i in json.load(sys.stdin)["instances"]:
    print("  %-16s %-11s %s" % (i["id"], i["scope_type"], i.get("scope_ref") or ""))'
# expect (8 instances):
#   nw-company       org
#   nw-engineering   group        engineering
#   nw-product       group        product       <- holds the confidential roadmap
#   nw-finance       group        finance
#   nw-hr            group        hr
#   nw-sales         group        sales
#   nw-security      group        leadership
#   spoon-knife      org
```

Sanity-check the headline before you walk in (cold check, ~5 s):

```bash
tok() { curl -s -X POST http://localhost:8090/realms/pansoph/protocol/openid-connect/token \
  -d grant_type=password -d client_id=lens -d "username=$1" -d "password=$1" \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])'; }
for u in alice bob; do T=$(tok $u);
  curl -s -X POST localhost:8080/api/search -H "authorization: Bearer $T" \
    -H 'content-type: application/json' -d '{"query":"confidential roadmap"}' \
  | python3 -c "import sys,json;d=json.load(sys.stdin)['results'];print('$u roadmap visible =', any('roadmap' in r['doc_id'] for r in d))"; done
# expect: alice True, bob False
```

Open two browser windows side by side, both at **http://localhost:3000**:
- Left window: sign in as **alice / alice** (do this now so it is ready).
- Right window: use a private/incognito window, sign in as **bob / bob**.

Have these tabs ready: lens Chat (both), and in Alice's window the **Connectors** and
**Access** screens. For the full set of queries per persona, keep
[`docs/demo-queries.md`](docs/demo-queries.md) open.

---

## 1. Credentials

| Who | User / pass | Role | Groups (what they can read) |
|---|---|---|---|
| Alice (eng lead) | `alice` / `alice` | **admin** | engineering, product, leadership, g1 |
| Bob (sales) | `bob` / `bob` | **member** | sales |
| Carol (finance/people) | `carol` / `carol` | **member** | finance, hr |
| Dave (engineer) | `dave` / `dave` | **connector_admin** | engineering |
| Keycloak admin | `admin` / `admin` at http://localhost:8090 | - | the IdP admin console (only if asked) |

lens is at http://localhost:3000. Alice is the only admin (she sees the Access
screen). Roles gate management; groups gate what documents each user can retrieve.
Keycloak is the demo IdP and is swappable; pansoph only knows the issuer and client id.

---

## 2. The script (5 minutes)

### Beat 1 — Frame it (30 s)
"Every org's knowledge is scattered across Drive, GitHub, Confluence, wikis. The open
tools that do retrieval well treat permissions as a paid add-on. pansoph makes
document-level access control the free, built-in, headline feature, and you run it on
your own hardware with your own models."

### Beat 2 — Alice asks (60 s)
In Alice's window, on **Chat**, ask:
> *"Summarize our confidential product roadmap."*

She gets an answer **with an inline citation** to the roadmap source. Point at the
citation: "Notice the answer is grounded in a real source, and she can open it because
she is allowed to."

Glance at the header: Alice carries a management role chip (**admin**) and document
group chips (**engineering, product, leadership**). "Two different things: roles decide
what she can *manage*, groups decide what she can *read*."

### Beat 3 — Bob asks the identical question (60 s) — THE MOMENT
In Bob's window, on **Chat**, ask the **exact same question**:
> *"Summarize our confidential product roadmap."*

Bob gets an honest answer that **it has no such source** (no roadmap citation). "Same
question, same assistant, same index. Bob is in sales, not product, so the roadmap
never even enters his candidate set. This is not a hidden button, it is the retrieval
engine refusing to return a document he is not allowed to see."

> Even starker: run a Search in both windows for *confidential roadmap*. Alice's
> results include the roadmap; Bob's do not. The same holds for Carol and Dave: only
> Alice is in the product group.

### Beat 4 — Why it is trustworthy (60 s)
In Alice's window, open **Access**. Show:
- **Your access**: her roles and groups, side by side.
- **Connector instances by scope**: the same backend configured as independent
  instances, one scoped to the whole **organization** (the public company wiki) and
  one per **group** (engineering, product, finance, hr, sales, security). "Each
  instance has its own credentials and its own permission namespace, so a finance
  instance can never leak into an engineering query."
- **Check access** tool: type `bob`, groups `sales`, query *confidential roadmap*,
  click Check. It returns nothing. Now type `alice`, groups `product`, Check. The
  roadmap appears. "This runs the exact same permission-enforced path the chat uses,
  so you can audit who-can-see-what live, for any user, without impersonating them."

### Beat 5 — Management is role-gated (30 s)
Open **Connectors** in Alice's window: she can add, sync, and delete (she is admin).
Mention: "Bob does not even see this screen, and if he forged the API call, agora
rejects it by role. Roles flow from the identity provider's token; swap Keycloak for
Zitadel or your own and nothing else changes."

### Beat 6 — Land it (20 s)
"So: connect any source, keep each source's real permissions, and a user can only ever
retrieve what they are already allowed to see. Self-hosted, model-agnostic,
Apache-2.0, and every part is a replaceable building block."

---

## 3. Reset between runs

Sign both users out (top-right **Sign out**). No data reset is needed; the index and
permissions are stable. If you created connectors live and want a clean slate, delete
them from the Connectors screen as Alice.

---

## 4. If something misbehaves (quick reference)

| Symptom | Fix |
|---|---|
| lens shows "agora unreachable" | `docker compose restart agora`; recheck `curl localhost:8080/api/health` |
| Login bounces / "HTTPS required" | `docker compose up -d --force-recreate keycloak keycloak-config` (re-seeds the realm) |
| Data missing / nothing retrievable | run `./deploy/seed-demo.sh`, then retry |
| Alice cannot see the roadmap | re-sync as Alice: Connectors -> nw-product -> Sync now, then retry |
| Bob can see the roadmap | confirm Bob's only group is sales (header chips); he must not be in product |
| Chat returns nothing for anyone | the LLM endpoint may be down; Search still proves the permission story without the model |

Full bring-up logs: `docker compose logs -f agora`.

---

## 5. If asked (mixed-audience Q&A talking points)

- **How is permission enforced?** Retrieval overfetches candidates, runs one
  authorization bulk check against the ReBAC engine (SpiceDB) at a freshness tied to
  the last permission write, keeps only the allowed subset, then reranks. The vector
  pre-filter is a speed optimization, never the source of truth, and explicit denies
  are applied as a final filter. A revocation is reflected on the next read.
- **Is it really replaceable?** Yes. Connector, Parser, Embedder, Reranker, LLM,
  VectorStore, Authz, Identity, ObjectStore, SecretStore are all ports with adapters,
  discovered as plugins. The UI itself is a replaceable consumer of a stable REST +
  MCP + SSE API. Swap the model to a cloud endpoint with one env var (a banner shows
  when cloud is on); swap the IdP from Keycloak to Zitadel without touching the core.
- **Roles vs groups?** Orthogonal building blocks. Groups gate what documents you can
  read (the ACL). Roles (admin / connector_admin / member) gate what management
  actions you can perform. Both arrive in the IdP token; pansoph maps them, nothing is
  hardcoded to a vendor.
- **Connectors?** One SDK, many instances, three tiers: native (GitHub, Google Drive,
  Confluence, Granola), reach-anything (public link fetcher + an MCP bridge that turns
  any MCP server into a source), and a consent-based browser grabber. Each instance is
  scoped (org / group / individual) and confined to one folder / space / repo.
- **What is not built yet (be honest)?** Per-connector depth is shallow in the local
  demo (one repo/drive each), durable execution and credential sealing are in progress,
  and the citation hover-preview/deep-link polish and the Refine/assistant-ui frontend
  adoption are the next UI step. The permission core is real and tested (red-team
  suite gates releases).
```
