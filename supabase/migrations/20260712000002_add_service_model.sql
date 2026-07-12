-- Record whether an owner delivers care at centers or in the home.
--
-- A clinic is a physical service location (methodology section 2). Some ABA
-- providers do not have any: they deliver therapy in the client's home or
-- community. Those providers still hold NPIs, and they register them somewhere,
-- so the registry hands us addresses that are not clinics. The evidence is
-- unambiguous once you look:
--
--   * Key Autism Services registers exactly ONE NPI per state, in fourteen
--     states, and every address is an office suite ("STE 650", "STE 800",
--     "FL 2"). It publishes 195 "in-home service area" pages and no centers.
--   * Butterfly Effects registers at literal apartments ("4650 34TH ST APT D",
--     "2708 NE 14TH ST APT 5") and downtown office towers.
--
-- Publishing those as clinics is a category error, so an in-home owner
-- contributes no clinics. Its OWNERSHIP is still true, sourced, and published:
-- Butterfly Effects really is owned by Moran Capital, Key Autism by Cane
-- Investment. Dropping the owners entirely would delete a real PE-ownership fact
-- and understate private-equity presence in ABA, so they stay on the map with a
-- clinic count of zero and an honest label, the same way Blackstone stays as a
-- former owner.
--
-- Idempotent so the ledger can be replayed safely.

ALTER TABLE owner_entity
    ADD COLUMN IF NOT EXISTS service_model text NOT NULL DEFAULT 'center_based'
    CHECK (service_model IN ('center_based', 'in_home'));

COMMENT ON COLUMN owner_entity.service_model IS
    'center_based: operates physical clinics. in_home: delivers therapy in the '
    'client''s home or community and operates no centers, so it contributes no '
    'clinic rows. Its ownership chain is still published.';
