-- Record how fresh a provider registry record is.
--
-- NPPES reports existence-ever, not existence-now. It is a registry of
-- identifiers, not an inventory of open businesses: when a clinic closes, its
-- NPI is not deactivated, and the record keeps reporting status 'A' forever.
-- Every published registry-sourced clinic in the dataset reports 'A', including
-- registrations last certified in 2008, so status alone cannot tell a live
-- clinic from a dead one.
--
-- The one signal NPPES does give is *when the registration was last touched*.
-- A center that closed years ago sits on a record no one has updated since. The
-- ingestion parser was discarding these fields, so the freshness signal existed
-- in the stored snapshots but nowhere in the database, and nothing downstream
-- could flag, quarantine, or disclose a stale record.
--
-- These columns carry that signal forward. They are NULL for rows that do not
-- come from a provider registry (an owner's own location directory has no NPI
-- and no registry timestamps; it is instead current by construction).
--
-- Idempotent so the ledger can be replayed safely.

ALTER TABLE staging_bacb_provider
    ADD COLUMN IF NOT EXISTS registry_status text,
    ADD COLUMN IF NOT EXISTS registry_last_updated date,
    ADD COLUMN IF NOT EXISTS registry_enumerated_on date;

ALTER TABLE clinic
    ADD COLUMN IF NOT EXISTS registry_status text,
    ADD COLUMN IF NOT EXISTS registry_last_updated date,
    ADD COLUMN IF NOT EXISTS registry_enumerated_on date;

-- Staleness is queried as a range scan ("everything not touched since X"), so a
-- plain btree on the date is the useful index.
CREATE INDEX IF NOT EXISTS clinic_registry_last_updated_idx
    ON clinic (registry_last_updated);
