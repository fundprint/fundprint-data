"""Raw snapshot storage. Two backends: local filesystem and S3.

Snapshots are write-once blobs keyed by sha256 of their content.
Re-fetching the same bytes returns the same snapshot_id without re-writing.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Protocol

from fundprint.config import settings


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class SnapshotStore(Protocol):
    """Interface for raw blob storage. Both backends must satisfy this."""

    def put(self, content: bytes, suffix: str = ".html") -> tuple[str, str]:
        """Store content and return (snapshot_id, content_hash).

        snapshot_id is the object-storage key; content_hash is the sha256 hex.
        If the same hash already exists the backend is allowed to skip the write.
        """
        ...

    def get(self, snapshot_id: str) -> bytes:
        """Retrieve previously stored content by snapshot_id."""
        ...


class LocalFilesystemStore:
    """Stores blobs under SNAPSHOT_STORE_PATH/{first2}/{sha256}{suffix}.

    Sharding by the first two hex chars keeps directory listings manageable
    once the snapshot count grows past a few thousand.
    """

    def __init__(self, base_path: str | None = None) -> None:
        self._base = Path(base_path or settings.snapshot_store_path)

    def put(self, content: bytes, suffix: str = ".html") -> tuple[str, str]:
        """Write content to disk. Returns (snapshot_id, content_hash)."""
        h = _sha256_hex(content)
        # Shard to avoid flat directories with 100k+ entries
        shard = h[:2]
        dest = self._base / shard / f"{h}{suffix}"
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(content)
        # snapshot_id is the relative path under base so callers can reconstruct it
        snapshot_id = str(Path(shard) / f"{h}{suffix}")
        return snapshot_id, h

    def get(self, snapshot_id: str) -> bytes:
        """Read a snapshot blob by its snapshot_id path."""
        return (self._base / snapshot_id).read_bytes()


class S3Store:
    """Stores blobs in an S3-compatible bucket.

    Accepts a pre-configured boto3 client so callers can inject mocks or
    LocalStack clients in tests without touching real AWS.
    """

    def __init__(self, client, bucket: str, prefix: str = "snapshots/") -> None:
        self._client = client
        self._bucket = bucket
        self._prefix = prefix

    def put(self, content: bytes, suffix: str = ".html") -> tuple[str, str]:
        """Upload content to S3. Returns (snapshot_id, content_hash).

        Uses sha256 as the key so re-uploading identical bytes is a no-op
        after the first upload (callers can check content_hash first).
        """
        h = _sha256_hex(content)
        key = f"{self._prefix}{h[:2]}/{h}{suffix}"
        # HEAD first to skip redundant PUTs on re-ingestion
        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
        except Exception:
            self._client.put_object(Bucket=self._bucket, Key=key, Body=content)
        return key, h

    def get(self, snapshot_id: str) -> bytes:
        """Download a snapshot blob by its S3 key."""
        response = self._client.get_object(Bucket=self._bucket, Key=snapshot_id)
        return response["Body"].read()
