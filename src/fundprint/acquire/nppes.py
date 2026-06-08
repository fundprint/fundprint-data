"""NPPES (CMS NPI Registry) provider scraper.

Why this exists: the BACB does not publish a bulk, downloadable directory of
ABA providers. Its public "Certificant Registry" is a one-at-a-time *credential
verification* tool (look up a named individual's status), not a listing, and
bulk use is restricted. The provider/clinic backbone the pipeline needs is
instead sourced from NPPES -- the public CMS National Plan & Provider
Enumeration System -- which exposes a free JSON API and returns ABA provider
*organizations* (clinics/agencies) with NPI, name, and location.

API: https://npiregistry.cms.hhs.gov/api/ (version 2.1, no auth).
Rows land in ``staging_bacb_provider`` (the generic provider staging table);
``source_record.source_type`` is recorded as ``'nppes'`` so provenance stays
honest about where the data actually came from.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx

from fundprint.acquire.base import Scraper
from fundprint.acquire.registry import register

logger = logging.getLogger(__name__)

NPPES_API_URL = "https://npiregistry.cms.hhs.gov/api/"
FUNDPRINT_UA = "FundprintBot/0.1 (+mailto:atharva.doke737@gmail.com)"

# NPPES caps each response at 200 records and the skip offset at 1000
# (so at most 1200 records are reachable for any one query).
NPPES_PAGE_SIZE = 200
NPPES_MAX_SKIP = 1000
REQUEST_DELAY_SEC = 0.2


@register
class NppesScraper(Scraper):
    """Fetches ABA provider organizations from the NPPES NPI registry."""

    source_family = "nppes"
    module_version = "0.1.0"

    def __init__(
        self,
        store=None,
        *,
        taxonomy_description: str = "Behavior Analyst",
        enumeration_type: str = "NPI-2",  # NPI-2 = organizations (clinics)
        max_records: int = 200,
    ) -> None:
        super().__init__(store)
        self._taxonomy = taxonomy_description
        self._enumeration_type = enumeration_type
        self._max_records = max_records

    def fetch(self) -> tuple[bytes, str]:
        """Page through NPPES for the configured taxonomy, merged into one doc.

        Returns the merged JSON bytes plus a stable, reproducible source_url
        describing the query that produced these rows.
        """
        headers = {"User-Agent": FUNDPRINT_UA, "Accept": "application/json"}
        results: list[dict[str, Any]] = []

        with httpx.Client(timeout=30.0) as client:
            skip = 0
            while skip <= NPPES_MAX_SKIP and len(results) < self._max_records:
                params = {
                    "version": "2.1",
                    "taxonomy_description": self._taxonomy,
                    "enumeration_type": self._enumeration_type,
                    "country_code": "US",
                    "limit": NPPES_PAGE_SIZE,
                    "skip": skip,
                }
                resp = client.get(NPPES_API_URL, params=params, headers=headers)
                resp.raise_for_status()
                page = resp.json().get("results", []) or []
                if not page:
                    break
                results.extend(page)
                if len(page) < NPPES_PAGE_SIZE:
                    break  # last page
                skip += NPPES_PAGE_SIZE
                time.sleep(REQUEST_DELAY_SEC)

        results = results[: self._max_records]
        document = {"result_count": len(results), "results": results}
        content = json.dumps(document).encode()
        source_url = (
            f"{NPPES_API_URL}?version=2.1"
            f"&taxonomy_description={self._taxonomy.replace(' ', '+')}"
            f"&enumeration_type={self._enumeration_type}&country_code=US"
        )
        logger.info(
            "NPPES fetch: %d provider records for taxonomy=%r type=%s",
            len(results),
            self._taxonomy,
            self._enumeration_type,
        )
        return content, source_url

    def parse(self, content: bytes) -> list[dict[str, Any]]:
        """Parse the NPPES JSON response into staging rows."""
        return parse_nppes_json(content)

    def _write_staging(
        self, rows: list[dict[str, Any]], source_record_id: str, conn: Any
    ) -> None:
        for row in rows:
            conn.execute(
                """
                INSERT INTO staging_bacb_provider
                    (source_record_id, raw_name, address_line1, city, state, zip,
                     npi, credential_type)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    source_record_id,
                    row.get("raw_name", ""),
                    row.get("address_line1"),
                    row.get("city"),
                    row.get("state"),
                    row.get("zip"),
                    row.get("npi"),
                    row.get("credential_type"),
                ),
            )


def parse_nppes_json(content: bytes) -> list[dict[str, Any]]:
    """Parse an NPPES API response into provider staging dicts.

    De-duplicates by NPI within the batch. Whitespace/shape normalization
    only -- no field interpretation beyond picking the practice LOCATION
    address and the primary taxonomy.
    """
    data = json.loads(content)
    results = data.get("results", []) or []
    rows: list[dict[str, Any]] = []
    seen_npi: set[str] = set()

    for res in results:
        row = _extract_provider_row(res)
        if not row:
            continue
        npi = row.get("npi")
        if npi and npi in seen_npi:
            continue
        if npi:
            seen_npi.add(npi)
        rows.append(row)
    return rows


def _extract_provider_row(res: dict[str, Any]) -> dict[str, Any] | None:
    """Extract a single NPPES result into a staging dict, or None if unusable."""
    basic = res.get("basic", {}) or {}

    # Organization name for NPI-2; first/last for NPI-1 individuals.
    raw_name = (basic.get("organization_name") or "").strip()
    if not raw_name:
        first = (basic.get("first_name") or "").strip()
        last = (basic.get("last_name") or "").strip()
        raw_name = f"{first} {last}".strip()
    if not raw_name:
        return None

    # Prefer the practice LOCATION address over the MAILING address.
    addresses = res.get("addresses", []) or []
    loc = next(
        (a for a in addresses if a.get("address_purpose") == "LOCATION"),
        addresses[0] if addresses else {},
    )
    state = (loc.get("state") or None)
    if state:
        state = state.strip()[:2] or None
    postal = loc.get("postal_code") or None

    # Primary taxonomy describes the credential / provider type.
    taxonomies = res.get("taxonomies", []) or []
    primary = next((t for t in taxonomies if t.get("primary")), taxonomies[0] if taxonomies else {})
    credential_type = (
        primary.get("desc")
        or basic.get("credential")
        or None
    )

    return {
        "raw_name": raw_name,
        "address_line1": (loc.get("address_1") or None),
        "city": (loc.get("city") or None),
        "state": state,
        "zip": postal,
        "npi": str(res.get("number")) if res.get("number") else None,
        "credential_type": credential_type,
    }
