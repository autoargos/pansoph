# Contributing to pansoph

Thank you for considering a contribution. pansoph is a self-hostable, modular knowledge
platform whose single defining feature is document-level access control enforced at
retrieval, built from the ground up and fully removable, under Apache-2.0. This guide is
the practical "how to get a change merged" companion to `CLAUDE.md`, which is the
engineering north star. Read `CLAUDE.md` for the why; read this for the how.

Writing convention, enforced everywhere including commit messages: **no em dashes**. Use
commas, periods, or parentheses.

## Ground rules in one paragraph

Permission correctness is sacred (never return a document the authz port did not allow).
pansoph is model-agnostic (no vendor is hardcoded as required; "tested with X" is not
"requires X"). Every role is a port and every default is one adapter behind it, so you
add capability through entry points, never by editing the core. And no fiction: do not
claim a capability works without a passing test or a run log to back it.

## Development setup

You need Python 3.12+ and [uv](https://docs.astral.sh/uv/). This is a uv workspace, so
one sync wires up every package.

```bash
# install uv if you do not have it
curl -LsSf https://astral.sh/uv/install.sh | sh

# from the repo root: sync every workspace package plus the dev group
uv sync --all-packages --dev
```

All commands below assume the repo root (`pansoph/`). If `uv` is not on your PATH after
install, add it, for example `export PATH="$HOME/.local/bin:$PATH"`.

## The gates you must pass before you push

Run the same checks CI runs. Keep the tree green.

```bash
# the core local gate (lint, format, fast tests)
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
```

`pytest -q` runs the unit suite (everything not marked `integration`). Two additional
gates are separated out because a green check on each one means something specific.

### The permission gate (highest priority)

This is the headline suite. It asserts that a user can never retrieve another user's
restricted document, that revocations propagate, and that tenants and connector
instances are isolated.

```bash
uv run pytest -m permission -q
```

**Any change to the retrieval enforcement path
(`packages/pansoph-retrieval/src/pansoph_retrieval/pipeline.py`) or to an authz adapter
(`packages/pansoph-authz/`) must come with a permission test.** A pull request that
touches enforcement without a permission test will not be merged. Note that today this
suite runs on in-memory fakes and validates enforcement logic, not a live SpiceDB or
OpenFGA engine. Live-engine integration tests are in progress, so if your change depends
on engine-specific behavior (consistency tokens, schema), say so explicitly in the PR.

### The conformance gate (connectors)

Every connector passes one shared, hermetic, offline conformance suite. It exercises the
contract, incremental sync, deletions, and ACL mapping with no live credentials and no
network.

```bash
uv run pytest -m conformance -q
```

A connector that does not pass conformance does not merge.

### Type checking

`mypy` is the direction of travel and is informational today, becoming a hard gate as
adapters land. New code should type check.

```bash
uv run mypy packages
```

## How to contribute, by kind of change

pansoph is ports and adapters. The contribution model follows from that: **you extend
pansoph by adding an adapter behind a port and registering it as a Python entry point.
You do not edit the core to add a capability.** If you find yourself needing to edit
`pansoph-core` to make your adapter work, that is a signal the port surface is wrong;
open an issue to discuss the contract rather than coupling the core to your adapter.

### Adding an adapter (Embedder, Reranker, LLM, VectorStore, Authz, Identity, and so on)

1. Implement the port's `typing.Protocol` in your own package or under the right
   `packages/` directory. Keep the surface minimal.
2. Register the entry point in that package's `pyproject.toml`, in the port's namespaced
   group (for example `pansoph.authz`, `pansoph.vector`).
3. Keep the adapter standalone. Do not import a concrete adapter from the core, and do
   not add a hard dependency from one building-block package to another unless it is
   part of that block's real contract. Shared shapes live in `pansoph-types`, which
   depends on nothing.
4. Add real unit tests (boundaries, error paths, invariants, not `assert a == a`). If
   the adapter is an authz adapter, add a permission test.

### Adding a connector (read the official docs first, map ACLs faithfully)

This rule is mandatory and is a security rule, not a style preference. A connector built
from stale assumptions is worse than no connector, because it fails silently or leaks.

1. **Read the source's latest official API documentation first. Do not implement from
   memory.** Verify the exact endpoints, the authentication method and required scopes,
   the pagination model, the incremental sync mechanism (cursors, change feeds,
   webhooks), the rate limits, and the permission or ACL model.
2. **Record the documentation links and the API version you targeted at the top of the
   connector module, with the date you verified them.**
3. **Map the source's real permission model to ACLs faithfully**, at the granularity the
   source exposes (user, team, group, org). Credentials are RBAC-scoped and vary, so do
   not assume a scope. Each configured instance has its own credential and its own ACL
   namespace, so different scopes never collide. Getting the ACL mapping wrong is a
   security bug.
4. Prefer a declarative YAML manifest for a simple REST source so it inherits
   pagination, retries, backoff, and incremental cursors for free. Drop to Python only
   for awkward sources.
5. Register the `pansoph.connectors` entry point and pass `assert_conforms` (the
   conformance gate above).
6. Re-verify against the docs whenever you change the connector. If the docs and the
   code disagree, the docs win.

### Adding a new heavy dependency (a datastore, a framework, a broker)

Write an ADR first and open it for discussion. Small libraries that implement an adapter
are fine without an ADR.

## Commit and pull request conventions

### Conventional commits, no co-author trailer

Use [Conventional Commits](https://www.conventionalcommits.org/): `feat:`, `fix:`,
`docs:`, `refactor:`, `test:`, `perf:`, `chore:`. No em dashes in the message. **Do not
add a co-author trailer.** Keep diffs small and atomic, one logical change per commit.

```text
feat(authz): add Cerbos adapter behind AuthzPort

Implements bulk_check via the Cerbos PlanResources API and maps the
plan AST to a final-filter set. Adds a permission test covering deny.
```

### DCO sign-off (recommended)

For an Apache-2.0 project with no relicensing plan, we use the
[Developer Certificate of Origin](https://developercertificate.org/) rather than a CLA.
The DCO is a lightweight, per-commit assertion that you wrote the patch or otherwise
have the right to submit it under the project's license. It is lower friction than a CLA
and is the common choice for permissive, stable licenses.

Sign off by adding `-s` (or `--signoff`) to your commit. This appends a
`Signed-off-by: Your Name <your@email>` trailer using your configured git identity.

```bash
git commit -s -m "fix(retrieval): apply deny as a final filter, never an exclusion tuple"
```

If you forget on a single commit, amend it:

```bash
git commit --amend -s --no-edit
```

The `Signed-off-by` line is the one trailer we do want. It is not a "co-author" trailer
and is unaffected by the no-co-author rule above.

### Pull requests

- Describe the change, the security impact if any, and how you tested it.
- Confirm the gates pass locally (paste the relevant output if it helps).
- If you touched enforcement or a connector, name the permission or conformance test you
  added.
- One reviewer is the maintainer for now. Be patient, this is a small project.

## Code of conduct

By participating you agree to abide by the project's Code of Conduct, the
[Contributor Covenant 3.0](CODE_OF_CONDUCT.md). Report conduct concerns to
`kyashkarande@gmail.com`. For security vulnerabilities, use the private channels in
`SECURITY.md` instead.

## Reporting security issues

Do not open a public issue for a security vulnerability. See `SECURITY.md` for the
private reporting channels and our coordinated-disclosure policy.

## Sources (verified 2026-06-23)

- Developer Certificate of Origin 1.1: https://developercertificate.org/
- OpenSSF Best Practices criteria (names DCO as the most common, easily-implemented
  contributor-authorization approach; CLA listed as the alternative):
  https://www.bestpractices.dev/en/criteria/0
- Conventional Commits 1.0.0: https://www.conventionalcommits.org/en/v1.0.0/
- uv workspaces documentation: https://docs.astral.sh/uv/concepts/projects/workspaces/
