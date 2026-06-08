"""CLI entrypoint for the PE-portfolio resolver.

Reads ``staging_pe_portfolio_listing`` rows for a given PE firm and resolves
them into ``parent_pe_firm`` + ``owner_entity`` rows plus
``resolution_claim`` rows of type ``'owner_to_pe_firm'``.

Usage::

    python scripts/run_resolve_portfolio.py --firm KKR
    python scripts/run_resolve_portfolio.py --firm KKR --names "HCA Healthcare,Envision"
    python scripts/run_resolve_portfolio.py --firm KKR --dry-run
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
        description=(
            "Resolve PE-portfolio staging rows into entity rows and ownership claims."
        ),
    )
    p.add_argument(
        "--firm",
        default="KKR",
        help="PE firm name to resolve (default: KKR)",
    )
    p.add_argument(
        "--names",
        default=None,
        help=(
            "Comma-separated list of portfolio company names to restrict processing to. "
            "When omitted, all companies for the firm are processed."
        ),
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Compute counts without writing anything to the database. "
            "The transaction is rolled back before exit."
        ),
    )
    return p


def main() -> int:
    args = build_parser().parse_args()

    # Parse --names into a list (or None when not provided / blank).
    only_names: list[str] | None = None
    if args.names:
        only_names = [n.strip() for n in args.names.split(",") if n.strip()]
        if not only_names:
            only_names = None

    from fundprint import db
    from fundprint.resolve.portfolio import resolve_portfolio

    conn = db.connect()
    try:
        summary = resolve_portfolio(
            conn,
            firm_name=args.firm,
            only_names=only_names,
            dry_run=args.dry_run,
        )
        if not args.dry_run:
            conn.commit()
    except Exception:
        logger.exception("resolve_portfolio failed")
        try:
            conn.rollback()
        except Exception:
            pass
        return 1
    finally:
        conn.close()

    logger.info(
        "resolve_portfolio finished: rows_seen=%d firms_upserted=%d "
        "owners_upserted=%d claims_written=%d claims_skipped=%d",
        summary["rows_seen"],
        summary["firms_upserted"],
        summary["owners_upserted"],
        summary["claims_written"],
        summary["claims_skipped"],
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
