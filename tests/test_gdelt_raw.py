"""Offline tests for GDELT v2 raw manifest and safe ZIP table reading."""

from dataclasses import replace
from pathlib import Path
import hashlib
import io
import sys
import tempfile
import unittest
from zipfile import ZipFile, ZIP_DEFLATED

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from horizont.adapters.gdelt_raw import (  # noqa: E402
    GDELTRawError,
    RawEntry,
    parse_manifest,
    parse_zip_rows,
)


BOUNDARY = "20261020000000"
HOST = "data.gdeltproject.org"
SUFFIXES = ("export.CSV", "mentions.CSV", "gkg.csv")


def synthetic_manifest(*, stream="english", timestamp=BOUNDARY):
    middle = ".translation" if stream == "translation" else ""
    return "\n".join(
        f"123 {('a' * 32)} http://{HOST}/gdeltv2/{timestamp}{middle}.{suffix}.zip"
        for suffix in SUFFIXES
    ) + "\n"


class GDELTRawManifestTests(unittest.TestCase):
    def test_three_archives_timestamp_and_https_upgrade(self):
        parsed = parse_manifest(synthetic_manifest())
        self.assertEqual(parsed.timestamp, BOUNDARY)
        self.assertEqual([e.kind for e in parsed.entries], ["events", "mentions", "gkg"])
        self.assertTrue(all(e.url.startswith("https://data.gdeltproject.org/") for e in parsed.entries))
        self.assertEqual(parsed.entries[0].compressed_bytes, 123)
        self.assertEqual(parsed.entries[0].md5_hex, "a" * 32)

    def test_translation_is_separate_manifest_stream(self):
        trans = parse_manifest(synthetic_manifest(stream="translation"), stream="translation")
        self.assertEqual(trans.entries[2].stream, "translation")
        self.assertIn(".translation.gkg.csv.zip", trans.entries[2].url)
        with self.assertRaisesRegex(GDELTRawError, "stream"):
            parse_manifest(synthetic_manifest(stream="translation"))

    def test_checkpoint_rejects_duplicate_and_old_intervals(self):
        for checkpoint in (BOUNDARY, "20261020001500"):
            with self.subTest(checkpoint=checkpoint):
                with self.assertRaisesRegex(GDELTRawError, "stale"):
                    parse_manifest(synthetic_manifest(), last_timestamp=checkpoint)
        self.assertEqual(
            parse_manifest(synthetic_manifest(), last_timestamp="20261019234500").timestamp,
            BOUNDARY,
        )

    def test_reject_bad_hosts_userinfo_fragments_ports_and_paths(self):
        valid = synthetic_manifest()
        variations = (
            valid.replace(HOST, "attacker.test", 1),
            valid.replace(HOST, HOST + ":8443", 1),
            valid.replace(HOST, "user@" + HOST, 1),
            valid.replace(".export.CSV.zip", ".export.CSV.zip?token=x", 1),
            valid.replace(".export.CSV.zip", ".export.CSV.zip#frag", 1),
            valid.replace("/gdeltv2/", "/gdeltv2/../", 1),
            valid.replace("http://", "ftp://", 1),
        )
        for item in variations:
            with self.subTest(item=item[:100]), self.assertRaises(GDELTRawError):
                parse_manifest(item)

    def test_reject_missing_duplicate_and_mixed_boundaries(self):
        valid = synthetic_manifest()
        lines = valid.splitlines()
        for candidate in (
            "\n".join(lines[:2]),
            "\n".join(lines + lines[:1]),
            "\n".join([lines[0], lines[0], lines[2]]),
            "\n".join([lines[0].replace(BOUNDARY, "20261020001500"), *lines[1:]]),
            valid.replace(BOUNDARY, "20261020000700"),
            valid.replace(BOUNDARY, "20261320000000"),
            valid.replace("123 ", "0 ", 1),
            valid.replace("123 ", "9999999999 ", 1),
            valid.replace("a" * 32, "no-digest", 1),
        ):
            with self.subTest(candidate=candidate[:90]), self.assertRaises(GDELTRawError):
                parse_manifest(candidate)

    def test_manifest_rejects_unbounded_or_invalid_encoding(self):
        with self.assertRaises(GDELTRawError):
            parse_manifest(b"\xff\xfe")
        with self.assertRaisesRegex(GDELTRawError, "size limit"):
            parse_manifest(synthetic_manifest() + " " * 4096)
        with self.assertRaises(GDELTRawError):
            parse_manifest(synthetic_manifest(), stream="unspecified")


class GDELTRawZipTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.entry = parse_manifest(synthetic_manifest()).entries[0]
        self.path = Path(self.tmp.name) / "sample.zip"

    def make_zip(self, rows=b"1\ta\tb\n2\tc\td\n", *, member=None):
        member = member or self.entry.url.rsplit("/", 1)[-1][:-4]
        with ZipFile(self.path, "w", compression=ZIP_DEFLATED) as z:
            z.writestr(member, rows)
        content = self.path.read_bytes()
        return replace(
            self.entry, compressed_bytes=len(content),
            md5_hex=hashlib.md5(content, usedforsecurity=False).hexdigest(),
        )

    def test_bounded_synthetic_rows(self):
        entry = self.make_zip()
        self.assertEqual(
            parse_zip_rows(self.path, entry, expected_columns=3),
            (("1", "a", "b"), ("2", "c", "d")),
        )

    def test_reject_md5_and_archive_size_changes(self):
        entry = self.make_zip()
        with self.assertRaisesRegex(GDELTRawError, "MD5"):
            parse_zip_rows(self.path, replace(entry, md5_hex="0" * 32))
        with self.assertRaisesRegex(GDELTRawError, "byte count"):
            parse_zip_rows(self.path, replace(entry, compressed_bytes=entry.compressed_bytes + 1))

    def test_reject_wrong_member_and_traversal(self):
        for bad in ("../path", "folder/" + self.entry.url.rsplit("/", 1)[-1][:-4], "other.CSV"):
            with self.subTest(name=bad):
                entry = self.make_zip(member=bad)
                with self.assertRaisesRegex(GDELTRawError, "unsafe ZIP"):
                    parse_zip_rows(self.path, entry)

    def test_reject_multiple_members(self):
        entry = self.make_zip()
        with ZipFile(self.path, "a") as z:
            z.writestr("unexpected", "x")
        changed = self.path.read_bytes()
        entry = replace(entry, compressed_bytes=len(changed), md5_hex=hashlib.md5(changed, usedforsecurity=False).hexdigest())
        with self.assertRaisesRegex(GDELTRawError, "exactly one member"):
            parse_zip_rows(self.path, entry)

    def test_reject_expanded_size_ratio_and_line_limits(self):
        entry = self.make_zip(rows=b"X" * 100000)
        with self.assertRaises(GDELTRawError):
            parse_zip_rows(self.path, entry, max_uncompressed_bytes=1024)
        with self.assertRaises(GDELTRawError):
            parse_zip_rows(self.path, entry, max_uncompressed_bytes=100000)

    def test_reject_corrupt_utf8_rows_and_excess_row_count(self):
        entry = self.make_zip(rows=b"\xff\tbad\n")
        with self.assertRaisesRegex(GDELTRawError, "UTF-8"):
            parse_zip_rows(self.path, entry)
        entry = self.make_zip(rows=b"one\ta\nb\tc\n")
        with self.assertRaisesRegex(GDELTRawError, "column"):
            parse_zip_rows(self.path, entry, expected_columns=3)
        with self.assertRaisesRegex(GDELTRawError, "row limit"):
            parse_zip_rows(self.path, entry, max_rows=1)

    def test_reject_invalid_zip_and_symlink_path(self):
        self.path.write_bytes(b"not a zip")
        content = self.path.read_bytes()
        entry = replace(self.entry, compressed_bytes=len(content),
                        md5_hex=hashlib.md5(content, usedforsecurity=False).hexdigest())
        with self.assertRaisesRegex(GDELTRawError, "corrupt"):
            parse_zip_rows(self.path, entry)
        symlink = Path(self.tmp.name) / "link.zip"
        symlink.symlink_to(self.path)
        with self.assertRaisesRegex(GDELTRawError, "regular local file"):
            parse_zip_rows(symlink, entry)

    def test_no_network_or_storage_dependencies(self):
        import inspect
        import horizont.adapters.gdelt_raw as module
        source = inspect.getsource(module)
        self.assertNotIn("urlopen(", source)
        self.assertNotIn("requests.", source)
        self.assertNotIn("extractall(", source)
        self.assertNotIn("sqlite3.", source)


if __name__ == "__main__":
    unittest.main()
