"""One-off dataset correction: collapse multi-NPI duplicates of one physical site.

A clinic is a physical service location. NPPES enumerates *registrations*, and a
chain may register several NPIs at one address: Action Behavior Centers holds six
NPIs at 320 E 1st Ave Ste 101, Broomfield CO, under two legal-entity name
variants ("ACTION BEHAVIOR CENTERS LLC" and "ACTION BEHAVIOR CENTERS THERAPY
LLC"). The clinic linker de-duplicated registry rows by NPI, which cannot see
that, so it wrote one clinic row per NPI and the dataset counted one center six
times. Across the published set this inflated 712 real sites to 904 rows, worst
at Action Behavior Centers (184 rows, 71 sites).

This script records the correction the auditable way: for each group of live
clinic rows sharing a site_key, it keeps one survivor and sets `superseded_by` on
the rest, pointing at the survivor. Nothing is deleted, and the resolution_claims
stay on the record: those NPIs really are registered to that owner, so the claims
are true. What was wrong was counting each as its own location.

Survivor choice is deterministic so a rebuild reproduces it: prefer a row that
carries an NPI, then the lowest NPI, then the lowest id.

The linker is separately fixed so this cannot recur (see site_key in
fundprint.resolve.clinic_link); this script only corrects accumulated state.

Idempotent: a second run finds one live row per site and does nothing.

Usage:
    python scripts/correct_duplicate_sites.py --dry-run
    python scripts/correct_duplicate_sites.py
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import defaultdict

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _survivor_sort_key(row: dict) -> tuple[int, str, str]:
    """Deterministic ordering; the first row after sorting is kept."""
    npi = row["npi"] or ""
    return (0 if npi else 1, npi, row["id"])


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be superseded without writing.",
    )
    args = p.parse_args()

    from fundprint import db
    from fundprint.resolve.clinic_link import site_key

    conn = db.connect()
    try:
        rows = conn.execute(
            """
            SELECT c.id, c.owner_entity_id, c.address_line1, c.zip, c.city,
                   c.state, c.npi, oe.name
            FROM clinic c
            JOIN owner_entity oe ON oe.id = c.owner_entity_id
            WHERE c.superseded_by IS NULL AND c.owner_entity_id IS NOT NULL
            """
        ).fetchall()

        groups: dict[tuple, list[dict]] = defaultdict(list)
        for cid, owner_id, addr, zipc, city, state, npi, owner_name in rows:
            key = site_key(str(owner_id), addr, zipc, city, state)
            groups[key].append(
                {
                    "id": str(cid),
                    "npi": npi,
                    "owner_name": owner_name,
                    "address_line1": addr,
                    "city": city,
                    "state": state,
                }
            )

        dupes = {k: v for k, v in groups.items() if len(v) > 1}
        if not dupes:
            logger.info("every live clinic is already one row per site; no-op")
            return 0

        per_owner: dict[str, int] = defaultdict(int)
        pairs: list[tuple[str, str]] = []  # (superseded_id, survivor_id)
        for members in dupes.values():
            members.sort(key=_survivor_sort_key)
            survivor = members[0]
            for loser in members[1:]:
                pairs.append((loser["id"], survivor["id"]))
                per_owner[loser["owner_name"]] += 1

        logger.info(
            "%d live clinic rows -> %d sites; %d duplicate row(s) to supersede",
            len(rows),
            len(groups),
            len(pairs),
        )
        for owner, n in sorted(per_owner.items(), key=lambda kv: -kv[1]):
            logger.info("  %-34s -%d", owner, n)

        if args.dry_run:
            logger.info("dry run; nothing written")
            return 0

        for loser_id, survivor_id in pairs:
            conn.execute(
                "UPDATE clinic SET superseded_by = %s WHERE id = %s",
                (survivor_id, loser_id),
            )
        conn.commit()
        logger.info("superseded %d duplicate clinic row(s)", len(pairs))
    except Exception:
        logger.exception("correction failed")
        conn.rollback()
        return 1
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
