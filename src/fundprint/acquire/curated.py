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

from fundprint import db, fetch
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
        # Therapeutic Pathways is a Modesto, California ABA brand and one of the two
        # sub-brands (with The Behavior Center) that came with Trumpet Behavioral
        # Health when KKR-backed BlueSprig rolled Trumpet up in October 2023, so KKR
        # is the ultimate PE owner. Its centres are published only from BlueSprig's
        # own directory (they are not otherwise in the registry under this brand),
        # and the name is generic enough that it is marked directory_only so it is
        # never used to name-match the registry.
        pe_firm_name="KKR",
        portfolio_name="Therapeutic Pathways",
        source_url=(
            "https://bhbusiness.com/2023/10/18/"
            "kkr-backed-bluesprig-rolls-up-trumpet-behavioral-health-adds-nearly-40-"
            "locations-to-footprint/"
        ),
        description=(
            "Therapeutic Pathways, a California ABA provider, is a sub-brand of "
            "Trumpet Behavioral Health, which KKR-backed BlueSprig Pediatrics "
            "acquired in October 2023. KKR is the ultimate private-equity owner."
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
    # ---- Second discovery pass: ranked chains + known PE platforms -----------
    #
    # EXCLUDED on the controlling-interest rule (section 2 of the methodology:
    # ownership means the parent acquired, controls, or holds a controlling
    # interest). These have an institutional investor but the founders retain
    # control, so they are not published as owned:
    #   * My Favorite Therapists (22 sites) - 5th Century Partners took a MINORITY
    #     strategic stake (Feb 2025); the founding Katari family retains
    #     significant ownership and the founder remains CEO.
    #   * Able Kids Services (22) - MKH Capital Partners describes its own
    #     position as a "minority co-investment".
    #   * Forta / Montera Health (24) - Insight Partners led a minority Series A.
    #
    # EXCLUDED as not institutionally owned: Golden Steps ABA (founder-owned),
    # Verbal Beginnings (founder-owned), Applied ABC (clinician-owned), Cortica
    # (venture syndicate, no controlling firm), Elemy/Tilly (venture-backed, and
    # it exited direct clinical care entirely), Blue Balloon ABA (now Children's
    # Specialized ABA under the RWJBarnabas nonprofit hospital system), Bierman,
    # Stride, Intercare, Soar Health, Positive Behavior Supports Corp.
    #
    # EXCLUDED as not an autism/ABA chain: Elite DNA / DNA Comprehensive Therapy
    # (general behavioral health; ABA is one line among several), Developmental
    # Disabilities Resources (nonprofit I/DD residential and day supports).
    #
    # HELD for want of a confirmed CURRENT source: Kyo Autism Therapy (Norwest
    # invested in 2019 and no exit is reported, but nothing since 2021 restates
    # them as owner), Behavior Analysis Support Services (no investor found at
    # all), Autism Care Partners (Coppermine Capital is confirmed, but its legal
    # entity name could not be resolved, so its clinics cannot be matched),
    # Invo Healthcare (Leonard Green via The Stepping Stones Group, but Invo is a
    # multi-service school-staffing group and matching its legal name would
    # over-capture non-ABA services).
    CuratedAcquisition(
        # The single largest chain Fundprint was missing. It was previously left
        # out because trade chatter had it moving from FFL Partners to H.I.G.
        # Capital and the current owner was ambiguous. That chatter does not hold
        # up: FFL's own portfolio page still lists Autism Learning Partners among
        # its current holdings, in a section distinct from its labelled exits, and
        # H.I.G.'s portfolio does not list it at all. The primary source wins.
        pe_firm_name="FFL Partners",
        portfolio_name="Autism Learning Partners",
        source_url="https://www.fflpartners.com/portfolio",
        description=(
            "Autism Learning Partners, one of the largest US ABA providers, was "
            "acquired by FFL Partners in a $270M+ secondary buyout (Dec 2017) and "
            "remains listed among FFL's current portfolio holdings, separately "
            "from that page's exited companies. Reports of a sale to H.I.G. "
            "Capital could not be substantiated against any primary source."
        ),
    ),
    # NOTE on Autism Learning Partners' clinic count, which is 1 and looks broken.
    # It is not. ALP registers a single organization NPI, and its own website
    # publishes 199 "service area" pages rather than centers: each carries only a
    # city ("Taunton, MA, USA"), never a street address, because ALP delivers much
    # of its care in homes and schools. Ingesting those 199 as clinics is exactly
    # the error we refused for Key Autism. So ALP's ownership is published (that
    # is the sourced fact) while its center count stays at what public records
    # actually establish. This is the clearest live example of the dataset's rule:
    # coverage, not census.
    CuratedAcquisition(
        # Registers with the provider registry under its legal name, "VOCATIONAL
        # DEVELOPMENT GROUP, LLC", though it trades as InBloom Autism Services.
        # The legal name is stored so the name-prefix match finds its centers, the
        # same pattern as ACES ("ACES 2020, LLC").
        #
        # Elysium Management is Leon Black's family office, not a buyout fund, so
        # firm_type is family_office rather than private_equity.
        pe_firm_name="Elysium Management",
        portfolio_name="Vocational Development Group",
        firm_type="family_office",
        source_url=(
            "https://bhbusiness.com/2026/01/14/"
            "webster-equity-partners-sells-inbloom-autism-services/"
        ),
        description=(
            "InBloom Autism Services, which registers as Vocational Development "
            "Group, LLC, was sold by Webster Equity Partners to Elysium "
            "Management (the family office of Leon Black) for a reported $75M, "
            "announced January 2026. Elysium is a family office rather than a "
            "private-equity fund, and is labelled as such."
        ),
    ),
    CuratedAcquisition(
        pe_firm_name="Pharos Capital Group",
        portfolio_name="Behavior Care Specialists",
        source_url=(
            "https://www.bassberry.com/experience/"
            "pharos-capitals-family-treatment-network-acquires-behavior-care-specialists/"
        ),
        description=(
            "Behavior Care Specialists, a South Dakota ABA provider, was acquired "
            "by Pharos Capital Group through its Family Treatment Network platform "
            "(2018). Its centers now also carry the Catalyst Behavior Solutions "
            "brand, another Family Treatment Network company, and no subsequent "
            "sale of the platform is reported."
        ),
    ),
    CuratedAcquisition(
        pe_firm_name="Goldman Sachs Alternatives",
        portfolio_name="Center for Social Dynamics",
        source_url=(
            "https://nms-capital.com/news/"
            "goldman-sachs-alternatives-completes-acquisition-of-center-for-social-dynamics-"
            "from-nms-capital/"
        ),
        description=(
            "Center for Social Dynamics (CSD), a West Coast ABA provider, was "
            "acquired from NMS Capital by Goldman Sachs Alternatives, which took "
            "majority ownership in December 2024. NMS Capital re-invested and "
            "retains a minority stake."
        ),
    ),
    CuratedAcquisition(
        # Registers as "CAROLINA CENTER FOR AUTISM SERVICES, LLC"; trades as Kind
        # Behavioral Health. This is a search-fund (entrepreneurship-through-
        # acquisition) deal with a syndicate of investors, not a single buyer:
        # Highland Creek, Pacific Lake, Trilogy Search Partners and WSC & Company
        # all hold it. We attribute a single controlling owner, and that is WSC:
        # it describes itself as acquiring AND operating its companies, its own
        # portfolio page carries Kind as an operating company, and third-party
        # databases name WSC alone as the owner. An earlier version of this entry
        # named Trilogy only because Trilogy's portfolio page was curated first;
        # that named one minority co-investor out of four and understated the
        # lead, which the methodology's controlling-interest bar does not allow.
        # Recorded as `other` because a search-fund holding company is neither a
        # classic buyout fund, a pension, nor a family office.
        pe_firm_name="WSC & Company",
        portfolio_name="Carolina Center for Autism Services",
        firm_type="other",
        source_url="https://wscandcompany.com/portfolio/",
        description=(
            "Carolina Center for Autism Services (trading as Kind Behavioral "
            "Health), a North Carolina ABA provider, is carried as an operating "
            "company on the portfolio page of WSC & Company, a private investment "
            "firm that acquires and operates its holdings and is the lead of the "
            "search-fund syndicate behind Kind. Recorded as `other` because a "
            "search-fund holding company is neither a buyout fund, a pension, nor "
            "a family office."
        ),
    ),
    CuratedAcquisition(
        # Registers every Colorado center as "BUCK JACK LLC"; trades as Woven Care
        # (formerly Shandy Clinic). The legal name is what the registry carries, so
        # it is what the name-prefix linker has to match on, exactly as with
        # Vocational Development Group (InBloom) and Carolina Center for Autism
        # Services (Kind Behavioral Health).
        #
        # Anacapa's portfolio page marks Shandy Clinic `opc new`: its own markup
        # defines .opc as "Operating Companies" and .new as "Current", so the firm
        # is stating this is a current holding, and a sibling entry records that
        # Buck Jack Capital "partnered with Anacapa to acquire Shandy Clinic".
        # Recorded as `other`, not private equity: a search fund is neither a buyout
        # fund, a pension, nor a family office. Same call as Trilogy Search Partners.
        #
        # WHY THIS ONE IS IN SCOPE AND ELITE DNA IS NOT. Woven Care is a
        # multi-service pediatric provider (ABA, speech, OT, PT), and those are
        # normally excluded: Geode Health, Invo Healthcare and Elite DNA all are.
        # The test is whether ABA is a core line or an incidental one. Woven Care
        # holds 22 NPIs whose PRIMARY taxonomy is behavior analysis and carries an
        # ABA taxonomy at 22 of its 24 sites. Elite DNA (which registers as DNA
        # Comprehensive Therapy Services) has exactly ONE primary-ABA NPI out of 65.
        # One of those companies runs ABA at essentially every center; the other
        # offers it here and there alongside psychiatry.
        pe_firm_name="Anacapa Partners",
        portfolio_name="Buck Jack",
        firm_type="other",
        source_url="https://anacapapartners.com/site/global/anacapa/portfolio/",
        description=(
            "Woven Care (formerly Shandy Clinic), a Colorado pediatric therapy "
            "provider that runs applied behavior analysis at essentially all of "
            "its centers, is listed as a current operating company in the "
            "portfolio of Anacapa Partners, a search-fund investor, which acquired "
            "it through the search vehicle Buck Jack Capital. It registers with the "
            "provider registry as Buck Jack LLC. Recorded as `other` because a "
            "search fund is neither a buyout fund, a pension, nor a family office."
        ),
    ),
    CuratedAcquisition(
        # Rolls up to Zenyth Partners, which Fundprint already tracks through
        # Helping Hands Family. Mission is a separate registered brand, so it is
        # its own owner entity under the same parent firm.
        pe_firm_name="Zenyth Partners",
        portfolio_name="Mission Autism Clinics",
        source_url=(
            "https://bhbusiness.com/2024/11/11/"
            "helping-hands-family-acquires-mission-autism-clinics-grows-to-nearly-40-locations/"
        ),
        description=(
            "Mission Autism Clinics was acquired by Helping Hands Family in "
            "November 2024. Helping Hands Family is a portfolio company of Zenyth "
            "Partners, making Zenyth the ultimate private-equity owner."
        ),
    ),
    CuratedAcquisition(
        # Rolls up to Goldman Sachs Alternatives through Center for Social
        # Dynamics, added above.
        pe_firm_name="Goldman Sachs Alternatives",
        portfolio_name="Behavior Change Institute",
        source_url=(
            "https://www.prnewswire.com/news-releases/"
            "csd-acquires-premier-new-mexico-provider-bci-advancing-access-to-breakthrough-"
            "autism-and-behavioral-care-in-the-southwest-302692824.html"
        ),
        description=(
            "Behavior Change Institute (BCI), a New Mexico autism and behavioral "
            "care provider, was acquired by Center for Social Dynamics in "
            "February 2026. CSD is majority-owned by Goldman Sachs Alternatives, "
            "making it the ultimate owner."
        ),
    ),
    # NOT INGESTED, deliberately:
    #   * ABA Connect / Austin Connect to Wellness (16 sites, reported MBF
    #     Healthcare Partners II, Dec 2022). The only source found is a
    #     Businesswire release that returns 403 to every client we have, curl
    #     included, so it cannot be fetched and content-hashed. Revisit when a
    #     fetchable source (an MBF portfolio page, say) is found.
    #   * Patterns Behavioral Services (15, reported Webster Equity Partners via
    #     Redwood Family Care Network). Each hop is separately sourced but no
    #     single current source states that Patterns is a Webster company, and a
    #     three-hop inference is below the bar.
    #   * Lolly Therapeutics / PatKids (16, JoyBridge Kids / Frontline Healthcare
    #     Partners). Ownership is solid but its primary registry taxonomy is
    #     occupational therapy: a multidisciplinary pediatric practice where ABA
    #     is one service among several, not an ABA chain.
    # ---- Chains found by ranking the whole registry, then verifying owners ---
    # These were surfaced by ranking every ABA organization in the national
    # provider registry by site count and researching the owners of the largest
    # ones we did not already track. Most of the big untracked chains turned out
    # NOT to be institutionally owned (Bierman is clinician-owned, Stride is
    # family-owned, Intercare has been family-owned since 1979, Soar Health is
    # venture-backed) and are therefore correctly absent from this dataset. These
    # three are the ones with a private-equity owner and a fetchable public source.
    CuratedAcquisition(
        pe_firm_name="NexPhase Capital",
        portfolio_name="Behavior Frontiers",
        source_url=(
            "https://www.prnewswire.com/news-releases/"
            "behavior-frontiers-announces-sale-to-nexphase-capital-302445660.html"
        ),
        description=(
            "Behavior Frontiers, a national ABA provider, was sold by Lorient "
            "Capital to NexPhase Capital in May 2025. NexPhase is the current "
            "private-equity owner."
        ),
    ),
    CuratedAcquisition(
        pe_firm_name="Zenyth Partners",
        portfolio_name="Helping Hands Family",
        source_url=(
            "https://monroecap.com/press_release/"
            "monroe-capital-supports-zenyth-partners-helping-hands-family/"
        ),
        description=(
            "Helping Hands Family (HHF), a Mid-Atlantic ABA provider, was founded "
            "as a platform by Zenyth Partners in 2019 and remains a Zenyth "
            "portfolio company, confirmed in a November 2024 financing "
            "announcement describing HHF as 'an existing portfolio company of "
            "Zenyth Partners'."
        ),
    ),
    CuratedAcquisition(
        # The registry lists this chain under its legal name, "ALTERNATIVE
        # BEHAVIOR STRATEGIES, LLC", though it trades publicly as ABS Kids. The
        # legal name is stored so the name-prefix match finds its centers.
        #
        # This source sits behind a TLS-fingerprinting WAF and was unfetchable
        # until fundprint.fetch could retry through curl, so the entry was held
        # back rather than published on an unsnapshottable source.
        pe_firm_name="Petra Capital Partners",
        portfolio_name="Alternative Behavior Strategies",
        source_url=(
            "https://www.bassberry.com/experience/petra-capital-and-mmc-acquire-abs/"
        ),
        description=(
            "Alternative Behavior Strategies (ABS Kids), an ABA provider, was "
            "acquired by Petra Capital Partners through its MMC Health Services "
            "platform (2017). Petra Capital Partners is the private-equity owner."
        ),
    ),
    # ---- LEARN Behavioral's remaining brands (Gryphon Investors) -------------
    # LEARN publishes a single location roster covering all of its brands, and
    # labels every center with the brand that runs it. That roster (read by
    # fundprint.acquire.roster) is LEARN's own statement that these are its
    # centers, and Gryphon's portfolio page is the statement that LEARN is
    # Gryphon's. Both hops are sourced. These five brands were previously omitted
    # for want of a safe way to identify their centers in the provider registry;
    # the roster removes that need, because a roster does not have to be matched
    # by name, it is published by the owner.
    CuratedAcquisition(
        pe_firm_name="Gryphon Investors",
        portfolio_name="Wisconsin Early Autism Project",
        source_url=(
            "https://learnbehavioral.com/careers/working-at-learn-behavioral"
            "#wisconsin-early-autism-project"
        ),
        description=(
            "Wisconsin Early Autism Project (WEAP), a Wisconsin ABA provider, is "
            "a brand in LEARN Behavioral's network. LEARN Behavioral is "
            "majority-owned by Gryphon Investors (invested 2019), making Gryphon "
            "the private-equity owner."
        ),
    ),
    CuratedAcquisition(
        pe_firm_name="Gryphon Investors",
        portfolio_name="Little Leaves Behavioral Services",
        source_url=(
            "https://learnbehavioral.com/careers/working-at-learn-behavioral"
            "#little-leaves-behavioral-services"
        ),
        description=(
            "Little Leaves Behavioral Services, a Mid-Atlantic ABA provider, is a "
            "brand in LEARN Behavioral's network. LEARN Behavioral is "
            "majority-owned by Gryphon Investors (invested 2019), making Gryphon "
            "the private-equity owner."
        ),
    ),
    CuratedAcquisition(
        # Name is too generic for the provider registry: unrelated organizations
        # begin with "Behavioral Concepts". Marked directory_only, so it is linked
        # only from LEARN's own roster and never used to match the registry.
        pe_firm_name="Gryphon Investors",
        portfolio_name="Behavioral Concepts",
        source_url=(
            "https://learnbehavioral.com/careers/working-at-learn-behavioral"
            "#behavioral-concepts"
        ),
        description=(
            "Behavioral Concepts (BCI), a Massachusetts ABA provider, is a brand "
            "in LEARN Behavioral's network. LEARN Behavioral is majority-owned by "
            "Gryphon Investors (invested 2019), making Gryphon the private-equity "
            "owner."
        ),
    ),
    CuratedAcquisition(
        # Also too generic for the registry; directory_only. See above.
        pe_firm_name="Gryphon Investors",
        portfolio_name="SPARKS ABA",
        source_url=(
            "https://learnbehavioral.com/careers/working-at-learn-behavioral"
            "#sparks-aba"
        ),
        description=(
            "SPARKS ABA, an ABA provider, is a brand in LEARN Behavioral's "
            "network. LEARN Behavioral is majority-owned by Gryphon Investors "
            "(invested 2019), making Gryphon the private-equity owner."
        ),
    ),
    CuratedAcquisition(
        pe_firm_name="Gryphon Investors",
        portfolio_name="Behavioral Development and Educational Services",
        source_url=(
            "https://learnbehavioral.com/careers/working-at-learn-behavioral"
            "#behavioral-development-and-educational-services"
        ),
        description=(
            "Behavioral Development and Educational Services (BDES) is a brand in "
            "LEARN Behavioral's network. LEARN Behavioral is majority-owned by "
            "Gryphon Investors (invested 2019), making Gryphon the private-equity "
            "owner."
        ),
    ),
    CuratedAcquisition(
        # ACES (Comprehensive Educational Services), founded by Kristin Farmer,
        # received a strategic investment from General Atlantic in January 2020.
        # Its centers register in NPPES under the legal entity "ACES 2020, LLC"
        # (formed the year of the investment); one record's NPPES other-names are
        # literally "ACES" and "COMPREHENSIVE EDUCATIONAL SERVICES, INC.",
        # confirming the identity. Brand is stored as "ACES 2020" (not the bare
        # "ACES", which is too short and collides with unrelated dental,
        # anesthesia, and evaluation-service orgs) so the name-prefix match stays
        # specific to ACES's "ACES 2020, LLC" behavioral-health centers.
        pe_firm_name="General Atlantic",
        portfolio_name="ACES 2020",
        source_url=(
            "https://www.generalatlantic.com/media-article/"
            "aces-and-general-atlantic-announce-strategic-partnership/"
        ),
        description=(
            "ACES (Comprehensive Educational Services), a Western-US ABA "
            "provider founded by Kristin Farmer, received a strategic investment "
            "from General Atlantic (a global growth-equity firm) in January 2020. "
            "Its centers register under the legal entity 'ACES 2020, LLC', whose "
            "NPPES alternate names include 'ACES' and 'Comprehensive Educational "
            "Services, Inc.', making General Atlantic the private-equity owner."
        ),
    ),
]


def _fetch(url: str) -> tuple[bytes, str]:
    """Fetch a curated source URL. Returns (content_bytes, source_url).

    Goes through fundprint.fetch, which retries a TLS-fingerprint block (a 403 to
    httpx, a 200 to curl, same User-Agent) through curl without changing who we
    say we are. Several ownership sources -- law-firm deal pages in particular --
    sit behind such a WAF. If every client fails, the exception propagates and the
    entry is not staged: a source we cannot snapshot is a claim we do not publish.
    """
    return fetch.get(url), url


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
