"""CLI for the NPPES bulk-registry acquire pass.

Ingests every location of every tracked ABA brand from the CMS monthly
dissemination archive, including the secondary practice locations the NPPES API
does not expose. See fundprint.acquire.nppes_bulk for why this exists.

Usage:
    python scripts/run_acquire_nppes_bulk.py --dry-run
    python scripts/run_acquire_nppes_bulk.py
    python scripts/run_acquire_nppes_bulk.py --archive .cache/nppes/monthly.zip
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--archive",
        type=Path,
        default=None,
        help="Path to an already-downloaded monthly zip (skips the ~1.1GB download).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Extract and report counts without writing anything.",
    )
    args = p.parse_args()

    from fundprint.acquire import nppes_bulk

    try:
        result = nppes_bulk.run(archive=args.archive, dry_run=args.dry_run)
    except Exception:
        logger.exception("bulk acquire failed")
        return 1

    logger.info(
        "bulk acquire complete: %d row(s) = %d primary + %d secondary location(s), "
        "from %d matched NPI(s); %d deactivated NPI(s) skipped",
        len(result.rows),
        len(result.rows) - result.secondary_locations,
        result.secondary_locations,
        result.matched_npis,
        result.deactivated_skipped,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
