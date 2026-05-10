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
from datetime import date, datetime
from typing import Any

import httpx

from fundprint.acquire.base import Scraper
from fundprint.acquire.registry import register

logger = logging.getLogger(__name__)

EDGAR_SEARCH_URL = "https://efts.sec.gov/LATEST/search-index"

# Issuers whose names or business descriptions contain these terms are candidates
# for ABA-sector ownership tracking. This is a broad net - resolution narrows it.
ABA_KEYWORDS = ["behavior", "autism", "ABA", "applied behavior", "behavioral health"]

FUNDPRINT_UA = "FundprintBot/0.1 (+mailto:atharva.doke737@gmail.com)"

# Form D and its amendment cover exempt PE fundraising and portfolio company rounds
FORM_D_TYPES = ["D", "D/A"]


@register
class SecEdgarScraper(Scraper):
    """Fetches Form D filings from EDGAR that mention ABA or autism keywords."""

    source_family = "sec_edgar"
    module_version = "0.1.0"

    def __init__(self, store=None, date_from: date | None = None) -> None:
        super().__init__(store)
        # Default to the last 90 days to catch recent fundraising rounds
        self._date_from = date_from or date.today().replace(
            month=max(1, date.today().month - 3)
        )

    def fetch(self) -> tuple[bytes, str]:
        """Query EDGAR for recent Form D filings matching ABA keywords.

        Returns the raw JSON response bytes and the constructed request URL
        so the snapshot faithfully records what was retrieved.
        """
        keyword_query = " OR ".join(f'"{kw}"' for kw in ABA_KEYWORDS)
        params = {
            "q": keyword_query,
            "dateRange": "custom",
            "startdt": self._date_from.isoformat(),
            "enddt": date.today().isoformat(),
            "forms": ",".join(FORM_D_TYPES),
            "_source": "file_date,period_of_report,entity_name,file_num,form_type",
            "hits.hits.total.value": "true",
            "hits.hits._source.file_date": "true",
        }
        headers = {
            "User-Agent": FUNDPRINT_UA,
            "Accept": "application/json",
        }
        with httpx.Client(timeout=30.0) as client:
            resp = client.get(EDGAR_SEARCH_URL, params=params, headers=headers)
            resp.raise_for_status()

        # The source URL logged in source_record includes query params so the
        # exact search can be reproduced from the snapshot metadata alone
        return resp.content, str(resp.url)

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
    # EDGAR accession numbers look like "0001234567-24-000001"
    accession_number = (
        source.get("accession_no")
        or source.get("accession_number")
        or hit_id
        or ""
    )
    if not accession_number:
        return None

    form_type = source.get("form_type") or source.get("file_type") or "D"

    filer_name = source.get("display_names") or source.get("entity_name") or None
    if isinstance(filer_name, list):
        filer_name = filer_name[0] if filer_name else None

    issuer_name = source.get("entity_name") or filer_name

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


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return datetime.fromisoformat(str(value).split("T")[0]).date()
    except (ValueError, TypeError):
        return None
