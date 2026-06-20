# pansoph-connector-zendesk

A pansoph connector for Zendesk Help Center articles.

It ingests Help Center articles as documents and maps each article's Zendesk user
segment to a pansoph `AccessControl`. A public article (null `user_segment_id`) maps
to public; a restricted article maps to the segment's reach (`staff` or
`signed_in`, plus `group:{id}` and `org:{id}` entries). Zendesk user segments only
grant access, so the connector never produces deny entries.

Config: `subdomain`, `email`, `token` (API token, used as HTTP Basic with the
username `{email}/token`), and an optional `locale` (default `en-us`). A `base_url`
override is supported for testing.

Implemented against the official Zendesk Help Center API. See the connector module
docstring for the exact endpoints, auth, pagination, incremental export, and the ACL
mapping table, with the verified date.
