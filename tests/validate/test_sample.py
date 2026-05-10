"""Tests for the hand-validation sample generator."""

from __future__ import annotations

import json
import uuid
from unittest.mock import MagicMock

from fundprint.validate.sample import SampleSheet, draw_sample


def _make_conn(rows: list) -> MagicMock:
    """Return a mock DB connection whose execute().fetchall() returns rows."""
    conn = MagicMock()
    conn.execute.return_value.fetchall.return_value = rows
    return conn


def _make_rows(n: int, method: str = "llm_inferred") -> list:
    """Generate fake resolution_claim rows in the shape draw_sample expects."""
    out = []
    for i in range(n):
        out.append((
            uuid.uuid4(),          # id
            "clinic_to_owner",     # claim_type
            0.85,                  # confidence_score
            method,                # confidence_method
            uuid.uuid4(),          # clinic_id
            uuid.uuid4(),          # owner_entity_id
            None,                  # parent_pe_firm_id
            None,                  # acquisition_event_id
            ["https://example.com/source"],  # source_urls
        ))
    return out


class TestDrawSample:
    def test_returns_sample_sheet(self, tmp_path):
        conn = _make_conn(_make_rows(10))
        sheet = draw_sample(str(uuid.uuid4()), conn, n=5, seed=42, samples_dir=tmp_path)
        assert isinstance(sheet, SampleSheet)

    def test_sample_capped_at_n(self, tmp_path):
        conn = _make_conn(_make_rows(200, "llm_inferred"))
        sheet = draw_sample(str(uuid.uuid4()), conn, n=50, seed=0, samples_dir=tmp_path)
        assert sheet.total_drawn <= 50

    def test_empty_pool_returns_zero_rows(self, tmp_path):
        conn = _make_conn([])
        run_id = str(uuid.uuid4())
        sheet = draw_sample(run_id, conn, n=100, seed=1, samples_dir=tmp_path)
        assert sheet.total_drawn == 0
        assert sheet.rows == []

    def test_seed_is_captured_in_sheet(self, tmp_path):
        conn = _make_conn(_make_rows(20))
        sheet = draw_sample(str(uuid.uuid4()), conn, n=10, seed=99, samples_dir=tmp_path)
        assert sheet.seed == 99

    def test_seed_reproducibility(self, tmp_path):
        """Same seed should produce the same sample IDs."""
        rows = _make_rows(50)
        run_id = str(uuid.uuid4())

        sheet1 = draw_sample(run_id + "_a", _make_conn(rows), n=20, seed=7, samples_dir=tmp_path)
        sheet2 = draw_sample(run_id + "_b", _make_conn(rows), n=20, seed=7, samples_dir=tmp_path)

        ids_1 = [r.claim_id for r in sheet1.rows]
        ids_2 = [r.claim_id for r in sheet2.rows]
        assert ids_1 == ids_2

    def test_different_seeds_produce_different_samples(self, tmp_path):
        rows = _make_rows(50)
        run_id = str(uuid.uuid4())

        sheet1 = draw_sample(run_id + "_c", _make_conn(rows), n=20, seed=1, samples_dir=tmp_path)
        sheet2 = draw_sample(run_id + "_d", _make_conn(rows), n=20, seed=2, samples_dir=tmp_path)

        ids_1 = set(r.claim_id for r in sheet1.rows)
        ids_2 = set(r.claim_id for r in sheet2.rows)
        # With 50 rows and n=20 it is extremely unlikely the two sets match exactly.
        assert ids_1 != ids_2

    def test_writes_json_file(self, tmp_path):
        run_id = str(uuid.uuid4())
        conn = _make_conn(_make_rows(10))
        draw_sample(run_id, conn, n=5, seed=0, samples_dir=tmp_path)

        dest = tmp_path / f"{run_id}.json"
        assert dest.exists()
        data = json.loads(dest.read_text())
        assert data["run_id"] == run_id
        assert "rows" in data

    def test_reviewer_label_blank_by_default(self, tmp_path):
        conn = _make_conn(_make_rows(5))
        sheet = draw_sample(str(uuid.uuid4()), conn, n=5, seed=0, samples_dir=tmp_path)
        for row in sheet.rows:
            assert row.reviewer_label is None

    def test_output_is_json_serializable(self, tmp_path):
        conn = _make_conn(_make_rows(10))
        sheet = draw_sample(str(uuid.uuid4()), conn, n=5, seed=0, samples_dir=tmp_path)
        serialized = json.dumps(sheet.to_dict(), default=str)
        assert isinstance(serialized, str)


class TestStratification:
    def test_all_methods_represented_when_possible(self, tmp_path):
        """All strata in the pool should appear in the sample."""
        rows = (
            _make_rows(20, "llm_inferred")
            + _make_rows(20, "fuzzy_high")
            + _make_rows(20, "exact_match")
        )
        conn = _make_conn(rows)
        sheet = draw_sample(str(uuid.uuid4()), conn, n=30, seed=5, samples_dir=tmp_path)

        methods = {r.confidence_method for r in sheet.rows}
        assert "llm_inferred" in methods
        assert "fuzzy_high" in methods
        assert "exact_match" in methods

    def test_small_pool_does_not_oversample(self, tmp_path):
        """n larger than pool size should not raise; just return all rows."""
        rows = _make_rows(5)
        conn = _make_conn(rows)
        sheet = draw_sample(str(uuid.uuid4()), conn, n=100, seed=0, samples_dir=tmp_path)
        assert sheet.total_drawn <= 5
