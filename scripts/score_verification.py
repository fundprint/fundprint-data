"""Score a reviewed verification sample into a publishable accuracy rate.

Reads the CSV the HTML tool exports and prints the numbers that go on the site
and in the methodology: an accuracy rate per stratum, with a 95% confidence
interval, plus the ghost rate and any false negatives found.

## Two decisions worth arguing with

**Wilson, not the textbook interval.** The normal approximation (p +/- 1.96*sqrt(
p(1-p)/n)) is wrong exactly where we will be using it: small strata and rates near
1.0, where it produces intervals that run above 100%. Wilson stays inside [0,1] and
is well behaved at n=20 and p=1.0, which is the case we should expect and must not
misreport.

**"Cannot tell" counts against us.** A clinic the reviewer could not verify is not
thrown away and is not counted as correct: it goes in the denominator as a
non-verification. Dropping unverifiable rows would quietly inflate the rate by
discarding the hardest cases, which are precisely the ones most likely to be wrong.
This makes the published number a conservative floor, which is what it should be.

Usage:
    python scripts/score_verification.py review_verify_<id>.csv
"""

from __future__ import annotations

import argparse
import csv
import math
import sys
from collections import defaultdict
from pathlib import Path


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for k successes in n trials."""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    centre = p + z * z / (2 * n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (max(0.0, (centre - half) / d), min(1.0, (centre + half) / d))


def pct(x: float) -> str:
    return f"{100 * x:.1f}%"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("csv", type=Path)
    args = p.parse_args()

    rows = list(csv.DictReader(args.csv.open(encoding="utf-8")))
    labelled = [r for r in rows if r.get("exists")]
    if not labelled:
        print("No labelled rows. Did you finish the review and export the CSV?")
        return 1

    print(f"\n{len(labelled)} of {len(rows)} rows labelled.\n")

    claimed = [r for r in labelled if r["stratum"] != "unclaimed"]
    unclaimed = [r for r in labelled if r["stratum"] == "unclaimed"]

    # --- precision: of the clinics we publish, how many are right in every way? --
    by_sub: dict[str, list[dict]] = defaultdict(list)
    for r in claimed:
        by_sub[r["sub_stratum"]].append(r)

    print("PRECISION: is a published clinic correct?")
    print("  A clinic is CORRECT only if it is open, the brand is right, and the")
    print("  parent firm is right. 'Cannot tell' counts as not verified.\n")
    print(f"  {'stratum':<22}{'correct':>9}{'n':>5}{'rate':>9}   95% CI")
    for sub in sorted(by_sub):
        bucket = by_sub[sub]
        ok = sum(
            1
            for r in bucket
            if r["exists"] == "yes" and r["owner"] == "yes" and r["firm"] == "yes"
        )
        lo, hi = wilson(ok, len(bucket))
        print(
            f"  {sub:<22}{ok:>9}{len(bucket):>5}{pct(ok / len(bucket)):>9}   "
            f"[{pct(lo)}, {pct(hi)}]"
        )

    ok_all = sum(
        1
        for r in claimed
        if r["exists"] == "yes" and r["owner"] == "yes" and r["firm"] == "yes"
    )
    lo, hi = wilson(ok_all, len(claimed))
    print(
        f"  {'ALL PUBLISHED':<22}{ok_all:>9}{len(claimed):>5}"
        f"{pct(ok_all / len(claimed)):>9}   [{pct(lo)}, {pct(hi)}]"
    )

    # --- the ghost rate, which the methodology already promises to disclose ------
    ghosts = [r for r in claimed if r["exists"] in ("closed", "not_aba")]
    g_lo, g_hi = wilson(len(ghosts), len(claimed))
    print("\nGHOSTS: published clinics that are closed or are not ABA clinics.")
    print(
        f"  {len(ghosts)} of {len(claimed)} = {pct(len(ghosts) / len(claimed))}   "
        f"[{pct(g_lo)}, {pct(g_hi)}]"
    )
    reg = [r for r in claimed if r["sub_stratum"].endswith("/registry")]
    dirs = [r for r in claimed if r["sub_stratum"].endswith("/directory")]
    for label, bucket in (("registry-sourced", reg), ("directory-sourced", dirs)):
        if bucket:
            g = sum(1 for r in bucket if r["exists"] in ("closed", "not_aba"))
            print(f"    {label:<20} {g:>3} of {len(bucket):<4} {pct(g / len(bucket))}")
    print("  The registry never marks a closed clinic closed, so a nonzero registry")
    print("  ghost rate is expected. A nonzero DIRECTORY ghost rate is not: the owner")
    print("  is saying that center is open. Investigate any that turn up.")

    # --- recall: the only stratum that can find what we are missing --------------
    if unclaimed:
        missed = [r for r in unclaimed if r.get("missed") == "yes"]
        m_lo, m_hi = wilson(len(missed), len(unclaimed))
        print("\nRECALL: ABA clinics we attribute to nobody. Did we miss an owner?")
        print(
            f"  {len(missed)} of {len(unclaimed)} had a financial owner we failed to "
            f"catch = {pct(len(missed) / len(unclaimed))}   [{pct(m_lo)}, {pct(m_hi)}]"
        )
        if missed:
            print("\n  MISSED OWNERS (each one is a clinic the dataset is undercounting):")
            for r in missed:
                where = ", ".join(x for x in (r["city"], r["state"]) if x)
                print(f"    - {r['name']}  ({where})  NPI {r['npi']}")
                if r.get("notes"):
                    print(f"        {r['notes']}")
            print("\n  This is the most valuable output of the whole exercise. A")
            print("  precision-only sample could never have found these.")
        else:
            print("  None found. That is evidence of coverage, not proof of it: with")
            print(f"  n={len(unclaimed)} the upper bound is still {pct(m_hi)}.")

    print("\nWhat to publish: the ALL PUBLISHED rate with its interval, the ghost")
    print("rate, and the recall result including its upper bound. Publish the")
    print("interval, not just the point estimate.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
