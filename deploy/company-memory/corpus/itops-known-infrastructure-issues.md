---
title: IT Ops known infrastructure issues
allow_groups: engineering
---
Marker: ITOPS-KNOWN-ISSUES.

Recurring issue 1: the VPN gateway certificate expires every 90 days and has caused two access outages; renewal is now automated but must be verified monthly.

Recurring issue 2: CI runners fill their disks with container layers; a weekly prune job was added.

Recurring issue 3: managed Postgres failovers expose connection pools without validation (see runbook RB-12).
