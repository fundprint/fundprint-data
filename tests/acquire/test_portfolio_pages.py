"""Tests for the portfolio parsers (HTML and JSON modes). No live HTTP."""

import json

import httpx
import pytest
import respx

from fundprint.acquire.portfolio_pages import (
    PE_FIRM_CONFIGS,
    PortfolioPageConfig,
    PortfolioPageScraper,
    _configs_by_firm,
    parse_portfolio_html,
    parse_portfolio_json,
    parse_portfolio_wp,
)


@pytest.fixture
def html_config() -> PortfolioPageConfig:
    """An HTML-mode config matching the portfolio_sample.html fixture."""
    return PortfolioPageConfig(
        firm_name="KKR",
        url="https://www.kkr.com/invest/portfolio",
        item_selector="div.portfolio-company",
        name_selector=".company-name, h3",
        description_selector=".description",
        link_selector="a",
        sector_selector=".sector",
    )


class TestParsePortfolioHtml:
    def test_parses_three_companies(self, portfolio_html, html_config):
        rows = parse_portfolio_html(portfolio_html, html_config)
        assert len(rows) == 3

    def test_portfolio_names(self, portfolio_html, html_config):
        rows = parse_portfolio_html(portfolio_html, html_config)
        names = [r["portfolio_name"] for r in rows]
        assert "Therapeutic Pathways Inc." in names
        assert "Centria Autism Services" in names
        assert "Accel Therapies" in names

    def test_firm_name_stamped(self, portfolio_html, html_config):
        rows = parse_portfolio_html(portfolio_html, html_config)
        assert all(r["pe_firm_name"] == "KKR" for r in rows)

    def test_descriptions_extracted(self, portfolio_html, html_config):
        rows = parse_portfolio_html(portfolio_html, html_config)
        by_name = {r["portfolio_name"]: r for r in rows}
        assert "ABA therapy" in by_name["Therapeutic Pathways Inc."]["description"]

    def test_sector_tags_extracted(self, portfolio_html, html_config):
        rows = parse_portfolio_html(portfolio_html, html_config)
        by_name = {r["portfolio_name"]: r for r in rows}
        tags = by_name["Therapeutic Pathways Inc."]["sector_tags"]
        assert "Healthcare" in tags
        assert "Behavioral Health" in tags

    def test_relative_url_resolved(self, portfolio_html, html_config):
        rows = parse_portfolio_html(portfolio_html, html_config)
        by_name = {r["portfolio_name"]: r for r in rows}
        url = by_name["Therapeutic Pathways Inc."]["portfolio_url"]
        assert url is not None
        assert "kkr.com" in url or url.startswith("/")

    def test_absolute_url_preserved(self, portfolio_html, html_config):
        rows = parse_portfolio_html(portfolio_html, html_config)
        by_name = {r["portfolio_name"]: r for r in rows}
        url = by_name["Centria Autism Services"]["portfolio_url"]
        assert url == "https://www.centriaautism.com"

    def test_empty_html_returns_empty_list(self, html_config):
        rows = parse_portfolio_html(b"<html><body></body></html>", html_config)
        assert rows == []

    def test_listed_as_of_is_set(self, portfolio_html, html_config):
        rows = parse_portfolio_html(portfolio_html, html_config)
        assert all(r["listed_as_of"] is not None for r in rows)


# A KKR-style API payload (one page) shaped like the live bioportfoliosearch JSON.
_API_SAMPLE = {
    "success": True,
    "hits": 2,
    "pages": 1,
    "results": [
        {
            "name": "Centria Autism Services",
            "hq": "Farmington Hills, MI, United States",
            "region": "Americas",
            "assetClass": "Private Equity",
            "industry": "Healthcare",
            "yoi": "2021",
            "url": "www.centriaautism.com",
            "description": "<p>National provider of <b>ABA</b> therapy for autism.</p>",
        },
        {
            "name": "123Dentist",
            "region": "Americas",
            "assetClass": "Private Equity",
            "industry": "Healthcare",
            "url": "https://www.123dentist.com",
            "description": "",
        },
    ],
}


@pytest.fixture
def api_config() -> PortfolioPageConfig:
    return _configs_by_firm["KKR"]


class TestParsePortfolioJson:
    def test_parses_results(self, api_config):
        rows = parse_portfolio_json(json.dumps(_API_SAMPLE).encode(), api_config)
        assert len(rows) == 2

    def test_firm_name_stamped(self, api_config):
        rows = parse_portfolio_json(json.dumps(_API_SAMPLE).encode(), api_config)
        assert all(r["pe_firm_name"] == "KKR" for r in rows)

    def test_bare_domain_made_absolute(self, api_config):
        rows = parse_portfolio_json(json.dumps(_API_SAMPLE).encode(), api_config)
        by_name = {r["portfolio_name"]: r for r in rows}
        assert by_name["Centria Autism Services"]["portfolio_url"] == "https://www.centriaautism.com"

    def test_absolute_url_preserved(self, api_config):
        rows = parse_portfolio_json(json.dumps(_API_SAMPLE).encode(), api_config)
        by_name = {r["portfolio_name"]: r for r in rows}
        assert by_name["123Dentist"]["portfolio_url"] == "https://www.123dentist.com"

    def test_html_description_flattened(self, api_config):
        rows = parse_portfolio_json(json.dumps(_API_SAMPLE).encode(), api_config)
        by_name = {r["portfolio_name"]: r for r in rows}
        desc = by_name["Centria Autism Services"]["description"]
        assert "<" not in desc and "ABA" in desc

    def test_sector_tags_from_industry_and_class(self, api_config):
        rows = parse_portfolio_json(json.dumps(_API_SAMPLE).encode(), api_config)
        by_name = {r["portfolio_name"]: r for r in rows}
        tags = by_name["Centria Autism Services"]["sector_tags"]
        assert "Healthcare" in tags
        assert "Private Equity" in tags

    def test_empty_results(self, api_config):
        rows = parse_portfolio_json(json.dumps({"results": []}).encode(), api_config)
        assert rows == []


class TestFetchApiPagination:
    @respx.mock
    def test_fetch_paginates_and_merges(self):
        """_fetch_api should follow `pages` and concatenate every page's results."""
        page1 = {"pages": 2, "results": [{"name": "Alpha", "url": "www.a.com"}]}
        page2 = {"pages": 2, "results": [{"name": "Beta", "url": "www.b.com"}]}
        scraper = PortfolioPageScraper(firm_name="KKR")
        route = respx.get(scraper._config.api_url).mock(
            side_effect=[
                httpx.Response(200, json=page1),
                httpx.Response(200, json=page2),
            ]
        )

        content, url = scraper.fetch()

        assert route.call_count == 2
        rows = parse_portfolio_json(content, scraper._config)
        assert [r["portfolio_name"] for r in rows] == ["Alpha", "Beta"]
        # Provenance is the human page, not the servlet URL.
        assert url == "https://www.kkr.com/invest/portfolio"


class TestPeFirmConfigs:
    def test_kkr_config_present_and_api_mode(self):
        cfg = _configs_by_firm["KKR"]
        assert cfg.api_url  # KKR is JSON-API mode

    def test_blackstone_config_present(self):
        assert "Blackstone" in _configs_by_firm

    def test_all_configs_are_usable(self):
        """Every config must support exactly one mode: JSON api_url or HTML selectors."""
        for cfg in PE_FIRM_CONFIGS:
            assert cfg.firm_name
            assert cfg.url.startswith("https://")
            if cfg.api_url:
                assert cfg.api_url.startswith("https://")
            else:
                assert cfg.item_selector and cfg.name_selector

    def test_blackstone_is_wp_mode_with_allowlist(self):
        cfg = _configs_by_firm["Blackstone"]
        assert cfg.api_style == "wp"
        assert cfg.api_taxonomy_url.startswith("https://")
        assert cfg.api_taxonomy_field == "investment-type"
        assert "Healthcare" in cfg.sector_allowlist


# A WordPress REST payload shaped like Blackstone's /wp/v2/investment feed,
# mixing a real portfolio company, an allowlisted-sector company, and a CSR /
# career-pathway entry that must be filtered out.
_WP_SAMPLE = {
    "taxonomy": {
        "259": "Healthcare",
        "102": "Technology",
        "105": "Veterans",
    },
    "results": [
        {
            "title": {"rendered": "TeamHealth"},
            "excerpt": {"rendered": "<p>A leading <b>physician</b> practice.</p>"},
            "link": "https://www.blackstone.com/investment/teamhealth/",
            "investment-type": [259],
        },
        {
            "title": {"rendered": "Salas O&#8217;Brien"},
            "excerpt": {"rendered": ""},
            "link": "https://www.blackstone.com/investment/salas-obrien/",
            "investment-type": [102],
        },
        {
            "title": {"rendered": "Hire Heroes USA"},
            "excerpt": {"rendered": "<p>Career support for veterans.</p>"},
            "link": "https://www.blackstone.com/investment/hire-heroes/",
            "investment-type": [105],
        },
    ],
}


@pytest.fixture
def wp_config() -> PortfolioPageConfig:
    return _configs_by_firm["Blackstone"]


class TestParsePortfolioWp:
    def test_filters_out_non_allowlisted_sectors(self, wp_config):
        rows = parse_portfolio_wp(json.dumps(_WP_SAMPLE).encode(), wp_config)
        names = {r["portfolio_name"] for r in rows}
        assert "TeamHealth" in names
        assert "Salas O’Brien" in names  # HTML entity unescaped
        assert "Hire Heroes USA" not in names  # Veterans CSR entry dropped

    def test_firm_name_stamped(self, wp_config):
        rows = parse_portfolio_wp(json.dumps(_WP_SAMPLE).encode(), wp_config)
        assert all(r["pe_firm_name"] == "Blackstone" for r in rows)

    def test_sector_tags_resolved_from_taxonomy(self, wp_config):
        rows = parse_portfolio_wp(json.dumps(_WP_SAMPLE).encode(), wp_config)
        by_name = {r["portfolio_name"]: r for r in rows}
        assert by_name["TeamHealth"]["sector_tags"] == ["Healthcare"]

    def test_html_description_flattened(self, wp_config):
        rows = parse_portfolio_wp(json.dumps(_WP_SAMPLE).encode(), wp_config)
        by_name = {r["portfolio_name"]: r for r in rows}
        desc = by_name["TeamHealth"]["description"]
        assert "<" not in desc and "physician" in desc

    def test_link_preserved(self, wp_config):
        rows = parse_portfolio_wp(json.dumps(_WP_SAMPLE).encode(), wp_config)
        by_name = {r["portfolio_name"]: r for r in rows}
        assert by_name["TeamHealth"]["portfolio_url"].endswith("/teamhealth/")

    def test_empty_results(self, wp_config):
        rows = parse_portfolio_wp(
            json.dumps({"results": [], "taxonomy": {}}).encode(), wp_config
        )
        assert rows == []

    def test_fetch_wp_paginates_via_header(self):
        """_fetch_wp follows X-WP-TotalPages and resolves the taxonomy once."""
        cfg = _configs_by_firm["Blackstone"]
        scraper = PortfolioPageScraper(firm_name="Blackstone")
        with respx.mock:
            respx.get(cfg.api_taxonomy_url).mock(
                return_value=httpx.Response(200, json=[{"id": 259, "name": "Healthcare"}])
            )
            respx.get(cfg.api_url).mock(
                side_effect=[
                    httpx.Response(
                        200,
                        headers={"X-WP-TotalPages": "2"},
                        json=[{
                            "title": {"rendered": "TeamHealth"},
                            "excerpt": {"rendered": ""},
                            "link": "https://x/teamhealth/",
                            "investment-type": [259],
                        }],
                    ),
                    httpx.Response(
                        200,
                        headers={"X-WP-TotalPages": "2"},
                        json=[{
                            "title": {"rendered": "Medline"},
                            "excerpt": {"rendered": ""},
                            "link": "https://x/medline/",
                            "investment-type": [259],
                        }],
                    ),
                ]
            )
            content, url = scraper.fetch()

        rows = parse_portfolio_wp(content, cfg)
        assert [r["portfolio_name"] for r in rows] == ["TeamHealth", "Medline"]
        assert url == cfg.url  # provenance is the human page
