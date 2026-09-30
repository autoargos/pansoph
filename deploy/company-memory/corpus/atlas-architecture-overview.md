---
title: Project Atlas architecture overview
allow_groups: project-atlas
---
Marker: ATLAS-ARCH-OVERVIEW.

The Atlas portal is a React and TypeScript single page app served from CloudFront. APIs are AWS Lambda functions behind the Atlas edge gateway. Orders are stored in DynamoDB with event sourcing (ATLAS ADR-003), and a nightly export feeds the client's warehouse.

Experience notes: Alice Nguyen led the React migration from a legacy jQuery front end and built the DynamoDB event store. The team used AWS CDK for infrastructure.
