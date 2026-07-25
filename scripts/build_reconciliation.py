"""Reconcile Fundprint against the published peer-reviewed estimate.

Fundprint publishes 2.8x more PE-owned autism clinics than the JAMA Pediatrics
letter of January 2026. A bare "we found more" is worthless; a reader has no way
to tell a better method from a looser one. This script builds the evidence that
tells them apart, and writes it into the snapshot so the public note is generated
rather than typed.

The argument it assembles, in the order the note makes it:

1. **The two counts agree on geography.** Both find private equity in exactly 42
   states. The disagreement is depth, not footprint.
2. **Restricted to what the federal registry can see, the two counts nearly
   agree**: 574 theirs, 580 ours, from methods that share no data source. That
   convergence is what makes the remaining gap interpretable instead of a
   standoff. It bounds how much of the difference can be method noise.
3. **The rest of the gap is a third source neither method uses**: the operator's
   own public location directory.
4. **The registry's blind spot is not a constant.** Per operator it runs from
   1.0x (the registry sees essentially everything) to unbounded (a brand with 18
   centers and zero registry-visible sites). So no multiplier can correct a
   registry-based or deal-based count. You have to go and read the directories.
   This is the actual methods contribution, and point 4 is why it matters more
   than point 1.

What it does NOT do, on purpose: attribute the gap to error on their part. The
letter names its own two limitations, and this is an attempt to supply both. It
also cannot separate method from elapsed time. Their count is as of December 31,
2024; ours is current. Nineteen months of real openings sit inside the ratio and
cannot be removed without their site list, which is not published. The note says
so rather than claiming the whole factor.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fundprint import db, external_estimates, fetch  # noqa: E402
from fundprint.acquire.base import _insert_source_record  # noqa: E402
from fundprint.storage import LocalFilesystemStore  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("reconciliation")

ROOT = Path(__file__).resolve().parents[1]
OUT_PATH = ROOT / "data" / "reconciliation" / "reconciliation.json"
MARKET_PATH = ROOT / "data" / "market" / "aba_market.json"
SOURCE_TYPE = "external_estimate"
MODULE_VERSION = "0.1.0"


def _snapshot_estimate(conn, est: external_estimates.ExternalEstimate) -> dict:
    """Fetch and content-hash the paper, so the comparison is auditable."""
    content = fetch.get(est.source_url)
    blob = content if isinstance(content, bytes) else content.content
    snapshot_id, content_hash = LocalFilesystemStore().put(blob, suffix=".html")

    existing = conn.execute(
        "SELECT id FROM source_record WHERE source_url = %s AND content_hash = %s",
        (est.source_url, content_hash),
    ).fetchone()
    if existing:
        source_record_id = str(existing[0])
        logger.info("%s unchanged, reusing source_record %s", est.key, source_record_id)
    else:
        source_record_id = _insert_source_record(
            conn,
            source_url=est.source_url,
            snapshot_id=snapshot_id,
            source_type=SOURCE_TYPE,
            fetched_at=datetime.now(UTC),
            content_hash=content_hash,
            module_version=MODULE_VERSION,
        )
        logger.info("snapshotted %s as source_record %s", est.key, source_record_id)

    return {"source_record_id": source_record_id, "content_hash": content_hash}


def _pe_by_state(conn) -> dict[str, int]:
    """Published clinics per state, PRIVATE EQUITY ONLY.

    The filter is not optional. `snapshot.states` counts every institutional
    financial owner, including a pension fund, a family office and two search
    funds, so it reads higher than PE alone. Comparing that column against a
    figure the letter labels "PE" would manufacture a disagreement out of our own
    broader scope and hand a reviewer a free objection.
    """
    rows = conn.execute(
        """
        SELECT vc.state, COUNT(*)
          FROM v_published_clinics vc
          JOIN owner_entity oe ON oe.id = vc.owner_entity_id
          JOIN parent_pe_firm f ON f.id = oe.parent_pe_firm_id
         WHERE f.firm_type = 'private_equity' AND vc.state IS NOT NULL
         GROUP BY vc.state
        """
    ).fetchall()
    return {str(s): int(n) for s, n in rows}


def _published_by_owner(conn) -> list[dict]:
    """Published clinic count per owner, with its firm, for center-based owners.

    Center-based only, to line up with the market numerator: an in-home owner
    publishes zero clinics by design and would read as a 0-of-0 row that means
    nothing.
    """
    rows = conn.execute(
        """
        SELECT oe.name,
               COALESCE(oe.trade_name, oe.name),
               f.name,
               f.firm_type,
               COUNT(*)
          FROM v_published_clinics vc
          JOIN owner_entity oe ON oe.id = vc.owner_entity_id
          JOIN parent_pe_firm f ON f.id = oe.parent_pe_firm_id
         WHERE oe.is_aba AND oe.service_model = 'center_based'
         GROUP BY oe.name, COALESCE(oe.trade_name, oe.name), f.name, f.firm_type
        """
    ).fetchall()
    return [
        {
            "owner": str(name),
            "brand": str(brand),
            "firm": str(firm),
            "firm_type": str(ftype),
            "published": int(n),
        }
        for name, brand, firm, ftype, n in rows
    ]


def build(conn, market: dict) -> dict:
    est = external_estimates.BROWN_2026
    provenance = _snapshot_estimate(conn, est)

    visible = {
        r["owner"]: r["registry_visible_sites"]
        for r in market["numerator"].get("by_owner", [])
    }
    if not visible:
        raise SystemExit(
            "aba_market.json carries no numerator.by_owner. Re-run "
            "scripts/compute_market_share.py, which now emits it."
        )

    owners = []
    for row in _published_by_owner(conn):
        seen = visible.get(row["owner"])
        if seen is None:
            # An owner the market pass never considered. It should not happen, and
            # guessing a visibility number would be worse than refusing to publish
            # the row, so fail loudly instead.
            raise SystemExit(
                f"owner {row['owner']!r} is published but absent from "
                "numerator.by_owner; the two passes disagree on scope."
            )
        owners.append(
            {
                **row,
                "registry_visible": seen,
                # None, not "infinity": a brand with zero registry-visible sites
                # has no ratio, and printing one would invent a finite blind spot
                # where the real answer is that the registry cannot see it at all.
                "ratio": round(row["published"] / seen, 1) if seen else None,
            }
        )
    owners.sort(key=lambda r: (-r["published"], r["brand"]))

    pe_by_state = _pe_by_state(conn)
    pe_clinics = sum(pe_by_state.values())
    pe_visible = market["numerator"]["private_equity_sites"]

    states = [
        {
            "state": st,
            "external": n,
            "fundprint_pe": pe_by_state.get(st, 0),
            "ratio": round(pe_by_state.get(st, 0) / n, 2) if n else None,
        }
        for st, n in sorted(est.top_states.items(), key=lambda kv: -kv[1])
    ]

    # The spread that kills the "just apply a multiplier" objection, computed over
    # owners big enough for a ratio to mean anything. A 2-clinic owner at 2.0x is
    # noise; a 134-clinic owner at 7.1x is not.
    material = [r for r in owners if r["published"] >= 10]
    finite = [r["ratio"] for r in material if r["ratio"] is not None]

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "estimate": {
            "key": est.key,
            "title": est.title,
            "authors": est.authors,
            "venue": est.venue,
            "published": est.published,
            "canonical_url": est.canonical_url,
            "source_url": est.source_url,
            "unit": est.unit,
            "as_of": est.as_of,
            "sites": est.sites,
            "acquisitions": est.acquisitions,
            "states": est.states,
            "method": est.method,
            "stated_limitations": est.stated_limitations,
            "notes": est.notes,
            **provenance,
        },
        "fundprint": {
            "pe_clinics": pe_clinics,
            "states": len(pe_by_state),
            "registry_visible_pe_sites": pe_visible,
            "aba_sites": market["denominator"]["aba_sites"],
            "pe_share_of_all_sites": market["share"]["private_equity_of_all_sites"],
            "archive_sha256": market["meta"]["archive_sha256"],
            "as_of": market["meta"]["computed_at"][:10],
        },
        "headline": {
            # Both ratios are published because they answer different questions.
            # The first is "how much more is there", the second is "how much of
            # that is method rather than reach".
            "ratio_all_sources": round(pe_clinics / est.sites, 2),
            "ratio_registry_visible": round(pe_visible / est.sites, 2),
            "registry_visible_gap": pe_visible - est.sites,
            "directory_only_pe_clinics": pe_clinics - pe_visible,
            "states_agree": len(pe_by_state) == est.states,
        },
        "states": states,
        "owner_spread": {
            "owners": len(material),
            "min_ratio": min(finite) if finite else None,
            "max_ratio": max(finite) if finite else None,
            "invisible_owners": [r["brand"] for r in material if r["ratio"] is None],
        },
        "owners": owners,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--market", default=str(MARKET_PATH))
    ap.add_argument("--out", default=str(OUT_PATH))
    args = ap.parse_args()

    market = json.loads(Path(args.market).read_text(encoding="utf-8"))
    with db.transaction() as conn:
        doc = build(conn, market)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")

    h, f, e = doc["headline"], doc["fundprint"], doc["estimate"]
    logger.info(
        "%s: %d sites as of %s; Fundprint %d PE clinics (%.2fx), "
        "%d registry-visible (%.2fx, gap %+d)",
        e["key"],
        e["sites"],
        e["as_of"],
        f["pe_clinics"],
        h["ratio_all_sources"],
        f["registry_visible_pe_sites"],
        h["ratio_registry_visible"],
        h["registry_visible_gap"],
    )
    spread = doc["owner_spread"]
    worst = "unbounded" if spread["invisible_owners"] else f"{spread['max_ratio']}x"
    logger.info(
        "per-owner registry blind spot over %d owners: %.1fx to %s",
        spread["owners"],
        spread["min_ratio"],
        worst,
    )
    logger.info("wrote %s", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
