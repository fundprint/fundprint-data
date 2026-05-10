"""CLI entrypoint for the Publish layer.

Usage:
    python scripts/run_publish.py \\
        --schema-version X \\
        --resolver-version Y \\
        --methodology-version Z \\
        --validation-run-id <uuid>
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
        description="Build a Fundprint release: Parquet files, data card, audit packet, manifest.",
    )
    p.add_argument("--schema-version", required=True)
    p.add_argument("--resolver-version", required=True)
    p.add_argument("--methodology-version", required=True)
    p.add_argument(
        "--validation-run-id",
        required=True,
        help="UUID of the validation_run that gated this release.",
    )
    p.add_argument(
        "--hf-repo-id",
        default=None,
        help="Hugging Face repo ID to upload to (optional; stub if omitted).",
    )
    p.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="Overwrite an existing manifest for today's date. Use only to correct mistakes.",
    )
    return p


def main() -> int:
    args = build_parser().parse_args()

    from fundprint import db
    from fundprint.publish import build_release

    try:
        conn = db.connect()
        manifest = build_release(
            conn,
            schema_version=args.schema_version,
            resolver_version=args.resolver_version,
            methodology_version=args.methodology_version,
            validation_run_id=args.validation_run_id,
            hf_repo_id=args.hf_repo_id,
            force=args.force,
        )
        conn.close()
    except Exception:
        logger.exception("Publish build failed")
        return 1

    logger.info(
        "Release %s complete: schema=%s resolver=%s methodology=%s",
        manifest.dataset_version,
        manifest.schema_version,
        manifest.resolver_version,
        manifest.methodology_version,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
