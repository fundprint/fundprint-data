-- Add an explicit owner-type label to parent_pe_firm.
--
-- The dataset's primary frame is private-equity ownership, but a small number
-- of clinics are owned by other institutional financial owners (pension funds,
-- family offices). Rather than mislabel those as PE, we record the owner type
-- explicitly so the dashboard and exports can show it honestly. Existing rows
-- default to 'private_equity', which is correct for every firm ingested so far.

-- Idempotent: this column was first applied directly against the database, so
-- a migration runner replaying the ledger would otherwise fail with 42701
-- ("column already exists"). IF NOT EXISTS makes the migration safe to re-run.
ALTER TABLE parent_pe_firm
    ADD COLUMN IF NOT EXISTS firm_type text NOT NULL DEFAULT 'private_equity'
    CHECK (firm_type IN (
        'private_equity', 'pension_fund', 'family_office', 'other'
    ));
