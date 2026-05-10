"""Release tagging: pins four versions, generates dataset_version, writes manifest.

Releases are IMMUTABLE once written. Overwriting an existing manifest is refused
unless force=True, which should only be used to correct a publishing mistake - never
to retroactively change what was in a release.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path


@dataclass
class ReleaseManifest:
    """Pinned versions and metadata for one public release."""

    dataset_version: str
    schema_version: str
    resolver_version: str
    methodology_version: str
    validation_run_id: str
    released_on: str  # ISO date string


def pin_release(
    schema_version: str,
    resolver_version: str,
    methodology_version: str,
    *,
    validation_run_id: str | uuid.UUID | None = None,
    dist_dir: Path | None = None,
    force: bool = False,
) -> ReleaseManifest:
    """Generate a dataset_version, write manifest.json, and return the manifest.

    dataset_version is YYYY.MM.DD, or YYYY.MM.DD-N for the Nth same-day release.
    Refuses to overwrite an existing manifest unless force=True.
    """
    if dist_dir is None:
        dist_dir = Path("dist/release")

    today = date.today().strftime("%Y.%m.%d")
    dataset_version = _unique_version(today, dist_dir)

    release_dir = dist_dir / dataset_version
    release_dir.mkdir(parents=True, exist_ok=True)

    manifest_path = release_dir / "manifest.json"
    if manifest_path.exists() and not force:
        raise FileExistsError(
            f"Release manifest already exists at {manifest_path}. "
            "Releases are immutable. Use force=True only to correct a publishing mistake."
        )

    manifest = ReleaseManifest(
        dataset_version=dataset_version,
        schema_version=schema_version,
        resolver_version=resolver_version,
        methodology_version=methodology_version,
        validation_run_id=str(validation_run_id) if validation_run_id else "",
        released_on=date.today().isoformat(),
    )
    manifest_path.write_text(json.dumps(asdict(manifest), indent=2))
    return manifest


def _unique_version(base: str, dist_dir: Path) -> str:
    """Return base if no release with that name exists; otherwise base-2, base-3, etc."""
    if not (dist_dir / base).exists():
        return base

    n = 2
    while (dist_dir / f"{base}-{n}").exists():
        n += 1
    return f"{base}-{n}"
