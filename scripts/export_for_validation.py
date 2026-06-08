"""Export staged rows to a CSV for manual hand-validation.

The plan's quality gate: a human eyeballs each real row against its public
source and records a verdict before we trust the automated resolver. This
script pulls the SEC filings and PE portfolio listings already in the live
database, joins each to its source_record (so you have the URL + local
snapshot to check against), and writes a spreadsheet with blank `verdict`,
`reviewer`, and `notes` columns for you to fill in by hand.

It is READ-ONLY: it never writes to the database.

Healthcare / behavioral-health rows are floated to the top and flagged in a
`relevance` column, since those are the ones the plan says to check first.

Usage:
    python scripts/export_for_validation.py                 # -> hand_validation.csv
    python scripts/export_for_validation.py --out review.csv
    python scripts/export_for_validation.py --only sec      # sec | portfolio | all
    python scripts/export_for_validation.py --relevant-only # skip clearly-unrelated rows
"""

from __future__ import annotations

import argparse
import csv
import sys

from fundprint import db

# Keywords that mark a row as worth checking first (autism / behavioral / health).
RELEVANCE_KEYWORDS = (
    "autism",
    "aba",
    "behavior",
    "behavioral",
    "therapy",
    "therapeutic",
    "clinic",
    "health",
    "healthcare",
    "medical",
    "pediatric",
    "developmental",
    "blue sprig",
    "bluesprig",
)

# Columns written for every row, in order. The blank ones are for the human.
FIELDNAMES = [
    "table",
    "row_id",
    "relevance",
    "pe_firm_name",
    "subject",          # portfolio company name OR SEC issuer/filer
    "form_or_sector",   # SEC form_type OR portfolio sector_tags
    "detail",           # description / filer / amount, for context
    "source_url",
    "snapshot_id",
    "ingested_at",
    # --- you fill these in by hand ---
    "verdict",          # pass | fail | unsure
    "reviewer",
    "notes",
]


def _relevance(*texts: str | None) -> str:
    """Return 'health' if any text hints at behavioral health, else ''."""
    blob = " ".join(t for t in texts if t).lower()
    return "health" if any(kw in blob for kw in RELEVANCE_KEYWORDS) else ""


def _fetch_sec(conn) -> list[dict]:
    rows = conn.execute(
        """
        SELECT f.id, f.form_type, f.filer_name, f.issuer_name,
               f.issuer_state, f.amount_raised, f.filing_date,
               f.ingested_at, s.source_url, s.snapshot_id
        FROM staging_sec_filing f
        LEFT JOIN source_record s ON s.id = f.source_record_id
        ORDER BY f.filing_date DESC NULLS LAST
        """
    ).fetchall()
    out = []
    for (
        rid, form_type, filer, issuer, issuer_state, amount,
        filing_date, ingested_at, url, snapshot,
    ) in rows:
        detail_bits = []
        if filer:
            detail_bits.append(f"filer={filer}")
        if issuer_state:
            detail_bits.append(f"state={issuer_state}")
        if amount is not None:
            detail_bits.append(f"raised=${amount:,.0f}")
        if filing_date:
            detail_bits.append(f"filed={filing_date}")
        out.append(
            {
                "table": "staging_sec_filing",
                "row_id": str(rid),
                "relevance": _relevance(issuer, filer),
                "pe_firm_name": "",
                "subject": issuer or filer or "(unknown)",
                "form_or_sector": form_type or "",
                "detail": "; ".join(detail_bits),
                "source_url": url or "",
                "snapshot_id": snapshot or "",
                "ingested_at": ingested_at.isoformat() if ingested_at else "",
            }
        )
    return out


def _fetch_portfolio(conn) -> list[dict]:
    rows = conn.execute(
        """
        SELECT p.id, p.pe_firm_name, p.portfolio_name, p.description,
               p.sector_tags, p.ingested_at, s.source_url, s.snapshot_id
        FROM staging_pe_portfolio_listing p
        LEFT JOIN source_record s ON s.id = p.source_record_id
        ORDER BY p.pe_firm_name, p.portfolio_name
        """
    ).fetchall()
    out = []
    for (
        rid, firm, name, description, sector_tags,
        ingested_at, url, snapshot,
    ) in rows:
        tags = ", ".join(sector_tags) if sector_tags else ""
        out.append(
            {
                "table": "staging_pe_portfolio_listing",
                "row_id": str(rid),
                "relevance": _relevance(name, description, tags),
                "pe_firm_name": firm or "",
                "subject": name or "(unknown)",
                "form_or_sector": tags,
                "detail": (description or "")[:300],
                "source_url": url or "",
                "snapshot_id": snapshot or "",
                "ingested_at": ingested_at.isoformat() if ingested_at else "",
            }
        )
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="hand_validation.csv", help="output CSV path")
    parser.add_argument(
        "--only",
        choices=["sec", "portfolio", "all"],
        default="all",
        help="which staging table(s) to export",
    )
    parser.add_argument(
        "--relevant-only",
        action="store_true",
        help="export only rows flagged as behavioral-health relevant",
    )
    args = parser.parse_args()

    rows: list[dict] = []
    with db.transaction() as conn:
        if args.only in ("sec", "all"):
            rows += _fetch_sec(conn)
        if args.only in ("portfolio", "all"):
            rows += _fetch_portfolio(conn)

    if args.relevant_only:
        rows = [r for r in rows if r["relevance"] == "health"]

    # Float the health-relevant rows to the top so you review them first.
    rows.sort(key=lambda r: (r["relevance"] != "health",))

    for r in rows:
        r.setdefault("verdict", "")
        r.setdefault("reviewer", "")
        r.setdefault("notes", "")

    with open(args.out, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

    relevant = sum(1 for r in rows if r["relevance"] == "health")
    print(f"Wrote {len(rows)} rows ({relevant} flagged health-relevant) -> {args.out}")
    print("Open it in Excel/Sheets, fill in the verdict/reviewer/notes columns.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
