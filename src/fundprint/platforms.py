"""The platform denominator: every known PE-backed ABA platform, covered or not.

A clinic count with no denominator invites the question "out of how many?" and has
no answer. This module supplies one. It enumerates the *platforms* (the operating
companies a financial sponsor actually buys), so coverage can be stated as
"N of M known PE-backed ABA platforms" rather than as a bare pile of clinics.

The unit is deliberately the platform, not the deal and not the clinic:

- A **deal** is the wrong unit because one platform absorbs many deals. Gryphon's
  LEARN Behavioral is eleven brands from at least six transactions; counting deals
  would say we cover LEARN six times and Caravel once.
- A **clinic** is the wrong unit for coverage because it is the thing being
  measured. Stating "we have 1,621 of an unknown total" is not a coverage claim.

**The spine is PESP's own appendix**, "Private equity-backed ABA providers"
(pages 22-25 of the April 2026 report). Using someone else's published list as
the denominator is the point: we do not get to draw our own finish line. It is
fetched and content-hashed by `scripts/build_platform_denominator.py` exactly
like any other source, so the denominator is auditable rather than asserted.

**The list is wrong in both directions, and that is the finding.** PESP omits
platforms we publish (Caravel Autism Health, 79 clinics, is absent from a table
titled "the largest private equity-backed ABA providers"), and it contains
platforms we do not publish. Both halves are recorded here. Naming our own gaps
is the whole value of the exercise; a denominator that flattered us would be
worthless.

`pesp_facilities` is **PESP's** number, never ours. It is retained unedited so a
reader can diff the two counts per platform. PESP states its own table "is likely
an undercount of providers, locations, and staff."

Status vocabulary, kept small on purpose:

covered       We publish this platform.
not_started   In scope and publishable. We simply have not done it. The honest gap.
blocked       In scope, but a specific documented obstacle prevents publication
              (no fetchable source, or the registry legal name cannot be found).
mixed_scope   Not primarily an ABA clinic operator. A staffing agency, a school
              operator, or a diversified rehab group that happens to own ABA
              brands. Only the named ABA sub-brands would ever be in scope.
out_of_scope  Fails a methodology rule outright: in-home only (operates no
              clinics), or no controlling institutional owner (venture minority).

Only covered + not_started + blocked count toward the denominator. mixed_scope and
out_of_scope are listed with reasons rather than silently dropped, because a
denominator you can shrink by redefinition is not a denominator.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# The PESP appendix, fetched and hashed by the build script.
PESP_REPORT_URL = (
    "https://pestakeholder.org/wp-content/uploads/2026/04/PESP_Report_PE-in-ABA_2026.pdf"
)
PESP_APPENDIX_TITLE = "Appendix: Private equity-backed ABA providers"
PESP_AS_OF = "2026-04"

COVERED = "covered"
NOT_STARTED = "not_started"
BLOCKED = "blocked"
MIXED_SCOPE = "mixed_scope"
OUT_OF_SCOPE = "out_of_scope"

#: Statuses that count toward the denominator of in-scope platforms.
IN_SCOPE_STATUSES = frozenset({COVERED, NOT_STARTED, BLOCKED})


@dataclass(frozen=True)
class Platform:
    """One PE-backed ABA platform and our coverage of it."""

    name: str
    investors: list[str]
    status: str
    note: str
    #: PESP's facility count. None where PESP lists "In home"/"In school" or
    #: where the platform is not in PESP's appendix at all.
    pesp_facilities: int | None = None
    #: True when the platform appears in PESP's appendix table.
    in_pesp: bool = True
    #: owner_entity.name values in our database, for the ones we publish.
    fundprint_owners: list[str] = field(default_factory=list)
    #: Sub-brands, as PESP lists them. Kept for the reconciliation (D3).
    other_brands: list[str] = field(default_factory=list)
    #: Source for a claim PESP does not carry (used for our own additions).
    source_url: str | None = None


PLATFORMS: list[Platform] = [
    # ------------------------------------------------------------------
    # Covered: in PESP's appendix and published by Fundprint.
    # ------------------------------------------------------------------
    Platform(
        name="Action Behavior Centers",
        investors=["Charlesbank Capital Partners", "Antares Capital"],
        status=COVERED,
        pesp_facilities=403,
        fundprint_owners=["Action Behavior Centers"],
        note="Read from ABC's own directory. Our 412 and PESP's 403 are the "
        "closest independent agreement in the table.",
    ),
    Platform(
        name="BlueSprig Pediatrics",
        investors=["KKR"],
        status=COVERED,
        pesp_facilities=155,
        fundprint_owners=[
            "Blue Sprig",
            "Florida Autism Center",
            "Trumpet Behavioral Health",
            "Therapeutic Pathways",
        ],
        other_brands=[
            "Florida Autism Center",
            "Trumpet Behavioral Health",
            "Therapeutic Pathways",
            "The Behavior Center",
            "Lone Star ABA",
            "Social Skills Playhouse",
            "The Shape of Behavior",
            "Thrive",
            "Verbal Behavior Consulting",
            "Tangible Differences Learning Center",
            "West Texas Autism Center",
        ],
        note="One directory, five brands. PESP names seven more sub-brands than "
        "bluesprigautism.com currently lists as centers; worth checking in D3.",
    ),
    Platform(
        name="LEARN Behavioral",
        investors=["Gryphon Investors", "PineBridge Investments"],
        status=COVERED,
        pesp_facilities=133,
        fundprint_owners=[
            "Autism Spectrum Therapies",
            "Total Spectrum Autism Services",
            "Wisconsin Early Autism Project",
            "Behavioral Concepts",
            "Little Leaves Behavioral Services",
            "Behavior Analysis Center for Autism",
            "Trellis Services",
            "SPARKS ABA",
            "Priorities ABA",
            "Tandem Therapy Services",
            "Behavioral Development and Educational Services",
        ],
        other_brands=["Creative Learning Center"],
        note="Eleven brands under one WP Store Locator. PESP lists a Creative "
        "Learning Center brand we do not carry.",
    ),
    Platform(
        name="Behavioral Innovations",
        investors=["Tenex Capital Management", "Yukon Partners"],
        status=COVERED,
        pesp_facilities=127,
        fundprint_owners=["Behavioral Innovations", "Monarch Behavioral Therapy"],
        note="Texas-heavy. The federal registry sees four of these; the gap is "
        "the lead example in the undercount argument.",
    ),
    Platform(
        name="Hopebridge",
        investors=["Arsenal Capital Partners"],
        status=COVERED,
        pesp_facilities=112,
        fundprint_owners=["Hopebridge"],
        other_brands=["Autism in Motion"],
        note="We publish 101, the number Hopebridge's own directory lists. PESP's "
        "112 includes 'opening soon' sites, which we exclude as not yet operating.",
    ),
    Platform(
        name="Proud Moments ABA",
        investors=["Nautic Partners"],
        status=COVERED,
        pesp_facilities=98,
        fundprint_owners=["Proud Moments"],
        note="",
    ),
    Platform(
        name="ABS Kids",
        investors=[
            "Petra Capital Partners",
            "Altos Health",
            "Morgan Stanley Expansion Capital",
        ],
        status=COVERED,
        pesp_facilities=87,
        fundprint_owners=["Alternative Behavior Strategies"],
        note="Registry legal name is Alternative Behavior Strategies. Diagnostic-"
        "only sites excluded as out of scope.",
    ),
    Platform(
        name="Acorn Health",
        investors=["Ontario Teachers' Pension Plan"],
        status=COVERED,
        pesp_facilities=70,
        fundprint_owners=["Acorn Health"],
        other_brands=["Concord Foundations Network"],
        note="Exact agreement: 70 and 70. A pension fund, not private equity, and "
        "labelled as such.",
    ),
    Platform(
        name="Autism Learning Partners",
        investors=["FFL Partners"],
        status=COVERED,
        pesp_facilities=64,
        fundprint_owners=["Autism Learning Partners"],
        note="We publish 45 centers with street addresses. PESP's 64 likely "
        "includes in-home service areas, which carry no clinic.",
    ),
    Platform(
        name="Centria Healthcare",
        investors=["Thomas H. Lee Partners", "Michigan Economic Development"],
        status=COVERED,
        pesp_facilities=64,
        fundprint_owners=["Centria"],
        other_brands=["Applied Behavioral Associates"],
        note="Exact agreement: 64 and 64, reached from opposite directions after "
        "the registry's residential rows were replaced by the owner's list.",
    ),
    Platform(
        name="ACES ABA",
        investors=["General Atlantic"],
        status=COVERED,
        pesp_facilities=59,
        fundprint_owners=["ACES 2020"],
        note="",
    ),
    Platform(
        name="Helping Hands Family",
        investors=["Zenyth Partners"],
        status=COVERED,
        pesp_facilities=52,
        fundprint_owners=["Helping Hands Family", "Mission Autism Clinics"],
        other_brands=["Mission Autism Clinics"],
        note="",
    ),
    Platform(
        name="Behavior Frontiers",
        investors=["NexPhase Capital", "Churchill Asset Management"],
        status=COVERED,
        pesp_facilities=28,
        fundprint_owners=["Behavior Frontiers"],
        note="We publish 54 against PESP's 28. Reading the owner's directory "
        "roughly doubled it.",
    ),
    Platform(
        name="InBloom Autism Services",
        investors=["Elysium Management"],
        status=COVERED,
        pesp_facilities=27,
        fundprint_owners=["Vocational Development Group"],
        note="Family office, not private equity. Registry legal name is "
        "Vocational Development Group.",
    ),
    Platform(
        name="Center for Social Dynamics",
        investors=["Goldman Sachs Asset Management", "CD Private Equity", "NMS Capital"],
        status=COVERED,
        pesp_facilities=22,
        fundprint_owners=["Center for Social Dynamics", "Behavior Change Institute"],
        other_brands=[
            "Behavior Change Institute",
            "JF Behavioral Services",
            "Ed Support Services",
            "Rocky Mountain Applied Behavior Analysts",
            "South Sound Behavior Therapy",
            "Behavior & Development Center",
        ],
        note="PESP names four sub-brands we do not carry. A D3 target.",
    ),
    Platform(
        name="Key Autism Services",
        investors=["Cane Investment Partners", "SierraStone Capital"],
        status=COVERED,
        pesp_facilities=22,
        fundprint_owners=["Key Autism Services"],
        note="Published with 0 clinics: in-home model. PESP counts 22 facilities; "
        "these are one registered office per state, not therapy centers.",
    ),
    Platform(
        name="Kind Behavioral Health",
        investors=[
            "WSC & Company",
            "Highland Creek Partners",
            "Pacific Lake Partners",
            "Trilogy Search Partners",
        ],
        status=COVERED,
        pesp_facilities=20,
        fundprint_owners=["Carolina Center for Autism Services"],
        note="Search-fund owned, not private equity. Registry legal name is "
        "Carolina Center for Autism Services.",
    ),
    # ------------------------------------------------------------------
    # Covered, and absent from PESP's appendix. The denominator is wrong in
    # both directions; these are the other direction.
    # ------------------------------------------------------------------
    Platform(
        name="Caravel Autism Health",
        investors=["GTCR"],
        status=COVERED,
        in_pesp=False,
        fundprint_owners=["Caravel Autism Health"],
        note="79 clinics, absent from a table of the largest PE-backed ABA "
        "providers. Larger than eleven platforms PESP does list.",
    ),
    Platform(
        name="Catalyst Behavior Solutions",
        investors=["Pharos Capital Group"],
        status=COVERED,
        in_pesp=False,
        fundprint_owners=["Behavior Care Specialists"],
        note="24 clinics, absent from PESP. Rebranded from Behavior Care "
        "Specialists, which is the name the registry still carries.",
    ),
    Platform(
        name="Woven Care",
        investors=["Anacapa Partners"],
        status=COVERED,
        in_pesp=False,
        fundprint_owners=["Buck Jack"],
        note="12 clinics, absent from PESP. Search fund, not private equity. "
        "Registry legal name is Buck Jack LLC, the acquisition vehicle.",
    ),
    Platform(
        name="Butterfly Effects",
        investors=["Moran Capital Partners"],
        status=COVERED,
        in_pesp=False,
        fundprint_owners=["Butterfly Effects"],
        note="Published with 0 clinics: in-home model, and its registry rows were "
        "apartments. Ownership published, footprint correctly empty.",
    ),
    # ------------------------------------------------------------------
    # Not started: in scope, publishable, simply not done. The honest gap.
    # ------------------------------------------------------------------
    Platform(
        name="The TreeTop ABA",
        investors=["Betterment Capital"],
        status=NOT_STARTED,
        pesp_facilities=114,
        other_brands=["Discovery ABA"],
        note="The largest single gap: 114 facilities across 11 states, none of "
        "them published. Highest-value expansion target in the dataset.",
    ),
    Platform(
        name="Center for Autism & Related Disorders",
        investors=["Audax Private Equity", "Pantogran"],
        status=NOT_STARTED,
        pesp_facilities=105,
        note="We carry CARD only as history: the Blackstone buyout and 2023 "
        "bankruptcy, with 0 live clinics. It now operates under a Pantogran-led "
        "group with Audax. The current footprint is unpublished.",
    ),
    Platform(
        name="Cultivate Behavioral Health & Education",
        investors=["Imperial Capital Group"],
        status=NOT_STARTED,
        pesp_facilities=65,
        note="65 facilities across 11 states, entirely unpublished.",
    ),
    Platform(
        name="VersiCare Group",
        investors=["Seven Hills Capital", "Tenth Street Capital"],
        status=NOT_STARTED,
        pesp_facilities=58,
        note="58 facilities across FL, MI, KY, TN. Unpublished.",
    ),
    Platform(
        name="Lighthouse Autism Center",
        investors=["Barings", "Cerberus Capital Management"],
        status=NOT_STARTED,
        pesp_facilities=50,
        other_brands=["A Step Ahead Pediatric Therapy", "Access Behavior Analysis"],
        note="50 centers across the Midwest. Unpublished.",
    ),
    Platform(
        name="Already Autism Health",
        investors=["ACE & Company", "Triton Pacific Capital Partners"],
        status=NOT_STARTED,
        pesp_facilities=33,
        note="33 facilities across seven southeastern states. Unpublished.",
    ),
    Platform(
        name="360 Behavioral Health",
        investors=["DW Healthcare Partners"],
        status=NOT_STARTED,
        pesp_facilities=30,
        other_brands=["Passport to Adaptive Living"],
        note="30 California facilities. Unpublished.",
    ),
    Platform(
        name="Kyo",
        investors=["Norwest Venture Partners"],
        status=NOT_STARTED,
        pesp_facilities=26,
        other_brands=["Gateway Learning Group", "Songbird"],
        note="Previously held back because the Norwest investment dated to 2019 "
        "and nothing since 2021 restated it. PESP's April 2026 appendix lists "
        "Norwest as a current investor, which clears that objection.",
    ),
    # ------------------------------------------------------------------
    # Blocked: in scope, with a specific documented obstacle.
    # ------------------------------------------------------------------
    Platform(
        name="Autism Care Partners",
        investors=["Coppermine Capital"],
        status=BLOCKED,
        pesp_facilities=23,
        other_brands=["Puddingstone Place", "Autism Bridges"],
        note="Ownership is confirmed and PESP corroborates it. Blocked on the "
        "registry legal entity name, without which its clinics cannot be matched.",
    ),
    Platform(
        name="ABA Connect",
        investors=["MBF Healthcare Partners"],
        status=BLOCKED,
        in_pesp=False,
        source_url="https://www.mbfhealthcare.com/portfolio/",
        note="Blocked on provenance, not on truth. Its only ownership source is a "
        "Businesswire release that returns 403 even to curl, and a claim we "
        "cannot content-hash does not ship.",
    ),
    Platform(
        name="Patterns Behavioral Services",
        investors=["Webster Equity Partners"],
        status=BLOCKED,
        in_pesp=False,
        source_url="https://www.websterequitypartners.com/portfolio/",
        note="Blocked on inference depth. Establishing current ownership takes "
        "three hops and no single current source names it outright.",
    ),
    # ------------------------------------------------------------------
    # Mixed scope: owns ABA, but is not primarily an ABA clinic operator.
    # ------------------------------------------------------------------
    Platform(
        name="ChanceLight",
        investors=["The Halifax Group"],
        status=MIXED_SCOPE,
        pesp_facilities=97,
        other_brands=[
            "Ombudsman Educational Services",
            "Spectrum Center",
            "Atlantis Academy",
            "Inspire",
        ],
        note="Alternative and special education schools. Its sites are schools, "
        "not ABA therapy centers.",
    ),
    Platform(
        name="Sevita",
        investors=[
            "Centerbridge Partners",
            "Aeterna Capital Partners",
            "Equity Investment Group",
            "Duchossois Capital Management",
            "Finback Investment Partners",
        ],
        status=MIXED_SCOPE,
        pesp_facilities=34,
        other_brands=[
            "BrightSpring Health Services",
            "Futures Behavioral Therapy Center",
            "Pediatric Therapy Partners",
        ],
        note="A 40,000-employee diversified home and community health provider. "
        "Only its named ABA brands could ever be in scope.",
    ),
    Platform(
        name="The Stepping Stones Group",
        investors=["Leonard Green & Partners", "Crescent Capital Group", "Five Arrows"],
        status=MIXED_SCOPE,
        pesp_facilities=32,
        other_brands=["Invo HealthCare", "New England ABA", "Southcoast Autism Center"],
        note="Primarily a school therapy staffing agency across 30-plus brands. "
        "Staffing contracts are not clinics.",
    ),
    Platform(
        name="New Story",
        investors=["Audax Private Equity"],
        status=MIXED_SCOPE,
        pesp_facilities=59,
        other_brands=["River Rock Academy", "Applied Behavioral Services"],
        note="Special education schools with one ABA brand attached.",
    ),
    Platform(
        name="Ivy Rehab Network",
        investors=["Waud Capital Partners", "ACE & Company"],
        status=MIXED_SCOPE,
        pesp_facilities=36,
        other_brands=[
            "Coastal Behavior Consulting",
            "Ivy Rehab for Kids",
            "ABC Pediatric Therapy Network",
            "Coastline Therapy Group",
            "Little Steps Pediatric Therapy",
        ],
        note="Physical and pediatric therapy network with ABA sub-brands. Only "
        "those brands would be in scope.",
    ),
    Platform(
        name="FullBloom",
        investors=["Vistria Group"],
        status=MIXED_SCOPE,
        other_brands=["Catapult Learning"],
        note="In-school services. PESP lists no facility count for it either.",
    ),
    # ------------------------------------------------------------------
    # Out of scope: fails a methodology rule outright.
    # ------------------------------------------------------------------
    Platform(
        name="Alora Behavioral Health",
        investors=["Enhanced Healthcare Partners", "Riverside Credit Solutions"],
        status=OUT_OF_SCOPE,
        other_brands=["Howard Chudler & Associates"],
        note="PESP lists it as 'In home'. An in-home provider operates no clinics, "
        "the same rule under which we publish Key Autism and Butterfly Effects "
        "with a footprint of zero.",
    ),
    Platform(
        name="Cortica",
        investors=[
            "CVS Health Ventures",
            "Deerfield Management",
            "Morgan Health",
            "RA Capital Management",
            "Questa Capital",
        ],
        status=OUT_OF_SCOPE,
        pesp_facilities=25,
        other_brands=["Springtide Child Development", "Melmed Center"],
        note="Fifteen venture and strategic minority investors, no controlling "
        "institutional owner. Our methodology requires a controlling interest, so "
        "we exclude it. PESP includes it; this is a definitional disagreement, "
        "not an error on either side.",
    ),
    Platform(
        name="My Favorite Therapists",
        investors=["5th Century Partners"],
        status=OUT_OF_SCOPE,
        in_pesp=False,
        note="Minority stake, founders retain control.",
    ),
    Platform(
        name="Able Kids",
        investors=["MKH Capital Partners"],
        status=OUT_OF_SCOPE,
        in_pesp=False,
        note="Minority stake, founders retain control.",
    ),
    Platform(
        name="Forta",
        investors=["Insight Partners"],
        status=OUT_OF_SCOPE,
        in_pesp=False,
        other_brands=["Montera"],
        note="Venture minority stake, founders retain control.",
    ),
]


def by_status(status: str) -> list[Platform]:
    """All platforms with the given status, largest PESP count first."""
    rows = [p for p in PLATFORMS if p.status == status]
    return sorted(rows, key=lambda p: -(p.pesp_facilities or 0))


def in_scope() -> list[Platform]:
    """Platforms that count toward the coverage denominator."""
    return [p for p in PLATFORMS if p.status in IN_SCOPE_STATUSES]


def coverage() -> dict:
    """The headline coverage figures.

    `covered` over `in_scope` is the number that replaces the site's pending
    "platforms covered" slot. `unpublished_facilities` is PESP's own facility
    count for the platforms we do not cover, which is the honest size of what we
    are still missing.
    """
    scope = in_scope()
    covered = [p for p in scope if p.status == COVERED]
    missing = [p for p in scope if p.status != COVERED]
    return {
        "covered": len(covered),
        "in_scope": len(scope),
        "not_started": len([p for p in scope if p.status == NOT_STARTED]),
        "blocked": len([p for p in scope if p.status == BLOCKED]),
        "excluded": len([p for p in PLATFORMS if p.status not in IN_SCOPE_STATUSES]),
        "total_listed": len(PLATFORMS),
        "in_pesp_appendix": len([p for p in PLATFORMS if p.in_pesp]),
        "covered_absent_from_pesp": len(
            [p for p in PLATFORMS if p.status == COVERED and not p.in_pesp]
        ),
        "unpublished_facilities": sum(p.pesp_facilities or 0 for p in missing),
    }
