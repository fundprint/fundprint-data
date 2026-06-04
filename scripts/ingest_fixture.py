"""Offline end-to-end ingest: push a saved fixture through the real pipeline.

This runs the genuine Acquire orchestration (Scraper.run): store the raw blob,
insert a source_record, parse, and write staging rows - all in one transaction
against the configured DATABASE_URL (e.g. Supabase). The ONLY thing it bypasses
is the live fetch(): instead of hitting the network, it feeds bytes read from a
local fixture file and stamps a `fixture://` source_url so the provenance is
honest about where the bytes came from.

Use this to prove the pipeline can actually persist data end-to-end before any
live scraper (Playwright, EDGAR API, etc.) is wired up.

Usage:
    python scripts/ingest_fixture.py sec_edgar tests/acquire/fixtures/sec_edgar_sample.json
    python scripts/ingest_fixture.py bacb tests/acquire/fixtures/bacb_sample.html
    python scripts/ingest_fixture.py pe_portfolio tests/acquire/fixtures/portfolio_sample.html --firm KKR

The rows it writes are real. Re-running with the same fixture is idempotent:
the content_hash already-staged check short-circuits the second run.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from fundprint import db

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Which staging table each source_family fills, so we can read rows back to
# confirm the write landed. Keyed by registered source_family.
STAGING_TABLE = {
    "sec_edgar": "staging_sec_filing",
    "bacb": "staging_bacb_provider",
    "pe_portfolio": "staging_pe_portfolio_listing",
}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument(
        "source_family",
        help="Registered scraper key (sec_edgar, bacb, pe_portfolio)",
    )
    p.add_argument("fixture", help="Path to the fixture file to ingest")
    p.add_argument(
        "--firm",
        default="KKR",
        help="For pe_portfolio: which firm config to use (default: KKR)",
    )
    return p


def main() -> int:
    args = build_parser().parse_args()

    fixture_path = Path(args.fixture)
    if not fixture_path.exists():
        logger.error("fixture not found: %s", fixture_path)
        return 1
    content = fixture_path.read_bytes()

    # Import scraper modules to trigger @register decoration
    import fundprint.acquire.bacb  # noqa: F401
    import fundprint.acquire.portfolio_pages  # noqa: F401
    import fundprint.acquire.sec_edgar  # noqa: F401
    from fundprint.acquire.registry import get

    try:
        scraper_cls = get(args.source_family)
    except KeyError as exc:
        logger.error("%s", exc)
        return 1

    if args.source_family == "pe_portfolio":
        scraper = scraper_cls(firm_name=args.firm)
    else:
        scraper = scraper_cls()

    # Provenance that is honest about the offline origin while still being a
    # stable, reproducible key for the source_record unique index.
    source_url = f"fixture://{args.source_family}/{fixture_path.name}"

    # Override only fetch(): everything downstream (snapshot, source_record,
    # parse, staging) runs exactly as it would for a live scrape.
    scraper.fetch = lambda: (content, source_url)  # type: ignore[method-assign]

    logger.info("ingesting %s as %s", fixture_path, source_url)
    scraper.run()

    _report(args.source_family, source_url)
    return 0


def _report(source_family: str, source_url: str) -> None:
    """Read back what we just wrote so success is visible, not assumed."""
    table = STAGING_TABLE.get(source_family)
    with db.transaction() as conn:
        rec = conn.execute(
            """
            SELECT id, source_type, content_hash, snapshot_id, fetched_at
            FROM source_record
            WHERE source_url = %s
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (source_url,),
        ).fetchone()
        if rec is None:
            logger.warning("no source_record found for %s", source_url)
            return
        source_record_id = rec[0]
        print("\nsource_record:")
        print(f"  id           {rec[0]}")
        print(f"  source_type  {rec[1]}")
        print(f"  content_hash {rec[2]}")
        print(f"  snapshot_id  {rec[3]}")
        print(f"  fetched_at   {rec[4]}")

        if not table:
            return
        rows = conn.execute(
            f"SELECT * FROM {table} WHERE source_record_id = %s",  # noqa: S608 - table from fixed allowlist
            (source_record_id,),
        ).fetchall()
        cols = [d[0] for d in conn.execute(
            f"SELECT * FROM {table} LIMIT 0"  # noqa: S608 - table from fixed allowlist
        ).description]
        print(f"\n{table}: {len(rows)} row(s)")
        for r in rows:
            record = dict(zip(cols, r, strict=True))
            # Keep the line readable: show the identifying fields, not raw_json.
            preview = {
                k: v for k, v in record.items() if k not in {"raw_json", "id", "source_record_id"}
            }
            print(f"  - {preview}")


if __name__ == "__main__":
    sys.exit(main())
