"""Tests for the SEC EDGAR scraper. HTTP is mocked with respx."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import respx
import httpx

from fundprint.acquire.sec_edgar import (
    SecEdgarScraper,
    parse_edgar_json,
    _extract_filing_row,
    _parse_date,
    EDGAR_SEARCH_URL,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


class TestParseEdgarJson:
    def test_parses_two_filings(self, edgar_json):
        rows = parse_edgar_json(edgar_json)
        assert len(rows) == 2

    def test_accession_numbers(self, edgar_json):
        rows = parse_edgar_json(edgar_json)
        nums = {r["accession_number"] for r in rows}
        assert "0001234567-24-000001" in nums
        assert "0009876543-24-000002" in nums

    def test_issuer_names(self, edgar_json):
        rows = parse_edgar_json(edgar_json)
        by_acc = {r["accession_number"]: r for r in rows}
        assert by_acc["0001234567-24-000001"]["issuer_name"] == "Lighthouse Autism Center LLC"
        assert by_acc["0009876543-24-000002"]["issuer_name"] == "Behavior Health Partners Fund III LP"

    def test_form_types(self, edgar_json):
        rows = parse_edgar_json(edgar_json)
        by_acc = {r["accession_number"]: r for r in rows}
        assert by_acc["0001234567-24-000001"]["form_type"] == "D"
        assert by_acc["0009876543-24-000002"]["form_type"] == "D/A"

    def test_filing_dates(self, edgar_json):
        rows = parse_edgar_json(edgar_json)
        by_acc = {r["accession_number"]: r for r in rows}
        assert by_acc["0001234567-24-000001"]["filing_date"] == date(2024, 3, 15)

    def test_issuer_states(self, edgar_json):
        rows = parse_edgar_json(edgar_json)
        by_acc = {r["accession_number"]: r for r in rows}
        assert by_acc["0001234567-24-000001"]["issuer_state"] == "IN"

    def test_empty_hits(self):
        data = {"hits": {"total": {"value": 0}, "hits": []}}
        rows = parse_edgar_json(json.dumps(data).encode())
        assert rows == []

    def test_raw_json_is_preserved(self, edgar_json):
        rows = parse_edgar_json(edgar_json)
        # raw_json should contain the original _source dict for downstream use
        assert rows[0]["raw_json"] is not None
        assert isinstance(rows[0]["raw_json"], dict)


class TestExtractFilingRow:
    def test_returns_none_without_accession(self):
        result = _extract_filing_row({}, "")
        assert result is None

    def test_uses_hit_id_as_fallback(self):
        result = _extract_filing_row({"form_type": "D"}, "fallback-id")
        assert result["accession_number"] == "fallback-id"

    def test_display_names_list_takes_first(self):
        source = {
            "accession_no": "123",
            "display_names": ["First Corp", "Second Corp"],
        }
        result = _extract_filing_row(source, "")
        assert result["filer_name"] == "First Corp"


class TestParseDate:
    def test_iso_string(self):
        assert _parse_date("2024-03-15") == date(2024, 3, 15)

    def test_datetime_string(self):
        assert _parse_date("2024-03-15T00:00:00Z") == date(2024, 3, 15)

    def test_none_input(self):
        assert _parse_date(None) is None

    def test_invalid_string(self):
        assert _parse_date("not-a-date") is None

    def test_date_passthrough(self):
        d = date(2024, 1, 1)
        assert _parse_date(d) == d


class TestSecEdgarFetch:
    @respx.mock
    def test_fetch_calls_edgar_api(self):
        """fetch() should hit EDGAR_SEARCH_URL and return bytes + URL."""
        sample = (FIXTURES_DIR / "sec_edgar_sample.json").read_bytes()
        respx.get(EDGAR_SEARCH_URL).mock(return_value=httpx.Response(200, content=sample))

        scraper = SecEdgarScraper(date_from=date(2024, 1, 1))
        content, url = scraper.fetch()

        assert content == sample
        assert EDGAR_SEARCH_URL in url

    @respx.mock
    def test_fetch_raises_on_500(self):
        """5xx responses should bubble up so the retry decorator can catch them."""
        respx.get(EDGAR_SEARCH_URL).mock(return_value=httpx.Response(500))
        scraper = SecEdgarScraper(date_from=date(2024, 1, 1))
        with pytest.raises(httpx.HTTPStatusError):
            scraper.fetch()
