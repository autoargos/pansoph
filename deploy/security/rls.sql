-- Opt-in Postgres row level security for hard tenant isolation on the vector table.
--
-- This is defense in depth on top of the WHERE-clause tenant filter and the authz
-- bulk check. It is NOT applied automatically, because enabling it requires the
-- vector store adapter to set the tenant on each connection:
--
--     SET app.tenant = '<tenant_id>';
--
-- Without that, FORCE row level security returns zero rows. Apply this only once the
-- pgvectorscale adapter sets app.tenant per connection (a planned hardening step).

ALTER TABLE pansoph_chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE pansoph_chunks FORCE ROW LEVEL SECURITY;

CREATE POLICY pansoph_chunks_tenant_isolation ON pansoph_chunks
    USING (tenant_id = current_setting('app.tenant', true));
