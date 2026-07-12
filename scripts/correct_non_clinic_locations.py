"""One-off dataset correction: hold non-clinic locations out of publication.

A clinic is a physical service location (methodology section 2). The provider
registry gives us addresses, but nothing in it says an address is *clinical*, so
two kinds of non-clinic have been published as clinics:

1. IN-HOME OWNERS. Some ABA providers operate no centers at all; they deliver
   therapy in the client's home. They still hold NPIs, registered at offices.
   Key Autism Services registers exactly one NPI per state across fourteen
   states, every one an office suite, and publishes 195 "in-home service area"
   pages and zero centers. Butterfly Effects registers at literal apartments
   ("4650 34TH ST APT D") and downtown towers. Neither has a single verifiable
   center, so neither contributes clinics.

   Their OWNERSHIP is untouched and still published: Butterfly Effects really is
   owned by Moran Capital, Key Autism by Cane Investment. Those are true, sourced
   facts, and deleting them would understate private-equity presence in ABA. Both
   firms stay on the map with zero clinics and an honest label.

2. ADMIN ADDRESSES. A head office registered as the practice "LOCATION". Only two
   are asserted, each because the owner's OWN directory of centers omits it:
   Proud Moments in suite 6115 of the Empire State Building, and ACES at its San
   Diego headquarters.

Both are quarantined the same auditable way as the Geode and ghost corrections: a
validation run writes a `quarantined` decision per clinic_to_owner claim, which
drops the clinic from every published view while the claim, and the reason, stay
on the record. Nothing is deleted.

The linker is separately guarded so a future run never recreates these rows (see
_ADMIN_ADDRESSES and the service_model filter in fundprint.resolve.clinic_link).

Idempotent: an already-quarantined claim is skipped.

Usage:
    python scripts/correct_non_clinic_locations.py --dry-run
    python scripts/correct_non_clinic_locations.py
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

# Owners that deliver therapy in the home and operate no centers.
IN_HOME_OWNERS = ["Butterfly Effects", "Key Autism Services"]

RULE_IN_HOME = "not_a_clinic:in_home_provider_operates_no_centers"
RULE_ADMIN = "not_a_clinic:administrative_address"
RUN_NOTES = (
    "Dataset correction: non-clinic locations. A clinic is a physical service "
    "location. In-home providers (Butterfly Effects, Key Autism Services) operate "
    "no centers, so their registry addresses are offices and homes, not clinics; "
    "their ownership chains remain published with a zero clinic count. Two "
    "corporate headquarters registered as practice locations are also held out."
)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true", help="Report without writing.")
    args = p.parse_args()

    from fundprint import db
    from fundprint.resolve.clinic_link import is_admin_address
    from fundprint.resolve.version import RESOLVER_VERSION

    conn = db.connect()
    try:
        # Mark the in-home owners, so the linker never rebuilds their clinics.
        if not args.dry_run:
            for name in IN_HOME_OWNERS:
                conn.execute(
                    "UPDATE owner_entity SET service_model = 'in_home' WHERE name = %s",
                    (name,),
                )

        rows = conn.execute(
            """
            SELECT c.id, oe.name, p.name, c.address_line1, c.city, c.state
            FROM clinic c
            JOIN owner_entity oe ON oe.id = c.owner_entity_id
            JOIN parent_pe_firm p ON p.id = oe.parent_pe_firm_id
            WHERE c.superseded_by IS NULL
            """
        ).fetchall()

        targets: list[tuple[str, str, str, str]] = []  # (clinic_id, owner, firm, rule)
        for cid, owner, firm, addr, city, state in rows:
            if owner in IN_HOME_OWNERS:
                targets.append((str(cid), owner, firm, RULE_IN_HOME))
            elif is_admin_address(owner, addr):
                logger.info("admin address: %s -- %s, %s %s", owner, addr, city, state)
                targets.append((str(cid), owner, firm, RULE_ADMIN))

        if not targets:
            logger.info("no non-clinic locations found; nothing to correct")
            return 0

        by_owner = Counter(o for _, o, _, _ in targets)
        by_firm = Counter(f for _, _, f, _ in targets)
        logger.info("%d clinic row(s) to hold out of publication:", len(targets))
        for owner, n in by_owner.most_common():
            logger.info("   %-24s -%d", owner, n)
        logger.info("by parent firm:")
        for firm, n in by_firm.most_common():
            logger.info("   %-30s -%d", firm, n)

        to_quarantine: list[str] = []
        for clinic_id, _owner, _firm, _rule in targets:
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
                (clinic_id,),
            ).fetchall()
            to_quarantine.extend(str(c[0]) for c in claims)

        if not to_quarantine:
            logger.info("all non-clinic claims already quarantined; no-op")
            if not args.dry_run:
                conn.commit()  # keep the service_model marks
            return 0

        if args.dry_run:
            logger.info("dry run; would quarantine %d claim(s)", len(to_quarantine))
            conn.rollback()
            return 0

        # The rule differs per claim, so carry it through rather than one blanket
        # reason: an auditor should see *why* each row left, not just that it did.
        rule_by_clinic = {cid: rule for cid, _, _, rule in targets}
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
        for claim_id in to_quarantine:
            clinic_id = conn.execute(
                "SELECT clinic_id FROM resolution_claim WHERE id = %s", (claim_id,)
            ).fetchone()[0]
            conn.execute(
                """
                INSERT INTO validation_run_decision (
                    id, validation_run_id, resolution_claim_id,
                    decision, trust_level, deciding_rule, decided_at
                ) VALUES (%s, %s, %s, 'quarantined', 'unverified', %s, %s)
                """,
                (
                    str(uuid.uuid4()),
                    str(run_id),
                    claim_id,
                    rule_by_clinic.get(str(clinic_id), RULE_IN_HOME),
                    now,
                ),
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
            "quarantined %d claim(s) across %d location(s) (run_id=%s)",
            len(to_quarantine),
            len(targets),
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
