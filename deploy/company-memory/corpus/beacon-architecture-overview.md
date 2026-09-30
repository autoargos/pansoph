---
title: Project Beacon architecture overview
allow_groups: project-beacon
---
Marker: BEACON-ARCH-OVERVIEW.

Beacon is a React Native mobile app for field crews, backed by Kotlin services on AWS ECS and an RDS Postgres database. Offline sync uses a local SQLite store with conflict resolution on the server (BEACON ADR-002).

Experience notes: Bob Martins built the offline sync engine and ran the RDS migration from a self managed Postgres.
