# Event schema (MVP)

This document describes the local-only SQLite prototype in `src/horizont/core/`. It adds no feed client, API, web framework, service, or production database. Use synthetic fixtures for development and review.

## Tables and relationships

- **`sources`** holds a stable source identifier, display name, optional public home URL, and latest source state. New sources start at `never_run`.
- **`ingest_runs`** records one attempted collection: source, UTC start/finish, outcome, item counts, and an optional error message. A run begins as `running`; only `ok`, `empty`, `failed`, or `unavailable` are final outcomes. Failures require an explanatory message and remain distinct from a successful empty response.
- **`events`** stores one normalized event per unique `dedup_key`. `occurred_at` (event time) and `published_at` (publisher time) are nullable and independent. `collected_at` is required. Optional coordinates, precision, confidence, and a validation state are stored with the normalized event.
- **`event_sources`** joins events to sources and preserves each source’s original URL, language, optional country, first/last seen collection times, and optional ingest run. The composite foreign key ensures the run belongs to the same source. One event can therefore retain provenance from multiple sources after deduplication.
- **`schema_migrations`** records each applied integer migration and its UTC application time.

Source states are constrained to `ok`, `empty`, `stale`, `unknown`, `failed`, `unavailable`, and `never_run`. `unavailable` means the source could not be queried; it does not mean zero events.

`first_seen_at` is the minimum recorded **collection timestamp** for an event/source pair; `last_seen_at` is the maximum. Both describe when this data store recorded collection observations, not the event’s `occurred_at` or publisher’s `published_at`. This min/max rule is stable under out-of-order historical imports. A repeat with a newer collection timestamp advances `last_seen_at` and may refresh that source’s URL/language/country; an older or equal-time replay cannot replace them. A newer replay with missing optional language or country retains the last known non-null value. Provenance is current-state only; the MVP does not retain every metadata revision.

## Dates, geography and integrity

Write helpers accept offset-aware ISO-8601 timestamps and normalize them to UTC (`Z`). Naive datetimes are rejected to avoid silently guessing a timezone. Missing event and publication dates remain SQL `NULL`; collection time is mandatory. SQLite stores these normalized strings for sortable UTC chronology.

Country is a nullable two-letter uppercase code; language is an optional language tag. Coordinates must be a valid latitude/longitude pair and within geographic bounds. Both coordinates can be absent. `geo_precision` describes the stated precision (`unknown`, `country`, `region`, `locality`, `point`); `geo_confidence` is nullable and bounded from 0 to 1. The MVP does not geocode, infer missing locations, or define a confidence calibration method.

Foreign keys are enabled for connections created by `connect()`. Unique source IDs and event dedup keys, check constraints, run counts, timestamps, and URL validation in the write helpers provide basic integrity. Deduplication is deterministic only to the extent that a future caller supplies a stable, appropriately scoped key; this prototype does not calculate similarity or resolve conflicting event text.

## Indexes and migrations

Migration 1 creates the source, run, event, provenance, and migration tables. Migration 2 adds indexes for event occurred/published/collected times, source recency, and per-source run history. `migrate(connection)` applies pending versions in order and is idempotent. Each migration explicitly begins an SQLite transaction **before** any DDL and commits the migration version in the same transaction; interrupted CREATE TABLE/INDEX statements roll back so a retry is possible. The connection must not have a pre-existing active transaction when pending migrations are applied. A synthetic interrupted-DDL regression test verifies rollback and retry. The current schema version is exported as `SCHEMA_VERSION`.

`connect(path)` enables SQLite WAL mode and foreign keys for a local file database. WAL supports local concurrent readers alongside a writer, but does not turn this prototype into a network/shared database. SQLite may report `memory` journal mode for `:memory:` databases; tests use that or temporary files and no real data.

The offline query layer in `src/horizont/core/queries.py` opens only an existing on-disk database through SQLite URI `mode=ro`, then enables `query_only`; it does not create files or migrate schemas. It exposes fixed, parameterized `SELECT` operations and no raw SQL argument. Time filters select exactly one named column and use a half-open `[start, end)` interval; naive bounds are rejected, and a NULL selected timestamp stays NULL and does not match either bound. Country filters use `event_sources.country`, which is **source-observed country**, not verified incident location. The query module is a Python library, not an MCP server, HTTP API, authentication layer, or deployment boundary; callers must keep database-file permissions restricted.

The response contract is plain Python data: `list_recent_events()` returns `{items, limit, offset, time_field, start, end, country_filter_semantics}`; each item contains the selected event fields and preserves SQL NULLs. Rows sort by the chosen timestamp descending, NULL timestamps last, then event ID descending. `get_event_detail()` returns `{event, sources}` or `None`; source entries are ordered by source ID and retain each source’s own URL, language, country and first/last-seen values. `list_source_statuses()` returns sources ordered by ID with the latest run ordered by start time then run ID; a source with no run has NULL run fields. Raw run error text is intentionally omitted. List pagination accepts `limit` 1–100 and `offset` 0–100000.

## Rollback and independent test plan

Rollback is to remove this prototype’s imports/call sites and the additive `src/horizont/core/`, `tests/test_sqlite_core.py`, and this document from the feature branch. No existing database is migrated or created by repository checks. If a later local prototype database is created, stop its caller, preserve a copy for review, and remove only that explicitly identified local test database; this PR creates none.

Independent verification uses only Python’s standard library and synthetic event/source values: `python3 -m unittest discover -s tests -v`. Tests cover incremental/idempotent migration, UTC normalization and missing dates, deduplication across sources, out-of-order re-import bounds and metadata, provenance rollback, null geolocation, constraints/foreign keys, source-state values, failed/unavailable/empty run distinctions, error handling, and WAL on a temporary file. Repository CI remains unchanged and runs its existing hygiene check and unittest suite on GitHub-hosted runners.

## MVP limits

No real ingestion, source-specific adapters, API calls, credentials, country reports, history retention policy, event revision model, clustering, geocoding, access-control layer, backup/restore tooling, concurrent writer policy, deployment, or production data are included. Event status and source/run status are separate concepts. Schema fields are a starting point for review, not a guarantee of source rights, factual accuracy, legal compliance, or production readiness.
