"""Guards on the state files, where this project is easiest to discredit.

The state file puts a government dollar figure next to a count of PE-owned
clinics. Done carelessly that is an insinuation, and one bad sentence there
would cost more credibility than every correct number on the site earns. These
tests protect the three things that keep it evidence rather than argument.
"""

from __future__ import annotations

from fundprint import audits


class TestTheCentralCaveat:
    """The page says no audit blames an owner. It has to stay true."""

    def test_no_audit_attributes_findings_to_ownership(self):
        # If an audit ever does name owners, that is a real finding and it needs
        # its own release with its own language. Flipping this flag quietly would
        # leave the page asserting the opposite of its own data.
        offenders = [a.state for a in audits.AUDITS if a.attributes_to_ownership]
        assert offenders == [], (
            f"{offenders} now attribute findings to ownership; the state file's "
            "central caveat is false and the page must be rewritten"
        )

    def test_the_totals_report_the_caveat_as_a_number(self):
        # Surfaced as a count so the page can render "0 audits blaming an owner"
        # from data rather than from a hardcoded claim.
        assert audits.totals()["attributing_to_ownership"] == 0


class TestTheControlCase:
    """Maine is the state that ruins the easy narrative. It must not go missing."""

    def test_maine_is_present_and_published(self):
        me = audits.by_state().get("ME")
        assert me is not None, "the control case was removed"
        assert me.status == audits.PUBLISHED, "the control case was quietly demoted"

    def test_maine_carries_a_large_finding(self):
        # The point only lands because the finding is big. If it were small,
        # "no PE clinics there" would prove nothing. Maine is third of four at
        # $45.6M, so the guard is absolute size plus "not the smallest", not a
        # rank claim: an earlier draft of this file called it second-largest on
        # the public page, and that was simply wrong.
        me = audits.by_state()["ME"]
        others = [a.improper for a in audits.published() if a.state != "ME"]
        assert me.improper >= 25_000_000
        assert me.improper > min(others), (
            "Maine is now the smallest finding in the series, so it no longer "
            "demonstrates that a large finding does not imply PE presence"
        )


class TestBlockedAuditsShipNoFigures:
    def test_every_blocked_audit_states_why(self):
        for a in audits.AUDITS:
            if a.status == audits.BLOCKED:
                assert a.blocked_reason, f"{a.state}: blocked with no reason given"

    def test_blocked_figures_stay_out_of_the_totals(self):
        # An unverifiable number inside a verifiable total contaminates the total.
        t = audits.totals()
        blocked = [a for a in audits.AUDITS if a.status == audits.BLOCKED]
        assert t["improper"] == sum(a.improper for a in audits.published())
        for a in blocked:
            assert a.state not in t["states"]

    def test_massachusetts_is_the_known_blocked_case(self):
        ma = audits.by_state().get("MA")
        assert ma is not None and ma.status == audits.BLOCKED
        assert "403" in ma.blocked_reason


class TestRecordHygiene:
    def test_states_are_unique(self):
        codes = [a.state for a in audits.AUDITS]
        assert len(codes) == len(set(codes))

    def test_every_audit_is_sourced_and_dated(self):
        for a in audits.AUDITS:
            assert a.source_url.startswith("https://")
            assert a.issued and a.issuer and a.title
            assert a.findings, f"{a.state}: no findings recorded"

    def test_confirmed_and_potential_are_never_conflated(self):
        # Summing them would inflate every headline. They are separate fields and
        # separate totals precisely so no template can add them by accident.
        t = audits.totals()
        assert t["improper"] != t["potentially_improper"]
        assert t["improper"] == sum(a.improper for a in audits.published())

    def test_spend_growth_runs_forwards(self):
        for a in audits.AUDITS:
            if a.spend_growth:
                (y0, d0), (y1, d1) = a.spend_growth
                assert y0 < y1 and d1 > d0, f"{a.state}: growth pair is backwards"
