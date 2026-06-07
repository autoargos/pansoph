-- Enable the Postgres extensions pansoph uses on the local tier.
-- pgvector and pgvectorscale ship in the timescaledb-ha image.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS vectorscale CASCADE;

-- pgmq provides the durable job queue. If the image does not bundle it, this
-- line is a no-op-by-error guard: install pgmq in the image, or run DBOS's own
-- Postgres-backed queue instead (the default for the local demo). See ADR-0002.
DO $$
BEGIN
    CREATE EXTENSION IF NOT EXISTS pgmq;
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'pgmq not available in this image; the durable queue will use DBOS native tables instead.';
END $$;
