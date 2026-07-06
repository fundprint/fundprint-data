"""Curated-acquisition ingester.

Some PE-backed ABA chains have no scrapable PE-owner portfolio page (the owner
site is JavaScript-only, unreachable, or the firm simply does not publish a
portfolio list). For those, ownership is still documented in a public primary
source - the acquisition press release or trade-press report. This module
ingests a hand-curated, individually-verified list of those ownership facts.

Each entry names the PE firm, the portfolio company, and a public source_url.
On ingest we *fetch and snapshot that exact URL* (same provenance model as the
scrapers: a real source_record with a content_hash), then write a
staging_pe_portfolio_listing row so the normal deterministic resolver turns it
into an owner -> PE-firm claim. Nothing here asserts ownership without a
captured, citable source.

Adding a chain = adding one verified CuratedAcquisition entry. Keep the bar
high: a primary acquisition announcement or reputable trade-press report that
explicitly states the ownership, and confirm it is the *current* owner.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

import httpx

from fundprint import db
from fundprint.acquire.base import (
    _find_existing_source_record,
    _insert_source_record,
)
from fundprint.storage import LocalFilesystemStore, SnapshotStore

logger = logging.getLogger(__name__)

FUNDPRINT_UA = "FundprintBot/0.1 (+mailto:atharva.doke737@gmail.com)"
SOURCE_FAMILY = "curated_acquisition"
MODULE_VERSION = "0.1.0"


@dataclass
class CuratedAcquisition:
    """One verified ownership fact, with the public source that documents it."""

    pe_firm_name: str
    portfolio_name: str
    source_url: str
    description: str
    sector_tags: list[str] = field(default_factory=lambda: ["Autism/ABA"])
    # Owner-type label. Most chains are PE-backed, but a few are owned by other
    # institutional financial owners; we record the type honestly rather than
    # calling everything "private equity". Maps to parent_pe_firm.firm_type.
    firm_type: str = "private_equity"


# Verified ownership facts. Each source_url was confirmed reachable and to state
# the ownership at curation time (June 2026). Most entries are private-equity
# ownership; a few are other institutional financial owners (pension funds,
# family offices). Those carry a non-default firm_type so the dashboard can
# label them honestly rather than implying they are private equity.
CURATED_ACQUISITIONS: list[CuratedAcquisition] = [
    CuratedAcquisition(
        # Brand stored as "Centria" (not "Centria Autism") so the clinic linker's
        # normalized name-prefix match catches NPPES orgs named "CENTRIA",
        # "CENTRIA HEALTHCARE", and "CENTRIA AUTISM".
        pe_firm_name="Thomas H. Lee Partners",
        portfolio_name="Centria",
        source_url=(
            "https://www.pehub.com/"
            "thomas-h-lee-partners-buys-centria-in-400-mln-plus-deal/"
        ),
        description=(
            "Centria (Centria Healthcare / Centria Autism), a national ABA "
            "provider, was recapitalized by Thomas H. Lee Partners in a $400M+ "
            "deal (Dec 2019)."
        ),
    ),
    CuratedAcquisition(
        pe_firm_name="Tenex Capital Management",
        portfolio_name="Behavioral Innovations",
        source_url=(
            "https://bhbusiness.com/2024/05/31/"
            "behavioral-innovations-sells-to-new-pe-owner-marking-biggest-deal-of-2024-so-far/"
        ),
        description=(
            "Behavioral Innovations, a Texas-based ABA provider, was sold by "
            "Shore Capital Partners to Tenex Capital Management (2024)."
        ),
    ),
    CuratedAcquisition(
        # Trumpet was rolled up by KKR-backed BlueSprig, so KKR is the PE owner.
        pe_firm_name="KKR",
        portfolio_name="Trumpet Behavioral Health",
        source_url=(
            "https://www.bluesprigautism.com/blog/"
            "trumpet-behavioral-health-joins-the-bluesprig-family-of-companies/"
        ),
        description=(
            "Trumpet Behavioral Health joined KKR-backed BlueSprig Pediatrics "
            "in 2023 (~40 locations), making KKR the ultimate PE owner."
        ),
    ),
    CuratedAcquisition(
        pe_firm_name="Ontario Teachers' Pension Plan",
        portfolio_name="Acorn Health",
        firm_type="pension_fund",
        source_url=(
            "https://www.otpp.com/en-ca/about-us/news-and-insights/2021/"
            "ontario-teachers-acquires-majority-interest-in-acorn-health/"
        ),
        description=(
            "Ontario Teachers' Pension Plan acquired a majority stake in Acorn "
            "Health, a national ABA provider, from MBF Healthcare Partners "
            "(Aug 2021). Owner is a pension fund, not a private-equity firm."
        ),
    ),
    CuratedAcquisition(
        pe_firm_name="Moran Capital Partners",
        portfolio_name="Butterfly Effects",
        firm_type="family_office",
        source_url=(
            "https://www.prweb.com/releases/"
            "butterfly_effects_completes_acquisition_of_autism_treatment_solutions_llc/"
            "prweb12936969.htm"
        ),
        description=(
            "Butterfly Effects, a national in-home/in-center ABA provider, is a "
            "portfolio company of Moran Capital Partners, LLC. Owner is a family "
            "office / private holding company, not a traditional PE fund."
        ),
    ),
]


def _fetch(url: str) -> tuple[bytes, str]:
    """Fetch a curated source URL. Returns (content_bytes, final_url)."""
    headers = {"User-Agent": FUNDPRINT_UA}
    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        resp = client.get(url, headers=headers)
        resp.raise_for_status()
    return resp.content, str(resp.url)


def _write_staging_row(
    conn: Any, entry: CuratedAcquisition, source_record_id: str
) -> None:
    conn.execute(
        """
        INSERT INTO staging_pe_portfolio_listing
            (source_record_id, pe_firm_name, portfolio_name,
             portfolio_url, description, sector_tags, listed_as_of)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (
            source_record_id,
            entry.pe_firm_name,
            entry.portfolio_name,
            entry.source_url,
            entry.description,
            entry.sector_tags,
            date.today().isoformat(),
        ),
    )


def ingest_curated(
    entries: list[CuratedAcquisition] | None = None,
    *,
    store: SnapshotStore | None = None,
) -> dict[str, int]:
    """Fetch, snapshot, and stage each curated ownership fact.

    Idempotent per entry via the (source_url, content_hash) source_record guard,
    exactly like the scrapers. Returns counts of staged / skipped / failed.
    """
    entries = entries if entries is not None else CURATED_ACQUISITIONS
    store = store or LocalFilesystemStore()
    summary = {"seen": len(entries), "staged": 0, "skipped": 0, "failed": 0}

    for entry in entries:
        try:
            content, source_url = _fetch(entry.source_url)
        except Exception:
            logger.exception("curated fetch failed for %s", entry.source_url)
            summary["failed"] += 1
            continue

        snapshot_id, content_hash = store.put(content)
        with db.transaction() as conn:
            if _find_existing_source_record(conn, source_url, content_hash):
                logger.info("curated entry already staged: %s", entry.portfolio_name)
                summary["skipped"] += 1
                continue
            source_record_id = _insert_source_record(
                conn,
                source_url=source_url,
                snapshot_id=snapshot_id,
                source_type=SOURCE_FAMILY,
                fetched_at=datetime.now(UTC),
                content_hash=content_hash,
                module_version=MODULE_VERSION,
            )
            _write_staging_row(conn, entry, source_record_id)
            summary["staged"] += 1
            logger.info(
                "curated staged: %s -> %s", entry.portfolio_name, entry.pe_firm_name
            )

    logger.info("ingest_curated complete: %s", summary)
    return summary
