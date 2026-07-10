"""Ingest owner location-directories (the second source of clinic existence).

Fetches and snapshots each center page from a tracked owner's public location
directory, then stages it into staging_bacb_provider (with no NPI) so the
deterministic clinic linker can brand-match and de-duplicate it against NPPES.
See fundprint.acquire.directory for the source list and provenance model.

Usage:
    python scripts/run_acquire_directory.py
    python scripts/run_acquire_directory.py --source bluesprig
"""

from __future__ import annotations

import argparse
import logging
import sys

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Ingest owner location directories.")
    p.add_argument(
        "--source",
        default=None,
        help="Restrict to a single directory source key (default: all).",
    )
    return p


def main() -> int:
    args = build_parser().parse_args()

    from fundprint.acquire.directory import ingest_directories

    try:
        summary = ingest_directories(args.source)
    except Exception:
        logger.exception("directory ingest failed")
        return 1

    logger.info("directory ingest summary: %s", summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
