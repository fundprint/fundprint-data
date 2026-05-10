"""Generic PE portfolio page scraper.

PE firms publish their current portfolio on their website in varying formats.
This module handles static-HTML portfolio pages using per-firm CSS selector
configs. One config entry is enough for testing; adding new firms is just
adding a new entry to PE_FIRM_CONFIGS.

Why CSS selectors in config rather than firm-specific parsers: portfolio pages
change layout every couple of years, and a config update beats a code deploy.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Any

import httpx
from bs4 import BeautifulSoup

from fundprint.acquire.base import Scraper
from fundprint.acquire.registry import register

logger = logging.getLogger(__name__)

FUNDPRINT_UA = "FundprintBot/0.1 (+mailto:atharva.doke737@gmail.com)"


@dataclass
class PortfolioPageConfig:
    """Selector config for one PE firm's portfolio listing page."""

    firm_name: str
    url: str
    # CSS selector that matches the container element for each portfolio company
    item_selector: str
    # Within each item, these selectors pull specific fields
    name_selector: str
    description_selector: str = ""
    link_selector: str = "a"
    # Optional: a selector for sector/category tags
    sector_selector: str = ""


# Real configs. KKR's portfolio page uses a straightforward card layout.
# Blackstone is added as a second reference point; its selector may need
# updating after a site redesign.
PE_FIRM_CONFIGS: list[PortfolioPageConfig] = [
    PortfolioPageConfig(
        firm_name="KKR",
        url="https://www.kkr.com/businesses/portfolio",
        item_selector="div.portfolio-company, article.portfolio-item, .company-card",
        name_selector=".company-name, h3, h2",
        description_selector=".description, .company-description, p",
        link_selector="a",
        sector_selector=".sector, .category, .tag",
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
        """Fetch the portfolio page HTML. Real HTTP, no JavaScript required."""
        headers = {"User-Agent": FUNDPRINT_UA}
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            resp = client.get(self._config.url, headers=headers)
            resp.raise_for_status()
        return resp.content, str(resp.url)

    def parse(self, content: bytes) -> list[dict[str, Any]]:
        """Parse portfolio page HTML into staging rows using this instance's config."""
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
