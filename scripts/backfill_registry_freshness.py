"""Backfill registry freshness onto rows staged before the parser captured it.

The NPPES parser (module_version 0.1.0) discarded `status`, `last_updated`,
`certification_date`, and `enumeration_date`. Those fields were never lost: they
are in the stored snapshot blobs, which are byte-faithful to what the registry
returned. So this is a **re-parse, not a re-fetch**, exactly as docs/ingestion.md
prescribes ("the snapshot blob is the truth ... if the parser was wrong, we
re-parse from the blob; we do not re-fetch"). No network call is made.

Every NPPES snapshot on disk is re-parsed with the current parser, and the
freshness fields are written onto the matching staging rows (by NPI) and onto the
clinic rows promoted from them.

Idempotent: re-running rewrites the same values.

Usage:
    python scripts/backfill_registry_freshness.py --dry-run
    python scripts/backfill_registry_freshness.py
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true", help="Report without writing.")
    args = p.parse_args()

    from fundprint import db
    from fundprint.acquire.nppes import parse_nppes_json
    from fundprint.storage import LocalFilesystemStore

    store = LocalFilesystemStore()
    conn = db.connect()
    try:
        snaps = conn.execute(
            "SELECT snapshot_id FROM source_record WHERE source_type = 'nppes'"
        ).fetchall()

        # Re-parse every stored NPPES snapshot. Later snapshots win on conflict:
        # the same NPI re-fetched later carries the newer registry timestamps.
        fresh: dict[str, dict] = {}
        unreadable = 0
        for (sid,) in snaps:
            try:
                content = store.get(sid)
            except FileNotFoundError:
                unreadable += 1
                continue
            for row in parse_nppes_json(content):
                npi = row.get("npi")
                if not npi:
                    continue
                prior = fresh.get(npi)
                if prior is None or (row.get("registry_last_updated") or "") >= (
                    prior.get("registry_last_updated") or ""
                ):
                    fresh[npi] = row

        logger.info(
            "re-parsed %d snapshot(s) (%d unreadable) -> %d NPI record(s)",
            len(snaps),
            unreadable,
            len(fresh),
        )

        staged_npis = {
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT npi FROM staging_bacb_provider WHERE npi IS NOT NULL"
            ).fetchall()
        }
        clinic_npis = {
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT npi FROM clinic WHERE npi IS NOT NULL"
            ).fetchall()
        }
        covered_staging = len(staged_npis & fresh.keys())
        covered_clinic = len(clinic_npis & fresh.keys())
        logger.info(
            "coverage: %d/%d staged NPIs, %d/%d clinic NPIs",
            covered_staging,
            len(staged_npis),
            covered_clinic,
            len(clinic_npis),
        )

        statuses = Counter(
            (r.get("registry_status") or "(none)") for n, r in fresh.items() if n in clinic_npis
        )
        logger.info("registry status across clinic NPIs: %s", dict(statuses))

        if args.dry_run:
            logger.info("dry run; nothing written")
            return 0

        for npi, row in fresh.items():
            params = (
                row.get("registry_status"),
                row.get("registry_last_updated"),
                row.get("registry_enumerated_on"),
                npi,
            )
            conn.execute(
                """
                UPDATE staging_bacb_provider
                SET registry_status = %s,
                    registry_last_updated = %s::date,
                    registry_enumerated_on = %s::date
                WHERE npi = %s
                """,
                params,
            )
            conn.execute(
                """
                UPDATE clinic
                SET registry_status = %s,
                    registry_last_updated = %s::date,
                    registry_enumerated_on = %s::date
                WHERE npi = %s
                """,
                params,
            )
        conn.commit()
        logger.info("backfilled freshness for %d NPI(s)", len(fresh))
    except Exception:
        logger.exception("backfill failed")
        conn.rollback()
        return 1
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
