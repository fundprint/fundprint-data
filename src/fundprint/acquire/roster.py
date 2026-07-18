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
    street = addr["streetAddress"].strip()
    city = (addr.get("addressLocality") or "").strip() or None
    state = (addr.get("addressRegion") or "").strip()[:2] or None
    zip_code = (addr.get("postalCode") or "").strip() or None

    # A third of BI's pages write the address as schema.org Text, one line, with no
    # separate locality fields: "1450 League Line Road, Suite 100, Conroe, Texas
    # 77304". Taking that whole string as the street put the city and state INSIDE
    # address_line1 and left the columns null, which is worse than it looks: the site
    # key is built from the street, so the centre could never match the same building
    # arriving from the registry, and a null state dropped it from the state map and
    # the per-state shares entirely. Split it, and refuse the row if it will not split
    # rather than stage a mangled street.
    if not city or not state:
        parsed = parse_us_address(street)
        if parsed is None:
            return None
        street, city, state, zip_code = parsed

    return RosterCenter(
        owner_name=BI_OWNER,
        address_line1=street,
        city=city,
        state=state,
        zip=zip_code,
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


# ---------------------------------------------------------------------------
# Helping Hands Family (Zenyth Partners)
# ---------------------------------------------------------------------------

# HHF runs the same WP Store Locator plugin as LEARN, so one autoload call returns
# every centre with a clean address/city/state/zip. The registry was giving us this
# owner wrong in both directions: it had missed real HHF centres AND captured a pile
# of name collisions ("HELPING HANDS FAMILY CHIROPRACTIC", "... HOMECARE LLC",
# "... CARE HOMES") that share the brand prefix, exactly the Hope Bridge / Acorn
# problem. HHF's own list is the fix; it operates only in PA, NJ, NY, MD, CT and VA.
HHF_OWNER = "Helping Hands Family"
HHF_HOST = "hhfamily.com"
HHF_ROSTER_URL = (
    "https://hhfamily.com/wp-admin/admin-ajax.php"
    "?action=store_search&lat=39.8&lng=-98.5&max_results=500&search_radius=5000&autoload=1"
)

# The plugin returns the state as either the two-letter code or the full name. A
# site key needs the code, so fold the names we actually see. Anything else is left
# as-is and truncated, which surfaces as a bad key rather than a silent wrong state.
_STATE_NAME_TO_CODE = {
    "pennsylvania": "PA",
    "new jersey": "NJ",
    "new york": "NY",
    "maryland": "MD",
    "connecticut": "CT",
    "virginia": "VA",
    "delaware": "DE",
}


def _state_code(value: str | None) -> str | None:
    v = (value or "").strip()
    if not v:
        return None
    return _STATE_NAME_TO_CODE.get(v.lower(), v[:2].upper())


def parse_hhf_roster(content: bytes) -> list[RosterCenter]:
    """Parse Helping Hands Family's store-locator JSON into centers. Pure; no I/O."""
    records = json.loads(content)
    centers: list[RosterCenter] = []
    for rec in records:
        street = (rec.get("address") or "").strip()
        if not street:
            continue
        city = (rec.get("city") or "").strip() or None
        state = _state_code(rec.get("state"))
        zip_code = (rec.get("zip") or "").strip() or None
        # A few store rows carry the whole address in the street field ("275 Curry
        # Hollow Rd Suite G100, Pittsburgh, PA 15236"). Left as-is it drags the city
        # and ZIP into the site key and stops the centre matching the same building
        # from the registry. Split it back out when it carries its own ST ZIP tail.
        if re.search(r",\s*[A-Za-z]{2}\.?\s+\d{5}", street):
            parsed = parse_us_address(street)
            if parsed is not None:
                street, city, state, zip_code = parsed
        centers.append(
            RosterCenter(
                owner_name=HHF_OWNER,
                address_line1=street,
                city=city,
                state=state,
                zip=zip_code,
                detail_url=(rec.get("url") or "").strip() or None,
            )
        )
    return centers


def fetch_hhf(client: httpx.Client) -> tuple[bytes, str]:
    """Fetch HHF's full roster in one store-locator autoload call."""
    return fetch.get(HHF_ROSTER_URL, client=client), HHF_ROSTER_URL


# ---------------------------------------------------------------------------
# Centria (Thomas H. Lee Partners)
# ---------------------------------------------------------------------------
#
# Centria's registry footprint was in-home noise. The NPPES name-prefix match on
# "CENTRIA" captured apartments and bare residential streets in Michigan (where an
# in-home provider's therapy is delivered, not a centre) plus the corporate HQ, and
# missed almost every real centre. Centria publishes its full centre list as a
# WordPress custom post type at /wp-json/wp/v2/location; each centre's detail page
# carries a clean "street <br> City, ST ZIP" block in a `span.info`. This is the
# same directory-beats-registry lever used for ABC and Hopebridge.
#
# Centria runs two consumer brands (Life Skills Autism Academy in the South and
# Southwest, Centria Autism in the Midwest and Northwest). They are one operator
# sharing suites: two of the listed addresses appear under both brands. So every
# centre stages under the single owner "Centria" and the site key collapses the
# shared addresses on its own, exactly as one owner should. A per-brand split would
# assert two operators at one suite, the claim the shared address contradicts.
CENTRIA_OWNER = "Centria"
CENTRIA_INDEX = "https://centriahealthcare.com/wp-json/wp/v2/location?per_page=100"
_CENTRIA_INFO_RE = re.compile(r'<span class="info">(.*?)</span>', re.S)
_TAG_RE = re.compile(r"<[^>]+>")


def parse_centria_page(html: str) -> RosterCenter | None:
    """Parse one Centria centre page's address block. Pure; no I/O.

    Each detail page renders its own centre's address as
    ``<span class="info">5275 N. 59th Ave <br /> Glendale, AZ, 85301 ...</span>``.
    The first such block carrying a ZIP is the page's own centre; the others on the
    page are phone, ages served and hours, which have no "City, ST ZIP" tail.
    """
    for block in _CENTRIA_INFO_RE.findall(html):
        parts = re.split(r"<br\s*/?>", block, maxsplit=1)
        if len(parts) != 2:
            continue
        street = html_lib.unescape(_TAG_RE.sub(" ", parts[0]))
        street = re.sub(r"\s+", " ", street).strip().rstrip(",")
        tail = html_lib.unescape(_TAG_RE.sub(" ", parts[1]))
        m = re.search(r"([A-Za-z .'-]+),\s*([A-Za-z]{2}),?\s*(\d{5})", tail)
        if not street or not m:
            continue
        return RosterCenter(
            owner_name=CENTRIA_OWNER,
            address_line1=street,
            city=m.group(1).strip(),
            state=m.group(2).upper(),
            zip=m.group(3),
        )
    return None


def fetch_centria(client: httpx.Client) -> tuple[bytes, str]:
    """Enumerate Centria's centres from its location post type, then read each
    centre page's address block."""
    links: list[str] = []
    page = 1
    while True:
        try:
            body = fetch.get(f"{CENTRIA_INDEX}&page={page}", client=client)
        except httpx.HTTPStatusError:
            break  # WordPress returns 400 past the last page
        batch = json.loads(body)
        if not batch:
            break
        links.extend(c["link"] for c in batch if c.get("link"))
        if len(batch) < 100:
            break
        page += 1
        time.sleep(REQUEST_DELAY_SEC)

    centers: list[dict] = []
    for link in links:
        try:
            html = fetch.get(link, client=client).decode("utf-8", errors="replace")
        except (httpx.HTTPError, fetch.FetchError) as exc:
            logger.warning("centria page failed %s: %s", link, exc)
            continue
        center = parse_centria_page(html)
        if center:
            center.detail_url = link
            centers.append(vars(center))
        time.sleep(REQUEST_DELAY_SEC)

    doc = json.dumps({"source": CENTRIA_INDEX, "centers": centers}).encode()
    return doc, CENTRIA_INDEX


def parse_centria_roster(content: bytes) -> list[RosterCenter]:
    """Parse the merged Centria document produced by fetch_centria."""
    data = json.loads(content)
    return [RosterCenter(**c) for c in data.get("centers", [])]


# ---------------------------------------------------------------------------
# A shared address-block splitter
# ---------------------------------------------------------------------------
#
# Three owners below publish the same shape: the street on one line, the locality
# on the next, separated by a <br>. That is the friendliest case in this file,
# because the <br> removes the guesswork of where the street ends. It is the same
# split Centria uses; factored out so InBloom, ABS Kids and Catalyst share it.
_CITY_ST_ZIP_RE = re.compile(r"([A-Za-z .'\-]+),\s*([A-Za-z]{2}),?\s*(\d{5})(?:-\d{4})?")


def _center_from_br_block(owner: str, block: str) -> RosterCenter | None:
    """Parse a ``STREET <br> City, ST ZIP`` block into a RosterCenter. Pure."""
    parts = re.split(r"<br\s*/?>", block, maxsplit=1)
    if len(parts) != 2:
        return None
    street = re.sub(r"\s+", " ", html_lib.unescape(_TAG_RE.sub(" ", parts[0])))
    street = street.strip().rstrip(",").strip()
    tail = html_lib.unescape(_TAG_RE.sub(" ", parts[1]))
    m = _CITY_ST_ZIP_RE.search(tail)
    # A street must carry a house number: a card with no digits is a heading or a
    # "coming soon" placeholder, not an address, and a wrong street is worse than none.
    if not street or not m or not any(ch.isdigit() for ch in street):
        return None
    return RosterCenter(
        owner_name=owner,
        address_line1=street,
        city=m.group(1).strip(),
        state=m.group(2).upper(),
        zip=m.group(3),
    )


# ---------------------------------------------------------------------------
# InBloom Autism Services (Elysium Management)
# ---------------------------------------------------------------------------
#
# The registry name is the search vehicle "Vocational Development Group"; the ABA
# brand families know is InBloom. Its /wp-json/ is robots-disallowed, but the one
# public /aba-therapy-learning-centers/ page carries every Learning Center as a
# card: an <h6> title, then the address as either "STREET <br> City, ST ZIP" or,
# on a few cards, the street and locality in two separate <p> tags. So rather than
# split on <br>, take the text between each <h6> and the next heading or button and
# peel the "City, ST ZIP" off the end; the remainder is the street. Stages under the
# legal name so the linker attaches it to the existing owner_entity.
INBLOOM_OWNER = "Vocational Development Group"
INBLOOM_URL = "https://inbloomautism.com/aba-therapy-learning-centers/"
_INBLOOM_CARD_RE = re.compile(
    r"<h6[^>]*>([^<]+)</h6>(.*?)"
    r"(?=<h6|<a class=\"fusion-button|<div class=centerBtn|More About This Learning Center)",
    re.S,
)
_INBLOOM_TAIL_RE = re.compile(
    r"^(.*?)[,\s]+([A-Za-z .'\-]+),\s*([A-Z]{2})\s+(\d{5})(?:-\d{4})?$"
)


def parse_inbloom_roster(content: bytes) -> list[RosterCenter]:
    """Parse the InBloom locations page. Pure; no I/O."""
    html = content.decode("utf-8", "replace")
    out: list[RosterCenter] = []
    for _name, block in _INBLOOM_CARD_RE.findall(html):
        text = re.sub(r"\s+", " ", html_lib.unescape(_TAG_RE.sub(" ", block)))
        text = text.strip().rstrip(",").strip()
        m = _INBLOOM_TAIL_RE.match(text)
        if not m:
            continue
        street = m.group(1).strip().rstrip(",").strip()
        if not any(ch.isdigit() for ch in street):
            continue
        out.append(
            RosterCenter(
                owner_name=INBLOOM_OWNER,
                address_line1=street,
                city=m.group(2).strip(),
                state=m.group(3),
                zip=m.group(4),
            )
        )
    return out


def fetch_inbloom(client: httpx.Client) -> tuple[bytes, str]:
    """Snapshot InBloom's single locations page (the fetchable source)."""
    return fetch.get(INBLOOM_URL, client=client), INBLOOM_URL


# ---------------------------------------------------------------------------
# ABS Kids / Alternative Behavior Strategies (Petra Capital Partners)
# ---------------------------------------------------------------------------
#
# The registry sees a handful of ABS Kids NPIs; the owner lists ~75 therapy
# centres. Its /wp-json/wp/v2/locations post type enumerates the leaf pages but
# carries no address (acf and content are prose), so the address is read from each
# leaf page, where every centre is a <h3 class="loc-meta-address-title"> title
# followed by an <address>STREET <br> City, ST ZIP</address>. One page can hold
# many centres (Charlotte has thirteen), so all cards on a page are taken.
#
# A stand-alone "Autism Diagnosis Clinic" is an evaluation site, not an ABA therapy
# centre, and is out of scope; a combined "ABA Therapy Center & Autism Diagnosis
# Clinic" is a therapy centre and stays. State-index hub pages carry no <address>
# tags, so they contribute nothing without special-casing.
ABS_OWNER = "Alternative Behavior Strategies"
ABS_INDEX = "https://www.abskids.com/wp-json/wp/v2/locations?per_page=100"
_ABS_CARD_RE = re.compile(
    r'<h3 class="loc-meta-address-title">(.*?)</h3>.*?<address>(.*?)</address>', re.S
)


def _abs_is_therapy(title: str) -> bool:
    t = title.lower()
    return not ("diagnosis" in t and "therapy" not in t and "aba" not in t)


def fetch_abs(client: httpx.Client) -> tuple[bytes, str]:
    """Enumerate ABS Kids leaf pages, then read each page's <address> cards."""
    index = json.loads(fetch.get(ABS_INDEX, client=client))
    centers: list[dict] = []
    seen: set[str] = set()
    for item in index:
        link = item.get("link")
        if not link or link in seen:
            continue
        seen.add(link)
        try:
            html = fetch.get(link, client=client).decode("utf-8", "replace")
        except (httpx.HTTPError, fetch.FetchError) as exc:
            logger.warning("abs page failed %s: %s", link, exc)
            continue
        for title, block in _ABS_CARD_RE.findall(html):
            if not _abs_is_therapy(html_lib.unescape(_TAG_RE.sub(" ", title))):
                continue
            center = _center_from_br_block(ABS_OWNER, block)
            if center:
                center.detail_url = link
                centers.append(vars(center))
        time.sleep(REQUEST_DELAY_SEC)
    return json.dumps({"source": ABS_INDEX, "centers": centers}).encode(), ABS_INDEX


def parse_abs_roster(content: bytes) -> list[RosterCenter]:
    return [RosterCenter(**c) for c in json.loads(content).get("centers", [])]


# ---------------------------------------------------------------------------
# Behavior Frontiers (NexPhase Capital)
# ---------------------------------------------------------------------------
#
# A Squarespace site with no structured endpoint. The /bf-locations index links to
# per-region pages; roughly half are pure in-home service areas with no street and
# are skipped by construction. On the rest, each centre is a block headed
# "City, ST (Center)[- STATUS]", then "Behavior Frontiers Autism Center", then the
# address up to "Phone:". The heading names the city, so the street is a
# subtraction, not a guess. "COMING SOON" sites are leased-but-not-open and are
# excluded: a clinic must be operating. "NOW ENROLLING" is open and kept.
BF_OWNER = "Behavior Frontiers"
BF_INDEX = "https://www.behaviorfrontiers.com/bf-locations"
# The heading is "City, ST[, sub-label] (Center)[- STATUS]", and the address that
# follows runs to the first of Phone:/Hours:/Fax: (pages vary in which comes first;
# stopping only at Phone: swallowed the hours text into the street and lost every
# page that lists Hours: first, e.g. all of Minnesota).
_BF_BLOCK_RE = re.compile(
    r"([A-Za-z .'\-]+,\s*[A-Za-z]{2})(?:,\s*[A-Za-z ]+?)?\s*\(Center\)\s*(-\s*[A-Za-z !]+)?\s*"
    r"Behavior Frontiers Autism Center\s+(.*?)\s+(?:Phone|Hours|Fax):",
    re.S,
)


def _bf_region_links(index_html: str) -> list[str]:
    slugs = sorted(set(re.findall(r'href="(/[a-z0-9][a-z0-9-]+-[a-z]{2})"', index_html)))
    return [f"https://www.behaviorfrontiers.com{s}" for s in slugs]


def parse_bf_page(html: str) -> list[RosterCenter]:
    """Parse one Behavior Frontiers region page. Pure; no I/O."""
    text = re.sub(r"\s+", " ", html_lib.unescape(_TAG_RE.sub(" ", html)))
    out: list[RosterCenter] = []
    for locality, status, addr in _BF_BLOCK_RE.findall(text):
        if status and "COMING SOON" in status.upper():
            continue
        parsed = parse_address_with_known_locality(addr.strip(), locality.strip())
        if parsed is None:  # no ZIP, or address does not end with the heading's city
            continue
        street, city, state, zipc = parsed
        out.append(
            RosterCenter(
                owner_name=BF_OWNER,
                address_line1=street,
                city=city,
                state=state,
                zip=zipc,
            )
        )
    return out


def fetch_bf(client: httpx.Client) -> tuple[bytes, str]:
    """Read the /bf-locations index, then each region page's centre blocks."""
    index_html = fetch.get(BF_INDEX, client=client).decode("utf-8", "replace")
    centers: list[dict] = []
    seen_keys: set[tuple] = set()
    for link in _bf_region_links(index_html):
        try:
            html = fetch.get(link, client=client).decode("utf-8", "replace")
        except (httpx.HTTPError, fetch.FetchError) as exc:
            logger.warning("bf page failed %s: %s", link, exc)
            continue
        for center in parse_bf_page(html):
            # One centre can be reached from two region slugs (Boston lists the
            # Chelmsford centre too); dedupe on the address before staging.
            key = (center.address_line1, center.zip)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            center.detail_url = link
            centers.append(vars(center))
        time.sleep(REQUEST_DELAY_SEC)
    return json.dumps({"source": BF_INDEX, "centers": centers}).encode(), BF_INDEX


def parse_bf_roster(content: bytes) -> list[RosterCenter]:
    return [RosterCenter(**c) for c in json.loads(content).get("centers", [])]


# ---------------------------------------------------------------------------
# Kind Behavioral Health / Carolina Center for Autism Services (WSC & Company)
# ---------------------------------------------------------------------------
#
# The registry legal name is Carolina Center for Autism Services; the brand is Kind
# Behavioral Health, which absorbed it (carolinacenterforaba.com now redirects to
# kindbh.com). location-sitemap.xml lists the leaf pages; every real centre is a
# "<slug>-clinic/" page (the region hubs have no "-clinic" suffix). The address is
# in a schema.org PostalAddress on most pages and a plain "Address: ..." text line
# on the rest, so both paths are tried.
KBH_OWNER = "Carolina Center for Autism Services"
KBH_SITEMAP = "https://kindbh.com/location-sitemap.xml"
_KBH_LEAF_RE = re.compile(r"<loc>(https://kindbh\.com/our-locations/[^<]+-clinic/)</loc>")
_KBH_TEXT_RE = re.compile(
    r"Address:\s*([0-9][^<]{6,90}?,\s*[A-Za-z .'\-]+,\s*[A-Z]{2}\s+\d{5})"
)


def _find_postal(node: Any) -> dict | None:
    """Depth-first search for a schema.org PostalAddress with a street."""
    if isinstance(node, dict):
        if node.get("streetAddress") and node.get("postalCode"):
            return node
        for v in node.values():
            found = _find_postal(v)
            if found:
                return found
    elif isinstance(node, list):
        for v in node:
            found = _find_postal(v)
            if found:
                return found
    return None


def parse_kbh_page(html: str) -> RosterCenter | None:
    """Parse one kindbh clinic page via JSON-LD then a text-label fallback. Pure."""
    for ld in _JSONLD_RE.findall(html):
        try:
            pa = _find_postal(json.loads(ld))
        except (json.JSONDecodeError, TypeError):
            continue
        if pa:
            region = str(pa.get("addressRegion", "")).strip()
            # Accept a two-letter region as-is or map a spelled-out state; anything
            # else falls through to the text-label path rather than being guessed
            # (truncating "North Carolina" to "NO" is the wrong-street failure).
            state = region.upper() if len(region) == 2 else _STATE_NAME_TO_CODE.get(region.lower())
            zipc = re.match(r"(\d{5})", str(pa.get("postalCode", "")).strip())
            if state and zipc:
                return RosterCenter(
                    owner_name=KBH_OWNER,
                    address_line1=str(pa["streetAddress"]).strip(),
                    city=str(pa.get("addressLocality", "")).strip() or None,
                    state=state,
                    zip=zipc.group(1),
                )
    m = _KBH_TEXT_RE.search(html)
    if m:
        parsed = parse_us_address(m.group(1))
        if parsed and parsed[3]:
            return RosterCenter(
                owner_name=KBH_OWNER,
                address_line1=parsed[0],
                city=parsed[1],
                state=parsed[2],
                zip=parsed[3],
            )
    return None


def fetch_kbh(client: httpx.Client) -> tuple[bytes, str]:
    """Read kindbh's location sitemap, then each -clinic/ leaf page. Honours the
    site's Crawl-delay: 1 (its robots.txt disallows named AI-training crawlers, not
    FundprintBot, and allows the clinic pages)."""
    sitemap = fetch.get(KBH_SITEMAP, client=client).decode("utf-8", "replace")
    centers: list[dict] = []
    for link in _KBH_LEAF_RE.findall(sitemap):
        try:
            html = fetch.get(link, client=client).decode("utf-8", "replace")
        except (httpx.HTTPError, fetch.FetchError) as exc:
            logger.warning("kbh page failed %s: %s", link, exc)
            continue
        center = parse_kbh_page(html)
        if center:
            center.detail_url = link
            centers.append(vars(center))
        time.sleep(1.0)
    return json.dumps({"source": KBH_SITEMAP, "centers": centers}).encode(), KBH_SITEMAP


def parse_kbh_roster(content: bytes) -> list[RosterCenter]:
    return [RosterCenter(**c) for c in json.loads(content).get("centers", [])]


# ---------------------------------------------------------------------------
# Behavior Care Specialists / Catalyst Behavior Solutions (Pharos Capital Group)
# ---------------------------------------------------------------------------
#
# Behavior Care Specialists rebranded to Catalyst Behavior Solutions
# (behaviorcarespecialists.com redirects to catalystbehavior.com). Its
# locations_place post type lists the current centres; each leaf page carries a
# `markers` JS object whose content block is a "STREET <br> City, ST ZIP". Stages
# under the registry legal name "Behavior Care Specialists". NOTE: the current list
# is 7 centres where the registry carried ~20; several old South Dakota pages
# (Aberdeen, Rapid City, Brookings, Sisseton) now redirect away, so those look
# closed. The extra registry rows are left published at their honest, lower
# confidence rather than quarantined on a rebrand, until the closures are confirmed.
CATALYST_OWNER = "Behavior Care Specialists"
CATALYST_INDEX = (
    "https://www.catalystbehavior.com/wp-json/wp/v2/locations_place?per_page=100"
)
_CATALYST_MARKERS_RE = re.compile(r'"markers"\s*:\s*(\[.*?\])', re.S)


def parse_catalyst_page(html: str) -> RosterCenter | None:
    """Parse one Catalyst location page's marker content block. Pure."""
    m = _CATALYST_MARKERS_RE.search(html)
    if not m:
        return None
    try:
        markers = json.loads(m.group(1))
    except json.JSONDecodeError:
        return None
    for marker in markers:
        content = marker.get("content") or ""
        # The content is "<strong>City, ST</strong><br> STREET <br> City, ST ZIP
        # <br> Phone:"; take the block between the heading and the phone line.
        body = re.split(r"Phone:", content, maxsplit=1)[0]
        body = re.split(r"</strong>", body, maxsplit=1)[-1]
        center = _center_from_br_block(CATALYST_OWNER, body.lstrip("<br>/ \r\n"))
        if center:
            return center
    return None


def fetch_catalyst(client: httpx.Client) -> tuple[bytes, str]:
    """Enumerate Catalyst's locations_place entries, then read each marker block."""
    index = json.loads(fetch.get(CATALYST_INDEX, client=client))
    centers: list[dict] = []
    for item in index:
        link = item.get("link")
        if not link:
            continue
        try:
            html = fetch.get(link, client=client).decode("utf-8", "replace")
        except (httpx.HTTPError, fetch.FetchError) as exc:
            logger.warning("catalyst page failed %s: %s", link, exc)
            continue
        center = parse_catalyst_page(html)
        if center:
            center.detail_url = link
            centers.append(vars(center))
        time.sleep(REQUEST_DELAY_SEC)
    return json.dumps({"source": CATALYST_INDEX, "centers": centers}).encode(), CATALYST_INDEX


def parse_catalyst_roster(content: bytes) -> list[RosterCenter]:
    return [RosterCenter(**c) for c in json.loads(content).get("centers", [])]


SOURCES = {
    "learn": (fetch_learn, parse_learn_roster),
    "caravel": (fetch_caravel, parse_caravel_roster),
    "behavioral-innovations": (fetch_bi, parse_bi_roster),
    "hopebridge": (fetch_hopebridge, parse_hopebridge_roster),
    "helping-hands-family": (fetch_hhf, parse_hhf_roster),
    "centria": (fetch_centria, parse_centria_roster),
    "inbloom": (fetch_inbloom, parse_inbloom_roster),
    "abs-kids": (fetch_abs, parse_abs_roster),
    "behavior-frontiers": (fetch_bf, parse_bf_roster),
    "kind-behavioral-health": (fetch_kbh, parse_kbh_roster),
    "behavior-care-specialists": (fetch_catalyst, parse_catalyst_roster),
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
