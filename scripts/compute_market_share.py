"""Compute the national ABA market denominator, and private equity's share of it.

Until now Fundprint could only say "we found N clinics". That invites the fair
reply: "found out of how many?" The bulk provider registry answers it. It
contains every ABA provider organization in the country, so it gives a real
denominator.

## The one rule that makes this honest

**The numerator must be a strict subset of the denominator.** Both are computed
in a single pass over one universe, with one site key. It is therefore impossible
for the numerator to count something the denominator does not.

That has a consequence worth stating plainly: this is a REGISTRY-BASIS share.
Clinics Fundprint sources from an owner's own location directory are excluded
from BOTH sides, because the registry cannot see them and including them on the
numerator alone would inflate the share. The published clinic count is therefore
larger than the numerator here, and the two are not interchangeable.

## The universe

An organization counts as an ABA provider if it is a live (non-deactivated)
organization NPI AND either:
  * it carries an ABA taxonomy (behavior analyst / behavior technician), or
  * its name matches a tracked ABA brand.

The second clause matters: real ABA chains do not all register under the ABA
taxonomy (ACES and Behavioral Innovations do not). Without it, tracked PE clinics
would fall out of the denominator, which would inflate the share. It adds a few
hundred organizations to a ~17,500-organization universe.

## There is no "chain" denominator here, and there must not be one

This script used to define a chain as an operator with five or more sites, and
report private equity's share of *chain-run* clinics as the headline. That was
withdrawn, because the number could not be defended:

1. **The threshold was arbitrary.** Five, not three, not ten. Nothing in the data
   picked it and no sensitivity analysis defended it. A share whose value depends
   on an unargued cut is an editorial choice wearing the costume of a measurement.
2. **The denominator was endogenous to the thing being measured.** An operator is
   a "chain" *because* it has many sites, and many of them have many sites
   *because private equity rolled them up*. PE's own buying inflated the numerator
   and the denominator together. In the limit, a firm that bought forty four-site
   operators and merged them into one forty-site chain would barely move its
   "share of chain-run clinics" while its actual market power exploded. The
   measure was partly blind to the thing it existed to measure.
3. **Nothing else in the literature uses it,** so the number could not be compared
   with, or checked against, anyone else's.

What replaces it is not another constructed ratio. It is the facts:

  * the whole operator-size distribution, so a reader can see the market's shape
    and draw their own cut if they want one (`size_distribution`);
  * private equity's share of ALL ABA sites, which needs no threshold at all;
  * private equity's share of ABA sites WITHIN EACH STATE, which is the closest
    this data gets to a market-power measure, since no family chooses between a
    clinic in Denver and one in Tampa.

If a future reader wants a chain share, `size_distribution` gives them everything
they need to compute one, and it makes them state their own threshold out loud.
That is the point.

Usage:
    python scripts/compute_market_share.py --archive .cache/nppes/monthly.zip
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import logging
import re
import sys
import zipfile
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# NUCC taxonomies for behavior analysis.
ABA_TAXONOMIES = {"103K00000X", "106S00000X"}

# Buckets for the operator-size distribution. These are descriptive, not a
# threshold: nothing downstream filters on them and no published share depends on
# where a boundary falls. They exist so a reader can see the shape of the market.
SIZE_BUCKETS: list[tuple[str, int, int]] = [
    ("1", 1, 1),
    ("2-4", 2, 4),
    ("5-9", 5, 9),
    ("10-24", 10, 24),
    ("25+", 25, 10**9),
]

# A state needs a floor of ABA sites before a percentage of it means anything: a
# state with 6 sites where PE holds 3 is not "50% private equity" in any sense a
# reader would understand. Reported states are those at or above this floor; the
# rest are still counted in every national total.
MIN_STATE_SITES = 25

_SUFFIX = re.compile(
    r"\b(llc|l l c|inc|incorporated|corp|corporation|pc|p c|pllc|pa|lp|llp|ltd|co|"
    r"company|group|holdings|holding|services|service|therapy|therapies|of|the)\b",
    re.I,
)


def chain_stem(name: str) -> str:
    """Collapse legal-entity variants to one operator ("HOPEBRIDGE, LLC" -> "hopebridge")."""
    s = re.sub(r"[^a-z0-9 ]+", " ", (name or "").lower())
    s = _SUFFIX.sub(" ", s)
    return " ".join(s.split())[:40]


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--archive", type=Path, required=True, help="NPPES monthly zip.")
    p.add_argument(
        "--out",
        type=Path,
        default=Path("data/market/aba_market.json"),
        help="Where to write the market stats.",
    )
    args = p.parse_args()

    from fundprint import db
    from fundprint.acquire.nppes_bulk import _member, _sha256_file
    from fundprint.resolve.clinic_link import (
        is_admin_address,
        is_linkable_brand,
        match_owner,
        normalize,
        normalize_street,
        zip5,
    )

    conn = db.connect()
    # Tracked ABA owners, with the parent firm and its honest owner type.
    owners = conn.execute(
        """
        SELECT oe.name, ppf.name, ppf.firm_type, oe.service_model
        FROM owner_entity oe
        JOIN parent_pe_firm ppf ON ppf.id = oe.parent_pe_firm_id
        WHERE oe.superseded_by IS NULL AND oe.is_aba
        """
    ).fetchall()
    published_clinics = conn.execute("SELECT count(*) FROM v_published_clinics").fetchone()[0]
    conn.close()

    # In-home owners run no centers, and CARD is defunct: neither holds sites.
    linkable = [
        (normalize(o), o, firm, ftype)
        for o, firm, ftype, model in owners
        if is_linkable_brand(o) and model == "center_based"
    ]
    linkable.sort(key=lambda t: len(t[0]), reverse=True)
    brands = [(b, o) for b, o, _, _ in linkable]
    firm_of = {o: (firm, ftype) for _, o, firm, ftype in linkable}
    logger.info("tracked ABA brands: %d", len(brands))

    z = zipfile.ZipFile(args.archive)
    main_csv = _member(z, "npidata_pfile")
    pl_csv = _member(z, "pl_pfile")

    # NPI -> (operator stem, tracked owner or None)
    npi_meta: dict[str, tuple[str, str | None]] = {}
    # operator stem -> {(street, zip5)}
    org_sites: dict[str, set] = defaultdict(set)
    # tracked owner -> {(street, zip5)}
    owner_sites: dict[str, set] = defaultdict(set)
    # (street, zip5) -> state. One address has one state, so this is a plain map
    # and not a set: it lets every national figure be re-cut by state without a
    # second pass, and without a site key ever meaning two different things.
    site_state: dict[tuple[str, str], str] = {}

    def add(stem_: str, owner: str | None, street: str, zipc: str, state: str) -> None:
        st = normalize_street(street)
        if not st:
            return
        if owner and is_admin_address(owner, street):
            return  # a head office is not a clinic, on either side of the ratio
        key = (st, zip5(zipc))
        org_sites[stem_].add(key)
        if owner:
            owner_sites[owner].add(key)
        s = (state or "").strip().upper()[:2]
        if s:
            site_state.setdefault(key, s)

    with z.open(main_csv) as f:
        r = csv.reader(io.TextIOWrapper(f, encoding="utf-8", errors="replace"))
        hdr = next(r)
        ix = {c.strip(): i for i, c in enumerate(hdr)}
        tax = [ix[c] for c in hdr if c.startswith("Healthcare Provider Taxonomy Code_")]
        I_NPI, I_ENT = ix["NPI"], ix["Entity Type Code"]
        I_ORG = ix["Provider Organization Name (Legal Business Name)"]
        I_OTHER = ix["Provider Other Organization Name"]
        I_A1 = ix["Provider First Line Business Practice Location Address"]
        I_ZIP = ix["Provider Business Practice Location Address Postal Code"]
        I_ST = ix["Provider Business Practice Location Address State Name"]
        I_DEACT = ix["NPI Deactivation Date"]

        scanned = 0
        for row in r:
            scanned += 1
            if row[I_ENT] != "2" or row[I_DEACT].strip():
                continue
            legal = row[I_ORG]
            owner = match_owner(legal, brands) or match_owner(row[I_OTHER], brands)
            has_aba_taxonomy = any(row[c] in ABA_TAXONOMIES for c in tax)
            # The universe: an ABA taxonomy, or a name we know to be an ABA chain.
            if not (has_aba_taxonomy or owner):
                continue
            stem_ = chain_stem(legal)
            if len(stem_) < 6:
                continue
            npi_meta[row[I_NPI]] = (stem_, owner)
            add(stem_, owner, row[I_A1], row[I_ZIP], row[I_ST])

    logger.info("scanned %d NPI rows", scanned)

    with z.open(pl_csv) as f:
        r = csv.reader(io.TextIOWrapper(f, encoding="utf-8", errors="replace"))
        hdr = next(r)
        ix = {c.strip(): i for i, c in enumerate(hdr)}
        P_NPI = ix["NPI"]
        P_A1 = ix["Provider Secondary Practice Location Address- Address Line 1"]
        P_ZIP = ix["Provider Secondary Practice Location Address - Postal Code"]
        P_ST = ix["Provider Secondary Practice Location Address - State Name"]
        for row in r:
            meta = npi_meta.get(row[P_NPI])
            if meta:
                add(meta[0], meta[1], row[P_A1], row[P_ZIP], row[P_ST])

    # ---- the numbers -------------------------------------------------------
    # Every figure below is a count of DISTINCT sites: a union, never a sum of
    # per-group sizes. This is the difference between the methodology's claim and
    # its implementation, and it was wrong in the direction that flattered us.
    #
    # One address can carry more than one legal entity, and therefore more than one
    # operator stem and more than one tracked brand. KKR registers BlueSprig and
    # Florida Autism Center at the same Ocala suite; LEARN registers Total Spectrum
    # and Wisconsin Early Autism Project at the same six Wisconsin streets. Summing
    # `len()` across owners counted such a site once per brand, so the numerator was
    # not the strict subset of the denominator this file claims it is: it was a
    # multiset that could, in principle, exceed it. Unions make the claim true.
    all_universe: set = set().union(*org_sites.values()) if org_sites else set()
    all_sites = len(all_universe)

    tracked_universe: set = set().union(*owner_sites.values()) if owner_sites else set()
    tracked_sites = len(tracked_universe)
    pe_owner_sets = [
        s for o, s in owner_sites.items() if firm_of[o][1] == "private_equity"
    ]
    pe_universe: set = set().union(*pe_owner_sets) if pe_owner_sets else set()
    pe_sites = len(pe_universe)

    # The operator-size distribution. This is the honest replacement for the old
    # chain threshold: it states the shape of the market and lets the reader pick
    # their own cut, out loud, instead of inheriting ours in silence.
    size_distribution = []
    for label, lo, hi in SIZE_BUCKETS:
        stems = [k for k, v in org_sites.items() if lo <= len(v) <= hi]
        sites = set().union(*(org_sites[k] for k in stems)) if stems else set()
        size_distribution.append(
            {
                "sites_per_operator": label,
                "operators": len(stems),
                # A union again, not a sum: two operators can share one address.
                "sites": len(sites),
            }
        )

    # Per-state shares. This is as close as the registry gets to market power: a
    # national share of a fragmented profession says little, but the share of the
    # ABA sites in one state is something a family, a regulator and a legislator
    # in that state can all act on.
    by_state: dict[str, dict[str, int]] = defaultdict(
        lambda: {"aba_sites": 0, "tracked_sites": 0, "private_equity_sites": 0}
    )
    for key, st in site_state.items():
        row = by_state[st]
        row["aba_sites"] += 1
        if key in tracked_universe:
            row["tracked_sites"] += 1
        if key in pe_universe:
            row["private_equity_sites"] += 1

    states = [
        {
            "state": st,
            **v,
            "private_equity_share": round(100 * v["private_equity_sites"] / v["aba_sites"], 1),
            "tracked_share": round(100 * v["tracked_sites"] / v["aba_sites"], 1),
        }
        for st, v in by_state.items()
        if v["aba_sites"] >= MIN_STATE_SITES
    ]
    states.sort(key=lambda r: (-r["private_equity_share"], -r["private_equity_sites"]))

    market = {
        "meta": {
            "basis": "registry",
            "source": "NPPES monthly bulk dissemination file",
            "archive": args.archive.name,
            "archive_sha256": _sha256_file(args.archive),
            "computed_at": datetime.now(UTC).isoformat(),
            "min_state_sites": MIN_STATE_SITES,
            "note": (
                "Numerator and denominator are computed in one pass over one "
                "universe with one site key, so the numerator is a strict subset "
                "of the denominator. Clinics Fundprint sources from an owner's own "
                "location directory are excluded from BOTH sides, because the "
                "registry cannot see them. The published clinic count is therefore "
                "larger than the numerator here and is not interchangeable with it. "
                "No chain threshold is applied anywhere: the operator-size "
                "distribution is published instead, so any cut is the reader's and "
                "is stated out loud."
            ),
        },
        "denominator": {
            "aba_organizations": len(org_sites),
            "aba_sites": all_sites,
        },
        "numerator": {
            "tracked_sites": tracked_sites,
            "private_equity_sites": pe_sites,
        },
        "share": {
            "tracked_of_all_sites": round(100 * tracked_sites / all_sites, 1),
            "private_equity_of_all_sites": round(100 * pe_sites / all_sites, 1),
        },
        "size_distribution": size_distribution,
        "states": states,
        "context": {
            "published_clinics": published_clinics,
            "why_larger": (
                "The published clinic count also includes centers read from owners' "
                "own public location directories, which the registry does not list."
            ),
        },
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(market, indent=2), encoding="utf-8")

    d, n, s = market["denominator"], market["numerator"], market["share"]
    logger.info("--- the national ABA market, from the registry ---")
    logger.info("  ABA provider organizations : %6d", d["aba_organizations"])
    logger.info("  ABA sites (all)            : %6d", d["aba_sites"])
    logger.info("--- operator size distribution ---")
    for b in size_distribution:
        logger.info(
            "  %-6s sites/operator : %6d operators, %6d sites",
            b["sites_per_operator"],
            b["operators"],
            b["sites"],
        )
    logger.info("--- Fundprint's tracked owners, same basis ---")
    logger.info("  tracked sites              : %6d", n["tracked_sites"])
    logger.info("  ...private equity only     : %6d", n["private_equity_sites"])
    logger.info("--- share (no threshold anywhere) ---")
    logger.info("  tracked share of ALL ABA sites : %5.1f%%", s["tracked_of_all_sites"])
    logger.info("  PE share of ALL ABA sites      : %5.1f%%", s["private_equity_of_all_sites"])
    logger.info("--- most private-equity-concentrated states (>=%d sites) ---", MIN_STATE_SITES)
    for r_ in states[:8]:
        logger.info(
            "  %s : %3d of %4d ABA sites  (%.1f%%)",
            r_["state"],
            r_["private_equity_sites"],
            r_["aba_sites"],
            r_["private_equity_share"],
        )
    logger.info("wrote %s", args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
