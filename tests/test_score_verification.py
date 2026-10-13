"""Tests for the publishable verification summary (pure function)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "score_verification",
    Path(__file__).resolve().parents[1] / "scripts" / "score_verification.py",
)
_MOD = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MOD)
summarize = _MOD.summarize


def row(stratum: str, source: str, exists: str, **kw) -> dict:
    base = {
        "stratum": stratum,
        "sub_stratum": f"{stratum}/{source}",
        "exists": exists,
        "owner": "yes" if stratum != "unclaimed" else "",
        "firm": "yes" if stratum != "unclaimed" else "",
        "missed": "no" if stratum == "unclaimed" else "",
        "notes": "",
    }
    return {**base, **kw}


def test_cannot_tell_counts_against_us():
    s = summarize([row("pe", "registry", "yes"), row("pe", "registry", "unclear")])
    assert s["headline_pe"]["k"] == 1 and s["headline_pe"]["n"] == 2
    assert s["cannot_tell"] == 1


def test_headline_is_pe_only_not_pooled():
    # An oversampled, all-correct non_pe stratum must not lift the PE headline.
    rows = [row("pe", "directory", "yes"), row("pe", "directory", "closed")]
    rows += [row("non_pe", "directory", "yes") for _ in range(10)]
    s = summarize(rows)
    assert s["headline_pe"]["rate"] == 50.0
    assert s["all_published"]["rate"] > s["headline_pe"]["rate"]


def test_unlabelled_rows_are_not_reviewed_and_unclaimed_is_recall_only():
    rows = [
        row("pe", "registry", "yes"),
        row("pe", "registry", ""),
        row("unclaimed", "registry", "yes", missed="yes"),
    ]
    s = summarize(rows)
    assert (s["drawn"], s["reviewed"]) == (3, 2)
    assert s["all_published"]["n"] == 1
    assert s["recall_missed_owners"]["k"] == 1


def test_perfect_small_sample_still_reports_a_lower_bound_below_100():
    s = summarize([row("pe", "registry", "yes") for _ in range(20)])
    assert s["headline_pe"]["rate"] == 100.0
    assert s["headline_pe"]["ci95"][0] < 90.0
