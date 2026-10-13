"""One-off dataset correction: retire InBloom clinics filed under a mis-split address.

`parse_inbloom_roster` used to flatten each card to text and peel "City, ST ZIP" off
the end. Where a card has no comma before the city, the city pattern swallowed the
street name:

    421 Windchime Place<br> Colorado Springs, CO 80919
      -> street "421", city "Windchime Place Colorado Springs"

and where the suite sat after the comma, the suite went into the city ("Suite B
Norwich"). The verification sample caught one; the page had eight. The parser now
splits on the card's markup and refuses a card whose locality is not its own line.

A wrong street is a wrong site key, and here it did damage in both directions:

  1. The garbled rows published with addresses that are not addresses.
  2. `correct_directory_contradicted.py` compared the registry against those garbled
     keys, found that "the directory does not list" 421 Windchime Pl, 125 Whiting St,
     5447 E Beaumont Center Blvd, 6973 University Blvd and 6140 W Executive Dr Ste B,
     and quarantined five correctly addressed registry rows for centres InBloom does
     list.

So this script does two things, after the corrected roster has been re-acquired and
linked:

  * Every live InBloom row whose key the corrected directory does not hold is paired
    with the one corrected entry at the same house number and ZIP, and superseded by
    the clinic at that entry's key. Pairing is by construction; the script refuses to
    write anything unless every garbled row pairs with exactly one entry.
  * Where that surviving clinic is a registry row whose only ownership claim was
    quarantined on the garbled evidence, it gets a NEW clinic_to_owner claim citing
    the corrected directory snapshot. The old claim and its quarantine stay on the
    record: they are what we did, and why. Run run_validate.py afterwards so the new
    claim is decided under the ordinary floors.

Then one more consequence of the fix. A corrected street can now carry the unit the
garbled one lost (Semoran's "Building F"), so the directory's row and a registry row
that never had the unit no longer share a key and the centre counts twice. Where the
directory lists that building ONCE, the unit-less row is the same centre and is
superseded by the directory row: the same provably-safe fold that
correct_family_building_dupes.py makes (a row with no unit cannot be a second suite).
A building the directory lists twice is left alone.

Nothing is deleted. Idempotent: a second run finds nothing to do.

Usage:
    python scripts/correct_inbloom_split_addresses.py --dry-run
    python scripts/correct_inbloom_split_addresses.py
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from fundprint import db  # noqa: E402
from fundprint.acquire.roster import INBLOOM_OWNER, INBLOOM_URL  # noqa: E402
from fundprint.resolve.clinic_link import normalize_street, zip5  # noqa: E402

logger = logging.getLogger(__name__)


# Same unit-tail rule as correct_family_building_dupes._peel, on normalize_street output.
_UNIT_TAIL = re.compile(r"(?:ste|bldg|flr)[a-z0-9-]*$")


def _peel(street_norm: str) -> str:
    while (m := _UNIT_TAIL.search(street_norm)) is not None:
        street_norm = street_norm[: m.start()]
    return street_norm


def _fold_unitless(conn, owner_id, entries, dry_run: bool) -> int:
    """Supersede a live unit-less row into the directory's single suited row there."""
    per_building: dict[tuple[str, str], set[str]] = {}
    for a, z in entries:
        st = normalize_street(a)
        per_building.setdefault((_peel(st), zip5(z)), set()).add(st)
    live = conn.execute(
        """
        SELECT id, address_line1, zip FROM clinic
        WHERE owner_entity_id = %s AND superseded_by IS NULL
        """,
        (owner_id,),
    ).fetchall()
    by_key = {(normalize_street(a), zip5(z)): i for i, a, z in live}
    folds = []
    for i, a, z in live:
        st = normalize_street(a)
        if st != _peel(st):
            continue
        streets = per_building.get((st, zip5(z)), set())
        if len(streets) != 1:
            continue
        (suited,) = streets
        survivor = by_key.get((suited, zip5(z)))
        if suited != st and survivor is not None:
            folds.append((i, survivor, a, suited))
    for i, survivor, a, suited in folds:
        logger.info("  fold unit-less %r into directory row %r", a, suited)
        if not dry_run:
            conn.execute("UPDATE clinic SET superseded_by = %s WHERE id = %s", (survivor, i))
    return len(folds)


def _house_number(street: str | None) -> str:
    return (street or "").strip().split(" ", 1)[0].upper()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true", help="Report without writing.")
    args = p.parse_args()

    conn = db.connect()
    try:
        owner_id = conn.execute(
            "SELECT id FROM owner_entity WHERE name = %s", (INBLOOM_OWNER,)
        ).fetchone()[0]

        # The corrected roster is the newest InBloom snapshot. Older snapshots carry
        # the garbled rows and must not count as "listed".
        latest = conn.execute(
            """
            SELECT sr.id FROM source_record sr
            WHERE sr.source_url = %s
            ORDER BY sr.fetched_at DESC LIMIT 1
            """,
            (INBLOOM_URL,),
        ).fetchone()
        if latest is None:
            logger.error("no InBloom snapshot; re-acquire the roster first")
            return 1
        source_id = latest[0]
        entries = conn.execute(
            "SELECT address_line1, zip FROM staging_bacb_provider WHERE source_record_id = %s",
            (source_id,),
        ).fetchall()
        listed = {(normalize_street(a), zip5(z)) for a, z in entries}
        logger.info("corrected InBloom roster: %d centres", len(entries))

        clinics = conn.execute(
            """
            SELECT c.id, c.address_line1, c.city, c.zip, c.npi,
                   c.id IN (SELECT id FROM v_published_clinics) AS published
            FROM clinic c
            WHERE c.owner_entity_id = %s AND c.superseded_by IS NULL
            """,
            (owner_id,),
        ).fetchall()
        by_key = {}
        for c in clinics:
            by_key.setdefault((normalize_street(c[1]), zip5(c[3])), []).append(c)

        # Garbled = a live, published, directory-sourced row (no NPI) whose key the
        # corrected directory does not hold. A registry row off the list is a
        # different question, answered by correct_directory_contradicted.py.
        garbled = [
            c for c in clinics
            if c[5] and c[4] is None
            and (normalize_street(c[1]), zip5(c[3])) not in listed
        ]
        if not garbled:
            logger.info("no garbled InBloom rows")
            n = _fold_unitless(conn, owner_id, entries, args.dry_run)
            if not args.dry_run:
                conn.commit()
            logger.info("%s %d unit-less row(s)", "would fold" if args.dry_run else "folded", n)
            return 0

        pairs, unpaired = [], []
        for g in garbled:
            hits = [
                (a, z) for a, z in entries
                if _house_number(a) == _house_number(g[1]) and zip5(z) == zip5(g[3])
            ]
            if len(hits) != 1:
                unpaired.append((g, f"{len(hits)} directory entries at that number+ZIP"))
                continue
            key = (normalize_street(hits[0][0]), zip5(hits[0][1]))
            twins = by_key.get(key, [])
            if len(twins) != 1:
                unpaired.append((g, f"{len(twins)} live clinics at the corrected key"))
                continue
            pairs.append((g, twins[0], hits[0][0]))

        for g, why in unpaired:
            logger.warning("  UNPAIRED %r, %r: %s", g[1], g[2], why)
        if unpaired:
            logger.error("%d garbled row(s) unpaired; nothing written", len(unpaired))
            return 1

        # A twin the linker just wrote has an undecided claim of its own; only a twin
        # whose every claim is quarantined needs a new one.
        live_claim = {
            r[0] for r in conn.execute(
                """
                SELECT rc.clinic_id FROM resolution_claim rc
                WHERE rc.clinic_id = ANY(%s) AND rc.claim_type = 'clinic_to_owner'
                  AND NOT EXISTS (
                    SELECT 1 FROM validation_run_decision d
                    WHERE d.resolution_claim_id = rc.id AND d.decision = 'quarantined')
                """,
                ([t[0] for _, t, _ in pairs],),
            ).fetchall()
        }
        needs_claim = [t for _, t, _ in pairs if t[0] not in live_claim]
        for g, t, street in pairs:
            logger.info(
                "  %-28s | %-34s -> %s%s",
                g[1], g[2], street,
                "  (+ new claim on quarantined twin)" if t[0] not in live_claim else "",
            )

        if args.dry_run:
            logger.info(
                "dry run: would supersede %d row(s), add %d claim(s)",
                len(pairs), len(needs_claim),
            )
            return 0

        now = datetime.now(UTC)
        snippets = json.dumps({
            "note": (
                "The owner's own centre directory lists this address. An earlier "
                "ownership claim on this row was quarantined because a mis-split "
                "directory address made the directory appear not to list it."
            ),
            "snippets": [INBLOOM_URL],
        })
        for t in needs_claim:
            conn.execute(
                """
                INSERT INTO resolution_claim
                    (claim_type, clinic_id, owner_entity_id, supporting_snippets,
                     llm_flags, source_record_ids, confidence_score, confidence_method,
                     resolver_version, extracted_at)
                VALUES ('clinic_to_owner', %s, %s, %s, '{}', %s, 0.88, 'fuzzy_high',
                        '0.1.0', %s)
                """,
                (t[0], owner_id, snippets, [source_id], now),
            )
            conn.execute(
                """
                UPDATE clinic SET source_record_ids =
                    array_append(source_record_ids, %s)
                WHERE id = %s AND NOT (%s = ANY(source_record_ids))
                """,
                (source_id, t[0], source_id),
            )
        for g, t, _ in pairs:
            conn.execute("UPDATE clinic SET superseded_by = %s WHERE id = %s", (t[0], g[0]))
        _fold_unitless(conn, owner_id, entries, dry_run=False)
        conn.commit()
        logger.info(
            "superseded %d garbled row(s); added %d claim(s). Now run run_validate.py.",
            len(pairs), len(needs_claim),
        )
    except Exception:
        logger.exception("correction failed")
        conn.rollback()
        return 1
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
