# pansoph-connector-linear

Connector for Linear (GraphQL API). Ingests issues (title plus description) and maps access from the issue's team.

Auth: a Linear API key sent in the `Authorization` header (personal API keys are sent without a `Bearer` prefix). Config: `token`.

ACL model (verified 2026-07-05 against https://linear.app/docs/private-teams, https://linear.app/docs/members-roles, and the guest-accounts changelog https://linear.app/changelog/2022-07-14-guest-accounts): access follows team membership, and it splits on whether the issue's team is private.

- Private team: "Those who are not a member of the private team will not be able to see issues associated with the team." Only the team's members may see it, so the issue maps to `allow_groups=[team_id]`.
- Open (non-private) team: its issues are visible to every full workspace member (Owner / Admin / Team Owner / Member), whether or not they joined the team, but NOT to guests. Linear guests are workspace principals (paid seats that appear in member management) yet a guest "will not be able to see or discover any other teams in the workspace or have access to those issues." pansoph's UNION-only AccessControl cannot express "all full members but not guests" as `public=True` without leaking to guests, so an open team maps to the reserved full-members group `linear:workspace-members`, never `public=True`. That group fails closed: it under-shares (grants no one) until the identity layer materializes it with the workspace's full members, excluding guests. This connector never emits `public=True` for a Linear issue.

Linear exposes no per-issue viewer list, so there are no per-issue `allow_users` and no deny at the issue level. Incremental sync and hard-delete tombstones are not enabled here (both capability flags are False); the follow-up paths are `filter:{updatedAt:{gt}}` for incremental and the webhook `remove` action for deletions.
