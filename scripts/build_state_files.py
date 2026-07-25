"""Build the per-state files: who owns the clinics, next to what the auditors found.

A national count is a statistic. A state count sitting beside a state dollar
figure is something a legislator or a local reporter can act on this week. This
joins the two, for the states where a government audit of Medicaid ABA spending
exists.

**The join is deliberately weak, and saying so is the whole point.** The audits
name no provider and no ownership type. Not one of them says private equity did
anything. Putting a $77.8 million finding next to a count of PE-owned clinics and
letting the reader draw a causal line would be the single fastest way to lose the
audience this project is built for, and it would be indefensible in front of
exactly the journalist, academic or Senate staffer the methodology names.

So the script enforces three things:

1. **`attributes_to_ownership` must be False everywhere.** If it is ever True the
   build fails, because the page's central caveat would then be a false statement.
2. **Maine is published even though it has zero PE clinics.** It has a $45.6
   million finding, larger than two of the other audited states, and no tracked
   private-equity presence at all, which is the cleanest available demonstration
   that these audits are not measuring ownership. A version of this page that
   quietly dropped Maine would be advocacy. The build fails if a published audit
   is missing from the output.
3. **A blocked audit ships no figures.** Massachusetts is real and well
   documented, and mass.gov returns 403 to everything including curl, so it cannot
   be content-hashed. It appears with its reason and without its numbers, exactly
   as the ABA Connect ownership claim does.

What the pairing does legitimately support is the argument PESP's own report makes:
this is a large, fast-growing Medicaid spend under weak oversight, and the people
auditing it cannot see who owns the providers. Wisconsin makes that concrete. The
federal registry sees 2 of the 48 private-equity-owned centres we track there, so
an auditor working from federal data is working blind. That claim needs no
causation and rests entirely on published facts.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fundprint import audits, db, fetch  # noqa: E402
from fundprint.acquire.base import _insert_source_record  # noqa: E402
from fundprint.storage import LocalFilesystemStore  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("state_files")

ROOT = Path(__file__).resolve().parents[1]
OUT_PATH = ROOT / "data" / "states" / "state_files.json"
MARKET_PATH = ROOT / "data" / "market" / "aba_market.json"
SOURCE_TYPE = "government_audit"
MODULE_VERSION = "0.1.0"


def _snapshot_audit(conn, audit: audits.Audit) -> dict:
    """Fetch and content-hash one audit, so its figures can be audited in turn."""
    content = fetch.get(audit.source_url)
    blob = content if isinstance(content, bytes) else content.content
    snapshot_id, content_hash = LocalFilesystemStore().put(blob, suffix=".html")

    existing = conn.execute(
        "SELECT id FROM source_record WHERE source_url = %s AND content_hash = %s",
        (audit.source_url, content_hash),
    ).fetchone()
    if existing:
        source_record_id = str(existing[0])
        logger.info("%s audit unchanged, reusing source_record %s", audit.state, source_record_id)
    else:
        source_record_id = _insert_source_record(
            conn,
            source_url=audit.source_url,
            snapshot_id=snapshot_id,
            source_type=SOURCE_TYPE,
            fetched_at=datetime.now(UTC),
            content_hash=content_hash,
            module_version=MODULE_VERSION,
        )
        logger.info("snapshotted %s audit as source_record %s", audit.state, source_record_id)

    return {"source_record_id": source_record_id, "content_hash": content_hash}


def _pe_owners_by_state(conn) -> dict[str, list[dict]]:
    """Per state, the private-equity owners operating there and their clinic counts.

    PE only. The audits speak about Medicaid ABA spending generally, but every
    comparison this project publishes against an outside PE figure has to be
    PE-only or it invents a disagreement out of our broader scope. The state map
    counts a pension fund, a family office and two search funds as well.
    """
    rows = conn.execute(
        """
        SELECT vc.state,
               COALESCE(oe.trade_name, oe.name) AS brand,
               f.name AS firm,
               COUNT(*) AS clinics
          FROM v_published_clinics vc
          JOIN owner_entity oe ON oe.id = vc.owner_entity_id
          JOIN parent_pe_firm f ON f.id = oe.parent_pe_firm_id
         WHERE f.firm_type = 'private_equity' AND vc.state IS NOT NULL
         GROUP BY vc.state, COALESCE(oe.trade_name, oe.name), f.name
         ORDER BY vc.state, COUNT(*) DESC
        """
    ).fetchall()
    out: dict[str, list[dict]] = {}
    for state, brand, firm, clinics in rows:
        out.setdefault(str(state), []).append(
            {"brand": str(brand), "firm": str(firm), "clinics": int(clinics)}
        )
    return out


def build(conn, market: dict) -> dict:
    by_state = _pe_owners_by_state(conn)
    market_states = {r["state"]: r for r in market["states"]}

    # Guardrail 1. The page says no audit attributes anything to ownership. If that
    # ever stops being true, the page is wrong and must be rewritten, not rebuilt.
    attributing = [a.state for a in audits.AUDITS if a.attributes_to_ownership]
    if attributing:
        raise SystemExit(
            f"audits {attributing} now attribute findings to ownership. The state "
            "file's central caveat is no longer true; rewrite the page before "
            "publishing it."
        )

    states = []
    for audit in audits.AUDITS:
        owners = by_state.get(audit.state, [])
        pe_clinics = sum(o["clinics"] for o in owners)
        mk = market_states.get(audit.state)
        blocked = audit.status == audits.BLOCKED

        if blocked and not audit.blocked_reason:
            raise SystemExit(f"{audit.state}: blocked audit carries no reason")

        row = {
            "state": audit.state,
            "state_name": audit.state_name,
            "status": audit.status,
            "audit": {
                "issuer": audit.issuer,
                "title": audit.title,
                "report_number": audit.report_number,
                "issued": audit.issued,
                "period": audit.period,
                "source_url": audit.source_url,
                "findings": audit.findings,
                "attributes_to_ownership": audit.attributes_to_ownership,
            },
            "note": audit.note,
            # The footprint is published for a blocked state too. Our own data is
            # not what is blocked; only the audit's figures are.
            "footprint": {
                "pe_clinics": pe_clinics,
                "owners": owners,
                "registry_visible_pe_sites": mk["private_equity_sites"] if mk else None,
                "aba_sites": mk["aba_sites"] if mk else None,
                "pe_share": mk["private_equity_share"] if mk else None,
            },
        }

        if blocked:
            row["blocked_reason"] = audit.blocked_reason
            # No figures. Not "null-but-present": absent, so no template can
            # accidentally render an unverifiable number as a verified one.
        else:
            row["audit"].update(
                {
                    "improper": audit.improper,
                    "potentially_improper": audit.potentially_improper,
                    "federal_refund": audit.federal_refund,
                    "spend_growth": (
                        [
                            {"year": y, "dollars": d}
                            for y, d in (audit.spend_growth or ())
                        ]
                        or None
                    ),
                }
            )
            row["audit"].update(_snapshot_audit(conn, audit))

        states.append(row)

    # Guardrail 2. Every published audit must reach the output, including the ones
    # whose footprint is zero. Maine with 0 PE clinics is the control case; losing
    # it to a filter would turn the page from evidence into argument.
    published_states = {a.state for a in audits.published()}
    emitted = {r["state"] for r in states if r["status"] == audits.PUBLISHED}
    if published_states != emitted:
        raise SystemExit(f"published audits missing from output: {published_states - emitted}")

    states.sort(key=lambda r: -(r["audit"].get("improper") or 0))

    tot = audits.totals()
    tot["pe_clinics_in_audited_states"] = sum(
        r["footprint"]["pe_clinics"] for r in states if r["status"] == audits.PUBLISHED
    )
    # The control case, surfaced as a number so the page cannot omit it by accident.
    tot["audited_states_with_no_pe"] = sorted(
        r["state"]
        for r in states
        if r["status"] == audits.PUBLISHED and r["footprint"]["pe_clinics"] == 0
    )
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "totals": tot,
        "states": states,
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

    t = doc["totals"]
    logger.info(
        "%d published audits across %s: $%.1fM improper, $%.1fM potentially, "
        "%d tracked PE clinics in those states",
        t["audits"],
        ",".join(t["states"]),
        t["improper"] / 1e6,
        t["potentially_improper"] / 1e6,
        t["pe_clinics_in_audited_states"],
    )
    if t["audited_states_with_no_pe"]:
        logger.info(
            "control case: %s audited with a finding and ZERO tracked PE clinics",
            ",".join(t["audited_states_with_no_pe"]),
        )
    logger.info("%d blocked (figures withheld)", t["blocked"])
    logger.info("wrote %s", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
