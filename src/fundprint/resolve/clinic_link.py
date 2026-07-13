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

# Owner brands that are correctly identified but out of scope for this dataset:
# the parent firm is one we track, yet the named entity does not operate ABA or
# autism-therapy clinics. Geode Health, for example, is a KKR-backed outpatient
# mental-health provider whose clinic names prefix-match its brand but are not
# autism therapy. Excluding it here keeps any future linker run from
# re-capturing those out-of-scope clinics. Names are stored normalized (see
# normalize()).
_OUT_OF_SCOPE_BRANDS = frozenset({"geodehealth"})

# Addresses a chain registers with the provider registry that are not clinics:
# corporate headquarters, billing offices, registered agents. Nothing in NPPES
# marks an address as clinical, and a provider may register its head office as
# the practice "LOCATION", so the registry hands these to us looking exactly like
# a center.
#
# The bar for this list is direct evidence, not a heuristic. Both entries below
# are corporate headquarters that the owner's OWN public location directory --
# their own statement of which centers they operate -- does not list. Nothing is
# added here on a hunch.
#
# A tempting heuristic was rejected: "many NPIs at one address means head
# office". It is false. Action Behavior Centers registers three to four NPIs at
# every one of its 35 Colorado centers, and Hopebridge does the same at real
# Florida and Kentucky centers. Multi-NPI registration is a billing practice, not
# an HQ signature, and keying on it would have deleted dozens of real clinics.
#
# Keyed by (normalized owner brand, normalized street).
_ADMIN_ADDRESSES = frozenset({
    # 350 Fifth Avenue is the Empire State Building. Proud Moments registers six
    # NPIs in suite 6115; its own directory of 109 centers does not list it.
    ("proudmoments", "3505thaveste6115"),
    # ACES is headquartered in San Diego. Its own directory of 67 centers does
    # not list this address.
    ("aces2020", "5333missioncenterrdste110"),
})

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize(name: str | None) -> str:
    """Lowercase and strip all non-alphanumeric characters for brand matching."""
    if not name:
        return ""
    return _NON_ALNUM.sub("", name.lower())


def zip5(zipc: str | None) -> str:
    """First five digits of a ZIP, or '' when there aren't five."""
    digits = "".join(ch for ch in str(zipc or "") if ch.isdigit())
    return digits[:5] if len(digits) >= 5 else ""


def site_key(
    owner_id: str,
    address_line1: str | None,
    zipc: str | None,
    city: str | None,
    state: str | None,
) -> tuple[str, str, str]:
    """Identity of one physical service location, for de-duplication.

    A clinic is a physical location, not a billing registration. A chain may hold
    several NPIs at one address -- Action Behavior Centers registers six at 320 E
    1st Ave Ste 101, Broomfield, under two legal-entity name variants -- so an
    NPI is not a location identity and de-duplicating on it counts one center
    many times.

    The key is (owner, normalized street, ZIP5). The street retains its suite, so
    two genuinely distinct clinics in one office park stay distinct. When a row
    carries no street (some directory pages), fall back to (owner, state, city),
    which is what the directory linker used before addresses were available.
    """
    street = normalize(address_line1)
    if street:
        return (owner_id, street, zip5(zipc))
    return (owner_id, f"city:{normalize(city)}", (state or "").strip().upper())


def is_linkable_brand(name: str | None) -> bool:
    """Whether an owner brand may be used for clinic matching.

    Excludes brands that are too short to be distinctive and brands that are
    correctly identified but out of scope for this dataset (see
    _OUT_OF_SCOPE_BRANDS). Pure so it can be tested without a database.
    """
    norm = normalize(name)
    return len(norm) >= _MIN_BRAND_LEN and norm not in _OUT_OF_SCOPE_BRANDS


def is_admin_address(owner_name: str | None, address_line1: str | None) -> bool:
    """Whether this owner/address pair is a head office rather than a clinic.

    See _ADMIN_ADDRESSES. Pure so it can be tested without a database.
    """
    return (normalize(owner_name), normalize(address_line1)) in _ADMIN_ADDRESSES


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
    """Return [(normalized_brand, owner_id)] sorted longest brand first.

    Two filters, and both are load-bearing:

    * ``is_aba`` -- owner_entity also holds every non-ABA company scraped from a
      PE firm's portfolio page (MyEyeDr., Heartland Dental, Del Taco). Against the
      taxonomy-filtered API they never matched anything. Against the bulk registry
      they match thousands of optometry and dental locations. Only ABA owners may
      capture a clinic.
    * ``service_model`` -- in-home owners operate no centers, so every address the
      registry holds for them is administrative. Their ownership chain is
      published separately and is unaffected.
    """
    rows = conn.execute(
        """
        SELECT id, name FROM owner_entity
        WHERE superseded_by IS NULL
          AND is_aba
          AND service_model = 'center_based'
        """
    ).fetchall()
    owners = [
        (normalize(name), str(oid))
        for oid, name in rows
        if is_linkable_brand(name)
    ]
    owners.sort(key=lambda t: len(t[0]), reverse=True)
    return owners


def _load_existing_site_keys(conn: Any) -> set[tuple[str, str, str]]:
    """Return the site_key of every clinic ever promoted, live *or superseded*.

    Covers both directions the same key protects against: a directory center that
    is already present from NPPES, and a second NPI enumeration at an address the
    chain already has a clinic row for.

    Superseded rows are deliberately included. `superseded_by` means one thing in
    this schema -- "this row duplicates that row, which is the same physical site"
    -- so a superseded site_key is a site we already count under another row, and
    re-promoting it would resurrect the duplicate. This is not hypothetical: the
    cross-brand correction (scripts/correct_cross_brand_sites.py) supersedes a site
    registered under a second brand of the same parent, and that row keeps its own
    owner_entity, hence its own site_key. Filtering to live rows here would let the
    very next linker run write it back and silently reverse the correction.

    A clinic held out for being a ghost or out of scope is quarantined instead, via
    a validation decision, and never lands in superseded_by, so this does not
    suppress anything the validator is entitled to reconsider.
    """
    rows = conn.execute(
        """
        SELECT owner_entity_id, address_line1, zip, city, state
        FROM clinic
        WHERE owner_entity_id IS NOT NULL
        """
    ).fetchall()
    return {
        site_key(str(owner_id), addr, zipc, city, state)
        for owner_id, addr, zipc, city, state in rows
    }


def _load_unpromoted_clinics(conn: Any) -> list[dict]:
    """Return every staged provider row; site_key decides what is already a clinic.

    This used to skip rows whose NPI already had a clinic. That guard has to go:
    one NPI can hold many locations. The bulk registry's Practice Location file
    lists a chain's secondary sites under the same NPI as its primary, so an
    NPI-based filter would discard every additional center a chain operates --
    exactly the undercount the bulk file exists to fix.

    Idempotence now rests entirely on site_key, which is the honest identity of a
    clinic anyway (see link_clinics).
    """
    rows = conn.execute(
        """
        SELECT s.id, s.source_record_id, s.raw_name, s.address_line1,
               s.city, s.state, s.zip, s.npi,
               s.registry_status, s.registry_last_updated, s.registry_enumerated_on
        FROM staging_bacb_provider s
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
            "registry_status": r[8],
            "registry_last_updated": r[9],
            "registry_enumerated_on": r[10],
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
            resolver_version, extracted_at,
            registry_status, registry_last_updated, registry_enumerated_on
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s::vector, %s,
            %s::uuid[], %s, %s,
            %s, %s,
            %s, %s, %s
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
            # NULL for directory-sourced centers: they carry no registry record,
            # and are current by construction rather than by timestamp.
            clinic_row.get("registry_status"),
            clinic_row.get("registry_last_updated"),
            clinic_row.get("registry_enumerated_on"),
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
    or long transactions). Idempotent via the existing-clinic site_key guard.
    """
    summary = {"staged_seen": 0, "matched": 0, "clinics_written": 0}

    c = db.connect()
    try:
        owners = _load_owners(c)
        staged = _load_unpromoted_clinics(c)
        site_keys = _load_existing_site_keys(c)
    finally:
        c.close()

    summary["staged_seen"] = len(staged)
    # Brand-match each staged row, then de-duplicate every row -- registry and
    # directory alike -- on its site_key, so one physical center yields one
    # clinic no matter how many NPIs the chain registered there, how many
    # locations one NPI holds, or how many sources listed it.
    #
    # There is deliberately no NPI-based de-duplication. Keying on the NPI is
    # wrong in both directions: several NPIs can share one address (which
    # inflated Action Behavior Centers threefold), and one NPI can hold many
    # addresses (which is how a chain registers its secondary centers in the bulk
    # registry's Practice Location file). The address is the clinic. The NPI is
    # a billing identifier and is not.
    brand_by_owner_id = {oid: brand for brand, oid in owners}
    matched: list[tuple[dict, str]] = []
    for row in staged:
        owner_id = match_owner(row["raw_name"], owners)
        if not owner_id:
            continue
        # A head office is not a clinic, however the registry labels it.
        if is_admin_address(brand_by_owner_id.get(owner_id), row.get("address_line1")):
            continue
        npi = row.get("npi")

        if normalize(row.get("address_line1")) or normalize(row.get("city")):
            key = site_key(
                owner_id,
                row.get("address_line1"),
                row.get("zip"),
                row.get("city"),
                row.get("state"),
            )
        elif npi:
            # No street and no city: unplaceable, but a registry row is still a
            # real record, so keep it keyed by its NPI rather than dropping it.
            key = (owner_id, f"npi:{npi}", "")
        else:
            # A directory row with no location at all carries nothing usable.
            continue

        if key in site_keys:
            continue
        site_keys.add(key)
        matched.append((row, owner_id))
    summary["matched"] = len(matched)
    logger.info("link_clinics: %d staged, %d brand-matched", len(staged), len(matched))

    if not matched:
        return summary
    if dry_run:
        logger.info("dry_run=True, not writing; summary=%s", summary)
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
                logger.warning(
                    "db drop on chunk %d attempt %d, retrying",
                    i // chunk_size + 1,
                    attempt,
                )
                time.sleep(3)
        else:
            logger.error("chunk %d failed after retries", i // chunk_size + 1)

    logger.info("link_clinics complete: %s", summary)
    return summary


# Source type stamped on owner location-directory snapshots (see
# fundprint.acquire.directory). Kept as a literal so this module does not import
# the acquire layer.
_DIRECTORY_SOURCE_TYPE = "owner_location_directory"


def link_directory_owner(
    owner_entity_name: str,
    source_host: str,
    *,
    dry_run: bool = False,
    chunk_size: int = 20,
) -> dict[str, int]:
    """Attach an explicit-owner directory source's staged centers to one owner.

    Some directories (e.g. ACES) list generically-named pages that all belong to
    a single known owner, so the brand-prefix linker cannot attribute them. This
    attaches every center staged from ``source_host`` to ``owner_entity_name``,
    de-duplicating against that owner's existing clinics by (state, city, street)
    so a center already present from NPPES or a previous run is not added twice.
    Idempotent via that key; the resolution method is recorded as ``fuzzy_high``
    (a high-confidence name match), the source as ``owner_location_directory``.
    """
    summary = {"staged_seen": 0, "matched": 0, "clinics_written": 0}

    c = db.connect()
    try:
        row = c.execute(
            "SELECT id FROM owner_entity WHERE name = %s AND superseded_by IS NULL",
            (owner_entity_name,),
        ).fetchone()
        if row is None:
            raise ValueError(f"owner_entity {owner_entity_name!r} not found")
        owner_id = str(row[0])
        # Superseded rows included, for the reason given in _load_existing_site_keys:
        # a superseded site is a duplicate we already count elsewhere, and writing it
        # back from a directory would undo a correction.
        existing = c.execute(
            "SELECT state, city, address_line1, zip FROM clinic "
            "WHERE owner_entity_id = %s",
            (owner_id,),
        ).fetchall()
        keys = {
            site_key(owner_id, ad, zc, ci, s) for s, ci, ad, zc in existing
        }
        staged = c.execute(
            """
            SELECT s.source_record_id, s.raw_name, s.address_line1, s.city,
                   s.state, s.zip
            FROM staging_bacb_provider s
            JOIN source_record sr ON sr.id = s.source_record_id
            WHERE sr.source_type = %s AND sr.source_url LIKE %s
            """,
            (_DIRECTORY_SOURCE_TYPE, f"%{source_host}%"),
        ).fetchall()
    finally:
        c.close()

    summary["staged_seen"] = len(staged)
    # De-duplicate on the same site_key the brand linker uses, so a center is one
    # clinic whether it arrives from this directory or from the NPI registry.
    matched: list[tuple[dict, str]] = []
    for srid, raw_name, addr1, city, state, zc in staged:
        key = site_key(owner_id, addr1, zc, city, state)
        if key in keys:
            continue
        keys.add(key)
        matched.append(
            (
                {
                    "source_record_id": str(srid),
                    "raw_name": raw_name,
                    "address_line1": addr1,
                    "city": city,
                    "state": state,
                    "zip": zc,
                    "npi": None,
                },
                owner_id,
            )
        )
    summary["matched"] = len(matched)
    logger.info(
        "link_directory_owner(%s): %d staged, %d new after dedup",
        owner_entity_name,
        len(staged),
        len(matched),
    )
    if not matched or dry_run:
        return summary

    names = [row["raw_name"] for row, _ in matched]
    vectors, model = embed(names)
    extracted_at = datetime.now(UTC)
    for i in range(0, len(matched), chunk_size):
        batch = list(zip(matched[i : i + chunk_size], vectors[i : i + chunk_size]))
        for attempt in range(1, 6):
            cc = db.connect()
            try:
                for ((row, oid), vec) in batch:
                    _write_clinic_and_claim(
                        cc,
                        clinic_row=row,
                        owner_id=oid,
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
                logger.warning(
                    "db drop on chunk %d attempt %d, retrying",
                    i // chunk_size + 1,
                    attempt,
                )
                time.sleep(3)
        else:
            logger.error("chunk %d failed after retries", i // chunk_size + 1)
    logger.info("link_directory_owner complete: %s", summary)
    return summary
