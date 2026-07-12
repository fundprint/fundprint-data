"""NPPES bulk dissemination ingester (the fix for the API's ceiling).

The NPPES *API* we have used until now has two structural limits, and both of
them are artifacts of the API rather than of the registry:

1. **The 1,200-record cap.** Any one query can reach at most 1,200 records, so a
   broad taxonomy sweep cannot enumerate a large chain, and we worked around it
   by querying brand by brand.
2. **Primary practice location only.** The API returns the address on the NPI
   record. A chain that operates fifty centers under a handful of NPIs therefore
   looks like a handful of clinics. This was believed to be a hard ceiling on
   coverage. It is not.

CMS publishes the whole registry monthly, free, no login and no data-use
agreement, at https://download.cms.gov/nppes/NPI_Files.html. The monthly archive
contains, alongside the main NPI file:

* ``pl_pfile`` -- the **Practice Location Reference File**, holding every
  *non-primary* practice location for every NPI. This is where a chain's other
  centers actually live, and it is the direct fix for limit 2.
* deactivation columns (``NPI Deactivation Date`` / ``Reactivation Date``) that
  the API does not expose, so a dead NPI can be skipped rather than published as
  a clinic.

The main file is ~11.5GB uncompressed, so it is streamed straight out of the zip
and never extracted to disk.

## Snapshot policy (a deliberate, documented deviation)

Every other acquirer stores the source document byte-for-byte. Storing a 1.1GB
archive per month is not proportionate, so this module snapshots the **filtered
ABA extract** it actually ingests (a few MB), and records in the source_record
both the upstream URL and the SHA-256 of the upstream archive. The extract is
reproducible by anyone: download that archive, verify the hash, apply the filter
below. Provenance is preserved; the blob is just scoped to what we used.

## The over-capture guard

``owner_entity`` holds every company scraped from a PE firm's portfolio page, not
only the autism ones: KKR's portfolio yields MyEyeDr., Heartland Dental, Medline,
Del Taco. Against the taxonomy-filtered API those brands never matched anything.
Against the bulk registry they match thousands of optometry and dental locations,
and pointing the linker at this file without a guard attributes ~5,000 non-ABA
locations to PE firms as "clinics". Only owners flagged ``is_aba`` are used for
matching (see the migration and ``resolve.clinic_link._load_owners``).

Usage:
    python scripts/run_acquire_nppes_bulk.py --dry-run
    python scripts/run_acquire_nppes_bulk.py
    python scripts/run_acquire_nppes_bulk.py --archive /path/to/monthly.zip
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import re
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from fundprint import db
from fundprint.acquire.base import _find_existing_source_record, _insert_source_record
from fundprint.storage import LocalFilesystemStore, SnapshotStore

logger = logging.getLogger(__name__)

FUNDPRINT_UA = "FundprintBot/0.1 (+mailto:atharva.doke737@gmail.com)"
NPPES_FILES_PAGE = "https://download.cms.gov/nppes/NPI_Files.html"
NPPES_BASE = "https://download.cms.gov/nppes/"

SOURCE_TYPE = "nppes_bulk"
MODULE_VERSION = "0.1.0"

# Organizations, not individual practitioners. A clinic is an organization.
ENTITY_TYPE_ORG = "2"

_MONTHLY_RE = re.compile(
    r'(NPPES_Data_Dissemination_[A-Z][a-z]+_\d{4}_V2\.zip)', re.I
)


@dataclass
class BulkRow:
    """One staged provider row extracted from the bulk registry."""

    npi: str
    raw_name: str
    address_line1: str | None
    city: str | None
    state: str | None
    zip: str | None
    credential_type: str | None = None
    registry_status: str | None = None
    registry_last_updated: str | None = None
    registry_enumerated_on: str | None = None
    # 'primary' from the NPI record, 'secondary' from the Practice Location file.
    location_kind: str = "primary"


@dataclass
class ExtractResult:
    """What a run of the extractor found."""

    rows: list[BulkRow] = field(default_factory=list)
    npis_scanned: int = 0
    orgs_scanned: int = 0
    deactivated_skipped: int = 0
    matched_npis: int = 0
    secondary_locations: int = 0


def resolve_monthly_url(client: httpx.Client | None = None) -> str:
    """Find the current monthly archive URL from the CMS file listing."""
    own = client is None
    client = client or httpx.Client(timeout=60.0, follow_redirects=True)
    try:
        resp = client.get(NPPES_FILES_PAGE, headers={"User-Agent": FUNDPRINT_UA})
        resp.raise_for_status()
        names = _MONTHLY_RE.findall(resp.text)
        if not names:
            raise RuntimeError(f"no monthly archive linked from {NPPES_FILES_PAGE}")
        # The page lists the current month first; take the first match.
        return NPPES_BASE + names[0]
    finally:
        if own:
            client.close()


def download_archive(url: str, dest: Path) -> str:
    """Stream the archive to *dest*. Returns its SHA-256. Skips an existing copy."""
    if dest.exists():
        logger.info("archive already present at %s, not re-downloading", dest)
        return _sha256_file(dest)

    dest.parent.mkdir(parents=True, exist_ok=True)
    logger.info("downloading %s -> %s (this is ~1.1GB)", url, dest)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with httpx.stream(
        "GET", url, headers={"User-Agent": FUNDPRINT_UA}, timeout=None, follow_redirects=True
    ) as r:
        r.raise_for_status()
        with tmp.open("wb") as f:
            for chunk in r.iter_bytes(chunk_size=1 << 20):
                f.write(chunk)
    tmp.replace(dest)
    return _sha256_file(dest)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _member(z: zipfile.ZipFile, prefix: str) -> str:
    """Return the archive member whose basename starts with *prefix*."""
    for n in z.namelist():
        base = n.rsplit("/", 1)[-1]
        if base.startswith(prefix) and base.endswith(".csv") and "fileheader" not in base:
            return n
    raise RuntimeError(f"no {prefix}* member in archive; members: {z.namelist()}")


def _rows(z: zipfile.ZipFile, member: str) -> Iterator[tuple[dict[str, int], list[str]]]:
    """Stream a CSV member out of the zip without extracting it."""
    with z.open(member) as f:
        reader = csv.reader(io.TextIOWrapper(f, encoding="utf-8", errors="replace"))
        header = next(reader)
        index = {c.strip(): i for i, c in enumerate(header)}
        taxonomy_cols = [
            i
            for c, i in index.items()
            if c.startswith("Healthcare Provider Taxonomy Code_")
        ]
        index["__taxonomy_cols__"] = taxonomy_cols  # type: ignore[assignment]
        for row in reader:
            yield index, row


def _iso(value: str | None) -> str | None:
    """NPPES bulk dates are MM/DD/YYYY. Return ISO, or None."""
    if not value:
        return None
    v = value.strip()
    if not v:
        return None
    parts = v.split("/")
    if len(parts) == 3 and len(parts[2]) == 4:
        return f"{parts[2]}-{parts[0].zfill(2)}-{parts[1].zfill(2)}"
    return v[:10] if len(v) >= 10 and v[4] == "-" else None


def extract(archive: Path, brands: list[tuple[str, str]]) -> ExtractResult:
    """Extract every location of every tracked ABA brand from the bulk archive.

    *brands* is [(normalized_brand, display_name)] as the clinic linker uses. Only
    ABA brands may be passed; see the over-capture guard in the module docstring.
    """
    from fundprint.resolve.clinic_link import match_owner, normalize

    result = ExtractResult()
    z = zipfile.ZipFile(archive)
    main = _member(z, "npidata_pfile")
    ploc = _member(z, "pl_pfile")

    # Pass 1: the NPI records themselves -> tracked-brand organizations.
    npi_to_row: dict[str, BulkRow] = {}
    for index, row in _rows(z, main):
        result.npis_scanned += 1
        if row[index["Entity Type Code"]] != ENTITY_TYPE_ORG:
            continue
        result.orgs_scanned += 1
        if row[index["NPI Deactivation Date"]].strip():
            # A dead NPI is not a clinic. The API never told us this.
            result.deactivated_skipped += 1
            continue

        legal = row[index["Provider Organization Name (Legal Business Name)"]]
        other = row[index["Provider Other Organization Name"]]
        if not (match_owner(legal, brands) or match_owner(other, brands)):
            continue

        npi = row[index["NPI"]]
        taxonomy = ""
        for col in index["__taxonomy_cols__"]:  # type: ignore[index]
            if row[col]:
                taxonomy = row[col]
                break

        bulk_row = BulkRow(
            npi=npi,
            raw_name=legal.strip(),
            address_line1=(
                row[index["Provider First Line Business Practice Location Address"]] or None
            ),
            city=row[index["Provider Business Practice Location Address City Name"]] or None,
            state=(row[index["Provider Business Practice Location Address State Name"]] or "")[:2]
            or None,
            zip=row[index["Provider Business Practice Location Address Postal Code"]] or None,
            credential_type=taxonomy or None,
            registry_status="A",  # not deactivated, checked above
            registry_last_updated=_iso(row[index["Last Update Date"]]),
            registry_enumerated_on=_iso(row[index.get("Provider Enumeration Date", 0)])
            if "Provider Enumeration Date" in index
            else None,
            location_kind="primary",
        )
        npi_to_row[npi] = bulk_row
        if normalize(bulk_row.address_line1):
            result.rows.append(bulk_row)

    result.matched_npis = len(npi_to_row)
    logger.info(
        "main file: %d NPIs scanned, %d organizations, %d deactivated skipped, "
        "%d matched a tracked ABA brand",
        result.npis_scanned,
        result.orgs_scanned,
        result.deactivated_skipped,
        result.matched_npis,
    )

    # Pass 2: the Practice Location file -> every OTHER center those NPIs run.
    for index, row in _rows(z, ploc):
        parent = npi_to_row.get(row[index["NPI"]])
        if parent is None:
            continue
        street = row[index["Provider Secondary Practice Location Address- Address Line 1"]]
        if not normalize(street):
            continue
        result.secondary_locations += 1
        result.rows.append(
            BulkRow(
                npi=parent.npi,
                raw_name=parent.raw_name,
                address_line1=street or None,
                city=row[index["Provider Secondary Practice Location Address - City Name"]] or None,
                state=(
                    row[index["Provider Secondary Practice Location Address - State Name"]] or ""
                )[:2]
                or None,
                zip=row[
                    index["Provider Secondary Practice Location Address - Postal Code"]
                ]
                or None,
                credential_type=parent.credential_type,
                registry_status=parent.registry_status,
                registry_last_updated=parent.registry_last_updated,
                registry_enumerated_on=parent.registry_enumerated_on,
                location_kind="secondary",
            )
        )

    logger.info(
        "practice-location file: %d secondary location(s) for tracked brands",
        result.secondary_locations,
    )
    return result


def _load_aba_brands(conn: Any) -> list[tuple[str, str]]:
    """Return [(normalized_brand, display_name)], longest first. ABA owners only.

    Excludes ``directory_only`` owners. Some real ABA brands have names generic
    enough that unrelated organizations in the national registry begin the same
    way ("Behavioral Concepts", "SPARKS ABA"), and the linker matches by name
    prefix, so using them here would attribute other companies' clinics to their
    parent firm. Those owners are linked from their own published roster instead,
    where nothing has to be inferred.
    """
    from fundprint.resolve.clinic_link import is_linkable_brand, normalize

    rows = conn.execute(
        """
        SELECT name FROM owner_entity
        WHERE superseded_by IS NULL
          AND is_aba
          AND service_model = 'center_based'
          AND NOT directory_only
        """
    ).fetchall()
    brands = [(normalize(n), n) for (n,) in rows if is_linkable_brand(n)]
    brands.sort(key=lambda t: len(t[0]), reverse=True)
    return brands


def run(
    archive: Path | None = None,
    *,
    store: SnapshotStore | None = None,
    dry_run: bool = False,
    work_dir: Path | None = None,
) -> ExtractResult:
    """Download (if needed), extract, snapshot, and stage the bulk registry."""
    store = store or LocalFilesystemStore()
    work_dir = work_dir or Path("./.cache/nppes")

    url = resolve_monthly_url()
    if archive is None:
        archive = work_dir / url.rsplit("/", 1)[-1]
        upstream_hash = download_archive(url, archive)
    else:
        upstream_hash = _sha256_file(archive)
    logger.info("archive %s sha256=%s", archive.name, upstream_hash[:16])

    conn = db.connect()
    try:
        brands = _load_aba_brands(conn)
        logger.info("matching against %d ABA brand(s)", len(brands))
        if not brands:
            raise RuntimeError(
                "no ABA owner brands found. Run the resolvers first, and check "
                "owner_entity.is_aba -- without it this module would match every "
                "non-ABA portfolio company in the registry."
            )

        result = extract(archive, brands)

        # The snapshot is the filtered extract we actually ingest, not the 1.1GB
        # archive. The upstream URL and hash below make it reproducible.
        extract_doc = {
            "source": url,
            "upstream_sha256": upstream_hash,
            "module_version": MODULE_VERSION,
            "extracted_at": datetime.now(UTC).isoformat(),
            "filter": {
                "entity_type": ENTITY_TYPE_ORG,
                "deactivated": "excluded",
                "brands": [b for _, b in brands],
            },
            "counts": {
                "npis_scanned": result.npis_scanned,
                "matched_npis": result.matched_npis,
                "secondary_locations": result.secondary_locations,
                "rows": len(result.rows),
            },
            "rows": [vars(r) for r in result.rows],
        }
        content = json.dumps(extract_doc, ensure_ascii=False).encode()

        if dry_run:
            logger.info(
                "dry run: %d row(s) extracted (%d primary, %d secondary); nothing written",
                len(result.rows),
                len(result.rows) - result.secondary_locations,
                result.secondary_locations,
            )
            return result

        snapshot_id, content_hash = store.put(content, suffix=".json")
        if _find_existing_source_record(conn, url, content_hash) is not None:
            logger.info("identical extract already staged for %s; skipping", url)
            return result

        source_record_id = _insert_source_record(
            conn,
            source_url=url,
            snapshot_id=snapshot_id,
            source_type=SOURCE_TYPE,
            fetched_at=datetime.now(UTC),
            content_hash=content_hash,
            module_version=MODULE_VERSION,
        )
        for r in result.rows:
            conn.execute(
                """
                INSERT INTO staging_bacb_provider
                    (source_record_id, raw_name, address_line1, city, state, zip,
                     npi, credential_type,
                     registry_status, registry_last_updated, registry_enumerated_on)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::date, %s::date)
                """,
                (
                    source_record_id,
                    r.raw_name,
                    r.address_line1,
                    r.city,
                    r.state,
                    r.zip,
                    r.npi,
                    r.credential_type,
                    r.registry_status,
                    r.registry_last_updated,
                    r.registry_enumerated_on,
                ),
            )
        conn.commit()
        logger.info(
            "staged %d row(s) from the bulk registry (%d secondary locations)",
            len(result.rows),
            result.secondary_locations,
        )
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return result
