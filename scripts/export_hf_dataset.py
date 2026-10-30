"""Build the Hugging Face dataset folder from the published dashboard snapshot.

Reads the same snapshot.json the site is built from, so the Hub copy can never
carry a number the site does not. Writes hf_release/ (gitignored); upload with

    hf upload gameloft737/fundprint hf_release . --repo-type=dataset

The dataset card is hf/README.md, copied verbatim. Its numbers are prose, so they
are part of the published-number sweep in CLAUDE.md.
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SNAPSHOT = ROOT.parent / "fundprint-dashboard" / "data" / "snapshot.json"

CLINIC_FIELDS = [
    "id", "name", "address", "city", "state", "zip", "npi", "lat", "lng",
    "owner_id", "owner_name", "firm_id", "firm_name", "firm_type",
    "confidence_overall", "confidence_open", "confidence_address",
    "confidence_site_type", "ownership_basis", "registry_last_updated",
    "confidence_score", "confidence_method", "sources",
]


def _flatten(c: dict) -> dict:
    conf = c["confidence"]
    return {
        **{k: c.get(k) for k in CLINIC_FIELDS if k in c},
        "confidence_overall": conf.get("overall"),
        "confidence_open": conf.get("open"),
        "confidence_address": conf.get("address"),
        "confidence_site_type": conf.get("site_type"),
        "ownership_basis": (conf.get("ownership") or {}).get("basis"),
        "registry_last_updated": conf.get("registry_last_updated"),
        "sources": list(c.get("sources") or []),
    }


def _write_table(rows: list[dict], fields: list[str], out: Path, name: str) -> None:
    rows = [{f: r.get(f) for f in fields} for r in rows]
    pq.write_table(pa.Table.from_pylist(rows), out / f"{name}.parquet")
    # CSV has no list type; sources are joined with " | " (no URL contains it).
    with open(out / f"{name}.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: " | ".join(v) if isinstance(v, list) else v for k, v in r.items()})


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    ap.add_argument("--out", type=Path, default=ROOT / "hf_release")
    args = ap.parse_args()

    snap = json.loads(args.snapshot.read_text(encoding="utf-8"))
    if args.out.exists():
        shutil.rmtree(args.out)
    data = args.out / "data"
    extra = args.out / "derived"
    data.mkdir(parents=True)
    extra.mkdir()

    clinics = [_flatten(c) for c in snap["clinics"]]
    _write_table(clinics, CLINIC_FIELDS, data, "clinics")
    _write_table(snap["brands"], ["owner_id", "owner_name", "firm_id", "firm_name",
                                  "firm_type", "clinic_count"], data, "owners")
    _write_table(snap["acquirers"], ["id", "name", "firm_type", "clinic_count",
                                     "brand_count", "former", "in_home"], data, "firms")

    for rel in ["market/aba_market.json", "platforms/coverage.json",
                "reconciliation/reconciliation.json", "states/state_files.json",
                "verification/verification.json", "verification/reviewed_3bf05f0a.csv"]:
        shutil.copy(ROOT / "data" / rel, extra / Path(rel).name)
    (extra / "snapshot_meta.json").write_text(
        json.dumps({"meta": snap["meta"], "totals": snap["totals"]}, indent=2), encoding="utf-8")

    shutil.copy(ROOT / "hf" / "README.md", args.out / "README.md")

    t = snap["totals"]
    print(f"wrote {args.out}: {len(clinics)} clinics ({t['pe_clinics']} PE), "
          f"{len(snap['brands'])} owners, {len(snap['acquirers'])} firms, "
          f"dataset {snap['meta']['dataset_version']}")


if __name__ == "__main__":
    main()
