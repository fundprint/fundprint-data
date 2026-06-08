"""Create (or refresh) the dashboard read-only SQL views.

Reads the canonical view SQL from fundprint.publish.dashboard, splits it into
individual statements, and executes each one against the configured database.
After creation it logs the row count for each view so the caller can confirm
the views are populated.

Usage:
    python scripts/create_dashboard_views.py
"""

import logging
import sys

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

DASHBOARD_VIEWS = [
    "v_published_claims",
    "v_published_clinics",
    "v_published_pe_links",
]


def create_views(conn) -> None:
    """Execute every CREATE OR REPLACE VIEW statement from generate_view_sql().

    Splits the SQL on ';', strips whitespace, and skips any chunk that is empty
    or consists entirely of SQL comment lines (lines starting with '--').
    """
    from fundprint.publish.dashboard import generate_view_sql

    sql = generate_view_sql()

    for chunk in sql.split(";"):
        stmt = chunk.strip()
        if not stmt:
            continue
        # Skip chunks whose non-empty lines are all comments.
        non_comment_lines = [
            line for line in stmt.splitlines() if line.strip() and not line.strip().startswith("--")
        ]
        if not non_comment_lines:
            continue
        conn.execute(stmt)
        logger.info("Executed statement: %s", stmt.splitlines()[0])


def main() -> int:
    from fundprint import db

    try:
        conn = db.connect()
        logger.info("Connected to database; creating dashboard views.")
        create_views(conn)
        conn.commit()
        logger.info("All views created/refreshed successfully.")
    except Exception:
        logger.exception("Failed to create dashboard views")
        return 1

    for view in DASHBOARD_VIEWS:
        try:
            row = conn.execute(f"SELECT count(*) FROM {view}").fetchone()
            count = row[0]
            logger.info("View %s: %d rows", view, count)
        except Exception:
            logger.exception("Could not query row count for view %s", view)

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
