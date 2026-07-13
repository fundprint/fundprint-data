"""Draw a stratified random sample of clinics for hand verification.

This answers the question the dataset cannot answer about itself: **how often is
a published clinic actually right?** The answer becomes a published verification
rate with a confidence interval, which is the difference between "a kid scraped
some websites" and "an audited dataset".

## Why three strata and not one

Sampling only the clinics we publish measures **precision**: of the things we
claim, how many are true. That is necessary and it is not sufficient, because a
precision-only sample is structurally incapable of finding the errors that matter
most: the clinics we *missed*, and the owners we *failed to attribute*. A dataset
can be 100% precise and still be missing half the market, and a sample drawn only
from its own rows will report a perfect score while it happens.

So the sample is drawn from three populations:

  * ``pe``        - clinics we attribute to a private-equity firm. The core claim.
  * ``non_pe``    - clinics we attribute to a pension fund, family office or search
                    fund. Same checks, but these also test whether the OWNER TYPE
                    is right, which is the distinction the whole headline rests on.
  * ``unclaimed`` - ABA sites that exist in the federal registry and that Fundprint
                    attributes to NOBODY. This is the only stratum that can catch a
                    false negative. If a reviewer finds a financial owner behind one
                    of these, the dataset is undercounting, and we would never have
                    learned it from the other two strata.

Within ``pe`` and ``non_pe``, the draw is further stratified by **source**, because
the two sources fail in different ways. A registry-sourced clinic can be a *ghost*
(the registry never marks a closed clinic closed). A directory-sourced clinic
cannot be a ghost, because the owner is saying it is open today, but its address is
parsed from HTML and can be wrong. Reporting one blended accuracy number would hide
both.

The draw is seeded and the seed is written into the sample file, so the exact same
150 clinics can be redrawn by anyone who wants to check us.

Usage:
    # the full sample, including the unclaimed stratum (needs the registry archive)
    python scripts/build_verification_sample.py --archive .cache/nppes/monthly.zip

    # published clinics only, no archive pass (faster, but measures precision only)
    python scripts/build_verification_sample.py --skip-unclaimed

    # then open the HTML it prints, label every row, and download the CSV
    python scripts/score_verification.py review_verify_<id>.csv
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import logging
import random
import sys
import uuid
import zipfile
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

ABA_TAXONOMIES = {"103K00000X", "106S00000X"}

# How the 150 is split. `pe` dominates because it is the claim the project is
# built on, but the other two are not decoration: `non_pe` tests the owner-type
# label that the headline depends on, and `unclaimed` is the only stratum that can
# ever tell us we are missing clinics.
DEFAULT_PLAN = {"pe": 110, "non_pe": 20, "unclaimed": 20}


def _registry_sourced(urls: list[str]) -> bool:
    return any(("npiregistry" in u or "nppes" in u.lower() or "cms.gov" in u) for u in urls)


def _fetch_published(conn) -> list[dict]:
    """Every published clinic, with its owner, parent firm, and source URLs."""
    # The published view is deliberately narrow and carries no street address, so
    # join back to `clinic` for the address and the registry freshness date. The
    # view still decides membership: only what it lists can be sampled.
    rows = conn.execute(
        """
        SELECT v.id, v.name, cl.address_line1, v.city, v.state, v.zip, v.npi,
               cl.registry_last_updated,
               oe.name AS owner_name,
               f.name AS firm_name,
               f.firm_type,
               ARRAY_AGG(sr.source_url) FILTER (WHERE sr.source_url IS NOT NULL) AS urls
        FROM v_published_clinics v
        JOIN clinic cl ON cl.id = v.id
        JOIN owner_entity oe ON oe.id = v.owner_entity_id
        JOIN v_published_pe_links l ON l.owner_entity_id = v.owner_entity_id
        JOIN parent_pe_firm f ON f.id = l.parent_pe_firm_id
        LEFT JOIN source_record sr ON sr.id = ANY(v.source_record_ids)
        GROUP BY v.id, v.name, cl.address_line1, v.city, v.state, v.zip, v.npi,
                 cl.registry_last_updated, oe.name, f.name, f.firm_type
        """
    ).fetchall()
    out = []
    for (
        cid, name, addr, city, state, zipc, npi, last_upd,
        owner, firm, firm_type, urls,
    ) in rows:
        urls = [str(u) for u in (urls or [])]
        out.append(
            {
                "clinic_id": str(cid),
                "name": name,
                "address": addr,
                "city": city,
                "state": state,
                "zip": zipc,
                "npi": npi,
                "registry_last_updated": last_upd.isoformat() if last_upd else None,
                "claimed_owner": owner,
                "claimed_firm": firm,
                "claimed_firm_type": firm_type,
                "source_urls": urls,
                "source": "registry" if _registry_sourced(urls) else "directory",
                "stratum": "pe" if firm_type == "private_equity" else "non_pe",
            }
        )
    return out


def _sample_unclaimed(archive: Path, conn, rng: random.Random, n: int) -> list[dict]:
    """Reservoir-sample ABA sites in the registry that we attribute to nobody.

    Uses the same brand matcher the pipeline uses, so "unclaimed" here means
    exactly what it means everywhere else in the codebase: no tracked ABA owner's
    name matches this organization. A reviewer who finds a financial owner behind
    one of these has found a clinic we are missing.
    """
    from fundprint.acquire.nppes_bulk import _member
    from fundprint.resolve.clinic_link import is_linkable_brand, match_owner, normalize

    owners = conn.execute(
        """
        SELECT oe.name FROM owner_entity oe
        WHERE oe.superseded_by IS NULL AND oe.is_aba
        """
    ).fetchall()
    brands = [(normalize(o[0]), o[0]) for o in owners if is_linkable_brand(o[0])]
    brands.sort(key=lambda t: len(t[0]), reverse=True)

    z = zipfile.ZipFile(archive)
    reservoir: list[dict] = []
    seen = 0

    with z.open(_member(z, "npidata_pfile")) as f:
        r = csv.reader(io.TextIOWrapper(f, encoding="utf-8", errors="replace"))
        hdr = next(r)
        ix = {c.strip(): i for i, c in enumerate(hdr)}
        tax = [ix[c] for c in hdr if c.startswith("Healthcare Provider Taxonomy Code_")]
        I_NPI, I_ENT = ix["NPI"], ix["Entity Type Code"]
        I_ORG = ix["Provider Organization Name (Legal Business Name)"]
        I_A1 = ix["Provider First Line Business Practice Location Address"]
        I_CITY = ix["Provider Business Practice Location Address City Name"]
        I_ST = ix["Provider Business Practice Location Address State Name"]
        I_ZIP = ix["Provider Business Practice Location Address Postal Code"]
        I_DEACT = ix["NPI Deactivation Date"]

        for row in r:
            if row[I_ENT] != "2" or row[I_DEACT].strip():
                continue
            if not any(row[c] in ABA_TAXONOMIES for c in tax):
                continue
            legal = row[I_ORG]
            if match_owner(legal, brands):
                continue  # we DO claim this one; it belongs in the pe/non_pe strata
            if not (row[I_A1] or "").strip():
                continue

            seen += 1
            item = {
                "clinic_id": f"unclaimed:{row[I_NPI]}",
                "name": legal,
                "address": row[I_A1],
                "city": row[I_CITY],
                "state": row[I_ST],
                "zip": row[I_ZIP],
                "npi": row[I_NPI],
                "registry_last_updated": None,
                "claimed_owner": None,
                "claimed_firm": None,
                "claimed_firm_type": None,
                "source_urls": [
                    f"https://npiregistry.cms.hhs.gov/provider-view/{row[I_NPI]}"
                ],
                "source": "registry",
                "stratum": "unclaimed",
            }
            # Reservoir sampling: one pass, uniform draw, no need to hold 17k rows.
            if len(reservoir) < n:
                reservoir.append(item)
            else:
                j = rng.randrange(seen)
                if j < n:
                    reservoir[j] = item

    logger.info("unclaimed ABA organizations in registry: %d (sampled %d)", seen, len(reservoir))
    return reservoir


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--archive", type=Path, help="NPPES monthly zip, for the unclaimed stratum.")
    p.add_argument("--skip-unclaimed", action="store_true", help="Published clinics only.")
    p.add_argument("--n", type=int, default=150, help="Total sample size (default 150).")
    p.add_argument("--seed", type=int, default=None, help="Random seed (recorded in the file).")
    p.add_argument("--out-dir", type=Path, default=Path("samples"))
    args = p.parse_args()

    if not args.skip_unclaimed and not args.archive:
        p.error("need --archive for the unclaimed stratum, or pass --skip-unclaimed")

    seed = args.seed if args.seed is not None else random.randrange(2**31)
    rng = random.Random(seed)

    from fundprint import db

    conn = db.connect()
    published = _fetch_published(conn)
    logger.info("published clinics: %d", len(published))

    # Scale the plan to the requested n, keeping the shape.
    scale = args.n / sum(DEFAULT_PLAN.values())
    plan = {k: max(1, round(v * scale)) for k, v in DEFAULT_PLAN.items()}
    if args.skip_unclaimed:
        plan.pop("unclaimed", None)

    pools: dict[str, list[dict]] = defaultdict(list)
    for row in published:
        pools[row["stratum"]].append(row)

    picked: list[dict] = []

    for stratum in ("pe", "non_pe"):
        want = plan.get(stratum, 0)
        pool = pools[stratum]
        if not pool or not want:
            continue
        # Sub-stratify by source: a registry clinic and a directory clinic fail in
        # different ways, and a blended rate would hide both.
        by_source: dict[str, list[dict]] = defaultdict(list)
        for row in pool:
            by_source[row["source"]].append(row)
        for src, bucket in sorted(by_source.items()):
            share = max(1, round(want * len(bucket) / len(pool)))
            take = min(share, len(bucket))
            for row in rng.sample(bucket, take):
                picked.append({**row, "sub_stratum": f"{stratum}/{src}"})
            logger.info("  %s / %s: drew %d of %d available", stratum, src, take, len(bucket))

    if not args.skip_unclaimed:
        want = plan["unclaimed"]
        for row in _sample_unclaimed(args.archive, conn, rng, want):
            picked.append({**row, "sub_stratum": "unclaimed/registry"})

    conn.close()
    rng.shuffle(picked)  # so the reviewer cannot pattern-match a stratum by position

    run_id = str(uuid.uuid4())[:8]
    sheet = {
        "run_id": run_id,
        "seed": seed,
        "drawn_at": datetime.now(UTC).isoformat(),
        "total_drawn": len(picked),
        "plan": plan,
        "note": (
            "Stratified random sample for hand verification. `pe` and `non_pe` "
            "measure precision (are the claims we publish true). `unclaimed` "
            "measures recall (are there financial owners we are missing) and is "
            "the only stratum that can ever detect a false negative. Redraw the "
            "identical sample with --seed."
        ),
        "rows": picked,
    }

    args.out_dir.mkdir(parents=True, exist_ok=True)
    dest = args.out_dir / f"verify_{run_id}.json"
    dest.write_text(json.dumps(sheet, indent=2), encoding="utf-8")

    counts: dict[str, int] = defaultdict(int)
    for row in picked:
        counts[row["sub_stratum"]] += 1
    logger.info("--- sample drawn: %d rows, seed %d ---", len(picked), seed)
    for k in sorted(counts):
        logger.info("  %-22s %3d", k, counts[k])
    logger.info("wrote %s", dest)
    logger.info("next: python scripts/build_verification_html.py %s", dest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
