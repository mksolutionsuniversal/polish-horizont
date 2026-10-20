"""Opt-in **fictional-only** persistent-volume laboratory operations.

The active WAL writer is never served to the browser. Explicit one-shot jobs
publish a consistent, sealed, read-only SQLite view or backup into separate
named volumes. No real OSINT, timers, feeds or external file paths.
"""

from __future__ import annotations

import argparse
from contextlib import closing, contextmanager
from dataclasses import dataclass
import fcntl
import os
from pathlib import Path
import sqlite3
import stat

from horizont.core.db import add_event, utc_now, SCHEMA_VERSION
from horizont.core.persistence import (
    StorageResult, backup_live, initialize_new, restore_to_new, verify_existing,
)
from horizont.core.queries import open_readonly


@dataclass(frozen=True)
class VolumePaths:
    primary: Path
    view: Path
    backup: Path
    recovery: Path


# Hard-coded LAB paths: Compose maps each to its own isolated project volume.
DEFAULT = VolumePaths(
    primary=Path("/opt/horizont/state/primary.sqlite"),
    view=Path("/opt/horizont/view/view-v1.sqlite"),
    backup=Path("/opt/horizont/backup/backup-v1.sqlite"),
    recovery=Path("/opt/horizont/recovered/restored-v1.sqlite"),
)


@contextmanager
def _exclusive_writer(directory: Path):
    """Serialize all explicit writer maintenance jobs across processes.

    Lock file lives in primary named volume and survives container recreation.
    Its existence alone is NOT the lock; flock is released on exit or crash.
    """
    if not directory.is_dir() or directory.is_symlink():
        raise ValueError("existing primary-volume directory required")
    fd = os.open(
        directory / ".writer.lock",
        os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
        stat.S_IRUSR | stat.S_IWUSR,
    )
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def _primary_connection(path: Path) -> sqlite3.Connection:
    if path.is_symlink() or not path.is_file():
        raise ValueError("primary database must be an existing regular file")
    # mode=rw explicitly prevents sqlite3.connect from creating a missing DB.
    conn = sqlite3.connect(f"{path.absolute().as_uri()}?mode=rw", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _check_schema(conn: sqlite3.Connection) -> None:
    versions = [row[0] for row in conn.execute(
        "SELECT version FROM schema_migrations ORDER BY version"
    )]
    if versions != list(range(1, SCHEMA_VERSION + 1)):
        raise ValueError("LAB schema version mismatch")


def _seed_fictional(path: Path) -> None:
    """Seed exactly two pretend events; do not silently reseed partial data."""
    with closing(_primary_connection(path)) as conn:
        _check_schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        try:
            if conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0] != 0:
                raise FileExistsError("refusing to reseed existing persistent sources")
            if conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] != 0:
                raise FileExistsError("refusing to reseed existing persistent events")
            now = utc_now()
            for source_id, name in (
                ("lab-pl", "Fikcyjny Biuletyn PL"),
                ("lab-uk", "Fictional UK Bulletin"),
            ):
                conn.execute(
                    "INSERT INTO sources (id,name,home_url,status,created_at,updated_at) "
                    "VALUES (?,?,?,?,?,?)",
                    (source_id, name, "https://example.test/" + source_id,
                     "never_run", now, now),
                )
            # Dedup across PL/UK must preserve BOTH source-country observations;
            # language of stored event title is intentionally NOT inferred.
            sample = dict(
                dedup_key="lab:fictional:shared",
                title="Fikcyjne zdarzenie w LAB",
                collected_at="2026-10-20T17:00:00Z",
                summary="Tylko test trwałego zapisu, nie rzeczywiste zdarzenie.",
                occurred_at=None, published_at=None, validation_status="pending",
            )
            add_event(
                conn, source_id="lab-pl",
                original_url="https://example.test/lab-pl/1",
                language="pl", country="PL", **sample,
            )
            add_event(
                conn, source_id="lab-uk",
                original_url="https://example.test/lab-uk/1",
                language="en", country="GB", **sample,
            )
            add_event(
                conn, source_id="lab-uk",
                original_url="https://example.test/lab-uk/2",
                dedup_key="lab:fictional:uk2",
                title="Fictional second lab record",
                collected_at="2026-10-20T17:05:00Z",
                summary="Synthetic volume persistence test only.",
                language="en", country="GB",
                occurred_at=None, published_at=None,
                validation_status="pending",
            )
            conn.commit()
        except BaseException:
            conn.rollback()
            raise


def summary(path: Path) -> dict[str, int]:
    """Read-only, no source names/content or private paths printed."""
    if path.is_symlink() or not path.is_file():
        raise ValueError("SQLite database does not exist")
    with closing(open_readonly(path)) as conn:
        _check_schema(conn)
        return {
            "events": conn.execute("SELECT COUNT(*) FROM events").fetchone()[0],
            "sources": conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0],
            "provenance": conn.execute(
                "SELECT COUNT(*) FROM event_sources"
            ).fetchone()[0],
        }


def _print_result(action: str, result: StorageResult) -> None:
    print(
        f"LAB {action} OK schema={result.schema_version} "
        f"bytes={result.bytes_written} sha256={result.sha256}",
        flush=True,
    )


def writer_action(action: str, paths: VolumePaths = DEFAULT) -> None:
    if action not in ("init", "seed", "publish", "backup", "status"):
        raise ValueError("writer action not allowed")
    with _exclusive_writer(paths.primary.parent):
        if action == "init":
            _print_result(action, initialize_new(paths.primary))
        elif action == "seed":
            _seed_fictional(paths.primary)
            print(f"LAB seed OK {summary(paths.primary)}", flush=True)
        elif action == "publish":
            # Immutable read-view: reader never sees the source WAL/-shm.
            # Intentional one-time publication for this Stage C LAB.
            _print_result(action, backup_live(paths.primary, paths.view))
        elif action == "backup":
            # Named-volume backup is on the SAME Docker host: not offsite DR.
            _print_result(action, backup_live(paths.primary, paths.backup))
        else:
            print(f"LAB primary {summary(paths.primary)}", flush=True)


def restore_action(paths: VolumePaths = DEFAULT) -> None:
    # Recovery service has NO mount/access to primary writer volume.
    # Destination must be EMPTY. Old backup and active primary unchanged.
    _print_result("restore", restore_to_new(paths.backup, paths.recovery))
    print(f"LAB recovered {summary(paths.recovery)}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Fictional-only HORIZONT Docker storage LAB")
    parser.add_argument("action", choices=[
        "init", "seed", "publish", "backup", "status", "restore"
    ])
    args = parser.parse_args()
    if args.action == "restore":
        restore_action()
    else:
        writer_action(args.action)


if __name__ == "__main__":
    main()
