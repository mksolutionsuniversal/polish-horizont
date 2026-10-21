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
SQLDATE are normalized before hashing. `collected_at`, source/run identifiers,
and any caller-supplied `revision_hash` are excluded. This is a prototype
metadata revision key, not a content-integrity or authenticity signature.

An accepted `incident_hypothesis` with `validation_status='valid'` cannot be
reclassified in this spike. Independence approval requires a comparator for
the same event claim and distinct observation IDs and canonical URLs; repeated
observations and GDELT/publisher records pointing at the same URL fail closed.
These checks are synthetic prototype rules, not production authorization.
