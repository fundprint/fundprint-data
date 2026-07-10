"""Brand-targeted NPPES acquire pass.

A single broad NPPES taxonomy query is capped at 1200 records, so it cannot
surface every location of every chain. This pass instead queries NPPES once per
tracked ABA brand by organization name (with the trailing wildcard the registry
needs), giving each brand its own record budget. That captures locations the
broad pull misses. Florida Autism Center, for example, has roughly 80 NPPES
locations but only a handful surfaced in the taxonomy pull.

Each brand here corresponds to an owner entity the pipeline already tracks and
attributes to a parent financial firm with a public source. The list is
deliberately narrow: we pull brands we can attribute, not the open web. Brands
that are out of scope (for example, a parent firm's non-ABA portfolio company)
or no longer operating under a current owner (for example, a defunct chain shown
only for its history) are intentionally excluded.

The pull is idempotent: NppesScraper.run() skips a fetch whose content hash is
already stored, so re-running only ingests genuinely new registry data. Staged
rows that do not brand-prefix-match any owner are simply left unlinked by the
resolver, exactly like any other unmatched provider record.

Usage:
    python scripts/run_acquire_nppes_brands.py
    python scripts/run_acquire_nppes_brands.py --only "Florida Autism Center"
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

# Tracked ABA brands, each attributable to a parent firm the dataset publishes.
# Kept in sync with the owner entities created by the portfolio and curated
# resolvers. Not included: Geode Health (out of scope, mental health) and CARD
# (former Blackstone brand, shown only for its ownership history).
TRACKED_BRANDS: list[str] = [
    "Action Behavior Centers",
    "Hopebridge",
    "Blue Sprig",
    "Trumpet Behavioral Health",
    "Florida Autism Center",
    "Acorn Health",
    "Butterfly Effects",
    "Centria",
    "Behavioral Innovations",
    # Behavioral Innovations operates its Maryland and Colorado centers under the
    # legal entity "Monarch Behavioral Therapy BII, LLC" (NPPES alternate name
    # "Behavioral Innovations"), so it is pulled as its own brand.
    "Monarch Behavioral Therapy",
    # Proud Moments ABA -> Nautic Partners (acquired from Audax, Feb 2025).
    "Proud Moments",
    # Caravel Autism Health -> GTCR (acquired from Frazier Healthcare, 2024).
    "Caravel Autism Health",
    # Key Autism Services -> Cane Investment Partners (private investment firm).
    "Key Autism Services",
]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Pull NPPES locations for tracked ABA brands.")
    p.add_argument(
        "--only",
        default=None,
        help="Restrict the pass to a single brand name (must match TRACKED_BRANDS).",
    )
    p.add_argument(
        "--max-records",
        type=int,
        default=1200,
        help="Cap per brand (default 1200, the NPPES reachable maximum).",
    )
    return p


def main() -> int:
    args = build_parser().parse_args()

    from fundprint.acquire.nppes import NppesScraper

    brands = TRACKED_BRANDS
    if args.only:
        brands = [b for b in TRACKED_BRANDS if b.lower() == args.only.lower()]
        if not brands:
            logger.error("--only %r is not in TRACKED_BRANDS", args.only)
            return 1

    failures = 0
    for brand in brands:
        try:
            NppesScraper(
                organization_name=brand, max_records=args.max_records
            ).run()
        except Exception:
            logger.exception("brand pull failed for %s", brand)
            failures += 1

    logger.info("brand acquire complete: %d brand(s), %d failure(s)", len(brands), failures)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
