"""Methodology audit packet builder.

Produces a structured summary for fundprint-methodology to embed in its
versioned white paper. A reader of the white paper must be able to
reconstruct the dataset's state at release time from this packet alone.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def build_packet(
    run_id: str,
    conn: Any,
    *,
    dataset_version: str,
    previous_release_dir: Path | None = None,
    dist_dir: Path | None = None,
) -> dict:
    """Build and write the audit packet for one release.

    Includes counts by state/acquirer, confidence-method breakdown, hand-validation
    summary, and a diff against the previous release.
    """
    if dist_dir is None:
        dist_dir = Path("dist/release")

    packet: dict = {
        "validation_run_id": run_id,
        "dataset_version": dataset_version,
    }

    packet["counts"] = _gather_counts(conn)
    packet["confidence_method_breakdown"] = _method_breakdown(conn)
    packet["hand_validation_summary"] = _hand_validation_summary(run_id, conn)
    packet["diff_vs_previous"] = _diff_vs_previous(conn, previous_release_dir)

    release_dir = dist_dir / dataset_version
    release_dir.mkdir(parents=True, exist_ok=True)
    dest = release_dir / "audit_packet.json"
    dest.write_text(json.dumps(packet, indent=2, default=str))

    return packet


def _gather_counts(conn: Any) -> dict:
    """Count clinics / owner_entities / PE firms by state and acquirer."""
    counts: dict = {}

    try:
        rows = conn.execute(
            """
            SELECT state, COUNT(*) AS n
            FROM clinic
            WHERE superseded_by IS NULL
            GROUP BY state
            ORDER BY n DESC
            """
        ).fetchall()
        counts["clinics_by_state"] = {r[0] or "unknown": r[1] for r in rows}
    except Exception as exc:
        logger.warning("clinics_by_state query failed: %s", exc)
        counts["clinics_by_state"] = {}

    try:
        rows = conn.execute(
            """
            SELECT ppf.name, COUNT(DISTINCT oe.id) AS owner_count
            FROM parent_pe_firm ppf
            JOIN owner_entity oe ON oe.parent_pe_firm_id = ppf.id
            WHERE oe.superseded_by IS NULL
            GROUP BY ppf.name
            ORDER BY owner_count DESC
            """
        ).fetchall()
        counts["owner_entities_by_acquirer"] = {r[0]: r[1] for r in rows}
    except Exception as exc:
        logger.warning("owner_entities_by_acquirer query failed: %s", exc)
        counts["owner_entities_by_acquirer"] = {}

    try:
        rows = conn.execute(
            "SELECT COUNT(*) FROM parent_pe_firm WHERE superseded_by IS NULL"
        ).fetchone()
        counts["total_pe_firms"] = rows[0] if rows else 0
    except Exception as exc:
        logger.warning("total_pe_firms query failed: %s", exc)
        counts["total_pe_firms"] = 0

    return counts


def _method_breakdown(conn: Any) -> dict:
    """Return fraction of published claims per confidence_method."""
    try:
        rows = conn.execute(
            """
            SELECT rc.confidence_method, COUNT(*) AS n
            FROM resolution_claim rc
            JOIN validation_run_decision vrd ON vrd.resolution_claim_id = rc.id
            WHERE vrd.decision = 'passed'
            GROUP BY rc.confidence_method
            """
        ).fetchall()
    except Exception as exc:
        logger.warning("method_breakdown query failed: %s", exc)
        return {}

    total = sum(r[1] for r in rows)
    if total == 0:
        return {}
    return {r[0]: round(r[1] / total, 4) for r in rows}


def _hand_validation_summary(run_id: str, conn: Any) -> dict:
    """Summarise the hand-validation sample attached to this run."""
    try:
        row = conn.execute(
            "SELECT hand_validation_sample FROM validation_run WHERE id = %s",
            (run_id,),
        ).fetchone()
    except Exception as exc:
        logger.warning("hand_validation_sample query failed: %s", exc)
        return {}

    if not row or not row[0]:
        return {}

    sample = row[0]
    if isinstance(sample, str):
        sample = json.loads(sample)

    rows = sample.get("rows", [])
    labels = [r.get("reviewer_label") for r in rows]
    agree = labels.count("agree")
    disagree = labels.count("disagree")
    unclear = labels.count("unclear")

    return {
        "sample_size": len(rows),
        "agree": agree,
        "disagree": disagree,
        "unclear": unclear,
        # Accuracy denominator excludes unclear per validation.md convention.
        "accuracy": round(agree / (agree + disagree), 4) if (agree + disagree) > 0 else None,
    }


def _diff_vs_previous(conn: Any, previous_release_dir: Path | None) -> dict:
    """Rows added, superseded, and quarantined vs the previous release."""
    diff: dict = {"rows_added": 0, "rows_superseded": 0, "rows_quarantined": 0}

    if previous_release_dir is None or not previous_release_dir.exists():
        return diff

    prev_packet = previous_release_dir / "audit_packet.json"
    if not prev_packet.exists():
        return diff

    try:
        previous = json.loads(prev_packet.read_text())
        prev_run_id = previous.get("validation_run_id")
    except Exception as exc:
        logger.warning("Could not read previous audit packet: %s", exc)
        return diff

    if not prev_run_id:
        return diff

    try:
        row = conn.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM validation_run_decision
                 WHERE validation_run_id = %s AND decision = 'passed') AS added,
                (SELECT COUNT(*) FROM resolution_claim
                 WHERE superseded_by IS NOT NULL) AS superseded,
                (SELECT COUNT(*) FROM validation_run_decision
                 WHERE validation_run_id = %s AND decision = 'quarantined') AS quarantined
            """,
            (prev_run_id, prev_run_id),
        ).fetchone()
        if row:
            diff["rows_added"] = row[0] or 0
            diff["rows_superseded"] = row[1] or 0
            diff["rows_quarantined"] = row[2] or 0
    except Exception as exc:
        logger.warning("diff query failed: %s", exc)

    return diff
