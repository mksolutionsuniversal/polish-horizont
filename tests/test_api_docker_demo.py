"""Offline checks for the opt-in synthetic API Docker LAB."""
from __future__ import annotations

from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

import generate_api_demo  # noqa: E402
from horizont.core.queries import list_recent_events, open_readonly  # noqa: E402
from contextlib import closing


class ApiDemoDockerPackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compose = (ROOT / "deploy/lab/compose.api-demo.yaml").read_text(encoding="utf-8")
        cls.image = (ROOT / "deploy/lab/Dockerfile.api-demo").read_text(encoding="utf-8")
        cls.ignore = (ROOT / "deploy/lab/Dockerfile.api-demo.dockerignore").read_text(encoding="utf-8")

    def test_demo_is_separate_private_project_with_no_mounts_or_restart(self):
        for keyword in (
            "name: horizont-api-lab", "127.0.0.1:15481:15481",
            "restart: \"no\"", "read_only: true",
            "pids_limit: 64", "mem_limit: 128m", "cpus: 0.5",
            "cap_drop:", "no-new-privileges:true",
            "HORIZONT_API_TOKEN:", "set_private_demo_token",
            "synthetic.sqlite", "internal: false",
        ):
            self.assertIn(keyword, self.compose)
        self.assertNotIn("volumes:", self.compose)
        self.assertNotIn("privileged: true", self.compose)
        self.assertNotIn("network_mode: host", self.compose)
        self.assertNotIn("docker.sock", self.compose)

    def test_docker_build_context_only_allows_approved_source(self):
        self.assertIn("USER 10001:10001", self.image)
        self.assertIn("python /opt/horizont/tools/generate_api_demo.py", self.image)
        self.assertIn("**", self.ignore)
        for path in (
            "src/horizont/core/db.py", "src/horizont/core/queries.py",
            "src/horizont/api/readonly.py", "tools/generate_api_demo.py"
        ):
            self.assertIn("!" + path, self.ignore)
        self.assertNotIn("!.env", self.ignore)

    def test_generated_database_is_fictional_pending_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "demo.sqlite"
            with patch.object(sys, "argv", ["generate_api_demo.py", str(path)]):
                generate_api_demo.main()
                with self.assertRaises(SystemExit):
                    generate_api_demo.main()
            with closing(open_readonly(path)) as conn:
                records = list_recent_events(conn)["items"]
                self.assertEqual(len(records), 2)
                self.assertTrue(all(e["validation_status"] == "pending" for e in records))
                self.assertTrue(all(e["published_at"] is None for e in records))
                self.assertTrue(all(e["occurred_at"] is None for e in records))
                self.assertTrue(all("fictional" in e["title"].lower() or "fikcyjne" in e["title"].lower() for e in records))
            self.assertTrue(path.exists())

    def test_current_preview_compose_remains_independent(self):
        preview = (ROOT / "deploy/lab/compose.yaml").read_text()
        self.assertIn("name: horizont-lab", preview)
        self.assertIn("127.0.0.1:${HORIZONT_LAB_PORT:-15480}:8080", preview)
        self.assertNotIn("HORIZONT_API_TOKEN", preview)
        self.assertNotIn("horizont-api-lab", preview)


if __name__ == "__main__":
    unittest.main()
