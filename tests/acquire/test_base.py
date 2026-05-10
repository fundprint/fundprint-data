"""Tests for the Scraper base class: idempotency, transaction behavior, retry."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, call, patch

import pytest

from fundprint.acquire.base import Scraper, _find_existing_source_record, _insert_source_record


class _FakeScraper(Scraper):
    """Minimal concrete scraper used to exercise base-class behavior."""

    source_family = "test_family"
    module_version = "0.0.1"

    def __init__(self, store=None, content=b"fake-content", source_url="https://example.com"):
        super().__init__(store)
        self._content = content
        self._source_url = source_url
        self.parse_calls: list[bytes] = []
        self.staging_calls: list[tuple] = []

    def fetch(self) -> tuple[bytes, str]:
        return self._content, self._source_url

    def parse(self, content: bytes) -> list[dict[str, Any]]:
        self.parse_calls.append(content)
        return [{"field": "value"}]

    def _write_staging(self, rows, source_record_id, conn):
        self.staging_calls.append((rows, source_record_id))


class _FakeStore:
    def put(self, content: bytes, suffix: str = ".html") -> tuple[str, str]:
        import hashlib
        h = hashlib.sha256(content).hexdigest()
        return f"fake/{h[:2]}/{h}.html", h

    def get(self, snapshot_id: str) -> bytes:
        raise NotImplementedError


@pytest.fixture
def fake_store():
    return _FakeStore()


@pytest.fixture
def scraper(fake_store):
    return _FakeScraper(store=fake_store)


def _make_mock_conn(existing_id=None):
    """Build a mock psycopg connection with controllable query results."""
    conn = MagicMock()
    # First execute call is _find_existing_source_record
    find_cursor = MagicMock()
    find_cursor.fetchone.return_value = (existing_id,) if existing_id else None
    # Second execute call is _insert_source_record
    insert_cursor = MagicMock()
    insert_cursor.fetchone.return_value = ("new-source-record-id",)
    # Further execute calls are for _write_staging
    staging_cursor = MagicMock()
    staging_cursor.fetchone.return_value = None

    conn.execute.side_effect = [find_cursor, insert_cursor, staging_cursor]
    return conn


class TestScraperRun:
    def test_happy_path_writes_staging(self, scraper):
        """A clean first run should parse and write staging rows."""
        mock_conn = _make_mock_conn(existing_id=None)
        with patch("fundprint.acquire.base.db.transaction") as mock_tx:
            mock_tx.return_value.__enter__ = MagicMock(return_value=mock_conn)
            mock_tx.return_value.__exit__ = MagicMock(return_value=False)
            scraper.run()

        assert len(scraper.parse_calls) == 1
        assert len(scraper.staging_calls) == 1

    def test_idempotent_on_duplicate_hash(self, scraper):
        """Re-running with identical content should skip parse and staging."""
        mock_conn = _make_mock_conn(existing_id="existing-uuid")
        with patch("fundprint.acquire.base.db.transaction") as mock_tx:
            mock_tx.return_value.__enter__ = MagicMock(return_value=mock_conn)
            mock_tx.return_value.__exit__ = MagicMock(return_value=False)
            scraper.run()

        # parse and staging should not be called when content already exists
        assert len(scraper.parse_calls) == 0
        assert len(scraper.staging_calls) == 0

    def test_transaction_rolls_back_on_parse_error(self, fake_store):
        """An exception in parse() should propagate so the transaction rolls back."""
        class _BrokenScraper(_FakeScraper):
            def parse(self, content):
                raise ValueError("parser exploded")
            def _write_staging(self, rows, source_record_id, conn):
                pass

        scraper = _BrokenScraper(store=fake_store)
        mock_conn = _make_mock_conn(existing_id=None)
        with patch("fundprint.acquire.base.db.transaction") as mock_tx:
            mock_tx.return_value.__enter__ = MagicMock(return_value=mock_conn)
            mock_tx.return_value.__exit__ = MagicMock(return_value=False)
            with pytest.raises(ValueError, match="parser exploded"):
                scraper.run()


class TestHelperFunctions:
    def test_find_existing_returns_none_when_absent(self):
        conn = MagicMock()
        cursor = MagicMock()
        cursor.fetchone.return_value = None
        conn.execute.return_value = cursor
        result = _find_existing_source_record(conn, "https://x.com", "abc123")
        assert result is None

    def test_find_existing_returns_id_string(self):
        conn = MagicMock()
        cursor = MagicMock()
        cursor.fetchone.return_value = ("some-uuid",)
        conn.execute.return_value = cursor
        result = _find_existing_source_record(conn, "https://x.com", "abc123")
        assert result == "some-uuid"

    def test_insert_source_record_returns_id(self):
        from datetime import datetime, timezone
        conn = MagicMock()
        cursor = MagicMock()
        cursor.fetchone.return_value = ("new-uuid",)
        conn.execute.return_value = cursor
        result = _insert_source_record(
            conn,
            source_url="https://x.com",
            snapshot_id="sn/abc.html",
            source_type="test",
            fetched_at=datetime.now(timezone.utc),
            content_hash="abc123",
            module_version="0.1.0",
        )
        assert result == "new-uuid"


class TestRetryLogic:
    def test_http_5xx_triggers_retry(self, fake_store):
        """5xx errors should be caught by the retry predicate."""
        import httpx
        from fundprint.acquire.base import _is_retryable

        resp = MagicMock()
        resp.status_code = 503
        exc = httpx.HTTPStatusError("server error", request=MagicMock(), response=resp)
        assert _is_retryable(exc) is True

    def test_http_4xx_not_retryable(self, fake_store):
        """404 is a permanent error; retrying won't fix it."""
        import httpx
        from fundprint.acquire.base import _is_retryable

        resp = MagicMock()
        resp.status_code = 404
        exc = httpx.HTTPStatusError("not found", request=MagicMock(), response=resp)
        assert _is_retryable(exc) is False

    def test_429_is_retryable(self):
        import httpx
        from fundprint.acquire.base import _is_retryable

        resp = MagicMock()
        resp.status_code = 429
        exc = httpx.HTTPStatusError("rate limited", request=MagicMock(), response=resp)
        assert _is_retryable(exc) is True

    def test_network_error_is_retryable(self):
        import httpx
        from fundprint.acquire.base import _is_retryable

        exc = httpx.ConnectError("connection refused")
        assert _is_retryable(exc) is True
