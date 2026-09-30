---
title: Engineering guidelines (company wide)
allow_groups: everyone
---
Marker: GUIDELINE ENG-01 PUBLIC.

Default stack for new client work: React with TypeScript for web front ends, AWS as the default cloud (ECS or Lambda for compute, RDS Postgres for relational data), Terraform for infrastructure.

Every change needs one reviewer. Secrets never go in source control. Architecture decisions are recorded as ADRs in each project's repository and summarized in the project directory.

Incidents follow the standard process: open an incident channel, assign an incident lead, write a blameless postmortem within five working days, and link the runbook that applied.
