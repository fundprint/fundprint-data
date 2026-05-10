-- Initial schema: core entities, staging tables, provenance infrastructure.
-- All entity tables use superseded_by for versioned updates rather than in-place edits.
-- pgvector is required for embedding-based entity resolution.

CREATE EXTENSION IF NOT EXISTS vector;

-- ------------------------------------------------------------
-- source_record: pointer to a public document backing any claim.
-- Every derived row must reference at least one of these.
-- ------------------------------------------------------------
CREATE TABLE source_record (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_url      text NOT NULL,
    snapshot_id     text,                      -- object-storage key for the raw blob
    source_type     text NOT NULL,             -- 'bacb', 'sec_edgar', 'pe_portfolio', 'news', ...
    fetched_at      timestamptz NOT NULL,
    content_hash    text,                      -- sha256 of the blob; detects re-fetches
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX source_record_url_hash_idx
    ON source_record (source_url, content_hash)
    WHERE content_hash IS NOT NULL;

-- ------------------------------------------------------------
-- Staging tables: typed holding areas between Acquire and Resolve.
-- Rows here are source-stamped and schema-valid but not yet resolved.
-- ------------------------------------------------------------

CREATE TABLE staging_bacb_provider (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_record_id uuid NOT NULL REFERENCES source_record (id),
    raw_name        text NOT NULL,
    address_line1   text,
    city            text,
    state           char(2),
    zip             text,
    npi             text,
    credential_type text,
    ingested_at     timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX staging_bacb_state_idx ON staging_bacb_provider (state);

CREATE TABLE staging_sec_filing (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_record_id uuid NOT NULL REFERENCES source_record (id),
    accession_number text NOT NULL,
    form_type       text NOT NULL,             -- 'D', 'D/A', 'SC 13D', ...
    filer_name      text,
    filing_date     date,
    issuer_name     text,
    issuer_state    char(2),
    amount_raised   numeric,
    raw_json        jsonb,
    ingested_at     timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX staging_sec_accession_idx ON staging_sec_filing (accession_number);

CREATE TABLE staging_pe_portfolio_listing (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_record_id uuid NOT NULL REFERENCES source_record (id),
    pe_firm_name    text NOT NULL,
    portfolio_name  text NOT NULL,
    portfolio_url   text,
    description     text,
    sector_tags     text[],
    listed_as_of    date,                      -- best-guess date the listing was live
    ingested_at     timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX staging_pe_portfolio_firm_idx ON staging_pe_portfolio_listing (pe_firm_name);

-- ------------------------------------------------------------
-- Core entity tables: clinic, owner_entity, parent_pe_firm.
-- Each carries provenance fields and a superseded_by pointer so
-- old versions are preserved when resolution improves.
-- ------------------------------------------------------------

CREATE TABLE parent_pe_firm (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name                    text NOT NULL,
    name_normalized         text,
    hq_state                char(2),
    website                 text,
    name_embedding          vector(1024),
    name_embedding_model    text,
    -- provenance
    source_record_ids       uuid[] NOT NULL,
    confidence_score        numeric NOT NULL CHECK (confidence_score BETWEEN 0 AND 1),
    confidence_method       text NOT NULL CHECK (confidence_method IN (
                                'exact_match', 'fuzzy_high', 'fuzzy_low',
                                'llm_inferred', 'human_verified'
                            )),
    resolver_version        text NOT NULL,
    extracted_at            timestamptz NOT NULL,
    superseded_by           uuid REFERENCES parent_pe_firm (id),
    created_at              timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX parent_pe_firm_name_embedding_idx
    ON parent_pe_firm USING ivfflat (name_embedding vector_cosine_ops)
    WHERE name_embedding IS NOT NULL;

CREATE TABLE owner_entity (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name                    text NOT NULL,
    name_normalized         text,
    entity_type             text,              -- 'LLC', 'Inc', 'LP', ...
    state_of_incorporation  char(2),
    parent_pe_firm_id       uuid REFERENCES parent_pe_firm (id),
    name_embedding          vector(1024),
    name_embedding_model    text,
    -- provenance
    source_record_ids       uuid[] NOT NULL,
    confidence_score        numeric NOT NULL CHECK (confidence_score BETWEEN 0 AND 1),
    confidence_method       text NOT NULL CHECK (confidence_method IN (
                                'exact_match', 'fuzzy_high', 'fuzzy_low',
                                'llm_inferred', 'human_verified'
                            )),
    resolver_version        text NOT NULL,
    extracted_at            timestamptz NOT NULL,
    superseded_by           uuid REFERENCES owner_entity (id),
    created_at              timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX owner_entity_name_embedding_idx
    ON owner_entity USING ivfflat (name_embedding vector_cosine_ops)
    WHERE name_embedding IS NOT NULL;
CREATE INDEX owner_entity_parent_firm_idx ON owner_entity (parent_pe_firm_id);

CREATE TABLE clinic (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name                    text NOT NULL,
    name_normalized         text,
    address_line1           text,
    city                    text,
    state                   char(2),
    zip                     text,
    npi                     text,
    owner_entity_id         uuid REFERENCES owner_entity (id),
    name_embedding          vector(1024),
    name_embedding_model    text,
    -- provenance
    source_record_ids       uuid[] NOT NULL,
    confidence_score        numeric NOT NULL CHECK (confidence_score BETWEEN 0 AND 1),
    confidence_method       text NOT NULL CHECK (confidence_method IN (
                                'exact_match', 'fuzzy_high', 'fuzzy_low',
                                'llm_inferred', 'human_verified'
                            )),
    resolver_version        text NOT NULL,
    extracted_at            timestamptz NOT NULL,
    superseded_by           uuid REFERENCES clinic (id),
    created_at              timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX clinic_name_embedding_idx
    ON clinic USING ivfflat (name_embedding vector_cosine_ops)
    WHERE name_embedding IS NOT NULL;
CREATE INDEX clinic_state_idx ON clinic (state);
CREATE INDEX clinic_owner_entity_idx ON clinic (owner_entity_id);

-- ------------------------------------------------------------
-- acquisition_event: append-only record of ownership changes.
-- Never update; add a superseding event if data improves.
-- ------------------------------------------------------------
CREATE TABLE acquisition_event (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_entity_id     uuid NOT NULL REFERENCES owner_entity (id),
    parent_pe_firm_id   uuid NOT NULL REFERENCES parent_pe_firm (id),
    event_type          text NOT NULL CHECK (event_type IN (
                            'acquisition', 'divestiture', 'bankruptcy',
                            'recapitalization', 'merger'
                        )),
    event_date          date,
    event_date_circa    bool NOT NULL DEFAULT false,  -- true when only year/quarter is known
    deal_notes          text,
    -- provenance
    source_record_ids   uuid[] NOT NULL,
    confidence_score    numeric NOT NULL CHECK (confidence_score BETWEEN 0 AND 1),
    confidence_method   text NOT NULL CHECK (confidence_method IN (
                            'exact_match', 'fuzzy_high', 'fuzzy_low',
                            'llm_inferred', 'human_verified'
                        )),
    resolver_version    text NOT NULL,
    extracted_at        timestamptz NOT NULL,
    superseded_by       uuid REFERENCES acquisition_event (id),
    created_at          timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX acquisition_event_entity_idx ON acquisition_event (owner_entity_id);
CREATE INDEX acquisition_event_firm_idx ON acquisition_event (parent_pe_firm_id);

-- ------------------------------------------------------------
-- resolution_claim: every candidate link the resolver proposes.
-- LLM and fuzzy outputs write here; Validate reads from here.
-- Writing directly to entity tables without a claim is not allowed.
-- ------------------------------------------------------------
CREATE TABLE resolution_claim (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    claim_type          text NOT NULL CHECK (claim_type IN (
                            'clinic_to_owner', 'owner_to_pe_firm', 'acquisition_event'
                        )),
    -- nullable FKs cover all three claim types without a discriminated union
    clinic_id           uuid REFERENCES clinic (id),
    owner_entity_id     uuid REFERENCES owner_entity (id),
    parent_pe_firm_id   uuid REFERENCES parent_pe_firm (id),
    acquisition_event_id uuid REFERENCES acquisition_event (id),
    -- the raw LLM or fuzzy-match output supporting this claim
    supporting_snippets jsonb,
    llm_flags           text[],                -- e.g. {'source_contradicts_itself'}
    -- provenance
    source_record_ids   uuid[] NOT NULL,
    confidence_score    numeric NOT NULL CHECK (confidence_score BETWEEN 0 AND 1),
    confidence_method   text NOT NULL CHECK (confidence_method IN (
                            'exact_match', 'fuzzy_high', 'fuzzy_low',
                            'llm_inferred', 'human_verified'
                        )),
    resolver_version    text NOT NULL,
    extracted_at        timestamptz NOT NULL,
    superseded_by       uuid REFERENCES resolution_claim (id),
    created_at          timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX resolution_claim_type_idx ON resolution_claim (claim_type);
CREATE INDEX resolution_claim_confidence_idx ON resolution_claim (confidence_score);

-- ------------------------------------------------------------
-- validation_run: audit trail for each Validate layer execution.
-- Every pass/fail decision is tied to a run; no silent demotions.
-- ------------------------------------------------------------
CREATE TABLE validation_run (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    resolver_version        text NOT NULL,
    methodology_version     text NOT NULL,
    started_at              timestamptz NOT NULL,
    finished_at             timestamptz,
    claims_evaluated        int,
    claims_passed           int,
    claims_failed           int,
    claims_quarantined      int,
    hand_validation_sample  jsonb,             -- the 100-row sample + reviewer labels if a gate ran
    gate_passed             bool,
    gate_passed_at          timestamptz,
    notes                   text,
    created_at              timestamptz NOT NULL DEFAULT now()
);

-- Per-claim decision rows inside a validation run.
CREATE TABLE validation_run_decision (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    validation_run_id   uuid NOT NULL REFERENCES validation_run (id),
    resolution_claim_id uuid NOT NULL REFERENCES resolution_claim (id),
    decision            text NOT NULL CHECK (decision IN (
                            'passed', 'failed', 'quarantined'
                        )),
    trust_level         text NOT NULL CHECK (trust_level IN (
                            'unverified', 'verified', 'human_anchored'
                        )),
    deciding_rule       text,                  -- which floor or gate triggered this decision
    reviewer_label      text,                  -- 'agree' / 'disagree' / 'unclear' for hand-validated rows
    decided_at          timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX validation_run_decision_run_idx ON validation_run_decision (validation_run_id);
CREATE INDEX validation_run_decision_claim_idx ON validation_run_decision (resolution_claim_id);
