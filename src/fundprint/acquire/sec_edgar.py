"""SEC EDGAR Form D fetcher.

Queries the EDGAR full-text search API for recent Form D filings from issuers
in the ABA / autism therapy space. Form D is the SEC's exempt offering notice,
filed when a PE firm raises a new fund or when a portfolio company raises capital.

API endpoint: https://efts.sec.gov/LATEST/search-index
Rate limit: ~10 req/sec per EDGAR documentation; we stay well under.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import date, datetime
from typing import Any

import httpx

from fundprint.acquire.base import Scraper
from fundprint.acquire.registry import register

logger = logging.getLogger(__name__)

EDGAR_SEARCH_URL = "https://efts.sec.gov/LATEST/search-index"

# Issuers whose names or business descriptions contain these terms are candidates
# for ABA-sector ownership tracking. This is a broad net - resolution narrows it.
#
# Each keyword is queried as its own EXACT-PHRASE request: EDGAR full-text search
# does not support Lucene-style `OR` across quoted phrases (it silently
# over-matches, and 500s once a `forms` filter is added), so we fan out one
# request per phrase and union the results.
ABA_KEYWORDS = ["behavior", "autism", "ABA", "applied behavior", "behavioral health"]

FUNDPRINT_UA = "FundprintBot/0.1 (+mailto:atharva.doke737@gmail.com)"

# Form D and its amendment cover exempt PE fundraising and portfolio company rounds
FORM_D_TYPES = ["D", "D/A"]

# EDGAR full-text search returns a fixed 10 hits per page; paginate with `from`.
EDGAR_PAGE_SIZE = 10
# Cap pages per keyword so a broad term can't trigger thousands of requests.
MAX_PAGES_PER_KEYWORD = 10
# EDGAR documents ~10 req/sec; stay well under with a small inter-request pause.
REQUEST_DELAY_SEC = 0.15

# display_names arrive as "ENTITY NAME  (CIK 0001234567)"; strip the CIK suffix.
_CIK_SUFFIX_RE = re.compile(r"\s*\(CIK\s+\d+\)\s*$", re.IGNORECASE)

# EDGAR full-text search only indexes filings from 2001 onward; matching Form D
# filings for these keywords are sparse and mostly historical, so by default we
# search the entire FTS window rather than a rolling recent slice.
EDGAR_FTS_START = date(2001, 1, 1)


@register
class SecEdgarScraper(Scraper):
    """Fetches Form D filings from EDGAR that mention ABA or autism keywords."""

    source_family = "sec_edgar"
    module_version = "0.1.0"

    def __init__(self, store=None, date_from: date | None = None) -> None:
        super().__init__(store)
        # Default to EDGAR's full full-text-search window: matching Form D
        # filings are few and mostly historical, so a rolling recent window
        # misses them. Callers can pass date_from to narrow for incremental runs.
        self._date_from = date_from or EDGAR_FTS_START

    def fetch(self) -> tuple[bytes, str]:
        """Query EDGAR for Form D filings matching each ABA keyword, merged.

        Issues one exact-phrase request per keyword (paginated), unions the
        hits, and de-duplicates by accession number. Returns the merged JSON
        bytes plus a descriptive, reproducible source_url so the snapshot
        records exactly what search produced these rows.
        """
        enddt = date.today().isoformat()
        startdt = self._date_from.isoformat()
        headers = {"User-Agent": FUNDPRINT_UA, "Accept": "application/json"}

        merged: dict[str, dict[str, Any]] = {}  # accession -> hit, deduped
        with httpx.Client(timeout=30.0) as client:
            for keyword in ABA_KEYWORDS:
                for hit in self._fetch_keyword(client, keyword, startdt, enddt, headers):
                    accession = hit.get("_source", {}).get("adsh") or hit.get("_id", "")
                    if accession:
                        merged.setdefault(accession, hit)

        hits = list(merged.values())
        document = {"hits": {"total": {"value": len(hits)}, "hits": hits}}
        content = json.dumps(document).encode()

        # Descriptive provenance: not a verbatim single response (we merge N
        # keyword queries), but a stable, reproducible description of the search.
        source_url = (
            f"{EDGAR_SEARCH_URL}?q=({'|'.join(ABA_KEYWORDS)})"
            f"&forms={','.join(FORM_D_TYPES)}&startdt={startdt}&enddt={enddt}"
        )
        logger.info(
            "EDGAR fetch: %d unique filings across %d keywords",
            len(hits),
            len(ABA_KEYWORDS),
        )
        return content, source_url

    def _fetch_keyword(
        self,
        client: httpx.Client,
        keyword: str,
        startdt: str,
        enddt: str,
        headers: dict[str, str],
    ) -> list[dict[str, Any]]:
        """Return all hits for one keyword, paginating with EDGAR's `from` offset."""
        out: list[dict[str, Any]] = []
        for page in range(MAX_PAGES_PER_KEYWORD):
            params = {
                "q": f'"{keyword}"',
                "forms": ",".join(FORM_D_TYPES),
                "dateRange": "custom",
                "startdt": startdt,
                "enddt": enddt,
                "from": page * EDGAR_PAGE_SIZE,
            }
            resp = client.get(EDGAR_SEARCH_URL, params=params, headers=headers)
            resp.raise_for_status()
            page_hits = resp.json().get("hits", {}).get("hits", [])
            out.extend(page_hits)
            if len(page_hits) < EDGAR_PAGE_SIZE:
                break  # last page
            time.sleep(REQUEST_DELAY_SEC)  # be polite between paged requests
        return out

    def parse(self, content: bytes) -> list[dict[str, Any]]:
        """Parse raw EDGAR JSON response into staging rows."""
        return parse_edgar_json(content)

    def _write_staging(
        self, rows: list[dict[str, Any]], source_record_id: str, conn: Any
    ) -> None:
        for row in rows:
            conn.execute(
                """
                INSERT INTO staging_sec_filing
                    (source_record_id, accession_number, form_type,
                     filer_name, filing_date, issuer_name, issuer_state,
                     amount_raised, raw_json)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (accession_number) DO NOTHING
                """,
                (
                    source_record_id,
                    row["accession_number"],
                    row.get("form_type", "D"),
                    row.get("filer_name"),
                    row.get("filing_date"),
                    row.get("issuer_name"),
                    row.get("issuer_state"),
                    row.get("amount_raised"),
                    json.dumps(row.get("raw_json")) if row.get("raw_json") else None,
                ),
            )


def parse_edgar_json(content: bytes) -> list[dict[str, Any]]:
    """Parse the EDGAR search-index JSON response into staging dicts.

    The EDGAR API returns an Elasticsearch-style response; hits are under
    hits.hits[*]._source. We extract only the fields we need for staging.
    """
    data = json.loads(content)
    hits = data.get("hits", {}).get("hits", [])
    rows = []
    for hit in hits:
        source = hit.get("_source", {})
        row = _extract_filing_row(source, hit.get("_id", ""))
        if row:
            rows.append(row)
    return rows


def _extract_filing_row(source: dict, hit_id: str) -> dict[str, Any] | None:
    """Extract and normalize a single EDGAR filing hit into a staging dict."""
    # EDGAR accession numbers look like "0001234567-24-000001". The live API
    # returns it as `adsh`; `_id` carries it with a ":primary_doc.xml" suffix
    # that must be stripped if we have to fall back to it.
    accession_number = (
        source.get("adsh")
        or source.get("accession_no")
        or source.get("accession_number")
        or hit_id.split(":")[0]
        or ""
    )
    if not accession_number:
        return None

    # Live EDGAR uses `form`; older/fixture data used `form_type`/`file_type`.
    form_type = (
        source.get("form")
        or source.get("form_type")
        or source.get("file_type")
        or "D"
    )

    filer_name = source.get("display_names") or source.get("entity_name") or None
    if isinstance(filer_name, list):
        filer_name = filer_name[0] if filer_name else None
    filer_name = _clean_display_name(filer_name)

    issuer_name = _clean_display_name(source.get("entity_name")) or filer_name

    filing_date_str = source.get("file_date") or source.get("period_of_report")
    filing_date = _parse_date(filing_date_str)

    # State is stored as a two-letter code in inc_states or similar fields
    issuer_state = source.get("inc_states") or source.get("biz_states")
    if isinstance(issuer_state, list):
        issuer_state = issuer_state[0] if issuer_state else None

    return {
        "accession_number": accession_number,
        "form_type": form_type,
        "filer_name": filer_name,
        "filing_date": filing_date,
        "issuer_name": issuer_name,
        "issuer_state": issuer_state,
        "amount_raised": None,  # Form D amount parsing deferred to resolve layer
        "raw_json": source,
    }


def _clean_display_name(name: Any) -> str | None:
    """Strip EDGAR's trailing "(CIK 0001234567)" suffix and surrounding space."""
    if not name or not isinstance(name, str):
        return None
    cleaned = _CIK_SUFFIX_RE.sub("", name).strip()
    return cleaned or None


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return datetime.fromisoformat(str(value).split("T")[0]).date()
    except (ValueError, TypeError):
        return None
