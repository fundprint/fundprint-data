"""Resolve pipeline orchestrator.

Reads unresolved staging rows, runs candidate generation and LLM verification,
writes resolution_claim rows, then assembles chains for affected clinics.

Idempotent for a fixed (resolver_version, prompt_version, input set):
re-running over already-processed staging rows is a no-op because existing
claims with matching (staging_id, resolver_version) are detected before
any LLM call is made.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import anthropic

from fundprint import db
from fundprint.resolve.candidate import get_candidates
from fundprint.resolve.chain import Chain, walk_chain
from fundprint.resolve.embeddings import embed
from fundprint.resolve.verify import VerificationClaim, verify
from fundprint.resolve.version import RESOLVER_VERSION

logger = logging.getLogger(__name__)

# Tables the pipeline reads new staging rows from.
# Currently only BACB providers; extend as new sources land.
_STAGING_TABLES = ["staging_bacb_provider"]

# Minimum similarity score for a candidate to be sent to the LLM.
# Candidates below this are dropped before the expensive verify step.
_SIMILARITY_FLOOR = 0.70


@dataclass
class RunResult:
    """Summary of a single pipeline run."""

    staging_rows_processed: int = 0
    claims_written: int = 0
    claims_skipped_idempotent: int = 0
    chains_walked: int = 0
    affected_clinic_ids: list[str] = field(default_factory=list)
    chains: list[Chain] = field(default_factory=list)


def _fetch_unprocessed_rows(conn: Any, staging_table: str, batch_size: int) -> list[dict]:
    """Return staging rows not yet processed by this resolver_version."""
    rows = conn.execute(
        f"""
        SELECT s.id, s.raw_name, s.city, s.state, s.source_record_id
        FROM {staging_table} s
        WHERE NOT EXISTS (
            SELECT 1 FROM resolution_claim rc
            WHERE rc.resolver_version = %s
              AND %s = ANY(rc.source_record_ids::text[])
        )
        ORDER BY s.ingested_at
        LIMIT %s
        """,
        (RESOLVER_VERSION, "s.id", batch_size),
    ).fetchall()
    return [
        {
            "id": str(r[0]),
            "raw_name": r[1],
            "city": r[2] or "",
            "state": r[3] or "",
            "source_record_id": str(r[4]),
        }
        for r in rows
    ]


def _claim_exists(conn: Any, staging_id: str) -> bool:
    """Check if a claim for this staging row and resolver version already exists."""
    row = conn.execute(
        """
        SELECT 1 FROM resolution_claim
        WHERE resolver_version = %s
          AND %s = ANY(source_record_ids::text[])
        LIMIT 1
        """,
        (RESOLVER_VERSION, staging_id),
    ).fetchone()
    return row is not None


def _write_claim(
    conn: Any,
    *,
    claim_type: str,
    clinic_id: str | None,
    owner_entity_id: str | None,
    parent_pe_firm_id: str | None,
    source_record_id: str,
    confidence_score: float,
    confidence_method: str,
    supporting_snippets: list[str] | None,
    llm_flags: list[str],
) -> str:
    """Insert a resolution_claim row; returns the new claim id."""
    claim_id = str(uuid.uuid4())
    snippets_json = (
        {"snippets": supporting_snippets} if supporting_snippets else None
    )
    conn.execute(
        """
        INSERT INTO resolution_claim (
            id, claim_type,
            clinic_id, owner_entity_id, parent_pe_firm_id,
            supporting_snippets, llm_flags,
            source_record_ids, confidence_score, confidence_method,
            resolver_version, extracted_at
        ) VALUES (
            %s, %s,
            %s, %s, %s,
            %s, %s,
            ARRAY[%s::uuid], %s, %s,
            %s, %s
        )
        """,
        (
            claim_id,
            claim_type,
            clinic_id,
            owner_entity_id,
            parent_pe_firm_id,
            snippets_json,
            llm_flags or [],
            source_record_id,
            confidence_score,
            confidence_method,
            RESOLVER_VERSION,
            datetime.now(timezone.utc),
        ),
    )
    return claim_id


def run(
    staging_table: str = "staging_bacb_provider",
    batch_size: int = 100,
    *,
    anthropic_client: anthropic.Anthropic | None = None,
    conn: Any = None,
) -> RunResult:
    """Run the resolve pipeline over a batch of staging rows.

    Idempotent: rows already processed by this resolver_version are skipped.
    """
    result = RunResult()
    own_conn = conn is None

    if own_conn:
        conn = db.connect()

    try:
        staging_rows = _fetch_unprocessed_rows(conn, staging_table, batch_size)
        result.staging_rows_processed = len(staging_rows)

        for row in staging_rows:
            staging_id = row["id"]

            # Idempotence: skip rows already processed under this resolver version.
            if _claim_exists(conn, staging_id):
                result.claims_skipped_idempotent += 1
                continue

            locality = " ".join(filter(None, [row["city"], row["state"]]))
            candidates = get_candidates(
                row["raw_name"],
                locality or None,
                conn=conn,
            )

            for candidate in candidates:
                if candidate.similarity < _SIMILARITY_FLOOR:
                    continue

                candidate_doc = {
                    "id": candidate.target_id,
                    "table": candidate.target_table,
                    "name": row["raw_name"],
                }
                staging_doc = {"id": staging_id, "name": row["raw_name"], "locality": locality}

                claim: VerificationClaim = verify(
                    staging_doc,
                    candidate_doc,
                    supporting_docs=[],
                    client=anthropic_client,
                )

                if not claim.link:
                    continue

                # Map candidate table to the right claim type and FK column.
                if candidate.target_table == "clinic":
                    claim_type = "clinic_to_owner"
                    clinic_id = candidate.target_id
                    owner_entity_id = None
                elif candidate.target_table == "owner_entity":
                    claim_type = "clinic_to_owner"
                    clinic_id = staging_id
                    owner_entity_id = candidate.target_id
                else:
                    claim_type = "owner_to_pe_firm"
                    clinic_id = None
                    owner_entity_id = None

                _write_claim(
                    conn,
                    claim_type=claim_type,
                    clinic_id=clinic_id,
                    owner_entity_id=owner_entity_id,
                    parent_pe_firm_id=(
                        candidate.target_id
                        if candidate.target_table == "parent_pe_firm"
                        else None
                    ),
                    source_record_id=row["source_record_id"],
                    confidence_score=claim.confidence,
                    confidence_method="llm_inferred",
                    supporting_snippets=claim.supporting_snippets,
                    llm_flags=claim.flags,
                )
                result.claims_written += 1

                if clinic_id and clinic_id not in result.affected_clinic_ids:
                    result.affected_clinic_ids.append(clinic_id)

        conn.commit()

        # Walk chains for all clinics touched by this run.
        for clinic_id in result.affected_clinic_ids:
            chain = walk_chain(clinic_id, conn=conn)
            result.chains.append(chain)
            result.chains_walked += 1

    except Exception:
        if own_conn:
            conn.rollback()
        raise
    finally:
        if own_conn:
            conn.close()

    return result
