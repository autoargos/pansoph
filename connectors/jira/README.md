# pansoph-connector-jira

Connector for Jira Cloud (REST API v3). Ingests issues (summary plus the flattened ADF description) and maps document access faithfully from the project's permission scheme and each issue's security level.

Auth: OAuth 2.0 (3LO, provider `atlassian`) or HTTP Basic with an Atlassian account email and an API token. Config: `base_url`, `project_key`, plus either `oauth_token` or (`email`, `api_token`).

ACL model (verified 2026-07-05):

- Browse audience is resolved from the project's permission scheme (`GET /rest/api/3/project/{key}/permissionscheme?expand=permissions`), reading only the `BROWSE_PROJECTS` grants. Group holders map to `allow_groups`, user holders to `allow_users` (by accountId), project-role holders are expanded to their actors, and an "any logged in user" (applicationRole) grant maps to `public`. Dynamic holders that cannot resolve to fixed principals (reporter, assignee, project lead, custom fields) are skipped, which under-shares and never widens.
- Issue security levels take precedence: when an issue's `security` field is set, the ACL is that level's members only (`GET /rest/api/3/issuesecurityschemes/level/member?levelId={id}`), never the wider project audience. That members endpoint requires the Administer Jira global permission; if the credential cannot resolve it, the issue fails closed (granted to no one) rather than over-sharing.
- If the project's permission scheme itself cannot be read, the browse audience also fails closed.

There is no deny concept; `permission_sync=True` is honest because the mapping is faithful and never widens a grant.

Deletions (`deletions=True`): a poll of JQL cannot see a deleted issue (it just disappears), so deletions are detected by client-side reconciliation. `list_changes` encodes the issue keys seen this run into the durable cursor; on the next run any key that vanished is emitted as a tombstone (`deleted=True`) and purged. The webhook path additionally translates `jira:issue_deleted` into a delete and `jira:issue_created`/`jira:issue_updated` into an upsert (which recomputes the ACL, so a newly applied security level propagates).

Incremental (`incremental=False`): the durable cursor carries the reconciliation key set, not a high-water mark, so a full re-list runs each sync. This is deliberate: it lets reconciliation catch removed or newly restricted issues and re-import every ACL, serving the permission guarantee over sync efficiency.
