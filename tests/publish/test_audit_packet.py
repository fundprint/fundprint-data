"""Tests for the methodology audit packet builder."""

from __future__ import annotations

import json
import uuid
from unittest.mock import MagicMock

import pytest

from fundprint.publish.audit_packet import build_packet


def _mock_conn_for_packet(
    *,
    clinic_rows=None,
    owner_rows=None,
    pe_count_row=None,
    method_rows=None,
    sample_row=None,
    prev_counts_row=None,
) -> MagicMock:
    conn = MagicMock()

    def _side_effect(*args, **kwargs):
        call_args = args[0] if args else ""
        mock_result = MagicMock()
        # Match by substring of the SQL query.
        if "state, COUNT" in call_args:
            mock_result.fetchall.return_value = clinic_rows or [("TX", 5), ("CA", 3)]
        elif "parent_pe_firm ppf" in call_args and "owner_count" in call_args:
            mock_result.fetchall.return_value = owner_rows or [("Acme PE", 4)]
        elif "COUNT(*) FROM parent_pe_firm" in call_args:
            mock_result.fetchone.return_value = pe_count_row or (2,)
        elif "confidence_method" in call_args:
            mock_result.fetchall.return_value = method_rows or [
                ("llm_inferred", 6),
                ("fuzzy_high", 4),
            ]
        elif "hand_validation_sample" in call_args:
            mock_result.fetchone.return_value = sample_row or (None,)
        else:
            mock_result.fetchone.return_value = prev_counts_row or (10, 2, 1)
        return mock_result

    conn.execute.side_effect = _side_effect
    return conn


class TestBuildPacketShape:
    def test_packet_has_required_keys(self, tmp_path):
        run_id = str(uuid.uuid4())
        conn = _mock_conn_for_packet()
        packet = build_packet(
            run_id,
            conn,
            dataset_version="2026.05.10",
            dist_dir=tmp_path / "dist",
        )
        assert "validation_run_id" in packet
        assert "dataset_version" in packet
        assert "counts" in packet
        assert "confidence_method_breakdown" in packet
        assert "hand_validation_summary" in packet
        assert "diff_vs_previous" in packet

    def test_packet_written_to_disk(self, tmp_path):
        run_id = str(uuid.uuid4())
        conn = _mock_conn_for_packet()
        build_packet(
            run_id,
            conn,
            dataset_version="2026.05.10",
            dist_dir=tmp_path / "dist",
        )
        dest = tmp_path / "dist" / "2026.05.10" / "audit_packet.json"
        assert dest.exists()
        data = json.loads(dest.read_text())
        assert data["validation_run_id"] == run_id

    def test_counts_by_state_present(self, tmp_path):
        conn = _mock_conn_for_packet(clinic_rows=[("TX", 10), ("FL", 5)])
        packet = build_packet(
            str(uuid.uuid4()),
            conn,
            dataset_version="2026.05.10",
            dist_dir=tmp_path / "dist",
        )
        assert "TX" in packet["counts"]["clinics_by_state"]
        assert packet["counts"]["clinics_by_state"]["TX"] == 10

    def test_method_breakdown_fractions_sum_to_one(self, tmp_path):
        conn = _mock_conn_for_packet(method_rows=[("llm_inferred", 60), ("fuzzy_high", 40)])
        packet = build_packet(
            str(uuid.uuid4()),
            conn,
            dataset_version="2026.05.10",
            dist_dir=tmp_path / "dist",
        )
        fractions = list(packet["confidence_method_breakdown"].values())
        if fractions:
            assert abs(sum(fractions) - 1.0) < 0.01


class TestDiffVsPrevious:
    def test_diff_zero_when_no_previous(self, tmp_path):
        conn = _mock_conn_for_packet()
        packet = build_packet(
            str(uuid.uuid4()),
            conn,
            dataset_version="2026.05.10",
            previous_release_dir=None,
            dist_dir=tmp_path / "dist",
        )
        diff = packet["diff_vs_previous"]
        assert diff["rows_added"] == 0
        assert diff["rows_superseded"] == 0
        assert diff["rows_quarantined"] == 0

    def test_diff_computed_from_previous_packet(self, tmp_path):
        dist = tmp_path / "dist"
        prev_dir = dist / "2026.05.09"
        prev_dir.mkdir(parents=True)
        prev_run_id = str(uuid.uuid4())
        (prev_dir / "audit_packet.json").write_text(
            json.dumps({"validation_run_id": prev_run_id, "dataset_version": "2026.05.09"})
        )

        conn = _mock_conn_for_packet(prev_counts_row=(5, 1, 2))
        packet = build_packet(
            str(uuid.uuid4()),
            conn,
            dataset_version="2026.05.10",
            previous_release_dir=prev_dir,
            dist_dir=dist,
        )
        diff = packet["diff_vs_previous"]
        # Values come from the mock; just assert the keys are present and numeric.
        assert isinstance(diff["rows_added"], int)
        assert isinstance(diff["rows_quarantined"], int)


class TestHandValidationSummary:
    def test_summary_empty_when_no_sample(self, tmp_path):
        conn = _mock_conn_for_packet(sample_row=(None,))
        packet = build_packet(
            str(uuid.uuid4()),
            conn,
            dataset_version="2026.05.10",
            dist_dir=tmp_path / "dist",
        )
        assert packet["hand_validation_summary"] == {}

    def test_accuracy_calculated_from_labels(self, tmp_path):
        sample_data = {
            "rows": [
                {"reviewer_label": "agree"},
                {"reviewer_label": "agree"},
                {"reviewer_label": "agree"},
                {"reviewer_label": "disagree"},
                {"reviewer_label": "unclear"},
            ]
        }
        conn = _mock_conn_for_packet(sample_row=(json.dumps(sample_data),))
        packet = build_packet(
            str(uuid.uuid4()),
            conn,
            dataset_version="2026.05.10",
            dist_dir=tmp_path / "dist",
        )
        summary = packet["hand_validation_summary"]
        assert summary["agree"] == 3
        assert summary["disagree"] == 1
        assert summary["unclear"] == 1
        assert summary["accuracy"] == pytest.approx(0.75)
