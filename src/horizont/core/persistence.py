"""Non-destructive persistent SQLite lifecycle for a *new* HORIZONT datastore.

Offline only: no Docker, network, collector, service restart or real data access.
Backups use the SQLite online backup API to include committed WAL transactions.
Never replace an existing DB or a symlink. Restore publishes only a NEW file.
"""

from __future__ import annotations

import argparse
from contextlib import closing
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import sqlite3
import stat
import tempfile

from .db import SCHEMA_VERSION, connect, migrate
from .queries import open_readonly


@dataclass(frozen=True)
class StorageResult:
    """File digest and byte length for audit without exposing any record data."""

    sha256: str
    bytes_written: int
    schema_version: int


def _existing_file(value: str | Path) -> Path:
    path = Path(value).expanduser().absolute()
    if path.is_symlink() or not path.is_file():
        raise ValueError("input must be an existing non-symlink regular SQLite file")
    return path


def _new_target(value: str | Path) -> Path:
    path = Path(value).expanduser().absolute()
    if not path.name or not path.parent.is_dir() or path.parent.is_symlink():
        raise ValueError("destination parent must exist and be a regular directory")
    if os.path.lexists(path):  # catches dangling symlinks, unlike Path.exists()
        raise FileExistsError("refusing to overwrite an existing path or symlink")
    return path


def _temp_stage(parent: Path) -> Path:
    descriptor, name = tempfile.mkstemp(prefix=".horizont-stage-", suffix=".sqlite", dir=parent)
    try:
        os.fchmod(descriptor, stat.S_IRUSR | stat.S_IWUSR)
    finally:
        os.close(descriptor)
    return Path(name)


def _verify(connection: sqlite3.Connection) -> None:
    """Fail closed on incomplete schemas, corrupted pages or broken provenance FK."""
    result = connection.execute("PRAGMA quick_check").fetchone()
    if result is None or result[0] != "ok":
        raise ValueError("SQLite quick_check failed")
    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise ValueError("SQLite foreign_key_check failed")
    versions = [int(row[0]) for row in connection.execute(
        "SELECT version FROM schema_migrations ORDER BY version"
    )]
    if versions != list(range(1, SCHEMA_VERSION + 1)):
        raise ValueError("SQLite schema migrations do not match this release")
    for table in ("sources", "events", "event_sources", "ingest_runs"):
        connection.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone()


def _sealed_stage(stage: Path) -> StorageResult:
    """Verify and close all SQLite sidecars before publishing the single file."""
    with closing(sqlite3.connect(str(stage), timeout=5)) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        # The SQLite backup API may propagate WAL journal mode from a live DB.
        # Convert ONLY the staged private copy to self-contained DELETE journal.
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        result = conn.execute("PRAGMA journal_mode=DELETE").fetchone()[0]
        if result.lower() != "delete":
            raise ValueError("unable to make portable self-contained backup")
        _verify(conn)
    for suffix in ("-wal", "-shm"):
        if os.path.lexists(str(stage) + suffix):
            raise ValueError("backup contains active SQLite journal sidecar")
    digest = hashlib.sha256()
    length = 0
    with stage.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
            length += len(chunk)
    with stage.open("rb") as source:
        os.fsync(source.fileno())
    return StorageResult(digest.hexdigest(), length, SCHEMA_VERSION)


def _publish_without_replace(stage: Path, destination: Path) -> None:
    """Atomic no-clobber: hard-link a completed snapshot, never os.replace()."""
    os.link(stage, destination)  # EEXIST on existing DB or dangling symlink
    # Best-effort durability of the new directory entry; some filesystems
    # reject directory fsync. File bytes were fsynced before publication.
    try:
        descriptor = os.open(str(destination.parent), os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError:
        pass


def _cleanup_stage(stage: Path) -> None:
    for suffix in ("", "-wal", "-shm"):
        try:
            Path(str(stage) + suffix).unlink(missing_ok=True)
        except OSError:
            pass


def initialize_new(destination: str | Path) -> StorageResult:
    """Migrate an empty private stage then publish a NEW DB without clobbering."""
    target = _new_target(destination)
    stage = _temp_stage(target.parent)
    try:
        with closing(connect(stage)) as conn:
            migrate(conn)
        result = _sealed_stage(stage)
        _publish_without_replace(stage, target)
        return result
    finally:
        _cleanup_stage(stage)


def _backup_into_new(source: str | Path, destination: str | Path) -> StorageResult:
    source_path = _existing_file(source)
    target = _new_target(destination)
    if source_path == target:
        raise ValueError("source and destination must differ")
    stage = _temp_stage(target.parent)
    try:
        with closing(open_readonly(source_path)) as reader:
            with closing(sqlite3.connect(str(stage), timeout=5)) as writer:
                reader.backup(writer, pages=128, sleep=0.05)
                writer.commit()
        result = _sealed_stage(stage)
        _publish_without_replace(stage, target)
        return result
    finally:
        _cleanup_stage(stage)


def backup_live(source: str | Path, destination: str | Path) -> StorageResult:
    """Snapshot a live WAL database into a NEW consistent, verified file."""
    return _backup_into_new(source, destination)


def restore_to_new(backup: str | Path, destination: str | Path) -> StorageResult:
    """Verify a backed-up SQLite file and restore only to a NEW destination.

    Never swap a live database path; the operator must stop the writer before
    separately choosing any restored DB. Leaves original DB intact for rollback.
    """
    return _backup_into_new(backup, destination)


def verify_existing(source: str | Path) -> StorageResult:
    """Read-only integrity/provenance verification, without migration or writes."""
    path = _existing_file(source)
    with closing(open_readonly(path)) as reader:
        _verify(reader)
    digest = hashlib.sha256()
    with path.open("rb") as reader:
        for chunk in iter(lambda: reader.read(1024 * 1024), b""):
            digest.update(chunk)
    return StorageResult(digest.hexdigest(), path.stat().st_size, SCHEMA_VERSION)


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline HORIZONT SQLite lifecycle; refuses overwrite")
    sub = parser.add_subparsers(dest="action", required=True)
    for action in ("init", "verify", "backup", "restore"):
        command = sub.add_parser(action)
        if action in ("init", "verify"):
            command.add_argument("database")
        else:
            command.add_argument("source")
            command.add_argument("new_destination")
    args = parser.parse_args()
    if args.action == "init":
        result = initialize_new(args.database)
    elif args.action == "verify":
        result = verify_existing(args.database)
    elif args.action == "backup":
        result = backup_live(args.source, args.new_destination)
    else:
        result = restore_to_new(args.source, args.new_destination)
    print(f"OK schema={result.schema_version} bytes={result.bytes_written} sha256={result.sha256}")


if __name__ == "__main__":
    main()
