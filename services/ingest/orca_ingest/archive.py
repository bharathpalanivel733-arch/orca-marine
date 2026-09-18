"""Raw-payload archiving (PLAN.md Phase 1.10, feeding Phase 6.4 replay).

Whatever an upstream returned is stored byte-for-byte and content-addressed by SHA-256,
before any parsing happens. Two reasons:

* **Deterministic replay.** A decision is re-run from the archived bytes, not by
  re-querying a source whose forecast has since been superseded. Without this, "replay"
  would silently produce a different answer.
* **Disputes are settleable.** When a recommendation is questioned, the exact advisory
  that produced it can be produced, with its retrieval time.

Two backends share one interface: the filesystem (tests, offline work) and S3/MinIO
(the dev stack and production). Both are content-addressed, so re-archiving identical
bytes is idempotent and costs no extra storage.
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Any

from orca_schemas import ArchiveRef

CONTENT_TYPE_EXTENSIONS = {
    "application/json": "json",
    "text/json": "json",
    "text/plain": "txt",
    "text/html": "html",
    "text/csv": "csv",
    "application/xml": "xml",
    "text/xml": "xml",
    "application/x-netcdf": "nc",
    "application/octet-stream": "bin",
}


def _extension_for(content_type: str) -> str:
    return CONTENT_TYPE_EXTENSIONS.get(content_type.split(";")[0].strip().lower(), "bin")


def _archive_key(*, source_id: str, retrieved_at: datetime, digest: str, extension: str) -> str:
    """Content-addressed key, partitioned by source and date for cheap lifecycle rules."""
    return f"raw/{source_id}/{retrieved_at:%Y/%m/%d}/{digest}.{extension}"


class RawPayloadArchive(ABC):
    """Stores upstream payloads exactly as received."""

    @abstractmethod
    def store(
        self,
        payload: bytes,
        *,
        source_id: str,
        content_type: str,
        retrieved_at: datetime,
        dataset_id: str | None = None,
    ) -> ArchiveRef:
        """Archive one payload and return a pointer to it."""

    @abstractmethod
    def read(self, ref: ArchiveRef) -> bytes:
        """Read a previously archived payload back, for replay."""


class FilesystemArchive(RawPayloadArchive):
    """Archive under a local directory. Used by tests and offline development."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def store(
        self,
        payload: bytes,
        *,
        source_id: str,
        content_type: str,
        retrieved_at: datetime,
        dataset_id: str | None = None,
    ) -> ArchiveRef:
        digest = hashlib.sha256(payload).hexdigest()
        key = _archive_key(
            source_id=source_id,
            retrieved_at=retrieved_at,
            digest=digest,
            extension=_extension_for(content_type),
        )
        path = self._root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():  # content-addressed: identical bytes need no rewrite
            path.write_bytes(payload)
        return ArchiveRef(
            uri=path.as_uri(),
            sha256=digest,
            size_bytes=len(payload),
            content_type=content_type,
            source_id=source_id,
            dataset_id=dataset_id,
            retrieved_at=retrieved_at,
        )

    def read(self, ref: ArchiveRef) -> bytes:
        from urllib.parse import unquote, urlparse

        path = Path(unquote(urlparse(ref.uri).path).lstrip("/"))
        return path.read_bytes()


class S3Archive(RawPayloadArchive):
    """Archive into S3 or MinIO (the ``orca-cache`` bucket in the dev stack)."""

    def __init__(self, client: Any, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    def store(
        self,
        payload: bytes,
        *,
        source_id: str,
        content_type: str,
        retrieved_at: datetime,
        dataset_id: str | None = None,
    ) -> ArchiveRef:
        digest = hashlib.sha256(payload).hexdigest()
        key = _archive_key(
            source_id=source_id,
            retrieved_at=retrieved_at,
            digest=digest,
            extension=_extension_for(content_type),
        )
        self._client.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=payload,
            ContentType=content_type,
            Metadata={"source-id": source_id, "dataset-id": dataset_id or ""},
        )
        return ArchiveRef(
            uri=f"s3://{self._bucket}/{key}",
            sha256=digest,
            size_bytes=len(payload),
            content_type=content_type,
            source_id=source_id,
            dataset_id=dataset_id,
            retrieved_at=retrieved_at,
        )

    def read(self, ref: ArchiveRef) -> bytes:
        key = ref.uri.split(f"s3://{self._bucket}/", 1)[-1]
        response = self._client.get_object(Bucket=self._bucket, Key=key)
        return bytes(response["Body"].read())


class NullArchive(RawPayloadArchive):
    """Archives nothing. For unit tests that are not exercising archiving."""

    def store(
        self,
        payload: bytes,
        *,
        source_id: str,
        content_type: str,
        retrieved_at: datetime,
        dataset_id: str | None = None,
    ) -> ArchiveRef:
        return ArchiveRef(
            uri="null://discarded",
            sha256=hashlib.sha256(payload).hexdigest(),
            size_bytes=len(payload),
            content_type=content_type,
            source_id=source_id,
            dataset_id=dataset_id,
            retrieved_at=retrieved_at,
        )

    def read(self, ref: ArchiveRef) -> bytes:
        msg = "NullArchive stores nothing; replay requires a real archive backend"
        raise NotImplementedError(msg)
