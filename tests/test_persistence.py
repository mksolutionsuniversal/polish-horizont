"""Offline WAL/persistence regression tests; only synthetic temporary files."""

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

from horizont.core.db import add_event, connect, create_source, migrate
from horizont.core.persistence import (
    backup_live, initialize_new, restore_to_new, verify_existing,
)
from horizont.core.queries import (
    get_event_detail, list_recent_events, open_readonly,
)


class SQLitePersistenceTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.db = self.root / "primary.sqlite"
        initialize_new(self.db)

    def _seed(self):
        writer = connect(self.db)
        create_source(writer, "pl", "Fikcyjne źródło PL")
        create_source(writer, "gb", "Fictional source GB")
        event_id, inserted = add_event(
            writer,
            source_id="pl", original_url="https://example.test/pl",
            dedup_key="fixture:dedup:1", title="Fikcyjny wpis",
            language="pl", country="PL", collected_at="2026-10-20T12:00:00Z",
            published_at=None, occurred_at=None,
        )
        self.assertTrue(inserted)
        same, inserted = add_event(
            writer, source_id="gb", original_url="https://example.test/gb",
            dedup_key="fixture:dedup:1", title="Fikcyjny wpis",
            language="en", country="GB", collected_at="2026-10-20T13:00:00Z",
            published_at=None, occurred_at=None,
        )
        self.assertEqual(same, event_id)
        self.assertFalse(inserted)
        return writer, event_id

    def _assert_contents(self, database: Path, event_id: int):
        with closing(open_readonly(database)) as reader:
            events = list_recent_events(reader)["items"]
            self.assertEqual(len(events), 1)
            self.assertIsNone(events[0]["occurred_at"])
            self.assertIsNone(events[0]["published_at"])
            detail = get_event_detail(reader, event_id)
            self.assertEqual({s["country"] for s in detail["sources"]}, {"PL", "GB"})
            self.assertEqual({s["language"] for s in detail["sources"]}, {"en", "pl"})
            self.assertEqual(events[0]["validation_status"], "pending")
            with self.assertRaises(sqlite3.OperationalError):
                reader.execute("DELETE FROM events")

    def test_initialize_requires_new_path_and_sets_private_permissions(self):
        self.assertTrue(self.db.is_file())
        self.assertEqual(stat.S_IMODE(self.db.stat().st_mode), 0o600)
        self.assertEqual(verify_existing(self.db).schema_version, 2)
        with self.assertRaises(FileExistsError):
            initialize_new(self.db)
        dangling = self.root / "dangling.sqlite"
        dangling.symlink_to(self.root / "no-such-file.sqlite")
        with self.assertRaises(FileExistsError):
            initialize_new(dangling)
        self.assertTrue(dangling.is_symlink())

    def test_backup_includes_uncheckpointed_wal_commits_and_restore_round_trip(self):
        writer, event_id = self._seed()
        try:
            self.assertEqual(writer.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            with self.assertRaisesRegex(ValueError, "self-contained"):
                verify_existing(self.db)
            # Leave writer OPEN; this read must see committed events still in WAL.
            backup = self.root / "snapshot.sqlite"
            result = backup_live(self.db, backup)
            self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o600)
            self.assertEqual(verify_existing(backup).sha256, result.sha256)
            self.assertFalse(Path(str(backup) + "-wal").exists())
            self.assertFalse(Path(str(backup) + "-shm").exists())
            self._assert_contents(backup, event_id)

            restored = self.root / "restored.sqlite"
            restore = restore_to_new(backup, restored)
            # SQLite backup/restore can change file header bytes while the data
            # and provenance remain identical; each digest verifies ITS own file.
            self.assertEqual(verify_existing(restored).sha256, restore.sha256)
            self.assertEqual(restore.schema_version, result.schema_version)
            self._assert_contents(restored, event_id)
            self._assert_contents(self.db, event_id)
            self.assertTrue(writer.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1)
        finally:
            writer.close()

    def test_snapshot_is_consistent_with_writer_transaction(self):
        writer, event_id = self._seed()
        try:
            writer.execute("BEGIN IMMEDIATE")
            writer.execute("UPDATE events SET title='UNCOMMITTED' WHERE id=?", (event_id,))
            # Backup reads last COMMITTED snapshot, never dirty transaction.
            backup = self.root / "snapshot-committed.sqlite"
            backup_live(self.db, backup)
            with closing(open_readonly(backup)) as reader:
                title = reader.execute("SELECT title FROM events WHERE id=?", (event_id,)).fetchone()[0]
            self.assertEqual(title, "Fikcyjny wpis")
            writer.rollback()
        finally:
            writer.close()

    def test_no_overwrite_existing_destinations_or_symlinks(self):
        writer, _ = self._seed()
        writer.close()
        backup = self.root / "protected.sqlite"
        backup.write_bytes(b"DO NOT OVERWRITE")
        with self.assertRaises(FileExistsError):
            backup_live(self.db, backup)
        with self.assertRaises(FileExistsError):
            restore_to_new(self.db, backup)
        self.assertEqual(backup.read_bytes(), b"DO NOT OVERWRITE")
        symlink = self.root / "alias.sqlite"
        symlink.symlink_to(self.db)
        with self.assertRaises(FileExistsError):
            backup_live(self.db, symlink)
        with self.assertRaises(ValueError):
            backup_live(symlink, self.root / "nope.sqlite")
        self.assertFalse((self.root / "nope.sqlite").exists())

    def test_corrupt_source_not_restored_and_cleans_stage(self):
        bad = self.root / "invalid.sqlite"
        bad.write_bytes(b"NOT SQLITE CONTENT")
        output = self.root / "restore.sqlite"
        with self.assertRaises((ValueError, sqlite3.DatabaseError)):
            restore_to_new(bad, output)
        self.assertFalse(output.exists())
        self.assertEqual(list(self.root.glob(".horizont-stage-*")), [])

    def test_unmigrated_database_rejected(self):
        wrong = self.root / "wrong.sqlite"
        with closing(sqlite3.connect(wrong)) as db:
            db.execute("CREATE TABLE meaningless(x)")
        with self.assertRaises((ValueError, sqlite3.DatabaseError)):
            backup_live(wrong, self.root / "should-not-exist.sqlite")
        self.assertFalse((self.root / "should-not-exist.sqlite").exists())

    def test_broken_foreign_key_rejected_on_backup(self):
        writer, _ = self._seed()
        writer.close()
        with closing(sqlite3.connect(self.db)) as db:
            db.execute("PRAGMA foreign_keys=OFF")
            db.execute(
                "INSERT INTO event_sources(event_id, source_id, original_url, first_seen_at, last_seen_at) "
                "VALUES (9999,'pl','https://example.test/bad','2026-10-20T00:00:00Z','2026-10-20T00:00:00Z')"
            )
            db.commit()
        with self.assertRaisesRegex(ValueError, "foreign_key_check"):
            backup_live(self.db, self.root / "no-invalid-provenance.sqlite")
        self.assertFalse((self.root / "no-invalid-provenance.sqlite").exists())

    def test_database_not_created_by_verify_or_backup_failures(self):
        missing = self.root / "missing.sqlite"
        for op in (verify_existing,):
            with self.assertRaises(ValueError):
                op(missing)
        with self.assertRaises(ValueError):
            backup_live(missing, self.root / "out.sqlite")
        self.assertFalse(missing.exists())
        self.assertFalse((self.root / "out.sqlite").exists())


if __name__ == "__main__":
    unittest.main()
