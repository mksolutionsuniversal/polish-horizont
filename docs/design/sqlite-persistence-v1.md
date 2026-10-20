# SQLite Persistent Storage v1 — offline lifecycle, not yet a Docker volume

**Private, noncommercial HORIZONT.** This is a safe first stage of persistent storage engineering. It adds *standalone* database lifecycle commands and tests but **does not create a volume, start a collector, connect a real news source or change any of the three active Mid-2014 Docker LAB projects**. No real user database, secret, snapshot, hostname or path is committed to GitHub.

## Grounded starting point

- Current schema `SCHEMA_VERSION=2`, tables `schema_migrations`, `sources`, `events`, `event_sources`, `ingest_runs`, plus indexes.
- Existing `horizont.core.db.connect` opens a writer in WAL mode and `migrate` applies DDL transactions. Writers should be a distinct, explicitly authorized process.
- Existing `horizont.core.queries.open_readonly` opens **an existing** DB in SQLite URI `mode=ro` and enables `PRAGMA query_only=ON`; read paths do not call `migrate`.
- All three current demo images contain *different immutable, fictional fixtures*. None are a persistent database or proof of ingestion from GDELT/GOV.UK.

## New lifecycle API and CLI

`src/horizont/core/persistence.py` provides:

| Python function | Offline CLI | Safety invariant |
| --- | --- | --- |
| `initialize_new(path)` | `init NEW.sqlite` | Creates schema v2 at a **new** pathname only, owner-only file mode 0600 |
| `backup_live(db, backup)` | `backup EXISTING.sqlite NEW-snapshot.sqlite` | SQLite online backup captures committed WAL transactions into a verified self-contained snapshot, never raw filesystem copy |
| `verify_existing(snapshot)` | `verify SNAPSHOT.sqlite` | Checks `quick_check`, `foreign_key_check`, schema migration history, returns SHA-256 digest for portable copy |
| `restore_to_new(snapshot, fresh_path)` | `restore VERIFIED-snapshot.sqlite NEW-restored.sqlite` | Always restores into a **new file**, leaves original DB and WAL/SHM alone |

Use with `PYTHONPATH=src python3 -m horizont.core.persistence ...` from the checked-out repo. The CLI prints **only** schema version, file size and SHA-256 — never DB contents or credentials. Backups are **NOT encrypted** by this library; encrypt their storage with owner-controlled local disk/backup mechanisms before any private data is used.

Creation/backup/restore uses a restricted 0600 temp file **in the destination directory**, validates the complete snapshot and publishes it via an atomic no-clobber hard link instead of `os.replace`. Existing regular file or symlink (including a dangling link) refuses with an error. Partial stage files are cleaned up after failure. This approach assumes a filesystem with atomic hard links and regular durable file semantics such as APFS/ext4; do not use network shares or cloud-synchronized folders.

**No in-place restore.** To recover an operational database: stop its one writer and any services accessing that database, then verify and restore to a separate fresh file or volume **under an independently reviewed operator procedure**. Switch paths only after validation; retain old DB + its WAL/SHM for rollback. The CLI does not swap a live pointer, delete sidecars or restart Docker. SQLite backup snapshots do not require shutting down readers/writer, but writer lock/contention could delay backup. Do not mistake "successful snapshot" for a tested disaster recovery until restore/readback on a fresh path succeeds.

## Safe offline dry run (synthetic only)

These commands are examples for a throwaway directory and a **new** path. They do not target any existing container, Apple Bridge/SKS folder, or live DB:

```sh
# Run only after tests/review and operator approval, with no real data:
mkdir -m 700 -p "${TMPDIR:-/tmp}/horizont-storage-test"
PYTHONPATH=src python3 -m horizont.core.persistence init "${TMPDIR:-/tmp}/horizont-storage-test/new.sqlite"
PYTHONPATH=src python3 -m horizont.core.persistence backup "${TMPDIR:-/tmp}/horizont-storage-test/new.sqlite" "${TMPDIR:-/tmp}/horizont-storage-test/backup.sqlite"
PYTHONPATH=src python3 -m horizont.core.persistence verify "${TMPDIR:-/tmp}/horizont-storage-test/backup.sqlite"
PYTHONPATH=src python3 -m horizont.core.persistence restore "${TMPDIR:-/tmp}/horizont-storage-test/backup.sqlite" "${TMPDIR:-/tmp}/horizont-storage-test/restored.sqlite"
```

Do **not** paste an existing home directory into `init` or try to overwrite a file to 'refresh' a snapshot. Use a new timestamped backup name each time; verify results and restore on independent storage for disaster-recovery acceptance.

## Reader vs writer, future Docker LAB design

This PR establishes file lifecycle primitives, not a complete Docker-based permission boundary. Plan the **next opt-in PR** for a separately named writer and read-only API:
- Persistent location **outside** the Git checkout and Google Drive/CloudStorage (dedicated Docker volume on Mid-2014). Backup target on independent physical disk/media, verified and encrypted.
- Schema migration only in a stopped/controlled writer maintenance job with rollback snapshot **before** changing schema, not on web request or reader startup.
- Design a single writer service identity with `rw` volume, and query API service identities with `ro` volume + SQLite `mode=ro`, mindful that WAL readers may require access to `-wal`/`-shm` and that read-only Docker mounts can make first WAL reader fail. Real Docker OS/UID permission tests required.
- Explicit network and authentication. Do not expose database file through public endpoints, Docker socket, arbitrary HTTP path parameter or MCP. Never put private operator data into synthetic `15480/15481/15482` demo images.
- Backup behavior under concurrent write, volume backups/sleep/wake, file ownership and restoration to a **fresh volume** must pass independent tests before attaching collectors. Existing Bridge/SKS and all three demos should continue independently.

## QA / acceptance / rollback

Unit tests cover active committed WAL snapshot, avoidance of uncommitted writes, multi-source provenance and unknown UTC timestamps, 0600/no overwrite/symlink guards, corrupt/incomplete schema rejection, foreign key integrity, and round-trip restore. Existing Python/Compose/Docker CI must remain green. No new persistent Docker service/volume is defined by this PR.

Rollback is **code-only**: revert this PR via reviewed GitHub change. There is no new running service or host file to remove. Never use `docker system prune`, `down -v` or global config edits in this stage. Don't infer live GDELT/GOV.UK ingestion, persistent volume deployment or a security review of encrypted offsite backup from a successful offline test.

GDELT Raw Events 61-column official mapping remains Issue #24; GOV.UK Atom adapter Issue #17. They are separate feed-ingestion tasks and not activated by this storage change.
