# pansoph-connector-teams

A pansoph connector for Microsoft Teams, implemented against the Microsoft Graph API.

Implement it against the latest official Microsoft Graph documentation. See the header of
`src/pansoph_connector_teams/connector.py` for the checklist of what was verified and recorded
(endpoints, auth and scopes, pagination, incremental sync, rate limits, ACL model).

It must pass the shared conformance suite (`assert_conforms` from
`pansoph-connector-sdk`) before it merges, and it must map the source permission
model to ACLs faithfully.
