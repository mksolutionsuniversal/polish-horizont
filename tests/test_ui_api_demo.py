"""Synthetic-only unit tests for opt-in HORIZONT same-origin SQLite UI."""
from __future__ import annotations

import io
import json
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
import unittest
from wsgiref.util import setup_testing_defaults

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

import generate_api_demo  # noqa: E402
from horizont.ui_demo.server import create_app, read_snapshot, _preview_fixture  # noqa: E402


def request(app, path="/", *, method="GET", host="127.0.0.1:15482",
            origin=None, site=None, query=""):
    env = {}
    setup_testing_defaults(env)
    env.update({"REQUEST_METHOD": method, "PATH_INFO": path,
                "QUERY_STRING": query, "HTTP_HOST": host, "wsgi.input": io.BytesIO()})
    if origin is not None:
        env["HTTP_ORIGIN"] = origin
    if site is not None:
        env["HTTP_SEC_FETCH_SITE"] = site
    response = {}

    def start(status, headers):
        response["status"] = int(status.split()[0])
        response["headers"] = dict(headers)

    body = b"".join(app(env, start))
    return response["status"], body, response["headers"]


class SyntheticUiSqliteTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.db = Path(temp.name) / "fictional.sqlite"
        old_argv = sys.argv[:]
        try:
            sys.argv = ["generate_api_demo.py", str(self.db)]
            generate_api_demo.main()
        finally:
            sys.argv = old_argv
        self.app = create_app(self.db, ROOT / "preview")

    def test_one_read_snapshot_has_two_sql_records_and_provenance(self):
        snap = read_snapshot(self.db)
        self.assertTrue(snap["synthetic"])
        self.assertEqual(len(snap["items"]), 2)
        self.assertEqual(len(snap["sources"]), 2)
        self.assertEqual(snap["country_filter_semantics"], "source_observed_country")
        for item in snap["items"]:
            self.assertEqual(item["validation_status"], "pending")
            self.assertIsNone(item["occurred_at"])
            self.assertIsNone(item["published_at"])
            self.assertEqual(len(item["sources"]), 1)
            self.assertIn(item["sources"][0]["country"], ("GB", "PL"))
            self.assertIn(item["sources"][0]["language"], ("pl", "en"))
            self.assertNotIn("source_language", item)

    def test_page_retains_layout_with_sql_records_not_embedded_preview_fixtures(self):
        code, body, h = request(self.app)
        self.assertEqual(code, 200)
        self.assertIn("text/html", h["Content-Type"])
        text = body.decode()
        self.assertIn('class="app-shell"', text)
        self.assertIn('href="styles.css"', text)
        self.assertIn('defer src="app.js"', text)
        self.assertNotIn("demo-001", text)
        self.assertNotIn("HORIZONT_API_TOKEN", text)
        matches = re.findall(r'<script id="fixture-data" type="application/json">(.*?)</script>', text, flags=re.S)
        self.assertEqual(len(matches), 1)
        fixture = json.loads(matches[0])
        self.assertTrue(fixture["synthetic"])
        self.assertEqual(len(fixture["events"]), 2)
        self.assertEqual(len(fixture["sources"]), 2)
        self.assertEqual(set(e["id"] for e in fixture["events"]), {"sql-1", "sql-2"})
        self.assertTrue(all(e["title"]["pl"] == e["title"]["en"] for e in fixture["events"]))
        self.assertTrue(all(e["sources"] for e in fixture["events"]))
        self.assertEqual({s["status"] for s in fixture["sources"]}, {"never_run"})

    def test_same_origin_data_routes_read_only_and_no_cors(self):
        code, body, headers = request(self.app, "/data/v1/events")
        self.assertEqual(code, 200)
        events = json.loads(body)
        self.assertEqual(len(events["items"]), 2)
        self.assertTrue(events["synthetic"])
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        self.assertEqual(headers["Cache-Control"], "no-store")
        src_code, src_body, _ = request(self.app, "/data/v1/sources")
        self.assertEqual(src_code, 200)
        self.assertEqual(len(json.loads(src_body)["items"]), 2)
        before = self.db.stat().st_size
        for _ in range(3):
            self.assertEqual(request(self.app, "/data/v1/events")[0], 200)
        self.assertEqual(before, self.db.stat().st_size)

    def test_bad_host_cross_site_origin_and_post_are_rejected(self):
        for kwargs in (
            {"host": "evil.example:15482"}, {"host": "127.0.0.1:9999"},
            {"origin": "http://evil.example"}, {"origin": "https://127.0.0.1:15482"},
            {"site": "cross-site"}, {"site": "same-site"},
        ):
            with self.subTest(**kwargs):
                self.assertEqual(request(self.app, "/data/v1/events", **kwargs)[0], 400)
        code, body, _ = request(self.app, method="POST")
        self.assertEqual(code, 405)
        self.assertIn(b"method_not_allowed", body)
        self.assertEqual(request(self.app, "/data/v1/events", query="q=invalid")[0], 400)
        self.assertEqual(request(self.app, "/etc/passwd")[0], 404)

    def test_missing_db_is_503_not_fake_zero_or_fallback_preview(self):
        missing = create_app(Path(self.db.parent) / "missing.sqlite", ROOT / "preview")
        for path in ("/", "/data/v1/events", "/data/v1/sources"):
            status, body, _ = request(missing, path)
            self.assertEqual(status, 503)
            self.assertEqual(json.loads(body)["error"], "data_unavailable")
            self.assertNotIn(b"demo-001", body)

    def test_head_health_and_static_label_are_safe(self):
        self.assertEqual(request(self.app, "/health")[0], 200)
        s, b, h = request(self.app, "/", method="HEAD")
        self.assertEqual(s, 200)
        self.assertEqual(b, b"")
        self.assertTrue(int(h["Content-Length"]) > 9000)
        status, js, _ = request(self.app, "/app.js")
        self.assertEqual(status, 200)
        self.assertIn(b"SQLITE DEMO", js)
        self.assertIn(b"textContent", js)
        self.assertNotIn(b"HORIZONT_API_TOKEN", js)
        status, css, _ = request(self.app, "/styles.css")
        self.assertEqual(status, 200)
        self.assertGreater(len(css), 1000)

    def test_script_breakout_from_db_is_escaped(self):
        conn = sqlite3.connect(self.db)
        try:
            conn.execute("UPDATE events SET title = ? WHERE id=1",
                         ('</script><script>alert("x")</script>',))
            conn.commit()
        finally:
            conn.close()
        _, body, _ = request(self.app)
        text = body.decode()
        self.assertNotIn('</script><script>alert', text)
        match = re.search(r'<script id="fixture-data" type="application/json">(.*?)</script>', text, re.S)
        self.assertIsNotNone(match)
        fixture = json.loads(match.group(1))
        self.assertIn('</script><script>', fixture["events"][1]["title"]["pl"])

    def test_current_production_like_preview_compose_files_unchanged(self):
        old = (ROOT / "deploy/lab/compose.yaml").read_text()
        api = (ROOT / "deploy/lab/compose.api-demo.yaml").read_text()
        new = (ROOT / "deploy/lab/compose.ui-api-demo.yaml").read_text()
        self.assertIn("name: horizont-lab", old)
        self.assertIn("name: horizont-api-lab", api)
        self.assertIn("name: horizont-ui-api-lab", new)
        self.assertIn("127.0.0.1:15482:15482", new)
        self.assertIn("no-new-privileges:true", new)
        self.assertIn("read_only: true", new)
        self.assertIn('restart: "no"', new)
        self.assertNotIn("volumes:", new)
        self.assertNotIn("HORIZONT_API_TOKEN", new)
        self.assertNotIn("docker.sock", new)


if __name__ == "__main__":
    unittest.main()
