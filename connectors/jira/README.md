# pansoph-connector-jira

Connector for Jira Cloud (REST API v3). Ingests issues (summary plus the flattened ADF description) and maps access from the project Browse role.

Auth: HTTP Basic with an Atlassian account email and an API token. Config: `base_url`, `email`, `api_token`, `project_key`.

ACL caveat: the access mapping is best effort and computed from the project Browse role (Users role) only. It does not resolve Jira issue security levels, so an issue restricted by a security level below its project can be over-shared by this mapping. There is no deny concept. Treat permission_sync here as best effort, not authoritative.
