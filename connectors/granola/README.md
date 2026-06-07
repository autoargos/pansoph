# pansoph-connector-granola

A pansoph connector for granola.

Implement it against the latest official granola API documentation. See the header of
`src/pansoph_connector_granola/connector.py` for the checklist of what to verify and record
(endpoints, auth and scopes, pagination, incremental sync, rate limits, ACL model).

It must pass the shared conformance suite (`assert_conforms` from
`pansoph-connector-sdk`) before it merges, and it must map the source permission
model to ACLs faithfully.
