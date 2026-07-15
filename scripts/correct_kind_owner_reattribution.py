"""One-off dataset correction: re-attribute Kind Behavioral Health to its lead owner.

Carolina Center for Autism Services (trading as Kind Behavioral Health) was published
as owned by Trilogy Search Partners. That was a search-fund deal with a syndicate of
investors, not a single buyer: Highland Creek, Pacific Lake, Trilogy and WSC & Company
all hold it. Trilogy was named only because its portfolio page was curated first, which
picked one minority co-investor out of four and understated the lead. The methodology
requires a controlling interest, so the owner must be the lead, not whichever investor
happened to publish first.

WSC & Company is the lead: it describes itself as acquiring AND operating its holdings,
carries Kind as an operating company on its own portfolio page (now snapshotted), and is
the firm third-party databases name as the owner. curated.py now names WSC, and the
WSC owner->firm claim has been created from that snapshot by the normal resolver.

This script retires the Trilogy claim in favour of the WSC one. Following the project's
rule, the Trilogy claim is quarantined, never deleted: it stays on the record (Trilogy
really is a co-investor) and simply leaves the published views. A quarantine decision is
permanent and survives later validation runs, so re-validating cannot resurrect it.

It also repoints owner_entity.parent_pe_firm_id at WSC. The published view keys off the
claim, not this column, so this is only to keep the live database identical to what a
rebuild from an empty database would produce (there the owner would be created fresh
under WSC).

No published total changes: the 18 clinics keep their owner entity and their `other`
classification, and were never in the private-equity headline. Only the name of the
financial owner changes, from Trilogy Search Partners to WSC & Company.

Idempotent: a second run finds the Trilogy claim already quarantined and does nothing.

Usage:
    python scripts/correct_kind_owner_reattribution.py --dry-run
    python scripts/correct_kind_owner_reattribution.py
"""

from __future__ import annotations

import argparse
import logging
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from fundprint import db  # noqa: E402

logger = logging.getLogger(__name__)

OWNER_NAME = "Carolina Center for Autism Services"
OLD_FIRM = "Trilogy Search Partners"
NEW_FIRM = "WSC & Company"


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true", help="Report without writing.")
    args = p.parse_args()

    conn = db.connect()
    try:
        new_firm = conn.execute(
            "SELECT id FROM parent_pe_firm WHERE lower(name) = lower(%s) AND superseded_by IS NULL",
            (NEW_FIRM,),
        ).fetchone()
        if new_firm is None:
            logger.error(
                "%s does not exist yet. Run acquire_curated then resolve_portfolio "
                "--firm %r first.",
                NEW_FIRM,
                NEW_FIRM,
            )
            return 1
        new_firm_id = new_firm[0]

        # The live Trilogy claim for this owner, and confirm the WSC claim is in place so
        # we never quarantine the old owner without the new one already present.
        old_claim = conn.execute(
            """
            SELECT rc.id
            FROM resolution_claim rc
            JOIN parent_pe_firm pf ON pf.id = rc.parent_pe_firm_id
            JOIN owner_entity oe ON oe.id = rc.owner_entity_id
            WHERE rc.claim_type = 'owner_to_pe_firm'
              AND rc.superseded_by IS NULL
              AND pf.name = %s AND oe.name = %s
            """,
            (OLD_FIRM, OWNER_NAME),
        ).fetchone()

        new_claim = conn.execute(
            """
            SELECT rc.id, rc.owner_entity_id
            FROM resolution_claim rc
            JOIN parent_pe_firm pf ON pf.id = rc.parent_pe_firm_id
            JOIN owner_entity oe ON oe.id = rc.owner_entity_id
            WHERE rc.claim_type = 'owner_to_pe_firm'
              AND rc.superseded_by IS NULL
              AND pf.name = %s AND oe.name = %s
            """,
            (NEW_FIRM, OWNER_NAME),
        ).fetchone()

        if new_claim is None:
            logger.error(
                "no %s claim for %s; nothing to switch to. Aborting.", NEW_FIRM, OWNER_NAME
            )
            return 1
        owner_entity_id = new_claim[1]

        already_quarantined = False
        if old_claim is not None:
            already_quarantined = (
                conn.execute(
                    """
                    SELECT 1 FROM validation_run_decision
                    WHERE resolution_claim_id = %s AND decision = 'quarantined' LIMIT 1
                    """,
                    (old_claim[0],),
                ).fetchone()
                is not None
            )

        if old_claim is None or already_quarantined:
            logger.info("Trilogy claim already retired; ensuring owner points at %s", NEW_FIRM)
            if not args.dry_run:
                conn.execute(
                    "UPDATE owner_entity SET parent_pe_firm_id = %s WHERE id = %s",
                    (new_firm_id, owner_entity_id),
                )
                conn.commit()
            return 0

        logger.info("will quarantine the %s claim %s for %s", OLD_FIRM, old_claim[0], OWNER_NAME)
        logger.info("will repoint owner_entity %s -> %s", owner_entity_id, NEW_FIRM)
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
                f"Re-attribute {OWNER_NAME} (Kind Behavioral Health) from {OLD_FIRM} to "
                f"{NEW_FIRM}. The deal is a search-fund syndicate; {OLD_FIRM} is one minority "
                f"co-investor and {NEW_FIRM} is the lead that acquires and operates it. The "
                f"controlling-interest bar requires the lead, so the {OLD_FIRM} claim is "
                f"quarantined in favour of the {NEW_FIRM} claim.",
            ),
        )
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
                old_claim[0],
                "co_investor_superseded_by_lead_owner",
                datetime.now(UTC),
            ),
        )
        conn.execute(
            "UPDATE owner_entity SET parent_pe_firm_id = %s WHERE id = %s",
            (new_firm_id, owner_entity_id),
        )
        conn.commit()
        logger.info("quarantined the %s claim and repointed the owner to %s", OLD_FIRM, NEW_FIRM)
    except Exception:
        logger.exception("correction failed")
        conn.rollback()
        return 1
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
