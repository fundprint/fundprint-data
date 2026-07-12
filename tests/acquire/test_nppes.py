"""Tests for the NPPES scraper. HTTP is mocked with respx."""

from __future__ import annotations

import json

import httpx
import respx

from fundprint.acquire.nppes import (
    NPPES_API_URL,
    NppesScraper,
    _extract_provider_row,
    parse_nppes_json,
)

# A minimal NPPES-shaped response: one organization, one individual.
_SAMPLE = {
    "result_count": 2,
    "results": [
        {
            "number": 1588030589,
            "basic": {"organization_name": "1 OF A KIND ABA AND SPEECH THERAPY"},
            "addresses": [
                {
                    "address_purpose": "MAILING",
                    "address_1": "PO BOX 1",
                    "city": "MAILTOWN",
                    "state": "NJ",
                    "postal_code": "00000",
                },
                {
                    "address_purpose": "LOCATION",
                    "address_1": "123 MAIN ST",
                    "city": "PISCATAWAY",
                    "state": "NJ",
                    "postal_code": "088544033",
                },
            ],
            "taxonomies": [
                {"desc": "Behavior Analyst", "code": "103K00000X", "primary": True}
            ],
        },
        {
            "number": 1356713762,
            "basic": {
                "first_name": "MELISSA",
                "last_name": "MOLLOY",
                "credential": "BCBA",
            },
            "addresses": [
                {
                    "address_purpose": "LOCATION",
                    "address_1": "5 ELM AVE",
                    "city": "MASSAPEQUA",
                    "state": "NY",
                    "postal_code": "11758",
                }
            ],
            "taxonomies": [{"desc": "Behavior Analyst", "primary": True}],
        },
    ],
}


def _sample_bytes() -> bytes:
    return json.dumps(_SAMPLE).encode()


class TestParseNppesJson:
    def test_parses_two_providers(self):
        rows = parse_nppes_json(_sample_bytes())
        assert len(rows) == 2

    def test_organization_name(self):
        rows = parse_nppes_json(_sample_bytes())
        by_npi = {r["npi"]: r for r in rows}
        assert by_npi["1588030589"]["raw_name"] == "1 OF A KIND ABA AND SPEECH THERAPY"

    def test_individual_name_falls_back_to_first_last(self):
        rows = parse_nppes_json(_sample_bytes())
        by_npi = {r["npi"]: r for r in rows}
        assert by_npi["1356713762"]["raw_name"] == "MELISSA MOLLOY"

    def test_location_address_preferred_over_mailing(self):
        rows = parse_nppes_json(_sample_bytes())
        by_npi = {r["npi"]: r for r in rows}
        org = by_npi["1588030589"]
        assert org["city"] == "PISCATAWAY"
        assert org["address_line1"] == "123 MAIN ST"

    def test_state_truncated_to_two_chars(self):
        rows = parse_nppes_json(_sample_bytes())
        assert all(r["state"] is None or len(r["state"]) <= 2 for r in rows)

    def test_credential_from_primary_taxonomy(self):
        rows = parse_nppes_json(_sample_bytes())
        by_npi = {r["npi"]: r for r in rows}
        assert by_npi["1588030589"]["credential_type"] == "Behavior Analyst"

    def test_dedupes_by_npi(self):
        doubled = {"results": _SAMPLE["results"] + _SAMPLE["results"]}
        rows = parse_nppes_json(json.dumps(doubled).encode())
        assert len(rows) == 2

    def test_empty_results(self):
        rows = parse_nppes_json(json.dumps({"results": []}).encode())
        assert rows == []


class TestRegistryFreshness:
    """NPPES never marks a closed clinic closed, so how stale a record is, is the
    only liveness signal it gives. These fields were dropped by module 0.1.0."""

    def _row(self, basic: dict) -> dict:
        return _extract_provider_row(
            {
                "number": 1,
                "basic": {"organization_name": "ACME ABA", **basic},
                "addresses": [
                    {"address_purpose": "LOCATION", "address_1": "1 A ST", "state": "TX"}
                ],
            }
        )

    def test_captures_status_and_dates(self):
        row = self._row(
            {
                "status": "A",
                "last_updated": "2024-11-05",
                "enumeration_date": "2016-05-20",
            }
        )
        assert row["registry_status"] == "A"
        assert row["registry_last_updated"] == "2024-11-05"
        assert row["registry_enumerated_on"] == "2016-05-20"

    def test_takes_the_later_of_last_updated_and_certification(self):
        # Either field is the provider saying "this is still true", so the later
        # one is the freshest evidence the record is live.
        row = self._row({"last_updated": "2019-01-01", "certification_date": "2025-06-30"})
        assert row["registry_last_updated"] == "2025-06-30"

        row = self._row({"last_updated": "2025-06-30", "certification_date": "2019-01-01"})
        assert row["registry_last_updated"] == "2025-06-30"

    def test_certification_alone_is_used(self):
        row = self._row({"certification_date": "2021-03-04"})
        assert row["registry_last_updated"] == "2021-03-04"

    def test_missing_dates_are_none_not_a_crash(self):
        row = self._row({"status": "A"})
        assert row["registry_last_updated"] is None
        assert row["registry_enumerated_on"] is None

    def test_malformed_date_is_rejected(self):
        row = self._row({"last_updated": "not-a-date"})
        assert row["registry_last_updated"] is None

    def test_absent_freshness_fields_do_not_break_older_fixtures(self):
        # The pre-0.2.0 sample has no basic.status at all.
        rows = parse_nppes_json(_sample_bytes())
        assert rows[0]["registry_status"] is None
        assert rows[0]["registry_last_updated"] is None


class TestExtractProviderRow:
    def test_returns_none_without_any_name(self):
        res = {"number": 1, "basic": {}, "addresses": [], "taxonomies": []}
        assert _extract_provider_row(res) is None

    def test_npi_is_stringified(self):
        row = _extract_provider_row(_SAMPLE["results"][0])
        assert row["npi"] == "1588030589"
        assert isinstance(row["npi"], str)


class TestNppesFetch:
    @respx.mock
    def test_fetch_returns_merged_document(self):
        route = respx.get(NPPES_API_URL).mock(
            return_value=httpx.Response(200, content=_sample_bytes())
        )
        scraper = NppesScraper(max_records=200)
        content, url = scraper.fetch()

        # A short page (<200) ends pagination after a single request.
        assert route.call_count == 1
        rows = parse_nppes_json(content)
        assert {r["npi"] for r in rows} == {"1588030589", "1356713762"}
        assert NPPES_API_URL in url

    @respx.mock
    def test_org_name_gets_trailing_wildcard(self):
        """A bare brand name must be sent with a trailing * for partial match."""
        route = respx.get(NPPES_API_URL).mock(
            return_value=httpx.Response(200, content=_sample_bytes())
        )
        scraper = NppesScraper(organization_name="action behavior")
        _, url = scraper.fetch()

        sent = route.calls.last.request.url
        assert sent.params["organization_name"] == "action behavior*"
        # The provenance URL reflects the wildcarded query too.
        assert "organization_name=action+behavior*" in url

    @respx.mock
    def test_org_name_existing_wildcard_not_doubled(self):
        route = respx.get(NPPES_API_URL).mock(
            return_value=httpx.Response(200, content=_sample_bytes())
        )
        scraper = NppesScraper(organization_name="hopebridge*")
        scraper.fetch()
        assert route.calls.last.request.url.params["organization_name"] == "hopebridge*"
