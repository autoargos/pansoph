# Security Policy

pansoph's whole reason to exist is a security claim: a user can only ever retrieve
what they are already allowed to see. We take vulnerability reports seriously, and we
want to make reporting easy and safe. Thank you for helping keep pansoph and its users
secure.

## Supported versions

pansoph is pre-1.0 and under active development. There is no long-term-support branch
yet, and APIs and adapters can still change.

Because of that, **all security reports are welcome regardless of version**. We do not
maintain a back-port matrix at this stage. Fixes land on `main` and in the next tagged
release. Once pansoph reaches 1.0 this section will be replaced with a concrete
supported-versions table.

| Version | Supported |
|---|---|
| `main` (unreleased) | Yes |
| Any tagged pre-1.0 release | Yes, report it and we will fix forward on `main` |

## What is in scope (and what we care about most)

Everything in this repository is in scope. We assign the **highest priority** to bugs
in the parts of the system that enforce the central security claim:

1. **The permission-enforcement path.** Anything that could cause a user to retrieve a
   document they are not authorized to see. The hot path is
   `packages/pansoph-retrieval/src/pansoph_retrieval/pipeline.py` (overfetch, then a
   single `authz.bulk_check`, then keep only the allowed subset, then rerank). Examples
   we want to hear about:
   - A vector pre-filter that returns a document the authz check should have removed but
     did not (a pre-filter group leak).
   - A staleness or freshness bug where a revoked permission is not reflected on the
     next read (a ZedToken / consistency-token failure, the New Enemy Problem).
   - A deny that is not applied as the final filter, or that is expressed as an authz
     exclusion tuple instead of a final filter.
   - Tenant or connector-instance isolation breaking (one instance's documents
     appearing in another instance's query).
2. **Connector ACL mapping.** A connector that maps a source's real permission model to
   relationship tuples incorrectly, so that a document becomes visible to a user the
   source itself would deny. The relevant code lives under `connectors/` and the
   mappers in `packages/pansoph-authz/`. Getting an ACL mapping wrong is a security bug,
   not a cosmetic one.
3. **The authz adapters.** `packages/pansoph-authz/src/pansoph_authz/adapters/`
   (SpiceDB, OpenFGA, and the no-op adapter) and the `AuthzPort` contract.
4. **Credential and secret handling.** Per-connector credential isolation, the secret
   store, egress allowlisting, and anything that could exfiltrate a connector
   credential or a tenant data key.
5. **Untrusted code execution.** The connector and MCP sandbox boundary, since pansoph
   runs untrusted connector and MCP code.

Out of scope: reports that depend on a misconfigured deployment that contradicts our
documented hardening guidance, denial of service from unbounded self-hosted resource
limits you control, social-engineering attacks, and findings in third-party services
(SpiceDB, OpenFGA, Postgres, Keycloak, and so on) that are not caused by how pansoph
uses them. Report those upstream. If a third-party finding is triggered by a default we
ship, please do tell us.

## How to report a vulnerability

**Please do not open a public issue, pull request, or discussion for a security
problem.** Use a private channel so we can fix it before it is disclosed.

Two private channels, in order of preference:

1. **GitHub private vulnerability reporting (preferred).** On the repository, go to the
   **Security** tab and click **Report a vulnerability**. This opens a private draft
   advisory visible only to maintainers, and GitHub adds you as a credited collaborator
   on that advisory automatically. GitHub private vulnerability reporting is a separate
   mechanism from this file, and we recommend it because it keeps the whole disclosure
   thread, the fix, and the credit in one private place. See the GitHub documentation
   linked in Sources below.

2. **Email.** Send to `kyashkarande@gmail.com`. A dedicated `security@` address may
   replace this before a wider public release. If you need to encrypt the report, email
   us first and ask for a key. A PGP or `age` public key, and an `Encryption` line in
   `/.well-known/security.txt`, are planned follow-ups (see "Follow-up items" below). Do
   not send secrets or live credentials in plaintext email.

In your report, please include, as far as you can:

- A description of the issue and the security impact (what an attacker gains).
- The component and, if you can, the file and line (for example
  `packages/pansoph-retrieval/.../pipeline.py`).
- Clear, minimal reproduction steps or a proof-of-concept.
- The version, commit SHA, and adapter configuration (which authz adapter, which vector
  store) you were running.
- Any suggested remediation, if you have one.

## Our commitments (response targets)

These are good-faith targets for a small, currently solo-maintained project, not a
contractual SLA. We will do our best to beat them.

| Stage | Target |
|---|---|
| **Acknowledge** your report | Within 3 business days |
| **Triage**: confirm or decline, assign a severity, agree on next steps | Within 7 business days |
| **Fix** a confirmed high-severity issue (a real authorization-bypass or data-leak) | Targeted within 30 days, faster when feasible |
| **Fix** a confirmed lower-severity issue | Next release, scheduled with you |

We will keep you updated as we work, credit you in the advisory and release notes
unless you ask us not to, and let you know when a fix ships.

## Coordinated disclosure

We follow coordinated vulnerability disclosure.

- We ask that you give us a reasonable window to fix the issue before any public
  disclosure. Our default target window is **90 days** from acknowledgement, or sooner
  once a fix is released and users have had a chance to update.
- We will agree a disclosure date with you. If an issue is being actively exploited in
  the wild, we may need to move faster, and we will coordinate that with you.
- When the fix is released we publish a GitHub Security Advisory (and a CVE where
  appropriate), credit the reporter, and note the fixed version.
- If you believe we are not responding in line with the targets above, you may disclose
  after the 90-day window. We would rather you tell us first.

## Safe harbor for good-faith research

We support security research and want you to feel safe doing it. If you make a
good-faith effort to comply with this policy, we will:

- Consider your research authorized, and we **will not pursue or support legal action**
  against you for it.
- Work with you to understand and resolve the issue quickly.

Good faith means: you only test against your own self-hosted instance or accounts you
control, you do not access, modify, or exfiltrate other people's data, you avoid
privacy violations and service degradation, you do not run denial-of-service or spam,
you give us a reasonable time to fix before public disclosure, and you do not exploit
the issue beyond the minimum needed to demonstrate it. If you are unsure whether an
action is authorized, ask us first via a private channel.

This safe harbor does not extend to actions against third parties (your identity
provider, your cloud, the upstream sources you connect), and it does not waive any
rights of third parties.

## Follow-up items (planned, tracked openly so this file stays honest)

These are not done yet and are listed here so the policy does not over-claim:

- Publish an RFC 9116 `security.txt` at `/.well-known/security.txt` over HTTPS once a
  hosted domain exists. RFC 9116 requires only `Contact` and `Expires` (an ISO 8601
  datetime with a timezone, set no more than about a year out), and recommends an
  OpenPGP signature. A stale `security.txt` is worse than none, so we will only publish
  it with a maintained `Expires` field.
- Publish a PGP or `age` public key and add an `Encryption` line to both this file and
  `security.txt`.
- Enable GitHub private vulnerability reporting in repository settings (Settings,
  Advanced Security, Private vulnerability reporting, Enable). Note that GitHub does not
  notify maintainers by default, so maintainers must Watch, Custom, Security alerts.

## Sources (verified 2026-06-23)

- GitHub Docs, "Configuring private vulnerability reporting for a repository" (private
  reporting is separate from this `SECURITY.md`; enable under Settings, Advanced
  Security; reporter is auto-credited): https://docs.github.com/en/code-security/security-advisories/working-with-repository-security-advisories/configuring-private-vulnerability-reporting-for-a-repository
- OpenSSF OSPS Baseline 2025-10-10 (an active project MUST document a coordinated
  vulnerability disclosure policy with a clear response timeframe, recommends
  `SECURITY.md` at repo root): https://baseline.openssf.org/versions/2025-10-10.html
- RFC 9116, "A File Format to Aid in Security Vulnerability Disclosure" (`Contact` and
  `Expires` required; HTTPS at `/.well-known/security.txt`; US spelling
  `Acknowledgments`): https://www.rfc-editor.org/info/rfc9116/
- sigstore `SECURITY.md` exemplar (24-hour acknowledgement, "email us for a PGP key"
  pattern): https://github.com/sigstore/sigstore/blob/main/SECURITY.md
- OpenSSF `oss-vulnerability-guide` and `project-template` `SECURITY.md`:
  https://github.com/ossf/oss-vulnerability-guide
