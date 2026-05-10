"""Hand-validation sample generator.

Draws a stratified random sample of resolution claims, one stratum per
confidence_method. The sample is written to disk so reviewers can work offline
and the seed is captured for reproducibility.
"""

from __future__ import annotations

import json
import random
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class SampleRow:
    """One row in the hand-validation sheet."""

    claim_id: str
    claim_type: str
    proposed_link: dict
    source_urls: list[str]
    confidence_score: float
    confidence_method: str
    reviewer_label: str | None = None  # filled in offline: agree/disagree/unclear


@dataclass
class SampleSheet:
    """Output of draw_sample. JSON-serializable."""

    run_id: str
    seed: int | None
    total_drawn: int
    rows: list[SampleRow] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "seed": self.seed,
            "total_drawn": self.total_drawn,
            "rows": [
                {
                    "claim_id": r.claim_id,
                    "claim_type": r.claim_type,
                    "proposed_link": r.proposed_link,
                    "source_urls": r.source_urls,
                    "confidence_score": r.confidence_score,
                    "confidence_method": r.confidence_method,
                    "reviewer_label": r.reviewer_label,
                }
                for r in self.rows
            ],
        }


def draw_sample(
    run_id: str | uuid.UUID,
    conn: Any,
    n: int = 100,
    seed: int | None = None,
    samples_dir: Path | None = None,
) -> SampleSheet:
    """Draw a stratified random sample of claims for hand-validation.

    Stratifies by confidence_method so all method buckets are represented.
    Writes the sample to samples/<run_id>.json for offline review.
    """
    rng = random.Random(seed)

    # Fetch all unverified claims with their source URLs for this run's scope.
    # In practice the caller constrains to a specific resolver_version.
    rows = conn.execute(
        """
        SELECT
            rc.id,
            rc.claim_type,
            rc.confidence_score,
            rc.confidence_method,
            rc.clinic_id,
            rc.owner_entity_id,
            rc.parent_pe_firm_id,
            rc.acquisition_event_id,
            ARRAY_AGG(sr.source_url) FILTER (WHERE sr.source_url IS NOT NULL) AS source_urls
        FROM resolution_claim rc
        LEFT JOIN source_record sr ON sr.id = ANY(rc.source_record_ids)
        GROUP BY rc.id
        ORDER BY rc.created_at
        """
    ).fetchall()

    if not rows:
        sheet = SampleSheet(run_id=str(run_id), seed=seed, total_drawn=0)
        _write_sample(sheet, samples_dir)
        return sheet

    # Group by confidence_method for stratification.
    by_method: dict[str, list] = {}
    for row in rows:
        method = row[3] or "unknown"
        by_method.setdefault(method, []).append(row)

    # Allocate n slots across strata proportionally, minimum 1 per stratum.
    total = len(rows)
    strata_counts: dict[str, int] = {}
    for method, bucket in by_method.items():
        share = max(1, round(n * len(bucket) / total))
        strata_counts[method] = share

    # Trim if rounding pushed us over n.
    while sum(strata_counts.values()) > n:
        largest = max(strata_counts, key=lambda k: strata_counts[k])
        strata_counts[largest] -= 1

    sample_rows: list[SampleRow] = []
    for method, count in strata_counts.items():
        bucket = by_method[method]
        picked = rng.sample(bucket, min(count, len(bucket)))
        for raw in picked:
            claim_id, claim_type, conf_score, conf_method = raw[0], raw[1], raw[2], raw[3]
            clinic_id, owner_id, pe_id, acq_id = raw[4], raw[5], raw[6], raw[7]
            source_urls = raw[8] or []

            proposed_link = {
                k: str(v)
                for k, v in {
                    "clinic_id": clinic_id,
                    "owner_entity_id": owner_id,
                    "parent_pe_firm_id": pe_id,
                    "acquisition_event_id": acq_id,
                }.items()
                if v is not None
            }

            sample_rows.append(
                SampleRow(
                    claim_id=str(claim_id),
                    claim_type=claim_type,
                    proposed_link=proposed_link,
                    source_urls=[str(u) for u in source_urls],
                    confidence_score=float(conf_score),
                    confidence_method=conf_method,
                )
            )

    sheet = SampleSheet(
        run_id=str(run_id),
        seed=seed,
        total_drawn=len(sample_rows),
        rows=sample_rows,
    )
    _write_sample(sheet, samples_dir)
    return sheet


def _write_sample(sheet: SampleSheet, samples_dir: Path | None) -> None:
    if samples_dir is None:
        samples_dir = Path("samples")
    samples_dir.mkdir(parents=True, exist_ok=True)
    dest = samples_dir / f"{sheet.run_id}.json"
    dest.write_text(json.dumps(sheet.to_dict(), indent=2, default=str))
