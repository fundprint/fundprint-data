"""Record the Blackstone / CARD ownership history as dated acquisition events.

CARD (Center for Autism and Related Disorders) is the canonical cautionary
tale of PE in autism therapy: Blackstone bought it in 2018, it filed for
bankruptcy in 2023 having closed 100+ locations, and its assets were split to
the founder's Pantogran entity and to Audax. Blackstone no longer owns it.

We therefore do NOT link current CARD clinics to Blackstone (that would be
false). Instead we record the *history* as sourced acquisition_event rows so
the dashboard can show the timeline while its current-ownership clinic count
for Blackstone stays correctly at zero.

Deliberate anti-landmine: the CARD owner_entity is named
"Center for Autism and Related Disorders (CARD)". The clinic linker matches a
clinic only when the clinic's normalized name *starts with* the owner brand;
the "(CARD)" suffix makes this brand longer than any real NPPES org name, so a
future "Center for Autism" NPPES pull can never prefix-match it and thus can
never misattribute current CARD clinics to Blackstone.

Idempotent: re-running fetches the same sources (deduped by content hash),
reuses the CARD owner_entity, and skips events already recorded.

Usage:
    python scripts/ingest_card_history.py
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, date, datetime

import httpx

from fundprint import db
from fundprint.acquire.base import _find_existing_source_record, _insert_source_record
from fundprint.resolve.embeddings import embed
from fundprint.resolve.version import RESOLVER_VERSION
from fundprint.storage import LocalFilesystemStore

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

FUNDPRINT_UA = "FundprintBot/0.1 (+mailto:atharva.doke737@gmail.com)"

FIRM_NAME = "Blackstone"
CARD_BRAND = "Center for Autism and Related Disorders (CARD)"

# Public sources documenting the history.
SOURCES = {
    "nbc": "https://www.nbcnews.com/health/health-care/card-blackstone-kids-autism-private-equity-bankruptcy-rcna118544",
    "bhb": "https://bhbusiness.com/2023/07/27/bankruptcy-court-approves-48-5m-sale-of-card-buyers-to-split-up-company/",
}

# Dated, sourced events. Notes state facts; they do not assert that the PE
# owner *caused* the closures - the copy layer holds that line.
EVENTS = [
    {
        "event_type": "acquisition",
        "event_date": date(2018, 1, 1),
        "circa": True,
        "notes": (
            "Blackstone acquired CARD, then the largest U.S. autism-therapy "
            "chain (~265 clinics at its peak)."
        ),
        "source": "nbc",
    },
    {
        "event_type": "bankruptcy",
        "event_date": date(2023, 5, 1),
        "circa": True,
        "notes": (
            "CARD filed for Chapter 11 bankruptcy, having closed 100+ of its "
            "locations."
        ),
        "source": "nbc",
    },
    {
        "event_type": "divestiture",
        "event_date": date(2023, 7, 27),
        "circa": False,
        "notes": (
            "Bankruptcy court approved a $48.5M sale splitting CARD: founder "
            "Doreen Granpeesheh's Pantogran (~112 clinics) and Audax (15 "
            "clinics and 3 schools in Virginia). Blackstone exited."
        ),
        "source": "bhb",
    },
]


def _snapshot_sources(conn, store) -> dict[str, str]:
    """Fetch and snapshot each source URL; return {key: source_record_id}."""
    ids: dict[str, str] = {}
    for key, url in SOURCES.items():
        headers = {"User-Agent": FUNDPRINT_UA}
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            resp = client.get(url, headers=headers)
            resp.raise_for_status()
        content, final_url = resp.content, str(resp.url)
        snapshot_id, content_hash = store.put(content)
        existing = _find_existing_source_record(conn, final_url, content_hash)
        if existing:
            ids[key] = existing
            logger.info("source already stored: %s", key)
            continue
        ids[key] = _insert_source_record(
            conn,
            source_url=final_url,
            snapshot_id=snapshot_id,
            source_type="curated_acquisition_event",
            fetched_at=datetime.now(UTC),
            content_hash=content_hash,
            module_version="0.1.0",
        )
        logger.info("source stored: %s -> %s", key, ids[key])
    return ids


def _firm_id(conn) -> str:
    row = conn.execute(
        "SELECT id FROM parent_pe_firm WHERE lower(name)=lower(%s) "
        "AND superseded_by IS NULL LIMIT 1",
        (FIRM_NAME,),
    ).fetchone()
    if not row:
        raise SystemExit(f"parent_pe_firm {FIRM_NAME!r} not found; ingest it first.")
    return str(row[0])


def _upsert_card_owner(conn, firm_id: str, source_record_id: str) -> str:
    row = conn.execute(
        "SELECT id FROM owner_entity WHERE lower(name)=lower(%s) "
        "AND superseded_by IS NULL LIMIT 1",
        (CARD_BRAND,),
    ).fetchone()
    if row:
        return str(row[0])
    vecs, model = embed([CARD_BRAND])
    owner_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO owner_entity (
            id, name, parent_pe_firm_id,
            name_embedding, name_embedding_model,
            source_record_ids, confidence_score, confidence_method,
            resolver_version, extracted_at
        ) VALUES (%s, %s, %s, %s::vector, %s, %s::uuid[], %s, %s, %s, %s)
        """,
        (
            owner_id,
            CARD_BRAND,
            firm_id,
            "[" + ",".join(str(x) for x in vecs[0]) + "]",
            model,
            [source_record_id],
            0.95,
            "exact_match",
            RESOLVER_VERSION,
            datetime.now(UTC),
        ),
    )
    logger.info("CARD owner_entity created: %s", owner_id)
    return owner_id


def main() -> int:
    store = LocalFilesystemStore()
    with db.transaction() as conn:
        firm_id = _firm_id(conn)
        src = _snapshot_sources(conn, store)
        owner_id = _upsert_card_owner(conn, firm_id, src["nbc"])

        written = skipped = 0
        for ev in EVENTS:
            exists = conn.execute(
                """
                SELECT 1 FROM acquisition_event
                WHERE parent_pe_firm_id=%s AND owner_entity_id=%s
                  AND event_type=%s AND event_date=%s AND superseded_by IS NULL
                LIMIT 1
                """,
                (firm_id, owner_id, ev["event_type"], ev["event_date"]),
            ).fetchone()
            if exists:
                skipped += 1
                continue
            conn.execute(
                """
                INSERT INTO acquisition_event (
                    id, owner_entity_id, parent_pe_firm_id,
                    event_type, event_date, event_date_circa, deal_notes,
                    source_record_ids, confidence_score, confidence_method,
                    resolver_version, extracted_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s::uuid[],%s,%s,%s,%s)
                """,
                (
                    str(uuid.uuid4()),
                    owner_id,
                    firm_id,
                    ev["event_type"],
                    ev["event_date"],
                    ev["circa"],
                    ev["notes"],
                    [src[ev["source"]]],
                    0.95,
                    "exact_match",
                    RESOLVER_VERSION,
                    datetime.now(UTC),
                ),
            )
            written += 1
        logger.info("CARD history: %d events written, %d skipped", written, skipped)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
