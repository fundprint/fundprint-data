"""Print a human-readable snapshot of the published Fundprint dataset.

Read-only. Shows the pipeline row counts, the published owner -> PE-firm links,
and a sample of complete clinic -> owner -> PE ownership chains -- i.e. what the
dashboard would render. Run this to see the dataset "in action" from the CLI.

Usage:
    python scripts/show_dataset.py
"""

from __future__ import annotations

import sys

from fundprint import db
from fundprint.resolve.chain import walk_chain


def main() -> int:
    c = db.connect()
    try:
        name = {}
        for tbl in ("clinic", "owner_entity", "parent_pe_firm"):
            for oid, nm in c.execute(f"SELECT id, name FROM {tbl}").fetchall():
                name[str(oid)] = nm

        print("=== Pipeline counts ===")
        for label, q in [
            ("parent PE firms", "SELECT count(*) FROM parent_pe_firm"),
            ("owner entities", "SELECT count(*) FROM owner_entity"),
            ("clinics", "SELECT count(*) FROM clinic"),
            ("published PE links", "SELECT count(*) FROM v_published_pe_links"),
            ("published clinics", "SELECT count(*) FROM v_published_clinics"),
        ]:
            print(f"  {label:22} {c.execute(q).fetchone()[0]}")

        print("\n=== Published owner -> PE links (first 10) ===")
        rows = c.execute(
            "SELECT owner_entity_name, parent_pe_firm_name, confidence_score, "
            "confidence_method FROM v_published_pe_links ORDER BY owner_entity_name LIMIT 10"
        ).fetchall()
        for r in rows:
            print(f"  {r[0][:34]:34} -> {r[1]:6} | conf {float(r[2])} | {r[3]}")

        print("\n=== Complete clinic -> owner -> PE chains (first 8) ===")
        clinics = c.execute(
            "SELECT id, name, city, state FROM clinic ORDER BY name LIMIT 8"
        ).fetchall()
        for cid, cname, city, st in clinics:
            ch = walk_chain(str(cid), conn=c)
            if ch.is_complete:
                owner = name.get(ch.owner_entity_id, "?")
                pe = name.get(ch.parent_pe_firm_id, "?")
                loc = f"{city}, {st}" if city else st or ""
                print(f"  {cname} ({loc}) -> {owner} -> {pe}  [min-conf {ch.confidence}]")
    finally:
        c.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
