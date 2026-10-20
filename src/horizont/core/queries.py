"""Fixed, parameterized, read-only queries for the local SQLite prototype."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sqlite3

from .db import normalize_utc

MAX_LIMIT = 100
MAX_OFFSET = 100_000
_TIME_FIELDS = {
    "occurred_at": "e.occurred_at",
    "published_at": "e.published_at",
    "collected_at": "e.collected_at",
}


def open_readonly(database: str | Path) -> sqlite3.Connection:
    """Open an existing on-disk SQLite database read-only; never create or migrate it."""
    path = Path(database).expanduser().resolve(strict=True)
    if not path.is_file():
        raise ValueError("database path must be an existing regular file")
    connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _pagination(limit: int, offset: int) -> None:
    if type(limit) is not int or not 1 <= limit <= MAX_LIMIT:
        raise ValueError(f"limit must be an integer from 1 to {MAX_LIMIT}")
    if type(offset) is not int or not 0 <= offset <= MAX_OFFSET:
        raise ValueError(f"offset must be an integer from 0 to {MAX_OFFSET}")


def _country(value: str | None) -> str | None:
    if value is not None and (len(value) != 2 or not value.isascii() or not value.isalpha() or value != value.upper()):
        raise ValueError("country must be a two-letter uppercase code")
    return value


def list_recent_events(
    connection: sqlite3.Connection,
    *,
    time_field: str = "collected_at",
    start: str | datetime | None = None,
    end: str | datetime | None = None,
    source_id: str | None = None,
    country: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, object]:
    """List unique events in deterministic order, using optional half-open UTC bounds.

    Bounds apply only to the named field: ``start <= timestamp < end``. A NULL
    timestamp is never replaced with another event date and cannot match a bound.
    Country is matched on source provenance (``event_sources.country``).
    """
    _pagination(limit, offset)
    if time_field not in _TIME_FIELDS:
        raise ValueError("time_field must be occurred_at, published_at, or collected_at")
    if source_id is not None and not source_id.strip():
        raise ValueError("source_id cannot be empty")
    country = _country(country)
    start_utc = normalize_utc(start)
    end_utc = normalize_utc(end)
    if start_utc is not None and end_utc is not None and start_utc >= end_utc:
        raise ValueError("time range must satisfy start < end")

    timestamp = _TIME_FIELDS[time_field]
    where: list[str] = []
    parameters: list[object] = []
    if start_utc is not None:
        where.append(f"{timestamp} >= ?")
        parameters.append(start_utc)
    if end_utc is not None:
        where.append(f"{timestamp} < ?")
        parameters.append(end_utc)
    if source_id is not None or country is not None:
        provenance = ["es.event_id = e.id"]
        if source_id is not None:
            provenance.append("es.source_id = ?")
            parameters.append(source_id)
        if country is not None:
            provenance.append("es.country = ?")
            parameters.append(country)
        where.append(f"EXISTS (SELECT 1 FROM event_sources es WHERE {' AND '.join(provenance)})")

    sql = (
        "SELECT e.id, e.dedup_key, e.title, e.summary, e.occurred_at, e.published_at, "
        "e.collected_at, e.latitude, e.longitude, e.geo_precision, e.geo_confidence, "
        "e.validation_status FROM events e"
    )
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += f" ORDER BY ({timestamp} IS NULL) ASC, {timestamp} DESC, e.id DESC LIMIT ? OFFSET ?"
    parameters.extend((limit, offset))
    rows = connection.execute(sql, parameters).fetchall()
    return {
        "items": [dict(row) for row in rows],
        "limit": limit,
        "offset": offset,
        "time_field": time_field,
        "start": start_utc,
        "end": end_utc,
        "country_filter_semantics": "source_observed_country",
    }


def get_event_detail(connection: sqlite3.Connection, event_id: int) -> dict[str, object] | None:
    """Return one event and its per-source provenance, or None when absent."""
    if type(event_id) is not int or event_id <= 0:
        raise ValueError("event_id must be a positive integer")
    event = connection.execute(
        """SELECT id, dedup_key, title, summary, occurred_at, published_at, collected_at,
                  latitude, longitude, geo_precision, geo_confidence, validation_status
           FROM events WHERE id = ?""",
        (event_id,),
    ).fetchone()
    if event is None:
        return None
    sources = connection.execute(
        """SELECT s.id AS source_id, s.name AS source_name,
                  es.original_url, es.language, es.country,
                  es.first_seen_at, es.last_seen_at, es.ingest_run_id
           FROM event_sources es JOIN sources s ON s.id = es.source_id
           WHERE es.event_id = ? ORDER BY s.id ASC""",
        (event_id,),
    ).fetchall()
    return {"event": dict(event), "sources": [dict(row) for row in sources]}


def list_source_statuses(connection: sqlite3.Connection) -> list[dict[str, object]]:
    """List source states with each source's latest run summary, without raw error text."""
    rows = connection.execute(
        """SELECT s.id AS source_id, s.name AS source_name, s.status AS source_status,
                  r.id AS run_id, r.started_at AS run_started_at, r.finished_at AS run_finished_at,
                  r.status AS run_status, r.items_seen, r.items_inserted
           FROM sources s
           LEFT JOIN ingest_runs r ON r.id = (
               SELECT latest.id FROM ingest_runs latest
               WHERE latest.source_id = s.id
               ORDER BY latest.started_at DESC, latest.id DESC LIMIT 1
           )
           ORDER BY s.id ASC"""
    ).fetchall()
    return [dict(row) for row in rows]
