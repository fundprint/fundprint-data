"""Tests for release tagging and manifest immutability."""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

import pytest

from fundprint.publish.release import ReleaseManifest, pin_release


class TestPinRelease:
    def test_creates_manifest_file(self, tmp_path):
        m = pin_release(
            "1.0.0", "0.1.0", "v1.0",
            dist_dir=tmp_path / "dist",
        )
        manifest_path = tmp_path / "dist" / m.dataset_version / "manifest.json"
        assert manifest_path.exists()

    def test_manifest_contains_all_four_versions(self, tmp_path):
        m = pin_release(
            "1.0.0", "0.2.0", "v1.1",
            dist_dir=tmp_path / "dist",
        )
        data = json.loads(
            (tmp_path / "dist" / m.dataset_version / "manifest.json").read_text()
        )
        assert data["schema_version"] == "1.0.0"
        assert data["resolver_version"] == "0.2.0"
        assert data["methodology_version"] == "v1.1"
        assert "dataset_version" in data

    def test_dataset_version_matches_date_format(self, tmp_path):
        m = pin_release(
            "1.0.0", "0.1.0", "v1.0",
            dist_dir=tmp_path / "dist",
        )
        # Accept YYYY.MM.DD or YYYY.MM.DD-N
        assert re.match(r"^\d{4}\.\d{2}\.\d{2}(-\d+)?$", m.dataset_version)

    def test_validation_run_id_captured(self, tmp_path):
        run_id = str(uuid.uuid4())
        m = pin_release(
            "1.0.0", "0.1.0", "v1.0",
            validation_run_id=run_id,
            dist_dir=tmp_path / "dist",
        )
        assert m.validation_run_id == run_id

    def test_returns_release_manifest_instance(self, tmp_path):
        m = pin_release(
            "1.0.0", "0.1.0", "v1.0",
            dist_dir=tmp_path / "dist",
        )
        assert isinstance(m, ReleaseManifest)


class TestImmutability:
    def test_raises_on_overwrite_without_force(self, tmp_path):
        # Simulate a pre-existing release directory + manifest, as would happen
        # if someone tried to re-run a publish step for a pinned version.
        dist = tmp_path / "dist"
        release_dir = dist / "2026.05.10"
        release_dir.mkdir(parents=True)
        existing_manifest = release_dir / "manifest.json"
        existing_manifest.write_text("{}")
        # _unique_version skips "2026.05.10" (it exists) to "2026.05.10-2",
        # so to hit the immutability guard we need that directory to also
        # have a manifest. Pre-create it too.
        release_dir_2 = dist / "2026.05.10-2"
        release_dir_2.mkdir(parents=True)
        (release_dir_2 / "manifest.json").write_text("{}")
        # Now pre-create the -3 directory as well so _unique_version lands on -3.
        # Instead, directly test by patching _unique_version to return an existing dir.
        from unittest.mock import patch
        with patch("fundprint.publish.release._unique_version", return_value="2026.05.10"):
            with pytest.raises(FileExistsError):
                pin_release("1.0.0", "0.1.0", "v1.0", dist_dir=dist)

    def test_force_flag_allows_overwrite(self, tmp_path):
        dist = tmp_path / "dist"
        release_dir = dist / "2026.05.10"
        release_dir.mkdir(parents=True)
        (release_dir / "manifest.json").write_text("{}")

        from unittest.mock import patch
        with patch("fundprint.publish.release._unique_version", return_value="2026.05.10"):
            # force=True should not raise even though manifest exists.
            m = pin_release("1.0.0", "0.1.0", "v1.0", dist_dir=dist, force=True)
        assert isinstance(m, ReleaseManifest)

    def test_second_release_same_day_gets_suffix(self, tmp_path):
        m1 = pin_release("1.0.0", "0.1.0", "v1.0", dist_dir=tmp_path / "dist")
        m2 = pin_release("1.0.0", "0.1.0", "v1.0", dist_dir=tmp_path / "dist")
        assert m1.dataset_version != m2.dataset_version
        assert m2.dataset_version.endswith("-2")

    def test_third_release_gets_incremented_suffix(self, tmp_path):
        dist = tmp_path / "dist"
        m1 = pin_release("1.0", "0.1", "v1", dist_dir=dist)
        m2 = pin_release("1.0", "0.1", "v1", dist_dir=dist)
        m3 = pin_release("1.0", "0.1", "v1", dist_dir=dist)
        assert m2.dataset_version.endswith("-2")
        assert m3.dataset_version.endswith("-3")

    def test_overwrite_error_message_mentions_immutability(self, tmp_path):
        dist = tmp_path / "dist"
        release_dir = dist / "2026.05.10"
        release_dir.mkdir(parents=True)
        (release_dir / "manifest.json").write_text("{}")

        from unittest.mock import patch
        with patch("fundprint.publish.release._unique_version", return_value="2026.05.10"):
            with pytest.raises(FileExistsError, match="immutable"):
                pin_release("1.0.0", "0.1.0", "v1.0", dist_dir=dist)
