"""CLI entrypoint for the Acquire layer.

Usage:
    python scripts/run_acquire.py <source_family> [--firm <firm_name>]

source_family must be one of the registered scrapers. Import the scraper
modules to trigger @register decoration before calling registry.get().
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
        description="Run a registered Fundprint acquire scraper.",
    )
    p.add_argument(
        "source_family",
        help="Registered source_family key (e.g. bacb, sec_edgar, pe_portfolio)",
    )
    p.add_argument(
        "--firm",
        default="KKR",
        help="For pe_portfolio: which firm config to use (default: KKR)",
    )
    return p


def main() -> int:
    args = build_parser().parse_args()

    # Import scraper modules to trigger @register calls
    import fundprint.acquire.bacb  # noqa: F401
    import fundprint.acquire.nppes  # noqa: F401
    import fundprint.acquire.portfolio_pages  # noqa: F401
    import fundprint.acquire.sec_edgar  # noqa: F401
    from fundprint.acquire.registry import get

    try:
        scraper_cls = get(args.source_family)
    except KeyError as exc:
        logger.error("%s", exc)
        return 1

    # pe_portfolio accepts a --firm argument; other scrapers ignore kwargs
    if args.source_family == "pe_portfolio":
        scraper = scraper_cls(firm_name=args.firm)
    else:
        scraper = scraper_cls()

    try:
        scraper.run()
    except NotImplementedError as exc:
        logger.error("Scraper not fully implemented: %s", exc)
        return 1
    except Exception:
        logger.exception("Scraper %s failed", args.source_family)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
