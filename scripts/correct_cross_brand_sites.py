"""One-off dataset correction: collapse one physical site registered under two of
a single parent firm's brands.

`site_key` identifies a site by (owner_entity, street, zip). That collapses many
NPIs at one address under one brand, which was the Action Behavior Centers bug.
It cannot see the same site registered under two *different* brands that the same
parent firm owns, because the owner_entity differs, so the site is counted twice.

This is not a co-location. It is what a rebrand leaves behind. KKR bought Florida
Autism Center and Trumpet Behavioral Health and folded them into BlueSprig, and
the registry still carries both registrations at the same suite: at 2437 SE 17th
St Ste 102, Ocala FL, the BlueSprig record was re-certified in October 2025 and
the Florida Autism Center record has not been touched since November 2022. One
suite does not hold two competing centers, and these are not even competitors:
they are the same parent. It is one clinic.

The methodology previously disclosed this as a residual counting limit rather than
correcting it, on the reasoning that merging two separately registered legal
entities into one center is a stronger claim than the registry supports. That was
backwards. Declining to merge is not neutral; it asserts that two centers exist at
one suite, which is the claim the evidence actually contradicts. The project's rule
is to publish less and disclose more, so the rows are merged.

Survivor choice is deterministic so a rebuild reproduces it, and it is chosen to
keep the row that best evidences the site being open *now*:
  1. a row from the owner's own location directory or roster (the owner is saying
     this center is open today) beats a registry row, which reports existence-ever;
  2. among registry rows, the freshest registration wins;
  3. ties break on the lowest clinic id.

Nothing is deleted. The loser's `superseded_by` points at the survivor and its
resolution_claim stays on the record: that NPI really is registered to that brand
at that address. What was wrong was counting it as its own location.

Idempotent: a second run finds one live row per site per parent firm and does
nothing.

Usage:
    python scripts/correct_cross_brand_sites.py --dry-run
    python scripts/correct_cross_brand_sites.py
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import defaultdict
from datetime import date

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# A row the owner itself publishes as a current center is better evidence of the
# site being open than a registry record, which never marks a closed clinic closed.
_DIRECTORY_SOURCES = frozenset({"owner_location_directory"})


def _survivor_sort_key(row: dict) -> tuple[int, int, str]:
    """Deterministic ordering; the first row after sorting is kept."""
    from_directory = bool(_DIRECTORY_SOURCES & row["source_types"])
    last_updated = row["registry_last_updated"] or date.min
    # Negated so the freshest registration sorts first under an ascending sort.
    return (0 if from_directory else 1, -last_updated.toordinal(), row["id"])


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be superseded without writing.",
    )
    args = p.parse_args()

    from fundprint import db
    from fundprint.resolve.clinic_link import normalize_street, zip5

    conn = db.connect()
    try:
        rows = conn.execute(
            """
            SELECT c.id, c.address_line1, c.zip, c.npi, c.registry_last_updated,
                   oe.name AS owner_name,
                   l.parent_pe_firm_id, l.parent_pe_firm_name,
                   COALESCE(
                     ARRAY(
                       SELECT DISTINCT sr.source_type
                       FROM unnest(c.source_record_ids) AS srid
                       JOIN source_record sr ON sr.id = srid
                     ), '{}'
                   ) AS source_types
            FROM clinic c
            JOIN owner_entity oe ON oe.id = c.owner_entity_id
            JOIN v_published_pe_links l ON l.owner_entity_id = oe.id
            WHERE c.superseded_by IS NULL
              AND c.address_line1 IS NOT NULL
            """
        ).fetchall()

        # Group by physical site *within one parent firm*. A shared address across
        # two unrelated firms is the ghost-clinic case and is handled elsewhere;
        # here both brands roll up to the same owner, so the site is one site.
        groups: dict[tuple, list[dict]] = defaultdict(list)
        for (
            cid,
            addr,
            zipc,
            npi,
            last_updated,
            owner_name,
            firm_id,
            firm_name,
            source_types,
        ) in rows:
            street = normalize_street(addr)
            if not street:
                continue
            key = (str(firm_id), street, zip5(zipc))
            groups[key].append(
                {
                    "id": str(cid),
                    "npi": npi,
                    "owner_name": owner_name,
                    "firm_name": firm_name,
                    "address_line1": addr,
                    "registry_last_updated": last_updated,
                    "source_types": set(source_types or ()),
                }
            )

        # Only groups spanning more than one brand are this bug. Same-brand
        # duplicates are already collapsed by site_key in the linker.
        dupes = {
            k: v
            for k, v in groups.items()
            if len({m["owner_name"] for m in v}) > 1
        }
        if not dupes:
            logger.info("no cross-brand duplicate sites; no-op")
            return 0

        per_firm: dict[str, int] = defaultdict(int)
        per_brand: dict[str, int] = defaultdict(int)
        pairs: list[tuple[str, str]] = []  # (superseded_id, survivor_id)
        for members in dupes.values():
            members.sort(key=_survivor_sort_key)
            survivor = members[0]
            for loser in members[1:]:
                pairs.append((loser["id"], survivor["id"]))
                per_firm[loser["firm_name"]] += 1
                per_brand[loser["owner_name"]] += 1

        logger.info(
            "%d cross-brand duplicate site(s); %d row(s) to supersede",
            len(dupes),
            len(pairs),
        )
        for firm, n in sorted(per_firm.items(), key=lambda kv: -kv[1]):
            logger.info("  %-30s -%d", firm, n)
        logger.info("losing brand rows:")
        for brand, n in sorted(per_brand.items(), key=lambda kv: -kv[1]):
            logger.info("  %-34s -%d", brand, n)

        if args.dry_run:
            logger.info("dry run; nothing written")
            return 0

        for loser_id, survivor_id in pairs:
            conn.execute(
                "UPDATE clinic SET superseded_by = %s WHERE id = %s",
                (survivor_id, loser_id),
            )
        conn.commit()
        logger.info("superseded %d cross-brand duplicate clinic row(s)", len(pairs))
    except Exception:
        logger.exception("correction failed")
        conn.rollback()
        return 1
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
