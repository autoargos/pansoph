## What this changes

A short description of the change and why it is needed.

## Type

- [ ] feat
- [ ] fix
- [ ] docs
- [ ] refactor
- [ ] test
- [ ] perf
- [ ] chore

## Checklist

- [ ] Ran the gates: `uv run ruff check . && uv run ruff format --check . && uv run pytest -q`
- [ ] If this touched the enforcement path (`packages/pansoph-retrieval/.../pipeline.py`) or an authz adapter (`packages/pansoph-authz/`), I added or updated a permission test (`uv run pytest -m permission`).
- [ ] If this touched a connector, I added or updated a conformance run (`uv run pytest -m conformance`) and verified the source ACL mapping against the current official API docs.
- [ ] No em dashes anywhere (code, comments, docs, or this message).
- [ ] Conventional commit, DCO signed off (`git commit -s`), no co-author trailer.
- [ ] No fiction: I did not claim a capability works without a passing test or a run log.

## Security impact

Does this change affect what a user can retrieve, an ACL mapping, credential handling, or the connector/MCP sandbox boundary? If yes, describe it. If you are unsure, say so.
