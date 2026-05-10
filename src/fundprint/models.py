"""Pydantic models mirroring the database schema. Type-safe, not an ORM."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

# Enum literals used across tables
ConfidenceMethod = Literal[
    "exact_match", "fuzzy_high", "fuzzy_low", "llm_inferred", "human_verified"
]
EventType = Literal[
    "acquisition", "divestiture", "bankruptcy", "recapitalization", "merger"
]
ClaimType = Literal["clinic_to_owner", "owner_to_pe_firm", "acquisition_event"]
Decision = Literal["passed", "failed", "quarantined"]
TrustLevel = Literal["unverified", "verified", "human_anchored"]


class ProvenanceMixin(BaseModel):
    """Six required fields that every derived row must carry."""

    source_record_ids: list[uuid.UUID]
    confidence_score: float = Field(ge=0.0, le=1.0)
    confidence_method: ConfidenceMethod
    resolver_version: str
    extracted_at: datetime
    superseded_by: uuid.UUID | None = None


# ------------------------------------------------------------
# Append-only / immutable tables
# ------------------------------------------------------------


class SourceRecord(BaseModel):
    """Pointer to a public document supporting any claim in the dataset."""

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    source_url: str
    snapshot_id: str | None = None
    source_type: str
    fetched_at: datetime
    content_hash: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ------------------------------------------------------------
# Staging tables (Store layer output)
# ------------------------------------------------------------


class StagingBacbProvider(BaseModel):
    """Raw BACB provider record after ingestion, before resolution."""

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    source_record_id: uuid.UUID
    raw_name: str
    address_line1: str | None = None
    city: str | None = None
    state: str | None = None
    zip: str | None = None
    npi: str | None = None
    credential_type: str | None = None
    ingested_at: datetime = Field(default_factory=datetime.utcnow)


class StagingSecFiling(BaseModel):
    """Raw SEC filing record (Form D, SC 13D, etc.) after ingestion."""

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    source_record_id: uuid.UUID
    accession_number: str
    form_type: str
    filer_name: str | None = None
    filing_date: date | None = None
    issuer_name: str | None = None
    issuer_state: str | None = None
    amount_raised: float | None = None
    raw_json: dict | None = None
    ingested_at: datetime = Field(default_factory=datetime.utcnow)


class StagingPePortfolioListing(BaseModel):
    """Raw PE portfolio page listing after ingestion."""

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    source_record_id: uuid.UUID
    pe_firm_name: str
    portfolio_name: str
    portfolio_url: str | None = None
    description: str | None = None
    sector_tags: list[str] = Field(default_factory=list)
    listed_as_of: date | None = None
    ingested_at: datetime = Field(default_factory=datetime.utcnow)


# ------------------------------------------------------------
# Core entity tables (Resolve layer output)
# ------------------------------------------------------------


class ParentPeFirm(ProvenanceMixin):
    """A PE firm or fund that owns or has owned one or more owner_entity rows."""

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    name: str
    name_normalized: str | None = None
    hq_state: str | None = None
    website: str | None = None
    # Embeddings are stored in Postgres as vector(1024); here they're plain lists.
    # The model column lets resolution block cross-model cosine comparisons.
    name_embedding: list[float] | None = None
    name_embedding_model: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class OwnerEntity(ProvenanceMixin):
    """The legal entity that directly operates a clinic or group of clinics."""

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    name: str
    name_normalized: str | None = None
    entity_type: str | None = None
    state_of_incorporation: str | None = None
    parent_pe_firm_id: uuid.UUID | None = None
    name_embedding: list[float] | None = None
    name_embedding_model: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Clinic(ProvenanceMixin):
    """A physical or operating ABA service location."""

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    name: str
    name_normalized: str | None = None
    address_line1: str | None = None
    city: str | None = None
    state: str | None = None
    zip: str | None = None
    npi: str | None = None
    owner_entity_id: uuid.UUID | None = None
    name_embedding: list[float] | None = None
    name_embedding_model: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class AcquisitionEvent(ProvenanceMixin):
    """A dated ownership change event. Append-only; never edit in place."""

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    owner_entity_id: uuid.UUID
    parent_pe_firm_id: uuid.UUID
    event_type: EventType
    event_date: date | None = None
    event_date_circa: bool = False
    deal_notes: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ------------------------------------------------------------
# Resolution and validation tables
# ------------------------------------------------------------


class ResolutionClaim(ProvenanceMixin):
    """A candidate link proposed by the resolver - fuzzy or LLM.

    Written by Resolve, read by Validate. Never written directly to entity tables.
    """

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    claim_type: ClaimType
    clinic_id: uuid.UUID | None = None
    owner_entity_id: uuid.UUID | None = None
    parent_pe_firm_id: uuid.UUID | None = None
    acquisition_event_id: uuid.UUID | None = None
    supporting_snippets: dict | None = None
    llm_flags: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ValidationRun(BaseModel):
    """Audit record for a single Validate layer execution."""

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    resolver_version: str
    methodology_version: str
    started_at: datetime
    finished_at: datetime | None = None
    claims_evaluated: int | None = None
    claims_passed: int | None = None
    claims_failed: int | None = None
    claims_quarantined: int | None = None
    hand_validation_sample: dict | None = None
    gate_passed: bool | None = None
    gate_passed_at: datetime | None = None
    notes: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ValidationRunDecision(BaseModel):
    """Per-claim decision produced during a validation run."""

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    validation_run_id: uuid.UUID
    resolution_claim_id: uuid.UUID
    decision: Decision
    trust_level: TrustLevel
    deciding_rule: str | None = None
    reviewer_label: str | None = None
    decided_at: datetime = Field(default_factory=datetime.utcnow)
