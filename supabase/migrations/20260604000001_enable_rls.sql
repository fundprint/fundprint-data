-- Enable Row Level Security on every public table.
--
-- Why: Supabase auto-exposes the public schema through its PostgREST API using
-- the anon/authenticated roles. Without RLS, anyone holding the project's anon
-- key could read/write these tables over that API. This is the source of the
-- "RLS Disabled in Public" database-linter warnings.
--
-- Effect: we enable RLS but add NO policies, which is deny-by-default for the
-- anon/authenticated roles -- the public API can touch nothing. The Fundprint
-- pipeline is unaffected: it connects as the `postgres` role (BYPASSRLS), so it
-- retains full access for ingest, resolve, validate, and publish.
--
-- If the dashboard ever needs read access to specific tables, add narrow
-- SELECT policies for the `authenticated` (or a dedicated) role at that time.

ALTER TABLE source_record               ENABLE ROW LEVEL SECURITY;
ALTER TABLE staging_bacb_provider       ENABLE ROW LEVEL SECURITY;
ALTER TABLE staging_sec_filing          ENABLE ROW LEVEL SECURITY;
ALTER TABLE staging_pe_portfolio_listing ENABLE ROW LEVEL SECURITY;
ALTER TABLE parent_pe_firm              ENABLE ROW LEVEL SECURITY;
ALTER TABLE owner_entity                ENABLE ROW LEVEL SECURITY;
ALTER TABLE clinic                      ENABLE ROW LEVEL SECURITY;
ALTER TABLE acquisition_event           ENABLE ROW LEVEL SECURITY;
ALTER TABLE resolution_claim            ENABLE ROW LEVEL SECURITY;
ALTER TABLE validation_run              ENABLE ROW LEVEL SECURITY;
ALTER TABLE validation_run_decision     ENABLE ROW LEVEL SECURITY;
