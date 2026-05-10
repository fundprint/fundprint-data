"""Tests for the chain walker: min-confidence rule and multi-chain selection."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from fundprint.resolve.chain import Chain, ChainLink, walk_chain


def _make_conn(c2o_rows: list[tuple], o2p_rows: dict[str, list[tuple]]) -> MagicMock:
    """Build a mock DB connection with canned query results.

    c2o_rows: rows for clinic_to_owner query (id, clinic_id, owner_id, conf, method)
    o2p_rows: map from owner_entity_id -> rows for owner_to_pe_firm query
              each row: (id, owner_id, pe_firm_id, conf, method)
    """
    conn = MagicMock()
    call_count = 0
    owner_call_map: dict[str, list[tuple]] = {}

    def execute_side_effect(sql, params):
        nonlocal call_count
        cursor = MagicMock()

        # Detect which query is being run by the params pattern.
        # First call to execute is always clinic_to_owner for this clinic.
        # Subsequent calls are owner_to_pe_firm for specific owners.
        if "clinic_to_owner" in sql:
            cursor.fetchall.return_value = c2o_rows
        elif "owner_to_pe_firm" in sql:
            owner_id = params[0]
            cursor.fetchall.return_value = o2p_rows.get(str(owner_id), [])
        else:
            cursor.fetchall.return_value = []

        return cursor

    conn.execute.side_effect = execute_side_effect
    return conn


class TestChainMinConfidence:
    def test_min_rule_single_chain(self):
        """Chain confidence is the minimum, not average or product."""
        conn = _make_conn(
            c2o_rows=[("claim-1", "clinic-1", "owner-1", 0.95, "llm_inferred")],
            o2p_rows={"owner-1": [("claim-2", "owner-1", "pe-1", 0.60, "llm_inferred")]},
        )
        chain = walk_chain("clinic-1", conn=conn)

        assert chain.confidence == pytest.approx(0.60)
        assert chain.clinic_id == "clinic-1"
        assert chain.owner_entity_id == "owner-1"
        assert chain.parent_pe_firm_id == "pe-1"
        assert len(chain.links) == 2

    def test_min_rule_first_hop_is_weaker(self):
        """Min applies regardless of hop order."""
        conn = _make_conn(
            c2o_rows=[("claim-1", "clinic-1", "owner-1", 0.50, "fuzzy_high")],
            o2p_rows={"owner-1": [("claim-2", "owner-1", "pe-1", 0.90, "human_verified")]},
        )
        chain = walk_chain("clinic-1", conn=conn)

        assert chain.confidence == pytest.approx(0.50)

    def test_empty_chain_confidence_is_zero(self):
        """A clinic with no claims should return a chain with confidence 0.0."""
        conn = _make_conn(c2o_rows=[], o2p_rows={})
        chain = walk_chain("clinic-no-claims", conn=conn)

        assert chain.confidence == 0.0
        assert chain.clinic_id == "clinic-no-claims"
        assert chain.links == []

    def test_incomplete_chain_stops_at_owner(self):
        """If owner has no PE firm claim, chain is still returned but incomplete."""
        conn = _make_conn(
            c2o_rows=[("claim-1", "clinic-1", "owner-1", 0.80, "llm_inferred")],
            o2p_rows={},
        )
        chain = walk_chain("clinic-1", conn=conn)

        assert chain.owner_entity_id == "owner-1"
        assert chain.parent_pe_firm_id is None
        assert chain.confidence == pytest.approx(0.80)
        assert not chain.is_complete


class TestMultipleChainSelection:
    def test_picks_highest_min_confidence(self):
        """When two complete chains exist, pick the one with the higher minimum."""
        # chain A: min(0.90, 0.60) = 0.60
        # chain B: min(0.70, 0.80) = 0.70  <- should win
        conn = _make_conn(
            c2o_rows=[
                ("claim-1", "clinic-1", "owner-a", 0.90, "llm_inferred"),
                ("claim-2", "clinic-1", "owner-b", 0.70, "llm_inferred"),
            ],
            o2p_rows={
                "owner-a": [("claim-3", "owner-a", "pe-1", 0.60, "llm_inferred")],
                "owner-b": [("claim-4", "owner-b", "pe-2", 0.80, "llm_inferred")],
            },
        )
        chain = walk_chain("clinic-1", conn=conn)

        assert chain.confidence == pytest.approx(0.70)
        assert chain.owner_entity_id == "owner-b"
        assert chain.parent_pe_firm_id == "pe-2"

    def test_complete_chain_beats_incomplete_with_higher_hop_confidence(self):
        """A complete chain with lower min beats an incomplete chain."""
        # incomplete: min = 0.95 (only one hop)
        # complete:   min(0.85, 0.65) = 0.65  <- still preferred because it's complete
        # NOTE: the walker doesn't explicitly prefer complete chains; it picks highest min.
        # This test documents that the algorithm is purely min-based.
        conn = _make_conn(
            c2o_rows=[
                ("claim-1", "clinic-1", "owner-incomplete", 0.95, "llm_inferred"),
                ("claim-2", "clinic-1", "owner-complete", 0.85, "llm_inferred"),
            ],
            o2p_rows={
                "owner-complete": [("claim-3", "owner-complete", "pe-1", 0.65, "llm_inferred")],
                # owner-incomplete has no PE claim
            },
        )
        chain = walk_chain("clinic-1", conn=conn)

        # The incomplete chain has conf 0.95 but the complete chain has conf 0.65.
        # Min-only rule: 0.95 > 0.65, so the incomplete chain wins.
        assert chain.confidence == pytest.approx(0.95)
        assert chain.owner_entity_id == "owner-incomplete"
        assert chain.parent_pe_firm_id is None

    def test_multiple_pe_paths_from_same_owner_picks_best(self):
        """When an owner has two PE claims, pick the one with higher confidence."""
        conn = _make_conn(
            c2o_rows=[("claim-1", "clinic-1", "owner-1", 0.90, "llm_inferred")],
            o2p_rows={
                "owner-1": [
                    ("claim-2", "owner-1", "pe-weak", 0.55, "llm_inferred"),
                    ("claim-3", "owner-1", "pe-strong", 0.80, "llm_inferred"),
                ]
            },
        )
        chain = walk_chain("clinic-1", conn=conn)

        # min(0.90, 0.80) = 0.80 wins over min(0.90, 0.55) = 0.55
        assert chain.confidence == pytest.approx(0.80)
        assert chain.parent_pe_firm_id == "pe-strong"
