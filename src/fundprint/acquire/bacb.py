"""BACB provider directory scraper.

Live URL: https://www.bacb.com/practitioner-directory/
The directory is rendered by JavaScript, so the fetch() path requires Playwright.
fetch() raises NotImplementedError so this module is testable without a browser;
tests call parse() directly with captured HTML fixtures.

The BACB publishes a public directory of credentialed behavior analysts.
We capture it to track which credentialed practitioners are affiliated with
PE-backed clinic chains - a signal that a clinic employs certified staff.
"""

from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup

from fundprint.acquire.base import Scraper
from fundprint.acquire.registry import register

BACB_DIRECTORY_URL = "https://www.bacb.com/practitioner-directory/"

# User-agent sent on all requests so BACB can identify us
FUNDPRINT_UA = "FundprintBot/0.1 (+mailto:atharva.doke737@gmail.com)"


@register
class BacbScraper(Scraper):
    """Ingests the BACB credentialed practitioner directory.

    fetch() is a Playwright stub. Use parse() in tests with saved HTML.
    """

    source_family = "bacb"
    module_version = "0.1.0"

    def fetch(self) -> tuple[bytes, str]:
        # The BACB directory requires JavaScript to render the provider table.
        # Production use: launch Playwright, navigate to BACB_DIRECTORY_URL,
        # wait for the results grid, then return page.content().encode().
        raise NotImplementedError(
            "BacbScraper.fetch() requires Playwright. "
            f"Target URL: {BACB_DIRECTORY_URL}"
        )

    def parse(self, content: bytes) -> list[dict[str, Any]]:
        """Parse BACB provider directory HTML into staging rows."""
        return parse_bacb_html(content)

    def _write_staging(
        self, rows: list[dict[str, Any]], source_record_id: str, conn: Any
    ) -> None:
        for row in rows:
            conn.execute(
                """
                INSERT INTO staging_bacb_provider
                    (source_record_id, raw_name, address_line1, city, state, zip,
                     npi, credential_type)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    source_record_id,
                    row.get("raw_name", ""),
                    row.get("address_line1"),
                    row.get("city"),
                    row.get("state"),
                    row.get("zip"),
                    row.get("npi"),
                    row.get("credential_type"),
                ),
            )


def parse_bacb_html(content: bytes) -> list[dict[str, Any]]:
    """Parse BACB practitioner directory HTML into a list of provider dicts.

    Handles the table structure the directory renders for each credentialed
    practitioner. Whitespace normalization only - no field interpretation.
    """
    soup = BeautifulSoup(content, "html.parser")
    rows: list[dict[str, Any]] = []

    # The directory renders results as <tr> rows inside a table with
    # class "provider-directory" or similar. We look for any table row
    # that contains a practitioner name cell.
    for tr in soup.select("table.provider-table tr, table tr.provider-row, tr[data-provider]"):
        cells = [td.get_text(separator=" ", strip=True) for td in tr.find_all("td")]
        if len(cells) < 2:
            continue

        row = _parse_provider_cells(cells)
        if row:
            rows.append(row)

    # Fallback: some captured pages store providers as divs
    if not rows:
        rows = _parse_provider_divs(soup)

    return rows


def _parse_provider_cells(cells: list[str]) -> dict[str, Any] | None:
    """Extract provider fields from a table row's cell texts."""
    raw_name = cells[0].strip()
    if not raw_name:
        return None

    # Credential type is usually the second cell (BCBA, BCaBA, RBT, ...)
    credential_type = cells[1].strip() if len(cells) > 1 else None

    # Address fields vary by page version; try to parse city/state/zip from
    # a combined address cell if present
    address_text = cells[2].strip() if len(cells) > 2 else ""
    address_line1, city, state, zip_code = _split_address(address_text)

    npi = None
    for cell in cells:
        m = re.search(r"\b(\d{10})\b", cell)
        if m:
            npi = m.group(1)
            break

    return {
        "raw_name": raw_name,
        "credential_type": credential_type,
        "address_line1": address_line1,
        "city": city,
        "state": state,
        "zip": zip_code,
        "npi": npi,
    }


def _parse_provider_divs(soup: BeautifulSoup) -> list[dict[str, Any]]:
    """Fallback parser for div-based BACB directory layouts."""
    rows = []
    for div in soup.select("div.provider-card, div.practitioner, div[data-name]"):
        name_el = div.select_one(".name, .provider-name, [data-name]")
        if not name_el:
            continue
        raw_name = name_el.get_text(strip=True)
        if not raw_name:
            continue

        cred_el = div.select_one(".credential, .cred-type")
        credential_type = cred_el.get_text(strip=True) if cred_el else None

        addr_el = div.select_one(".address, .location")
        address_text = addr_el.get_text(separator=", ", strip=True) if addr_el else ""
        address_line1, city, state, zip_code = _split_address(address_text)

        rows.append({
            "raw_name": raw_name,
            "credential_type": credential_type,
            "address_line1": address_line1,
            "city": city,
            "state": state,
            "zip": zip_code,
            "npi": None,
        })
    return rows


def _split_address(text: str) -> tuple[str | None, str | None, str | None, str | None]:
    """Best-effort split of a single address string into components.

    Returns (address_line1, city, state, zip). Returns None for any component
    that cannot be extracted - never raises.
    """
    if not text:
        return None, None, None, None

    # Match "City, ST XXXXX" at the end of the string
    m = re.search(
        r"^(.*?)([A-Za-z\s]+),\s*([A-Z]{2})\s+(\d{5}(?:-\d{4})?)\s*$",
        text.strip(),
    )
    if m:
        address_line1 = m.group(1).strip(" ,") or None
        city = m.group(2).strip() or None
        state = m.group(3).strip() or None
        zip_code = m.group(4).strip() or None
        return address_line1, city, state, zip_code

    return text.strip() or None, None, None, None
