"""Generate fictional SQLite records for Docker API LAB only.

This is a build-time script; it refuses existing destinations. NEVER pass a
real HORIZONT database path. No real providers, requests or user records.
"""
from __future__ import annotations

import sys
from pathlib import Path

from horizont.core.db import add_event, connect, create_source, migrate


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: python generate_api_demo.py NEW_DEMO_DB_PATH")
    destination = Path(sys.argv[1])
    if destination.exists() or not destination.parent.is_dir():
        raise SystemExit("refusing existing path or missing parent directory")
    connection = connect(destination)
    try:
        migrate(connection)
        create_source(connection, "demo-pl", "Fikcyjne źródło PL", "https://example.test/pl")
        create_source(connection, "demo-uk", "Fictional UK source", "https://example.test/uk")
        add_event(
            connection, source_id="demo-pl",
            original_url="https://example.test/pl/fictional-1",
            dedup_key="synthetic:pl:1", title="Fikcyjne zdarzenie testowe",
            language="pl", country="PL", collected_at="2026-10-20T10:00:00Z",
            occurred_at=None, published_at=None,
            summary="Wyłącznie próbka demonstracyjna — to nie jest fakt.",
        )
        add_event(
            connection, source_id="demo-uk",
            original_url="https://example.test/uk/fictional-1",
            dedup_key="synthetic:uk:1", title="Fictional demonstration event",
            language="en", country="GB", collected_at="2026-10-20T11:00:00Z",
            published_at=None, occurred_at=None,
            summary="Synthetic only — not a real incident.",
        )
        # This image is immutable at runtime, so checkpoint the synthetic WAL
        # and switch its *demo-only* DB to rollback journaling before COPY.
        # Real collector SQLite WAL strategy is unaffected by this fixture.
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        connection.execute("PRAGMA journal_mode=DELETE")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
