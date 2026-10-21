"""Synthetic-only contract tests for GDELT's 61-column Events projection."""

from datetime import date, datetime, timezone
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from horizont.adapters.gdelt_events import (  # noqa: E402
    CODEBOOK_URL,
    EVENT_COLUMNS,
    EXPECTED_COLUMNS,
    GDELTEventError,
    project_tsv_row,
    project_tsv_rows,
)


# Independent, codebook-ordered expected export header. Codebook calls field 2
# "Day"; the standard 61-column export header calls it SQLDATE.
EXPECTED_HEADER = (
    "GLOBALEVENTID SQLDATE MonthYear Year FractionDate Actor1Code Actor1Name Actor1CountryCode "
    "Actor1KnownGroupCode Actor1EthnicCode Actor1Religion1Code Actor1Religion2Code Actor1Type1Code "
    "Actor1Type2Code Actor1Type3Code Actor2Code Actor2Name Actor2CountryCode Actor2KnownGroupCode "
    "Actor2EthnicCode Actor2Religion1Code Actor2Religion2Code Actor2Type1Code Actor2Type2Code "
    "Actor2Type3Code IsRootEvent EventCode EventBaseCode EventRootCode QuadClass GoldsteinScale "
    "NumMentions NumSources NumArticles AvgTone Actor1Geo_Type Actor1Geo_FullName "
    "Actor1Geo_CountryCode Actor1Geo_ADM1Code Actor1Geo_ADM2Code Actor1Geo_Lat Actor1Geo_Long "
    "Actor1Geo_FeatureID Actor2Geo_Type Actor2Geo_FullName Actor2Geo_CountryCode "
    "Actor2Geo_ADM1Code Actor2Geo_ADM2Code Actor2Geo_Lat Actor2Geo_Long Actor2Geo_FeatureID "
    "ActionGeo_Type ActionGeo_FullName ActionGeo_CountryCode ActionGeo_ADM1Code ActionGeo_ADM2Code "
    "ActionGeo_Lat ActionGeo_Long ActionGeo_FeatureID DATEADDED SOURCEURL"
).split()


def synthetic_row(**overrides):
    values = [""] * EXPECTED_COLUMNS
    defaults = {
        "GLOBALEVENTID": "9900000001",
        "SQLDATE": "20261020",
        "EventCode": "0251",
        "Actor1Name": "SYNTHETIC ACTOR ONE",
        "Actor2Name": "SYNTHETIC ACTOR TWO",
        "DATEADDED": "20261020121500",
        "SOURCEURL": "https://example.test/synthetic/story#source",
    }
    defaults.update(overrides)
    for name, value in defaults.items():
        values[EVENT_COLUMNS.index(name)] = value
    return "\t".join(values)


class GDELTEventProjectionTests(unittest.TestCase):
    def test_all_61_positions_match_codebook_order_and_export_tail(self):
        self.assertEqual(len(EVENT_COLUMNS), EXPECTED_COLUMNS)
        self.assertEqual(tuple(EVENT_COLUMNS), tuple(EXPECTED_HEADER))
        self.assertEqual(EVENT_COLUMNS[0], "GLOBALEVENTID")
        self.assertEqual(EVENT_COLUMNS[1], "SQLDATE")  # Codebook: Day
        self.assertEqual(EVENT_COLUMNS[25:30], ("IsRootEvent", "EventCode", "EventBaseCode", "EventRootCode", "QuadClass"))
        self.assertEqual(EVENT_COLUMNS[35], "Actor1Geo_Type")
        self.assertEqual(EVENT_COLUMNS[51], "ActionGeo_Type")
        self.assertEqual(EVENT_COLUMNS[59:], ("DATEADDED", "SOURCEURL"))
        self.assertEqual(CODEBOOK_URL, "https://data.gdeltproject.org/documentation/GDELT-Event_Codebook-V2.0.pdf")

    def test_projection_keeps_gdelt_id_dates_source_and_pending_status_distinct(self):
        event = project_tsv_row(
            synthetic_row(), collected_at="2026-10-20T13:00:00+01:00"
        )
        self.assertEqual(event.gdelt_event_id, 9900000001)
        self.assertEqual(event.dedup_key, "gdelt-raw-events-v2:9900000001")
        self.assertEqual(event.gdelt_sql_date, date(2026, 10, 20))
        self.assertEqual(event.gdelt_date_added_at, datetime(2026, 10, 20, 12, 15, tzinfo=timezone.utc))
        self.assertEqual(event.collected_at, "2026-10-20T12:00:00.000000Z")
        self.assertEqual(event.publisher_url, "https://example.test/synthetic/story")
        self.assertEqual(event.cameo_event_code, "0251")
        self.assertEqual(event.actor1_label, "SYNTHETIC ACTOR ONE")
        self.assertIsNone(event.occurred_at)
        self.assertIsNone(event.published_at)
        self.assertEqual(event.validation_status, "pending")
        self.assertEqual(event.source_id, "gdelt-raw-events-v2")

    def test_missing_and_ambiguous_geography_are_not_filled(self):
        no_geo = project_tsv_row(synthetic_row(), collected_at="2026-10-20T13:00:00Z")
        self.assertIsNone(no_geo.action_geography)
        candidate = project_tsv_row(
            synthetic_row(
                ActionGeo_Type="4", ActionGeo_FullName="Synthetic City, ZZ",
                ActionGeo_CountryCode="ZZ", ActionGeo_Lat="10.5", ActionGeo_Long="20.25",
                ActionGeo_FeatureID="synthetic-feature",
            ),
            collected_at="2026-10-20T13:00:00Z",
        ).action_geography
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.fips_country_code, "ZZ")  # not ISO country mapping
        self.assertEqual(candidate.validation_status, "pending")
        # Names and FIPS codes can conflict; retain them only as raw pending
        # candidate attributes and do not infer or persist an event country.
        conflict = project_tsv_row(
            synthetic_row(
                ActionGeo_Type="4", ActionGeo_FullName="Synthetic City, AA",
                ActionGeo_CountryCode="ZZ", ActionGeo_Lat="10.5", ActionGeo_Long="20.25",
            ),
            collected_at="2026-10-20T13:00:00Z",
        ).action_geography
        self.assertEqual(conflict.fullname, "Synthetic City, AA")
        self.assertEqual(conflict.fips_country_code, "ZZ")
        self.assertEqual(conflict.validation_status, "pending")

    def test_row_width_identifiers_dates_urls_and_cameo_are_checked(self):
        valid = synthetic_row()
        bad = (
            "\t".join(valid.split("\t")[:-1]),
            valid + "\textra",
            synthetic_row(GLOBALEVENTID="0"),
            synthetic_row(GLOBALEVENTID="id-1"),
            synthetic_row(GLOBALEVENTID="9999999999999999999"),
            synthetic_row(SQLDATE="20260230"),
            synthetic_row(SQLDATE="2026-10-20"),
            synthetic_row(DATEADDED="20261020121700"),
            synthetic_row(DATEADDED="20261320121500"),
            synthetic_row(EventCode="02511"),
            synthetic_row(SOURCEURL="javascript:alert(1)"),
            synthetic_row(SOURCEURL="https://user@example.test/story"),
        )
        for candidate in bad:
            with self.subTest(candidate=candidate[:60]), self.assertRaises(GDELTEventError):
                project_tsv_row(candidate, collected_at="2026-10-20T13:00:00Z")

    def test_geography_out_of_range_or_contradictory_is_rejected(self):
        for overrides in (
            {"ActionGeo_Lat": "1", "ActionGeo_Long": "2"},
            {"ActionGeo_Type": "4", "ActionGeo_Lat": "91", "ActionGeo_Long": "2"},
            {"ActionGeo_Type": "4", "ActionGeo_Lat": "1", "ActionGeo_Long": "181"},
            {"ActionGeo_Type": "0", "ActionGeo_Lat": "1", "ActionGeo_Long": "2"},
        ):
            with self.subTest(overrides=overrides), self.assertRaises(GDELTEventError):
                project_tsv_row(synthetic_row(**overrides), collected_at="2026-10-20T13:00:00Z")
        # A partial coordinate pair is ambiguous; it is omitted, never completed.
        item = project_tsv_row(
            synthetic_row(ActionGeo_Type="4", ActionGeo_Lat="1"),
            collected_at="2026-10-20T13:00:00Z",
        )
        self.assertIsNone(item.action_geography)

    def test_batch_rejects_duplicate_gdelt_ids_atomically(self):
        first = synthetic_row()
        duplicate = synthetic_row(Actor1Name="OTHER SYNTHETIC ACTOR")
        with self.assertRaisesRegex(GDELTEventError, "duplicate GLOBALEVENTID"):
            project_tsv_rows([first, duplicate], collected_at="2026-10-20T13:00:00Z")
        self.assertEqual(
            len(project_tsv_rows([first, synthetic_row(GLOBALEVENTID="9900000002")],
                                 collected_at="2026-10-20T13:00:00Z")), 2
        )

    def test_non_utc_collected_at_is_normalized_and_naive_is_rejected(self):
        with self.assertRaises(GDELTEventError):
            project_tsv_row(synthetic_row(), collected_at="2026-10-20T13:00:00")


if __name__ == "__main__":
    unittest.main()
