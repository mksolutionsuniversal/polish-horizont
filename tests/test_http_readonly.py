"""Synthetic WSGI tests. Never start a real listener or use a real user DB."""
from __future__ import annotations

import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from wsgiref.util import setup_testing_defaults

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from horizont.api.readonly import create_app  # noqa: E402
from horizont.core.db import add_event, create_source, migrate, connect, start_ingest_run, finish_ingest_run  # noqa: E402


TOKEN = "synthetic-secret-token-for-unit-tests-only-00000"


def invoke(app, path="/health", *, token=TOKEN, method="GET", query=""):
    environ = {}
    setup_testing_defaults(environ)
    environ.update({
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "QUERY_STRING": query,
        "wsgi.input": io.BytesIO(b""),
    })
    if token is not None:
        environ["HTTP_AUTHORIZATION"] = "Bearer " + token
    captured = {}

    def start(status, headers):
        captured["status"] = status
        captured["headers"] = dict(headers)

    data = b"".join(app(environ, start))
    result = json.loads(data) if data else None
    return int(captured["status"].split()[0]), result, captured["headers"]


class ReadonlyHttpApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "sample.db"
        writer = connect(self.db)
        try:
            migrate(writer)
            create_source(writer, "synthetic-pl", "Synthetic PL")
            create_source(writer, "synthetic-gb", "Synthetic GB")
            self.item_id, _ = add_event(
                writer, source_id="synthetic-pl",
                dedup_key="synthetic-event-a", original_url="https://example.test/news/a",
                title="Fictional event", collected_at="2026-10-20T12:00:00Z",
                published_at=None, occurred_at=None,
                country="PL", language="pl", validation_status="pending",
            )
            add_event(
                writer, source_id="synthetic-gb",
                dedup_key="synthetic-event-b", original_url="https://example.test/news/b",
                title="Another fictional event", collected_at="2026-10-20T13:00:00Z",
                country="GB", language="en",
            )
            run = start_ingest_run(writer, "synthetic-gb", started_at="2026-10-20T12:30:00Z")
            finish_ingest_run(writer, run, "failed", finished_at="2026-10-20T12:31:00Z",
                              error_message="sensitive upstream text must not leak")
        finally:
            writer.close()
        self.api = create_app(self.db, TOKEN)

    def test_health_is_generic_and_never_exposes_db_path(self):
        status, payload, headers = invoke(self.api, token=None)
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "running")
        self.assertNotIn(str(self.db), str(payload))
        self.assertEqual(headers["Cache-Control"], "no-store")

    def test_data_endpoints_require_bearer_even_on_localhost(self):
        for path in ("/api/v1/events", "/api/v1/events/1", "/api/v1/sources", "/ready"):
            for value in (None, "bad-token", "synthetic-secret-token-for-unit-tests-only-00001"):
                with self.subTest(path=path, token=value):
                    status, payload, _ = invoke(self.api, path=path, token=value)
                    self.assertEqual(status, 401)
                    self.assertEqual(payload["error"], "unauthorized")
        with self.assertRaises(ValueError):
            create_app(self.db, "short")

    def test_events_paginate_and_do_not_invent_unknown_dates(self):
        status, payload, headers = invoke(self.api, "/api/v1/events", query="country=PL&limit=1&time_field=occurred_at")
        self.assertEqual(status, 200)
        self.assertEqual(len(payload["items"]), 1)
        self.assertIsNone(payload["items"][0]["occurred_at"])
        self.assertIsNone(payload["items"][0]["published_at"])
        self.assertEqual(payload["items"][0]["validation_status"], "pending")
        self.assertEqual(payload["country_filter_semantics"], "source_observed_country")
        self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_detail_includes_provenance_and_id_filter(self):
        status, payload, _ = invoke(self.api, f"/api/v1/events/{self.item_id}")
        self.assertEqual(status, 200)
        self.assertEqual(payload["sources"][0]["original_url"], "https://example.test/news/a")
        self.assertEqual(payload["sources"][0]["country"], "PL")
        self.assertEqual(invoke(self.api, "/api/v1/events/999999")[0], 404)
        self.assertEqual(invoke(self.api, "/api/v1/events/0")[0], 404)
        self.assertEqual(invoke(self.api, "/api/v1/events/1", query="extra=true")[0], 400)

    def test_sources_paginate_and_omit_raw_errors(self):
        status, data, _ = invoke(self.api, "/api/v1/sources", query="offset=0&limit=1")
        self.assertEqual(status, 200)
        self.assertEqual(len(data["items"]), 1)
        self.assertEqual(data["items"][0]["source_status"], "failed")
        self.assertNotIn("sensitive upstream text", str(data))
        self.assertEqual(data["offset"], 0)
        second = invoke(self.api, "/api/v1/sources", query="offset=1&limit=1")[1]
        self.assertEqual(second["items"][0]["source_id"], "synthetic-pl")

    def test_invalid_query_fields_duplicates_and_bounds_fail_closed(self):
        samples = (
            "limit=0", "limit=101", "limit=-1", "offset=-1", "offset=100001",
            "limit=1&limit=2", "q=DROP%20TABLE", "country=pl", "start=2026-10-20",
            "start=2026-10-20T12%3A00%3A00Z&end=2026-10-19T12%3A00%3A00Z",
            "source_id=", "time_field=collection", "limit=a", "limit=",
            "limit=" + "1" * 2100
        )
        for query in samples:
            with self.subTest(query=query[:40]):
                self.assertEqual(invoke(self.api, "/api/v1/events", query=query)[0], 400)

    def test_missing_db_and_corrupt_schema_are_unavailable_not_empty(self):
        missing_app = create_app(Path(self.tmp.name) / "missing.sqlite", TOKEN)
        for path in ("/ready", "/api/v1/events", "/api/v1/sources"):
            status, data, _ = invoke(missing_app, path)
            self.assertEqual(status, 503)
            self.assertEqual(data["error"], "data_unavailable")
        self.assertFalse((Path(self.tmp.name) / "missing.sqlite").exists())
        empty = Path(self.tmp.name) / "empty.db"
        sqlite3.connect(empty).close()
        self.assertEqual(invoke(create_app(empty, TOKEN), "/ready")[0], 503)

    def test_only_get_and_head_and_no_cors(self):
        self.assertEqual(invoke(self.api, "/api/v1/events", method="POST")[0], 405)
        s, payload, headers = invoke(self.api, "/api/v1/events", method="HEAD")
        self.assertEqual(s, 200)
        self.assertIsNone(payload)
        self.assertEqual(headers["Referrer-Policy"], "no-referrer")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(headers["Content-Security-Policy"], "default-src 'none'")

    def test_query_does_not_mutate_db_and_has_no_external_transport(self):
        before = self.db.stat().st_size
        for _ in range(3):
            self.assertEqual(invoke(self.api, "/api/v1/events")[0], 200)
            self.assertEqual(invoke(self.api, "/api/v1/sources")[0], 200)
        self.assertEqual(before, self.db.stat().st_size)
        code = (Path(__file__).resolve().parents[1] / "src/horizont/api/readonly.py").read_text()
        for banned in ("urlopen(", "sqlite3.connect(", "subprocess", "create_source(",
                       "migrate(", "Access-Control-Allow-Origin"):
            self.assertNotIn(banned, code)


if __name__ == "__main__":
    unittest.main()
