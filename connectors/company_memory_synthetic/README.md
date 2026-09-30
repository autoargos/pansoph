# Synthetic corpus connector (Company Memory fork)

Reads fictional documents from a local directory so the Company Memory demo and the
permission tests exercise the real connector, ingestion, ACL diff, SpiceDB, and pgvector
path. **Never point it at real company data.**

Each document is a `*.md` file with a small front matter block:

```
---
title: Project Atlas ADR-007: authentication architecture
allow_groups: project-atlas
allow_users:
deny:
---
Body text. Plain paragraphs, no markdown headings.
```

- `allow_groups`, `allow_users`, `deny`: comma separated. The ACL is a union of grants
  (pansoph has no intersection), and a document with no grant reaches nobody.
- The file stem is the source id (`[a-z0-9-]`), so the doc id is
  `cm_synthetic:{instance}:{stem}`.
- Incremental: the cursor records a hash per file. A changed file (content or ACL) is
  re-ingested, and a removed file becomes a tombstone.

Config: `corpus_dir` (a directory the agora process can read). The demo corpus is
`deploy/company-memory/corpus/`.
