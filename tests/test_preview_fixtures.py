"""Validate that the static dashboard contains only safe synthetic fixtures."""

from datetime import datetime
from html.parser import HTMLParser
import json
import shutil
import subprocess
from pathlib import Path
import unittest
from urllib.parse import urlsplit


PREVIEW = Path(__file__).resolve().parents[1] / "preview"
EXPECTED_SOURCE_STATES = {"ok", "empty", "stale", "failed", "unavailable", "never_run"}
TIMESTAMP_FIELDS = ("occurred_at", "published_at", "collected_at")


class FixtureParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_fixture = False
        self.parts = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "script" and attributes.get("id") == "fixture-data":
            self.in_fixture = True

    def handle_endtag(self, tag):
        if tag == "script" and self.in_fixture:
            self.in_fixture = False

    def handle_data(self, data):
        if self.in_fixture:
            self.parts.append(data)


def read_fixtures():
    parser = FixtureParser()
    parser.feed((PREVIEW / "index.html").read_text(encoding="utf-8"))
    return json.loads("".join(parser.parts))


class PreviewFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (PREVIEW / "index.html").read_text(encoding="utf-8")
        cls.script = (PREVIEW / "app.js").read_text(encoding="utf-8")
        cls.fixtures = read_fixtures()

    def test_data_is_explicitly_synthetic_with_bilingual_uk_and_pl_examples(self):
        self.assertIs(self.fixtures["synthetic"], True)
        countries = set()
        for event in self.fixtures["events"]:
            self.assertTrue(event["title"]["pl"])
            self.assertTrue(event["title"]["en"])
            self.assertTrue(event["summary"]["pl"])
            self.assertTrue(event["summary"]["en"])
            countries.update(source["country"] for source in event["sources"] if source["country"])
        self.assertTrue({"PL", "GB"}.issubset(countries))

    def test_timestamp_fields_are_independent_utc_or_null(self):
        saw_missing_event_and_publication = False
        for event in self.fixtures["events"]:
            self.assertTrue(set(TIMESTAMP_FIELDS).issubset(event))
            for field in TIMESTAMP_FIELDS:
                value = event[field]
                if value is not None:
                    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                    self.assertIsNotNone(parsed.utcoffset())
            if event["occurred_at"] is None and event["published_at"] is None:
                saw_missing_event_and_publication = True
        self.assertTrue(saw_missing_event_and_publication)

    def test_all_source_state_examples_are_present(self):
        self.assertEqual({source["status"] for source in self.fixtures["sources"]}, EXPECTED_SOURCE_STATES)
        for source in self.fixtures["sources"]:
            self.assertTrue(source["detail"]["pl"])
            self.assertTrue(source["detail"]["en"])
        failed = next(source for source in self.fixtures["sources"] if source["status"] == "failed")
        unavailable = next(source for source in self.fixtures["sources"] if source["status"] == "unavailable")
        self.assertIn("unknown", failed["detail"]["en"])
        self.assertIn("zero events", unavailable["detail"]["en"])

    def test_source_provenance_uses_example_test_and_nullable_geolocation(self):
        self.assertTrue(any(event["geo_precision"] is None for event in self.fixtures["events"]))
        for event in self.fixtures["events"]:
            self.assertTrue(event["sources"])
            for source in event["sources"]:
                self.assertEqual(urlsplit(source["url"]).hostname, "example.test")
                self.assertIn(source["country"], {"PL", "GB"})

    @unittest.skipUnless(shutil.which("node"), "Node.js not installed in this environment")
    def test_preview_javascript_executes_with_bilingual_and_filter_smoke(self):
        script = Path(__file__).with_name("test_preview_runtime.cjs")
        result = subprocess.run(
            ["node", "--check", str(PREVIEW / "app.js")],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        result = subprocess.run(
            ["node", str(script)],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Preview JS smoke PASS", result.stdout)

    def test_preview_uses_only_local_assets_and_no_network_api(self):
        self.assertIn('href="styles.css"', self.html)
        self.assertIn('src="app.js"', self.html)
        self.assertNotRegex(self.html, r"(?:src|href)=['\"]https?://")
        self.assertNotRegex(self.script, r"\b(?:fetch|XMLHttpRequest|WebSocket)\s*\(")


if __name__ == "__main__":
    unittest.main()
