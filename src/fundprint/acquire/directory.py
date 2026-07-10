"""Owner location-directory acquire layer (a second source of clinic existence).

Why this exists: NPPES enumerates only the handful of provider *organizations* a
chain registers with a National Provider Identifier, so it structurally
undercounts a chain that operates many centers under a few NPIs. BlueSprig, for
example, registers ~26 NPI-2 organizations but publicly lists far more operating
centers. The authoritative source for those operating locations is the owner's
own public directory.

This layer reads that directory as a distinct, honestly-labeled source
(``source_record.source_type = 'owner_location_directory'``) and stages each
center into the same ``staging_bacb_provider`` table the NPPES scraper uses, with
no NPI. The deterministic clinic linker then brand-matches each center to the
owner exactly as it does an NPPES row, and de-duplicates directory centers
against clinics already gathered from NPPES so the same physical center is not
counted twice (see ``resolve.clinic_link``).

Provenance is identical to the other scrapers: each center page is fetched and
stored as a content-hashed snapshot, linked to a ``source_record``. The parse is
a pure read of the page's schema.org ``MedicalBusiness`` JSON-LD block (a machine
-readable address the site publishes for search engines), not brittle scraping of
rendered HTML. Ingest is idempotent per page via the ``(source_url,
content_hash)`` guard.

Only owners the pipeline already attributes to a parent firm with a public source
gain clinics here; a staged center whose name does not brand-prefix-match a known
owner is simply left unlinked, exactly like any unmatched provider record.

Usage:
    python scripts/run_acquire_directory.py
    python scripts/run_acquire_directory.py --source bluesprig
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
REQUEST_DELAY_SEC = 0.2

_JSONLD_RE = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.S | re.I,
)
# Fallback locator for "... in <City>, <ST>" phrasing in the SEO title.
_CITY_STATE_RE = re.compile(r"\bin\s+([A-Za-z .'\-]+?),\s*([A-Z]{2})\b")
_LOCATION_TYPES = {"MedicalBusiness", "MedicalClinic", "LocalBusiness", "Physician"}


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


def parse_jsonld_location(
    content: bytes | str, *, fallback_name: str | None = None
) -> dict[str, Any] | None:
    """Extract one center's staging row from a directory page's JSON-LD.

    Pure function: reads the schema.org ``MedicalBusiness``/``LocalBusiness``
    node the site publishes and returns a staging dict, or None if the page
    carries no usable location node. Tests call this directly with fixtures.
    """
    text = content.decode("utf-8", "replace") if isinstance(content, bytes) else content
    for node in _iter_jsonld_nodes(text):
        node_type = node.get("@type")
        types = node_type if isinstance(node_type, list) else [node_type]
        if not (set(types) & _LOCATION_TYPES):
            continue
        addr = node.get("address")
        if isinstance(addr, list):
            addr = addr[0] if addr else None
        if not isinstance(addr, dict):
            continue
        name = html.unescape((node.get("name") or fallback_name or "").strip())
        if not name:
            continue
        state = (addr.get("addressRegion") or "").strip()[:2].upper() or None
        return {
            "raw_name": name,
            "address_line1": (addr.get("streetAddress") or "").strip() or None,
            "city": (addr.get("addressLocality") or "").strip() or None,
            "state": state,
            "zip": (str(addr.get("postalCode")).strip() or None)
            if addr.get("postalCode")
            else None,
            "npi": None,
        }
    return None


class BlueSprigDirectory:
    """Ingests the BlueSprig network's public center directory.

    BlueSprig (KKR-backed) publishes its centers as a WordPress custom post type
    exposed at ``/wp-json/wp/v2/center``. Each center's own page carries a
    schema.org ``MedicalBusiness`` JSON-LD block with its street address. The
    listing spans the KKR/BlueSprig family brands (BlueSprig, Trumpet Behavioral
    Health, Florida Autism Center); centers whose name brand-matches an owner the
    pipeline already tracks are linked, the rest are left unlinked.
    """

    key = "bluesprig"
    base = "https://www.bluesprigautism.com"
    enumerate_path = "/wp-json/wp/v2/center?per_page=100&page={page}"
    max_pages = 10

    def __init__(self, store: SnapshotStore | None = None) -> None:
        self._store = store or LocalFilesystemStore()

    def _center_pages(self, client: httpx.Client) -> list[dict[str, str]]:
        """Return [{url, seo_title}] for every center in the directory."""
        centers: list[dict[str, str]] = []
        for page in range(1, self.max_pages + 1):
            url = self.base + self.enumerate_path.format(page=page)
            resp = client.get(url, headers={"Accept": "application/json"})
            if resp.status_code == 400:
                break  # WP returns 400 past the last page
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break
            for item in batch:
                link = item.get("link")
                if not link:
                    continue
                seo = (item.get("yoast_head_json") or {}).get("title") or ""
                centers.append({"url": link, "seo_title": seo})
            total_pages = int(resp.headers.get("X-WP-TotalPages", "1") or "1")
            if page >= total_pages:
                break
            time.sleep(REQUEST_DELAY_SEC)
        return centers

    def run(self) -> dict[str, int]:
        """Fetch, snapshot, and stage every center in the directory.

        Idempotent per center via the ``(source_url, content_hash)`` guard, like
        the scrapers. Returns counts of staged / skipped / failed.
        """
        summary = {"seen": 0, "staged": 0, "skipped": 0, "failed": 0}
        with httpx.Client(
            timeout=30.0, follow_redirects=True, headers={"User-Agent": FUNDPRINT_UA}
        ) as client:
            centers = self._center_pages(client)
            summary["seen"] = len(centers)
            logger.info("%s directory: %d centers listed", self.key, len(centers))
            for center in centers:
                url = center["url"]
                try:
                    resp = client.get(url)
                    resp.raise_for_status()
                    content = resp.content
                except Exception:
                    logger.exception("directory fetch failed for %s", url)
                    summary["failed"] += 1
                    continue

                # Best-available location fields: prefer the page's JSON-LD; fall
                # back to the SEO title's "in <City>, <ST>" when a page omits it.
                fallback_name = html.unescape(
                    (center["seo_title"].split(" in ")[0] or "").strip()
                )
                row = parse_jsonld_location(content, fallback_name=fallback_name)
                if row is None:
                    row = self._row_from_seo(center)
                if row is None:
                    summary["skipped"] += 1
                    continue

                snapshot_id, content_hash = self._store.put(content)
                with db.transaction() as conn:
                    if _find_existing_source_record(conn, url, content_hash):
                        summary["skipped"] += 1
                        continue
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
                    summary["staged"] += 1
                time.sleep(REQUEST_DELAY_SEC)

        logger.info("%s directory ingest complete: %s", self.key, summary)
        return summary

    @staticmethod
    def _row_from_seo(center: dict[str, str]) -> dict[str, Any] | None:
        """Fallback staging row from the SEO title alone (no street address)."""
        seo = center["seo_title"]
        name = html.unescape((seo.split(" in ")[0] or "").strip())
        m = _CITY_STATE_RE.search(seo)
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


_SOURCES: dict[str, type[BlueSprigDirectory]] = {
    BlueSprigDirectory.key: BlueSprigDirectory,
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
