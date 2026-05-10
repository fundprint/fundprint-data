"""Publish layer: HF export, dashboard views, methodology audit trail.

Public entrypoint is build_release(). Submodules are importable for
testing and one-off scripts.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fundprint.publish.audit_packet import build_packet
from fundprint.publish.hf import build_parquet_files
from fundprint.publish.release import ReleaseManifest, pin_release

logger = logging.getLogger(__name__)


def build_release(
    conn: Any,
    *,
    schema_version: str,
    resolver_version: str,
    methodology_version: str,
    validation_run_id: str,
    hf_repo_id: str | None = None,
    previous_release_dir: Path | None = None,
    dist_dir: Path | None = None,
    force: bool = False,
) -> ReleaseManifest:
    """Produce a full release: Parquet files, data card, audit packet, manifest.

    Returns the ReleaseManifest for the new release.
    If hf_repo_id is provided, attempts to upload to Hugging Face (stub if
    huggingface_hub is not installed).
    """
    if dist_dir is None:
        dist_dir = Path("dist/release")

    manifest = pin_release(
        schema_version,
        resolver_version,
        methodology_version,
        validation_run_id=validation_run_id,
        dist_dir=dist_dir,
        force=force,
    )
    dataset_version = manifest.dataset_version

    build_parquet_files(
        conn,
        dataset_version=dataset_version,
        schema_version=schema_version,
        resolver_version=resolver_version,
        methodology_version=methodology_version,
        dist_dir=dist_dir,
    )

    build_packet(
        validation_run_id,
        conn,
        dataset_version=dataset_version,
        previous_release_dir=previous_release_dir,
        dist_dir=dist_dir,
    )

    if hf_repo_id:
        from fundprint.publish.hf import upload_to_hf

        release_dir = dist_dir / dataset_version
        upload_to_hf(release_dir, repo_id=hf_repo_id, dataset_version=dataset_version)

    logger.info("Release %s complete. Artifacts at %s/%s", dataset_version, dist_dir, dataset_version)
    return manifest
