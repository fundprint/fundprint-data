-- Mark which owner entities are actually ABA providers.
--
-- owner_entity holds every company scraped from a PE firm's portfolio page, not
-- just the autism-therapy ones: KKR's portfolio gives us MyEyeDr., Heartland
-- Dental, Medline, Del Taco. Those rows are legitimate (they document what the
-- firm owns) and until now they were harmless, because clinic linking only ever
-- saw NPPES records returned by a *taxonomy-filtered* API query, which never
-- contained an optometrist.
--
-- The bulk registry has no such filter. It is every NPI in the country, and the
-- brand-prefix linker matched roughly 5,000 optometry, dental and fast-food
-- locations to their PE owners as "ABA clinics" the first time it was pointed at
-- the file. This flag is the guard: only an ABA owner may capture a clinic.
--
-- Default FALSE, so a newly scraped portfolio company cannot silently start
-- capturing clinics. Marking a brand as ABA is a deliberate, curated act, the
-- same bar as adding a CuratedAcquisition.
--
-- Idempotent so the ledger can be replayed safely.

ALTER TABLE owner_entity
    ADD COLUMN IF NOT EXISTS is_aba boolean NOT NULL DEFAULT false;

COMMENT ON COLUMN owner_entity.is_aba IS
    'True when this owner actually provides ABA / autism therapy. Only ABA owners '
    'are used for clinic linking. False for the non-ABA portfolio companies that '
    'come in from a PE firm''s portfolio page (MyEyeDr., Heartland Dental, ...).';

-- Every owner that already holds a published clinic is, by construction, an ABA
-- provider: it got those clinics from a taxonomy-filtered ABA registry query.
UPDATE owner_entity oe
SET is_aba = true
WHERE EXISTS (SELECT 1 FROM clinic c WHERE c.owner_entity_id = oe.id);

-- Plus the two in-home ABA providers, which have no clinics by definition but
-- are still ABA companies, and CARD, kept for its Blackstone history.
UPDATE owner_entity
SET is_aba = true
WHERE name IN (
    'Butterfly Effects',
    'Key Autism Services',
    'Center for Autism and Related Disorders (CARD)'
);

CREATE INDEX IF NOT EXISTS owner_entity_is_aba_idx ON owner_entity (is_aba);
