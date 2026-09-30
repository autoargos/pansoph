---
name: upstream-sync-review
description: Review divergence between this DOOR3 fork and upstream pansoph (autoargos/pansoph), or review an incoming upstream merge. Lists fork patches, flags changes that will complicate future merges, and checks that upstream changes do not break Company Memory security invariants. Use before merging upstream/main, before committing edits to upstream files, or on request.
---

# Upstream sync review

## Setup

```bash
git remote get-url upstream || git remote add upstream https://github.com/autoargos/pansoph.git
git fetch upstream
```

Fetching is a network read. Do not push, rebase published history, or merge unless the user
asks.

## A. Fork divergence (what we carry)

1. `git log --oneline upstream/main..HEAD` and `git diff --stat upstream/main...HEAD`.
2. Split the touched paths into two groups.
   - **Fork owned:** `docs/company-memory/`, `.claude/`, `tests/permission/test_company_memory_*`,
     `tests/_agora_harness.py`, `door3-*` or `company-memory-*` packages, and deploy overlays.
     These cause no merge risk.
   - **Upstream files:** every one must appear in the fork patch register in
     `docs/company-memory/UPSTREAM_STRATEGY.md`. Unregistered edits are findings.
3. For each upstream file edit, flag:
   - pure formatting, reorders, or renames, which should be reverted;
   - a large hunk where a small one would do;
   - a change that could be an adapter, config, or service instead;
   - a candidate to upstream as a PR.
4. `CLAUDE.md`: Part 2 must stay byte identical to upstream's `CLAUDE.md`.

   ```bash
   diff <(sed -n '/^# Part 2/,$p' CLAUDE.md | tail -n +6) <(git show upstream/main:CLAUDE.md)
   ```

   Adjust the offset if the Part 2 header changes.

## B. Incoming upstream changes (what we would take)

1. `git log --oneline HEAD..upstream/main` and `git diff HEAD...upstream/main --stat`.
2. Predict conflicts: intersect with the upstream files the fork edits.
3. Security triage of the incoming diff. Run the `cm-security-review` steps on any change
   touching retrieval, authz, identity, routes, MCP, ingestion, connectors, or deploy.
   - Does it close a `CM-*` gap? Then the matching strict xfail will fail after the merge.
     Remove the marker, update the review, and drop our fork patch if it is now redundant.
   - Does it reopen one, or add a content path, identity source, or default credential?
4. Dependency changes: look at the `uv.lock` and `pnpm-lock.yaml` diffs and run `pip-audit` on
   the result.

## C. Validate after a merge (on a branch)

```bash
uv sync --all-packages
uv run ruff check . && uv run ruff format --check . && uv run pytest -q
uv run pytest -q -m permission -rxX
```

## Report

Give the fork patch list with its register status, the predicted conflicts, and the security
notes on the incoming changes. For each fork patch, give an action: keep, upstream it, or drop.
