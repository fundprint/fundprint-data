"""Hand-validation helper for a drawn sample sheet.

Turns the UUID-based sample (samples/<run_id>.json) into a human-readable CSV
you fill in offline, then scores the filled CSV against the methodology's 95%
gate and writes the labels back into the sample JSON (the audit record).

Workflow:
    1. python scripts/review_sample.py --to-csv samples/<run_id>.json
         -> writes review_<run_id>.csv with a blank `verdict` column.
    2. Open the CSV, and for each row write agree / disagree / unclear in
       `verdict` after checking the source_url against the claim.
    3. python scripts/review_sample.py --score review_<run_id>.csv \
            --sample samples/<run_id>.json
         -> prints agree/(agree+disagree) vs the 0.95 floor and saves labels.

The link is rendered with real entity names (not UUIDs) so you know what each
claim asserts before checking its source.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from fundprint import db

FIELDNAMES = [
    "claim_id",
    "claim_type",
    "asserts",          # human-readable: "X -> Y"
    "confidence",
    "method",
    "source_url",
    "verdict",          # YOU fill: agree | disagree | unclear
    "notes",
]


def _name_map() -> dict[str, str]:
    c = db.connect()
    try:
        out: dict[str, str] = {}
        for tbl in ("clinic", "owner_entity", "parent_pe_firm"):
            for oid, nm in c.execute(f"SELECT id, name FROM {tbl}").fetchall():
                out[str(oid)] = nm
        return out
    finally:
        c.close()


def _asserts(claim_type: str, link: dict, names: dict[str, str]) -> str:
    def nm(key: str) -> str:
        return names.get(link.get(key, ""), link.get(key, "?"))

    if claim_type == "owner_to_pe_firm":
        return f"{nm('owner_entity_id')} -> {nm('parent_pe_firm_id')}"
    if claim_type == "clinic_to_owner":
        return f"{nm('clinic_id')} -> {nm('owner_entity_id')}"
    return claim_type


def to_csv(sample_path: Path) -> Path:
    sheet = json.loads(sample_path.read_text())
    names = _name_map()
    out = Path(f"review_{sheet['run_id']}.csv")
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        w.writeheader()
        for r in sheet["rows"]:
            w.writerow(
                {
                    "claim_id": r["claim_id"],
                    "claim_type": r["claim_type"],
                    "asserts": _asserts(r["claim_type"], r["proposed_link"], names),
                    "confidence": r["confidence_score"],
                    "method": r["confidence_method"],
                    "source_url": (r["source_urls"][0] if r["source_urls"] else ""),
                    "verdict": r.get("reviewer_label") or "",
                    "notes": "",
                }
            )
    print(f"Wrote {len(sheet['rows'])} rows -> {out}")
    print("Fill the `verdict` column (agree/disagree/unclear), then run --score.")
    return out


def score(csv_path: Path, sample_path: Path | None) -> int:
    rows = list(csv.DictReader(csv_path.open(encoding="utf-8")))
    tally = {"agree": 0, "disagree": 0, "unclear": 0, "blank": 0}
    labels: dict[str, str] = {}
    for r in rows:
        v = (r.get("verdict") or "").strip().lower()
        if v in ("agree", "disagree", "unclear"):
            tally[v] += 1
            labels[r["claim_id"]] = v
        else:
            tally["blank"] += 1

    decided = tally["agree"] + tally["disagree"]
    ratio = (tally["agree"] / decided) if decided else 0.0
    print(f"Reviewed: {tally}")
    print(f"Accuracy: {tally['agree']}/{decided} = {ratio:.3f}  (gate floor 0.95)")
    if tally["blank"]:
        print(f"WARNING: {tally['blank']} rows still blank -- label them for a complete gate.")
    print("GATE:", "PASS" if (decided and ratio >= 0.95 and not tally["blank"]) else "NOT PASSED")

    # Write labels back into the sample JSON audit record.
    if sample_path and labels:
        sheet = json.loads(sample_path.read_text())
        for row in sheet["rows"]:
            if row["claim_id"] in labels:
                row["reviewer_label"] = labels[row["claim_id"]]
        sample_path.write_text(json.dumps(sheet, indent=2, default=str))
        print(f"Saved {len(labels)} labels back into {sample_path}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--to-csv", metavar="SAMPLE_JSON", help="render a sample to a review CSV")
    p.add_argument("--score", metavar="REVIEW_CSV", help="score a filled review CSV")
    p.add_argument(
        "--sample",
        metavar="SAMPLE_JSON",
        help="sample JSON to save labels into (with --score)",
    )
    args = p.parse_args()

    if args.to_csv:
        to_csv(Path(args.to_csv))
        return 0
    if args.score:
        return score(Path(args.score), Path(args.sample) if args.sample else None)
    p.error("pass --to-csv <sample.json> or --score <review.csv>")
    return 2


if __name__ == "__main__":
    sys.exit(main())
