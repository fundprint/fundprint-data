"""Tests for the curated-acquisitions ingester. No live HTTP or DB."""

from __future__ import annotations

from fundprint.acquire.curated import (
    CURATED_ACQUISITIONS,
    CuratedAcquisition,
    _write_staging_row,
)


class _FakeConn:
    """Records execute() calls so we can assert on the staging insert."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []

    def execute(self, sql: str, params: tuple) -> None:
        self.calls.append((sql, params))


class TestCuratedList:
    def test_every_entry_is_well_formed(self):
        assert CURATED_ACQUISITIONS, "curated list should not be empty"
        for e in CURATED_ACQUISITIONS:
            assert e.pe_firm_name
            assert e.portfolio_name
            assert e.source_url.startswith("https://")
            assert e.description
            assert e.sector_tags  # every entry carries at least one tag

    def test_source_urls_are_unique(self):
        urls = [e.source_url for e in CURATED_ACQUISITIONS]
        assert len(urls) == len(set(urls))


class TestWriteStagingRow:
    def test_insert_carries_firm_company_and_source(self):
        conn = _FakeConn()
        entry = CuratedAcquisition(
            pe_firm_name="Tenex Capital Management",
            portfolio_name="Behavioral Innovations",
            source_url="https://example.com/deal",
            description="desc",
        )
        _write_staging_row(conn, entry, "srid-123")

        assert len(conn.calls) == 1
        sql, params = conn.calls[0]
        assert "staging_pe_portfolio_listing" in sql
        assert "srid-123" in params
        assert "Tenex Capital Management" in params
        assert "Behavioral Innovations" in params
        # The cited source URL is stored as the row's portfolio_url provenance.
        assert "https://example.com/deal" in params
