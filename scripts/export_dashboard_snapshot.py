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
from datetime import UTC, date, datetime
from pathlib import Path

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# The dashboard pins exactly one dataset_version per deploy. Bump this in step
# with the Hugging Face release the snapshot is cut from.
DATASET_VERSION = "2026.07-beta"
# Bumped with the platform coverage denominator (section 8d): coverage is now a
# published fraction, 21 of 32 known PE-backed platforms, measured against an
# outside list rather than one of our own. No clinic or share figure moved, but a
# new published measure is a methodology change under section 12. The pin must move
# in the same commit as the numbers, or a reader who follows it lands on a document
# describing different ones.
METHODOLOGY_VERSION = "2026.07-coverage-v1"


# source_record_id -> (url, type), loaded once. The snapshot resolves provenance
# for ~1,800 clinics plus events; one round-trip per clinic (times two, for URL and
# type) turned a few-second export into minutes, so the whole table is read once.
_SRC_INDEX: dict[str, tuple[str | None, str | None]] = {}


def _prime_source_cache(conn) -> None:
    _SRC_INDEX.clear()
    for sid, url, stype in conn.execute(
        "SELECT id, source_url, source_type FROM source_record"
    ).fetchall():
        _SRC_INDEX[str(sid)] = (url, stype)


def _source_urls(conn, source_record_ids) -> list[str]:
    """Resolve an array of source_record ids to their public URLs, de-duped."""
    if not source_record_ids:
        return []
    if not _SRC_INDEX:
        _prime_source_cache(conn)
    return sorted(
        {
            url
            for sid in source_record_ids
            if (url := _SRC_INDEX.get(str(sid), (None, None))[0])
        }
    )


def _source_types(conn, source_record_ids) -> set[str]:
    """The distinct source_type values behind a clinic's source_record ids."""
    if not source_record_ids:
        return set()
    if not _SRC_INDEX:
        _prime_source_cache(conn)
    return {
        t
        for sid in source_record_ids
        if (t := _SRC_INDEX.get(str(sid), (None, None))[1])
    }


# The four confidence dimensions, each derived, not asserted. The single old label
# ("Strong name match") answered one question the reader was never asking and hid
# the four they were: is this clinic open, is it at this address, is it a centre or
# in-home, and who owns it. Every value below is reproducible from the published
# dataset alone: the clinic's own source URLs (is one an owner directory?), its
# registry freshness date, its owner's service model, and the type of the ownership
# link. No staging table, no judgement call, nothing a reader with the Hugging Face
# download could not recompute.
_DIRECTORY_TYPE = "owner_location_directory"
_REGISTRY_TYPES = {"nppes", "nppes_bulk"}
# A registry registration reports existence-EVER, never existence-NOW; the last time
# the provider touched it is the only freshness signal it carries. Under three years
# is current; six years cold is a likely-closed ghost. These match section 9.
_REGISTRY_CURRENT_YEARS = 3
_REGISTRY_STALE_YEARS = 6


def _ownership_basis(conn) -> dict[str, str]:
    """Per owner: 'curated' if a dated acquisition announcement backs the ownership
    link, else 'portfolio' (the PE firm's own portfolio page lists the company).
    Both are direct assertions of ownership; a curated announcement is the stronger,
    dated grade."""
    rows = conn.execute(
        """
        SELECT l.owner_entity_id,
               bool_or(sr.source_type LIKE 'curated%%') AS curated
        FROM v_published_pe_links l
        JOIN resolution_claim rc
          ON rc.owner_entity_id = l.owner_entity_id
         AND rc.claim_type = 'owner_to_pe_firm'
        JOIN source_record sr ON sr.id = ANY(rc.source_record_ids)
        GROUP BY l.owner_entity_id
        """
    ).fetchall()
    return {str(oid): ("curated" if curated else "portfolio") for oid, curated in rows}


def _confidence(
    *,
    source_types: set[str],
    registry_last_updated: date | None,
    service_model: str | None,
    firm_type: str | None,
    ownership_basis: str,
    today: date,
) -> dict:
    """Grade one clinic on the four dimensions and an overall (its weakest link)."""
    owner_listed = _DIRECTORY_TYPE in source_types
    in_home = service_model == "in_home"

    if owner_listed:
        # The owner names this site today: it is open, at this address, a centre.
        open_basis, overall = "owner_listed", "owner_verified"
    elif in_home:
        open_basis, overall = "in_home", "in_home"
    elif registry_last_updated is None:
        open_basis, overall = "registry_undated", "registry_undated"
    else:
        years = (today - registry_last_updated).days / 365.25
        if years < _REGISTRY_CURRENT_YEARS:
            open_basis, overall = "registry_current", "registry_current"
        elif years < _REGISTRY_STALE_YEARS:
            open_basis, overall = "registry_aging", "registry_aging"
        else:
            open_basis, overall = "registry_stale", "registry_stale"

    return {
        "overall": overall,
        "open": open_basis,
        "address": "owner_stated" if owner_listed else "registry_filed",
        "site_type": "center" if owner_listed else ("in_home" if in_home else "unverified"),
        "ownership": {"firm_type": firm_type, "basis": ownership_basis},
        "registry_last_updated": (
            registry_last_updated.isoformat() if registry_last_updated else None
        ),
    }


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
        SELECT c.id, c.name, oe.trade_name, addr.address_line1, c.city, c.state, c.zip, c.npi,
               c.confidence_score, c.confidence_method, c.source_record_ids,
               l.owner_entity_id, l.owner_entity_name,
               l.parent_pe_firm_id, l.parent_pe_firm_name, l.parent_pe_firm_type,
               addr.registry_last_updated, oe.service_model
        FROM v_published_clinics c
        JOIN clinic addr ON addr.id = c.id
        JOIN owner_entity oe ON oe.id = c.owner_entity_id
        JOIN v_published_pe_links l ON l.owner_entity_id = oe.id
        ORDER BY c.name
        """
    ).fetchall()

    ownership_basis = _ownership_basis(conn)
    today = datetime.now(UTC).date()
    exact_centroids, zip3_centroids = _load_centroids()
    clinics = []
    for r in clinic_rows:
        (cid, name, trade_name, address, city, state, zipc, npi, conf, method, srids,
         owner_id, owner_name, firm_id, firm_name, firm_type,
         registry_last_updated, service_model) = r
        # Title every card with the brand families know, not the legal name the
        # registry carries. Registry-sourced rows arrive in all caps (FLORIDA AUTISM
        # CENTER, BUCK JACK LLC, HELPING HANDS FAMILY MARYLAND LLC) while an owner's
        # own directory rows already carry the mixed-case brand. Two rules cover it:
        # an explicit trade name wins (Buck Jack -> Woven Care), and otherwise an
        # all-caps legal title falls back to the owner's brand. The address, always
        # shown beneath the title, is what distinguishes sibling centres.
        name = trade_name or name
        if name and name == name.upper() and any(ch.isalpha() for ch in name):
            name = owner_name
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
                "confidence": _confidence(
                    source_types=_source_types(conn, srids),
                    registry_last_updated=registry_last_updated,
                    service_model=service_model,
                    firm_type=firm_type,
                    ownership_basis=ownership_basis.get(str(owner_id), "portfolio"),
                    today=today,
                ),
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
            SELECT ae.id, ppf.name AS firm_name,
                   COALESCE(oe.trade_name, oe.name) AS brand_name,
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

    # The dataset graded by its strongest-to-weakest confidence. This is the honest
    # answer to "how do you know?" at the level of the whole dataset, and it is what
    # the old single label could never give: the share of clinics an owner's own
    # directory attests versus the share resting on a registry record alone.
    confidence_counts: dict[str, int] = {}
    for c in clinics:
        overall = c["confidence"]["overall"]
        confidence_counts[overall] = confidence_counts.get(overall, 0) + 1

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

    # The platform coverage denominator, built by build_platform_denominator.py
    # against PESP's published appendix. Optional for the same reason as market:
    # if it has not been built, the dashboard shows the slot as pending rather
    # than inventing a coverage fraction.
    coverage = None
    coverage_path = (
        Path(__file__).resolve().parent.parent / "data" / "platforms" / "coverage.json"
    )
    try:
        coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.warning("no coverage file at %s; snapshot will carry no denominator", coverage_path)

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
            # Every clinic bucketed by its overall confidence (owner_verified,
            # registry_current, registry_aging, registry_undated, registry_stale,
            # in_home). The dashboard reads this for the dataset-level breakdown.
            "confidence": confidence_counts,
        },
        "market": market,
        "coverage": coverage,
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
