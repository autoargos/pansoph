# pansoph-connector-slack

A pansoph connector for slack.

It is implemented against the latest official Slack Web API documentation. See the
header of `src/pansoph_connector_slack/connector.py` for the checklist of what was
verified and recorded (endpoints, auth and scopes, pagination, incremental sync,
rate limits, ACL model).

A document is modeled as a Slack channel. A public channel maps to public access. A
private channel, im, or mpim maps to its member user ids. Slack exposes no per
channel groups and no per channel deny, so `allow_groups` and `deny` stay empty.

It must pass the shared conformance suite (`assert_conforms` from
`pansoph-connector-sdk`) before it merges, and it maps the source permission model to
ACLs faithfully.
