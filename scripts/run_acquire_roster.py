"""CLI for the owner-roster acquire pass (LEARN Behavioral, Caravel Autism Health).

Both chains publish their whole center list as structured data, and the provider
registry badly undercounts both. See fundprint.acquire.roster.

Usage:
    python scripts/run_acquire_roster.py --dry-run
    python scripts/run_acquire_roster.py --source learn
    python scripts/run_acquire_roster.py
"""

from __future__ import annotations

import argparse
import logging
import sys

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)


def main() -> int:
    from fundprint.acquire.roster import SOURCES, run

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--source",
        choices=sorted(SOURCES),
        default=None,
        help="Which roster to pull (default: all).",
    )
    p.add_argument("--dry-run", action="store_true", help="Report without writing.")
    args = p.parse_args()

    sources = [args.source] if args.source else sorted(SOURCES)
    failures = 0
    total = 0
    for name in sources:
        try:
            centers = run(name, dry_run=args.dry_run)
            total += len(centers)
        except Exception:
            logger.exception("roster failed for %s", name)
            failures += 1

    logger.info(
        "roster acquire complete: %d center(s) across %d source(s), %d failure(s)",
        total,
        len(sources),
        failures,
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
