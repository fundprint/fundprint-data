"""CLI entrypoint for the Validate layer.

Usage:
    python scripts/run_validate.py --resolver-version X --methodology-version Y
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
        description="Run the Fundprint validation pass over unvalidated resolution claims.",
    )
    p.add_argument(
        "--resolver-version",
        required=True,
        help="Resolver version string to validate claims against.",
    )
    p.add_argument(
        "--methodology-version",
        required=True,
        help="Methodology version string governing confidence floors for this run.",
    )
    return p


def main() -> int:
    args = build_parser().parse_args()

    from fundprint import db
    from fundprint.validate import run_validation

    try:
        conn = db.connect()
        run_id = run_validation(
            conn,
            resolver_version=args.resolver_version,
            methodology_version=args.methodology_version,
        )
        conn.close()
    except Exception:
        logger.exception("Validation run failed")
        return 1

    logger.info("Validation run complete. run_id=%s", run_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
