"""Deterministic clinic -> owner_entity linker (brand-name match).

Promotes staged provider rows (``staging_bacb_provider``) into ``clinic``
entity rows and links each to an existing ``owner_entity`` when the clinic's
name carries that owner's brand -- e.g. an NPPES org named "BLUESPRIG" or
"GEODE HEALTH OF ARIZONA, P.C." belongs to the "Blue Sprig" / "Geode Health"
owner we resolved from KKR's portfolio.

The match is a normalized name-prefix test (case-, space-, punctuation-
insensitive), not an LLM call, so this stage costs nothing on the Anthropic
budget. A minimum brand length guards against short owner names producing
spurious matches. Once a clinic has a ``clinic_to_owner`` claim and its owner
already has an ``owner_to_pe_firm`` claim, the chain walker can assemble the
full clinic -> owner -> PE-firm chain.
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from datetime import UTC, datetime
from typing import Any

import psycopg

from fundprint import db
from fundprint.resolve.embeddings import embed
from fundprint.resolve.version import RESOLVER_VERSION

logger = logging.getLogger(__name__)

# Brand-name match: high-confidence but not exact, and above the 0.80 floor
# for clinic_to_owner claims.
_CLINIC_CONFIDENCE = 0.88
_CONFIDENCE_METHOD = "fuzzy_high"

# Owners whose normalized brand is shorter than this are not used for matching,
# so a short name like "April" cannot capture unrelated clinics.
_MIN_BRAND_LEN = 6

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize(name: str | None) -> str:
    """Lowercase and strip all non-alphanumeric characters for brand matching."""
    if not name:
        return ""
    return _NON_ALNUM.sub("", name.lower())


def match_owner(
    clinic_name: str,
    owners_by_brand: list[tuple[str, str]],
) -> str | None:
    """Return the owner_entity id whose brand best matches the clinic name.

    *owners_by_brand* is a list of (normalized_brand, owner_id) sorted longest
    brand first. Returns the id of the longest brand that is a prefix of the
    clinic's normalized name, or None when nothing matches.
    """
    norm = normalize(clinic_name)
    if not norm:
        return None
    for brand, owner_id in owners_by_brand:
        if norm.startswith(brand):
            return owner_id
    return None


def _load_owners(conn: Any) -> list[tuple[str, str]]:
    """Return [(normalized_brand, owner_id)] sorted longest brand first."""
    rows = conn.execute(
        "SELECT id, name FROM owner_entity WHERE superseded_by IS NULL"
    ).fetchall()
    owners = [
        (normalize(name), str(oid))
        for oid, name in rows
        if len(normalize(name)) >= _MIN_BRAND_LEN
    ]
    owners.sort(key=lambda t: len(t[0]), reverse=True)
    return owners


def _load_unpromoted_clinics(conn: Any) -> list[dict]:
    """Return staged provider rows that do not yet have a clinic row by NPI."""
    rows = conn.execute(
        """
        SELECT s.id, s.source_record_id, s.raw_name, s.address_line1,
               s.city, s.state, s.zip, s.npi
        FROM staging_bacb_provider s
        WHERE s.npi IS NULL
           OR NOT EXISTS (
               SELECT 1 FROM clinic c
               WHERE c.npi = s.npi AND c.superseded_by IS NULL
           )
        ORDER BY s.raw_name
        """
    ).fetchall()
    return [
        {
            "id": str(r[0]),
            "source_record_id": str(r[1]),
            "raw_name": r[2],
            "address_line1": r[3],
            "city": r[4],
            "state": r[5],
            "zip": r[6],
            "npi": r[7],
        }
        for r in rows
    ]


def _fmt_vec(vec: list[float]) -> str:
    return "[" + ",".join(str(x) for x in vec) + "]"


def _write_clinic_and_claim(
    conn: Any,
    *,
    clinic_row: dict,
    owner_id: str,
    name_vec: list[float],
    embedding_model: str,
    extracted_at: datetime,
) -> None:
    """Insert one clinic entity row and its clinic_to_owner claim."""
    clinic_id = str(uuid.uuid4())
    srid = clinic_row["source_record_id"]
    conn.execute(
        """
        INSERT INTO clinic (
            id, name, name_normalized, address_line1, city, state, zip, npi,
            owner_entity_id, name_embedding, name_embedding_model,
            source_record_ids, confidence_score, confidence_method,
            resolver_version, extracted_at
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s::vector, %s,
            %s::uuid[], %s, %s,
            %s, %s
        )
        """,
        (
            clinic_id,
            clinic_row["raw_name"],
            normalize(clinic_row["raw_name"]),
            clinic_row["address_line1"],
            clinic_row["city"],
            clinic_row["state"],
            clinic_row["zip"],
            clinic_row["npi"],
            owner_id,
            _fmt_vec(name_vec),
            embedding_model,
            [srid],
            _CLINIC_CONFIDENCE,
            _CONFIDENCE_METHOD,
            RESOLVER_VERSION,
            extracted_at,
        ),
    )
    snippets = json.dumps(
        {
            "snippets": [clinic_row["raw_name"]],
            "note": (
                "Provider name carries the owner's brand; matched by normalized "
                "name prefix against an owner_entity resolved from the PE "
                "portfolio."
            ),
        }
    )
    conn.execute(
        """
        INSERT INTO resolution_claim (
            id, claim_type, clinic_id, owner_entity_id,
            supporting_snippets, llm_flags,
            source_record_ids, confidence_score, confidence_method,
            resolver_version, extracted_at
        ) VALUES (
            %s, 'clinic_to_owner', %s, %s,
            %s::jsonb, %s::text[],
            %s::uuid[], %s, %s,
            %s, %s
        )
        """,
        (
            str(uuid.uuid4()),
            clinic_id,
            owner_id,
            snippets,
            [],
            [srid],
            _CLINIC_CONFIDENCE,
            _CONFIDENCE_METHOD,
            RESOLVER_VERSION,
            extracted_at,
        ),
    )


def link_clinics(*, dry_run: bool = False, chunk_size: int = 20) -> dict[str, int]:
    """Promote brand-matched staged providers into clinics + clinic_to_owner claims.

    Self-managing: loads data on one short-lived connection, embeds all matched
    clinic names in a single Voyage call, then writes in small chunks each on a
    fresh connection with retry (the hosted pooler intermittently drops large
    or long transactions). Idempotent via the existing-clinic-by-NPI guard.
    """
    summary = {"staged_seen": 0, "matched": 0, "clinics_written": 0}

    c = db.connect()
    try:
        owners = _load_owners(c)
        staged = _load_unpromoted_clinics(c)
    finally:
        c.close()

    summary["staged_seen"] = len(staged)
    # Brand-match each staged row, de-duplicating by NPI within this run so the
    # exact+wildcard NPPES pulls don't create two clinics for the same NPI.
    matched: list[tuple[dict, str]] = []
    seen_npi: set[str] = set()
    for row in staged:
        owner_id = match_owner(row["raw_name"], owners)
        if not owner_id:
            continue
        npi = row.get("npi")
        if npi and npi in seen_npi:
            continue
        if npi:
            seen_npi.add(npi)
        matched.append((row, owner_id))
    summary["matched"] = len(matched)
    logger.info("link_clinics: %d staged, %d brand-matched", len(staged), len(matched))

    if not matched:
        return summary
    if dry_run:
        logger.info("dry_run=True — not writing; summary=%s", summary)
        return summary

    # One embed call for every matched clinic name (stay under Voyage rate limit).
    names = [row["raw_name"] for row, _ in matched]
    vectors, model = embed(names)
    extracted_at = datetime.now(UTC)

    for i in range(0, len(matched), chunk_size):
        batch = list(zip(matched[i : i + chunk_size], vectors[i : i + chunk_size]))
        for attempt in range(1, 6):
            cc = db.connect()
            try:
                for ((row, owner_id), vec) in batch:
                    _write_clinic_and_claim(
                        cc,
                        clinic_row=row,
                        owner_id=owner_id,
                        name_vec=vec,
                        embedding_model=model,
                        extracted_at=extracted_at,
                    )
                    summary["clinics_written"] += 1
                cc.commit()
                cc.close()
                break
            except (psycopg.OperationalError, psycopg.InterfaceError):
                try:
                    cc.close()
                except Exception:
                    pass
                logger.warning("db drop on chunk %d attempt %d, retrying", i // chunk_size + 1, attempt)
                time.sleep(3)
        else:
            logger.error("chunk %d failed after retries", i // chunk_size + 1)

    logger.info("link_clinics complete: %s", summary)
    return summary
