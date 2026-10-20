# POLISH HORIZONT — opt-in persistent named-volume LAB (Issue #39)

**PRIVATE, NONCOMMERCIAL, FICTIONAL-ONLY.** Separate Docker Compose project `horizont-storage-lab`. Existing macOS Mid-2014 containers on host ports **15480**, **15481**, **15482** remain untouched; new authenticated API (if explicitly started) uses **localhost 15483**. No source ingestion, no schedules, no production OAuth/MCP, no Cloudflare/Contabo, no host bind mounts, no user data.

## Why two database areas?

SQLite in WAL mode normally needs access to `-wal` and `-shm`, which can fail when the live database directory is bind-mounted or volume-mounted `read_only` inside an API container. **We do not pretend a live WAL file can safely be opened on a :ro mount with no sidecars.** Instead:

- `primary_data`: only the one-shot **writer** service mounts this volume RW. `primary.sqlite` uses WAL. No HTTP service has this mount. Lock `.writer.lock` + nonblocking advisory `flock` serializes maintenance writers.
- `read_view`: writer explicitly calls `publish` to create a complete **sealed DELETE-journal** SQLite snapshot `view-v1.sqlite` using the verified online backup API. The bearer-protected **reader API mounts this volume :ro** and uses `mode=ro/query_only=ON`. This is an immutable **point-in-time snapshot**; it is **not refreshed automatically**.
- `offline_backup`: writer explicitly calls `backup` to create `backup-v1.sqlite` in a **different Docker volume**. It is a verified snapshot but **not an off-host backup**.
- `recovered_data`: separate **recovery** job mounts backup volume :ro plus a **new** recovery volume RW, restores only to `restored-v1.sqlite`, then runs read-only SQL summary. Recovery has **no primary volume mounted**. Never swap/rewrite the original.

All four volumes are namespaced to `-p horizont-storage-lab` by Docker Compose and use nonroot UID/GID 10001. No host paths, no external requests by maintenance jobs (`network_mode:none`), rootfs read-only, no privileges. The optional read-only API uses project-scoped bridge for loopback-host publishing; potential egress exists but code has no outbound network calls.

## Preconditions on the real Mid-2014 (operator-controlled)

1. Record *fresh* health of Apple Bridge and SKS, plus Docker Desktop VM/host free disk space, RAM and load. Existing 3 HORIZONT projects must all be healthy. Do **not** proceed if they are degraded.
2. Confirm main branch merged reviewed implementation and CI success. `git pull --ff-only` in `~/polish-horizont`; confirm the expected commit. Nothing under `~/AppleBridge`, synced Google Drive or a real SQLite path should be passed in any command.
3. Keep the existing LAB services running; do **not** use `docker system prune`, `volume prune`, `down -v`, or reuse any of the three existing project names.

All commands below are from Mid-2014's local Terminal:

```sh
cd ~/polish-horizont
git pull --ff-only

# Required only BEFORE starting reader; never paste the token into chat/logs.
export HORIZONT_STORAGE_LAB_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(36))')"

docker compose --env-file /dev/null -p horizont-storage-lab -f deploy/lab/compose.storage-lab.yaml config --quiet
docker compose --env-file /dev/null -p horizont-storage-lab -f deploy/lab/compose.storage-lab.yaml build writer

# Each maintenance job is ONE SHOT, explicitly invoked, no live collector:
docker compose --env-file /dev/null -p horizont-storage-lab -f deploy/lab/compose.storage-lab.yaml run --rm writer init
docker compose --env-file /dev/null -p horizont-storage-lab -f deploy/lab/compose.storage-lab.yaml run --rm writer seed
docker compose --env-file /dev/null -p horizont-storage-lab -f deploy/lab/compose.storage-lab.yaml run --rm writer status
docker compose --env-file /dev/null -p horizont-storage-lab -f deploy/lab/compose.storage-lab.yaml run --rm writer publish
docker compose --env-file /dev/null -p horizont-storage-lab -f deploy/lab/compose.storage-lab.yaml run --rm writer backup
docker compose --env-file /dev/null -p horizont-storage-lab -f deploy/lab/compose.storage-lab.yaml run --rm recovery restore

# Reader remains optional and must have a fresh private bearer token:
docker compose --env-file /dev/null -p horizont-storage-lab -f deploy/lab/compose.storage-lab.yaml up -d --no-build reader
docker compose --env-file /dev/null -p horizont-storage-lab -f deploy/lab/compose.storage-lab.yaml ps
curl -i --max-time 5 http://127.0.0.1:15483/health

# Missing bearer MUST yield 401; authorized response has 2 fictional events.
curl -s -o /dev/null -w 'Missing auth HTTP %{http_code}\n' --max-time 5 http://127.0.0.1:15483/api/v1/events
curl -fsS --max-time 5 -H "Authorization: Bearer ${HORIZONT_STORAGE_LAB_TOKEN}" http://127.0.0.1:15483/api/v1/events
```

**Do not paste authorized curl stdout with token accidentally embedded.** The event JSON is fictional. `status` should report `events=2, sources=2, provenance=3`. Backup and recovered snapshots have matching logical contents; byte hash may differ because SQLite backup rewrites headers. Operator should keep the actual token private and not put it in version control.

## Acceptance after manual deployment

- No existing :15480/:15481/:15482 services restarted or changed; **all three healthy after new lab**, plus Apple Bridge and SKS.
- New :15483 reader on `127.0.0.1` only; no UI integration and no browser tokens. `/health` 200, `/api/v1/events` 401 without bearer, 200 with bearer; 2 fictional records, one multi-source provenance PL+GB.
- `writer status` after restarting/recreating a one-shot writer container still returns 2 events (actual persistence). **Docker `down` WITHOUT `-v` preserves the 4 named volumes.** For a deliberate disaster recovery drill, use `recovery restore` only into empty recovered volume and verify counts; no switching live paths.
- New reader healthy for >=15min and total host memory/CPU/disk acceptable. Sleep/wake and longer run are later gates, not automatically passed by CI.
- Repeating `init`, `seed`, `publish`, `backup`, or `restore` MUST fail with a nonzero exit rather than overwrite. Those commands have fixed versioned destination names in Stage 1; adding timestamped rotations requires another change.

## Rollback and retention

Stop/remove **only** the new project containers and network, **without removing volumes**:

```sh
docker compose --env-file /dev/null -p horizont-storage-lab -f deploy/lab/compose.storage-lab.yaml down
```

This preserves all four named volumes and data; **DO NOT add `-v`**. A code rollback is a reviewed Git revert, but volumes and their owners remain; migrate or remove only by a separately authorized retention/disposal procedure after verified export and backups. No automatic backup retention or encryption has been implemented. Four volumes on one Docker Desktop host do NOT protect against laptop disk failure or theft. Before real data, require encrypted backup to external media/location plus separately tested independent restore and a reviewed private-session/auth design.

## Automation deliberately NOT included

`writer seed` is an explicitly invoked *fixed fictional* data operation, not an OSINT adapter. `publish` is a one-time snapshot; no live updates, no recurring ingestion. The old API on 15481 is unchanged and does not use these volumes. Real GDELT Events mapping is #24 and GOV.UK Atom #17, independent tasks.

For CI, `.github/workflows/ci.yml` runs this Compose only on disposable GitHub runners. On Mid-2014, **nothing starts as a result of a Git merge**.
