"""Compute the national ABA market denominator, and private equity's share of it.

Until now Fundprint could only say "we found N clinics". That invites the fair
reply: "found out of how many?" The bulk provider registry answers it. It
contains every ABA provider organization in the country, so it gives a real
denominator, and a share is a far stronger claim than a count.

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

## The two shares, and why the second is the story

Roughly three quarters of ABA providers are single-site independents. Private
equity does not buy those; it buys chains. So the share of ALL sites understates
what consolidation has done, and the share of MULTI-SITE CHAIN sites is the
number that describes the market. Both are reported. Neither is hidden.

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

# A "chain" for market-structure purposes. Private equity buys chains, not
# single-site independents, so this is the population that can consolidate.
CHAIN_MIN_SITES = 5

_SUFFIX = re.compile(
    r"\b(llc|l l c|inc|incorporated|corp|corporation|pc|p c|pllc|pa|lp|llp|ltd|co|"
    r"company|group|holdings|holding|services|service|therapy|therapies|of|the)\b",
    re.I,
)


def chain_stem(name: str) -> str:
    """Collapse legal-entity variants to one chain ("HOPEBRIDGE, LLC" -> "hopebridge")."""
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

    # NPI -> (chain stem, tracked owner or None)
    npi_meta: dict[str, tuple[str, str | None]] = {}
    # chain stem -> {(street, zip5)}
    chain_sites: dict[str, set] = defaultdict(set)
    # tracked owner -> {(street, zip5)}
    owner_sites: dict[str, set] = defaultdict(set)

    def add(stem_: str, owner: str | None, street: str, zipc: str) -> None:
        st = normalize(street)
        if not st:
            return
        if owner and is_admin_address(owner, street):
            return  # a head office is not a clinic, on either side of the ratio
        key = (st, zip5(zipc))
        chain_sites[stem_].add(key)
        if owner:
            owner_sites[owner].add(key)

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
            add(stem_, owner, row[I_A1], row[I_ZIP])

    logger.info("scanned %d NPI rows", scanned)

    with z.open(pl_csv) as f:
        r = csv.reader(io.TextIOWrapper(f, encoding="utf-8", errors="replace"))
        hdr = next(r)
        ix = {c.strip(): i for i, c in enumerate(hdr)}
        P_NPI = ix["NPI"]
        P_A1 = ix["Provider Secondary Practice Location Address- Address Line 1"]
        P_ZIP = ix["Provider Secondary Practice Location Address - Postal Code"]
        for row in r:
            meta = npi_meta.get(row[P_NPI])
            if meta:
                add(meta[0], meta[1], row[P_A1], row[P_ZIP])

    # ---- the numbers -------------------------------------------------------
    # Every figure below is a count of DISTINCT sites: a union, never a sum of
    # per-group sizes. This is the difference between the methodology's claim and
    # its implementation, and it was wrong in the direction that flattered us.
    #
    # One address can carry more than one legal entity, and therefore more than one
    # chain stem and more than one tracked brand. KKR registers BlueSprig and
    # Florida Autism Center at the same Ocala suite; LEARN registers Total Spectrum
    # and Wisconsin Early Autism Project at the same six Wisconsin streets. Summing
    # `len()` across owners counted such a site once per brand, so the numerator was
    # not the strict subset of the denominator this file claims it is: it was a
    # multiset that could, in principle, exceed it. Unions make the claim true.
    all_sites = len(set().union(*chain_sites.values())) if chain_sites else 0
    chains = {k: v for k, v in chain_sites.items() if len(v) >= CHAIN_MIN_SITES}
    chain_universe: set = set().union(*chains.values()) if chains else set()
    chain_site_total = len(chain_universe)

    tracked_universe: set = set().union(*owner_sites.values()) if owner_sites else set()
    tracked_sites = len(tracked_universe)
    pe_owner_sets = [
        s for o, s in owner_sites.items() if firm_of[o][1] == "private_equity"
    ]
    pe_universe: set = set().union(*pe_owner_sets) if pe_owner_sets else set()
    pe_sites = len(pe_universe)
    # Set intersection, so a tracked site inside a chain is counted exactly once no
    # matter how many brands or chain stems that one address registers under.
    tracked_in_chains = len(tracked_universe & chain_universe)
    # Private equity ALONE, on the same basis. The tracked figure also contains a
    # pension fund and a family office, so a headline that says "private equity"
    # must be built on this number and not on that one.
    pe_in_chains = len(pe_universe & chain_universe)

    market = {
        "meta": {
            "basis": "registry",
            "source": "NPPES monthly bulk dissemination file",
            "archive": args.archive.name,
            "archive_sha256": _sha256_file(args.archive),
            "computed_at": datetime.now(UTC).isoformat(),
            "chain_min_sites": CHAIN_MIN_SITES,
            "note": (
                "Numerator and denominator are computed in one pass over one "
                "universe with one site key, so the numerator is a strict subset "
                "of the denominator. Clinics Fundprint sources from an owner's own "
                "location directory are excluded from BOTH sides, because the "
                "registry cannot see them. The published clinic count is therefore "
                "larger than the numerator here and is not interchangeable with it."
            ),
        },
        "denominator": {
            "aba_organizations": len(chain_sites),
            "aba_sites": all_sites,
            "chains": len(chains),
            "chain_sites": chain_site_total,
            "independent_sites": all_sites - chain_site_total,
        },
        "numerator": {
            "tracked_sites": tracked_sites,
            "private_equity_sites": pe_sites,
            "tracked_sites_within_chains": tracked_in_chains,
            "private_equity_sites_within_chains": pe_in_chains,
        },
        "share": {
            "tracked_of_all_sites": round(100 * tracked_sites / all_sites, 1),
            "private_equity_of_all_sites": round(100 * pe_sites / all_sites, 1),
            "tracked_of_chain_sites": round(100 * tracked_in_chains / chain_site_total, 1),
            "private_equity_of_chain_sites": round(
                100 * pe_in_chains / chain_site_total, 1
            ),
        },
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
    logger.info(
        "    of which single-site/small: %6d  (%.0f%%)",
        d["independent_sites"],
        100 * d["independent_sites"] / d["aba_sites"],
    )
    logger.info(
        "    of which in chains (>=%d)  : %6d  across %d chains",
        CHAIN_MIN_SITES,
        d["chain_sites"],
        d["chains"],
    )
    logger.info("--- Fundprint's tracked owners, same basis ---")
    logger.info("  tracked sites              : %6d", n["tracked_sites"])
    logger.info("  ...private equity only     : %6d", n["private_equity_sites"])
    logger.info("--- share ---")
    logger.info("  tracked share of ALL ABA sites   : %5.1f%%", s["tracked_of_all_sites"])
    logger.info("  PE share of ALL ABA sites        : %5.1f%%", s["private_equity_of_all_sites"])
    logger.info(
        "  tracked share of CHAIN sites     : %5.1f%%  <-- the story",
        s["tracked_of_chain_sites"],
    )
    logger.info(
        "  PE-only share of CHAIN sites     : %5.1f%%",
        s["private_equity_of_chain_sites"],
    )
    logger.info("wrote %s", args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
