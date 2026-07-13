"""Tests for the cross-brand duplicate-site survivor rule (pure function)."""

from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

# The corrections are scripts, not package modules, so load it by path.
_SPEC = importlib.util.spec_from_file_location(
    "correct_cross_brand_sites",
    Path(__file__).resolve().parents[1] / "scripts" / "correct_cross_brand_sites.py",
)
_MOD = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MOD)
_survivor_sort_key = _MOD._survivor_sort_key


def row(
    rid: str,
    *,
    last_updated: date | None = None,
    directory: bool = False,
) -> dict:
    return {
        "id": rid,
        "registry_last_updated": last_updated,
        "source_types": {"owner_location_directory"} if directory else {"nppes_bulk"},
    }


def survivor(*rows: dict) -> str:
    return sorted(rows, key=_survivor_sort_key)[0]["id"]


class TestSurvivorRule:
    def test_fresher_registration_wins(self):
        # KKR's BlueSprig record at 2437 SE 17th St was re-certified in 2025; the
        # Florida Autism Center record at the same suite died in 2022.
        bluesprig = row("a", last_updated=date(2025, 10, 24))
        florida = row("b", last_updated=date(2022, 11, 10))
        assert survivor(florida, bluesprig) == "a"

    def test_the_owners_own_directory_beats_any_registry_record(self):
        # A directory listing is the owner saying the center is open today. The
        # registry only ever says it once existed, however recently it was touched.
        directory = row("a", directory=True)
        fresh_registry = row("b", last_updated=date(2026, 6, 1))
        assert survivor(fresh_registry, directory) == "a"

    def test_a_row_with_no_registration_date_loses_to_one_with_a_date(self):
        assert survivor(row("a"), row("b", last_updated=date(2019, 1, 1))) == "b"

    def test_ties_break_on_id_so_a_rebuild_reproduces_the_choice(self):
        d = date(2024, 1, 1)
        assert survivor(row("b", last_updated=d), row("a", last_updated=d)) == "a"

    def test_rule_does_not_favour_a_brand_only_the_evidence(self):
        # Same two brands, opposite freshness: the other one survives. The rule is
        # about which registration is alive, not which brand we like.
        stale_bluesprig = row("a", last_updated=date(2019, 3, 3))
        fresh_trumpet = row("b", last_updated=date(2026, 3, 4))
        assert survivor(stale_bluesprig, fresh_trumpet) == "b"
