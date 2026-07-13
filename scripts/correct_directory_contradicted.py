"""Quarantine registry clinics that the owner's own directory contradicts.

When an owner publishes a complete list of its centres, that list is a stronger
statement about what it operates than a provider registration is. The registrant
filed the registration once and nobody ever revokes it; the directory is what the
company tells parents *today*. So where the two disagree about whether a centre
exists, the directory wins.

Action Behavior Centers is the case that forced this. Its directory lists 414
centres. The registry gave us 206 rows, of which 105 are addresses ABC does not
list anywhere:

  * nine in Ohio, three in Virginia, one in Georgia. ABC does not operate in
    those states at all.
  * an *apartment* in Palm Beach Gardens, Florida (9103 Ducale Way Apt 208).
  * its own corporate headquarters, 6300 Bee Caves Rd, filed as a practice
    location on the NPI that carries its 109 real centres as secondaries.
  * 34 in Colorado, where ABC lists 42 centres and the registry gave us 67. The
    surplus is centres that closed, still registered, exactly as the methodology
    says the registry behaves: it reports existence-EVER, never existence-NOW.

Publishing those as clinics overstates the count and puts a wrong dot on the map.

## What this does NOT do

It does not touch a clinic that came FROM the directory, and it does not touch an
owner that has no directory source. It quarantines only registry-sourced rows for
an owner whose directory we hold in full, and only where that directory has no
centre at the same site key.

It quarantines rather than deletes, and it writes a `validation_run` with one
`quarantined` decision per claim, so the claim stays on the record and simply
leaves the published views. The registration is real; what is false is the
inference that a registration is a centre.

## The bar this rests on

That the directory is COMPLETE. ABC's sitemap yields 414 centre pages against a
public claim of "400+ locations", so it is. Do not point this script at a
directory that only lists a region, or it will quarantine real clinics.

Usage:
    python scripts/correct_directory_contradicted.py --owner "Action Behavior Centers" \
        --host actionbehavior.com --dry-run
"""

from __future__ import annotations

import argparse
import logging
import sys
import uuid
from collections import Counter
from datetime import UTC, datetime

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--owner", required=True, help="owner_entity name")
    p.add_argument("--host", required=True, help="directory source host")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    from fundprint import db
    from fundprint.resolve.clinic_link import normalize_street, zip5

    conn = db.connect()

    directory = conn.execute(
        """
        SELECT s.address_line1, s.zip
        FROM staging_bacb_provider s
        JOIN source_record sr ON sr.id = s.source_record_id
        WHERE sr.source_url LIKE %s
        """,
        (f"%{args.host}%",),
    ).fetchall()
    if not directory:
        logger.error("no staged directory rows for %s; refusing to run", args.host)
        conn.close()
        return 1
    listed = {(normalize_street(a), zip5(z)) for a, z in directory}
    logger.info(
        "%s directory: %d centres, %d distinct sites",
        args.owner,
        len(directory),
        len(listed),
    )

    # Live, published clinics of this owner that came from the REGISTRY (they carry
    # an NPI). A directory-sourced row is the owner's own claim and is never in
    # question here.
    rows = conn.execute(
        """
        SELECT cl.id, cl.name, cl.address_line1, cl.city, cl.state, cl.zip, cl.npi
        FROM v_published_clinics v
        JOIN clinic cl ON cl.id = v.id
        JOIN owner_entity oe ON oe.id = cl.owner_entity_id
        WHERE oe.name = %s AND cl.npi IS NOT NULL
        """,
        (args.owner,),
    ).fetchall()

    contradicted = [
        r for r in rows if (normalize_street(r[2]), zip5(r[5])) not in listed
    ]
    logger.info(
        "%d registry-sourced published clinics; %d are not in the directory",
        len(rows),
        len(contradicted),
    )
    if not contradicted:
        conn.close()
        return 0

    by_state = Counter(r[4] for r in contradicted)
    for st, n in by_state.most_common():
        logger.info("   %-3s %3d", st, n)

    if args.dry_run:
        logger.info("dry run; nothing written")
        for r in contradicted[:10]:
            logger.info("   would quarantine: %s | %s, %s", r[2], r[3], r[4])
        conn.close()
        return 0

    run_id = uuid.uuid4()
    conn.execute(
        """
        INSERT INTO validation_run (id, resolver_version, methodology_version, started_at, notes)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (
            run_id,
            "0.1.0",
            "2026.07-directory-v1",
            datetime.now(UTC),
            f"Quarantine {len(contradicted)} registry clinics for {args.owner} that its own "
            f"public directory of centres does not list. The directory is the owner's current "
            f"statement of what it operates; a registration is not.",
        ),
    )

    claims = conn.execute(
        """
        SELECT rc.id, rc.clinic_id FROM resolution_claim rc
        WHERE rc.clinic_id = ANY(%s) AND rc.claim_type = 'clinic_to_owner'
        """,
        ([r[0] for r in contradicted],),
    ).fetchall()

    for claim_id, _clinic_id in claims:
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
                "owner_directory_does_not_list_this_address",
                datetime.now(UTC),
            ),
        )

    conn.commit()
    logger.info("quarantined %d claim(s) across %d clinic(s)", len(claims), len(contradicted))
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
