"""Tests for the Hugging Face export: Parquet writes and data card content."""

from __future__ import annotations

from unittest.mock import MagicMock

from fundprint.publish.hf import build_data_card, build_parquet_files


def _mock_conn(claim_rows=None, clinic_rows=None):
    conn = MagicMock()
    results = [
        MagicMock(fetchall=MagicMock(return_value=claim_rows or [])),
        MagicMock(fetchall=MagicMock(return_value=clinic_rows or [])),
    ]
    conn.execute.side_effect = results
    return conn


class TestBuildDataCard:
    def test_front_matter_contains_all_four_versions(self):
        card = build_data_card(
            dataset_version="2026.05.10",
            schema_version="1.0.0",
            resolver_version="0.2.0",
            methodology_version="v1.1",
        )
        assert "dataset_version: 2026.05.10" in card
        assert "schema_version: 1.0.0" in card
        assert "resolver_version: 0.2.0" in card
        assert "methodology_version: v1.1" in card

    def test_front_matter_is_yaml_block(self):
        card = build_data_card(
            dataset_version="2026.05.10",
            schema_version="1.0.0",
            resolver_version="0.1.0",
            methodology_version="v1.0",
        )
        # YAML front-matter must start and end with ---
        assert card.startswith("---\n")
        lines = card.split("\n")
        closing_idx = next(i for i, line in enumerate(lines[1:], 1) if line.strip() == "---")
        assert closing_idx > 1

    def test_data_card_mentions_known_limitations(self):
        card = build_data_card(
            dataset_version="2026.05.10",
            schema_version="1.0",
            resolver_version="0.1",
            methodology_version="v1",
        )
        assert "Known limitations" in card or "known limitations" in card.lower()

    def test_data_card_has_contact_section(self):
        card = build_data_card(
            dataset_version="2026.05.10",
            schema_version="1.0",
            resolver_version="0.1",
            methodology_version="v1",
        )
        assert "contact" in card.lower() or "corrections" in card.lower()


class TestBuildParquetFiles:
    def test_creates_release_directory(self, tmp_path):
        conn = _mock_conn()
        release_dir = build_parquet_files(
            conn,
            dataset_version="2026.05.10",
            schema_version="1.0",
            resolver_version="0.1",
            methodology_version="v1",
            dist_dir=tmp_path / "dist",
        )
        assert release_dir.exists()
        assert release_dir.name == "2026.05.10"

    def test_readme_is_written(self, tmp_path):
        conn = _mock_conn()
        release_dir = build_parquet_files(
            conn,
            dataset_version="2026.05.10",
            schema_version="1.0",
            resolver_version="0.1",
            methodology_version="v1",
            dist_dir=tmp_path / "dist",
        )
        assert (release_dir / "README.md").exists()

    def test_readme_contains_all_four_versions(self, tmp_path):
        conn = _mock_conn()
        release_dir = build_parquet_files(
            conn,
            dataset_version="2026.05.10",
            schema_version="1.2",
            resolver_version="0.3",
            methodology_version="v2.0",
            dist_dir=tmp_path / "dist",
        )
        content = (release_dir / "README.md").read_text()
        assert "2026.05.10" in content
        assert "1.2" in content
        assert "0.3" in content
        assert "v2.0" in content

    def test_parquet_files_written(self, tmp_path):
        conn = _mock_conn()
        release_dir = build_parquet_files(
            conn,
            dataset_version="2026.05.10",
            schema_version="1.0",
            resolver_version="0.1",
            methodology_version="v1",
            dist_dir=tmp_path / "dist",
        )
        parquet_files = list(release_dir.glob("*.parquet"))
        assert len(parquet_files) >= 1
