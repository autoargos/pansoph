# pansoph-connector-gmail

A pansoph connector for gmail.

Implement it against the latest official Gmail API documentation. See the header of
`src/pansoph_connector_gmail/connector.py` for the checklist of what to verify and record
(endpoints, auth and scopes, pagination, incremental sync, rate limits, ACL model).

It passes the shared conformance suite (`assert_conforms` from
`pansoph-connector-sdk`), which is invoked directly in
`tests/test_gmail_conformance.py`, and it maps the source permission model to ACLs
faithfully.

Capabilities (all honest, all exercised by the conformance tests):

- Incremental sync (`incremental=True`): real partial sync via
  `users.history.list` keyed by a Gmail `historyId` cursor sourced from
  `users.getProfile`. An expired cursor (HTTP 404) downgrades to a full pass.
- Deletions (`deletions=True`): `users.history.list` `messagesDeleted[]` records
  become `DocRef(deleted=True)` tombstones. Trashing is not treated as a deletion.
- Permission sync (`permission_sync=True`): owner-only ACL. A Gmail mailbox is
  private to its owner and the `gmail.readonly` scope this connector requests
  exposes no per message sharing and no reachable delegates, so access maps to the
  mailbox owner only. If the credential is ever widened to
  `gmail.settings.sharing`, the ACL must also enumerate `users.settings.delegates`
  (see the connector module header).
