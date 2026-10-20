"""Minimal standard-library SQLite schema and write helpers.

This module performs no network access and starts no background work.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from urllib.parse import urlsplit

SCHEMA_VERSION = 2
SOURCE_STATUSES = (
    "ok",
    "empty",
    "stale",
    "unknown",
    "failed",
    "unavailable",
    "never_run",
)
RUN_STATUSES = ("running", "ok", "empty", "failed", "unavailable")
VALIDATION_STATUSES = ("pending", "valid", "invalid")
GEO_PRECISIONS = ("unknown", "country", "region", "locality", "point")


def utc_now() -> str:
    """Return current UTC in a stable, sortable ISO-8601 representation."""
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def normalize_utc(value: str | datetime | None, *, required: bool = False) -> str | None:
    """Validate an aware ISO-8601 datetime and normalize it to UTC with a Z suffix."""
    if value is None:
        if required:
            raise ValueError("timestamp is required")
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"invalid ISO-8601 timestamp: {value!r}") from exc
    else:
        raise TypeError("timestamp must be an ISO-8601 string, datetime, or None")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include a UTC offset")
    return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def connect(database: str | Path) -> sqlite3.Connection:
    """Open a local SQLite database with foreign keys and WAL enabled."""
    connection = sqlite3.connect(str(database))
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection


def _migration_1(connection: sqlite3.Connection) -> None:
    connection.execute(
        """CREATE TABLE sources (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            home_url TEXT,
            status TEXT NOT NULL DEFAULT 'never_run'
                CHECK (status IN ('ok','empty','stale','unknown','failed','unavailable','never_run')),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )"""
    )
    connection.execute(
        """CREATE TABLE ingest_runs (
            id INTEGER PRIMARY KEY,
            source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE RESTRICT,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            status TEXT NOT NULL DEFAULT 'running'
                CHECK (status IN ('running','ok','empty','failed','unavailable')),
            items_seen INTEGER NOT NULL DEFAULT 0 CHECK (items_seen >= 0),
            items_inserted INTEGER NOT NULL DEFAULT 0 CHECK (items_inserted >= 0),
            error_message TEXT,
            UNIQUE (id, source_id),
            CHECK (finished_at IS NULL OR finished_at >= started_at),
            CHECK (items_inserted <= items_seen)
        )"""
    )
    connection.execute(
        """CREATE TABLE events (
            id INTEGER PRIMARY KEY,
            dedup_key TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            summary TEXT,
            occurred_at TEXT,
            published_at TEXT,
            collected_at TEXT NOT NULL,
            latitude REAL,
            longitude REAL,
            geo_precision TEXT NOT NULL DEFAULT 'unknown'
                CHECK (geo_precision IN ('unknown','country','region','locality','point')),
            geo_confidence REAL CHECK (geo_confidence IS NULL OR (geo_confidence >= 0 AND geo_confidence <= 1)),
            validation_status TEXT NOT NULL DEFAULT 'pending'
                CHECK (validation_status IN ('pending','valid','invalid')),
            created_at TEXT NOT NULL,
            CHECK ((latitude IS NULL AND longitude IS NULL) OR (latitude IS NOT NULL AND longitude IS NOT NULL)),
            CHECK (latitude IS NULL OR (latitude >= -90 AND latitude <= 90)),
            CHECK (longitude IS NULL OR (longitude >= -180 AND longitude <= 180))
        )"""
    )
    connection.execute(
        """CREATE TABLE event_sources (
            event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
            source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE RESTRICT,
            ingest_run_id INTEGER REFERENCES ingest_runs(id) ON DELETE SET NULL,
            original_url TEXT NOT NULL,
            language TEXT,
            country TEXT,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            PRIMARY KEY (event_id, source_id),
            FOREIGN KEY (ingest_run_id, source_id) REFERENCES ingest_runs(id, source_id) ON DELETE RESTRICT,
            CHECK (country IS NULL OR (length(country) = 2 AND country = upper(country))),
            CHECK (language IS NULL OR length(language) BETWEEN 2 AND 35)
        )"""
    )


def _migration_2(connection: sqlite3.Connection) -> None:
    connection.execute("CREATE INDEX idx_events_occurred_at ON events(occurred_at)")
    connection.execute("CREATE INDEX idx_events_published_at ON events(published_at)")
    connection.execute("CREATE INDEX idx_events_collected_at ON events(collected_at)")
    connection.execute("CREATE INDEX idx_event_sources_source_seen ON event_sources(source_id, last_seen_at)")
    connection.execute("CREATE INDEX idx_ingest_runs_source_started ON ingest_runs(source_id, started_at)")


_MIGRATIONS = {1: _migration_1, 2: _migration_2}


def migrate(connection: sqlite3.Connection, target_version: int = SCHEMA_VERSION) -> int:
    """Apply each pending schema migration atomically and return its version."""
    if target_version < 0 or target_version > SCHEMA_VERSION:
        raise ValueError(f"unsupported schema version: {target_version}")
    connection.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations "
        "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    current = connection.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()[0]
    if current > target_version:
        raise ValueError(f"database schema {current} is newer than target {target_version}")
    if connection.in_transaction:
        raise ValueError("migrations require a connection without an active transaction")
    for version in range(current + 1, target_version + 1):
        with connection:
            # sqlite3's legacy transaction mode does not implicitly BEGIN for DDL.
            # Explicitly start a transaction so incomplete CREATE TABLE/INDEX
            # changes roll back along with the migration-version record.
            connection.execute("BEGIN")
            _MIGRATIONS[version](connection)
            connection.execute(
                "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (version, utc_now()),
            )
    return target_version


def create_source(
    connection: sqlite3.Connection,
    source_id: str,
    name: str,
    home_url: str | None = None,
) -> None:
    """Create one source record, initially marked never_run."""
    if not source_id.strip() or not name.strip():
        raise ValueError("source_id and name are required")
    if home_url is not None:
        parts = urlsplit(home_url)
        if parts.scheme not in {"http", "https"} or not parts.netloc:
            raise ValueError("home_url must be an absolute HTTP(S) URL")
    now = utc_now()
    with connection:
        connection.execute(
            "INSERT INTO sources(id, name, home_url, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (source_id, name, home_url, now, now),
        )


def start_ingest_run(connection: sqlite3.Connection, source_id: str, started_at: str | datetime | None = None) -> int:
    """Record the beginning of a source fetch; this function performs no fetch."""
    started = normalize_utc(started_at or utc_now(), required=True)
    with connection:
        cursor = connection.execute(
            "INSERT INTO ingest_runs(source_id, started_at) VALUES (?, ?)", (source_id, started)
        )
    return int(cursor.lastrowid)


def finish_ingest_run(
    connection: sqlite3.Connection,
    run_id: int,
    status: str,
    *,
    finished_at: str | datetime | None = None,
    items_seen: int = 0,
    items_inserted: int = 0,
    error_message: str | None = None,
) -> None:
    """Finish a recorded run and set its source state without equating failure to emptiness."""
    if status not in {"ok", "empty", "failed", "unavailable"}:
        raise ValueError(f"invalid final run status: {status}")
    if items_seen < 0 or items_inserted < 0 or items_inserted > items_seen:
        raise ValueError("item counts must be non-negative and inserted cannot exceed seen")
    if status in {"failed", "unavailable"} and not error_message:
        raise ValueError("failed and unavailable runs require an error_message")
    finished = normalize_utc(finished_at or utc_now(), required=True)
    with connection:
        row = connection.execute(
            "SELECT source_id, started_at, status FROM ingest_runs WHERE id = ?", (run_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"unknown ingest run: {run_id}")
        if row["status"] != "running":
            raise ValueError(f"ingest run {run_id} is already finished")
        if finished < row["started_at"]:
            raise ValueError("finished_at must not precede started_at")
        connection.execute(
            """UPDATE ingest_runs SET finished_at=?, status=?, items_seen=?, items_inserted=?, error_message=?
               WHERE id=?""",
            (finished, status, items_seen, items_inserted, error_message, run_id),
        )
        connection.execute(
            "UPDATE sources SET status=?, updated_at=? WHERE id=?",
            (status, finished, row["source_id"]),
        )


def add_event(
    connection: sqlite3.Connection,
    *,
    source_id: str,
    original_url: str,
    dedup_key: str,
    title: str,
    collected_at: str | datetime,
    occurred_at: str | datetime | None = None,
    published_at: str | datetime | None = None,
    language: str | None = None,
    country: str | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
    geo_precision: str = "unknown",
    geo_confidence: float | None = None,
    validation_status: str = "pending",
    summary: str | None = None,
    ingest_run_id: int | None = None,
) -> tuple[int, bool]:
    """Insert or deduplicate an event, retaining its source URL and provenance."""
    if not dedup_key.strip() or not title.strip():
        raise ValueError("dedup_key and title are required")
    parts = urlsplit(original_url)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ValueError("original_url must be an absolute HTTP(S) URL")
    if country is not None and (len(country) != 2 or not country.isalpha() or country != country.upper()):
        raise ValueError("country must be a two-letter uppercase code or None")
    if language is not None and not 2 <= len(language) <= 35:
        raise ValueError("language must be a 2-35 character language tag or None")
    if geo_precision not in GEO_PRECISIONS:
        raise ValueError(f"invalid geo_precision: {geo_precision}")
    if validation_status not in VALIDATION_STATUSES:
        raise ValueError(f"invalid validation_status: {validation_status}")
    collected = normalize_utc(collected_at, required=True)
    occurred = normalize_utc(occurred_at)
    published = normalize_utc(published_at)
    created = utc_now()
    with connection:
        row = connection.execute("SELECT id FROM events WHERE dedup_key=?", (dedup_key,)).fetchone()
        inserted = row is None
        if inserted:
            cursor = connection.execute(
                """INSERT INTO events(
                    dedup_key,title,summary,occurred_at,published_at,collected_at,latitude,longitude,
                    geo_precision,geo_confidence,validation_status,created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (dedup_key, title, summary, occurred, published, collected, latitude, longitude,
                 geo_precision, geo_confidence, validation_status, created),
            )
            event_id = int(cursor.lastrowid)
        else:
            event_id = int(row["id"])
        connection.execute(
            """INSERT INTO event_sources(
                event_id,source_id,ingest_run_id,original_url,language,country,first_seen_at,last_seen_at
            ) VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(event_id,source_id) DO UPDATE SET
                ingest_run_id=excluded.ingest_run_id,
                original_url=excluded.original_url,
                language=excluded.language,
                country=excluded.country,
                last_seen_at=excluded.last_seen_at""",
            (event_id, source_id, ingest_run_id, original_url, language, country, collected, collected),
        )
        return event_id, inserted
