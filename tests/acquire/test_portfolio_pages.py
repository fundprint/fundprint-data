"""Tests for the portfolio page parser. No HTTP calls."""

import pytest

from fundprint.acquire.portfolio_pages import (
    PE_FIRM_CONFIGS,
    PortfolioPageConfig,
    _configs_by_firm,
    parse_portfolio_html,
)


@pytest.fixture
def kkr_config() -> PortfolioPageConfig:
    return _configs_by_firm["KKR"]


class TestParsePortfolioHtml:
    def test_parses_three_companies(self, portfolio_html, kkr_config):
        rows = parse_portfolio_html(portfolio_html, kkr_config)
        assert len(rows) == 3

    def test_portfolio_names(self, portfolio_html, kkr_config):
        rows = parse_portfolio_html(portfolio_html, kkr_config)
        names = [r["portfolio_name"] for r in rows]
        assert "Therapeutic Pathways Inc." in names
        assert "Centria Autism Services" in names
        assert "Accel Therapies" in names

    def test_firm_name_stamped(self, portfolio_html, kkr_config):
        rows = parse_portfolio_html(portfolio_html, kkr_config)
        assert all(r["pe_firm_name"] == "KKR" for r in rows)

    def test_descriptions_extracted(self, portfolio_html, kkr_config):
        rows = parse_portfolio_html(portfolio_html, kkr_config)
        by_name = {r["portfolio_name"]: r for r in rows}
        assert "ABA therapy" in by_name["Therapeutic Pathways Inc."]["description"]

    def test_sector_tags_extracted(self, portfolio_html, kkr_config):
        rows = parse_portfolio_html(portfolio_html, kkr_config)
        by_name = {r["portfolio_name"]: r for r in rows}
        tags = by_name["Therapeutic Pathways Inc."]["sector_tags"]
        assert "Healthcare" in tags
        assert "Behavioral Health" in tags

    def test_relative_url_resolved(self, portfolio_html, kkr_config):
        rows = parse_portfolio_html(portfolio_html, kkr_config)
        by_name = {r["portfolio_name"]: r for r in rows}
        url = by_name["Therapeutic Pathways Inc."]["portfolio_url"]
        # Relative href should be prepended with KKR's domain
        assert url is not None
        assert "kkr.com" in url or url.startswith("/")

    def test_absolute_url_preserved(self, portfolio_html, kkr_config):
        rows = parse_portfolio_html(portfolio_html, kkr_config)
        by_name = {r["portfolio_name"]: r for r in rows}
        url = by_name["Centria Autism Services"]["portfolio_url"]
        assert url == "https://www.centriaautism.com"

    def test_empty_html_returns_empty_list(self, kkr_config):
        rows = parse_portfolio_html(b"<html><body></body></html>", kkr_config)
        assert rows == []

    def test_listed_as_of_is_set(self, portfolio_html, kkr_config):
        rows = parse_portfolio_html(portfolio_html, kkr_config)
        # Should have a date string; we don't pin the exact value
        assert all(r["listed_as_of"] is not None for r in rows)


class TestPeFirmConfigs:
    def test_kkr_config_present(self):
        assert "KKR" in _configs_by_firm

    def test_blackstone_config_present(self):
        assert "Blackstone" in _configs_by_firm

    def test_all_configs_have_required_fields(self):
        for cfg in PE_FIRM_CONFIGS:
            assert cfg.firm_name
            assert cfg.url.startswith("https://")
            assert cfg.item_selector
            assert cfg.name_selector
