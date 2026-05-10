"""Pydantic models mirroring the database schema. Type-safe, not an ORM."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal, Optional

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
    superseded_by: Optional[uuid.UUID] = None


# ------------------------------------------------------------
# Append-only / immutable tables
# ------------------------------------------------------------


class SourceRecord(BaseModel):
    """Pointer to a public document supporting any claim in the dataset."""

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    source_url: str
    snapshot_id: Optional[str] = None
    source_type: str
    fetched_at: datetime
    content_hash: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ------------------------------------------------------------
# Staging tables (Store layer output)
# ------------------------------------------------------------


class StagingBacbProvider(BaseModel):
    """Raw BACB provider record after ingestion, before resolution."""

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    source_record_id: uuid.UUID
    raw_name: str
    address_line1: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip: Optional[str] = None
    npi: Optional[str] = None
    credential_type: Optional[str] = None
    ingested_at: datetime = Field(default_factory=datetime.utcnow)


class StagingSecFiling(BaseModel):
    """Raw SEC filing record (Form D, SC 13D, etc.) after ingestion."""

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    source_record_id: uuid.UUID
    accession_number: str
    form_type: str
    filer_name: Optional[str] = None
    filing_date: Optional[date] = None
    issuer_name: Optional[str] = None
    issuer_state: Optional[str] = None
    amount_raised: Optional[float] = None
    raw_json: Optional[dict] = None
    ingested_at: datetime = Field(default_factory=datetime.utcnow)


class StagingPePortfolioListing(BaseModel):
    """Raw PE portfolio page listing after ingestion."""

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    source_record_id: uuid.UUID
    pe_firm_name: str
    portfolio_name: str
    portfolio_url: Optional[str] = None
    description: Optional[str] = None
    sector_tags: list[str] = Field(default_factory=list)
    listed_as_of: Optional[date] = None
    ingested_at: datetime = Field(default_factory=datetime.utcnow)


# ------------------------------------------------------------
# Core entity tables (Resolve layer output)
# ------------------------------------------------------------


class ParentPeFirm(ProvenanceMixin):
    """A PE firm or fund that owns or has owned one or more owner_entity rows."""

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    name: str
    name_normalized: Optional[str] = None
    hq_state: Optional[str] = None
    website: Optional[str] = None
    # Embeddings are stored in Postgres as vector(1024); here they're plain lists.
    # The model column lets resolution block cross-model cosine comparisons.
    name_embedding: Optional[list[float]] = None
    name_embedding_model: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class OwnerEntity(ProvenanceMixin):
    """The legal entity that directly operates a clinic or group of clinics."""

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    name: str
    name_normalized: Optional[str] = None
    entity_type: Optional[str] = None
    state_of_incorporation: Optional[str] = None
    parent_pe_firm_id: Optional[uuid.UUID] = None
    name_embedding: Optional[list[float]] = None
    name_embedding_model: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Clinic(ProvenanceMixin):
    """A physical or operating ABA service location."""

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    name: str
    name_normalized: Optional[str] = None
    address_line1: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip: Optional[str] = None
    npi: Optional[str] = None
    owner_entity_id: Optional[uuid.UUID] = None
    name_embedding: Optional[list[float]] = None
    name_embedding_model: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class AcquisitionEvent(ProvenanceMixin):
    """A dated ownership change event. Append-only; never edit in place."""

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    owner_entity_id: uuid.UUID
    parent_pe_firm_id: uuid.UUID
    event_type: EventType
    event_date: Optional[date] = None
    event_date_circa: bool = False
    deal_notes: Optional[str] = None
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
    clinic_id: Optional[uuid.UUID] = None
    owner_entity_id: Optional[uuid.UUID] = None
    parent_pe_firm_id: Optional[uuid.UUID] = None
    acquisition_event_id: Optional[uuid.UUID] = None
    supporting_snippets: Optional[dict] = None
    llm_flags: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ValidationRun(BaseModel):
    """Audit record for a single Validate layer execution."""

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    resolver_version: str
    methodology_version: str
    started_at: datetime
    finished_at: Optional[datetime] = None
    claims_evaluated: Optional[int] = None
    claims_passed: Optional[int] = None
    claims_failed: Optional[int] = None
    claims_quarantined: Optional[int] = None
    hand_validation_sample: Optional[dict] = None
    gate_passed: Optional[bool] = None
    gate_passed_at: Optional[datetime] = None
    notes: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ValidationRunDecision(BaseModel):
    """Per-claim decision produced during a validation run."""

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    validation_run_id: uuid.UUID
    resolution_claim_id: uuid.UUID
    decision: Decision
    trust_level: TrustLevel
    deciding_rule: Optional[str] = None
    reviewer_label: Optional[str] = None
    decided_at: datetime = Field(default_factory=datetime.utcnow)
