"""Abstract Scraper base class. All acquire modules inherit from this."""

from __future__ import annotations

import abc
import logging
from datetime import UTC, datetime
from typing import Any

import tenacity

from fundprint import db
from fundprint.storage import LocalFilesystemStore, SnapshotStore

logger = logging.getLogger(__name__)

# Transient HTTP errors worth retrying. Permanent 4xx errors (except 429) are not.
_RETRYABLE_STATUS = frozenset([429, 500, 502, 503, 504])


def _is_retryable(exc: BaseException) -> bool:
    """Return True for transient network and rate-limit errors."""
    import httpx

    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _RETRYABLE_STATUS
    # Any network-level error (timeouts, DNS, connection refused) is transient
    if isinstance(exc, (httpx.RequestError,)):
        return True
    return False


class Scraper(abc.ABC):
    """Base for all ingestion modules.

    Subclasses must set class-level source_family and module_version,
    and implement fetch() and parse() as pure transformations.
    """

    source_family: str  # must be a non-empty string on every subclass
    module_version: str  # bump when parsing logic or extracted fields change

    def __init__(self, store: SnapshotStore | None = None) -> None:
        self._store = store or LocalFilesystemStore()

    # ------------------------------------------------------------------
    # Abstract interface
    # ------------------------------------------------------------------

    @abc.abstractmethod
    def fetch(self) -> tuple[bytes, str]:
        """Retrieve the raw source document. Returns (content_bytes, source_url).

        Must raise NotImplementedError for browser-dependent paths that cannot
        run headlessly; document the live URL in the subclass docstring.
        """

    @abc.abstractmethod
    def parse(self, content: bytes) -> list[dict[str, Any]]:
        """Parse raw bytes into a list of dicts ready for the staging table.

        Pure function - no side effects. Tests call this directly with fixtures.
        """

    @abc.abstractmethod
    def _write_staging(self, rows: list[dict[str, Any]], source_record_id: str, conn: Any) -> None:
        """Write parsed rows to the appropriate staging table inside conn."""

    # ------------------------------------------------------------------
    # Orchestration
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Fetch, snapshot, source_record, parse, and stage - one transaction.

        Idempotent: if the same content_hash exists for this source_url the
        snapshot is marked superseded and no staging rows are duplicated.
        """
        content, source_url = self._fetch_with_retry()

        snapshot_id, content_hash = self._store.put(content)
        fetched_at = datetime.now(UTC)

        with db.transaction() as conn:
            existing = _find_existing_source_record(conn, source_url, content_hash)
            if existing is not None:
                # Identical snapshot already staged - nothing new to do
                logger.info(
                    "content_hash %s already staged for %s, skipping",
                    content_hash,
                    source_url,
                )
                return

            source_record_id = _insert_source_record(
                conn,
                source_url=source_url,
                snapshot_id=snapshot_id,
                source_type=self.source_family,
                fetched_at=fetched_at,
                content_hash=content_hash,
                module_version=self.module_version,
            )

            rows = self.parse(content)
            if not rows:
                logger.warning("parser returned zero rows for %s", source_url)
            self._write_staging(rows, source_record_id, conn)

        logger.info(
            "staged %d rows from %s (snapshot %s)", len(rows), source_url, snapshot_id
        )

    @tenacity.retry(
        retry=tenacity.retry_if_exception(_is_retryable),
        wait=tenacity.wait_exponential_jitter(initial=2, max=60),
        stop=tenacity.stop_after_attempt(5),
        before_sleep=tenacity.before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    def _fetch_with_retry(self) -> tuple[bytes, str]:
        return self.fetch()


# ------------------------------------------------------------------
# DB helpers (module-private; not part of the public Scraper API)
# ------------------------------------------------------------------


def _find_existing_source_record(conn: Any, source_url: str, content_hash: str) -> str | None:
    """Return the existing source_record id if this hash is already stored."""
    row = conn.execute(
        "SELECT id FROM source_record WHERE source_url = %s AND content_hash = %s",
        (source_url, content_hash),
    ).fetchone()
    return str(row[0]) if row else None


def _insert_source_record(
    conn: Any,
    *,
    source_url: str,
    snapshot_id: str,
    source_type: str,
    fetched_at: datetime,
    content_hash: str,
    module_version: str,
) -> str:
    """Insert a source_record row and return its id as a string."""
    row = conn.execute(
        """
        INSERT INTO source_record
            (source_url, snapshot_id, source_type, fetched_at, content_hash)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING id
        """,
        (source_url, snapshot_id, source_type, fetched_at, content_hash),
    ).fetchone()
    return str(row[0])
