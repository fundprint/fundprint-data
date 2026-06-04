"""Connectivity and schema health check for the configured database.

Confirms that DATABASE_URL points at a reachable Postgres (e.g. Supabase),
that pgvector is installed, and that the expected Fundprint tables exist.
Use this to verify the pipeline can actually talk to the database before
running a real ingest.

Usage:
    python scripts/check_db.py

Reads DATABASE_URL from the environment / .env via fundprint.config.
Prints a checklist and exits non-zero if any required check fails.
"""

from __future__ import annotations

import sys
from urllib.parse import urlsplit

from fundprint import db
from fundprint.config import settings

# Tables the initial migration is expected to create.
EXPECTED_TABLES = frozenset(
    {
        "source_record",
        "staging_bacb_provider",
        "staging_sec_filing",
        "staging_pe_portfolio_listing",
        "parent_pe_firm",
        "owner_entity",
        "clinic",
        "acquisition_event",
        "resolution_claim",
        "validation_run",
        "validation_run_decision",
    }
)


def _redacted_target() -> str:
    """Return host/db from DATABASE_URL with credentials stripped, for logging."""
    parts = urlsplit(settings.database_url)
    host = parts.hostname or "?"
    port = f":{parts.port}" if parts.port else ""
    dbname = parts.path.lstrip("/") or "?"
    return f"{host}{port}/{dbname}"


def main() -> int:
    print(f"Target: {_redacted_target()}")
    ok = True

    # 1. Can we open a connection and run a trivial query?
    try:
        with db.transaction() as conn:
            version = conn.execute("SELECT version()").fetchone()[0]
        print(f"[ok]   connected: {version.split(' on ')[0]}")
    except Exception as exc:  # noqa: BLE001 - surface any failure to the user
        print(f"[FAIL] could not connect: {exc}")
        print("\nVerification FAILED: no connection to the database.")
        return 1

    # 2. Is pgvector available? Entity resolution depends on it.
    try:
        with db.transaction() as conn:
            row = conn.execute(
                "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
            ).fetchone()
        if row:
            print(f"[ok]   pgvector extension installed (v{row[0]})")
        else:
            print("[warn] pgvector NOT installed - run the migration to enable it")
            ok = False
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] could not check pgvector: {exc}")
        ok = False

    # 3. Are the expected tables present?
    with db.transaction() as conn:
        present = {
            r[0]
            for r in conn.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
            ).fetchall()
        }
    missing = EXPECTED_TABLES - present
    if not missing:
        print(f"[ok]   all {len(EXPECTED_TABLES)} expected tables present")
    else:
        print(f"[warn] migration not applied - missing {len(missing)} table(s):")
        for name in sorted(missing):
            print(f"         - {name}")
        ok = False

    # 4. Round-trip write/read in a rolled-back transaction (touches nothing).
    try:
        conn = db.connect()
        try:
            conn.execute("CREATE TEMP TABLE _fundprint_probe (n int)")
            conn.execute("INSERT INTO _fundprint_probe VALUES (1)")
            n = conn.execute("SELECT count(*) FROM _fundprint_probe").fetchone()[0]
            conn.rollback()  # leave the database untouched
            print(f"[ok]   write/read round-trip works (probe rows={n}, rolled back)")
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] write/read round-trip failed: {exc}")
        ok = False

    if ok:
        print("\nVerification PASSED: the pipeline can talk to this database.")
        return 0
    print(
        "\nConnection works, but the database is not fully ready. "
        "Apply the migration in supabase/migrations/ and re-run."
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
