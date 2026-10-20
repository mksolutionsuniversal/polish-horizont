# HORIZONT / opt-in Docker API + synthetic SQLite lab

**Private, noncommercial HORIZONT.** This is an **optional** second Compose project. It is NOT added to or automatically started by `deploy/lab/compose.yaml`. The accepted PL/EN preview remains at `127.0.0.1:15480`; its files, ports, container, network and look are not changed.

## What this demonstrates

New project `horizont-api-lab` serves a **bearer-protected read-only API** on host `127.0.0.1:15481`, backed solely by a fictional SQLite database generated inside the image at build time. There is no host database mount, private data, publisher text, GDELT download, scheduler or collector. Synthetic source/event URLs point to `example.test`. Distinct from production feeds. This enables isolated testing of HTTP → API → SQLite with no risk of accidentally writing user records.

Build is based on the existing `python:3.12-alpine` with no pip dependencies. Runs as UID 10001, read-only root FS, memory 128MiB, 0.5 CPU, PID limit 64, all capabilities dropped, no restart policy, no volumes/Docker socket. Its project-scoped bridge allows *potential* egress, but code has no network fetch. Host publication is *only* loopback. Other containers on the host are out of scope.

## Prerequisites and manual start on Mid-2014

Before launching check that the normal static preview still returns HTTP 200, Apple Bridge and SKS health has not regressed, and Docker Desktop has enough free RAM/disk. The current Docker Desktop build sometimes asks to access macOS Keychain while resolving public image metadata over SSH: do image build from the interactive **local Mac terminal** if necessary; do not change global Keychain, Docker config or any other running services.

In the local Mac terminal inside `~/polish-horizont`, after merging this PR and pulling main, generate an **ephemeral demonstration token**. It appears in process/container environment, so it must never be a real/private API token. Do not share `docker inspect` full Environment with anyone. The command only sets a shell variable for this terminal session:

```sh
export HORIZONT_API_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(36))')"
docker compose --env-file /dev/null -p horizont-api-lab -f deploy/lab/compose.api-demo.yaml config --quiet
docker compose --env-file /dev/null -p horizont-api-lab -f deploy/lab/compose.api-demo.yaml up -d --build api
docker compose --env-file /dev/null -p horizont-api-lab -f deploy/lab/compose.api-demo.yaml ps
```

Test with no token first (expect HTTP 401), then authorize (expect exactly two synthetic demo items); `curl` will NOT print the token itself:

```sh
curl -s -o /dev/null -w 'Unauthenticated HTTP %{http_code}\n' http://127.0.0.1:15481/api/v1/events
curl -fsS -H "Authorization: Bearer $HORIZONT_API_TOKEN" http://127.0.0.1:15481/api/v1/events
```

The `curl` JSON response is fictional and suitable for the private terminal, but never submit future **real** API output into the public GitHub repo. Do not use this demo token for any production deployment. Close the terminal to discard the exported shell variable, or run `unset HORIZONT_API_TOKEN` when done; note that an already running demo container retains its copy until stopped.

## Verification and status

`GET /health` is public generic process liveness, not data freshness. `GET /ready` requires bearer and only tests the synthetic DB. Data endpoints require bearer and use only fixed, parameterized read-only SQLite queries; failed availability returns 503 rather than a false empty dataset. This is **not** a browser-facing authenticated session architecture. Do not embed bearer credentials in `preview/app.js` or expose this API through Cloudflare, Tunnels, MCP, Contabo or LAN. The current UI is still synthetic and unchanged.

Before deploying a real user SQLite volume we need a separate design for dedicated path/permissions, WAL/SHM, backup/restore, secret handling and browser auth. No production data volume may be connected to this demo by merely editing an env var: the database path in Compose points at the **fixed image fixture**.

## Scoped rollback

```sh
docker compose --env-file /dev/null -p horizont-api-lab -f deploy/lab/compose.api-demo.yaml down
unset HORIZONT_API_TOKEN
```

This removes only the `horizont-api-lab` demo service/network; HORIZONT preview `horizont-lab`, Apple Bridge, SKS and other services are not targeted. **Never use Docker prune, `down -v` or global Docker reset.** No persistent SQLite volume is created; this demo image can be removed separately only after confirmation it is unused by other projects.

The CI test proves an isolated GitHub runner can build/start the demo, not that Mid-2014 has enough resources. Runbook and test status must be updated after actual on-device validation.
