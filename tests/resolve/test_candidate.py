"""Tests for the Candidate stage: cosine SQL, ordering, top-K selection."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from fundprint.resolve.candidate import Candidate, get_candidates


def _make_mock_conn(table_results: dict[str, list[tuple]]) -> MagicMock:
    """Build a mock psycopg connection that returns pre-defined rows per table.

    table_results maps table name -> list of (id, table, distance, similarity).
    """
    conn = MagicMock()
    cursors = []
    for table in ["clinic", "owner_entity", "parent_pe_firm"]:
        cursor = MagicMock()
        cursor.fetchall.return_value = table_results.get(table, [])
        cursors.append(cursor)
    conn.execute.side_effect = cursors
    return conn


@pytest.fixture
def stub_embed():
    """Patch embed() to return a deterministic vector without network calls."""
    with patch("fundprint.resolve.candidate.embed") as mock_embed:
        mock_embed.return_value = ([[0.1] * 1024], "stub-0")
        yield mock_embed


class TestGetCandidates:
    def test_returns_candidates_sorted_by_similarity(self, stub_embed):
        conn = _make_mock_conn(
            {
                "clinic": [
                    ("clinic-uuid-1", "clinic", 0.10, 0.90),
                    ("clinic-uuid-2", "clinic", 0.30, 0.70),
                ],
                "owner_entity": [
                    ("owner-uuid-1", "owner_entity", 0.05, 0.95),
                ],
                "parent_pe_firm": [],
            }
        )
        results = get_candidates("Sunshine ABA", "Austin TX", conn=conn)

        assert len(results) == 3
        assert results[0].target_id == "owner-uuid-1"
        assert results[0].similarity == pytest.approx(0.95)
        assert results[1].target_id == "clinic-uuid-1"
        assert results[2].target_id == "clinic-uuid-2"

    def test_respects_top_k_limit(self, stub_embed):
        # 4 results across tables but top_k=2
        conn = _make_mock_conn(
            {
                "clinic": [
                    ("c1", "clinic", 0.10, 0.90),
                    ("c2", "clinic", 0.20, 0.80),
                ],
                "owner_entity": [
                    ("o1", "owner_entity", 0.15, 0.85),
                    ("o2", "owner_entity", 0.25, 0.75),
                ],
                "parent_pe_firm": [],
            }
        )
        results = get_candidates("Sunshine ABA", conn=conn, top_k=2)
        assert len(results) == 2
        # Should be the two highest-similarity entries
        assert results[0].similarity >= results[1].similarity

    def test_empty_tables_returns_empty_list(self, stub_embed):
        conn = _make_mock_conn({})
        results = get_candidates("Unknown Clinic", conn=conn)
        assert results == []

    def test_candidate_model_fields_populated(self, stub_embed):
        conn = _make_mock_conn(
            {
                "clinic": [("clinic-id-abc", "clinic", 0.12, 0.88)],
                "owner_entity": [],
                "parent_pe_firm": [],
            }
        )
        results = get_candidates("Test Clinic", conn=conn)
        assert len(results) == 1
        c = results[0]
        assert isinstance(c, Candidate)
        assert c.target_id == "clinic-id-abc"
        assert c.target_table == "clinic"
        assert c.distance == pytest.approx(0.12)
        assert c.similarity == pytest.approx(0.88)

    def test_cosine_sql_uses_operator(self, stub_embed):
        """Verify that the SQL issued to the DB uses the pgvector <=> operator."""
        conn = _make_mock_conn({})
        get_candidates("Clinic Name", conn=conn)

        # Each execute call should reference <=> for cosine distance
        for call_args in conn.execute.call_args_list:
            sql = call_args[0][0]
            assert "<=>" in sql, f"Expected <=> in SQL but got: {sql}"

    def test_locality_is_appended_to_embed_input(self, stub_embed):
        conn = _make_mock_conn({})
        get_candidates("My Clinic", "Dallas TX", conn=conn)
        stub_embed.assert_called_once_with(["My Clinic Dallas TX"])

    def test_no_locality_embeds_name_only(self, stub_embed):
        conn = _make_mock_conn({})
        get_candidates("My Clinic", conn=conn)
        stub_embed.assert_called_once_with(["My Clinic"])
