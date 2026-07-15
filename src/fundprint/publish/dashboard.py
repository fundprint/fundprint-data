"""SQL view generators for the dashboard's read-only consumption.

The view is the contract between this repo and fundprint-dashboard.
Column renames in entity tables do not break the dashboard as long as
the view keeps its column names stable.

Only fields on this list are exposed. Internal scoring intermediates,
candidate sets, and raw resolver details stay behind the boundary.
"""

from __future__ import annotations

# Explicit allow-list of columns the dashboard may render publicly.
# Add a new column here only after the dashboard team confirms they need it
# and after verifying it contains no PII or non-public financials.
ALLOWED_COLUMNS = [
    "rc.id",
    "rc.claim_type",
    "rc.clinic_id",
    "rc.owner_entity_id",
    "rc.parent_pe_firm_id",
    "rc.acquisition_event_id",
    "rc.confidence_score",
    "rc.confidence_method",
    "rc.resolver_version",
    "rc.extracted_at",
    "rc.source_record_ids",
    "vrd.trust_level",
    "vrd.validation_run_id",
]


def generate_view_sql() -> str:
    """Emit CREATE OR REPLACE VIEW statements for the dashboard.

    Filters applied on every refresh:
      1. Confidence floor met (decision = 'passed' in validation_run_decision).
      2. Not quarantined (decision != 'quarantined').
      3. Not embargoed (embargo flag on the claim row).
      4. Allow-listed columns only - no scoring intermediates.
      5. Not superseded. A clinic row merged into another (e.g. one of several
         NPI enumerations at a single physical site) carries superseded_by and
         must not be counted again here. hf.py already filters this; the view
         did not, so the two exports disagreed on any superseded row.

    The embargo column does not exist yet.
    TODO: add embargoed boolean column to resolution_claim in a future migration.
    """
    col_list = ",\n    ".join(ALLOWED_COLUMNS)

    return f"""
CREATE OR REPLACE VIEW v_published_claims AS
SELECT
    {col_list}
FROM resolution_claim rc
JOIN validation_run_decision vrd
    ON vrd.resolution_claim_id = rc.id
WHERE vrd.decision = 'passed'
  AND rc.source_record_ids IS NOT NULL
  AND cardinality(rc.source_record_ids) > 0
  -- TODO: add embargoed column to resolution_claim then uncomment:
  -- AND rc.embargoed IS NOT TRUE
  AND NOT EXISTS (
      SELECT 1 FROM validation_run_decision vrd2
      WHERE vrd2.resolution_claim_id = rc.id
        AND vrd2.decision = 'quarantined'
  );


CREATE OR REPLACE VIEW v_published_clinics AS
SELECT DISTINCT
    c.id,
    c.name,
    c.city,
    c.state,
    c.zip,
    c.npi,
    c.owner_entity_id,
    c.confidence_score,
    c.confidence_method,
    c.resolver_version,
    c.extracted_at,
    c.source_record_ids
FROM clinic c
WHERE c.superseded_by IS NULL
  AND EXISTS (
    SELECT 1 FROM v_published_claims vpc
    WHERE vpc.clinic_id = c.id
);


CREATE OR REPLACE VIEW v_published_pe_links AS
SELECT
    oe.id        AS owner_entity_id,
    -- Display the brand families know, not the legal/holding name the registry
    -- carries (Buck Jack -> Woven Care, Carolina Center -> Kind Behavioral Health).
    -- The linker and audit trail still use the legal oe.name, display follows brand.
    COALESCE(oe.trade_name, oe.name) AS owner_entity_name,
    oe.state_of_incorporation,
    ppf.id        AS parent_pe_firm_id,
    ppf.name      AS parent_pe_firm_name,
    ppf.hq_state,
    vpc.confidence_score,
    vpc.confidence_method,
    vpc.validation_run_id,
    ppf.firm_type AS parent_pe_firm_type
FROM v_published_claims vpc
JOIN owner_entity oe ON oe.id = vpc.owner_entity_id
JOIN parent_pe_firm ppf ON ppf.id = vpc.parent_pe_firm_id
WHERE vpc.claim_type = 'owner_to_pe_firm';
""".strip()
