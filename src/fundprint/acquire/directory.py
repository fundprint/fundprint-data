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
_LOCATION_TYPES = {"MedicalBusiness", "MedicalClinic", "LocalBusiness", "Physician"}
# Splits a US "street, City, ST ZIP" tail. City may lack a leading comma.
_STATE_ZIP_RE = re.compile(
    r"^(?P<pre>.*?)[,\s]+(?P<state>[A-Z]{2})\s+(?P<zip>\d{5})(?:-\d{4})?\s*$"
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
    """Return the schema.org ``address`` dict of the first location node, if any."""
    for node in _iter_jsonld_nodes(content):
        node_type = node.get("@type")
        types = node_type if isinstance(node_type, list) else [node_type]
        if not (set(types) & _LOCATION_TYPES):
            continue
        addr = node.get("address")
        if isinstance(addr, list):
            addr = addr[0] if addr else None
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
    return {
        "raw_name": name,
        "address_line1": (addr.get("streetAddress") or "").strip() or None,
        "city": (addr.get("addressLocality") or "").strip() or None,
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
    s = " ".join((value or "").split())
    m = _STATE_ZIP_RE.match(s)
    if not m:
        return None
    pre = m.group("pre").rstrip(", ").strip()
    state = m.group("state")
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
    return (street or None, city or None, state, zip_code)


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


_SOURCES: dict[str, type[_DirectorySource]] = {
    BlueSprigDirectory.key: BlueSprigDirectory,
    AcesDirectory.key: AcesDirectory,
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
