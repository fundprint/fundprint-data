"""Ingest the curated-acquisitions list into staging.

Fetches and snapshots each verified ownership source, then writes a
staging_pe_portfolio_listing row so the deterministic resolver can turn it into
an owner -> PE-firm claim. See fundprint.acquire.curated for the curated list.

Usage:
    python scripts/run_acquire_curated.py
"""

import logging
import sys

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    level=logging.INFO,
)


def main() -> int:
    from fundprint.acquire.curated import ingest_curated

    summary = ingest_curated()
    logging.getLogger(__name__).info("curated ingest summary: %s", summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
