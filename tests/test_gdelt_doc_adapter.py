"""Offline GDELT adapter tests using synthetic example.test records only."""

from __future__ import annotations

from pathlib import Path
import json
import sqlite3
import sys
import unittest
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from horizont.adapters.gdelt_doc import (  # noqa: E402
    DOC_ENDPOINT,
    GDELT_PROJECT_URL,
    SOURCE_ID,
    GDELTDocError,
    build_search_url,
    canonicalize_article_url,
    ingest_payload,
    normalize_response,
)
from horizont.core import migrate  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "gdelt_doc_synthetic.json"


class GDELTDocAdapterTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        migrate(self.connection)
        self.payload = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def tearDown(self):
        self.connection.close()

    def test_fixture_normalizes_provenance_and_distinct_dates(self):
        batch = normalize_response(self.payload)
        self.assertEqual(len(batch.articles), 2)
        first = batch.articles[0]
        self.assertIn("ćwiczeń", first.title)
        self.assertEqual(first.original_url, "https://example.test/pl/wiadomosc/alpha")
        self.assertEqual(first.gdelt_seen_at, "2026-10-19T12:00:00.000000Z")
        self.assertIsNone(first.published_at)
        self.assertIsNone(first.occurred_at)
        self.assertIsNone(first.country)
        self.assertEqual(first.source_country_label, "Synthetic country label")
        self.assertEqual(first.source_id, SOURCE_ID)
        self.assertEqual(first.attribution_url, GDELT_PROJECT_URL)
        self.assertEqual(first.geo_precision, "unknown")
        self.assertEqual(first.validation_status, "pending")

    def test_url_builder_is_bounded_and_offline(self):
        with patch("urllib.request.urlopen") as urlopen:
            url = build_search_url("test & ćwiczenia", "2026-10-19T10:00:00+02:00", "2026-10-19T12:00:00Z")
            urlopen.assert_not_called()
        parsed = urlsplit(url)
        self.assertEqual(f"{parsed.scheme}://{parsed.netloc}{parsed.path}", DOC_ENDPOINT)
        params = parse_qs(parsed.query)
        self.assertEqual(params["query"], ["test & ćwiczenia"])
        self.assertEqual(params["mode"], ["artlist"])
        self.assertEqual(params["format"], ["json"])
        self.assertEqual(params["startdatetime"], ["20261019080000"])
        self.assertEqual(params["enddatetime"], ["20261019120000"])
        self.assertEqual(params["maxrecords"], ["10"])
        self.assertNotIn("gdeltcloud", url.lower())

    def test_url_builder_rejects_invalid_bounds(self):
        valid = ("2026-10-19T10:00:00Z", "2026-10-19T11:00:00Z")
        for kwargs in (
            {"query": " ", "start": valid[0], "end": valid[1]},
            {"query": "x", "start": "2026-10-19T10:00:00", "end": valid[1]},
            {"query": "x", "start": valid[1], "end": valid[0]},
            {"query": "x", "start": "2026-10-19T10:00:00.000001Z", "end": valid[1]},
            {"query": "x", "start": valid[0], "end": valid[1], "max_records": 11},
            {"query": "x", "start": valid[0], "end": valid[1], "max_records": True},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(GDELTDocError):
                build_search_url(**kwargs)

    def test_canonical_dedup_uses_publisher_url_not_title(self):
        url = canonicalize_article_url("HTTPS://EXAMPLE.TEST:443/a?q=1#frag")
        self.assertEqual(url, "https://example.test/a?q=1")
        first = normalize_response({"articles": [{"title": "one", "url": url}]})
        second = normalize_response({"articles": [{"title": "different", "url": url}]})
        self.assertEqual(first.articles[0].dedup_key, second.articles[0].dedup_key)
        with self.assertRaisesRegex(GDELTDocError, "duplicates"):
            normalize_response({"articles": [
                {"title": "one", "url": "https://example.test/a#one"},
                {"title": "two", "url": "https://example.test/a#two"},
            ]})

    def test_valid_empty_is_empty_and_exact_limit_may_be_truncated(self):
        empty = ingest_payload(self.connection, {"articles": []}, collected_at="2026-10-20T10:00:00Z")
        self.assertEqual(empty.status, "empty")
        self.assertFalse(empty.possibly_truncated)
        payload = {"articles": self.payload["articles"][:1]}
        self.assertTrue(normalize_response(payload, max_records=1).possibly_truncated)
        self.assertFalse(normalize_response(payload, max_records=2).possibly_truncated)

    def test_ingest_persists_only_safe_metadata_and_reimport_deduplicates(self):
        first = ingest_payload(self.connection, self.payload, collected_at="2026-10-20T10:00:00+01:00")
        self.assertEqual((first.status, first.items_seen, first.items_inserted), ("ok", 2, 2))
        second = ingest_payload(self.connection, self.payload, collected_at="2026-10-20T10:01:00Z")
        self.assertEqual(second.items_inserted, 0)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM events").fetchone()[0], 2)
        event = self.connection.execute("SELECT * FROM events ORDER BY id LIMIT 1").fetchone()
        origin = self.connection.execute("SELECT * FROM event_sources WHERE event_id=?", (event["id"],)).fetchone()
        source = self.connection.execute("SELECT * FROM sources WHERE id=?", (SOURCE_ID,)).fetchone()
        self.assertEqual(source["home_url"], GDELT_PROJECT_URL)
        self.assertIsNone(event["occurred_at"])
        self.assertIsNone(event["published_at"])
        self.assertEqual(event["collected_at"], "2026-10-20T09:00:00.000000Z")
        self.assertIsNone(event["latitude"])
        self.assertIsNone(event["longitude"])
        self.assertIsNone(origin["country"])
        self.assertEqual(origin["original_url"], "https://example.test/pl/wiadomosc/alpha")
        self.assertEqual(origin["language"], "Polish")
        self.assertEqual(origin["last_seen_at"], "2026-10-20T10:01:00.000000Z")

    def test_malformed_and_all_invalid_payloads_fail_run_not_empty(self):
        broken = (
            "{bad json",
            [],
            {},
            {"articles": [None]},
            {"articles": [{"url": "https://example.test/no-title"}]},
            {"articles": [{"title": "x", "url": "file:///private"}]},
            {"articles": [{"title": "x", "url": "https://example.test/x", "seendate": "bad"}]},
            {"articles": [{"title": "x", "url": "https://example.test/x"},
                           {"title": "y", "url": "https://example.test/y"}]},
        )
        for payload in broken:
            with self.subTest(payload=payload), self.assertRaises(GDELTDocError):
                ingest_payload(self.connection, payload, collected_at="2026-10-20T10:00:00Z", max_records=1)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM events").fetchone()[0], 0)
        self.assertEqual(self.connection.execute("SELECT status FROM sources WHERE id=?", (SOURCE_ID,)).fetchone()[0], "failed")
        self.assertTrue(all(row[0] == "failed" for row in self.connection.execute("SELECT status FROM ingest_runs")))


if __name__ == "__main__":
    unittest.main()
