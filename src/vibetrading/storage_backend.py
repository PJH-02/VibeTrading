from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Protocol

from .settings import RuntimeSettings


@dataclass(frozen=True)
class ArchiveObject:
    key: str
    size_bytes: int
    sha256: str


class ArchiveBackend(Protocol):
    def put_file(self, source: Path, key: str, *, sha256: str) -> ArchiveObject: ...
    def put_bytes(self, payload: bytes, key: str, *, sha256: str) -> ArchiveObject: ...
    def verify(self, key: str, *, size_bytes: int, sha256: str) -> ArchiveObject: ...
    def download_file(self, key: str, destination: Path) -> None: ...


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


class LocalStorageBackend:
    """Filesystem backend used by tests and local development only."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def _target(self, key: str) -> Path:
        # Deterministic short paths also keep Windows development tests below
        # the legacy MAX_PATH limit; the object key remains the public identity.
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self.root / digest[:2] / digest

    def put_file(self, source: Path, key: str, *, sha256: str) -> ArchiveObject:
        target = self._target(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".uploading")
        with source.open("rb") as src, temporary.open("wb") as dst:
            self._copy(src, dst)
            dst.flush()
            os.fsync(dst.fileno())
        os.replace(temporary, target)
        return self.verify(key, size_bytes=source.stat().st_size, sha256=sha256)

    def put_bytes(self, payload: bytes, key: str, *, sha256: str) -> ArchiveObject:
        target = self._target(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".uploading")
        temporary.write_bytes(payload)
        os.replace(temporary, target)
        return self.verify(key, size_bytes=len(payload), sha256=sha256)

    def verify(self, key: str, *, size_bytes: int, sha256: str) -> ArchiveObject:
        target = self._target(key)
        if not target.exists() or target.stat().st_size != size_bytes:
            raise RuntimeError(f"archive verification size mismatch key={key}")
        actual = sha256_file(target)
        if actual != sha256:
            raise RuntimeError(f"archive verification checksum mismatch key={key}")
        return ArchiveObject(key=key, size_bytes=size_bytes, sha256=actual)

    def download_file(self, key: str, destination: Path) -> None:
        source = self._target(key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(destination.name + ".downloading")
        try:
            with source.open("rb") as src, temporary.open("wb") as dst:
                self._copy(src, dst)
                dst.flush()
                os.fsync(dst.fileno())
            os.replace(temporary, destination)
        finally:
            # A failed copy owns only this staging path. In particular, keep
            # any previously published destination intact for its readers.
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _copy(source: BinaryIO, destination: BinaryIO) -> None:
        while chunk := source.read(1024 * 1024):
            destination.write(chunk)


class NcloudStorageBackend:
    """S3-compatible Ncloud Storage backend with content verification."""

    def __init__(self, settings: RuntimeSettings) -> None:
        archive = settings.archive
        if not archive.enabled:
            raise RuntimeError("Ncloud archive is not fully configured")
        self.bucket = archive.bucket
        self.endpoint = archive.endpoint
        self.region = archive.region
        try:
            import boto3
            from boto3.s3.transfer import TransferConfig
            from botocore.config import Config
        except ImportError as exc:  # pragma: no cover - deployment dependency
            raise RuntimeError("boto3 is required for Ncloud Storage archival") from exc
        self._client = boto3.client(
            "s3",
            endpoint_url=self.endpoint,
            region_name=self.region,
            aws_access_key_id=archive.access_key,
            aws_secret_access_key=archive.secret_key,
            config=Config(retries={"max_attempts": 5, "mode": "standard"}, connect_timeout=10, read_timeout=120),
        )
        # A 1 GiB SQLite backup otherwise starts ten parallel multipart
        # requests and competes with NATS and Parquet writes on this small VM.
        self._upload_config = TransferConfig(use_threads=False)

    def put_file(self, source: Path, key: str, *, sha256: str) -> ArchiveObject:
        size = source.stat().st_size
        self._client.upload_file(
            str(source),
            self.bucket,
            key,
            ExtraArgs={"Metadata": {"sha256": sha256}},
            Config=self._upload_config,
        )
        return self.verify(key, size_bytes=size, sha256=sha256)

    def put_bytes(self, payload: bytes, key: str, *, sha256: str) -> ArchiveObject:
        self._client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=payload,
            Metadata={"sha256": sha256},
        )
        return self.verify(key, size_bytes=len(payload), sha256=sha256)

    def verify(self, key: str, *, size_bytes: int, sha256: str) -> ArchiveObject:
        head = self._client.head_object(Bucket=self.bucket, Key=key)
        if int(head.get("ContentLength") or -1) != int(size_bytes):
            raise RuntimeError(f"archive verification size mismatch key={key}")
        metadata = {str(name).lower(): str(value) for name, value in (head.get("Metadata") or {}).items()}
        if metadata.get("sha256") not in {None, sha256}:
            raise RuntimeError(f"archive verification metadata mismatch key={key}")
        response = self._client.get_object(Bucket=self.bucket, Key=key)
        body = response["Body"]
        digest = hashlib.sha256()
        received = 0
        try:
            while chunk := body.read(1024 * 1024):
                digest.update(chunk)
                received += len(chunk)
        finally:
            body.close()
        actual = digest.hexdigest()
        if received != size_bytes or actual != sha256:
            raise RuntimeError(f"archive verification checksum mismatch key={key}")
        return ArchiveObject(key=key, size_bytes=size_bytes, sha256=actual)

    def download_file(self, key: str, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(destination.name + ".downloading")
        try:
            self._client.download_file(self.bucket, key, str(temporary))
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)

    def close(self) -> None:
        self._client.close()


def archive_backend(settings: RuntimeSettings) -> ArchiveBackend:
    backend = settings.archive.backend.strip().lower()
    if backend == "local":
        return LocalStorageBackend(settings.data_root / "archive_test")
    if backend != "ncloud_storage":
        raise RuntimeError(f"Unsupported archive backend: {settings.archive.backend or '<unset>'}")
    return NcloudStorageBackend(settings)
