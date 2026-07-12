"""One-off dataset correction: hold ghost clinics out of publication.

A ghost is a clinic that closed but whose provider-registry record never died.
NPPES reports existence-ever, not existence-now: nothing forces an NPI to be
deactivated when a center shuts, so a dead registration keeps reporting status
'A' indefinitely. A closed clinic and an open one are indistinguishable in the
registry.

There is one place a ghost becomes provable: when a *different* tracked chain,
under a *different* parent firm, registers at the same street address. Two
competing chains do not operate out of one suite. One of them left, and the one
that left is the one whose registration is years staler. In every such collision
in the dataset the gap is unambiguous, for example:

    3351 EASTBROOK DR STE 101, FORT COLLINS CO
        Hopebridge (Arsenal)              last touched 2020-03-26
        Action Behavior Centers (Charlesbank)  last touched 2025-12-11

Hopebridge vacated, ABC took the lease, Hopebridge's NPI was never deactivated.

This script quarantines the stale side of each such collision, the same auditable
mechanism used for the Geode out-of-scope correction: a validation run writes a
`quarantined` decision per affected claim, which drops the clinic from every
published view while leaving the claim, and the reason, on the record. Nothing is
deleted, and the claim is not *wrong*: that NPI really is registered to that
owner. What is wrong is publishing it as an operating clinic.

Deliberately conservative. It acts only where:
  * two owners under DIFFERENT parent firms share a street address (a same-firm
    collision is a brand merger at one real center, not a ghost), and
  * the stale side was last touched at least MIN_GAP_YEARS before the fresh side,
    so a near-tie is never resolved by a coin flip.

This catches ghosts only where a competitor we also track happened to move in, so
it is a lower bound on the phenomenon, not a measure of it. The broader staleness
(about a fifth of registry-sourced clinics rest on records untouched for six or
more years) is disclosed rather than silently corrected.

Requires scripts/backfill_registry_freshness.py to have run.

Idempotent: an already-quarantined claim is skipped.

Usage:
    python scripts/correct_stale_ghost_clinics.py --dry-run
    python scripts/correct_stale_ghost_clinics.py
"""

from __future__ import annotations

import argparse
import logging
import sys
import uuid
from collections import defaultdict
from datetime import UTC, datetime

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# A stale record must predate the live one by at least this much to be called a
# ghost. Below this, the two registrations are close enough that which one is
# dead is not established by the timestamps alone.
MIN_GAP_YEARS = 2.0

DECIDING_RULE = "stale_registration:address_reoccupied_by_other_firm"
RUN_NOTES = (
    "Dataset correction: ghost clinics. Where two chains under different parent "
    "firms register at one street address, the chain that left is the one whose "
    "registry record is years staler; its NPI was never deactivated. The stale "
    "side is quarantined so it leaves the published views while the claim stays "
    "on the record."
)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true", help="Report without writing.")
    args = p.parse_args()

    from fundprint import db
    from fundprint.resolve.clinic_link import normalize, zip5
    from fundprint.resolve.version import RESOLVER_VERSION

    conn = db.connect()
    try:
        rows = conn.execute(
            """
            SELECT c.id, c.name, c.address_line1, c.city, c.state, c.zip,
                   c.registry_last_updated,
                   oe.name AS owner_name, p.id AS firm_id, p.name AS firm_name
            FROM clinic c
            JOIN owner_entity oe ON oe.id = c.owner_entity_id
            JOIN parent_pe_firm p ON p.id = oe.parent_pe_firm_id
            WHERE c.superseded_by IS NULL
            """
        ).fetchall()

        groups = defaultdict(list)
        for r in rows:
            (cid, cname, addr, city, state, zc, touched, owner, firm_id, firm) = r
            if normalize(addr):
                groups[(normalize(addr), zip5(zc))].append(
                    {
                        "clinic_id": str(cid),
                        "name": cname,
                        "addr": addr,
                        "city": city,
                        "state": state,
                        "touched": touched,
                        "owner": owner,
                        "firm_id": str(firm_id),
                        "firm": firm,
                    }
                )

        ghosts: list[dict] = []
        for members in groups.values():
            firms = {m["firm_id"] for m in members}
            if len(firms) < 2:
                continue  # same firm: a brand merger at one real center, not a ghost
            dated = [m for m in members if m["touched"]]
            if len(dated) < 2:
                logger.warning(
                    "skipping %s: missing registry_last_updated on one side",
                    members[0]["addr"],
                )
                continue
            dated.sort(key=lambda m: m["touched"])
            stale, live = dated[0], dated[-1]
            if stale["firm_id"] == live["firm_id"]:
                continue
            gap_years = (live["touched"] - stale["touched"]).days / 365.25
            if gap_years < MIN_GAP_YEARS:
                logger.info(
                    "skipping %s: only %.1fy between registrations, too close to call",
                    stale["addr"],
                    gap_years,
                )
                continue
            stale["gap_years"] = gap_years
            stale["displaced_by"] = f"{live['firm']}/{live['owner']}"
            stale["live_touched"] = live["touched"]
            ghosts.append(stale)

        if not ghosts:
            logger.info("no ghost clinics found; nothing to correct")
            return 0

        logger.info("%d ghost clinic(s) identified:", len(ghosts))
        for g in sorted(ghosts, key=lambda g: -g["gap_years"]):
            logger.info(
                "  %s, %s %s", g["addr"], g["city"], g["state"]
            )
            logger.info(
                "     %s/%s last touched %s -> displaced by %s (%s), gap %.1fy",
                g["firm"],
                g["owner"],
                g["touched"],
                g["displaced_by"],
                g["live_touched"],
                g["gap_years"],
            )

        # Claims to quarantine: the clinic_to_owner claim for each ghost clinic.
        to_quarantine: list[tuple[str, str]] = []
        for g in ghosts:
            claims = conn.execute(
                """
                SELECT rc.id FROM resolution_claim rc
                WHERE rc.clinic_id = %s
                  AND rc.claim_type = 'clinic_to_owner'
                  AND NOT EXISTS (
                      SELECT 1 FROM validation_run_decision vrd
                      WHERE vrd.resolution_claim_id = rc.id
                        AND vrd.decision = 'quarantined'
                  )
                """,
                (g["clinic_id"],),
            ).fetchall()
            for (claim_id,) in claims:
                to_quarantine.append((str(claim_id), g["clinic_id"]))

        if not to_quarantine:
            logger.info("all ghost claims already quarantined; no-op")
            return 0

        if args.dry_run:
            logger.info("dry run; would quarantine %d claim(s)", len(to_quarantine))
            return 0

        run_id = uuid.uuid4()
        now = datetime.now(UTC)
        conn.execute(
            """
            INSERT INTO validation_run (
                id, resolver_version, methodology_version, started_at, created_at, notes
            ) VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (str(run_id), RESOLVER_VERSION, "2026.06-floors-v0", now, now, RUN_NOTES),
        )
        for claim_id, _clinic_id in to_quarantine:
            conn.execute(
                """
                INSERT INTO validation_run_decision (
                    id, validation_run_id, resolution_claim_id,
                    decision, trust_level, deciding_rule, decided_at
                ) VALUES (%s, %s, %s, 'quarantined', 'unverified', %s, %s)
                """,
                (str(uuid.uuid4()), str(run_id), claim_id, DECIDING_RULE, now),
            )
        conn.execute(
            """
            UPDATE validation_run
            SET finished_at = %s, gate_passed = TRUE, gate_passed_at = %s,
                claims_evaluated = %s, claims_passed = 0, claims_failed = 0,
                claims_quarantined = %s
            WHERE id = %s
            """,
            (now, now, len(to_quarantine), len(to_quarantine), str(run_id)),
        )
        conn.commit()
        logger.info(
            "quarantined %d claim(s) across %d ghost clinic(s) (run_id=%s)",
            len(to_quarantine),
            len(ghosts),
            run_id,
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
