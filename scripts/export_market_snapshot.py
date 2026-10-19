"""Create a verified, point-in-time market-data export on the server.

Run this inside the maintenance container because only that container receives
the read-only Ncloud Storage credentials.  The export contains active Parquet
objects plus a filtered JSONL catalogue; it never copies application secrets or
order state.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import tarfile
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from vibetrading.settings import get_settings
from vibetrading.sqlite_store import RuntimeStore
from vibetrading.storage_backend import archive_backend


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def safe_partition(value: Any) -> str:
    text = str(value or "unknown").strip()
    return "".join(character if character.isalnum() or character in "._-" else "_" for character in text)


def copy_verified(source: Path, destination: Path, expected_size: int, expected_sha256: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".copying")
    with source.open("rb") as source_handle, temporary.open("wb") as destination_handle:
        shutil.copyfileobj(source_handle, destination_handle, length=1024 * 1024)
        destination_handle.flush()
        os.fsync(destination_handle.fileno())
    if temporary.stat().st_size != expected_size or sha256_file(temporary) != expected_sha256:
        temporary.unlink(missing_ok=True)
        raise RuntimeError(f"local file verification failed: {source}")
    os.replace(temporary, destination)
    fsync_directory(destination.parent)


def load_active_manifest(database: Path) -> list[dict[str, Any]]:
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        return [
            dict(row)
            for row in connection.execute(
                """
                SELECT *
                FROM parquet_manifest
                WHERE lifecycle_state='active'
                ORDER BY event_type,trading_date,provider,venue,min_ts_ns,file_id
                """
            )
        ]
    finally:
        connection.close()


def expected_hash(row: dict[str, Any]) -> str:
    for key in ("file_sha256", "content_sha256", "checksum"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    raise RuntimeError(f"manifest has no file checksum: file_id={row.get('file_id')}")


def relative_parquet_path(row: dict[str, Any]) -> Path:
    return (
        Path("market_data")
        / f"event_type={safe_partition(row.get('event_type'))}"
        / f"trading_date={safe_partition(row.get('trading_date'))}"
        / f"provider={safe_partition(row.get('provider'))}"
        / f"venue={safe_partition(row.get('venue'))}"
        / f"{safe_partition(row.get('file_id'))}.parquet"
    )


def write_json_atomic(path: Path, payload: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    os.replace(temporary, path)
    fsync_directory(path.parent)


def create_tar_gz(source_directory: Path, archive_path: Path) -> dict[str, Any]:
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = archive_path.with_suffix(archive_path.suffix + ".tmp")
    with tarfile.open(temporary, mode="w:gz", compresslevel=1) as archive:
        archive.add(source_directory, arcname=source_directory.name, recursive=True)
    with temporary.open("rb+") as handle:
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, archive_path)
    fsync_directory(archive_path.parent)
    return {
        "path": str(archive_path),
        "bytes": archive_path.stat().st_size,
        "sha256": sha256_file(archive_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Export active market Parquet from local disk and Ncloud")
    parser.add_argument("--output-root", type=Path, default=Path("/data/exports"))
    parser.add_argument("--name", default="")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--no-archive", action="store_true")
    args = parser.parse_args()

    settings = get_settings()
    store = RuntimeStore(settings)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    export_name = args.name.strip() or f"vibetrading-market-{timestamp}"
    final_directory = args.output_root / export_name
    building_directory = args.output_root / f".{export_name}.building"
    archive_path = args.output_root / f"{export_name}.tar.gz"
    if final_directory.exists() or building_directory.exists() or archive_path.exists():
        raise FileExistsError(f"export target already exists: {export_name}")
    building_directory.mkdir(parents=True)

    # SQLite backup gives the export a stable manifest cutoff while collectors,
    # compaction and archival continue operating.
    with tempfile.TemporaryDirectory(prefix="vibetrading-export-catalog-") as temporary_directory:
        catalogue_database = Path(temporary_directory) / "catalog.sqlite3"
        store.backup_to(catalogue_database)
        manifest_rows = load_active_manifest(catalogue_database)

    backend = archive_backend(settings)
    started_ns = time.time_ns()
    results: list[dict[str, Any]] = []

    def export_one(row: dict[str, Any]) -> dict[str, Any]:
        relative_path = relative_parquet_path(row)
        destination = building_directory / relative_path
        expected_size = int(row.get("byte_size") or 0)
        checksum = expected_hash(row)
        source = Path(str(row.get("path") or ""))
        source_kind = "local"
        try:
            if not source.is_file():
                raise FileNotFoundError(source)
            copy_verified(source, destination, expected_size, checksum)
        except (FileNotFoundError, RuntimeError, OSError):
            remote_key = str(row.get("remote_key") or "")
            if not remote_key or row.get("upload_status") != "remote_verified":
                raise RuntimeError(f"no verified source for active file_id={row.get('file_id')}")
            source_kind = "ncloud"
            destination.parent.mkdir(parents=True, exist_ok=True)
            backend.download_file(remote_key, destination)
            if destination.stat().st_size != expected_size or sha256_file(destination) != checksum:
                destination.unlink(missing_ok=True)
                raise RuntimeError(f"remote file verification failed: file_id={row.get('file_id')}")
        exported = {
            key: row.get(key)
            for key in (
                "file_id", "event_type", "trading_date", "provider", "venue", "rows",
                "min_ts_ns", "max_ts_ns", "byte_size", "remote_key", "upload_status",
                "created_ts_ns",
            )
        }
        exported.update({"relative_path": relative_path.as_posix(), "sha256": checksum, "source": source_kind})
        return exported

    try:
        with ThreadPoolExecutor(max_workers=max(1, min(8, args.workers))) as executor:
            futures = [executor.submit(export_one, row) for row in manifest_rows]
            for index, future in enumerate(as_completed(futures), start=1):
                results.append(future.result())
                if index % 100 == 0 or index == len(futures):
                    print(json.dumps({"exported": index, "total": len(futures)}, ensure_ascii=False), flush=True)

        results.sort(key=lambda row: str(row["relative_path"]))
        manifest_path = building_directory / "manifest.jsonl"
        with manifest_path.open("w", encoding="utf-8", newline="\n") as handle:
            for row in results:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

        metadata = {
            "format_version": 1,
            "export_name": export_name,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "snapshot_started_ns": started_ns,
            "collection_generation": store.collection_generation(),
            "files": len(results),
            "rows": sum(int(row.get("rows") or 0) for row in results),
            "bytes": sum(int(row.get("byte_size") or 0) for row in results),
            "source_files": {
                "local": sum(row["source"] == "local" for row in results),
                "ncloud": sum(row["source"] == "ncloud" for row in results),
            },
            "event_types": sorted({str(row.get("event_type")) for row in results}),
            "providers": sorted({str(row.get("provider")) for row in results}),
            "notes": [
                "Only manifest-active files are included; superseded compaction inputs are excluded.",
                "Every file was checked against the manifest byte size and SHA-256.",
                "The snapshot cutoff is the copied SQLite manifest, so later arrivals are not included.",
            ],
        }
        write_json_atomic(building_directory / "export_metadata.json", metadata)
        os.replace(building_directory, final_directory)
        fsync_directory(args.output_root)
        if not args.no_archive:
            metadata["archive"] = create_tar_gz(final_directory, archive_path)
            write_json_atomic(final_directory / "export_metadata.json", metadata)
            checksum_path = Path(str(archive_path) + ".sha256")
            checksum_path.write_text(
                f"{metadata['archive']['sha256']}  {archive_path.name}\n",
                encoding="ascii",
            )
        print(json.dumps(metadata, ensure_ascii=False, indent=2), flush=True)
        return 0
    except Exception:
        (building_directory / "EXPORT_FAILED").write_text(datetime.now(timezone.utc).isoformat(), encoding="utf-8")
        raise


if __name__ == "__main__":
    raise SystemExit(main())
