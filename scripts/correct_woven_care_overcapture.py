"""One-off dataset correction: cut Woven Care (Buck Jack) down to its real centres.

Woven Care (formerly The Shandy Clinic) registers every Colorado centre under the
legal name of its search-fund holding company, "BUCK JACK LLC". Matching on that name
pulled 24 registry rows into the dataset, but Woven Care's own location page lists only
12 centres. The extras are the usual registry junk: two rows whose street field is the
literal text "MOUNTAIN VIEW CORE KNOWLEDGE SCHOOL", a Salt Lake City site the company
says is still "opening soon", several closed Colorado Springs / Denver / Pueblo
addresses, and unit-level duplicate registrations of a centre we already hold.

We match the owner's own page on the normalized STREET (unit included, ZIP excluded),
which the registry forces on us for two reasons:
  - Woven Care does not publish a ZIP for its Monument centre, so a ZIP-keyed match
    would drop a real centre; the full street identifies it unambiguously.
  - The registry and the directory spell the same suite differently ("STE 110" vs
    "Suite 110", "PL" vs "Place"); normalize_street folds those to one form.
The unit is kept in the key on purpose: Woven Care lists one centre at 2233 Academy
Place Suite 200, and the registry also carries Suite 201 at the same building, which is
a stale second registration the directory does not list, so it is quarantined.

Unlike Helping Hands, no centres are added here: all 12 of Woven Care's real sites are
already in the dataset as registry rows carrying good ZIPs, so the fix is purely to
quarantine the 12 that its own directory contradicts. Nothing is deleted; each loser
gets a quarantined decision and stays on the record.

The 12 buildings come from the owner's live location page, logged on every run for
audit. Idempotent: a second run finds the junk already quarantined and does nothing.

Usage:
    python scripts/correct_woven_care_overcapture.py --dry-run
    python scripts/correct_woven_care_overcapture.py
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from fundprint import db, fetch  # noqa: E402
from fundprint.acquire.directory import parse_us_address  # noqa: E402
from fundprint.resolve.clinic_link import normalize_street  # noqa: E402

logger = logging.getLogger(__name__)

OWNER_NAME = "Buck Jack"
LOCATION_URL = "https://www.wovencare.com/location"
# Woven Care publishes each centre's address in a maps link styled with this class.
_ADDR_RE = re.compile(r"text-weight-semibold\">([^<]+)</div>")


def _directory_street(address: str) -> str:
    """The normalized street (with unit) of one directory address. parse_us_address
    handles the 11 addresses that carry a ZIP; Monument carries none, so fall back to
    everything before the trailing city and state, which keeps the suite that a
    naive split-on-first-comma would drop."""
    parsed = parse_us_address(address)
    if parsed is not None:
        return normalize_street(parsed[0])
    parts = [p.strip() for p in address.split(",") if p.strip()]
    street = ", ".join(parts[:-2]) if len(parts) > 2 else parts[0]
    return normalize_street(street)


def _directory_streets() -> set[str]:
    html = fetch.get(LOCATION_URL).decode("utf-8", errors="replace")
    raw = [re.sub(r"\s+", " ", m).strip() for m in _ADDR_RE.findall(html)]
    addrs = [a for a in raw if re.search(r"\d", a) and re.search(r"\b(CO|UT|Colorado|Utah)\b", a)]
    seen: list[str] = []
    for a in addrs:
        if a not in seen:
            seen.append(a)
    logger.info("Woven Care location page: %d centre(s)", len(seen))
    return {_directory_street(a) for a in seen}


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true", help="Report without writing.")
    args = p.parse_args()

    listed = _directory_streets()
    if len(listed) < 8:
        logger.error("parsed only %d centres; refusing to run on a partial page", len(listed))
        return 1

    conn = db.connect()
    try:
        rows = conn.execute(
            """
            SELECT c.id, c.name, c.address_line1, c.city, c.state
            FROM v_published_clinics v
            JOIN clinic c ON c.id = v.id
            JOIN owner_entity oe ON oe.id = c.owner_entity_id
            WHERE oe.name = %s AND c.npi IS NOT NULL
            """,
            (OWNER_NAME,),
        ).fetchall()

        contradicted = [r for r in rows if normalize_street(r[2]) not in listed]
        logger.info(
            "%d registry-sourced published clinics; %d not on the owner's page",
            len(rows),
            len(contradicted),
        )
        for r in sorted(contradicted, key=lambda x: (x[4], x[3] or "")):
            logger.info("   quarantine: %s | %s, %s", r[2], r[3], r[4])
        if not contradicted:
            return 0
        if args.dry_run:
            logger.info("dry run; nothing written")
            return 0

        run_id = uuid.uuid4()
        conn.execute(
            """
            INSERT INTO validation_run
                (id, resolver_version, methodology_version, started_at, notes)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                run_id,
                "0.1.0",
                "2026.07-directory-v2",
                datetime.now(UTC),
                f"Quarantine {len(contradicted)} Buck Jack (Woven Care) registry clinics its own "
                f"location page does not list: school-name addresses, a not-yet-open Utah site, "
                f"closed Colorado centres, and unit-level duplicate registrations.",
            ),
        )
        claims = conn.execute(
            """
            SELECT rc.id FROM resolution_claim rc
            WHERE rc.clinic_id = ANY(%s) AND rc.claim_type = 'clinic_to_owner'
            """,
            ([r[0] for r in contradicted],),
        ).fetchall()
        for (claim_id,) in claims:
            conn.execute(
                """
                INSERT INTO validation_run_decision
                    (id, validation_run_id, resolution_claim_id, decision, trust_level,
                     deciding_rule, decided_at)
                VALUES (%s, %s, %s, 'quarantined', 'human_anchored', %s, %s)
                """,
                (
                    uuid.uuid4(),
                    run_id,
                    claim_id,
                    "owner_location_page_does_not_list_this_building",
                    datetime.now(UTC),
                ),
            )
        conn.commit()
        logger.info("quarantined %d claim(s)", len(claims))
    except Exception:
        logger.exception("correction failed")
        conn.rollback()
        return 1
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
