-- Enable the Postgres extensions pansoph uses on the local tier.
-- pgvector and pgvectorscale ship in the timescaledb-ha image.
CREATE EXTENSION IF NOT EXISTS vector;

-- vectorscale adds DiskANN for scale. It is optional: the local demo uses exact
-- search over pgvector, so a missing vectorscale must not abort startup.
DO $$
BEGIN
    CREATE EXTENSION IF NOT EXISTS vectorscale CASCADE;
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'vectorscale not available in this image; using exact pgvector search.';
END $$;

-- pgmq provides the durable job queue. If the image does not bundle it, this
-- guard keeps startup green: run DBOS's own Postgres-backed queue instead.
DO $$
BEGIN
    CREATE EXTENSION IF NOT EXISTS pgmq;
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'pgmq not available in this image; the durable queue will use DBOS native tables instead.';
END $$;
