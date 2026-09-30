---
title: Project Atlas ADR-007: authentication architecture
allow_groups: project-atlas
---
Marker: ATLAS ADR-007 PASSKEY-FIRST OIDC.

Decision: Project Atlas uses passkey-first OpenID Connect with PKCE, brokered by the Atlas edge gateway running on AWS API Gateway with a Lambda authorizer. Access tokens live 10 minutes; refresh tokens are rotated on every use and bound to the device.

Context: the client's field staff share tablets, so password based login caused lockouts. Passkeys removed the shared password problem.

Consequences: the gateway validates tokens once, and downstream Lambdas trust a signed internal header. The team accepted the gateway as a single point of failure and mitigated it with multi region deployment. Decided by the Atlas architecture review, recorded by Alice Nguyen.
