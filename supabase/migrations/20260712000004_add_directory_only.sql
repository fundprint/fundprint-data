-- Mark owner brands that may only be matched from their own directory.
--
-- Some real ABA brands have names too generic to use against the national
-- provider registry. LEARN Behavioral (Gryphon Investors) runs sub-brands called
-- "Behavioral Concepts" and "SPARKS ABA"; the registry contains unrelated
-- organizations whose names begin the same way, and the clinic linker matches by
-- normalized name prefix, so using those brands against the registry would
-- attribute other people's clinics to Gryphon.
--
-- The brand is still perfectly safe against the owner's OWN location directory,
-- because there the record is staged under the owner's name by construction: we
-- are not guessing which company a registry row belongs to, we are reading a list
-- the owner publishes of its own centers.
--
-- So these owners are excluded from registry name-matching (see
-- fundprint.acquire.nppes_bulk._load_aba_brands) while remaining fully linkable
-- from their directory. This keeps a real chain in the dataset without buying its
-- coverage with a false attribution.
--
-- Idempotent so the ledger can be replayed safely.

ALTER TABLE owner_entity
    ADD COLUMN IF NOT EXISTS directory_only boolean NOT NULL DEFAULT false;

COMMENT ON COLUMN owner_entity.directory_only IS
    'True when the brand name is too generic to match against the national '
    'provider registry without over-capturing unrelated organizations. Such an '
    'owner is linked only from its own published location directory.';
