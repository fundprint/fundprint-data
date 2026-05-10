"""ValidationRun lifecycle management.

Every run is append-only: we insert rows but never UPDATE past ones.
A journalist asking "what did you know on date X" must get a complete
answer from this table without any reconstruction.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fundprint.models import ResolutionClaim, ValidationRun, ValidationRunDecision
from fundprint.validate.floors import _FLOOR_BY_CLAIM_TYPE, passes_floor


def open_run(
    conn: Any,
    *,
    methodology_version: str,
    resolver_version: str,
) -> uuid.UUID:
    """Insert a new validation_run row and return its id.

    The run is open (finished_at is NULL) until close_run() is called.
    """
    run_id = uuid.uuid4()
    started_at = datetime.now(timezone.utc)

    conn.execute(
        """
        INSERT INTO validation_run (
            id, resolver_version, methodology_version, started_at, created_at
        ) VALUES (%s, %s, %s, %s, %s)
        """,
        (str(run_id), resolver_version, methodology_version, started_at, started_at),
    )
    return run_id


def record_decision(
    conn: Any,
    *,
    run_id: uuid.UUID,
    claim: ResolutionClaim,
    passed: bool,
    quarantined: bool = False,
) -> None:
    """Write a validation_run_decision row for one claim.

    The deciding_rule captures which floor was applied, so the audit is
    self-explanatory without needing to recompute it later.
    """
    floor = _FLOOR_BY_CLAIM_TYPE.get(claim.claim_type)
    floor_label = f"{claim.claim_type}:floor={floor}" if floor is not None else "unknown_type"

    if quarantined:
        decision = "quarantined"
        trust_level = "unverified"
    elif passed:
        decision = "passed"
        trust_level = "verified"
    else:
        decision = "failed"
        trust_level = "unverified"

    conn.execute(
        """
        INSERT INTO validation_run_decision (
            id, validation_run_id, resolution_claim_id,
            decision, trust_level, deciding_rule, decided_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (
            str(uuid.uuid4()),
            str(run_id),
            str(claim.id),
            decision,
            trust_level,
            floor_label,
            datetime.now(timezone.utc),
        ),
    )


def close_run(
    conn: Any,
    *,
    run_id: uuid.UUID,
    passed: bool,
    counts: dict[str, int],
) -> None:
    """Stamp finished_at and summary counts onto the run row.

    counts should have keys: evaluated, passed, failed, quarantined.
    """
    conn.execute(
        """
        UPDATE validation_run
        SET finished_at = %s,
            gate_passed = %s,
            gate_passed_at = %s,
            claims_evaluated = %s,
            claims_passed = %s,
            claims_failed = %s,
            claims_quarantined = %s
        WHERE id = %s
        """,
        (
            datetime.now(timezone.utc),
            passed,
            datetime.now(timezone.utc) if passed else None,
            counts.get("evaluated", 0),
            counts.get("passed", 0),
            counts.get("failed", 0),
            counts.get("quarantined", 0),
            str(run_id),
        ),
    )
