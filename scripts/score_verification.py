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


def _check_collisions(moved: list[dict]) -> None:
    """Does a corrected address already exist in the dataset under another row?

    This is the question that decides how bad a wrong address is. If the true
    address is new to us, we have one misplaced clinic and the count is intact. If
    the true address is one we ALREADY hold for the same owner, then two rows are
    the same physical site, we have counted one centre twice, and the clinic count
    is overstated. That is a supersession, not a shrug, and it is exactly the class
    of bug `correct_cross_brand_sites.py` exists to fix.

    Fails soft: the score is still worth printing without a database.
    """
    with_addr = [r for r in moved if (r.get("correct_address") or "").strip()]
    if not with_addr:
        print("    (No corrected addresses were typed in, so no collision check.)")
        return
    try:
        from fundprint import db
        from fundprint.resolve.clinic_link import normalize
    except Exception as exc:  # pragma: no cover - environment, not logic
        print(f"    (Collision check skipped: {exc})")
        return

    try:
        conn = db.connect()
        rows = conn.execute(
            """
            SELECT oe.name, cl.address_line1, cl.zip
            FROM clinic cl
            JOIN owner_entity oe ON oe.id = cl.owner_entity_id
            WHERE cl.superseded_by IS NULL
            """
        ).fetchall()
        conn.close()
    except Exception as exc:  # pragma: no cover - environment, not logic
        print(f"    (Collision check skipped: {exc})")
        return

    # Do NOT parse the typed address by splitting on the first comma. Real
    # addresses put the suite after a comma ("15220 S 50th St. Building B, Suite
    # 103"), and the suite is PART OF THE SITE KEY, so that split silently drops
    # the very thing that distinguishes two clinics in one building. It made this
    # check miss a real double count in testing.
    #
    # Instead, normalize the whole typed line and ask whether a street we already
    # hold for this owner is a prefix of it. The reviewer types street, suite, city,
    # state, ZIP; a held street is the head of that. The prefix survives whatever
    # trailing city/state/ZIP they include, and it keeps the suite.
    by_owner: dict[str, list[str]] = {}
    for o, a, _z in rows:
        by_owner.setdefault(o, []).append(normalize(a or ""))

    hits = []
    for r in with_addr:
        typed = normalize(r["correct_address"] or "")
        for street in by_owner.get(r["claimed_owner"], []):
            # A short stem would prefix-match half a city, so require some length.
            if len(street) >= 10 and typed.startswith(street):
                hits.append((r, street))
                break

    if hits:
        print(f"\n    DOUBLE COUNT: {len(hits)} corrected address(es) are ALREADY in the")
        print("    dataset under another row for the same owner. Each is one real centre")
        print("    counted twice, so the clinic count is overstated. Supersede, do not delete:")
        for r, street in hits:
            print(f"      - {r['name']}")
            print(f"          we publish: {r['address']}")
            print(f"          truly at:   {r['correct_address']}")
            print(f"          which we already hold as: {street}")
    else:
        print(f"    Checked {len(with_addr)} corrected address(es): none collide with an")
        print("    existing row, so these are misplaced clinics, not double counts.")


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
    print("  CORRECT means all four: it is open, it is open AT THE ADDRESS WE GIVE,")
    print("  the brand is right, and the parent firm is right. 'Cannot tell' counts")
    print("  as not verified.\n")
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

    # --- wrong addresses, which are NOT ghosts and must not be counted as ghosts -
    # A ghost inflates the clinic count: we publish a centre that is not there. A
    # wrong address does not inflate the count at all, because the centre is real.
    # It corrupts the site key instead, which is a different injury: the map is
    # wrong, the state total may be wrong, and if the true address already sits in
    # the dataset under another row, then one real centre has been counted twice
    # and the count IS inflated after all. Blending the two would hide both.
    moved = [r for r in claimed if r["exists"] == "wrong_address"]
    if moved:
        m_lo, m_hi = wilson(len(moved), len(claimed))
        print("\nWRONG ADDRESS: the clinic is real, but not where we say it is.")
        print(
            f"  {len(moved)} of {len(claimed)} = {pct(len(moved) / len(claimed))}   "
            f"[{pct(m_lo)}, {pct(m_hi)}]"
        )
        sources = (("registry-sourced", "/registry"), ("directory-sourced", "/directory"))
        for label, suffix in sources:
            bucket = [r for r in claimed if r["sub_stratum"].endswith(suffix)]
            if bucket:
                k = sum(1 for r in bucket if r["exists"] == "wrong_address")
                print(f"    {label:<20} {k:>3} of {len(bucket):<4} {pct(k / len(bucket))}")
        _check_collisions(moved)
        print("  This does not inflate the clinic count on its own: the centre exists.")
        print("  It corrupts the site key, so the map and the state totals are wrong.")

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
