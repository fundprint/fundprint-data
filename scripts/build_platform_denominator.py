"""Build the platform coverage denominator: "N of M known PE-backed platforms".

Publishing a clinic count with no denominator invites "out of how many?" and has
no answer. This produces the answer, from *someone else's* published list, so the
finish line is not one we drew for ourselves.

What it does:

1. Fetches PESP's April 2026 report and content-hashes it into a source_record,
   the same provenance model as every other source. The denominator is auditable,
   not asserted: anyone can pull the same PDF and check the SHA-256.
2. Cross-checks every platform we claim to cover against the live published data.
   A platform marked `covered` whose owner_entity rows are absent or publish zero
   clinics is a **hard failure**, not a warning. Coverage that drifts from the
   database is worse than no coverage claim at all, because it is a claim about
   ourselves that we did not check.
3. Writes data/platforms/coverage.json for the dashboard exporter.

Run after export_dashboard_snapshot.py has a current database, and re-run whenever
a platform's status changes.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fundprint import db, fetch, platforms  # noqa: E402
from fundprint.acquire.base import _insert_source_record  # noqa: E402
from fundprint.storage import LocalFilesystemStore  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("platform_denominator")

OUT_PATH = Path(__file__).resolve().parents[1] / "data" / "platforms" / "coverage.json"
SOURCE_TYPE = "platform_denominator"
MODULE_VERSION = "0.1.0"


def _snapshot_pesp(conn) -> dict:
    """Fetch and content-hash the PESP report; return its provenance block."""
    content = fetch.get(platforms.PESP_REPORT_URL)
    if isinstance(content, bytes):
        blob = content
    else:  # httpx.Response
        blob = content.content
    snapshot_id, content_hash = LocalFilesystemStore().put(blob, suffix=".pdf")
    fetched_at = datetime.now(UTC)

    existing = conn.execute(
        "SELECT id FROM source_record WHERE source_url = %s AND content_hash = %s",
        (platforms.PESP_REPORT_URL, content_hash),
    ).fetchone()
    if existing:
        source_record_id = str(existing[0])
        logger.info("PESP report unchanged, reusing source_record %s", source_record_id)
    else:
        source_record_id = _insert_source_record(
            conn,
            source_url=platforms.PESP_REPORT_URL,
            snapshot_id=snapshot_id,
            source_type=SOURCE_TYPE,
            fetched_at=fetched_at,
            content_hash=content_hash,
            module_version=MODULE_VERSION,
        )
        logger.info("snapshotted PESP report as source_record %s", source_record_id)

    return {
        "source_url": platforms.PESP_REPORT_URL,
        "source_record_id": source_record_id,
        "content_hash": content_hash,
        "bytes": len(blob),
        "as_of": platforms.PESP_AS_OF,
        "appendix": platforms.PESP_APPENDIX_TITLE,
    }


def _published_owner_clinics(conn) -> dict[str, int]:
    """Published clinic count per owner_entity name, from the published views."""
    rows = conn.execute(
        """
        SELECT o.name, COUNT(*)
          FROM v_published_clinics c
          JOIN owner_entity o ON o.id = c.owner_entity_id
         GROUP BY o.name
        """
    ).fetchall()
    return {str(name): int(n) for name, n in rows}


def _known_owner_names(conn) -> set[str]:
    """Every live owner_entity name, so a zero-clinic owner is not a typo."""
    rows = conn.execute(
        "SELECT name FROM owner_entity WHERE superseded_by IS NULL"
    ).fetchall()
    return {str(r[0]) for r in rows}


def build(conn) -> dict:
    provenance = _snapshot_pesp(conn)
    published = _published_owner_clinics(conn)
    known = _known_owner_names(conn)

    problems: list[str] = []
    rows = []
    for p in platforms.PLATFORMS:
        clinics = sum(published.get(name, 0) for name in p.fundprint_owners)
        if p.status == platforms.COVERED:
            if not p.fundprint_owners:
                problems.append(f"{p.name}: marked covered but names no owner_entity")
            for name in p.fundprint_owners:
                if name not in known:
                    problems.append(f"{p.name}: owner_entity {name!r} not in database")
        rows.append(
            {
                "name": p.name,
                "investors": p.investors,
                "status": p.status,
                "note": p.note,
                "in_pesp": p.in_pesp,
                "pesp_facilities": p.pesp_facilities,
                "other_brands": p.other_brands,
                "fundprint_owners": p.fundprint_owners,
                "fundprint_clinics": clinics if p.fundprint_owners else None,
                "source_url": p.source_url,
            }
        )

    if problems:
        for msg in problems:
            logger.error("%s", msg)
        raise SystemExit(
            f"{len(problems)} platform(s) disagree with the database. "
            "Fix src/fundprint/platforms.py or the data before publishing coverage."
        )

    cov = platforms.coverage()
    cov["published_clinics_at_covered_platforms"] = sum(
        r["fundprint_clinics"] or 0 for r in rows if r["status"] == platforms.COVERED
    )
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "provenance": provenance,
        "coverage": cov,
        "platforms": rows,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(OUT_PATH))
    args = ap.parse_args()

    with db.transaction() as conn:
        doc = build(conn)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")

    c = doc["coverage"]
    logger.info(
        "coverage: %d of %d in-scope platforms (%d not started, %d blocked); "
        "%d facilities unpublished at platforms we do not cover",
        c["covered"],
        c["in_scope"],
        c["not_started"],
        c["blocked"],
        c["unpublished_facilities"],
    )
    logger.info("wrote %s", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
