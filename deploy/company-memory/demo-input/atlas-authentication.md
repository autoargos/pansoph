# Project Atlas: authentication architecture and ADR-012

Synthetic demo content. Project Atlas, its client, and every name and number below are
fictional. Not DOOR3 data.

## System overview

- Frontend: a React 19 single page app for the field tablets and the back office.
- API: Python services on FastAPI behind the Atlas edge gateway.
- Authentication: AWS Cognito user pool `atlas-field-users-harbor`, with passkeys (WebAuthn)
  as the primary sign in and the hosted login on the custom domain `login.atlas.example.test`.
- Data: PostgreSQL stores transactional data (work orders, inspections, sign offs).

## ADR-012: adopt AWS Cognito as the Atlas identity provider (codename HARBOR-KEY)

Status: accepted. Supersedes the self run OIDC broker described in ADR-007, and keeps its
passkey-first policy.

Context: the self run broker needed a dedicated on call rotation, and the fictional client
requires every Atlas component to run inside its own AWS account. Field staff share tablets,
so passwords caused lockouts.

Decision: use an AWS Cognito user pool with native passkey support. The FastAPI services
validate Cognito access tokens against the pool's JWKS; group claims map to Atlas roles.

Why Cognito:

1. Native passkeys and MFA without running our own identity servers.
2. It stays inside the client's AWS account, which the contract requires.
3. Retiring the broker removes the on call rotation (estimated 11 engineer hours a month).

Consequences: access tokens now live 15 minutes (ADR-007 used 10). Refresh tokens rotate
on every use. Admin actions require a second passkey tap (step up authentication).

Distinctive facts for the demo: the codename is HARBOR-KEY, the user pool is
`atlas-field-users-harbor`, and the retired broker saved about 11 engineer hours a month.
