"""Credit a published clinic when the owner's own directory lists its site.

The linker keys a clinic on ``site_key(owner, street, zip)`` and writes each site
once. So when a centre arrives from BOTH the registry and the owner's own
directory, the two collapse to one clinic and only the first-seen source_record
(usually the registry NPI) is kept; the directory row is deduped away. The fact
that the owner lists the site then lives only in staging, invisible to anyone
reading the published dataset.

That fact is the single strongest confidence signal we have: a site the owner's
own directory names today is open, at that address, and a real centre, not a stale
registration. This backfill makes it visible where it belongs, on the clinic, by
APPENDING the directory source_record to any published clinic whose owner and
site_key a staged directory row covers.

It is additive and idempotent: it never removes a source and never touches a
clinic that already carries a directory source, so it cannot reverse a correction
or change a single published count. It only enriches provenance, which is what lets
the dashboard grade a clinic "owner-verified" from the published source URLs alone,
with nothing to reproduce from staging.

Usage:
    python scripts/backfill_directory_corroboration.py --dry-run
    python scripts/backfill_directory_corroboration.py
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_DIRECTORY_SOURCE_TYPE = "owner_location_directory"
_MIN_BRAND_LEN = 6


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    from fundprint import db
    from fundprint.resolve.clinic_link import normalize, normalize_street, zip5

    conn = db.connect()

    owners = conn.execute(
        "SELECT id, name FROM owner_entity WHERE superseded_by IS NULL"
    ).fetchall()
    # Longest normalized brand first, so the most specific owner wins a prefix match,
    # exactly as the brand linker resolves a staged name to an owner.
    onorm = sorted(
        ((normalize(n), str(i)) for i, n in owners), key=lambda x: -len(x[0])
    )

    def owner_of(raw: str) -> str | None:
        r = normalize(raw)
        for brand, oid in onorm:
            if len(brand) >= _MIN_BRAND_LEN and r.startswith(brand):
                return oid
        return None

    # (owner_id, street, zip5) -> a directory source_record that attests the site.
    staged = conn.execute(
        """
        SELECT s.raw_name, s.address_line1, s.zip, s.source_record_id
        FROM staging_bacb_provider s
        JOIN source_record sr ON sr.id = s.source_record_id
        WHERE sr.source_type = %s
        """,
        (_DIRECTORY_SOURCE_TYPE,),
    ).fetchall()
    dirkey: dict[tuple[str, str, str], str] = {}
    for raw, addr, zc, srid in staged:
        oid = owner_of(raw)
        if oid and addr:
            dirkey.setdefault((oid, normalize_street(addr), zip5(zc)), str(srid))

    # Published clinics that do NOT already carry a directory source.
    rows = conn.execute(
        """
        SELECT v.id, v.owner_entity_id, cl.address_line1, cl.zip, cl.state
        FROM v_published_clinics v
        JOIN clinic cl ON cl.id = v.id
        WHERE NOT EXISTS (
            SELECT 1 FROM source_record sr
            WHERE sr.id = ANY(cl.source_record_ids)
              AND sr.source_type = %s
        )
        """,
        (_DIRECTORY_SOURCE_TYPE,),
    ).fetchall()

    to_credit: list[tuple[str, str]] = []  # (clinic_id, directory source_record_id)
    by_state: Counter = Counter()
    for cid, oid, addr, zc, state in rows:
        key = (str(oid), normalize_street(addr), zip5(zc))
        srid = dirkey.get(key)
        if srid:
            to_credit.append((str(cid), srid))
            by_state[state] += 1

    logger.info(
        "%d published clinics carry no directory source; %d are in fact listed "
        "in their owner's directory and will be credited",
        len(rows),
        len(to_credit),
    )
    for st, n in by_state.most_common(12):
        logger.info("   %-3s %3d", st, n)

    if args.dry_run:
        logger.info("dry run; nothing written")
        conn.close()
        return 0

    for clinic_id, srid in to_credit:
        # array_append only if absent keeps the operation idempotent.
        conn.execute(
            """
            UPDATE clinic
            SET source_record_ids = array_append(source_record_ids, %s::uuid)
            WHERE id = %s AND NOT (%s::uuid = ANY(source_record_ids))
            """,
            (srid, clinic_id, srid),
        )
    conn.commit()
    logger.info("credited %d clinic(s) with their owner-directory source", len(to_credit))
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
