# pansoph-connector-linear

Connector for Linear (GraphQL API). Ingests issues (title plus description) and maps access from the issue's team.

Auth: a Linear API key sent in the `Authorization` header (personal API keys are sent without a `Bearer` prefix). Config: `token`.

ACL caveat: Linear has no per-issue viewer list, so access is mapped best effort to the issue's team (the team id becomes an allowed group). It does not express finer-grained per-issue visibility, and there is no deny concept. Treat permission_sync here as team scoped and best effort.
