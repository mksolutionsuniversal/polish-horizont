# Event schema (MVP)

This document describes the local-only SQLite prototype in `src/horizont/core/`. It adds no feed client, API, web framework, service, or production database. Use synthetic fixtures for development and review.

## Tables and relationships

- **`sources`** holds a stable source identifier, display name, optional public home URL, and latest source state. New sources start at `never_run`.
- **`ingest_runs`** records one attempted collection: source, UTC start/finish, outcome, item counts, and an optional error message. A run begins as `running`; only `ok`, `empty`, `failed`, or `unavailable` are final outcomes. Failures require an explanatory message and remain distinct from a successful empty response.
- **`events`** stores one normalized event per unique `dedup_key`. `occurred_at` (event time) and `published_at` (publisher time) are nullable and independent. `collected_at` is required. Optional coordinates, precision, confidence, and a validation state are stored with the normalized event.
- **`event_sources`** joins events to sources and preserves each source’s original URL, language, optional country, first/last seen collection times, and optional ingest run. The composite foreign key ensures the run belongs to the same source. One event can therefore retain provenance from multiple sources after deduplication.
- **`schema_migrations`** records each applied integer migration and its UTC application time.

Source states are constrained to `ok`, `empty`, `stale`, `unknown`, `failed`, `unavailable`, and `never_run`. `unavailable` means the source could not be queried; it does not mean zero events.

## Dates, geography and integrity

Write helpers accept offset-aware ISO-8601 timestamps and normalize them to UTC (`Z`). Naive datetimes are rejected to avoid silently guessing a timezone. Missing event and publication dates remain SQL `NULL`; collection time is mandatory. SQLite stores these normalized strings for sortable UTC chronology.

Country is a nullable two-letter uppercase code; language is an optional language tag. Coordinates must be a valid latitude/longitude pair and within geographic bounds. Both coordinates can be absent. `geo_precision` describes the stated precision (`unknown`, `country`, `region`, `locality`, `point`); `geo_confidence` is nullable and bounded from 0 to 1. The MVP does not geocode, infer missing locations, or define a confidence calibration method.

Foreign keys are enabled for connections created by `connect()`. Unique source IDs and event dedup keys, check constraints, run counts, timestamps, and URL validation in the write helpers provide basic integrity. Deduplication is deterministic only to the extent that a future caller supplies a stable, appropriately scoped key; this prototype does not calculate similarity or resolve conflicting event text.

## Indexes and migrations

Migration 1 creates the source, run, event, provenance, and migration tables. Migration 2 adds indexes for event occurred/published/collected times, source recency, and per-source run history. `migrate(connection)` applies pending versions in order and is idempotent. Each migration explicitly begins an SQLite transaction **before** any DDL and commits the migration version in the same transaction; interrupted CREATE TABLE/INDEX statements roll back so a retry is possible. The connection must not have a pre-existing active transaction when pending migrations are applied. A synthetic interrupted-DDL regression test verifies rollback and retry. The current schema version is exported as `SCHEMA_VERSION`.

`connect(path)` enables SQLite WAL mode and foreign keys for a local file database. WAL supports local concurrent readers alongside a writer, but does not turn this prototype into a network/shared database. SQLite may report `memory` journal mode for `:memory:` databases; tests use that or temporary files and no real data.

For a future local read-only MCP/query process, open the database with SQLite URI `mode=ro` and expose only fixed, parameterized `SELECT` operations over approved fields. This prototype does not implement an MCP server or a read-only connection factory.

## Rollback and independent test plan

Rollback is to remove this prototype’s imports/call sites and the additive `src/horizont/core/`, `tests/test_sqlite_core.py`, and this document from the feature branch. No existing database is migrated or created by repository checks. If a later local prototype database is created, stop its caller, preserve a copy for review, and remove only that explicitly identified local test database; this PR creates none.

Independent verification uses only Python’s standard library and synthetic event/source values: `python3 -m unittest discover -s tests -v`. Tests cover incremental/idempotent migration, UTC normalization and missing dates, deduplication across sources, provenance, null geolocation, constraints/foreign keys, source-state values, failed/unavailable/empty run distinctions, error handling, and WAL on a temporary file. Repository CI remains unchanged and runs its existing hygiene check and unittest suite on GitHub-hosted runners.

## MVP limits

No real ingestion, source-specific adapters, API calls, credentials, country reports, history retention policy, event revision model, clustering, geocoding, access-control layer, backup/restore tooling, concurrent writer policy, deployment, or production data are included. Event status and source/run status are separate concepts. Schema fields are a starting point for review, not a guarantee of source rights, factual accuracy, legal compliance, or production readiness.
