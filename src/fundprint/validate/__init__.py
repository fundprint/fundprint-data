"""Validate layer: confidence scoring, quarantine, and audit hooks.

Public entrypoint is run_validation(). Submodules are importable for testing
and one-off scripts.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fundprint.validate.audit import close_run, open_run, record_decision
from fundprint.validate.floors import passes_floor
from fundprint.validate.quarantine import should_quarantine

logger = logging.getLogger(__name__)


def run_validation(
    conn: Any,
    *,
    methodology_version: str,
    resolver_version: str,
) -> uuid.UUID:
    """Validate all unvalidated resolution_claim rows and write audit records.

    Returns the validation_run_id for the run just completed.
    """
    run_id = open_run(
        conn,
        methodology_version=methodology_version,
        resolver_version=resolver_version,
    )

    claims = _fetch_unvalidated_claims(conn, resolver_version=resolver_version)

    counts = {"evaluated": 0, "passed": 0, "failed": 0, "quarantined": 0}

    for row in claims:
        counts["evaluated"] += 1

        claim = _row_to_claim(row)
        context = _build_context(row)

        quarantined, _reason = should_quarantine(claim, context)
        if quarantined:
            counts["quarantined"] += 1
            record_decision(conn, run_id=run_id, claim=claim, passed=False, quarantined=True)
            continue

        passed = passes_floor(claim)
        if passed:
            counts["passed"] += 1
        else:
            counts["failed"] += 1

        record_decision(conn, run_id=run_id, claim=claim, passed=passed)

    # Gate passes only if every evaluated claim either passed or was quarantined;
    # any failure means the batch is not publication-ready.
    gate_passed = counts["failed"] == 0 and counts["evaluated"] > 0

    close_run(conn, run_id=run_id, passed=gate_passed, counts=counts)
    conn.commit()

    logger.info(
        "validation run %s finished: %d evaluated, %d passed, %d failed, %d quarantined",
        run_id,
        counts["evaluated"],
        counts["passed"],
        counts["failed"],
        counts["quarantined"],
    )
    return run_id


def _fetch_unvalidated_claims(conn: Any, *, resolver_version: str) -> list[Any]:
    """Return resolution_claim rows not yet covered by any validation_run_decision."""
    return conn.execute(
        """
        SELECT rc.id, rc.claim_type, rc.confidence_score,
               rc.confidence_method, rc.llm_flags
        FROM resolution_claim rc
        WHERE rc.resolver_version = %s
          AND NOT EXISTS (
              SELECT 1 FROM validation_run_decision vrd
              WHERE vrd.resolution_claim_id = rc.id
          )
        ORDER BY rc.created_at
        """,
        (resolver_version,),
    ).fetchall()


def _row_to_claim(row: Any) -> Any:
    """Build a minimal claim-like object from a DB row for floor/quarantine checks.

    We use a simple namespace rather than the full Pydantic model to avoid
    requiring a live DB schema match during tests.
    """
    from types import SimpleNamespace

    return SimpleNamespace(
        id=row[0],
        claim_type=row[1],
        confidence_score=float(row[2]) if row[2] is not None else 0.0,
        confidence_method=row[3],
        llm_flags=row[4] if row[4] is not None else [],
    )


def _build_context(row: Any) -> dict:
    """Extract quarantine-relevant context from a raw DB row."""
    # These context fields are not yet columns; they will be added in a future
    # migration. For now they default to False/None.
    return {
        "sources_contradict": False,
        "external_challenge": False,
        "reviewer_label": None,
    }
