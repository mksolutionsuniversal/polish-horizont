"""Offline synthetic tests for separate writer, sealed read-view and recovery volumes."""

from __future__ import annotations

from contextlib import closing
import os
from pathlib import Path
import sqlite3
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from horizont.core.db import add_event
from horizont.core.lab_storage.operations import (
    VolumePaths, _exclusive_writer, _primary_connection,
    restore_action, summary, writer_action,
)
from horizont.core.persistence import verify_existing
from horizont.core.queries import (
    get_event_detail, list_recent_events, open_readonly,
)


class PersistentVolumeLabTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        for directory in ("primary", "view", "backup", "recovered"):
            (root / directory).mkdir()
        self.paths = VolumePaths(
            primary=root / "primary" / "primary.sqlite",
            view=root / "view" / "view-v1.sqlite",
            backup=root / "backup" / "backup-v1.sqlite",
            recovery=root / "recovered" / "restored-v1.sqlite",
        )

    def _init_seed(self):
        writer_action("init", self.paths)
        writer_action("seed", self.paths)

    def test_full_roundtrip_and_provenance(self):
        self._init_seed()
        self.assertEqual(stat.S_IMODE(self.paths.primary.stat().st_mode), 0o600)
        self.assertEqual(summary(self.paths.primary),
                         {"events": 2, "sources": 2, "provenance": 3})
        writer_action("publish", self.paths)
        writer_action("backup", self.paths)
        restore_action(self.paths)
        for path in (self.paths.view, self.paths.backup, self.paths.recovery):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(verify_existing(path).schema_version, 2)
            self.assertEqual(summary(path),
                             {"events": 2, "sources": 2, "provenance": 3})
            self.assertFalse(Path(str(path) + "-wal").exists())
            with closing(open_readonly(path)) as reader:
                events = list_recent_events(reader)["items"]
                self.assertTrue(all(e["occurred_at"] is None for e in events))
                self.assertTrue(all(e["published_at"] is None for e in events))
                shared = reader.execute(
                    "SELECT id FROM events WHERE dedup_key='lab:fictional:shared'"
                ).fetchone()[0]
                detail = get_event_detail(reader, shared)
                self.assertEqual({s["country"] for s in detail["sources"]},
                                 {"PL", "GB"})
                self.assertEqual({s["language"] for s in detail["sources"]},
                                 {"pl", "en"})
                with self.assertRaises(sqlite3.OperationalError):
                    reader.execute("DELETE FROM events")

    def test_persisted_primary_and_view_not_refreshed_by_silent_write(self):
        self._init_seed()
        writer_action("publish", self.paths)
        with closing(_primary_connection(self.paths.primary)) as conn:
            add_event(
                conn, source_id="lab-pl",
                original_url="https://example.test/lab-pl/new",
                dedup_key="lab:fictional:third",
                title="Another fictional LAB record",
                language="pl", country="PL",
                collected_at="2026-10-20T18:00:00Z",
            )
        self.assertEqual(summary(self.paths.primary)["events"], 3)
        self.assertEqual(summary(self.paths.view)["events"], 2)
        # Stage v1 has a one-time immutable published view, never an implicit
        # replacement while API is serving it.
        with self.assertRaises(FileExistsError):
            writer_action("publish", self.paths)
        self.assertEqual(summary(self.paths.view)["events"], 2)
        writer_action("backup", self.paths)
        restore_action(self.paths)
        self.assertEqual(summary(self.paths.recovery)["events"], 3)
        self.assertEqual(summary(self.paths.primary)["events"], 3)

    def test_no_implicit_init_or_reseed_and_no_overwrite(self):
        with self.assertRaises(ValueError):
            writer_action("seed", self.paths)
        with self.assertRaises(ValueError):
            writer_action("publish", self.paths)
        self.assertFalse(self.paths.primary.exists())
        self._init_seed()
        with self.assertRaises(FileExistsError):
            writer_action("init", self.paths)
        with self.assertRaises(FileExistsError):
            writer_action("seed", self.paths)
        writer_action("backup", self.paths)
        with self.assertRaises(FileExistsError):
            writer_action("backup", self.paths)
        restore_action(self.paths)
        with self.assertRaises(FileExistsError):
            restore_action(self.paths)
        self.assertEqual(summary(self.paths.primary)["events"], 2)

    def test_writer_flock_refuses_concurrent_maintenance(self):
        with _exclusive_writer(self.paths.primary.parent):
            with self.assertRaises(BlockingIOError):
                writer_action("init", self.paths)
        writer_action("init", self.paths)
        self.assertEqual(summary(self.paths.primary)["events"], 0)

    def test_recovery_without_primary_volume_mounted(self):
        self._init_seed()
        writer_action("backup", self.paths)
        # The recovery service must only require backup/recovery volumes.
        independent = VolumePaths(
            primary=self.paths.primary.parent / "NO-PRIMARY-MOUNT" / "db.sqlite",
            view=self.paths.view.parent / "NO-VIEW-MOUNT" / "view.sqlite",
            backup=self.paths.backup,
            recovery=self.paths.recovery,
        )
        restore_action(independent)
        self.assertEqual(summary(independent.recovery)["events"], 2)
        self.assertTrue(self.paths.primary.exists())

    def test_symlink_primary_refused_and_schema_checked(self):
        self._init_seed()
        alias = self.paths.primary.parent / "alias.sqlite"
        alias.symlink_to(self.paths.primary)
        with self.assertRaises(ValueError):
            summary(alias)
        with self.assertRaises(ValueError):
            _primary_connection(alias)
        self.assertEqual(
            self.paths.primary.parent.joinpath(".writer.lock").is_file(), True
        )

    def test_docker_profile_and_volume_separation_stays_explicit(self):
        content = (Path(__file__).resolve().parents[1] /
                   "deploy/lab/compose.storage-lab.yaml").read_text()
        self.assertIn("name: horizont-storage-lab", content)
        self.assertIn('profiles: ["maintenance"]', content)
        self.assertIn('profiles: ["reader"]', content)
        self.assertIn("127.0.0.1:15483:15481", content)
        self.assertIn("read_view:/opt/horizont/view:ro", content)
        self.assertIn("offline_backup:/opt/horizont/backup:ro", content)
        self.assertIn("recovered_data:/opt/horizont/recovered:rw", content)
        self.assertNotIn("docker.sock", content)
        self.assertNotIn("down -v", content)
        self.assertNotIn("15480:", content)
        self.assertNotIn("15482:", content)
        self.assertNotIn("15481:15481", content)


if __name__ == "__main__":
    unittest.main()
