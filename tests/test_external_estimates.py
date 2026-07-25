"""Guards on the outside estimates we reconcile against.

The failure mode these exist for is quiet drift in somebody else's numbers. Our
own figures move every release, and it is a short step from updating ours to
"tidying" theirs so the two agree better. That would destroy the only thing the
reconciliation is for. These tests pin the published values, so changing one is a
deliberate act with a diff attached, not a side effect of a release.
"""

from __future__ import annotations

import pytest

from fundprint import external_estimates as X


class TestBrown2026:
    """The JAMA Pediatrics letter, as published. Do not edit to fit our numbers."""

    def test_headline_figures_are_as_published(self):
        e = X.BROWN_2026
        assert e.sites == 574
        assert e.acquisitions == 147
        assert e.states == 42

    def test_as_of_is_their_date_not_their_publication_date(self):
        # Their count describes 2024-12-31 and appeared 2026-01-05. Collapsing the
        # two would silently delete the elapsed-time caveat from the comparison,
        # which is the caveat most likely to be raised by a reviewer.
        e = X.BROWN_2026
        assert e.as_of == "2024-12-31"
        assert e.published == "2026-01-05"
        assert e.as_of < e.published

    def test_top_states_are_consistent_with_the_total(self):
        e = X.BROWN_2026
        assert sum(e.top_states.values()) < e.sites
        assert len(e.top_states) <= e.states

    def test_their_stated_limitations_are_recorded(self):
        # The fairest framing of the disagreement is that they named it first. If
        # this quote goes missing, the note reads as a rebuttal instead.
        lim = X.BROWN_2026.stated_limitations
        assert "percentage of all ASD service delivery" in lim
        assert "undercounting" in lim

    def test_the_hashed_source_is_the_open_access_copy(self):
        # The canonical citation is paywalled, so hashing it would snapshot a
        # gateway page rather than the paper. Cite one, hash the other.
        e = X.BROWN_2026
        assert e.canonical_url != e.source_url
        assert "pmc.ncbi.nlm.nih.gov" in e.source_url
        assert "jamanetwork.com" in e.canonical_url


class TestRegistry:
    def test_every_estimate_is_fetchable_and_dated(self):
        for e in X.ESTIMATES:
            assert e.source_url.startswith("https://")
            assert e.as_of and e.method and e.unit

    def test_keys_are_unique(self):
        keys = [e.key for e in X.ESTIMATES]
        assert len(keys) == len(set(keys))


@pytest.mark.parametrize("estimate", X.ESTIMATES, ids=lambda e: e.key)
def test_counts_are_positive(estimate):
    assert estimate.sites > 0
    assert estimate.states > 0
