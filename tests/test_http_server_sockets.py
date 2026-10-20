"""Real socket regressions for blocked HTTP connections (synthetic, loopback only)."""

from __future__ import annotations

import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from wsgiref.simple_server import make_server

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from horizont.api.readonly import (  # noqa: E402
    BoundedThreadingWSGIServer,
    MAX_ACTIVE_CONNECTIONS,
    create_app,
)
from horizont.core.db import add_event, connect, create_source, migrate  # noqa: E402

DEMO_TOKEN = "test-only-bearer-token-for-socket-regression-2026"


class ApiStalledSocketTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        database = Path(directory.name) / "synthetic.sqlite"
        writer = connect(database)
        try:
            migrate(writer)
            create_source(writer, "fixture", "Fictional source")
            add_event(
                writer, source_id="fixture", original_url="https://example.test/item",
                dedup_key="fixture:1", title="Fictional event",
                collected_at="2026-10-20T10:00:00Z",
            )
        finally:
            writer.close()

        # Bind ephemeral loopback port; no Docker, network fetch or Mac services.
        self.server = make_server(
            "127.0.0.1", 0, create_app(database, DEMO_TOKEN),
            server_class=BoundedThreadingWSGIServer,
        )
        self.server.socket_timeout_seconds = 0.7
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.addCleanup(self._close)

    def _close(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(timeout=2)

    def _read(self, path, *, authenticated=False):
        headers = {"Authorization": "Bearer " + DEMO_TOKEN} if authenticated else {}
        request = urllib.request.Request(self.base + path, headers=headers)
        with urllib.request.urlopen(request, timeout=2) as response:
            return response.status, json.load(response)

    def test_incomplete_http_headers_do_not_block_health_or_authenticated_query(self):
        blocker = socket.create_connection(("127.0.0.1", self.server.server_port), timeout=2)
        self.addCleanup(blocker.close)
        # No CRLFCRLF terminator, so the server must keep waiting for this client.
        blocker.sendall(b"GET /health HTTP/1.1\r\nHost: test\r\n")
        time.sleep(0.12)
        self.assertEqual(self._read("/health")[0], 200)
        status, payload = self._read("/api/v1/events", authenticated=True)
        self.assertEqual(status, 200)
        self.assertEqual([i["dedup_key"] for i in payload["items"]], ["fixture:1"])

    def test_incomplete_socket_releases_slot_after_timeout(self):
        blocker = socket.create_connection(("127.0.0.1", self.server.server_port), timeout=2)
        self.addCleanup(blocker.close)
        blocker.sendall(b"GET /health HTTP/1.1\r\nHost: test\r\n")
        time.sleep(0.12)
        self.assertEqual(self._read("/health")[0], 200)
        time.sleep(0.9)  # more than short test-only socket timeout
        self.assertEqual(self._read("/health")[0], 200)

    def test_concurrent_connection_count_is_bounded(self):
        self.assertEqual(MAX_ACTIVE_CONNECTIONS, 8)
        self.assertEqual(self.server._connection_slots._value, MAX_ACTIVE_CONNECTIONS)


if __name__ == "__main__":
    unittest.main()
