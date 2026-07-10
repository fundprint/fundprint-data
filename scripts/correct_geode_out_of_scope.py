"""One-off dataset correction: hold Geode Health out of publication (out of scope).

Geode Health is a KKR-backed outpatient mental-health provider. Its clinic names
prefix-match its brand, so the deterministic linker attached six "GEODE HEALTH
..." registry records to it, and through it to KKR. The links are correctly
identified, but Geode does not operate ABA or autism-therapy clinics, so those
rows are out of scope for this dataset.

This script records that correction the auditable way: it opens a validation run
and writes a `quarantined` decision for each affected claim (the six
clinic_to_owner links and the one owner_to_pe_firm link). Quarantined claims are
excluded from every published view, and the append-only decision rows leave a
dated record of why. Nothing is deleted.

The linker itself is separately guarded against re-capturing Geode on any future
run (see _OUT_OF_SCOPE_BRANDS in fundprint.resolve.clinic_link), so a rebuild
from scratch never produces these rows in the first place. This script only
corrects the already-accumulated database state.

Idempotent: re-running adds no new quarantine decision for a claim that is
already quarantined.

Usage:
    python scripts/correct_geode_out_of_scope.py
"""

from __future__ import annotations

import logging
import sys
import uuid
from datetime import UTC, datetime

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DECIDING_RULE = "out_of_scope:mental_health_not_aba"
RUN_NOTES = (
    "Dataset correction: Geode Health (KKR-backed outpatient mental health) is "
    "out of scope for an ABA / autism-therapy dataset. Its six clinic links and "
    "one owner-to-KKR link are quarantined so they leave the published views "
    "while remaining on the record."
)


def main() -> int:
    from fundprint import db
    from fundprint.resolve.version import RESOLVER_VERSION

    conn = db.connect()
    try:
        owner = conn.execute(
            """
            SELECT oe.id, oe.name
            FROM owner_entity oe
            JOIN parent_pe_firm p ON p.id = oe.parent_pe_firm_id
            WHERE p.name = 'KKR'
              AND lower(oe.name) LIKE 'geode%'
              AND oe.superseded_by IS NULL
            """
        ).fetchone()
        if not owner:
            logger.info("no Geode owner_entity found; nothing to correct")
            return 0
        owner_id, owner_name = owner

        claims = conn.execute(
            "SELECT id, claim_type FROM resolution_claim WHERE owner_entity_id = %s",
            (str(owner_id),),
        ).fetchall()
        if not claims:
            logger.info("no claims reference %s; nothing to correct", owner_name)
            return 0

        # Skip claims already quarantined so the script is idempotent.
        to_quarantine = []
        for claim_id, claim_type in claims:
            already = conn.execute(
                """
                SELECT 1 FROM validation_run_decision
                WHERE resolution_claim_id = %s AND decision = 'quarantined'
                LIMIT 1
                """,
                (str(claim_id),),
            ).fetchone()
            if not already:
                to_quarantine.append((claim_id, claim_type))

        if not to_quarantine:
            logger.info("all %d Geode claims already quarantined; no-op", len(claims))
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

        for claim_id, claim_type in to_quarantine:
            conn.execute(
                """
                INSERT INTO validation_run_decision (
                    id, validation_run_id, resolution_claim_id,
                    decision, trust_level, deciding_rule, decided_at
                ) VALUES (%s, %s, %s, 'quarantined', 'unverified', %s, %s)
                """,
                (str(uuid.uuid4()), str(run_id), str(claim_id), DECIDING_RULE, now),
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
            "quarantined %d claim(s) for %s (run_id=%s)",
            len(to_quarantine),
            owner_name,
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
