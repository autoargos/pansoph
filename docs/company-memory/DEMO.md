# Company Memory live demo (local, synthetic data)

This is a 5 to 10 minute runbook. Everything runs on your laptop, bound to `127.0.0.1`.
The data is fictional: there are no real people, projects, or DOOR3 documents.

**Needs:** Docker Desktop (about 4 GB RAM free), `uv`, and Claude Code.

**First run:** it builds one image and pulls Keycloak, SpiceDB, Postgres, and a small
embedding model, which takes a few minutes. On Apple silicon the embedder runs under
amd64 emulation. That is slower to start, but fine for this corpus.

All commands run from the repo root. `make -C deploy/company-memory <target>` wraps the
same commands.

## 0. One time setup

```bash
uv sync --all-packages
uv run company-memory init
```

`init` generates secrets and user passwords into `deploy/company-memory/.secrets/`
(gitignored). They are never printed.

## 1. Start the environment

```bash
uv run company-memory up
```

## 2. Seed the synthetic data

```bash
uv run company-memory seed
```

The seed does three things:

- restores the baseline users and groups in Keycloak;
- creates the `cm_synthetic` connector instance;
- syncs the 11 corpus documents through the real ingestion path: ACL diff, then SpiceDB,
  then pgvector.

It is safe to re-run: a second seed reports 0 changed documents.

| User | Groups | Sees, besides the three public docs |
|---|---|---|
| alice | engineering, project-atlas | Atlas ADR-007, Atlas architecture, IT Ops runbook, known issues |
| bob | engineering, project-beacon | Beacon postmortem INC-4471, Beacon architecture, runbook, known issues |
| carol | leadership | the FY27 financial plan (the one with `PROJECT ORION BUDGET RESERVE IS 7.3M`) |
| dave | people-strategy | the capability plan, the IT Ops runbook |

Every user is also in `everyone`. Group grants add up (union): `engineering` alone does
not reach the Atlas or Beacon documents.

## 3. Authenticate as Alice

```bash
uv run company-memory login alice
uv run company-memory whoami
```

`whoami` shows the groups Keycloak reports live.

## 4. Connect the MCP client (once)

```bash
uv run company-memory mcp-config
```

Run the `claude mcp add-json --scope local ...` line it prints. Claude Code then calls
`company-memory token-headers` (its `headersHelper`) on every connection, which sends the
signed in user's token in the `Authorization` header.

The token never appears in the config, in a prompt, or in a tool argument. Check it with:

```bash
claude mcp get company-memory
```

It should show `Connected`.

## 5. Query 1 (as Alice)

Start `claude` in the repo and ask:

> Which architecture decisions were made on Project Atlas?

Claude calls `search_company_memory`. It answers with ADR-007 (passkey-first OIDC) and
ADR-003 (DynamoDB event sourcing), citing source ids such as
`cm_synthetic:synthetic:atlas-adr-007-authentication`.

## 6. The restricted query

Ask, still as Alice:

> What is the Project Orion budget reserve?

Claude finds only the public project directory's note that Orion details are restricted to leadership. The 7.3M figure never reaches it, because Alice is not in `leadership`. You can also call a tool directly,
with no LLM involved:

```bash
uv run company-memory ask "Project Orion budget reserve" --tool evidence
uv run company-memory ask cm_synthetic:synthetic:leadership-fy27-financial-plan --tool source
```

The second command returns `Source not found or not accessible.`, the same answer a
nonexistent id gets.

## 7. Revoke a permission

```bash
uv run company-memory revoke alice project-atlas
```

This removes Alice from the group in Keycloak. No resync or rebuild is needed.

## 8. Rerun the same query

> What authentication architecture does Project Atlas use?

No Atlas documents come back, and no Atlas citations or ids appear. It is the same token
and the same query: groups are read from Keycloak userinfo on every request, and the
stale SpiceDB membership is deleted before the check (CM-H1).

Restore the membership afterwards:

```bash
uv run company-memory grant alice project-atlas
```

A **source** ACL change works too. Edit a file's `allow_groups` under
`deploy/company-memory/corpus/`, then run `uv run company-memory sync`.

## 9. Switch user

```bash
uv run company-memory login bob
```

In Claude Code, run `/mcp` and reconnect `company-memory`, then ask the question from
step 5 again. Bob gets no Atlas decisions: only the public project directory, which says
the details are restricted. Try carol and dave the same way.

## 10. IT Ops and People Strategy questions

As **bob** (IT Ops):

- Which project had a database incident?
- What remediation was used?
- Which runbook applies?
- Summarize known infrastructure issues I can access.

As **dave** (People Strategy):

- What technologies have been used across the projects I can access?
- Which project experience mentions React and AWS?
- Summarize capability evidence from accessible sources.

Dave gets evidence from the public project directory and the capability plan, never
Atlas, Beacon, or leadership detail. The MCP server tells the client to return evidence
only: no scoring, ranking, or judgement of people.

To see the audit trail (who queried, when, which tool, the tenant, the decision, and the
returned source ids; never tokens):

```bash
uv run company-memory audit
```

To run the automated end to end check: the permission matrix for all four users, REST
and MCP parity, the doc id bypass, revocation with restore, and the knowledge write flow
from step 11.

```bash
uv run company-memory verify
```

## 11. Add knowledge through Claude (MCP write)

Company Memory can accept new knowledge from a signed in user and serve it to others only
as their permissions allow. The flow is:

```
Claude Code -> MCP add_company_memory_knowledge (as the signed in user)
  -> manual Company Memory source (a record: text, creator, tenant, scope, provenance)
  -> pansoph IngestionPipeline.ingest_one (parse, chunk, embed, ACL tuples, ACL log)
  -> pgvector chunks + SpiceDB reader tuples
  -> the unchanged read path: search_company_memory, get_company_memory_source
```

**Who may publish where.** Without a scope, knowledge is private to its creator. A shared
scope needs an explicit publish grant: membership in a `*-publishers` group that
`deploy/company-memory/publish-policy.json` names for that scope, plus membership of the
scope itself. Reading a group never grants publishing. `leadership`, `people-strategy`,
and tenant wide audiences (`everyone`) are not publishable at all.

| User | Can publish to |
|---|---|
| alice | private, `project-atlas` (holds `project-atlas-publishers`) |
| bob | private, `project-beacon` (holds `project-beacon-publishers`) |
| carol, dave | private only |

Grants are ordinary Keycloak groups, so `company-memory grant alice engineering-publishers`
adds one and `revoke` removes it on the next request.

**A. Start and seed** (steps 1 and 2). `seed` also creates any missing publisher groups.

**B. Sign in as Alice** and reconnect the MCP server in Claude Code (`/mcp`).

```bash
uv run company-memory login alice
```

**C. Add the document.** In Claude Code:

> Read `deploy/company-memory/demo-input/atlas-authentication.md` and add the document to
> Company Memory for Project Atlas.

Claude reads the file itself and calls `add_company_memory_knowledge` with its text and
`scope: "project-atlas"`. The server never receives a path. The result shows the
`source_id`, `scope: group:project-atlas`, and `created_by` (from the token).

**D. Ask as Alice:** "What authentication technology does Project Atlas use and why?"
Expected: AWS Cognito with passkeys (ADR-012, codename HARBOR-KEY), cited to the new
`cm_manual:manual:...` source, often alongside ADR-007, which it supersedes.

**E. Sign in as Bob** (`login bob`, then `/mcp` reconnect) and ask the same question.
Expected: no Atlas knowledge, no title, no citation. Only the public directory.

**F. Grant Bob**, reconnect, ask again. Expected: the grounded answer, cited to the manual
source.

```bash
uv run company-memory grant bob project-atlas
```

**G. Revoke Bob**, reconnect, ask again. Expected: the knowledge is gone on the next query.
Even a direct `get_company_memory_source` with the id returns "not found or not
accessible".

```bash
uv run company-memory revoke bob project-atlas
```

Without Claude, the CLI makes the same calls (it reads the file client side):

```bash
uv run company-memory add deploy/company-memory/demo-input/atlas-authentication.md --scope project-atlas --as alice
uv run company-memory knowledge --as alice
uv run company-memory knowledge --as alice --delete cm_manual:manual:<id>
```

`company-memory verify` runs the whole write flow automatically with throwaway probe
documents (private default, publish grant, denied publishes, grant, revoke, delete).

### Observed in a real Claude Code session (2026-09-29)

Headless `claude -p` against this stack, with the local `company-memory` MCP entry and
only the Company Memory tools allowed:

1. `login alice`. Claude read the file with its Read tool and called
   `add_company_memory_knowledge` (title, 1792 characters of content, description, tags,
   `scope: "project-atlas"`, `original_filename`). Result: `status: added`,
   `scope: group:project-atlas`, `created_by: alice@company-memory.test`. Provenance
   recorded the client as `claude-code/2.1.195`.
2. Alice asked the question. Claude searched, read both sources, and answered "AWS
   Cognito with passkeys", with the three ADR-012 reasons and the note that it supersedes
   ADR-007. Both source ids were cited, and it flagged the new source as `user_supplied`.
3. `login bob`. The same question: Claude found only the public project directory and
   said Atlas architecture decisions are restricted. No Cognito, HARBOR-KEY, or ADR-012
   text appeared in any tool result.
4. `grant bob project-atlas`. The same question: a full, correct answer cited to
   `cm_manual:manual:k1e9f8aa1a92774d9f577acbc` and ADR-007.
5. `revoke bob project-atlas`. The same question, and this time the prompt also handed
   Claude the source id: search returned only the directory, and
   `get_company_memory_source` returned "Source not found or not accessible." Claude
   answered that the information is restricted. Bob's own session guessed the word
   "Cognito" in a search query; the server returned nothing matching it.

On the first attempt Claude sent `tags` as a comma separated string and got a
validation error; the tool now accepts either a list or a comma separated string.

### Remaining risks for manual knowledge

- Submitted text is untrusted. It is stored and indexed as text, and results carry
  `trust: user_supplied` and the untrusted content notice, but a reader's agent can still
  be influenced by instructions in it. Nothing here filters prompt injection.
- An authorized publisher can add misleading content to a shared scope. Provenance shows
  who added it; there is no review or approval step.
- Only the creator can delete, and for shared knowledge only while still a publisher of
  that scope. There is no admin override and no edit tool (re adding identical content is
  idempotent; changed content is a new document).
- The MCP process now holds write access to SpiceDB and pgvector (it did not need it for
  read only tools).
- Title, description, and tags are shown to authorized readers but only the title and
  content are indexed.

## 12. Real-user local demo (optional)

Real DOOR3 identities, synthetic knowledge. Everything still runs on `127.0.0.1`; nothing
connects to a DOOR3 system, and no real Slack, Jira, GitHub, Drive, or Confluence data is
ingested. The synthetic Alice/Bob demo above keeps working unchanged in the same stack.

**How it is separated.** Real users live in the same local Keycloak but in their own
tenant (`door3`, from `users.local.json`), carried in the token by an admin only
`tenant` attribute. The synthetic users stay in tenant `default`. Tenants never share
documents, groups, or SpiceDB memberships, so a synthetic user in a group called
`everyone` cannot read DOOR3 IT Support knowledge, and the reverse.

**Identity.** The canonical principal is the verified email, lowercased:

```
OIDC token (signature, issuer, audience, expiry, subject; email_verified must be true)
  -> company_memory identity adapter (email lowercased; must be in a configured group)
  -> Company Memory principal: someone@door3.com, tenant door3
  -> SpiceDB subject: user:door3_someone@door3.com
```

A token without a verified email is rejected. Tool arguments never set the email,
creator, or tenant.

**`everyone`.** "Configured" means "in the `everyone` directory group", and only
configured users authenticate at all (`CM_REQUIRED_GROUPS`). A valid Keycloak account
outside `everyone` gets a 401 from MCP and REST alike, exactly like an invalid token.
Anonymous requests are never `everyone`. The seeder service account authenticates through
`cm-service-accounts`, a group that grants no reads.

**IT Support permissions** (`publish-policy.json`, tenant `door3`):

| | Who |
|---|---|
| Read `it-support` knowledge | every configured user (group `everyone`), authenticated |
| Publish to `it-support` | members of `it-support-publishers` (who are also in `everyone`) |
| Publish anywhere else | nobody: no other `door3` scope is publishable |
| Private notes | the creator only, even though every user is in `everyone` |

Reading `it-support` never implies publishing: `everyone` can never be a publisher group,
and the policy refuses a publisher group that is also a reader group.

### Steps

**1. Configure yourself** (writes the gitignored `deploy/company-memory/users.local.json`
and generates a local password into `.secrets/real-user-passwords.json`; neither is ever
printed or committed):

```bash
uv run company-memory user add you@door3.com --group it-support-publishers
uv run company-memory user list
```

**2. Initialize or reset the realm.** Keycloak imports the realm only when its container
is created, so after the first real user, or after pulling realm changes, recreate it.
Data in Postgres (indexed documents, SpiceDB) is kept:

```bash
uv run company-memory init
uv run company-memory down
```

**3. Start the stack and seed:**

```bash
uv run company-memory up
uv run company-memory seed
```

Later `user add|grant|revoke|remove` commands also apply to the running Keycloak at once
(they take effect on the user's next request), so only the first run needs a reset.

**4. Log in** (the CLI signs in with the generated password; you never type or see it):

```bash
uv run company-memory login you@door3.com
```

**5. Verify the authenticated identity:**

```bash
uv run company-memory whoami
```

It shows the token's email, `email verified: True`, `token tenant: door3`, the live
groups, and what the MCP server sees (`MCP principal: you@door3.com (tenant door3)`,
`MCP may publish: ['private', 'it-support']`).

**6. Connect or reconnect Claude Code.** The entry from step 4 of the synthetic demo is
reused as is: `headersHelper` runs `company-memory token-headers`, which prints the
Authorization header for whoever is signed in right now. There is no token in the Claude
configuration. After `login` (or `logout`), run `/mcp` and reconnect `company-memory`: the
next MCP connection carries the new user's token, and the server authorizes every request
as that user. A Claude session that is not reconnected keeps using the previous user's
header until it reconnects or gets a 401 (which re runs the helper). After `logout` the
helper prints no header, so the server answers 401.

**7. Publish the IT Support guide** (synthetic, clearly labelled) and query it:

```bash
uv run company-memory add deploy/company-memory/demo-input/it-support-quick-guide.md --scope it-support --title "DOOR3 IT Support Quick Guide"
```

In Claude Code: "What is the name of the IT Support demo VPN profile?" Expected:
NORTHSTAR, cited to the `cm_manual:manual:...` guide.

**8. Add IT Support knowledge through Claude:** "Add this knowledge to IT Support: ..."
Expected: `status: added`, `scope: group:it-support`, `created_by` your email. Asking to
publish to `leadership` is denied before anything is stored, and "save this for me only"
creates a private note.

**9. Verify citations and permissions end to end:**

```bash
uv run company-memory verify-real --as you@door3.com
```

It creates a throwaway configured reader (in `everyone`, no publisher grant), checks read,
publish, denied publish, private isolation, MCP and REST parity, and the 401 for an
unconfigured account, then deletes its probes and the reader.

**10. Log out and shut down:**

```bash
uv run company-memory logout
uv run company-memory down
```

### Adding another real user

```bash
uv run company-memory user add colleague@door3.com
uv run company-memory user grant colleague@door3.com it-support-publishers
uv run company-memory login colleague@door3.com
```

Every added user is in `everyone` (a reader of `it-support`). The grant is optional and
makes them a publisher; `user revoke colleague@door3.com it-support-publishers` takes it
back on their next request, and `user remove colleague@door3.com` ends all access. No
source change is needed. On a shared laptop, each person should run their own checkout:
the generated passwords sit in that checkout's `.secrets/`.

### Replacing local Keycloak with production SSO later (not done here)

The services only need an OIDC provider that issues access tokens with the
`company-memory` audience, `email` plus `email_verified`, a `tenant` claim, and groups on
the userinfo endpoint. For Google Workspace or Okta that means: register Company Memory
as an OIDC resource or API with that audience; map the directory's verified email (both
already verify it); emit a fixed `tenant` claim (`door3`) through a claim mapping; sync
the `everyone`, `it-support-publishers`, and project groups from directory groups; and
switch the CLI password login to authorization code with PKCE or the device flow. The
identity adapter, the publish policy, and the authorization path do not change; only
`OIDC_*` settings do. Until a security review approves a pilot, the knowledge stays
synthetic.

### Observed with a real DOOR3 identity (2026-09-29)

With a real DOOR3 identity (`<dmytro>@door3.com`, from the local `users.local.json`) configured as an IT Support publisher, the live stack, and
headless Claude Code using only the `company-memory` tools:

- **Identity:** `get_company_memory_identity` returned principal
  `<dmytro>@door3.com` (the real verified email), tenant `door3`, groups `everyone` and
  `it-support-publishers`, publishable scopes `private` and `it-support`.
  `company-memory whoami` showed `email verified: True` and `token tenant: door3`.
- **Read:** "What is the name of the IT Support demo VPN profile?" returned
  **NORTHSTAR**, cited to `cm_manual:manual:k24841fed26786c7a56cba2fb` ("DOOR3 IT Support
  Quick Guide"), and Claude noted it is synthetic.
- **Publish:** "Add this knowledge to IT Support: ..." (a printer note) returned
  `status: added`, `scope: group:it-support`.
- **Denied:** publishing to `leadership` returned
  "You are not authorized to publish to 'leadership'." and nothing was stored.
- **Private:** "Save this for me only" created a private note. A throwaway configured
  reader then saw both IT Support documents but not the private note, in search or by its
  id ("Source not found or not accessible.").
- `company-memory verify-real` passed all 18 checks; `company-memory verify` (synthetic)
  still passed.

## 13. Shut down

```bash
uv run company-memory down
uv run company-memory down --volumes
claude mcp remove company-memory -s local
```

The first command keeps the data. The second deletes the indexed data and Keycloak
state; the next `up` plus `seed` restores it. The third removes the client entry.

## Troubleshooting

| Problem | Fix |
|---|---|
| Claude says the server needs auth | Run `uv run company-memory login <user>`, then `/mcp`, then reconnect. |
| `seed` waits on `embedder` | The first start downloads the model. Watch it with `docker compose -f deploy/company-memory/compose.yaml --env-file deploy/company-memory/.secrets/stack.env logs -f tei-embed`. |
| `user add` says the realm cannot store the tenant attribute | The realm predates the real-user profile. Run `company-memory down`, then `up`, then `seed`. |
| A real user's login fails with `invalid_grant` | The user is not in the running realm yet: `company-memory user list`, then `down` and `up` (or re run `user add` while Keycloak is up). |
| `grant`/`revoke` says unknown group `*-publishers` | The realm was imported before those groups existed. Run `uv run company-memory seed` (it creates them). |
| Secrets rotated with `init --force` | Run `down --volumes`, then `up`, then `seed`. |

## 14. Live demo checklist

### Before the meeting

```bash
uv run company-memory up
uv run company-memory demo-reset
uv run company-memory login <your-email>
uv run company-memory demo-check
```

`up` is only needed if the stack is not already running. `demo-check` must end with
`DEMO READY` (exit 0). Then, in Claude Code, run `/mcp` and reconnect `company-memory`.

`demo-reset` restores the known state:
- Keycloak: synthetic users get exactly their baseline groups, configured real users get
  exactly their `users.local.json` groups, and leftover temporary accounts are deleted.
- Knowledge: the synthetic corpus is re-synced, and exactly one "DOOR3 IT Support Quick
  Guide" is kept (re-published as the first configured `it-support-publishers` member,
  same id every run). Every other manually added document is purged: notes, private
  notes, Alice's Atlas document, and verification probes, along with their chunks and
  grants. SpiceDB memberships are reconciled to the baseline.
- It never touches `users.local.json` and refuses to run anywhere except the local demo
  stack.

`demo-check` is read only. It covers services, identity, permissions, the baseline
knowledge and its citation, a clean state, loopback-only ports, the startup guards, the
disabled grab route, generated secrets, and the MCP tool schemas. Every failure prints why
it matters and the fix. `uv run company-memory demo-ready` runs both in one step.

### Demo 1: existing shared knowledge

Ask Claude: "What is the name of the IT Support demo VPN profile?"
Expected: **NORTHSTAR**, cited to the "DOOR3 IT Support Quick Guide" source.

### Demo 2: add new organizational knowledge

Paste a short synthetic note and ask: "Add this knowledge to Company Memory under IT
Support." For example: "Synthetic demo note: the demo guest Wi-Fi network is called
LANTERN-GUEST and its password rotates every Monday at 07:00." Confirm Claude calls
`add_company_memory_knowledge` and the result says `status: added`, `scope:
group:it-support`, and `created_by` is your email.

### Demo 3: retrieve it

Ask: "What is the demo guest Wi-Fi network called and when does its password rotate?"
Expected: LANTERN-GUEST, Mondays at 07:00, cited to the new `cm_manual:manual:...` source.

### Demo 4 (optional): a second, reader-only real user

```bash
uv run company-memory user add colleague@door3.com
uv run company-memory login colleague@door3.com
```

Reconnect `/mcp`. The colleague reads IT Support (NORTHSTAR, the new note), is denied
publishing to IT Support, and cannot see the first user's private notes. Afterwards run
`user remove colleague@door3.com` if they are not needed again. This is not required
for `DEMO READY`.

### After the meeting

```bash
uv run company-memory demo-reset
uv run company-memory logout
```

### Observed rehearsal (2026-09-29, real DOOR3 identity, headless Claude Code)

1. `demo-reset` removed 3 leftover manual documents (a printer note, a private note,
   Alice's Atlas document) and the memberships of 4 deleted temporary users, and kept the
   guide. A second `demo-reset` removed nothing, and the stored state was byte for byte
   identical.
2. After `login`, `demo-check` printed `DEMO READY` (exit 0). A second run changed no
   stored state, token files, or `users.local.json`. With nobody signed in, it exited 1
   and pointed every identity item at the login command.
3. Claude answered NORTHSTAR with the guide's citation, added the LANTERN-GUEST note to
   IT Support through `add_company_memory_knowledge`, and answered the Wi-Fi question from
   it with its citation.
4. `demo-check` then reported `Clean state NOT READY (1 extra manual doc)`. `demo-reset`
   removed that one document, the note was no longer retrievable (search found nothing;
   the source id answered "not found or not accessible"), and `demo-check` was
   `DEMO READY` again.
5. `verify` and `verify-real` both passed, and the next `demo-reset` found nothing to clean
   up (0 users, 0 documents, 0 memberships).
