"""Synthetic offline tests for the read-only SQLite query layer."""

from __future__ import annotations

from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from horizont.core import (
    add_event,
    create_source,
    finish_ingest_run,
    get_event_detail,
    list_recent_events,
    list_source_statuses,
    migrate,
    open_readonly,
    start_ingest_run,
)


class ReadonlyQueryTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        migrate(self.connection)
        create_source(self.connection, "feed-a", "Synthetic Feed A")
        create_source(self.connection, "feed-b", "Synthetic Feed B")

    def tearDown(self):
        self.connection.close()

    def add(self, key, **overrides):
        values = {
            "source_id": "feed-a",
            "original_url": f"https://example.test/{key}",
            "dedup_key": key,
            "title": f"Synthetic {key}",
            "collected_at": "2026-05-03T00:00:00Z",
        }
        values.update(overrides)
        return add_event(self.connection, **values)

    def test_half_open_utc_bounds_use_only_selected_timestamp(self):
        start_id, _ = self.add(
            "at-start", occurred_at="2026-05-01T00:00:00Z", published_at=None,
            collected_at="2026-05-04T00:00:00Z",
        )
        self.add("at-end", occurred_at="2026-05-02T00:00:00Z")
        self.add("null-date", occurred_at=None, published_at="2026-05-01T12:00:00Z")
        result = list_recent_events(
            self.connection,
            time_field="occurred_at",
            start="2026-04-30T20:00:00-04:00",
            end="2026-05-02T00:00:00+00:00",
        )
        self.assertEqual([item["id"] for item in result["items"]], [start_id])
        self.assertEqual(result["start"], "2026-05-01T00:00:00.000000Z")
        self.assertEqual(result["end"], "2026-05-02T00:00:00.000000Z")
        # A NULL published_at is not silently substituted with occurred_at.
        published = list_recent_events(
            self.connection,
            time_field="published_at",
            start="2026-05-01T00:00:00Z",
            end="2026-05-02T00:00:00Z",
        )
        self.assertEqual([item["dedup_key"] for item in published["items"]], ["null-date"])

    def test_null_time_is_returned_as_null_without_bounds_and_ordered_last(self):
        dated_id, _ = self.add("dated", occurred_at="2026-05-01T00:00:00Z")
        null_id, _ = self.add("no-occurred-date", occurred_at=None)
        result = list_recent_events(self.connection, time_field="occurred_at")
        self.assertEqual([item["id"] for item in result["items"]], [dated_id, null_id])
        self.assertIsNone(result["items"][1]["occurred_at"])

    def test_source_and_source_observed_country_filters_do_not_duplicate_events(self):
        event_id, _ = self.add("shared", country="PL", language="pl")
        self.add(
            "shared", source_id="feed-b", original_url="https://feed-b.test/shared",
            country="PL", language="pl", collected_at="2026-05-04T00:00:00Z",
        )
        result = list_recent_events(self.connection, country="PL")
        self.assertEqual([item["id"] for item in result["items"]], [event_id])
        self.assertEqual(result["country_filter_semantics"], "source_observed_country")
        by_source = list_recent_events(self.connection, source_id="feed-b", country="PL")
        self.assertEqual([item["id"] for item in by_source["items"]], [event_id])
        self.assertEqual(list_recent_events(self.connection, country="GB")["items"], [])

    def test_detail_contains_each_source_provenance(self):
        event_id, _ = self.add("detail", country="GB", language="en")
        self.add(
            "detail", source_id="feed-b", original_url="https://feed-b.test/detail",
            country="PL", language="pl",
        )
        detail = get_event_detail(self.connection, event_id)
        self.assertEqual(detail["event"]["id"], event_id)
        self.assertEqual([item["source_id"] for item in detail["sources"]], ["feed-a", "feed-b"])
        self.assertEqual(detail["sources"][1]["original_url"], "https://feed-b.test/detail")
        self.assertEqual(detail["sources"][1]["country"], "PL")
        self.assertIsNone(get_event_detail(self.connection, 99999))

    def test_same_timestamp_order_and_offset_pagination_are_deterministic(self):
        ids = [self.add(f"same-{index}", collected_at="2026-05-01T00:00:00Z")[0] for index in range(3)]
        full = list_recent_events(self.connection, limit=3)
        page0 = list_recent_events(self.connection, limit=1, offset=0)
        page1 = list_recent_events(self.connection, limit=1, offset=1)
        self.assertEqual([item["id"] for item in full["items"]], list(reversed(ids)))
        self.assertEqual(page0["items"][0]["id"], full["items"][0]["id"])
        self.assertEqual(page1["items"][0]["id"], full["items"][1]["id"])

    def test_invalid_query_arguments_are_rejected(self):
        invalid = (
            {"time_field": "occurred_at DESC; DROP TABLE events"},
            {"start": "2026-05-01T00:00:00"},
            {"start": "2026-05-02T00:00:00Z", "end": "2026-05-01T00:00:00Z"},
            {"country": "pl"},
            {"source_id": " "},
            {"limit": 101},
            {"offset": -1},
        )
        for arguments in invalid:
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                list_recent_events(self.connection, **arguments)
        with self.assertRaises(ValueError):
            get_event_detail(self.connection, 0)

    def test_source_status_includes_deterministic_last_run_summary(self):
        first = start_ingest_run(self.connection, "feed-a", "2026-05-01T00:00:00Z")
        finish_ingest_run(
            self.connection, first, "failed", finished_at="2026-05-01T00:00:03Z",
            error_message="synthetic failure detail",
        )
        latest = start_ingest_run(self.connection, "feed-a", "2026-05-02T00:00:00Z")
        finish_ingest_run(
            self.connection, latest, "ok", finished_at="2026-05-02T00:00:03Z", items_seen=4, items_inserted=2
        )
        statuses = list_source_statuses(self.connection)
        feed_a = statuses[0]
        self.assertEqual(feed_a["source_id"], "feed-a")
        self.assertEqual(feed_a["source_status"], "ok")
        self.assertEqual(feed_a["run_id"], latest)
        self.assertEqual(feed_a["run_status"], "ok")
        self.assertEqual(feed_a["items_seen"], 4)
        self.assertNotIn("error_message", feed_a)
        self.assertIsNone(statuses[1]["run_id"])

    def test_queries_do_not_write_to_connection(self):
        self.add("read-only-check", occurred_at="2026-05-01T00:00:00Z")
        before = self.connection.total_changes
        list_recent_events(self.connection)
        get_event_detail(self.connection, 1)
        list_source_statuses(self.connection)
        self.assertEqual(self.connection.total_changes, before)


class ReadonlyConnectionTests(unittest.TestCase):
    def test_open_readonly_blocks_writes_and_does_not_create_missing_files(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "synthetic.sqlite"
            connection = sqlite3.connect(database)
            connection.row_factory = sqlite3.Row
            migrate(connection)
            create_source(connection, "offline", "Synthetic offline source")
            event_id, _ = add_event(
                connection,
                source_id="offline",
                original_url="https://example.test/offline",
                dedup_key="offline:event",
                title="Synthetic offline event",
                collected_at="2026-05-01T00:00:00Z",
            )
            connection.close()

            readonly = open_readonly(database)
            try:
                self.assertEqual(readonly.execute("PRAGMA query_only").fetchone()[0], 1)
                self.assertEqual(len(list_recent_events(readonly)["items"]), 1)
                self.assertEqual(get_event_detail(readonly, event_id)["event"]["dedup_key"], "offline:event")
                self.assertEqual(list_source_statuses(readonly)[0]["source_id"], "offline")
                with self.assertRaises(sqlite3.OperationalError):
                    readonly.execute("DELETE FROM events")
            finally:
                readonly.close()

            missing = Path(directory) / "missing.sqlite"
            with self.assertRaises(FileNotFoundError):
                open_readonly(missing)
            self.assertFalse(missing.exists())

    def test_open_readonly_rejects_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "regular file"):
                open_readonly(directory)


if __name__ == "__main__":
    unittest.main()
