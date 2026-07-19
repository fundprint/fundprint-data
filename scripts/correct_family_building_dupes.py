"""One-off dataset correction: collapse suite-variant duplicates of one site.

`correct_cross_brand_sites` collapses one site registered under two of a parent
firm's brands, but only when both rows carry the *same* normalized street, because
it keys on the full site_key. It cannot see the same centre written two ways: the
registry files "17435 US HIGHWAY 441" with no unit and the owner's directory files
"17435 US HWY-441 Unit 101", or a cross-brand pair where one brand's row carries a
suite and the other's does not. Those keep distinct site_keys, so the linker wrote
a second clinic and the centre is counted twice.

This is the residue of a directory acquire that staged an owner's own centre list
over the registry rows for the same buildings without collapsing them at building
level (the current acquirer, run_bluesprig_family.py, corroborates at building
level and does not create these; this script cleans up what an earlier run left).

The owner's OWN directory is the authority, which is what makes this safe rather
than a fuzzy merge, and the authority is used at the right grain: NOT "one building
is one clinic", which would wrongly merge two real suites in one office park (the
BlueSprig family genuinely runs two centres at 5601 Arnold Rd, Dublin, one in
Unit 100 and one in Unit 108, and the directory lists both). Instead:

  * A building the directory lists as ONE centre collapses to one row. Any
    suite-spelling difference between the rows there ("Building 100" vs "Suite
    100") is noise, because the owner says there is a single centre.
  * A building the directory lists as TWO OR MORE centres is left alone, except
    for two provably-safe merges: rows with the identical normalized street, and a
    same-owner row that dropped its unit folded into that owner's single suited row
    at the building (a row with no unit cannot be a *second* distinct suite).

So the directory's per-building centre count caps how far the collapse can go, and
two genuinely distinct suites are never merged.

Survivor choice is deterministic: a directory-sourced row beats a bare registry
row; a row that keeps its unit (the more complete address) beats one that dropped
it; then the freshest registration; then the lowest id. Nothing is deleted: the
loser's `superseded_by` points at the survivor and its claim stays on the record.

Idempotent. Usage:
    python scripts/correct_family_building_dupes.py --dry-run
    python scripts/correct_family_building_dupes.py
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from collections import defaultdict
from datetime import date

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_FAMILY = [
    "Blue Sprig",
    "Florida Autism Center",
    "Trumpet Behavioral Health",
    "Therapeutic Pathways",
]
_DIRECTORY_SOURCES = frozenset({"owner_location_directory"})
_BLUESPRIG_INDEX = "https://www.bluesprigautism.com/wp-json/wp/v2/center?per_page=100"
_UNIT_TAIL = re.compile(r"(?:ste|bldg|flr)[a-z0-9-]*$")


def _peel(street_norm: str) -> str:
    while (m := _UNIT_TAIL.search(street_norm)) is not None:
        street_norm = street_norm[: m.start()]
    return street_norm


def _has_unit(street_norm: str) -> bool:
    return street_norm != _peel(street_norm)


def _survivor_sort_key(row: dict) -> tuple[int, int, int, str]:
    from_directory = bool(_DIRECTORY_SOURCES & row["source_types"])
    last_updated = row["registry_last_updated"] or date.min
    return (
        0 if from_directory else 1,
        0 if _has_unit(row["street_norm"]) else 1,
        -last_updated.toordinal(),
        row["id"],
    )


def _load_directory_buildings(conn) -> dict[tuple[str, str], set[str]]:
    """Return building -> set of distinct directory site-streets (one per centre),
    read from the snapshot of the most recent BlueSprig directory source_record."""
    from fundprint.resolve.clinic_link import normalize_street, zip5
    from fundprint.storage import LocalFilesystemStore

    row = conn.execute(
        """
        SELECT snapshot_id FROM source_record
        WHERE source_url = %s AND source_type = 'owner_location_directory'
        ORDER BY fetched_at DESC LIMIT 1
        """,
        (_BLUESPRIG_INDEX,),
    ).fetchone()
    if not row:
        raise SystemExit("no BlueSprig directory snapshot found; run run_bluesprig_family.py first")
    content = LocalFilesystemStore().get(str(row[0]))
    centers = json.loads(content).get("centers", [])
    buildings: dict[tuple[str, str], set[str]] = defaultdict(set)
    for c in centers:
        s = normalize_street(c.get("address_line1"))
        if not s:
            continue
        z = zip5(c.get("zip"))
        buildings[(_peel(s), z)].add(s)
    return buildings


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    from fundprint import db
    from fundprint.resolve.clinic_link import normalize_street, zip5

    conn = db.connect()
    try:
        dir_buildings = _load_directory_buildings(conn)

        rows = conn.execute(
            """
            SELECT c.id, c.address_line1, c.zip, c.registry_last_updated,
                   oe.name AS owner_name,
                   COALESCE(
                     ARRAY(
                       SELECT DISTINCT sr.source_type
                       FROM unnest(c.source_record_ids) AS srid
                       JOIN source_record sr ON sr.id = srid
                     ), '{}'
                   ) AS source_types
            FROM clinic c
            JOIN owner_entity oe ON oe.id = c.owner_entity_id
            WHERE c.superseded_by IS NULL
              AND c.address_line1 IS NOT NULL
              AND oe.name = ANY(%s)
            """,
            (_FAMILY,),
        ).fetchall()

        groups: dict[tuple, list[dict]] = defaultdict(list)
        for cid, addr, zipc, last_updated, owner_name, source_types in rows:
            snorm = normalize_street(addr)
            if not snorm:
                continue
            groups[(_peel(snorm), zip5(zipc))].append(
                {
                    "id": str(cid),
                    "owner_name": owner_name,
                    "address_line1": addr,
                    "street_norm": snorm,
                    "registry_last_updated": last_updated,
                    "source_types": set(source_types or ()),
                }
            )

        pairs: list[tuple[str, str]] = []
        per_brand: dict[str, int] = defaultdict(int)

        def collapse(members: list[dict]) -> None:
            members.sort(key=_survivor_sort_key)
            survivor = members[0]
            logger.info("  KEEP  %-42s [%s]", survivor["address_line1"], survivor["owner_name"])
            for loser in members[1:]:
                logger.info("  DROP  %-42s [%s]", loser["address_line1"], loser["owner_name"])
                pairs.append((loser["id"], survivor["id"]))
                per_brand[loser["owner_name"]] += 1

        for building, members in sorted(groups.items()):
            if len(members) < 2:
                continue
            dir_centers = dir_buildings.get(building)
            if not dir_centers:
                logger.info("  SKIP (building not in directory) %s x%d", building, len(members))
                continue

            if len(dir_centers) == 1:
                # The owner lists one centre here; every row is that centre.
                collapse(members)
                continue

            # Two or more real suites at this building. Only provably-safe merges:
            # identical normalized street, and a same-owner unit-less row folded into
            # that owner's single suited row.
            by_street: dict[str, list[dict]] = defaultdict(list)
            for m in members:
                by_street[m["street_norm"]].append(m)
            for same in by_street.values():
                if len(same) > 1:
                    collapse(same)
            by_owner: dict[str, list[dict]] = defaultdict(list)
            for m in members:
                by_owner[m["owner_name"]].append(m)
            for owner_rows in by_owner.values():
                suited = [m for m in owner_rows if _has_unit(m["street_norm"])]
                unitless = [m for m in owner_rows if not _has_unit(m["street_norm"])]
                if len(suited) == 1 and unitless:
                    collapse([suited[0], *unitless])

        logger.info("%d row(s) to supersede", len(pairs))
        for brand, n in sorted(per_brand.items(), key=lambda kv: -kv[1]):
            logger.info("  %-30s -%d", brand, n)

        if args.dry_run:
            logger.info("dry run; nothing written")
            return 0

        for loser_id, survivor_id in pairs:
            conn.execute(
                "UPDATE clinic SET superseded_by = %s WHERE id = %s",
                (survivor_id, loser_id),
            )
        conn.commit()
        logger.info("superseded %d suite-variant duplicate row(s)", len(pairs))
    except Exception:
        logger.exception("correction failed")
        conn.rollback()
        return 1
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
