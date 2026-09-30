---
title: IT Ops runbook RB-12: Postgres database failover
allow_groups: engineering, people-strategy
---
Marker: RUNBOOK RB-12 SHARED.

Use when a managed Postgres primary is unhealthy or has failed over. Step 1: confirm the failover event in the cloud console. Step 2: check replication lag on the new primary. Step 3: restart application connection pools or confirm they validate connections. Step 4: post status in the incident channel every 15 minutes. Step 5: file a postmortem.

People Strategy access: this runbook is shared with People Strategy for on call staffing planning. It contains no client data.
