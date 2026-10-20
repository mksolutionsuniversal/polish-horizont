"""Offline tests for the SQLite data core; all inputs are synthetic."""

from __future__ import annotations

from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from horizont.core import db as db_module  # noqa: E402
from horizont.core import (  # noqa: E402
    SCHEMA_VERSION,
    add_event,
    connect,
    create_source,
    finish_ingest_run,
    migrate,
    normalize_utc,
    start_ingest_run,
)


class SQLiteCoreTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        migrate(self.connection)
        create_source(self.connection, "synthetic-feed", "Synthetic feed", "https://example.test/feed")

    def tearDown(self):
        self.connection.close()

    def add(self, **overrides):
        values = {
            "source_id": "synthetic-feed",
            "original_url": "https://example.test/story/1",
            "dedup_key": "synthetic:story:1",
            "title": "Synthetic event",
            "collected_at": "2026-01-03T04:00:00Z",
        }
        values.update(overrides)
        return add_event(self.connection, **values)

    def test_distinct_dates_are_normalized_to_utc(self):
        event_id, inserted = self.add(
            occurred_at="2026-01-01T06:00:00+02:00",
            published_at="2026-01-02T02:00:00-02:00",
        )
        self.assertTrue(inserted)
        event = self.connection.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
        self.assertEqual(event["occurred_at"], "2026-01-01T04:00:00.000000Z")
        self.assertEqual(event["published_at"], "2026-01-02T04:00:00.000000Z")
        self.assertEqual(event["collected_at"], "2026-01-03T04:00:00.000000Z")

    def test_missing_dates_are_null_and_naive_dates_rejected(self):
        event_id, _ = self.add(occurred_at=None, published_at=None)
        event = self.connection.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
        self.assertIsNone(event["occurred_at"])
        self.assertIsNone(event["published_at"])
        self.assertIsNone(normalize_utc(None))
        with self.assertRaisesRegex(ValueError, "UTC offset"):
            normalize_utc("2026-01-01T12:00:00")
        with self.assertRaisesRegex(ValueError, "required"):
            normalize_utc(None, required=True)

    def test_dedup_keeps_one_event_and_multiple_source_provenance(self):
        event_id, inserted = self.add()
        create_source(self.connection, "second-feed", "Second synthetic feed")
        duplicate_id, duplicate_inserted = self.add(
            source_id="second-feed",
            original_url="https://other.example.test/story/9",
            dedup_key="synthetic:story:1",
            collected_at="2026-01-04T00:00:00Z",
            language="pl",
            country="PL",
        )
        self.assertTrue(inserted)
        self.assertFalse(duplicate_inserted)
        self.assertEqual(event_id, duplicate_id)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM events").fetchone()[0], 1)
        origins = self.connection.execute(
            "SELECT source_id, original_url FROM event_sources ORDER BY source_id"
        ).fetchall()
        self.assertEqual([row["source_id"] for row in origins], ["second-feed", "synthetic-feed"])
        self.assertEqual(origins[0]["original_url"], "https://other.example.test/story/9")

    def test_null_geolocation_is_valid(self):
        event_id, _ = self.add()
        event = self.connection.execute(
            "SELECT latitude, longitude, geo_precision, geo_confidence FROM events WHERE id=?", (event_id,)
        ).fetchone()
        self.assertIsNone(event["latitude"])
        self.assertIsNone(event["longitude"])
        self.assertEqual(event["geo_precision"], "unknown")
        self.assertIsNone(event["geo_confidence"])

    def test_database_constraints_enforce_integrity(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(
                "INSERT INTO event_sources(event_id,source_id,original_url,first_seen_at,last_seen_at) "
                "VALUES (999,'missing','https://example.test/x','2026-01-01T00:00:00Z','2026-01-01T00:00:00Z')"
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.add(latitude=91, longitude=0)
        with self.assertRaises(sqlite3.IntegrityError):
            self.add(latitude=1, longitude=None)
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(
                "INSERT INTO sources(id,name,status,created_at,updated_at) VALUES "
                "('bad','bad','zero','2026-01-01T00:00:00Z','2026-01-01T00:00:00Z')"
            )

    def test_failed_and_unavailable_runs_remain_distinct_from_empty(self):
        failed = start_ingest_run(self.connection, "synthetic-feed", "2026-01-01T00:00:00Z")
        finish_ingest_run(
            self.connection, failed, "failed", finished_at="2026-01-01T00:00:02Z", error_message="synthetic timeout"
        )
        self.assertEqual(
            self.connection.execute("SELECT status FROM sources WHERE id='synthetic-feed'").fetchone()[0], "failed"
        )
        unavailable = start_ingest_run(self.connection, "synthetic-feed", "2026-01-02T00:00:00Z")
        finish_ingest_run(
            self.connection, unavailable, "unavailable", finished_at="2026-01-02T00:00:02Z",
            error_message="synthetic offline fixture",
        )
        run = self.connection.execute("SELECT status, items_seen FROM ingest_runs WHERE id=?", (unavailable,)).fetchone()
        self.assertEqual(tuple(run), ("unavailable", 0))
        self.assertEqual(
            self.connection.execute("SELECT status FROM sources WHERE id='synthetic-feed'").fetchone()[0],
            "unavailable",
        )
        empty = start_ingest_run(self.connection, "synthetic-feed", "2026-01-03T00:00:00Z")
        finish_ingest_run(self.connection, empty, "empty", finished_at="2026-01-03T00:00:02Z")
        self.assertEqual(
            self.connection.execute("SELECT status FROM sources WHERE id='synthetic-feed'").fetchone()[0], "empty"
        )

    def test_source_status_vocabulary_and_never_run_default(self):
        self.assertEqual(
            self.connection.execute("SELECT status FROM sources WHERE id='synthetic-feed'").fetchone()[0], "never_run"
        )
        accepted = ("ok", "empty", "stale", "unknown", "failed", "unavailable", "never_run")
        for status in accepted:
            with self.subTest(status=status):
                self.connection.execute("UPDATE sources SET status=? WHERE id='synthetic-feed'", (status,))
                self.assertEqual(
                    self.connection.execute("SELECT status FROM sources WHERE id='synthetic-feed'").fetchone()[0], status
                )

    def test_run_validation_errors_do_not_mutate_state(self):
        run_id = start_ingest_run(self.connection, "synthetic-feed", "2026-01-01T00:00:00Z")
        with self.assertRaisesRegex(ValueError, "require an error_message"):
            finish_ingest_run(self.connection, run_id, "failed")
        with self.assertRaisesRegex(ValueError, "inserted cannot exceed seen"):
            finish_ingest_run(self.connection, run_id, "ok", items_seen=1, items_inserted=2)
        self.assertEqual(
            self.connection.execute("SELECT status FROM ingest_runs WHERE id=?", (run_id,)).fetchone()[0], "running"
        )

    def test_file_database_uses_wal(self):
        with tempfile.TemporaryDirectory() as directory:
            connection = connect(Path(directory) / "synthetic.sqlite")
            try:
                self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal")
                self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            finally:
                connection.close()


class MigrationTests(unittest.TestCase):
    def test_migration_is_incremental_and_idempotent(self):
        connection = sqlite3.connect(":memory:")
        try:
            self.assertEqual(migrate(connection, target_version=1), 1)
            self.assertEqual(
                connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0], 1
            )
            before = {row[1] for row in connection.execute("PRAGMA index_list(events)")}
            self.assertNotIn("idx_events_occurred_at", before)
            self.assertEqual(migrate(connection), SCHEMA_VERSION)
            self.assertEqual(migrate(connection), SCHEMA_VERSION)
            after = {row[1] for row in connection.execute("PRAGMA index_list(events)")}
            self.assertIn("idx_events_occurred_at", after)
            self.assertEqual(
                connection.execute("SELECT count(*) FROM schema_migrations").fetchone()[0], SCHEMA_VERSION
            )
        finally:
            connection.close()

    def test_failed_ddl_migration_rolls_back_and_can_retry(self):
        connection = sqlite3.connect(":memory:")
        try:
            self.assertEqual(migrate(connection, target_version=1), 1)

            def interrupted_migration(db):
                db.execute("CREATE INDEX idx_synthetic_partial ON events(title)")
                raise sqlite3.OperationalError("synthetic interrupted migration")

            with patch.dict(db_module._MIGRATIONS, {2: interrupted_migration}):
                with self.assertRaisesRegex(sqlite3.OperationalError, "synthetic interrupted"):
                    migrate(connection, target_version=2)

            index = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_synthetic_partial'"
            ).fetchone()
            self.assertIsNone(index, "DDL must be rolled back after an interrupted migration")
            self.assertEqual(
                connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0], 1
            )
            self.assertEqual(migrate(connection), SCHEMA_VERSION)
            self.assertEqual(
                connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0],
                SCHEMA_VERSION,
            )
        finally:
            connection.close()

    def test_newer_schema_target_is_rejected(self):
        connection = sqlite3.connect(":memory:")
        try:
            with self.assertRaisesRegex(ValueError, "unsupported schema version"):
                migrate(connection, target_version=SCHEMA_VERSION + 1)
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
