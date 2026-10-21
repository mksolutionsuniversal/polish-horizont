# EXPERIMENTAL — SYNTHETIC, NOT DEPLOYABLE

This isolated proof of concept creates a fictional schema-v2 fixture in SQLite
memory and applies candidate v3 DDL only to that marked in-memory connection.
It refuses file-backed databases. It is not imported by the app and has no
network, migration command, service, or production identity integration.

Run its focused tests with:

```sh
python3 -m unittest tests.test_evidence_v3_spike -v
```

The synthetic collector and owner capabilities are test seams, not credentials
or authentication. Direct access to Python or SQLite is outside this
prototype's security boundary; do not use it with real records or databases.
Production `SCHEMA_VERSION` and migrations remain v2 and unchanged.

The spike computes `revision_hash` as lowercase SHA-256 over UTF-8 compact,
sorted-key JSON containing exactly `canonical_url`, `title`, normalized
`source_published_at`, normalized `source_updated_at`, normalized `gdelt_sqldate`,
normalized `gdelt_dateadded_at`, `attribution`, `licence_uri`, and
`geo_source_fields`. Missing values are JSON `null`; timestamp offsets and
SQLDATE are normalized before hashing. Text values are trimmed, line endings
are converted to LF, and Unicode is normalized to NFC before storage and
hashing. `collected_at`, source/run identifiers,
and any caller-supplied `revision_hash` are excluded. This is a prototype
metadata revision key, not a content-integrity or authenticity signature.

The legacy `events.validation_status` value is preserved as historical v2 data.
The v3 effective hypothesis status is derived only from
`event_hypothesis_history`; it is `pending` until an explicit owner decision
exists, regardless of a legacy `valid` or `invalid` value. New v3 decisions do
not rewrite the v2 field. A v3 `valid` hypothesis cannot be reclassified in
this spike.

Independence approval requires a comparator for the same event claim with a
different observation, `source_record_id`, and canonical URL. Revisions of one
source record and GDELT/publisher records pointing at the same URL fail closed.
Even distinct source-record IDs and URLs do not establish editorial
independence: the positive synthetic test assumes separate origins as fixture
setup because this spike has no real lineage-attestation model. These are
synthetic prototype rules, not production authorization.
