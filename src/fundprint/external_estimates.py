"""The published estimates Fundprint reconciles against.

A count nobody can check against anything is a curiosity. A count that disagrees
with a peer-reviewed estimate, and can say exactly where and why, is a finding.
This module records the outside estimates verbatim so the disagreement is
computed from their numbers rather than from our memory of their numbers.

Two rules, both learned from `platforms.py`:

1. **Their numbers are stored unedited.** No rescaling, no "adjusting" their
   count to our basis. Where the two are not like-for-like, that is disclosed in
   the reconciliation, never fixed by quietly moving one of them.
2. **The source is fetched and content-hashed** like any other source, by
   `scripts/build_reconciliation.py`. We cite the JAMA page as canonical and
   hash the open-access PubMed Central copy, because a claim we cannot snapshot
   does not ship.

The reconciliation is a contribution, not a rebuttal. The Brown letter states
its own two limitations plainly: it cannot show PE's share of all sites, and it
is likely undercounting. Fundprint exists to supply the first and to measure the
second. Any framing that treats the disagreement as an error on their part is
both wrong and useless.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Canonical citation. Paywalled abstract page; the PMC copy below is open.
BROWN_CANONICAL_URL = "https://jamanetwork.com/journals/jamapediatrics/fullarticle/2843100"
#: The copy we fetch and hash. Open access, stable, and carries the full text.
BROWN_SOURCE_URL = "https://pmc.ncbi.nlm.nih.gov/articles/PMC12771383/"


@dataclass(frozen=True)
class ExternalEstimate:
    """One published estimate of PE ownership in autism services."""

    key: str
    title: str
    authors: str
    venue: str
    published: str
    canonical_url: str
    source_url: str
    #: The unit their count is in, in their words. Not translated to ours.
    unit: str
    #: The date their count describes, which is not the date it was published.
    as_of: str
    sites: int
    acquisitions: int
    states: int
    #: Their per-state counts, for the states they published.
    top_states: dict[str, int] = field(default_factory=dict)
    #: Their method, in one sentence, so a reader can see why the gap exists.
    method: str = ""
    #: Their own stated limitations, quoted. This is the fairest possible framing
    #: of the disagreement: they named it first.
    stated_limitations: str = ""
    notes: list[str] = field(default_factory=list)


BROWN_2026 = ExternalEstimate(
    key="brown_2026",
    title="Private Equity in Autism Services",
    authors=(
        "Arnold DR, Reddy M, Cantor J, McBain RK, Yu H, Whaley CM, Singh Y"
    ),
    venue="JAMA Pediatrics",
    published="2026-01-05",
    canonical_url=BROWN_CANONICAL_URL,
    source_url=BROWN_SOURCE_URL,
    unit="ASD service delivery sites owned by private equity",
    as_of="2024-12-31",
    sites=574,
    acquisitions=147,
    states=42,
    top_states={"CA": 97, "TX": 81, "CO": 38, "IL": 36, "FL": 36},
    method=(
        "PitchBook acquisitions from 2015-01-01 to 2024-12-31 filtered on "
        "healthcare plus the keywords autism, autism treatment facilities and "
        "ABA therapy services, then manually verified and expanded in mid-2025 "
        "from press releases and company websites."
    ),
    stated_limitations=(
        "we are unable to show PE's percentage of all ASD service delivery "
        "sites, and we are likely undercounting PE acquisitions, the latter of "
        "which is generally true of studies that attempt to fully-track PE "
        "activity"
    ),
    notes=[
        "Nearly 80% of the acquisitions (117 of 147) fell in 2018-2022.",
        "16 states had one or no PE-owned sites.",
        "The letter reports only its five largest states by site count, so a "
        "full per-state reconciliation is not possible from the published text.",
    ],
)

ESTIMATES: list[ExternalEstimate] = [BROWN_2026]
