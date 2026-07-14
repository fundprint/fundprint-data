"""Export a version-pinned static snapshot for the public dashboard.

This is the dashboard's Consume -> Cache boundary, run in the data repo where
reading the published views (and resolving their source_record URLs) is
legitimate. The dashboard itself never touches the database: it ships this
JSON as a static asset and queries it client-side.

Everything written here is reproducible from the published dataset - the
script only reads the ``v_published_*`` views (never entity/staging tables,
except to resolve the public source URLs the views reference by id) and
aggregates columns the views already expose. No number is invented that a
reader could not recreate from the Hugging Face download.

Usage::

    python scripts/export_dashboard_snapshot.py \
        --out ../fundprint-dashboard/data/snapshot.json
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import UTC, datetime
from pathlib import Path

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# The dashboard pins exactly one dataset_version per deploy. Bump this in step
# with the Hugging Face release the snapshot is cut from.
DATASET_VERSION = "2026.07-beta"
# Bumped with the site-counting correction: one site is one clinic even when two
# of a parent firm's brands are registered at it. The floors are unchanged; the
# definition of a countable site is not, which is a methodology change under
# section 12. The pin must move in the same commit as the numbers, or a reader who
# follows it lands on a document describing different ones.
METHODOLOGY_VERSION = "2026.07-directory-v2"


def _source_urls(conn, source_record_ids) -> list[str]:
    """Resolve an array of source_record ids to their public URLs, de-duped."""
    if not source_record_ids:
        return []
    rows = conn.execute(
        "SELECT DISTINCT source_url FROM source_record WHERE id = ANY(%s::uuid[])",
        (list(source_record_ids),),
    ).fetchall()
    return sorted(u for (u,) in rows if u)


# ZIP-level geocoding for the map. Coordinates are ZIP Code Tabulation Area
# centroids from the U.S. Census 2023 national gazetteer (public domain), bundled
# at data/geo/zcta_centroids.json for reproducibility (no build-time API call).
# ZIP-centroid precision is deliberate: the map is a national dot map, so we
# place a clinic in its ZIP, not at a false-precision street pin.
_CENTROIDS_PATH = Path(__file__).resolve().parent.parent / "data" / "geo" / "zcta_centroids.json"


def _load_centroids() -> tuple[dict[str, list[float]], dict[str, list[float]]]:
    """Return (exact ZIP5 -> [lat, lng], ZIP3 prefix -> mean [lat, lng])."""
    try:
        exact = json.loads(_CENTROIDS_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.warning("zcta centroids missing at %s; clinics will be unplaced", _CENTROIDS_PATH)
        return {}, {}
    buckets: dict[str, list[list[float]]] = {}
    for z, latlng in exact.items():
        buckets.setdefault(z[:3], []).append(latlng)
    zip3 = {
        pre: [
            round(sum(p[0] for p in pts) / len(pts), 5),
            round(sum(p[1] for p in pts) / len(pts), 5),
        ]
        for pre, pts in buckets.items()
    }
    return exact, zip3


def _coords(zipc, exact, zip3) -> tuple[float | None, float | None]:
    """Best ZIP centroid for a clinic: exact ZIP5, then ZIP3 prefix, else none."""
    if not zipc:
        return None, None
    digits = "".join(ch for ch in str(zipc) if ch.isdigit())
    if len(digits) < 5:
        return None, None
    z = digits[:5]
    hit = exact.get(z) or zip3.get(z[:3])
    return (hit[0], hit[1]) if hit else (None, None)


def build_snapshot(conn) -> dict:
    # --- clinics (the search + heatmap substrate) -----------------------------
    # Join each published clinic to its owner brand and ultimate acquirer via
    # the published PE-links view, so every clinic carries its full chain and
    # the honest firm_type. A clinic with no published PE link simply does not
    # appear here (absence is a coverage statement, surfaced in the UI copy).
    # The street comes from `clinic`, not the view, which carries only city/state/zip.
    # Without it the site cannot tell one clinic from another: a chain names every
    # site after the brand, so Behavioral Innovations' three Cypress TX centres all
    # render as the identical string "Behavioral Innovations, Cypress, TX" (two even
    # share a ZIP). The street is what a parent actually recognises, and it is the
    # only field that distinguishes real sibling sites from a duplicate-row bug.
    clinic_rows = conn.execute(
        """
        SELECT c.id, c.name, addr.address_line1, c.city, c.state, c.zip, c.npi,
               c.confidence_score, c.confidence_method, c.source_record_ids,
               l.owner_entity_id, l.owner_entity_name,
               l.parent_pe_firm_id, l.parent_pe_firm_name, l.parent_pe_firm_type
        FROM v_published_clinics c
        JOIN clinic addr ON addr.id = c.id
        JOIN owner_entity oe ON oe.id = c.owner_entity_id
        JOIN v_published_pe_links l ON l.owner_entity_id = oe.id
        ORDER BY c.name
        """
    ).fetchall()

    exact_centroids, zip3_centroids = _load_centroids()
    clinics = []
    for r in clinic_rows:
        (cid, name, address, city, state, zipc, npi, conf, method, srids,
         owner_id, owner_name, firm_id, firm_name, firm_type) = r
        lat, lng = _coords(zipc, exact_centroids, zip3_centroids)
        clinics.append(
            {
                "id": str(cid),
                "name": name,
                "address": address,
                "city": city,
                "state": state,
                "zip": zipc,
                "npi": npi,
                "lat": lat,
                "lng": lng,
                "owner_id": str(owner_id),
                "owner_name": owner_name,
                "firm_id": str(firm_id),
                "firm_name": firm_name,
                "firm_type": firm_type,
                "confidence_score": float(conf) if conf is not None else None,
                "confidence_method": method,
                "sources": _source_urls(conn, srids),
            }
        )

    # --- acquirers (the sortable table) --------------------------------------
    # Counts are a GROUP BY over the published links view - reproducible by any
    # reader from the HF download. Coverage counts, not a census.
    acquirer_rows = conn.execute(
        """
        SELECT parent_pe_firm_id, parent_pe_firm_name, parent_pe_firm_type,
               hq_state,
               count(DISTINCT owner_entity_id) AS brand_count
        FROM v_published_pe_links
        GROUP BY parent_pe_firm_id, parent_pe_firm_name,
                 parent_pe_firm_type, hq_state
        """
    ).fetchall()

    # Firms whose only ABA holding delivers therapy in the client's home and runs
    # no centers. They correctly have zero clinics, but the ownership is real and
    # sourced, so they stay on the map with an honest label rather than being
    # dropped -- deleting them would understate PE presence in ABA.
    in_home_firms = {
        name
        for (name,) in conn.execute(
            """
            SELECT DISTINCT ppf.name
            FROM owner_entity oe
            JOIN parent_pe_firm ppf ON ppf.id = oe.parent_pe_firm_id
            WHERE oe.service_model = 'in_home' AND oe.superseded_by IS NULL
            """
        ).fetchall()
    }

    # clinic counts per firm, from the joined clinic set above
    clinics_per_firm: dict[str, int] = {}
    brands_per_firm: dict[str, set] = {}
    for cl in clinics:
        clinics_per_firm[cl["firm_id"]] = clinics_per_firm.get(cl["firm_id"], 0) + 1
        brands_per_firm.setdefault(cl["firm_id"], set()).add(cl["owner_id"])

    acquirers = []
    for firm_id, name, ftype, hq_state, brand_count in acquirer_rows:
        fid = str(firm_id)
        acquirers.append(
            {
                "id": fid,
                "name": name,
                "firm_type": ftype,
                "hq_state": hq_state,
                "clinic_count": clinics_per_firm.get(fid, 0),
                "brand_count": len(brands_per_firm.get(fid, set())),
            }
        )
    acquirers.sort(key=lambda a: a["clinic_count"], reverse=True)

    # --- brands (the ownership-chain view) -----------------------------------
    brands_map: dict[str, dict] = {}
    for cl in clinics:
        b = brands_map.setdefault(
            cl["owner_id"],
            {
                "owner_id": cl["owner_id"],
                "owner_name": cl["owner_name"],
                "firm_id": cl["firm_id"],
                "firm_name": cl["firm_name"],
                "firm_type": cl["firm_type"],
                "clinic_count": 0,
            },
        )
        b["clinic_count"] += 1
    brands = sorted(
        brands_map.values(), key=lambda b: b["clinic_count"], reverse=True
    )

    # --- states (the heatmap) ------------------------------------------------
    states_map: dict[str, int] = {}
    for cl in clinics:
        if cl["state"]:
            states_map[cl["state"]] = states_map.get(cl["state"], 0) + 1
    states = [
        {"state": s, "clinic_count": n}
        for s, n in sorted(states_map.items(), key=lambda kv: kv[1], reverse=True)
    ]

    # --- acquisition timeline (if the dataset publishes dated events) ---------
    # Plot only acquisition_event rows the dataset actually asserts, each with
    # its own source. If the table is empty the UI shows an honest placeholder.
    timeline = []
    try:
        ev_rows = conn.execute(
            """
            SELECT ae.id, ppf.name AS firm_name, oe.name AS brand_name,
                   ae.event_date, ae.event_date_circa, ae.event_type,
                   ae.deal_notes, ae.source_record_ids
            FROM acquisition_event ae
            LEFT JOIN parent_pe_firm ppf ON ppf.id = ae.parent_pe_firm_id
            LEFT JOIN owner_entity oe ON oe.id = ae.owner_entity_id
            WHERE ae.superseded_by IS NULL
            ORDER BY ae.event_date
            """
        ).fetchall()
        for eid, firm_name, brand_name, edate, circa, etype, notes, srids in ev_rows:
            timeline.append(
                {
                    "id": str(eid),
                    "firm_name": firm_name,
                    "brand_name": brand_name,
                    "date": edate.isoformat() if edate else None,
                    "date_circa": bool(circa),
                    "event_type": etype,
                    "notes": notes,
                    "sources": _source_urls(conn, srids),
                }
            )
    except Exception as exc:  # pragma: no cover - schema drift tolerance
        logger.warning("acquisition_event export skipped: %s", exc)

    # Keep an owner in the table if it currently has clinics, OR it has a sourced
    # history to show (Blackstone, which no longer owns any ABA clinics but whose
    # CARD acquisition and bankruptcy is the canonical story), OR its ABA holding
    # is an in-home provider that runs no centers (Moran, Cane). The last case is
    # a zero that means something specific, so it is labeled rather than dropped.
    firms_with_history = {e["firm_name"] for e in timeline if e["firm_name"]}
    for a in acquirers:
        a["former"] = a["clinic_count"] == 0 and a["name"] in firms_with_history
        a["in_home"] = a["clinic_count"] == 0 and a["name"] in in_home_firms
    acquirers = [
        a
        for a in acquirers
        if a["clinic_count"] > 0
        or a["name"] in firms_with_history
        or a["name"] in in_home_firms
    ]

    current_owner_count = sum(1 for a in acquirers if a["clinic_count"] > 0)
    pe_clinics = sum(1 for c in clinics if c["firm_type"] == "private_equity")
    located_clinics = sum(1 for c in clinics if c["lat"] is not None)

    # How many published clinics an owner's own directory or roster attests to. The
    # site states this split, so it has to come from the data: a clinic the owner
    # itself lists today cannot be a stale registration, and that is the single
    # strongest thing we can say about the dataset's freshness.
    directory_sourced = conn.execute(
        """
        SELECT count(*) FROM v_published_clinics c
        WHERE EXISTS (
            SELECT 1 FROM source_record sr
            WHERE sr.id = ANY(c.source_record_ids)
              AND sr.source_type = 'owner_location_directory'
        )
        """
    ).fetchone()[0]

    # The national market denominator, computed by scripts/compute_market_share.py
    # from the same bulk registry. Optional: if it has not been computed, the
    # dashboard simply omits the share rather than inventing one.
    market = None
    market_path = Path(__file__).resolve().parent.parent / "data" / "market" / "aba_market.json"
    try:
        market = json.loads(market_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.warning("no market file at %s; snapshot will carry no share", market_path)

    snapshot = {
        "meta": {
            "dataset_version": DATASET_VERSION,
            "resolver_version": _resolver_version(),
            "methodology_version": METHODOLOGY_VERSION,
            "generated_at": datetime.now(UTC).isoformat(),
            "source": "fundprint-data v_published_* views",
        },
        "totals": {
            "clinics": len(clinics),
            # Owners that currently hold at least one tracked clinic (excludes
            # former owners like Blackstone that are shown for their history).
            "acquirers": current_owner_count,
            "states": len(states),
            "pe_clinics": pe_clinics,
            "non_pe_clinics": len(clinics) - pe_clinics,
            # How many clinics carry a map coordinate (ZIP centroid). Disclosed
            # on the map so a reader knows the dot count vs the tracked count.
            "located_clinics": located_clinics,
            "directory_sourced_clinics": directory_sourced,
            "registry_only_clinics": len(clinics) - directory_sourced,
        },
        "market": market,
        "acquirers": acquirers,
        "brands": brands,
        "states": states,
        "clinics": clinics,
        "timeline": timeline,
    }
    return snapshot


def _resolver_version() -> str:
    try:
        from fundprint.resolve.version import RESOLVER_VERSION

        return RESOLVER_VERSION
    except Exception:  # pragma: no cover
        return "unknown"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--out",
        default="../fundprint-dashboard/data/snapshot.json",
        help="Path to write the snapshot JSON (default: dashboard data dir).",
    )
    args = p.parse_args()

    from fundprint import db

    with db.transaction() as conn:
        snapshot = build_snapshot(conn)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8")

    t = snapshot["totals"]
    logger.info(
        "wrote %s: %d clinics, %d acquirers, %d states, %d timeline events",
        out,
        t["clinics"],
        t["acquirers"],
        t["states"],
        len(snapshot["timeline"]),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
