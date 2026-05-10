"""Tests for quarantine rule evaluation."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

from fundprint.validate.quarantine import should_quarantine


def _claim(llm_flags: list[str] | None = None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        claim_type="clinic_to_owner",
        confidence_score=0.90,
        llm_flags=llm_flags or [],
    )


def _ctx(**kwargs) -> dict:
    base = {
        "sources_contradict": False,
        "external_challenge": False,
        "reviewer_label": None,
    }
    base.update(kwargs)
    return base


class TestSourcesContradict:
    def test_fires_when_sources_contradict(self):
        q, reason = should_quarantine(_claim(), _ctx(sources_contradict=True))
        assert q is True
        assert reason == "sources_contradict"

    def test_does_not_fire_when_false(self):
        q, reason = should_quarantine(_claim(), _ctx(sources_contradict=False))
        assert q is False
        assert reason is None


class TestLlmFlag:
    def test_fires_when_claim_has_llm_flags(self):
        q, reason = should_quarantine(
            _claim(llm_flags=["source_contradicts_itself"]), _ctx()
        )
        assert q is True
        assert reason == "llm_flag"

    def test_fires_for_any_flag_value(self):
        q, reason = should_quarantine(_claim(llm_flags=["ambiguous_parent"]), _ctx())
        assert q is True
        assert reason == "llm_flag"

    def test_does_not_fire_with_empty_flags(self):
        q, reason = should_quarantine(_claim(llm_flags=[]), _ctx())
        assert q is False


class TestReviewerUnclear:
    def test_fires_when_label_is_unclear(self):
        q, reason = should_quarantine(_claim(), _ctx(reviewer_label="unclear"))
        assert q is True
        assert reason == "reviewer_unclear"

    def test_does_not_fire_when_agree(self):
        q, reason = should_quarantine(_claim(), _ctx(reviewer_label="agree"))
        assert q is False

    def test_does_not_fire_when_disagree(self):
        # disagree is a pipeline failure, not a quarantine trigger
        q, reason = should_quarantine(_claim(), _ctx(reviewer_label="disagree"))
        assert q is False

    def test_does_not_fire_when_label_is_none(self):
        q, reason = should_quarantine(_claim(), _ctx(reviewer_label=None))
        assert q is False


class TestExternalChallenge:
    def test_fires_when_challenged(self):
        q, reason = should_quarantine(_claim(), _ctx(external_challenge=True))
        assert q is True
        assert reason == "external_challenge"

    def test_does_not_fire_when_not_challenged(self):
        q, reason = should_quarantine(_claim(), _ctx(external_challenge=False))
        assert q is False


class TestPriority:
    def test_sources_contradict_wins_when_multiple_flags(self):
        # sources_contradict is checked first; that reason should be returned.
        q, reason = should_quarantine(
            _claim(llm_flags=["something"]),
            _ctx(sources_contradict=True, external_challenge=True),
        )
        assert reason == "sources_contradict"

    def test_llm_flag_beats_reviewer_unclear(self):
        q, reason = should_quarantine(
            _claim(llm_flags=["flag"]),
            _ctx(reviewer_label="unclear"),
        )
        assert reason == "llm_flag"

    def test_no_trigger_returns_false_none(self):
        q, reason = should_quarantine(_claim(), _ctx())
        assert q is False
        assert reason is None
