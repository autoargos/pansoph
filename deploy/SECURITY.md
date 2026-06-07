# pansoph security model and hardening

pansoph holds an organization's private knowledge and many connectors' credentials,
and it runs untrusted connector and MCP code. Security is built in, not bolted on.
This document is the operator's hardening guide. The full checklist lives in the
engineering guide (CLAUDE.md section 8); this is how to apply it.

## What is enforced by default

- **Permission correctness.** Retrieval overfetches, then runs one authz bulk check,
  then returns only the allowed subset. The vector pre-filter is never the source of
  truth. With SpiceDB, ZedToken consistency means a revocation is not read stale.
- **Tenant tagging.** Every chunk carries `tenant_id`, and every query filters on it.
- **Audit log.** Every search, chat, and grab is recorded on the `pansoph.audit`
  logger as a structured line with actor, tenant, and result shape, never contents.
- **Secrets at rest.** Connector credentials are sealed with envelope encryption
  (per-tenant data keys, AES-GCM) or OpenBao. The root key is never committed.
- **No authentication bypass.** The grabber captures only what the signed in user can
  already see, with consent. No anti-bot, no CAPTCHA solving, no credential replay.

## Hardening to apply for a real deployment

1. **Network egress allowlist.** Run the hardening overlay so workers reach only the
   providers and sources you allow, and the cloud metadata IP is blocked:
   `docker compose -f compose.yaml -f compose.hardened.yaml --profile core,workers,hardened up`.
   Edit `deploy/security/squid-egress.conf` to list your connectors' domains.
2. **Sandbox untrusted code.** Run connector and MCP code under a microVM
   (Firecracker, Kata) or gVisor runtime class, out of process, never inside agora.
   The overlay sets `cap_drop: ALL` and `no-new-privileges` as the baseline.
3. **Tenant isolation in the database.** Apply `deploy/security/rls.sql` to enable
   Postgres row level security on the vector table. This requires the vector adapter
   to `SET app.tenant` per connection (a planned step), so it is opt-in, not default.
4. **Supply chain.** The release workflow generates an SBOM (Syft) and signs images
   with cosign. Pin images by digest and rebuild on patch.
5. **Secrets.** Generate a real envelope root key (`age-keygen`) and keep it out of
   source control, or switch the secret store to OpenBao.
6. **TLS.** Terminate TLS at the proxy in front of agora and lens. All outbound calls
   already use HTTPS.

## Threat notes

- The most security-critical property is that a revoked user cannot read a document
  from a stale cache. This is why SpiceDB (ZedToken) is the default authz engine and
  why retrieval threads the consistency token, not just writes.
- Permission sync usually expands access; the dangerous case is a contraction (a
  revocation), which the ingestion path applies first on re-sync.
- Multi-tenant isolation is enforced in both the authz namespace and the vector
  payload. Enable RLS for a third layer.
