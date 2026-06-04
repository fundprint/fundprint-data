"""Generic PE portfolio page scraper.

PE firms publish their current portfolio on their website in varying formats.
This module handles static-HTML portfolio pages using per-firm CSS selector
configs. One config entry is enough for testing; adding new firms is just
adding a new entry to PE_FIRM_CONFIGS.

Why CSS selectors in config rather than firm-specific parsers: portfolio pages
change layout every couple of years, and a config update beats a code deploy.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import httpx
from bs4 import BeautifulSoup

from fundprint.acquire.base import Scraper
from fundprint.acquire.registry import register

logger = logging.getLogger(__name__)

FUNDPRINT_UA = "FundprintBot/0.1 (+mailto:atharva.doke737@gmail.com)"

# Polite pause between paged API requests.
REQUEST_DELAY_SEC = 0.15
# Safety cap so a misbehaving paginated API can't loop forever.
MAX_API_PAGES = 100


@dataclass
class PortfolioPageConfig:
    """Config for one PE firm's portfolio listing.

    Two modes:
      * HTML  - set the CSS selectors; fetch() pulls the page and parse() reads
                the DOM. Works for server-rendered static portfolio pages.
      * JSON  - set api_url (and any api_params); fetch() paginates the JSON
                endpoint and parse() reads the result objects. Needed for the
                JavaScript-rendered sites large PE firms use, whose portfolio
                data loads from a backing API rather than the initial HTML.

    `url` is always the human-facing portfolio page, recorded as provenance.
    """

    firm_name: str
    url: str
    # --- HTML mode ---
    # CSS selector matching the container element for each portfolio company
    item_selector: str = ""
    # Within each item, these selectors pull specific fields
    name_selector: str = ""
    description_selector: str = ""
    link_selector: str = "a"
    sector_selector: str = ""
    # --- JSON mode (takes precedence over HTML when api_url is set) ---
    api_url: str = ""
    api_params: dict[str, str] = field(default_factory=dict)


# Real configs.
#
# KKR's portfolio is a JavaScript app (Adobe Experience Manager); the initial
# HTML has no company data. It loads from a backing search servlet that returns
# clean JSON, so we use JSON mode against that endpoint.
#
# Blackstone is kept as an HTML reference config; its selectors may need
# updating after a site redesign (and it, too, may move to JS-only rendering).
PE_FIRM_CONFIGS: list[PortfolioPageConfig] = [
    PortfolioPageConfig(
        firm_name="KKR",
        url="https://www.kkr.com/invest/portfolio",
        api_url=(
            "https://www.kkr.com/content/kkr/sites/global/en/invest/portfolio/"
            "jcr:content/root/main-par/bioportfoliosearch.bioportfoliosearch.json"
        ),
        api_params={"region": "all"},
    ),
    PortfolioPageConfig(
        firm_name="Blackstone",
        url="https://www.blackstone.com/our-businesses/portfolio-operations/portfolio-companies/",
        item_selector=".portfolio-company, article, .company-item",
        name_selector="h3, h4, .name",
        description_selector=".description, p",
        link_selector="a",
        sector_selector=".sector, .category",
    ),
]

_configs_by_firm: dict[str, PortfolioPageConfig] = {
    c.firm_name: c for c in PE_FIRM_CONFIGS
}


@register
class PortfolioPageScraper(Scraper):
    """Scrapes PE firm portfolio listing pages using CSS selector configs."""

    source_family = "pe_portfolio"
    module_version = "0.1.0"

    def __init__(self, store=None, firm_name: str = "KKR") -> None:
        super().__init__(store)
        if firm_name not in _configs_by_firm:
            raise ValueError(
                f"No config for firm {firm_name!r}. "
                f"Available: {list(_configs_by_firm)}"
            )
        self._config = _configs_by_firm[firm_name]

    def fetch(self) -> tuple[bytes, str]:
        """Fetch the portfolio listing. JSON API if configured, else page HTML."""
        if self._config.api_url:
            return self._fetch_api()
        headers = {"User-Agent": FUNDPRINT_UA}
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            resp = client.get(self._config.url, headers=headers)
            resp.raise_for_status()
        return resp.content, str(resp.url)

    def _fetch_api(self) -> tuple[bytes, str]:
        """Paginate the firm's JSON portfolio endpoint and merge all results.

        Returns the merged results as JSON bytes plus the human portfolio page
        URL as provenance (not the servlet URL, which is an implementation detail).
        """
        headers = {"User-Agent": FUNDPRINT_UA, "Accept": "application/json"}
        results: list[dict[str, Any]] = []
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            page = 1
            total_pages = 1
            while page <= total_pages and page <= MAX_API_PAGES:
                params = {**self._config.api_params, "page": str(page)}
                resp = client.get(self._config.api_url, params=params, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                results.extend(data.get("results", []))
                total_pages = int(data.get("pages", 1) or 1)
                page += 1
                if page <= total_pages:
                    time.sleep(REQUEST_DELAY_SEC)
        content = json.dumps({"results": results}).encode()
        logger.info("%s portfolio API: %d companies", self._config.firm_name, len(results))
        return content, self._config.url

    def parse(self, content: bytes) -> list[dict[str, Any]]:
        """Parse the portfolio listing into staging rows using this config."""
        if self._config.api_url:
            return parse_portfolio_json(content, self._config)
        return parse_portfolio_html(content, self._config)

    def _write_staging(
        self, rows: list[dict[str, Any]], source_record_id: str, conn: Any
    ) -> None:
        for row in rows:
            conn.execute(
                """
                INSERT INTO staging_pe_portfolio_listing
                    (source_record_id, pe_firm_name, portfolio_name,
                     portfolio_url, description, sector_tags, listed_as_of)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    source_record_id,
                    row["pe_firm_name"],
                    row["portfolio_name"],
                    row.get("portfolio_url"),
                    row.get("description"),
                    row.get("sector_tags", []),
                    row.get("listed_as_of"),
                ),
            )


def parse_portfolio_html(
    content: bytes, config: PortfolioPageConfig
) -> list[dict[str, Any]]:
    """Parse PE portfolio page HTML using the given selector config.

    Pure function - no HTTP, no side effects. Tests call this directly.
    """
    soup = BeautifulSoup(content, "html.parser")
    items = soup.select(config.item_selector)
    rows = []

    for item in items:
        name_el = item.select_one(config.name_selector)
        if not name_el:
            continue
        portfolio_name = name_el.get_text(strip=True)
        if not portfolio_name:
            continue

        desc_sel = config.description_selector
        desc_el = item.select_one(desc_sel) if desc_sel else None
        description = desc_el.get_text(strip=True) if desc_el else None

        link_el = item.select_one(config.link_selector)
        portfolio_url = link_el.get("href") if link_el else None
        # Resolve relative URLs - a best-effort prepend of the base domain
        if portfolio_url and portfolio_url.startswith("/"):
            from urllib.parse import urlparse
            parsed = urlparse(config.url)
            portfolio_url = f"{parsed.scheme}://{parsed.netloc}{portfolio_url}"

        sector_tags = []
        if config.sector_selector:
            for tag_el in item.select(config.sector_selector):
                tag = tag_el.get_text(strip=True)
                if tag:
                    sector_tags.append(tag)

        rows.append({
            "pe_firm_name": config.firm_name,
            "portfolio_name": portfolio_name,
            "portfolio_url": portfolio_url,
            "description": description,
            "sector_tags": sector_tags,
            "listed_as_of": date.today().isoformat(),
        })

    return rows


def parse_portfolio_json(
    content: bytes, config: PortfolioPageConfig
) -> list[dict[str, Any]]:
    """Parse a firm's portfolio JSON ({"results": [...]}) into staging rows.

    Pure function - no HTTP, no side effects. Tests call this directly.
    Maps the common KKR-style fields; description HTML is flattened to text and
    industry/assetClass/region become sector_tags for the resolve layer to use.
    """
    data = json.loads(content)
    rows = []

    for item in data.get("results", []):
        portfolio_name = (item.get("name") or "").strip()
        if not portfolio_name:
            continue

        # Company URLs arrive as bare domains ("www.example.com"); make absolute.
        portfolio_url = (item.get("url") or "").strip() or None
        if portfolio_url and not portfolio_url.startswith(("http://", "https://")):
            portfolio_url = "https://" + portfolio_url.lstrip("/")

        # Descriptions are HTML fragments; flatten to plain text.
        desc_raw = item.get("description") or ""
        description = (
            BeautifulSoup(desc_raw, "html.parser").get_text(" ", strip=True) or None
        )

        sector_tags = [
            str(tag).strip()
            for key in ("industry", "assetClass", "region")
            if (tag := item.get(key))
        ]

        rows.append({
            "pe_firm_name": config.firm_name,
            "portfolio_name": portfolio_name,
            "portfolio_url": portfolio_url,
            "description": description,
            "sector_tags": sector_tags,
            "listed_as_of": date.today().isoformat(),
        })

    return rows
