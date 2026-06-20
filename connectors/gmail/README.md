# pansoph-connector-gmail

A pansoph connector for gmail.

Implement it against the latest official Gmail API documentation. See the header of
`src/pansoph_connector_gmail/connector.py` for the checklist of what to verify and record
(endpoints, auth and scopes, pagination, incremental sync, rate limits, ACL model).

It must pass the shared conformance suite (`assert_conforms` from
`pansoph-connector-sdk`) before it merges, and it must map the source permission
model to ACLs faithfully. A Gmail mailbox is private to its owner and the API has no
per message sharing surface, so access maps to the mailbox owner only.
