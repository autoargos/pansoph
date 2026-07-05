# pansoph-connector-linear

Connector for Linear (GraphQL API). Ingests issues (title plus description) and maps access from the issue's team.

Auth: a Linear API key sent in the `Authorization` header (personal API keys are sent without a `Bearer` prefix). Config: `token`.

ACL model (verified 2026-07-05 against https://linear.app/docs/private-teams): access follows team membership, and it splits on whether the issue's team is private.

- Private team: "Those who are not a member of the private team will not be able to see issues associated with the team." Only the team's members may see it, so the issue maps to `allow_groups=[team_id]`.
- Non-private (workspace) team: its issues are visible to all workspace members, not just the team, so the issue maps to `public=True` within this instance's ACL namespace (workspace-wide, not globally public). Mapping a public-team issue to the team group would wrongly hide it from workspace members who are not on that team.

Linear exposes no per-issue viewer list, so there are no per-issue `allow_users` and no deny at the issue level. Incremental sync and hard-delete tombstones are not enabled here (both capability flags are False); the follow-up paths are `filter:{updatedAt:{gt}}` for incremental and the webhook `remove` action for deletions.
