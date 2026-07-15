-- Give owners a public-facing trade name distinct from the legal name they register under.
--
-- Some ABA operators register with the provider registry under a holding-company or
-- state-entity legal name that is not the brand families know. The clinic linker has to
-- match on that legal name, but the dashboard should show the brand:
--
--   Buck Jack LLC                          trades as  Woven Care (formerly The Shandy Clinic)
--   Vocational Development Group, LLC       trades as  InBloom Autism Services
--   Carolina Center for Autism Services     trades as  Kind Behavioral Health
--
-- "Buck Jack" in particular is the search-fund acquisition vehicle, not an ABA brand, so
-- publishing it as the owner of clinics is both meaningless to a reader and slightly wrong.
--
-- The legal name stays in owner_entity.name, because the name-prefix linker must keep
-- matching the registry, and the audit trail must keep pointing at the registered entity.
-- The published views read COALESCE(trade_name, name), so display follows the brand while
-- resolution follows the legal name.
--
-- Idempotent so the ledger can be replayed safely.

ALTER TABLE owner_entity
    ADD COLUMN IF NOT EXISTS trade_name text;

COMMENT ON COLUMN owner_entity.trade_name IS
    'Public brand the owner operates under, when it differs from the legal name in '
    '.name that the registry carries. Display uses COALESCE(trade_name, name); the '
    'clinic linker and audit trail always use the legal .name.';
