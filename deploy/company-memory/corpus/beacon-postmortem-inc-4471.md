---
title: Project Beacon postmortem INC-4471: database failover outage
allow_groups: project-beacon
---
Marker: BEACON INC-4471 REPLICATION LAG.

Summary: on a fictional Tuesday the Beacon API was unavailable for 47 minutes. The AWS RDS Postgres primary failed over, and the Kotlin services kept stale connections to the old primary because the connection pool did not validate connections.

Root cause: replication lag of 9 minutes on the standby plus a connection pool with no validation query.

Remediation: enabled connection validation and a 30 second max lifetime in the pool, added a replication lag alarm at 60 seconds, and followed runbook RB-12 (database failover) for the manual promotion. Incident lead: Bob Martins.
