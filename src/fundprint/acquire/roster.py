"""Owner location rosters published as structured data (a third source shape).

``directory.py`` reads an owner's centers by fetching each center's page and
parsing its schema.org or Drupal address markup. Some owners do better than that:
they expose the whole roster as structured data in one place, because their site
is driven by it.

Three such rosters, all of chains the provider registry badly undercounts. The
undercount is not incidental: these chains bill under a handful of organization
NPIs, so the registry can only ever see a handful of clinics, however hard we
query it. The owner's own roster is the only public record of the rest.

* **LEARN Behavioral** (Gryphon Investors) runs a WP Store Locator. Its search
  endpoint returns all 151 centers in a single JSON response, each with a street
  address and, crucially, the LEARN sub-brand that operates it. LEARN keeps each
  acquired brand's own name (Autism Spectrum Therapies, Total Spectrum, Wisconsin
  Early Autism Project, ...), so the roster attributes each center to the right
  owner rather than to a holding company. The registry sees 9 of these centers.
* **Behavioral Innovations** (Tenex Capital Management) is the starkest case in
  the dataset. It operates roughly 150 centers and the federal registry yields
  **four**. Its sitemap lists every center page, and each carries a schema.org
  ``MedicalClinic`` block with the address.
* **Caravel Autism Health** (GTCR) exposes its centers as a WordPress custom post
  type, and each center page carries the same kind of schema.org block. The
  registry sees 2 of Caravel's 77 centers.

All three are read as *published structured data*, not scraped prose: a JSON
endpoint and machine-readable address blocks the sites publish for search engines.

## Why rows are staged under the OWNER's name

A registry row has to be attributed: we see "CARAVEL AUTISM HEALTH LLC" and infer
the owner from the name. A roster row does not need inferring. The owner is
telling us, on its own website, that this is one of its centers. So each row is
staged with ``raw_name`` set to the owner entity's name, and the deterministic
linker attaches it exactly. Nothing is guessed.

## The name-collision guard

Several LEARN brands ("Behavioral Concepts", "SPARKS ABA") have names generic
enough that unrelated organizations in the national registry begin the same way.
Those owners are marked ``directory_only``: safe to link from LEARN's own roster,
never used to match the registry. See the migration for the reasoning.

Usage:
    python scripts/run_acquire_roster.py --source learn --dry-run
    python scripts/run_acquire_roster.py
"""

from __future__ import annotations

import html as html_lib
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from fundprint import db, fetch
from fundprint.acquire.base import _find_existing_source_record, _insert_source_record
from fundprint.acquire.directory import (
    parse_address_with_known_locality,
    parse_us_address,
)
from fundprint.storage import LocalFilesystemStore, SnapshotStore

logger = logging.getLogger(__name__)

FUNDPRINT_UA = "FundprintBot/0.1 (+mailto:atharva.doke737@gmail.com)"
SOURCE_TYPE = "owner_location_directory"
MODULE_VERSION = "0.1.0"
REQUEST_DELAY_SEC = 0.15

_JSONLD_RE = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.S | re.I
)
_LOCATION_TYPES = {"MedicalBusiness", "MedicalClinic", "LocalBusiness", "Place"}


@dataclass
class RosterCenter:
    """One center from an owner's published roster."""

    owner_name: str
    address_line1: str
    city: str | None
    state: str | None
    zip: str | None
    detail_url: str | None = None


# ---------------------------------------------------------------------------
# LEARN Behavioral (Gryphon Investors)
# ---------------------------------------------------------------------------

LEARN_HOST = "learnbehavioral.com"
LEARN_ROSTER_URL = (
    "https://learnbehavioral.com/wp-admin/admin-ajax.php"
    "?action=store_search&lat=39.8&lng=-98.5&max_results=500&search_radius=5000&autoload=1"
)

# LEARN's roster labels each center with the sub-brand that runs it. Map those
# labels to the owner_entity each belongs to. A label not in this map is skipped
# rather than guessed at.
LEARN_BRAND_TO_OWNER: dict[str, str] = {
    "Autism Spectrum Therapies": "Autism Spectrum Therapies",
    "Total Spectrum": "Total Spectrum Autism Services",
    "Wisconsin Early Autism Project": "Wisconsin Early Autism Project",
    "Behavioral Concepts": "Behavioral Concepts",
    "Little Leaves Behavioral Services": "Little Leaves Behavioral Services",
    "BACA": "Behavior Analysis Center for Autism",
    "SPARKS ABA": "SPARKS ABA",
    "Tandem Therapy Services": "Tandem Therapy Services",
    "Trellis Services": "Trellis Services",
    "Priorities ABA": "Priorities ABA",
    "Trellis Autism Waiver Services": "Trellis Services",
    "The Trellis School": "Trellis Services",
    "Behavioral Development and Educational Services (BDES)": (
        "Behavioral Development and Educational Services"
    ),
    # "LEARN Behavioral HQ" is deliberately absent: a head office is not a clinic.
}


def parse_learn_roster(content: bytes) -> list[RosterCenter]:
    """Parse LEARN's store-locator JSON into centers. Pure; no I/O."""
    records = json.loads(content)
    centers: list[RosterCenter] = []
    for rec in records:
        brand = (rec.get("store") or "").strip()
        owner = LEARN_BRAND_TO_OWNER.get(brand)
        if not owner:
            # An unmapped label (e.g. the HQ) is skipped, never guessed at.
            logger.debug("skipping unmapped LEARN label %r", brand)
            continue
        street = (rec.get("address") or "").strip()
        if not street:
            continue
        centers.append(
            RosterCenter(
                owner_name=owner,
                address_line1=street,
                city=(rec.get("city") or "").strip() or None,
                state=((rec.get("state") or "").strip()[:2] or None),
                zip=(rec.get("zip") or "").strip() or None,
                detail_url=(rec.get("url") or "").strip() or None,
            )
        )
    return centers


def fetch_learn(client: httpx.Client) -> tuple[bytes, str]:
    resp = client.get(LEARN_ROSTER_URL, headers={"User-Agent": FUNDPRINT_UA})
    resp.raise_for_status()
    return resp.content, LEARN_ROSTER_URL


# ---------------------------------------------------------------------------
# Caravel Autism Health (GTCR)
# ---------------------------------------------------------------------------

CARAVEL_OWNER = "Caravel Autism Health"
CARAVEL_INDEX = "https://www.caravelautism.com/wp-json/wp/v2/autism-center?per_page=100"


def _jsonld_address(html: str) -> dict[str, Any] | None:
    """Return the first schema.org PostalAddress on a page, or None."""
    for block in _JSONLD_RE.findall(html):
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        nodes = data.get("@graph", [data]) if isinstance(data, dict) else data
        for node in nodes if isinstance(nodes, list) else [nodes]:
            if not isinstance(node, dict):
                continue
            types = node.get("@type")
            types = types if isinstance(types, list) else [types]
            if not any(t in _LOCATION_TYPES for t in types if t):
                continue
            addr = node.get("address")
            if isinstance(addr, dict) and addr.get("streetAddress"):
                return addr
    return None


def parse_caravel_page(html: str) -> RosterCenter | None:
    """Parse one Caravel center page's schema.org block. Pure; no I/O."""
    addr = _jsonld_address(html)
    if not addr:
        return None
    street = (addr.get("streetAddress") or "").strip()
    if not street:
        return None
    return RosterCenter(
        owner_name=CARAVEL_OWNER,
        address_line1=street,
        city=(addr.get("addressLocality") or "").strip() or None,
        state=((addr.get("addressRegion") or "").strip()[:2] or None),
        zip=(addr.get("postalCode") or "").strip() or None,
    )


def fetch_caravel(client: httpx.Client) -> tuple[bytes, str]:
    """Enumerate Caravel's centers, then read each center page's address block."""
    headers = {"User-Agent": FUNDPRINT_UA}
    links: list[str] = []
    page = 1
    while True:
        resp = client.get(f"{CARAVEL_INDEX}&page={page}", headers=headers)
        if resp.status_code == 400:
            break  # past the last page
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        links.extend(c["link"] for c in batch if c.get("link"))
        if len(batch) < 100:
            break
        page += 1
        time.sleep(REQUEST_DELAY_SEC)

    # Caravel's center pages sit behind a WAF that fingerprints the TLS handshake
    # and serves httpx a 403 while serving curl a 200, for the identical
    # User-Agent. fundprint.fetch retries such a block through curl, keeping our
    # user-agent and contact address unchanged. See that module for the reasoning.
    centers: list[dict] = []
    for link in links:
        try:
            html = fetch.get(link, client=client).decode("utf-8", errors="replace")
        except (httpx.HTTPError, fetch.FetchError) as exc:
            logger.warning("caravel page failed %s: %s", link, exc)
            continue
        center = parse_caravel_page(html)
        if center:
            center.detail_url = link
            centers.append(vars(center))
        time.sleep(REQUEST_DELAY_SEC)

    doc = json.dumps({"source": CARAVEL_INDEX, "centers": centers}).encode()
    return doc, CARAVEL_INDEX


def parse_caravel_roster(content: bytes) -> list[RosterCenter]:
    """Parse the merged Caravel document produced by fetch_caravel."""
    data = json.loads(content)
    return [RosterCenter(**c) for c in data.get("centers", [])]


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Behavioral Innovations (Tenex Capital Management)
# ---------------------------------------------------------------------------
#
# The starkest registry undercount in the dataset. Behavioral Innovations
# operates roughly 150 centers and registers almost none of them as organization
# NPIs, so the federal registry yields four. Its own sitemap lists every center,
# and each center page carries a schema.org MedicalClinic block with the address.

BI_OWNER = "Behavioral Innovations"
BI_SITEMAP = "https://behavioral-innovations.com/sitemap-0.xml"
_BI_LOC_RE = re.compile(r"<loc>([^<]+)</loc>", re.I)


def _bi_location_urls(sitemap_xml: str) -> list[str]:
    """English center pages from the sitemap. Pure, so it is testable."""
    urls = _BI_LOC_RE.findall(sitemap_xml)
    return [
        u
        for u in urls
        # /es/ is the Spanish mirror of the same centers: same addresses, counted
        # twice if included. The bare /location/ index is not a center.
        if "/location/" in u and "/es/" not in u and not u.rstrip("/").endswith("/location")
    ]


def parse_bi_page(html: str) -> RosterCenter | None:
    """Parse one Behavioral Innovations center page. Pure; no I/O."""
    addr = _jsonld_address(html)
    if not addr or not (addr.get("streetAddress") or "").strip():
        return None
    return RosterCenter(
        owner_name=BI_OWNER,
        address_line1=addr["streetAddress"].strip(),
        city=(addr.get("addressLocality") or "").strip() or None,
        state=((addr.get("addressRegion") or "").strip()[:2] or None),
        zip=(addr.get("postalCode") or "").strip() or None,
    )


def fetch_bi(client: httpx.Client) -> tuple[bytes, str]:
    """Enumerate BI's centers from its sitemap, then read each page's address."""
    sitemap = fetch.get(BI_SITEMAP, client=client).decode("utf-8", errors="replace")
    links = _bi_location_urls(sitemap)
    logger.info("behavioral innovations: %d center page(s) in sitemap", len(links))

    centers: list[dict] = []
    for link in links:
        try:
            html = fetch.get(link, client=client).decode("utf-8", errors="replace")
        except (httpx.HTTPError, fetch.FetchError) as exc:
            logger.warning("bi page failed %s: %s", link, exc)
            continue
        center = parse_bi_page(html)
        if center:
            center.detail_url = link
            centers.append(vars(center))
        time.sleep(REQUEST_DELAY_SEC)

    doc = json.dumps({"source": BI_SITEMAP, "centers": centers}).encode()
    return doc, BI_SITEMAP


def parse_bi_roster(content: bytes) -> list[RosterCenter]:
    """Parse the merged Behavioral Innovations document produced by fetch_bi."""
    data = json.loads(content)
    return [RosterCenter(**c) for c in data.get("centers", [])]


# ---------------------------------------------------------------------------
# Hopebridge (Arsenal Capital Partners)
# ---------------------------------------------------------------------------

HOPEBRIDGE_OWNER = "Hopebridge"
HOPEBRIDGE_URL = "https://www.hopebridge.com/centers/"

# Every centre is a card on the single /centers/ index, so the whole roster is one
# request. Each card pairs a heading with an `address` list item.
_HB_CARD_RE = re.compile(
    r"<h3[^>]*>(?P<name>.*?)</h3>.*?<li class=\"address[^\"]*\">(?P<addr>[^<]+)</li>",
    re.S | re.I,
)
_HB_TAG_RE = re.compile(r"<[^>]+>")
# "Fort Wayne Autism Therapy Center" -> "Fort Wayne". The suffix is boilerplate on
# every card; what is left is the city, which the address does not always delimit.
_HB_NAME_SUFFIX_RE = re.compile(
    r"\s*(?:-\s*\w+\s*)?(?:In-Home\s+)?Autism\s+Therapy(?:\s+Center)?\s*$", re.I
)


def parse_hopebridge_roster(content: bytes) -> list[RosterCenter]:
    """Parse Hopebridge's /centers/ index. Pure; no I/O.

    Two shapes have to survive here. Georgia writes the state as "Atlanta, Ga.,
    30329"; Indiana drops the comma before the city ("4422 E State Blvd. Fort Wayne,
    IN 46815"), where no rule can find the street's end unaided. The card's heading
    names the city, so the city is subtracted rather than guessed, exactly as for
    Acorn. See ``directory.parse_address_with_known_locality``.

    In-home entries carry a "Serving areas: ..." list where the address goes and no
    ZIP at all. They fall out here and they should: an in-home service area is not a
    centre, which is the same trap Key Autism sets.
    """
    text = content.decode("utf-8", errors="replace")
    centers: list[RosterCenter] = []
    for m in _HB_CARD_RE.finditer(text):
        raw_addr = html_lib.unescape(m.group("addr")).strip()
        name = html_lib.unescape(_HB_TAG_RE.sub("", m.group("name"))).strip()
        city = _HB_NAME_SUFFIX_RE.sub("", name).strip()
        state_m = re.search(r"[,\s]([A-Za-z]{2})\.?[,\s]+\d{5}\b", raw_addr)
        parsed = None
        if city and state_m:
            parsed = parse_address_with_known_locality(
                raw_addr, f"{city} {state_m.group(1).upper()}"
            )
        parsed = parsed or parse_us_address(raw_addr)
        if parsed is None:
            logger.debug("hopebridge: no street address for %r (%r)", name, raw_addr)
            continue
        street, parsed_city, state, zip_code = parsed
        centers.append(
            RosterCenter(
                owner_name=HOPEBRIDGE_OWNER,
                address_line1=street,
                city=parsed_city,
                state=state,
                zip=zip_code,
            )
        )
    return centers


def fetch_hopebridge(client: httpx.Client) -> tuple[bytes, str]:
    """Fetch Hopebridge's centre index. One page, every centre."""
    return fetch.get(HOPEBRIDGE_URL, client=client), HOPEBRIDGE_URL


SOURCES = {
    "learn": (fetch_learn, parse_learn_roster),
    "caravel": (fetch_caravel, parse_caravel_roster),
    "behavioral-innovations": (fetch_bi, parse_bi_roster),
    "hopebridge": (fetch_hopebridge, parse_hopebridge_roster),
}


def run(
    source: str,
    *,
    store: SnapshotStore | None = None,
    dry_run: bool = False,
) -> list[RosterCenter]:
    """Fetch, snapshot and stage one owner roster. Idempotent by content hash."""
    if source not in SOURCES:
        raise KeyError(f"unknown roster {source!r}; known: {', '.join(SOURCES)}")
    fetch, parse = SOURCES[source]
    store = store or LocalFilesystemStore()

    with httpx.Client(timeout=60.0, follow_redirects=True) as client:
        content, source_url = fetch(client)

    centers = parse(content)
    logger.info("%s roster: %d center(s)", source, len(centers))
    if dry_run:
        by_owner: dict[str, int] = {}
        for c in centers:
            by_owner[c.owner_name] = by_owner.get(c.owner_name, 0) + 1
        for owner, n in sorted(by_owner.items(), key=lambda kv: -kv[1]):
            logger.info("   %-42s %3d", owner, n)
        logger.info("dry run; nothing written")
        return centers

    snapshot_id, content_hash = store.put(content, suffix=".json")
    with db.transaction() as conn:
        if _find_existing_source_record(conn, source_url, content_hash) is not None:
            logger.info("identical roster already staged for %s; skipping", source_url)
            return centers
        source_record_id = _insert_source_record(
            conn,
            source_url=source_url,
            snapshot_id=snapshot_id,
            source_type=SOURCE_TYPE,
            fetched_at=datetime.now(UTC),
            content_hash=content_hash,
            module_version=MODULE_VERSION,
        )
        for c in centers:
            # raw_name is the OWNER's name: the owner is telling us this is its
            # center, so there is nothing to infer and the linker attaches it
            # exactly. No NPI: a roster is not a registry.
            conn.execute(
                """
                INSERT INTO staging_bacb_provider
                    (source_record_id, raw_name, address_line1, city, state, zip, npi)
                VALUES (%s, %s, %s, %s, %s, %s, NULL)
                """,
                (source_record_id, c.owner_name, c.address_line1, c.city, c.state, c.zip),
            )
    logger.info("staged %d center(s) from the %s roster", len(centers), source)
    return centers
