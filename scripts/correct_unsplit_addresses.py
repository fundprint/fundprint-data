"""One-off dataset correction: re-key clinics whose whole address landed in the street.

A third of Behavioral Innovations' centre pages serve a schema.org PostalAddress with
the entire address crammed into `streetAddress` and no locality fields at all:

    {"@type": "PostalAddress",
     "streetAddress": "1450 League Line Road, Suite 100, Conroe, Texas 77304",
     "addressCountry": "US"}

The roster took that verbatim, so 33 published clinics carried the city, state and ZIP
*inside* address_line1 with the city/state/zip columns null. Three things follow, and
none of them are cosmetic:

  1. `site_key` is built from the street, so these rows could never match the same
     building arriving from the registry or from a later directory pull. They were one
     re-acquire away from being counted twice.
  2. A null state drops the clinic out of `snapshot.states` and off the map. The state
     table summed to 1,672 of 1,705 clinics: Texas was understated by 21 centres,
     Oklahoma and North Carolina by 5 each, Maryland by 1.
  3. A null ZIP drops the clinic out of the market denominator's site key, so it could
     never be counted in the per-state shares either.

`parse_bi_page` now splits the crammed form (and refuses a row it cannot split, because
a wrong street is worse than no street). Re-acquiring produced a correctly keyed twin
for each of the 33. This script retires the malformed original in favour of its twin.

Nothing is deleted. The malformed row's `superseded_by` points at the correctly keyed
row and its resolution_claim stays on the record: the centre really is at that address,
and we really did read it from that page. What was wrong was the key we filed it under.

The pairing is by construction, not by guesswork: the malformed address is run through
the very parser that produced the twin, and the correction refuses to run unless every
malformed row maps to exactly one live twin.

Idempotent: a second run finds no live malformed rows and does nothing.

Usage:
    python scripts/correct_unsplit_addresses.py --dry-run
    python scripts/correct_unsplit_addresses.py
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from fundprint import db  # noqa: E402
from fundprint.acquire.directory import parse_us_address  # noqa: E402
from fundprint.resolve.clinic_link import normalize_street  # noqa: E402

logger = logging.getLogger(__name__)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true", help="Report without writing.")
    args = p.parse_args()

    conn = db.connect()
    try:
        rows = conn.execute(
            """
            SELECT c.id, c.address_line1, c.city, c.zip, oe.name
            FROM clinic c
            JOIN owner_entity oe ON oe.id = c.owner_entity_id
            WHERE c.superseded_by IS NULL
            """
        ).fetchall()

        # A malformed row is one with no city: the acquirer had nowhere to put it, so it
        # is still sitting in the street. A row with a city was split correctly.
        malformed = [r for r in rows if r[2] is None]
        if not malformed:
            logger.info("no live clinics with an unsplit address; nothing to do")
            return 0

        index: dict[tuple[str, str, str], list] = {}
        for r in rows:
            if r[2] is None:
                continue
            index.setdefault((r[4], normalize_street(r[1]), (r[3] or "")[:5]), []).append(r)

        pairs, unpaired = [], []
        for r in malformed:
            parsed = parse_us_address(r[1])
            if parsed is None:
                unpaired.append((r, "address does not parse"))
                continue
            street, _city, _state, zip_code = parsed
            twins = index.get((r[4], normalize_street(street), zip_code[:5]), [])
            if len(twins) == 1:
                pairs.append((r, twins[0]))
            else:
                unpaired.append((r, f"{len(twins)} candidate twins"))

        logger.info("live clinics with an unsplit address: %d", len(malformed))
        logger.info("paired to exactly one correctly keyed twin: %d", len(pairs))
        for r, why in unpaired:
            logger.warning("  UNPAIRED %s (%s): %s", r[1][:60], r[4], why)

        # Refuse a partial correction. Superseding some rows while leaving others live
        # would leave the dataset in a state neither before nor after: the retired ones
        # vanish from the published views while their unretired siblings still carry a
        # wrong key. Re-acquire the owner first, so every malformed row has a twin.
        if unpaired:
            logger.error(
                "%d malformed row(s) have no twin; re-acquire the owner's roster first. "
                "Nothing written.",
                len(unpaired),
            )
            return 1

        per_owner: dict[str, int] = {}
        for r, _ in pairs:
            per_owner[r[4]] = per_owner.get(r[4], 0) + 1
        for owner, n in sorted(per_owner.items(), key=lambda kv: -kv[1]):
            logger.info("  %-30s %d row(s) re-keyed", owner, n)

        if args.dry_run:
            logger.info("dry run; nothing written")
            return 0

        for old, new in pairs:
            conn.execute(
                "UPDATE clinic SET superseded_by = %s WHERE id = %s",
                (new[0], old[0]),
            )
        conn.commit()
        logger.info("superseded %d clinic row(s) carrying an unsplit address", len(pairs))
    except Exception:
        logger.exception("correction failed")
        conn.rollback()
        return 1
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
