"""Government audits of state Medicaid ABA spending, and the guardrail on using them.

A national count is a statistic; a state number sitting next to a state dollar
figure is a story a local reporter can run this week. That is why the state file
exists. It is also the single easiest place in this project to destroy its own
credibility, so the rules come before the data.

**No audit here attributes a dollar to private equity.** Every one of them audits
a *state Medicaid program*. They name no provider and no ownership type; they
find missing session notes, uncredentialed technicians, supervision ratios out of
compliance, and billing for naps, meals and holidays, across all providers in the
state. `attributes_to_ownership` is False on every record and there is a test that
it stays False. If an audit ever does name owners, that is a finding worth its own
release, not a flag to flip quietly.

**Maine is in this list precisely because it ruins the easy narrative.** It has a
$45.6 million finding, third-largest of the four published, and in our data *zero*
private-equity-owned clinics. Anyone
inclined to read "big audit finding" as "private equity did this" has to get past
Maine first. Dropping it would make the other four look like evidence of something
they are not evidence of, so it stays, and it stays visible.

**What the pairing legitimately supports** is the thing PESP's report already
recommends and that these audits keep demonstrating: this is a large, fast-growing
Medicaid spend whose oversight is weak, and the regulators auditing it *cannot see
who owns the providers*. Wisconsin is the sharpest case in the dataset: 48
private-equity-owned centers, of which the federal provider registry can see 2. An
auditor working from federal data there is working blind. That is an argument for
ownership transparency, and it is made without asserting anything the audits do not.

Figures are stored exactly as published, including the ones that superseded a
number this project previously carried. Where a report publishes both a confirmed
and a potentially-improper figure, both are kept: quoting only the larger would be
advocacy, quoting only the smaller would be false modesty.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: The audit's figures are published on the state file.
PUBLISHED = "published"
#: The finding is real and documented in reputable press, but its primary source
#: cannot be fetched or content-hashed, so its figures do NOT ship. Same rule that
#: keeps the ABA Connect ownership claim unpublished: plausible is not the bar,
#: snapshottable is. The state is listed with the reason instead of dropped.
BLOCKED = "blocked"


@dataclass(frozen=True)
class Audit:
    """One government audit of a state's Medicaid ABA payments."""

    state: str
    state_name: str
    #: The body that issued it. Not all are federal, and the difference matters.
    issuer: str
    title: str
    report_number: str | None
    issued: str
    #: The years of payments examined, not the years the report covers.
    period: str
    source_url: str
    #: Confirmed improper payments, in dollars, as published.
    improper: int
    #: Additional payments the report calls *potentially* improper, where it
    #: separates the two. Kept distinct; the two must never be summed silently.
    potentially_improper: int | None = None
    #: Federal share recommended for refund, where the report states one.
    federal_refund: int | None = None
    findings: list[str] = field(default_factory=list)
    #: Program spending growth the report cites, as (year, dollars) pairs. This is
    #: the context that makes the audits a trend rather than five separate events.
    spend_growth: tuple[tuple[str, int], tuple[str, int]] | None = None
    #: THE GUARDRAIL. True only if the audit itself attributes findings to an
    #: owner or an ownership type. It never has. See the module docstring.
    attributes_to_ownership: bool = False
    status: str = PUBLISHED
    #: Why a blocked audit is blocked. Required when status is BLOCKED.
    blocked_reason: str = ""
    note: str = ""


AUDITS: list[Audit] = [
    Audit(
        state="CO",
        state_name="Colorado",
        issuer="U.S. Department of Health and Human Services, Office of Inspector General",
        title=(
            "Colorado Made at Least $77.8 Million in Improper Fee-for-Service "
            "Medicaid Payments for Applied Behavior Analysis Provided to Children"
        ),
        report_number="A-09-24-02004",
        issued="2026-02-25",
        period="2022-2023",
        source_url=(
            "https://oig.hhs.gov/reports/all/2026/colorado-made-at-least-778-million-"
            "in-improper-fee-for-service-medicaid-payments-for-applied-behavior-"
            "analysis-provided-to-children/"
        ),
        improper=77_800_000,
        potentially_improper=112_542_978,
        federal_refund=42_649_438,
        findings=[
            "Every one of the 100 sampled enrollee-months contained at least one "
            "improper or potentially improper claim line.",
            "Payments for care delivered by behavioral technicians who were not "
            "credentialed as required.",
            "Billing for time that may not qualify as ABA therapy, including "
            "recreation, day care, custodial care, meals, breaks and naps.",
            "Session notes that did not support the billed CPT codes.",
        ],
        spend_growth=(("2019", 60_100_000), ("2023", 163_500_000)),
        note="The largest finding in the series, in the state with our largest "
        "tracked private-equity footprint outside Texas.",
    ),
    Audit(
        state="IN",
        state_name="Indiana",
        issuer="U.S. Department of Health and Human Services, Office of Inspector General",
        title=(
            "Indiana Made at Least $56 Million in Improper Fee-for-Service Medicaid "
            "Payments for Applied Behavior Analysis Provided to Children Diagnosed "
            "With Autism"
        ),
        report_number="A-09-22-02002",
        issued="2024-09-01",
        period="2019-2020",
        source_url=(
            "https://oig.hhs.gov/reports/all/2024/indiana-made-at-least-56-million-in-"
            "improper-fee-for-service-medicaid-payments-for-applied-behavior-analysis-"
            "provided-to-children-diagnosed-with-autism/"
        ),
        improper=56_000_000,
        findings=[
            "Every one of the 100 sampled enrollee-months contained at least one "
            "improper or potentially improper claim line.",
        ],
        note="The first in the series, and the report that established the "
        "sampling design the later audits reuse.",
    ),
    Audit(
        state="WI",
        state_name="Wisconsin",
        issuer="U.S. Department of Health and Human Services, Office of Inspector General",
        title=(
            "Wisconsin Made at Least $18.5 Million in Improper Fee-For-Service "
            "Medicaid Payments for Applied Behavior Analysis Provided to Children "
            "Diagnosed With Autism"
        ),
        # The landing page states the findings and recommendations but not the
        # report number. Left null rather than guessed at.
        report_number=None,
        issued="2025-07-01",
        period="2021-2022",
        source_url=(
            "https://oig.hhs.gov/reports/all/2025/wisconsin-made-at-least-185-million-"
            "in-improper-fee-for-service-medicaid-payments-for-applied-behavior-"
            "analysis-provided-to-children-diagnosed-with-autism/"
        ),
        improper=18_500_000,
        potentially_improper=94_300_000,
        federal_refund=12_200_000,
        findings=[
            "Every one of the 100 sampled enrollee-months contained at least one "
            "improper or potentially improper claim line.",
            "OIG recommended the state give ABA facilities additional guidance on "
            "documenting ABA and perform periodic statewide post-payment review.",
        ],
        spend_growth=(("2018", 39_900_000), ("2022", 53_700_000)),
        note="The sharpest ownership-visibility case in the dataset: 48 tracked "
        "private-equity-owned centres, of which the federal registry sees 2.",
    ),
    Audit(
        state="MA",
        state_name="Massachusetts",
        # Not a federal audit. Conflating the two would be an easy and unforced
        # error, so the issuer is carried on every record and shown on the page.
        issuer="Massachusetts Office of the Inspector General, Healthcare Division",
        title=(
            "MassHealth's Applied Behavior Analysis Program: Service Providers "
            "(2024 Annual Report)"
        ),
        report_number=None,
        issued="2024-03-01",
        period="not stated on the release",
        source_url=(
            "https://www.mass.gov/news/inspector-general-estimates-masshealth-overpaid-"
            "up-to-173-million-to-service-providers-for-children-with-autism"
        ),
        # Figures are recorded for the record but NOT published: status is BLOCKED.
        # They come from press accounts of the report, not from the report itself.
        improper=17_300_000,
        findings=[
            "$16,761,445 of the total was billed for services that did not meet the "
            "required 10-to-1 supervision ratio for paraprofessional staff.",
            "Roughly $440,000 in claims that 'impossibly billed' 24 hours of "
            "continuous service.",
        ],
        status=BLOCKED,
        blocked_reason=(
            "mass.gov returns HTTP 403 to every request, including from curl and "
            "including its own robots.txt, so the report cannot be fetched or "
            "content-hashed. The 2025 annual report filed with the legislature is "
            "fetchable but does not carry the ABA findings. The finding is well "
            "documented in local press; a claim we cannot snapshot still does not "
            "ship as a figure."
        ),
        note="A state inspector general, not the federal OIG. The $16.7M figure "
        "carried in earlier drafts of our own plan is the supervision-ratio "
        "component of a total estimated at up to $17.3M. Both are correct and "
        "they are not alternatives.",
    ),
    Audit(
        state="ME",
        state_name="Maine",
        issuer="U.S. Department of Health and Human Services, Office of Inspector General",
        title=(
            "HHS-OIG Audit Finds Maine Made At Least $45.6 Million in Improper "
            "Medicaid Payments for Autism Services"
        ),
        report_number=None,
        issued="2026-01-22",
        period="2019-2023",
        source_url=(
            "https://oig.hhs.gov/newsroom/news-releases-articles/hhs-oig-audit-finds-"
            "maine-made-at-least-456-million-in-improper-medicaid-payments-for-autism-"
            "services/"
        ),
        improper=45_600_000,
        federal_refund=28_700_000,
        findings=[
            "Children received services without the required comprehensive "
            "assessment, or with assessments missing required signatures.",
            "Session documentation lacked service descriptions, goals addressed, or "
            "data collected.",
        ],
        spend_growth=(("2019", 52_200_000), ("2023", 80_600_000)),
        note="THE CONTROL CASE. A $45.6M finding, larger than two of the other "
        "audited states, sits where Fundprint tracks zero private-equity-owned "
        "clinics. It is "
        "published for exactly that reason: it is the state that most clearly "
        "shows these audits are not measuring private-equity ownership.",
    ),
]


def by_state() -> dict[str, Audit]:
    return {a.state: a for a in AUDITS}


def published() -> list[Audit]:
    """Audits whose figures ship, because their source can be content-hashed."""
    return [a for a in AUDITS if a.status == PUBLISHED]


def totals() -> dict:
    """Headline figures across the series. Confirmed and potential stay apart.

    Totals cover PUBLISHED audits only. A blocked audit's figures are recorded in
    this module for the record but must never reach a total, or an unverifiable
    number ends up inside a verifiable one.
    """
    pub = published()
    return {
        "audits": len(pub),
        "states": sorted(a.state for a in pub),
        "improper": sum(a.improper for a in pub),
        "potentially_improper": sum(a.potentially_improper or 0 for a in pub),
        "federal_refund": sum(a.federal_refund or 0 for a in pub),
        "federal_audits": len([a for a in pub if a.issuer.startswith("U.S.")]),
        "blocked": len([a for a in AUDITS if a.status == BLOCKED]),
        # If this is ever non-zero, the state file's central caveat is wrong and
        # the page must be rewritten before it ships.
        "attributing_to_ownership": len([a for a in AUDITS if a.attributes_to_ownership]),
    }
