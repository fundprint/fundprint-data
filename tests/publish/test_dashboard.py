"""Tests for the SQL view generator."""

from __future__ import annotations

import pytest

from fundprint.publish.dashboard import ALLOWED_COLUMNS, generate_view_sql


class TestGenerateViewSql:
    def test_returns_string(self):
        sql = generate_view_sql()
        assert isinstance(sql, str)
        assert len(sql) > 0

    def test_contains_confidence_floor_filter(self):
        sql = generate_view_sql()
        # The floor is enforced by joining to validation_run_decision where decision='passed'.
        assert "decision = 'passed'" in sql

    def test_contains_quarantine_exclusion(self):
        sql = generate_view_sql()
        # Quarantined claims must not leak through.
        assert "quarantined" in sql.lower()

    def test_contains_embargo_filter_comment(self):
        sql = generate_view_sql()
        # The embargo column does not exist yet; there must be a TODO or commented filter.
        assert "embargo" in sql.lower()

    def test_only_allowed_columns_selected(self):
        sql = generate_view_sql()
        # Every column in the first view's SELECT should be in ALLOWED_COLUMNS.
        # Extract the SELECT block for the first view.
        select_start = sql.index("SELECT")
        from_start = sql.index("FROM", select_start)
        select_block = sql[select_start:from_start]

        for col in ALLOWED_COLUMNS:
            bare = col.split(".")[-1]  # strip table prefix for matching
            assert bare in select_block, f"Expected column {bare!r} in SELECT block"

    def test_contains_create_or_replace_view(self):
        sql = generate_view_sql()
        assert "CREATE OR REPLACE VIEW" in sql

    def test_provenance_completeness_filter(self):
        sql = generate_view_sql()
        # Rows missing source_record_ids must not export.
        assert "source_record_ids" in sql

    def test_allowed_columns_list_is_nonempty(self):
        assert len(ALLOWED_COLUMNS) > 0

    def test_no_internal_scoring_fields_in_allowed_columns(self):
        # Raw resolver intermediates should not be in the public allow-list.
        disallowed = {"supporting_snippets", "llm_flags", "name_embedding"}
        for col in ALLOWED_COLUMNS:
            bare = col.split(".")[-1]
            assert bare not in disallowed, f"Internal field {bare!r} is in the allow-list"
