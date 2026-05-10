"""CLI entrypoint for the Resolve layer.

Usage:
    python scripts/run_resolve.py [--staging-table <table>] [--batch-size <n>]
"""

import argparse
import logging
import sys

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Run the Fundprint resolve pipeline over unprocessed staging rows.",
    )
    p.add_argument(
        "--staging-table",
        default="staging_bacb_provider",
        help="Staging table to read from (default: staging_bacb_provider)",
    )
    p.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Number of staging rows to process per run (default: 100)",
    )
    return p


def main() -> int:
    args = build_parser().parse_args()

    from fundprint.resolve.pipeline import run

    try:
        result = run(
            staging_table=args.staging_table,
            batch_size=args.batch_size,
        )
    except Exception:
        logger.exception("Resolve pipeline failed")
        return 1

    logger.info(
        "resolve finished: %d staged rows, %d claims written, %d skipped (idempotent), "
        "%d chains walked",
        result.staging_rows_processed,
        result.claims_written,
        result.claims_skipped_idempotent,
        result.chains_walked,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
