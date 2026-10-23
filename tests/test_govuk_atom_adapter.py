"""Offline GOV.UK Atom adapter tests using synthetic example.test records."""

from __future__ import annotations

from pathlib import Path
import sqlite3
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from horizont.adapters.govuk_atom import (  # noqa: E402
    GOVUK_HOME_URL,
    MAX_ENTRIES,
    MAX_XML_BYTES,
    SOURCE_ID,
    GOVUKAtomError,
    canonicalize_article_url,
    ingest_payload,
    parse_feed,
)
from horizont.core import migrate  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "govuk_atom_synthetic.xml"
COLLECTED = "2026-10-21T10:00:00+01:00"


def entry(atom_id: str, title: str, href: str, dates: str = "") -> str:
    return ("<entry><id>" + atom_id + "</id><title>" + title + "</title>"
            '<link rel="alternate" type="text/html" href="' + href + '" />'
            + dates + "</entry>")


def feed(entries: str, namespace: str = "http://www.w3.org/2005/Atom") -> str:
    return (f'<feed xmlns="{namespace}"><id>https://example.test/feed</id>'
            "<title>Synthetic feed</title><updated>2026-10-21T09:00:00Z</updated>"
            f"{entries}</feed>")


class GOVUKAtomAdapterTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        migrate(self.connection)
        self.xml = FIXTURE.read_text(encoding="utf-8")

    def tearDown(self):
        self.connection.close()

    def test_fixture_parses_offline_and_keeps_time_and_verification_semantics(self):
        batch = parse_feed(self.xml, collected_at=COLLECTED)
        self.assertEqual(batch.collected_at, "2026-10-21T09:00:00.000000Z")
        self.assertEqual(len(batch.publications), 2)
        first, second = batch.publications
        self.assertEqual(first.title, "Syntetyczny komunikat urzędowy — ćwiczenie")
        self.assertEqual(first.original_url, "https://example.test/notices/alpha")
        self.assertEqual(first.published_at, "2026-10-20T09:30:00.000000Z")
        self.assertEqual(first.updated_at, "2026-10-21T08:45:00.000000Z")
        self.assertIsNone(first.occurred_at)
        self.assertIsNone(first.country)
        self.assertIsNone(first.latitude)
        self.assertEqual(first.geo_precision, "unknown")
        self.assertEqual(first.validation_status, "pending")
        self.assertEqual(first.source_id, SOURCE_ID)
        self.assertEqual(first.attribution, "GOV.UK")
        self.assertEqual(second.published_at, None)
        self.assertEqual(second.updated_at, None)
        self.assertNotIn("Fikcyjny opis", repr(first))

    def test_canonical_url_and_source_specific_dedup(self):
        first = parse_feed(feed(entry("a", "Alpha", "https://example.test/a#frag")), collected_at=COLLECTED)
        second = parse_feed(feed(entry("b", "Different title", "https://example.test/a")), collected_at=COLLECTED)
        self.assertEqual(first.publications[0].dedup_key, second.publications[0].dedup_key)
        self.assertTrue(first.publications[0].dedup_key.startswith("govuk-news-atom-v1:"))
        self.assertNotEqual(first.publications[0].dedup_key, "gdelt-project-doc-v2:" + first.publications[0].dedup_key.split(":", 1)[1])
        with self.assertRaisesRegex(GOVUKAtomError, "HTTPS"):
            canonicalize_article_url("http://example.test/a")

    def test_empty_feed_is_valid_empty_batch(self):
        result = ingest_payload(self.connection, feed(""), collected_at=COLLECTED)
        self.assertEqual((result.status, result.items_seen, result.items_inserted), ("empty", 0, 0))
        self.assertEqual(self.connection.execute("SELECT status FROM sources WHERE id=?", (SOURCE_ID,)).fetchone()[0], "empty")

    def test_accepts_exact_entry_limit_and_rejects_invalid_collection_time(self):
        entries = "".join(entry(str(i), f"Synthetic {i}", f"https://example.test/{i}") for i in range(MAX_ENTRIES))
        self.assertEqual(len(parse_feed(feed(entries), collected_at=COLLECTED).publications), MAX_ENTRIES)
        with self.assertRaisesRegex(GOVUKAtomError, "collected_at"):
            parse_feed(feed(""), collected_at="2026-10-21T10:00:00")

    def test_observed_twenty_entries_are_not_truncated_and_twenty_one_fail_closed(self):
        # A one-shot GOV.UK Atom response on 2026-10-23 contained 20 entries.
        # These are entirely synthetic example.test URLs, not captured live data.
        self.assertEqual(MAX_ENTRIES, 20)
        accepted = "".join(
            entry(str(i), f"Synthetic publication {i}", f"https://example.test/notices/{i}")
            for i in range(20)
        )
        batch = parse_feed(feed(accepted), collected_at=COLLECTED)
        self.assertEqual(len(batch.publications), 20)
        self.assertEqual(len({p.original_url for p in batch.publications}), 20)
        self.assertTrue(all(p.validation_status == "pending" and p.occurred_at is None
                            and p.geo_precision == "unknown" for p in batch.publications))

        result = ingest_payload(self.connection, feed(accepted), collected_at=COLLECTED)
        self.assertEqual((result.status, result.items_seen, result.items_inserted), ("ok", 20, 20))
        self.assertEqual(self.connection.execute("SELECT count(*) FROM events").fetchone()[0], 20)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM event_sources").fetchone()[0], 20)

        excess = accepted + entry("20", "Synthetic publication 20",
                                  "https://example.test/notices/20")
        with self.assertRaisesRegex(GOVUKAtomError, "feed exceeds 20 entries"):
            parse_feed(feed(excess), collected_at=COLLECTED)
        with self.assertRaisesRegex(GOVUKAtomError, "feed exceeds 20 entries"):
            ingest_payload(self.connection, feed(excess), collected_at=COLLECTED)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM events").fetchone()[0], 20)
        self.assertEqual(self.connection.execute("SELECT status FROM ingest_runs ORDER BY id DESC LIMIT 1").fetchone()[0], "failed")

    def test_rejects_invalid_offset_components_in_every_atom_timestamp(self):
        invalid_offsets = ("+00:99", "-10:90", "+24:00", "+00:60")
        for offset in invalid_offsets:
            malformed_time = f"2026-10-21T10:00:00{offset}"
            timestamp_cases = (
                feed("").replace("2026-10-21T09:00:00Z", malformed_time),
                feed(entry("published", "Bad published", "https://example.test/published",
                           f"<published>{malformed_time}</published>")),
                feed(entry("updated", "Bad updated", "https://example.test/updated",
                           f"<updated>{malformed_time}</updated>")),
            )
            for document in timestamp_cases:
                with self.subTest(offset=offset, document=document[:80]), self.assertRaises(GOVUKAtomError):
                    parse_feed(document, collected_at=COLLECTED)

            # A malformed collection offset must fail before the importer
            # creates a source or run, so no event/provenance can be written.
            with self.subTest(offset=offset, collection="parse"), self.assertRaises(GOVUKAtomError):
                parse_feed(feed(""), collected_at=f"2026-10-21T10:00:00{offset}")
            with self.subTest(offset=offset, collection="import"), self.assertRaises(GOVUKAtomError):
                ingest_payload(self.connection, self.xml, collected_at=f"2026-10-21T10:00:00{offset}")
        self.assertEqual(self.connection.execute("SELECT count(*) FROM sources").fetchone()[0], 0)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM ingest_runs").fetchone()[0], 0)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM events").fetchone()[0], 0)

    def test_valid_utc_and_numeric_offsets_are_preserved_as_expected(self):
        document = feed(entry("valid", "Valid", "https://example.test/valid",
                              "<published>2026-10-21T10:00:00+01:00</published>"
                              "<updated>2026-10-21T09:00:00Z</updated>"))
        batch = parse_feed(document, collected_at="2026-10-21T10:00:00Z")
        publication = batch.publications[0]
        self.assertEqual(publication.published_at, "2026-10-21T09:00:00.000000Z")
        self.assertEqual(publication.updated_at, "2026-10-21T09:00:00.000000Z")
        self.assertEqual(batch.collected_at, "2026-10-21T10:00:00.000000Z")

    def test_invalid_atom_offset_marks_import_failed_without_events(self):
        document = feed(entry("invalid", "Invalid offset", "https://example.test/invalid",
                              "<published>2026-10-21T10:00:00+00:99</published>"))
        with self.assertRaises(GOVUKAtomError):
            ingest_payload(self.connection, document, collected_at=COLLECTED)
        run = self.connection.execute("SELECT status, items_seen FROM ingest_runs").fetchone()
        self.assertEqual(tuple(run), ("failed", 0))
        self.assertEqual(self.connection.execute("SELECT count(*) FROM events").fetchone()[0], 0)

    def test_rejects_namespace_shape_dtd_entities_and_oversized_xml(self):
        good_entry = entry("urn:x", "Synthetic", "https://example.test/x")
        bad_documents = [
            feed(good_entry, namespace="urn:not-atom"),
            feed('<x:entry xmlns:x="urn:not-atom"><x:id>fake</x:id></x:entry>'),
            "<!DOCTYPE feed [<!ENTITY x SYSTEM 'file:///etc/passwd'>]>" + feed(good_entry),
            "<!DOCTYPE feed [<!ENTITY x 'expanded'>]>" + feed(good_entry),
            feed(good_entry).replace("</feed>", "&unknown;</feed>"),
            "<feed",
            " " * (MAX_XML_BYTES + 1),
        ]
        for document in bad_documents:
            with self.subTest(prefix=document[:35]), self.assertRaises(GOVUKAtomError):
                parse_feed(document, collected_at=COLLECTED)
        with self.assertRaisesRegex(GOVUKAtomError, "131072"):
            parse_feed(" " * (MAX_XML_BYTES + 1), collected_at=COLLECTED)

    def test_rejects_entry_limit_duplicates_bad_urls_and_bad_dates(self):
        too_many = "".join(entry(str(i), f"Title {i}", f"https://example.test/{i}") for i in range(MAX_ENTRIES + 1))
        duplicate_title = entry("id1", "Repeat", "https://example.test/1") + entry("id2", "repeat", "https://example.test/2")
        duplicate_url = entry("id1", "One", "https://example.test/a#first") + entry("id2", "Two", "https://example.test/a#second")
        duplicate_id = entry("same", "One", "https://example.test/1") + entry("same", "Two", "https://example.test/2")
        bad_date = entry("id", "Bad date", "https://example.test/date", "<published>2026-10-21T09:00:00</published>")
        unsafe = entry("id", "Unsafe", "https://user@example.test/path")
        for document in (feed(too_many), feed(duplicate_title), feed(duplicate_url), feed(duplicate_id), feed(bad_date), feed(unsafe)):
            with self.subTest(document=document[:100]), self.assertRaises(GOVUKAtomError):
                parse_feed(document, collected_at=COLLECTED)

    def test_ingest_persists_only_approved_event_metadata_and_deduplicates(self):
        first = ingest_payload(self.connection, self.xml, collected_at=COLLECTED)
        second = ingest_payload(self.connection, self.xml, collected_at="2026-10-21T10:01:00Z")
        self.assertEqual((first.status, first.items_seen, first.items_inserted), ("ok", 2, 2))
        self.assertEqual(second.items_inserted, 0)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM events").fetchone()[0], 2)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM event_sources").fetchone()[0], 2)
        event = self.connection.execute("SELECT * FROM events ORDER BY id LIMIT 1").fetchone()
        source = self.connection.execute("SELECT * FROM sources WHERE id=?", (SOURCE_ID,)).fetchone()
        provenance = self.connection.execute("SELECT * FROM event_sources WHERE event_id=?", (event["id"],)).fetchone()
        self.assertEqual(source["home_url"], GOVUK_HOME_URL)
        self.assertEqual(event["published_at"], "2026-10-20T09:30:00.000000Z")
        self.assertIsNone(event["occurred_at"])
        self.assertEqual(event["collected_at"], "2026-10-21T09:00:00.000000Z")
        self.assertIsNone(event["summary"])
        self.assertIsNone(provenance["country"])
        self.assertEqual(provenance["original_url"], "https://example.test/notices/alpha")
        self.assertEqual(event["validation_status"], "pending")

    def test_malformed_feed_records_failed_run_not_empty(self):
        with self.assertRaises(GOVUKAtomError):
            ingest_payload(self.connection, "<broken>", collected_at=COLLECTED)
        run = self.connection.execute("SELECT status,items_seen FROM ingest_runs").fetchone()
        self.assertEqual(tuple(run), ("failed", 0))
        self.assertEqual(self.connection.execute("SELECT count(*) FROM events").fetchone()[0], 0)

    def test_later_sqlite_failure_rolls_back_whole_batch(self):
        self.connection.execute("""CREATE TRIGGER fail_second_notice BEFORE INSERT ON events
            WHEN NEW.title = 'Synthetic notice with unknown dates'
            BEGIN SELECT RAISE(FAIL, 'synthetic failure'); END""")
        with self.assertRaisesRegex(GOVUKAtomError, "could not be stored"):
            ingest_payload(self.connection, self.xml, collected_at=COLLECTED)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM events").fetchone()[0], 0)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM event_sources").fetchone()[0], 0)
        self.assertEqual(self.connection.execute("SELECT status FROM ingest_runs").fetchone()[0], "failed")
        self.assertFalse(self.connection.in_transaction)


if __name__ == "__main__":
    unittest.main()
