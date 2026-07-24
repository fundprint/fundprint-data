"""Invariants for the platform coverage denominator.

The denominator is a published claim about ourselves, so the things that would
quietly corrupt it get tests: a duplicate platform inflating M, a status typo
silently dropping a platform out of scope, or a covered platform that names no
owner and therefore proves nothing.
"""

from __future__ import annotations

import pytest

from fundprint import platforms as P

VALID_STATUSES = {
    P.COVERED,
    P.NOT_STARTED,
    P.BLOCKED,
    P.MIXED_SCOPE,
    P.OUT_OF_SCOPE,
}


class TestPlatformList:
    def test_names_are_unique(self):
        """A duplicate platform inflates both halves of the coverage fraction."""
        names = [p.name for p in P.PLATFORMS]
        assert len(names) == len(set(names))

    def test_every_status_is_known(self):
        """A typo'd status would silently vanish from the denominator."""
        unknown = {p.name: p.status for p in P.PLATFORMS if p.status not in VALID_STATUSES}
        assert unknown == {}

    def test_every_platform_names_an_investor(self):
        missing = [p.name for p in P.PLATFORMS if not p.investors]
        assert missing == []

    def test_covered_platforms_name_an_owner_entity(self):
        """`covered` is a claim we publish it; without an owner it proves nothing."""
        missing = [
            p.name for p in P.PLATFORMS if p.status == P.COVERED and not p.fundprint_owners
        ]
        assert missing == []

    def test_only_covered_platforms_claim_owners(self):
        """A platform we have not published must not point at published owners."""
        wrong = [
            p.name
            for p in P.PLATFORMS
            if p.status != P.COVERED and p.fundprint_owners
        ]
        assert wrong == []

    def test_non_covered_platforms_explain_themselves(self):
        """Naming a gap without a reason is not naming a gap."""
        silent = [p.name for p in P.PLATFORMS if p.status != P.COVERED and not p.note]
        assert silent == []

    def test_platforms_absent_from_pesp_carry_their_own_source(self):
        """If PESP does not vouch for it, something else must.

        A covered platform is vouched for by its published clinics; anything else
        we assert outside PESP's appendix needs its own citable source URL.
        """
        unsourced = [
            p.name
            for p in P.PLATFORMS
            if not p.in_pesp
            and p.status in {P.NOT_STARTED, P.BLOCKED}
            and not p.source_url
        ]
        assert unsourced == []


class TestCoverage:
    def test_in_scope_is_the_three_actionable_statuses(self):
        scope = P.in_scope()
        assert {p.status for p in scope} <= P.IN_SCOPE_STATUSES
        assert len(scope) == sum(
            len(P.by_status(s)) for s in (P.COVERED, P.NOT_STARTED, P.BLOCKED)
        )

    def test_covered_never_exceeds_in_scope(self):
        c = P.coverage()
        assert 0 < c["covered"] <= c["in_scope"] <= c["total_listed"]

    def test_the_parts_sum_to_the_whole(self):
        """Excluding a platform by redefinition must not shrink the denominator."""
        c = P.coverage()
        assert c["covered"] + c["not_started"] + c["blocked"] == c["in_scope"]
        assert c["in_scope"] + c["excluded"] == c["total_listed"]

    def test_unpublished_facilities_excludes_covered_platforms(self):
        """The gap figure counts what we are missing, never what we have."""
        c = P.coverage()
        expected = sum(
            p.pesp_facilities or 0
            for p in P.in_scope()
            if p.status != P.COVERED
        )
        assert c["unpublished_facilities"] == expected
        assert c["unpublished_facilities"] > 0

    def test_pesp_counts_are_never_invented(self):
        """A facility count must come from PESP's table or be absent."""
        invented = [
            p.name for p in P.PLATFORMS if p.pesp_facilities is not None and not p.in_pesp
        ]
        assert invented == []

    @pytest.mark.parametrize("status", sorted(VALID_STATUSES))
    def test_by_status_sorts_largest_first(self, status):
        rows = P.by_status(status)
        counts = [p.pesp_facilities or 0 for p in rows]
        assert counts == sorted(counts, reverse=True)
