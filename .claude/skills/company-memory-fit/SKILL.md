---
name: company-memory-fit
description: Evaluate a proposed feature, component, or change against DOOR3 Company Memory requirements, and decide whether it belongs in pansoph core, an adapter, a separate Company Memory service, or upstream. Use before starting any feature work in this fork, or when asked whether something fits.
---

# Company Memory fit check

Answer these in order, and stop at the first hard no.

1. **Does it serve the requirement?** "A user or agent retrieves only what the authenticated
   user may see, authorized before the LLM or agent." If it weakens that, reject it (priorities
   in `CLAUDE.md` Part 1 section 2).
2. **Which layer owns it?** Use the boundary in `CLAUDE.md` Part 1 section 4.
   - **pansoph:** ingestion, parsing, indexing, retrieval, authz, ACL sync, identity,
     citations, and secure REST or MCP.
   - **Company Memory service (separate):** People, Project, Skills, Decision, and Experience
     memory, reports, agents, workflows, agent prompts, and graph models.
   - A rule of thumb: if it needs to know what an "employee", "project", or "incident" is, it
     is not pansoph core.
3. **How to build it, least divergence first.** Configuration, then an adapter behind an
   existing port (entry points), then a separate service calling pansoph as the user, then an
   upstream PR, and a fork patch last (`docs/company-memory/UPSTREAM_STRATEGY.md`).
4. **Security impact.**
   - Does it add a content emitting path? It must go through `retrieve()`.
   - Does it add an identity source? It must be a validated token.
   - Does it aggregate across users? Watch for aggregation leaks: a report's audience must be
     able to read all its inputs.
   - Does it store derived data? The derived data must keep its provenance and source ACL,
     and it must be revoked when its sources are.
5. **People data.** Evidence and context with citations only. No scoring, ranking, or
   automated personnel or staffing decisions.
6. **Readiness dependencies.** Which open `CM-*` gaps block it? Check the review's gap list and
   the connector readiness table.

## Output

A short verdict with these parts:

- **fits in:** pansoph core, adapter, service, upstream, or does not fit;
- **why:** the reasons;
- **blocking gaps:** the `CM-*` ids that block it;
- **smallest viable next step;**
- **tests needed:** name them.

Do not start implementation unless asked.
