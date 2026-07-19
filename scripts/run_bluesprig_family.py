"""Ingest the BlueSprig family directory: corroborate, and add only net-new sites.

KKR-backed BlueSprig publishes all five of its brands (BlueSprig, Florida Autism
Center, Trumpet Behavioral Health, Therapeutic Pathways, The Behavior Center)
through one WordPress `center` post type. Most of those centres are ALREADY in
the dataset from the federal registry; the directory's value is two-fold and this
script keeps them apart, because conflating them is how a merged chain gets
double-counted:

  * CORROBORATION. A published clinic whose building the owner's own current
    directory lists today is open, at that address, and a real centre. We attach
    the directory source_record to it, at BUILDING level (unit markers peeled) and
    scoped to the whole KKR family, so a site the registry filed without a suite,
    or under the brand it carried before the merger, is still recognised as the
    same centre and credited rather than duplicated.

  * NET-NEW. A directory building that no family clinic sits at yet is the only
    thing that becomes a new clinic. Everything else already exists.

This is why the family is NOT run through the generic roster path: that stages
every row, and the standard linker would mint a second clinic for every registry
row the directory writes with a different suite or brand. See roster.py.

The one genuinely new owner is Therapeutic Pathways (a Trumpet sub-brand whose
centres are not otherwise in the dataset); its ownership is ingested as a curated
KKR fact and it is marked directory_only so its generic name never matches the
registry. The Behavior Center's centres are already published under BlueSprig, so
they corroborate and add no owner.

Idempotent: the source_record is content-hashed, net-new is decided against the
live published set (so a previously-added site is corroborated, not re-added on a
second run), and corroboration only ever appends a source that is absent.

Usage:
    python scripts/run_bluesprig_family.py --dry-run
    python scripts/run_bluesprig_family.py
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from collections import defaultdict
from datetime import UTC, datetime

import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_FAMILY = [
    "Blue Sprig",
    "Florida Autism Center",
    "Trumpet Behavioral Health",
    "Therapeutic Pathways",
]
_TP_OWNER = "Therapeutic Pathways"
_UNIT_TAIL = re.compile(r"(?:ste|bldg|flr)[a-z0-9-]*$")


def _building(street_norm: str, zipc: str) -> tuple[str, str]:
    """The building a normalized street sits in, unit markers peeled (see
    compute_market_share._building). Used only to ask 'is this building already
    published?', never as an identity, so it never relaxes the site key."""
    s = street_norm
    while (m := _UNIT_TAIL.search(s)) is not None:
        s = s[: m.start()]
    return (s, zipc)


def _ensure_therapeutic_pathways(store) -> None:
    """Ingest the curated KKR ownership fact for Therapeutic Pathways and mark the
    owner ABA + directory_only + center_based. Idempotent."""
    from fundprint import db
    from fundprint.acquire.curated import CURATED_ACQUISITIONS, ingest_curated
    from fundprint.resolve.portfolio import resolve_portfolio

    entry = next(e for e in CURATED_ACQUISITIONS if e.portfolio_name == _TP_OWNER)
    ingest_curated([entry], store=store)
    with db.transaction() as conn:
        resolve_portfolio(conn, firm_name="KKR", only_names={_TP_OWNER})
        # is_aba so the linker will promote its roster rows; directory_only so its
        # generic name is never used to name-match the registry; center_based so it
        # is not treated as an in-home owner.
        conn.execute(
            """
            UPDATE owner_entity
            SET is_aba = TRUE, directory_only = TRUE, service_model = 'center_based',
                trade_name = COALESCE(trade_name, %s)
            WHERE lower(name) = lower(%s) AND superseded_by IS NULL
            """,
            (_TP_OWNER, _TP_OWNER),
        )
    logger.info("therapeutic pathways owner ensured (ABA, directory_only, KKR)")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    from fundprint import db
    from fundprint.acquire.base import _find_existing_source_record, _insert_source_record
    from fundprint.acquire.roster import SOURCE_TYPE, fetch_bluesprig, parse_bluesprig_roster
    from fundprint.resolve.clinic_link import link_clinics, normalize_street, zip5
    from fundprint.storage import LocalFilesystemStore

    store = LocalFilesystemStore()

    # 1. Fetch + parse the directory (all five brands, one document).
    with httpx.Client(timeout=60.0, follow_redirects=True) as client:
        content, source_url = fetch_bluesprig(client)
    centers = parse_bluesprig_roster(content)
    by_owner: dict[str, int] = defaultdict(int)
    for c in centers:
        by_owner[c.owner_name] += 1
    logger.info("bluesprig directory: %d centre(s) with an address", len(centers))
    for owner, n in sorted(by_owner.items(), key=lambda kv: -kv[1]):
        logger.info("   %-28s %3d", owner, n)

    # 2. Ensure the one genuinely new owner exists and is KKR-linked.
    if not args.dry_run:
        _ensure_therapeutic_pathways(store)

    # 3. Partition against the live published family footprint, at building level.
    conn = db.connect()
    fam_rows = conn.execute(
        """
        SELECT v.id, cl.address_line1, cl.zip
        FROM v_published_clinics v
        JOIN clinic cl ON cl.id = v.id
        JOIN owner_entity oe ON oe.id = v.owner_entity_id
        WHERE oe.name = ANY(%s)
        """,
        (_FAMILY,),
    ).fetchall()
    fam_by_building: dict[tuple[str, str], list[str]] = defaultdict(list)
    for cid, addr, zc in fam_rows:
        fam_by_building[_building(normalize_street(addr), zip5(zc))].append(str(cid))
    published_buildings = set(fam_by_building)

    net_new = []
    seen_new: set[tuple[str, str]] = set()
    for c in centers:
        b = _building(normalize_street(c.address_line1), zip5(c.zip))
        if b in published_buildings or b in seen_new:
            continue
        seen_new.add(b)
        net_new.append(c)

    logger.info(
        "partition: %d already published (corroborate), %d net-new building(s)",
        len(centers) - len(net_new),
        len(net_new),
    )
    for c in net_new:
        logger.info(
            "   NET-NEW  %-26s %s, %s %s %s",
            c.owner_name, c.address_line1, c.city, c.state, c.zip,
        )

    if args.dry_run:
        logger.info("dry run; nothing written")
        conn.close()
        return 0

    # 4. Snapshot the directory as one content-hashed source_record.
    snapshot_id, content_hash = store.put(content, suffix=".json")
    with db.transaction() as tconn:
        existing = _find_existing_source_record(tconn, source_url, content_hash)
        if existing is not None:
            source_record_id = existing
            logger.info("identical directory already snapshotted; reusing source_record")
        else:
            source_record_id = _insert_source_record(
                tconn,
                source_url=source_url,
                snapshot_id=snapshot_id,
                source_type=SOURCE_TYPE,
                fetched_at=datetime.now(UTC),
                content_hash=content_hash,
                module_version="0.1.0",
            )
        # 5. Stage ONLY the net-new rows; the linker promotes them by site_key.
        for c in net_new:
            tconn.execute(
                """
                INSERT INTO staging_bacb_provider
                    (source_record_id, raw_name, address_line1, city, state, zip, npi)
                VALUES (%s, %s, %s, %s, %s, %s, NULL)
                """,
                (source_record_id, c.owner_name, c.address_line1, c.city, c.state, c.zip),
            )
    conn.close()

    link_summary = link_clinics()
    logger.info("link_clinics: %s", link_summary)

    # 6. Corroborate every family clinic whose building the directory confirms.
    conn = db.connect()
    credited = 0
    for c in centers:
        b = _building(normalize_street(c.address_line1), zip5(c.zip))
        for clinic_id in fam_by_building.get(b, []):
            r = conn.execute(
                """
                UPDATE clinic
                SET source_record_ids = array_append(source_record_ids, %s::uuid)
                WHERE id = %s AND NOT (%s::uuid = ANY(source_record_ids))
                """,
                (source_record_id, clinic_id, source_record_id),
            )
            credited += r.rowcount or 0
    conn.commit()
    conn.close()
    logger.info("corroborated %d family clinic(s) with the directory source", credited)
    logger.info("done; next: validate -> views -> market -> export")
    return 0


if __name__ == "__main__":
    sys.exit(main())
