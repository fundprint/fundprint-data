"""Tests for the SEC EDGAR scraper. HTTP is mocked with respx."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import httpx
import pytest
import respx

from fundprint.acquire.sec_edgar import (
    ABA_KEYWORDS,
    EDGAR_SEARCH_URL,
    SecEdgarScraper,
    _extract_filing_row,
    _parse_date,
    parse_edgar_json,
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
        expected = "Behavior Health Partners Fund III LP"
        assert by_acc["0009876543-24-000002"]["issuer_name"] == expected

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

    def test_live_edgar_field_shape(self):
        """Real EDGAR hits use adsh/form and a "(CIK ...)" name suffix."""
        source = {
            "adsh": "0001864634-23-000001",
            "form": "D/A",
            "file_type": "D/A",
            "display_names": ["AUTISM IMPACT FUND LP  (CIK 0001864634)"],
            "file_date": "2023-07-14",
            "inc_states": ["DE"],
            "biz_states": ["FL"],
        }
        result = _extract_filing_row(source, "0001864634-23-000001:primary_doc.xml")
        assert result["accession_number"] == "0001864634-23-000001"
        assert result["form_type"] == "D/A"
        assert result["filer_name"] == "AUTISM IMPACT FUND LP"  # CIK suffix stripped
        assert result["issuer_state"] == "DE"

    def test_id_suffix_stripped_when_falling_back(self):
        result = _extract_filing_row({"form": "D"}, "0001234567-24-000001:primary_doc.xml")
        assert result["accession_number"] == "0001234567-24-000001"


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
    def test_fetch_merges_and_dedupes_keyword_results(self):
        """fetch() fans out per keyword and unions+dedupes hits into one document."""
        sample = (FIXTURES_DIR / "sec_edgar_sample.json").read_bytes()
        route = respx.get(EDGAR_SEARCH_URL).mock(
            return_value=httpx.Response(200, content=sample)
        )

        scraper = SecEdgarScraper(date_from=date(2024, 1, 1))
        content, url = scraper.fetch()

        # One exact-phrase request per keyword (each returns <10 hits => no paging)
        assert route.call_count == len(ABA_KEYWORDS)
        # Every request used a single quoted phrase, never the broken OR query
        for call in route.calls:
            assert " OR " not in call.request.url.params["q"]
        # The two sample filings appear once each despite N keyword responses
        rows = parse_edgar_json(content)
        assert {r["accession_number"] for r in rows} == {
            "0001234567-24-000001",
            "0009876543-24-000002",
        }
        assert EDGAR_SEARCH_URL in url

    @respx.mock
    def test_fetch_raises_on_500(self):
        """5xx responses should bubble up so the retry decorator can catch them."""
        respx.get(EDGAR_SEARCH_URL).mock(return_value=httpx.Response(500))
        scraper = SecEdgarScraper(date_from=date(2024, 1, 1))
        with pytest.raises(httpx.HTTPStatusError):
            scraper.fetch()
