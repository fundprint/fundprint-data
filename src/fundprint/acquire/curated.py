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
        # "Monarch Behavioral Therapy BII, LLC" is the single legal entity under
        # which Behavioral Innovations operates its Maryland and Colorado ABA
        # centers, so those clinics roll up to Tenex through Behavioral
        # Innovations. Evidence: the federal NPI registry (NPPES) records this
        # entity's alternate organization name as "Behavioral Innovations", and
        # Behavioral Innovations' own site lists Maryland centers in the same
        # cities as the entity's Maryland clinics (Cockeysville, Columbia,
        # Linthicum Heights, Silver Spring, Waldorf, Windsor Mill). Brand stored
        # as "Monarch Behavioral Therapy" so the linker's name-prefix match
        # catches the NPPES "MONARCH BEHAVIORAL THERAPY BII, LLC" orgs.
        pe_firm_name="Tenex Capital Management",
        portfolio_name="Monarch Behavioral Therapy",
        source_url="https://behavioral-innovations.com/location/maryland/",
        description=(
            "Monarch Behavioral Therapy BII, LLC operates Behavioral "
            "Innovations' Maryland and Colorado ABA centers. The NPI registry "
            "records the entity's alternate name as 'Behavioral Innovations', "
            "and Behavioral Innovations lists Maryland centers in the same "
            "cities. Behavioral Innovations is owned by Tenex Capital "
            "Management (acquired from Shore Capital Partners in 2024), so "
            "Tenex is the ultimate private-equity owner."
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
        # BlueSprig (KKR-backed) acquired Florida Autism Center from Shore
        # Capital in a ~$120M deal (2020); FAC now operates as a division of
        # BlueSprig, so KKR is the ultimate PE owner. Brand stored as "Florida
        # Autism Center" so the clinic linker's normalized name-prefix match
        # catches the NPPES "FLORIDA AUTISM CENTER" orgs.
        pe_firm_name="KKR",
        portfolio_name="Florida Autism Center",
        source_url=(
            "https://www.prnewswire.com/news-releases/"
            "bluesprig-and-fac-partner-to-build-a-national-leader-in-aba-therapy-services-301018648.html"
        ),
        description=(
            "Florida Autism Center was acquired by KKR-backed BlueSprig "
            "Pediatrics (from Shore Capital Partners) in a ~$120M deal (2020) "
            "and now operates as a division of BlueSprig, making KKR the "
            "ultimate private-equity owner."
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
    CuratedAcquisition(
        # Nautic Partners acquired Proud Moments ABA from Audax Private Equity
        # on Feb 3, 2025, so Nautic is the current PE owner. Source is Audax's
        # own exit announcement, which names Nautic as the buyer. Brand stored
        # as "Proud Moments" so the clinic linker's normalized name-prefix match
        # catches the NPPES "PROUD MOMENTS ABA OF <STATE>" orgs.
        pe_firm_name="Nautic Partners",
        portfolio_name="Proud Moments",
        source_url=(
            "https://www.audaxprivateequity.com/news/"
            "audax-private-equity-completes-exit-of-proud-moments"
        ),
        description=(
            "Proud Moments ABA, a national provider of applied behavior analysis "
            "therapy for children with autism (70+ clinics across 12 states), was "
            "acquired by Nautic Partners from Audax Private Equity on Feb 3, 2025. "
            "Nautic Partners is the current private-equity owner."
        ),
    ),
    CuratedAcquisition(
        # GTCR acquired Caravel Autism Health from Frazier Healthcare Partners in
        # 2024 and lists it as a current portfolio company. Brand stored as
        # "Caravel Autism Health" (not just "Caravel") so the name-prefix match
        # stays specific to the NPPES "CARAVEL AUTISM HEALTH" orgs.
        pe_firm_name="GTCR",
        portfolio_name="Caravel Autism Health",
        source_url="https://www.gtcr.com/portfolio-company/caravel-autism-health/",
        description=(
            "Caravel Autism Health, an Upper-Midwest ABA provider (60+ locations "
            "across eight states), was acquired by GTCR from Frazier Healthcare "
            "Partners in 2024 and is a current GTCR portfolio company, making "
            "GTCR the private-equity owner."
        ),
    ),
    CuratedAcquisition(
        # Key Autism Services is a portfolio company of Cane Investment Partners,
        # which lists it on its portfolio page. Cane describes itself as "a
        # private investment firm" providing expansion capital for mid- and
        # long-term holdings, not a traditional buyout PE fund, so firm_type is
        # "other" rather than "private_equity" to label the owner honestly.
        # Brand stored as "Key Autism Services" so the name-prefix match catches
        # the NPPES "KEY AUTISM SERVICES <STATE>, LLC" orgs.
        pe_firm_name="Cane Investment Partners",
        portfolio_name="Key Autism Services",
        firm_type="other",
        source_url="https://caneip.com/portfolio/",
        description=(
            "Key Autism Services, a multi-state ABA provider, is a portfolio "
            "company of Cane Investment Partners, a private investment firm that "
            "provides expansion capital for mid- and long-term holdings. Owner is "
            "an institutional private investor, not a traditional buyout PE fund."
        ),
    ),
    # --- LEARN Behavioral federation (Gryphon Investors) ---------------------
    # LEARN Behavioral is majority-owned by Gryphon Investors (invested 2019,
    # per Gryphon's own portfolio page https://www.gryphon-inv.com/companies/
    # learn-behavioral/). LEARN runs as a federation of distinct, locally
    # recognized ABA brands rather than one name, and lists those brands on its
    # own site (source_url below). Each brand below is therefore attributed to
    # Gryphon through LEARN. Only brands whose name is distinctive enough for a
    # clean NPPES name-prefix match are included; LEARN's "Behavioral Concepts"
    # and "SPARKS" are deliberately omitted because those names collide with
    # unrelated organizations in the registry (over-capture risk).
    CuratedAcquisition(
        pe_firm_name="Gryphon Investors",
        portfolio_name="Autism Spectrum Therapies",
        source_url=(
            "https://learnbehavioral.com/careers/working-at-learn-behavioral"
            "#autism-spectrum-therapies"
        ),
        description=(
            "Autism Spectrum Therapies (AST) is a California-based ABA provider "
            "and one of the brands in LEARN Behavioral's network. LEARN "
            "Behavioral is majority-owned by Gryphon Investors (invested 2019), "
            "making Gryphon the ultimate private-equity owner."
        ),
    ),
    CuratedAcquisition(
        pe_firm_name="Gryphon Investors",
        portfolio_name="Trellis Services",
        source_url=(
            "https://learnbehavioral.com/careers/working-at-learn-behavioral"
            "#trellis-services"
        ),
        description=(
            "Trellis Services, a Maryland-based ABA provider, is a brand in LEARN "
            "Behavioral's network. LEARN Behavioral is majority-owned by Gryphon "
            "Investors (invested 2019), making Gryphon the private-equity owner."
        ),
    ),
    CuratedAcquisition(
        pe_firm_name="Gryphon Investors",
        portfolio_name="Tandem Therapy Services",
        source_url=(
            "https://learnbehavioral.com/careers/working-at-learn-behavioral"
            "#tandem-therapy-services"
        ),
        description=(
            "Tandem Therapy Services, a Nevada-based ABA provider, is a brand in "
            "LEARN Behavioral's network. LEARN Behavioral is majority-owned by "
            "Gryphon Investors (invested 2019), making Gryphon the PE owner."
        ),
    ),
    CuratedAcquisition(
        pe_firm_name="Gryphon Investors",
        portfolio_name="Behavior Analysis Center for Autism",
        source_url=(
            "https://learnbehavioral.com/careers/working-at-learn-behavioral"
            "#behavior-analysis-center-for-autism"
        ),
        description=(
            "Behavior Analysis Center for Autism (BACA), an Indiana ABA provider "
            "known for verbal-behavior specialization, joined LEARN Behavioral's "
            "network. LEARN is majority-owned by Gryphon Investors (invested "
            "2019), making Gryphon the ultimate private-equity owner."
        ),
    ),
    CuratedAcquisition(
        pe_firm_name="Gryphon Investors",
        portfolio_name="Priorities ABA",
        source_url=(
            "https://learnbehavioral.com/careers/working-at-learn-behavioral"
            "#priorities-aba"
        ),
        description=(
            "Priorities ABA is a brand in LEARN Behavioral's network. LEARN "
            "Behavioral is majority-owned by Gryphon Investors (invested 2019), "
            "making Gryphon the ultimate private-equity owner."
        ),
    ),
    CuratedAcquisition(
        # Stored under the full registered name "Total Spectrum Autism Services"
        # (not the shorter brand "Total Spectrum") so the name-prefix match stays
        # specific to LEARN's ABA entity and does not capture the unrelated
        # "Total Spectrum" counseling / speech / mental-health orgs in NPPES.
        pe_firm_name="Gryphon Investors",
        portfolio_name="Total Spectrum Autism Services",
        source_url=(
            "https://learnbehavioral.com/careers/working-at-learn-behavioral"
            "#total-spectrum"
        ),
        description=(
            "Total Spectrum (registered as Total Spectrum Autism Services), a "
            "Midwest ABA provider, is a brand in LEARN Behavioral's network. "
            "LEARN is majority-owned by Gryphon Investors (invested 2019), "
            "making Gryphon the ultimate private-equity owner."
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
