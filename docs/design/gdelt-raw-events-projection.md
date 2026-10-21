# GDELT 2.0 Events: codebook-grounded projection

This offline decoder accepts synthetic 61-column TSV rows only. It does not
read archives, access a network, write SQLite, or assert that a GDELT-coded
row proves that a physical incident occurred. Every projection is
`pending` and identifies GDELT as its origin.

## Authoritative field order

The mapping is grounded in the official [GDELT Event Database Codebook V2.0
(PDF)](https://data.gdeltproject.org/documentation/GDELT-Event_Codebook-V2.0.pdf),
linked from the [GDELT 2.0 launch documentation](https://blog.gdeltproject.org/gdelt-2-0-our-global-world-in-realtime/).
In the codebook, the second Events-table field is named **Day**; the 61-column
export uses **SQLDATE**. The codebook's field sequence and the export order
are captured in `EVENT_COLUMNS`; tests compare every position against a
separately written expected header. Python indexes are zero-based below;
codebook column numbers are one-based.

| Position | Export field(s) |
| --- | --- |
| 1–5 | `GLOBALEVENTID`, `SQLDATE` (codebook: Day), `MonthYear`, `Year`, `FractionDate` |
| 6–15 | `Actor1Code`, `Actor1Name`, `Actor1CountryCode`, `Actor1KnownGroupCode`, `Actor1EthnicCode`, `Actor1Religion1Code`, `Actor1Religion2Code`, `Actor1Type1Code`, `Actor1Type2Code`, `Actor1Type3Code` |
| 16–25 | `Actor2Code`, `Actor2Name`, `Actor2CountryCode`, `Actor2KnownGroupCode`, `Actor2EthnicCode`, `Actor2Religion1Code`, `Actor2Religion2Code`, `Actor2Type1Code`, `Actor2Type2Code`, `Actor2Type3Code` |
| 26–35 | `IsRootEvent`, `EventCode`, `EventBaseCode`, `EventRootCode`, `QuadClass`, `GoldsteinScale`, `NumMentions`, `NumSources`, `NumArticles`, `AvgTone` |
| 36–43 | `Actor1Geo_Type`, `Actor1Geo_FullName`, `Actor1Geo_CountryCode`, `Actor1Geo_ADM1Code`, `Actor1Geo_ADM2Code`, `Actor1Geo_Lat`, `Actor1Geo_Long`, `Actor1Geo_FeatureID` |
| 44–51 | `Actor2Geo_Type`, `Actor2Geo_FullName`, `Actor2Geo_CountryCode`, `Actor2Geo_ADM1Code`, `Actor2Geo_ADM2Code`, `Actor2Geo_Lat`, `Actor2Geo_Long`, `Actor2Geo_FeatureID` |
| 52–59 | `ActionGeo_Type`, `ActionGeo_FullName`, `ActionGeo_CountryCode`, `ActionGeo_ADM1Code`, `ActionGeo_ADM2Code`, `ActionGeo_Lat`, `ActionGeo_Long`, `ActionGeo_FeatureID` |
| 60–61 | `DATEADDED`, `SOURCEURL` |

The decoder requires exactly 61 textual fields. It keeps `EventCode` a string
to preserve leading zeroes. `GLOBALEVENTID` is retained in a GDELT-namespaced
dedup key, not treated as a globally verified HORIZONT incident ID. Batch
projection rejects duplicate GDELT IDs before returning any result.

## Time and geography semantics

`SQLDATE` is parsed as a `date` (day precision) and retained as
`gdelt_sql_date`; it is not turned into midnight or used as HORIZONT
`occurred_at`. `DATEADDED` is parsed as a UTC GDELT-ingestion timestamp on a
15-minute boundary and retained separately as `gdelt_date_added_at`. It is
not publisher publication time or HORIZONT `collected_at`. The caller must
provide an aware `collected_at`, normalized with the shared SQLite-core UTC
helper. Publisher publication time and incident instant remain NULL/unknown.

The codebook distinguishes actor country affiliations from action geography;
its geographic country codes are FIPS10-4, not ISO-3166. No country is mapped
into HORIZONT's ISO-like event field. A complete in-range action coordinate
pair is represented only as an unvalidated `GeographyCandidate` with pending
status; it is not emitted as a validated map point. Missing or partial pairs
produce no candidate. Invalid type/range and structurally contradictory
coordinate-only rows are rejected. `ActionGeo_Type=0` with every remaining
action geography field empty is treated as unlocated; type zero paired with
any name, country, ADM1/ADM2 code, coordinate, or feature ID is rejected as
contradictory. ADM1/ADM2 are included in that consistency check and are
retained as raw candidate attributes for types 1–5. The projection does not
reconcile names, country codes, actor affiliations, or geocoding results.

## SQLite boundary and follow-up proposal

The current SQLite v2 schema has no dedicated columns for GDELT event IDs,
`SQLDATE`, or GDELT `DATEADDED`. Using `occurred_at`, `published_at`,
`event_sources.country`, `first_seen_at`/`last_seen_at`, or a free-form title
to carry those values would change their established meanings or lose
provenance. Accordingly this change stops at an in-memory typed projection:
it does **not** stage or persist records and does not migrate SQLite.

Proposed future schema review (not implemented): add a source-record/evidence
table with a unique `(source_id, external_record_id)` key and nullable FK to a
HORIZONT event, plus explicit `gdelt_sql_date`, `gdelt_date_added_at`,
`collected_at`, publisher URL, raw CAMEO identifiers, unverified geography,
and validation state. Keep raw GDELT identity/provenance separate from
HORIZONT incident identity and require an explicit evidence review before
linking rows or increasing corroboration. Review that design before a
versioned migration or any SQLite staging work.

## Attribution and limits

Attribute the dataset to the **GDELT Project** and link to
[gdeltproject.org](https://www.gdeltproject.org/). The projection retains the
publisher `SOURCEURL`; it does not fetch it or store article text/media.
All tests use invented row values and `example.test`. The 61-position contract
is codebook-grounded, but this change makes no claim about current feed
availability, classifier accuracy, or the truth of extracted events and
locations.
