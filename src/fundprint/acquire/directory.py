"""Owner location-directory acquire layer (a second source of clinic existence).

Why this exists: NPPES enumerates only the handful of provider *organizations* a
chain registers with a National Provider Identifier, so it structurally
undercounts a chain that operates many centers under a few NPIs. BlueSprig, for
example, registers ~26 NPI-2 organizations but publicly lists ~154 operating
centers; ACES registers ~15 but lists ~70. The authoritative source for those
operating locations is the owner's own public directory.

This layer reads that directory as a distinct, honestly-labeled source
(``source_record.source_type = 'owner_location_directory'``) and stages each
center into the same ``staging_bacb_provider`` table the NPPES scraper uses, with
no NPI. Provenance is identical to the other scrapers: each center page is fetched
and stored as a content-hashed snapshot linked to a ``source_record``. The parse
is a pure read of the page's schema.org ``MedicalBusiness`` JSON-LD block (a
machine-readable address the site publishes for search engines), not brittle
scraping of rendered HTML.

There are two source shapes:

* **Prefix sources** (e.g. BlueSprig) whose directory mixes several tracked
  brands; each center's name carries its brand, so the deterministic clinic
  linker brand-matches it to an owner exactly as it does an NPPES row.
* **Explicit-owner sources** (e.g. ACES) whose every center belongs to one known
  owner but whose pages are generically named; these are attributed to that owner
  directly by ``resolve.clinic_link.link_directory_owner`` rather than by name.

Either way, directory centers are de-duplicated against clinics already gathered
from NPPES (and each other) so the same physical center is not counted twice, and
a center that matches no tracked owner is simply left unlinked. Ingest is
idempotent per page via the ``(source_url, content_hash)`` guard.

Usage:
    python scripts/run_acquire_directory.py
    python scripts/run_acquire_directory.py --source aces
"""

from __future__ import annotations

import html
import json
import logging
import re
import time
from datetime import UTC, datetime
from typing import Any

import httpx

from fundprint import db
from fundprint.acquire.base import (
    _find_existing_source_record,
    _insert_source_record,
)
from fundprint.storage import LocalFilesystemStore, SnapshotStore

logger = logging.getLogger(__name__)

FUNDPRINT_UA = "FundprintBot/0.1 (+mailto:atharva.doke737@gmail.com)"
SOURCE_TYPE = "owner_location_directory"
MODULE_VERSION = "0.1.0"
REQUEST_DELAY_SEC = 0.15

_JSONLD_RE = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.S | re.I,
)
_LOC_RE = re.compile(r"<loc>([^<]+)</loc>", re.I)
_CITY_STATE_RE = re.compile(r"\bin\s+([A-Za-z .'\-]+?),\s*([A-Z]{2})\b")
# A Drupal "address" field: semantic <span class="..."> parts under one wrapper.
_DRUPAL_ADDR_BLOCK_RE = re.compile(
    r"field--name-field-location-address.*?</p>", re.S | re.I
)
_DRUPAL_SPAN_RE = re.compile(
    r'<span class="([a-z0-9\-]+)"[^>]*>(.*?)</span>', re.S | re.I
)
_LOCATION_TYPES = {"MedicalBusiness", "MedicalClinic", "LocalBusiness", "Physician"}
_EN_DASH_RE = re.compile(r"[‐-―]")
_TAG_RE = re.compile(r"<[^>]+>")
_US_STATE_NAMES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "district of columbia": "DC", "florida": "FL", "georgia": "GA", "hawaii": "HI",
    "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS",
    "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI",
    "south carolina": "SC", "south dakota": "SD", "tennessee": "TN", "texas": "TX",
    "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
}
_USPS_CODES = set(_US_STATE_NAMES.values())
_STATE_NAME_TAIL_RE = re.compile(
    r"\b(" + "|".join(sorted(_US_STATE_NAMES, key=len, reverse=True)) + r")\b"
    r"(?=[,\s]+\d{5}(?:-\d{4})?\s*$)",
    re.I,
)


def _strip_tags(value: str) -> str:
    """Drop HTML tags from a JSON-LD text value (some CMSes leave a <br /> in)."""
    return _TAG_RE.sub(" ", value)


def _is_street(street: str | None) -> bool:
    """True when a parsed street plausibly names a building.

    A directory row is not always an address. Acorn lists Alpena, MI with the text
    "In-home services available now" where the street goes, which parsed cleanly and
    staged a clinic whose street was that sentence. Requiring a digit is enough to
    reject it: a US street address carries a house number, and an entry without one
    is either prose or a service area, neither of which is a physical site.
    """
    return bool(street) and any(ch.isdigit() for ch in street)


def _expand_state_name(value: str) -> str:
    """Rewrite a spelled-out state immediately before the ZIP to its USPS code.

    ALP publishes both "Holyoke, MA 01040" and "Holyoke, Massachusetts 01040". This is
    a canonicalisation of one fixed vocabulary, not a fuzzy match: it only ever fires
    on a state name sitting directly in front of a five-digit ZIP, so a street called
    Virginia Avenue is untouched.
    """
    return _STATE_NAME_TAIL_RE.sub(
        lambda m: _US_STATE_NAMES[m.group(1).lower()], value
    )
# Splits a US "street, City, ST ZIP" tail. City may lack a leading comma, the state
# may be lowercased and abbreviated with a period, and a comma may sit between the
# state and the ZIP: Hopebridge writes Georgia centres as "Atlanta, Ga., 30329".
# Matching case-insensitively is only safe because the captured token is then checked
# against the real USPS code list, so a two-letter word cannot be read as a state.
_STATE_ZIP_RE = re.compile(
    r"^(?P<pre>.*?)[,\s]+(?P<state>[A-Za-z]{2})\.?[,\s]+(?P<zip>\d{5})(?:-\d{4})?\s*$"
)
# A country suffix sits *after* the ZIP, so it defeats the anchored tail match above.
_TRAILING_COUNTRY_RE = re.compile(
    r"[,\s]+(?:USA|U\.S\.A\.|US|United States(?: of America)?)\.?\s*$", re.I
)
# Splits a "City, ST" or "City ST - Neighbourhood" locality label.
_LOCALITY_RE = re.compile(
    r"^(?P<city>.*?)[,\s]+(?P<state>[A-Z]{2})\b(?:\s*[-,]\s*(?P<suffix>.*))?$"
)


def _iter_jsonld_nodes(content: bytes | str) -> Any:
    """Yield every JSON-LD object embedded in a page (flattening @graph)."""
    text = content.decode("utf-8", "replace") if isinstance(content, bytes) else content
    for blob in _JSONLD_RE.findall(text):
        try:
            data = json.loads(blob)
        except json.JSONDecodeError:
            continue
        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
            elif isinstance(node, dict):
                if "@graph" in node and isinstance(node["@graph"], list):
                    stack.extend(node["@graph"])
                yield node


def _jsonld_address(content: bytes | str) -> dict[str, Any] | None:
    """Return the schema.org ``address`` of the first location node, if any."""
    for node in _iter_jsonld_nodes(content):
        node_type = node.get("@type")
        types = node_type if isinstance(node_type, list) else [node_type]
        if not (set(types) & _LOCATION_TYPES):
            continue
        addr = node.get("address")
        if isinstance(addr, list):
            addr = addr[0] if addr else None
        # schema.org allows `address` to be a plain Text as well as a PostalAddress,
        # and Autism Learning Partners uses the Text form. Reading only the dict form
        # silently skipped all 59 of its centre pages, which is why the chain sat at
        # one clinic. Present it as a one-line streetAddress; parse_us_address splits it.
        if isinstance(addr, str) and addr.strip():
            return {"name": node.get("name"), "streetAddress": addr.strip()}
        if isinstance(addr, dict) and (addr.get("streetAddress") or addr.get("addressLocality")):
            return {"name": node.get("name"), **addr}
    return None


def parse_jsonld_location(
    content: bytes | str, *, fallback_name: str | None = None
) -> dict[str, Any] | None:
    """Extract a staging row from a page whose JSON-LD splits the address fields.

    Used for directories (like BlueSprig) that populate ``addressLocality`` /
    ``addressRegion`` / ``postalCode`` separately. Pure function for testing.
    """
    addr = _jsonld_address(content)
    if addr is None:
        return None
    name = html.unescape((addr.get("name") or fallback_name or "").strip())
    if not name:
        return None
    state = (addr.get("addressRegion") or "").strip()[:2].upper() or None
    postal = addr.get("postalCode")
    # Unescape the ADDRESS too, not just the name. JSON-LD embedded in HTML carries
    # HTML entities, and "Suite 100 &amp; 400" was reaching the database verbatim:
    # it displays wrong, and worse, the stray "amp" token lands in the site key and
    # stops the address matching itself when it arrives from another source.
    return {
        "raw_name": name,
        "address_line1": html.unescape((addr.get("streetAddress") or "").strip()) or None,
        "city": html.unescape((addr.get("addressLocality") or "").strip()) or None,
        "state": state,
        "zip": str(postal).strip() if postal else None,
        "npi": None,
    }


def parse_us_address(value: str) -> tuple[str | None, str | None, str, str] | None:
    """Split a one-line US address into (street, city, state, zip).

    Handles the common shapes ``"5701 W Talavi Blvd., Glendale, AZ 85306"`` and
    the comma-less ``"6511 W Loop 1604 N., Suite 123 San Antonio, TX 78254"``.
    Returns None when no ``ST ZIP`` tail is present. Pure function for testing.
    """
    s = _strip_tags(value or "")
    s = " ".join(s.split())
    s = _TRAILING_COUNTRY_RE.sub("", s)
    s = _expand_state_name(s)
    m = _STATE_ZIP_RE.match(s)
    if not m:
        return None
    state = m.group("state").upper()
    if state not in _USPS_CODES:
        return None
    pre = m.group("pre").rstrip(", ").strip()
    zip_code = m.group("zip")
    if "," in pre:
        street, last = pre.rsplit(",", 1)
        city_m = re.search(r"([A-Za-z .'\-]+)$", last.strip())
        city = city_m.group(1).strip() if city_m else last.strip()
        street = street.strip()
    else:
        # No comma before the city: peel trailing alpha words off a street that
        # ends in a number or suite token (e.g. "... Suite 123 San Antonio").
        city_m = re.search(r"(\d\S*|\bSuite\b[^A-Za-z]*\S*)\s+([A-Za-z .'\-]+)$", pre)
        if city_m:
            city = city_m.group(2).strip()
            street = pre[: city_m.start(2)].strip()
        else:
            city, street = None, pre
        # The peel is only safe when a street name survives it. On "7041 Transit Rd
        # East Amherst" the last digit run is the house number itself, so every word
        # after it gets taken for the city and the street collapses to "7041". Refuse
        # the row instead: a wrong street silently becomes a wrong site key, and the
        # centre then fails to match the same building arriving from the registry and
        # is counted twice. Use parse_address_with_known_locality when the source
        # tells us the city separately.
        if street and not re.search(r"[A-Za-z]", street):
            return None
    if not _is_street(street):
        return None
    return (street or None, city or None, state, zip_code)


def parse_address_with_known_locality(
    value: str, locality: str
) -> tuple[str | None, str | None, str, str] | None:
    """Split a one-line US address whose city and state are already known.

    Use this, not ``parse_us_address``, whenever the source hands us the locality
    separately (Acorn Health titles every centre "Novi, MI"). Guessing where the
    street ends is unreliable exactly where it matters: "890 Airport Park Road Suite
    100 Glen Burnie MD 21061" has no comma before the city, so a heuristic peels only
    the last alpha run and yields the street "...Suite 100 Glen" in the city "Burnie".
    A wrong street is worse than no street, because the site key is built from it, so
    the centre stops matching the registry row for the same building and gets counted
    twice.

    Knowing the locality turns the guess into a subtraction: strip the ZIP, strip the
    known "City, ST" tail, and whatever remains in front is the street. Returns None if
    the address does not in fact end with that locality. Pure function for testing.
    """
    s = _EN_DASH_RE.sub("-", _strip_tags(html.unescape(value or "")))
    s = _expand_state_name(" ".join(s.split()))
    label = " ".join(_EN_DASH_RE.sub("-", html.unescape(locality or "")).split())
    m = _LOCALITY_RE.match(label)
    if not m:
        return None
    city, state = m.group("city").strip(), m.group("state").upper()
    zip_m = re.search(r"\b(\d{5})(?:-\d{4})?\s*$", _TRAILING_COUNTRY_RE.sub("", s))
    if not zip_m:
        return None
    body = _TRAILING_COUNTRY_RE.sub("", s)[: zip_m.start()].strip().strip(",").strip()
    # Match the label's words rather than its exact text: the source punctuates the
    # same locality inconsistently ("Fort Wayne, IN" in the address, "Fort Wayne IN"
    # in the heading it came from). Slicing at the match keeps the street's own
    # internal commas intact.
    tail = re.compile(
        r"[,\s]+".join(re.escape(tok) for tok in label.replace(",", " ").split())
        + r"\s*$",
        re.I,
    )
    tail_m = tail.search(body)
    if tail_m is None:
        return None
    street = body[: tail_m.start()].strip().strip(",").strip()
    if not _is_street(street):
        return None
    return (street or None, city or None, state, zip_m.group(1))


def parse_drupal_address_field(content: bytes | str) -> dict[str, str | None] | None:
    """Read a Drupal ``address`` field's semantic spans into address parts.

    Drupal's address field renders each part in its own class-named span
    (``address-line1``, ``locality``, ``administrative-area``, ``postal-code``),
    which is a machine-readable contract the same way schema.org JSON-LD is, not
    brittle scraping of free text. Returns None when no ``ST ZIP`` is present.
    Pure function for testing.
    """
    text = content.decode("utf-8", "replace") if isinstance(content, bytes) else content
    block = _DRUPAL_ADDR_BLOCK_RE.search(text)
    if block is None:
        return None
    parts = {
        cls: html.unescape(val.strip())
        for cls, val in _DRUPAL_SPAN_RE.findall(block.group(0))
    }
    state = (parts.get("administrative-area") or "").strip()[:2].upper()
    zip_code = (parts.get("postal-code") or "").strip()[:5]
    if not (state and zip_code.isdigit()):
        return None
    street = " ".join(
        p for p in (parts.get("address-line1"), parts.get("address-line2")) if p
    ).strip()
    return {
        "address_line1": street or None,
        "city": (parts.get("locality") or "").strip() or None,
        "state": state,
        "zip": zip_code,
    }


def _write_staging_row(conn: Any, row: dict[str, Any], source_record_id: str) -> None:
    conn.execute(
        """
        INSERT INTO staging_bacb_provider
            (source_record_id, raw_name, address_line1, city, state, zip,
             npi, credential_type)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            source_record_id,
            row["raw_name"],
            row.get("address_line1"),
            row.get("city"),
            row.get("state"),
            row.get("zip"),
            None,
            None,
        ),
    )


def _snapshot_and_stage(
    store: SnapshotStore, url: str, content: bytes, row: dict[str, Any]
) -> str:
    """Snapshot one page and stage its row. Returns 'staged' or 'skipped'."""
    snapshot_id, content_hash = store.put(content)
    with db.transaction() as conn:
        if _find_existing_source_record(conn, url, content_hash):
            return "skipped"
        source_record_id = _insert_source_record(
            conn,
            source_url=url,
            snapshot_id=snapshot_id,
            source_type=SOURCE_TYPE,
            fetched_at=datetime.now(UTC),
            content_hash=content_hash,
            module_version=MODULE_VERSION,
        )
        _write_staging_row(conn, row, source_record_id)
    return "staged"


class _DirectorySource:
    """Base for a single owner-directory source."""

    key: str
    host: str
    # None => centers carry their brand in the name; the prefix linker attributes
    # them. A value => every center belongs to this owner_entity (attributed by
    # link_directory_owner) because the pages are generically named.
    owner_name: str | None = None

    def __init__(self, store: SnapshotStore | None = None) -> None:
        self._store = store or LocalFilesystemStore()

    def _centers(self, client: httpx.Client) -> list[dict[str, Any]]:
        """Return [{url, row}] for every center in the directory."""
        raise NotImplementedError

    def run(self) -> dict[str, int]:
        summary = {"seen": 0, "staged": 0, "skipped": 0, "failed": 0}
        with httpx.Client(
            timeout=30.0, follow_redirects=True, headers={"User-Agent": FUNDPRINT_UA}
        ) as client:
            centers = self._centers(client)
            summary["seen"] = len(centers)
            logger.info("%s directory: %d centers listed", self.key, len(centers))
            for center in centers:
                try:
                    result = _snapshot_and_stage(
                        self._store, center["url"], center["content"], center["row"]
                    )
                    summary[result] += 1
                except Exception:
                    logger.exception("directory stage failed for %s", center["url"])
                    summary["failed"] += 1
        logger.info("%s directory ingest complete: %s", self.key, summary)
        return summary


class BlueSprigDirectory(_DirectorySource):
    """The BlueSprig network directory (a prefix source, several KKR brands).

    BlueSprig publishes its centers as a WordPress custom post type at
    ``/wp-json/wp/v2/center``; each center's own page carries schema.org JSON-LD
    with its street address. The listing spans BlueSprig, Trumpet Behavioral
    Health, and Florida Autism Center; each center's name carries its brand, so
    the deterministic prefix linker attributes it.
    """

    key = "bluesprig"
    host = "bluesprigautism.com"
    base = "https://www.bluesprigautism.com"
    max_pages = 10

    def _centers(self, client: httpx.Client) -> list[dict[str, Any]]:
        centers: list[dict[str, Any]] = []
        for page in range(1, self.max_pages + 1):
            url = f"{self.base}/wp-json/wp/v2/center?per_page=100&page={page}"
            resp = client.get(url, headers={"Accept": "application/json"})
            if resp.status_code == 400:
                break
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break
            for item in batch:
                link = item.get("link")
                if not link:
                    continue
                seo = (item.get("yoast_head_json") or {}).get("title") or ""
                fallback = html.unescape((seo.split(" in ")[0] or "").strip())
                try:
                    page_resp = client.get(link)
                    page_resp.raise_for_status()
                except Exception:
                    logger.exception("directory fetch failed for %s", link)
                    continue
                row = parse_jsonld_location(page_resp.content, fallback_name=fallback)
                if row is None:
                    row = self._row_from_seo(seo)
                if row is not None:
                    centers.append(
                        {"url": link, "content": page_resp.content, "row": row}
                    )
                time.sleep(REQUEST_DELAY_SEC)
            total_pages = int(resp.headers.get("X-WP-TotalPages", "1") or "1")
            if page >= total_pages:
                break
        return centers

    @staticmethod
    def _row_from_seo(seo_title: str) -> dict[str, Any] | None:
        """Fallback staging row from the SEO title alone (no street address)."""
        name = html.unescape((seo_title.split(" in ")[0] or "").strip())
        m = _CITY_STATE_RE.search(seo_title)
        if not name or not m:
            return None
        return {
            "raw_name": name,
            "address_line1": None,
            "city": m.group(1).strip(),
            "state": m.group(2).strip()[:2].upper(),
            "zip": None,
            "npi": None,
        }


class AcesDirectory(_DirectorySource):
    """The ACES directory (an explicit-owner source, all General Atlantic / ACES).

    ACES lists its centers as pages under ``/locations/<slug>``, enumerated by the
    site's sitemap, each carrying a schema.org JSON-LD address as a single string.
    Every center belongs to ACES, so they are attributed to the ``ACES 2020``
    owner entity directly (the pages are generically titled and do not carry a
    brand prefix to match on).
    """

    key = "aces"
    host = "acesaba.com"
    base = "https://acesaba.com"
    owner_name = "ACES 2020"

    def _center_urls(self, client: httpx.Client) -> list[str]:
        resp = client.get(f"{self.base}/sitemap.xml")
        resp.raise_for_status()
        urls = _LOC_RE.findall(resp.text)
        # English center detail pages only: /locations/<slug>, not the /es/
        # Spanish mirror and not the bare /locations index.
        detail = [
            u.rstrip("/")
            for u in urls
            if re.search(r"/locations/[^/]+/?$", u) and "/es/" not in u
        ]
        return sorted(set(detail))

    def _centers(self, client: httpx.Client) -> list[dict[str, Any]]:
        centers: list[dict[str, Any]] = []
        for url in self._center_urls(client):
            try:
                resp = client.get(url)
                resp.raise_for_status()
            except Exception:
                logger.exception("directory fetch failed for %s", url)
                continue
            addr = _jsonld_address(resp.content)
            if addr is None or not addr.get("streetAddress"):
                continue
            parsed = parse_us_address(addr["streetAddress"])
            if parsed is None:
                continue
            street, city, state, zip_code = parsed
            slug = url.rstrip("/").rsplit("/", 1)[-1].replace("-", " ").title()
            centers.append(
                {
                    "url": url,
                    "content": resp.content,
                    "row": {
                        "raw_name": f"ACES ABA - {slug}",
                        "address_line1": street,
                        "city": city,
                        "state": state,
                        "zip": zip_code,
                        "npi": None,
                    },
                }
            )
            time.sleep(REQUEST_DELAY_SEC)
        return centers


class ProudMomentsDirectory(_DirectorySource):
    """The Proud Moments directory (an explicit-owner source, all Nautic / Proud Moments).

    Proud Moments lists every center as a card on ``/our-locations`` linking to a
    detail page, whose Drupal ``address`` field carries the street address in
    class-named spans. Every center belongs to Proud Moments, so they are
    attributed to the ``Proud Moments`` owner entity directly. NPPES registers
    only ~14 organization NPIs for the chain; the public directory lists ~109
    operating centers, which is exactly the undercount this layer exists to fill.
    """

    key = "proudmoments"
    host = "proudmomentsaba.com"
    base = "https://www.proudmomentsaba.com"
    owner_name = "Proud Moments"

    _CARD_LINK_RE = re.compile(
        r'class="location-card".*?<a href="(https://www\.proudmomentsaba\.com/[a-z0-9\-]+)"',
        re.S | re.I,
    )

    def _center_urls(self, client: httpx.Client) -> list[str]:
        resp = client.get(f"{self.base}/our-locations")
        resp.raise_for_status()
        return sorted(set(self._CARD_LINK_RE.findall(resp.text)))

    @staticmethod
    def _name_from_slug(url: str) -> str:
        slug = url.rstrip("/").rsplit("/", 1)[-1]
        slug = re.sub(r"-aba-therapy$", "", slug)
        return f"Proud Moments ABA - {slug.replace('-', ' ').title()}"

    def _centers(self, client: httpx.Client) -> list[dict[str, Any]]:
        centers: list[dict[str, Any]] = []
        for url in self._center_urls(client):
            try:
                resp = client.get(url)
                resp.raise_for_status()
            except Exception:
                logger.exception("directory fetch failed for %s", url)
                continue
            addr = parse_drupal_address_field(resp.content)
            if addr is None:
                continue
            centers.append(
                {
                    "url": url,
                    "content": resp.content,
                    "row": {"raw_name": self._name_from_slug(url), "npi": None, **addr},
                }
            )
            time.sleep(REQUEST_DELAY_SEC)
        return centers


class ActionBehaviorDirectory(_DirectorySource):
    """The Action Behavior Centers directory (an explicit-owner source, Charlesbank).

    ABC publishes every centre as ``/location/<state>/<slug>``, enumerated from the
    site's sitemap, each carrying schema.org JSON-LD with a split ``PostalAddress``.

    This source exists because the registry is wrong about ABC in *both* directions
    at once, which is the clearest case yet for reading an owner's own directory:

      * It undercounts. ABC's directory lists 227 Texas centres. NPPES gave us 15.
        The registry cannot see a centre a chain never registered separately.
      * It overcounts. NPPES gave us 67 Colorado clinics against ABC's 42, plus
        nine in Ohio, three in Virginia, one in Georgia and an *apartment* in Palm
        Beach Gardens, none of which ABC operates. The registry never marks a
        closed clinic closed, and it happily carries an address the company
        abandoned years ago.

    The JSON-LD ``name`` is the brand string ("Action Behavior Centers") on every
    page, so it cannot distinguish one centre from another. The slug is what
    carries the identity ("alamo-ranch"), so the row name is built from the URL,
    as it is for ACES and Proud Moments.
    """

    key = "action_behavior"
    host = "actionbehavior.com"
    base = "https://www.actionbehavior.com"
    owner_name = "Action Behavior Centers"

    def _center_urls(self, client: httpx.Client) -> list[str]:
        resp = client.get(f"{self.base}/sitemap.xml")
        resp.raise_for_status()
        urls = _LOC_RE.findall(resp.text)
        # Centre detail pages are /location/<state>/<slug>. /location/request is the
        # "find a centre near me" form, not a centre.
        detail = [
            u.rstrip("/")
            for u in urls
            if re.search(r"/location/[^/]+/[^/]+/?$", u) and "/location/request" not in u
        ]
        return sorted(set(detail))

    @staticmethod
    def _name_from_slug(url: str) -> str:
        slug = url.rstrip("/").rsplit("/", 1)[-1]
        return f"Action Behavior Centers - {slug.replace('-', ' ').title()}"

    def _centers(self, client: httpx.Client) -> list[dict[str, Any]]:
        centers: list[dict[str, Any]] = []
        for url in self._center_urls(client):
            try:
                resp = client.get(url)
                resp.raise_for_status()
            except Exception:
                logger.exception("directory fetch failed for %s", url)
                continue
            row = parse_jsonld_location(resp.content)
            if row is None or not row.get("address_line1"):
                continue
            centers.append(
                {
                    "url": url,
                    "content": resp.content,
                    "row": {**row, "raw_name": self._name_from_slug(url)},
                }
            )
            time.sleep(REQUEST_DELAY_SEC)
        return centers


class AutismLearningPartnersDirectory(_DirectorySource):
    """The Autism Learning Partners directory (an explicit-owner source, FFL Partners).

    ALP's sitemap carries 200 URLs under ``/locations/``, but they are a three-level
    tree: ``/locations/<state>/``, ``/locations/<state>/<county>/`` and, at the leaf,
    ``/locations/<state>/<county>/<centre>/``. Only the leaves are centres, and only
    the leaves carry an address, so the depth filter is the whole selection rule.

    This corrects a real misreading. ALP was previously recorded as a chain that
    publishes service-area pages with no street address and registers a single NPI,
    and so was left at one clinic on purpose. That is true of the state and county
    pages. It is not true of the 59 leaf pages beneath them, each of which is a
    physical centre publishing its own street address in schema.org JSON-LD. The
    lesson generalises: "the directory has no addresses" has to be checked at the
    leaf, not at the index that links to it.

    The address is a schema.org Text (one line, "2406 Merced Street, San Leandro, CA
    94577, USA"), not a PostalAddress, so it is split by ``parse_us_address``.
    """

    key = "autism_learning_partners"
    host = "autismlearningpartners.com"
    base = "https://autismlearningpartners.com"
    owner_name = "Autism Learning Partners"

    # /locations/<state>/<county>/<centre>/ -> 7 parts once the scheme is counted.
    _LEAF_DEPTH = 7

    def _center_urls(self, client: httpx.Client) -> list[str]:
        resp = client.get(f"{self.base}/sitemap.xml")
        resp.raise_for_status()
        urls = _LOC_RE.findall(resp.text)
        if "<sitemapindex" in resp.text:
            nested: list[str] = []
            for sub in urls:
                sub_resp = client.get(sub)
                sub_resp.raise_for_status()
                nested.extend(_LOC_RE.findall(sub_resp.text))
            urls = nested
        return sorted(
            {
                u
                for u in urls
                if "/locations/" in u
                and len(u.rstrip("/").split("/")) == self._LEAF_DEPTH
            }
        )

    @staticmethod
    def _parse_with_url_city(
        raw: str, url: str
    ) -> tuple[str | None, str | None, str, str] | None:
        """Fall back on the city in the URL when the address omits its commas.

        Some ALP pages write the address as one unpunctuated run ("7041 Transit Rd
        East Amherst New York 14051"), where no rule can tell the street from the
        city: the last number is the house number, so peeling trailing words takes
        the whole street name into the city. The leaf URL
        (``/new-york/erie-county/east-amherst/``) already names the city, which turns
        the guess back into a subtraction. If the address does not actually end in
        that city, the parse is refused rather than forced.
        """
        m = re.search(r"\b([A-Z]{2})\s+\d{5}(?:-\d{4})?\s*$", _expand_state_name(
            _TRAILING_COUNTRY_RE.sub("", " ".join(_strip_tags(raw).split()))
        ))
        if not m:
            return None
        slug = url.rstrip("/").rsplit("/", 1)[-1]
        city = slug.replace("-", " ").title()
        return parse_address_with_known_locality(raw, f"{city} {m.group(1)}")

    def _centers(self, client: httpx.Client) -> list[dict[str, Any]]:
        centers: list[dict[str, Any]] = []
        for url in self._center_urls(client):
            try:
                resp = client.get(url)
                resp.raise_for_status()
            except Exception:
                logger.exception("directory fetch failed for %s", url)
                continue
            addr = _jsonld_address(resp.content)
            if addr is None or not addr.get("streetAddress"):
                continue
            name = html.unescape((addr.get("name") or "").strip())
            raw = html.unescape(str(addr["streetAddress"])).strip()
            # A few pages prepend the centre's own name to its address ("Autism
            # Learning Partners San Diego, 4025 Camino del Rio South #101, ..."),
            # which would otherwise land in address_line1 and be published as if it
            # were part of the street.
            if name and raw.casefold().startswith(name.casefold()):
                raw = raw[len(name) :].lstrip(" ,")
            # URL-city subtraction first, guess second. parse_us_address does not fail
            # loudly on an unpunctuated address, it just returns a bad split ("7316
            # Spout Springs Rd Suite 103 Flowery" in a city called "Branch"), so
            # trying it first would mask the reliable answer with a plausible wrong
            # one. When the slug is not the city (whittier -> La Habra) the
            # subtraction declines and the guess still gets its turn.
            parsed = self._parse_with_url_city(raw, url) or parse_us_address(raw)
            if parsed is None:
                # City-only pages ("Newark, NJ, USA") are service-area pages, not
                # centres, and are meant to fall out here.
                logger.info("alp: no street address on %s (%r)", url, raw)
                continue
            street, city, state, zip_code = parsed
            centers.append(
                {
                    "url": url,
                    "content": resp.content,
                    "row": {
                        "raw_name": name or "Autism Learning Partners",
                        "address_line1": street,
                        "city": city,
                        "state": state,
                        "zip": zip_code,
                        "npi": None,
                    },
                }
            )
            time.sleep(REQUEST_DELAY_SEC)
        return centers


class AcornHealthDirectory(_DirectorySource):
    """The Acorn Health directory (an explicit-owner source, MBF Healthcare Partners).

    Acorn publishes its centres as a WordPress ``locations`` post type, so the roster
    of 71 arrives in one call. The centre pages carry no schema.org address; the
    address instead sits in the ``#address`` input the page hands to its own Google
    Maps geocoder to place its pin. That input is a machine-readable contract in the
    same sense the JSON-LD is (the site depends on it resolving to the right
    building), not a scrape of rendered prose.

    The address string does not reliably delimit the city ("890 Airport Park Road
    Suite 100 Glen Burnie MD 21061"), but each post's title names it ("Glen Burnie,
    MD"), so the split is a subtraction rather than a guess. See
    ``parse_address_with_known_locality`` for why guessing here is actively unsafe.
    """

    key = "acorn"
    host = "acornhealth.com"
    base = "https://acornhealth.com"
    owner_name = "Acorn Health"

    _ADDRESS_INPUT_RE = re.compile(
        r'<input[^>]+id="address"[^>]+value="([^"]+)"', re.I
    )

    def _posts(self, client: httpx.Client) -> list[dict[str, Any]]:
        resp = client.get(
            f"{self.base}/wp-json/wp/v2/locations?per_page=100",
            headers={"Accept": "application/json"},
        )
        resp.raise_for_status()
        return resp.json()

    def _centers(self, client: httpx.Client) -> list[dict[str, Any]]:
        centers: list[dict[str, Any]] = []
        for post in self._posts(client):
            url = post.get("link")
            title = ((post.get("title") or {}).get("rendered") or "").strip()
            if not url or not title:
                continue
            try:
                resp = client.get(url)
                resp.raise_for_status()
            except Exception:
                logger.exception("directory fetch failed for %s", url)
                continue
            m = self._ADDRESS_INPUT_RE.search(resp.text)
            if m is None:
                logger.warning("acorn: no address input on %s", url)
                continue
            parsed = parse_address_with_known_locality(m.group(1), title)
            if parsed is None:
                logger.warning("acorn: unparsed address on %s: %r", url, m.group(1))
                continue
            street, city, state, zip_code = parsed
            centers.append(
                {
                    "url": url,
                    "content": resp.content,
                    "row": {
                        "raw_name": f"Acorn Health - {html.unescape(title)}",
                        "address_line1": street,
                        "city": city,
                        "state": state,
                        "zip": zip_code,
                        "npi": None,
                    },
                }
            )
            time.sleep(REQUEST_DELAY_SEC)
        return centers


_SOURCES: dict[str, type[_DirectorySource]] = {
    BlueSprigDirectory.key: BlueSprigDirectory,
    AcesDirectory.key: AcesDirectory,
    ProudMomentsDirectory.key: ProudMomentsDirectory,
    ActionBehaviorDirectory.key: ActionBehaviorDirectory,
    AutismLearningPartnersDirectory.key: AutismLearningPartnersDirectory,
    AcornHealthDirectory.key: AcornHealthDirectory,
}


def explicit_owner_sources() -> list[tuple[str, str]]:
    """Return (host, owner_name) for every explicit-owner directory source."""
    return [
        (cls.host, cls.owner_name)
        for cls in _SOURCES.values()
        if cls.owner_name is not None
    ]


def ingest_directories(
    source: str | None = None, *, store: SnapshotStore | None = None
) -> dict[str, int]:
    """Run every configured directory source (or one named source)."""
    sources = _SOURCES
    if source:
        if source not in _SOURCES:
            raise KeyError(f"unknown directory source {source!r}; have {list(_SOURCES)}")
        sources = {source: _SOURCES[source]}
    totals = {"seen": 0, "staged": 0, "skipped": 0, "failed": 0}
    for cls in sources.values():
        summary = cls(store=store).run()
        for k in totals:
            totals[k] += summary.get(k, 0)
    return totals
