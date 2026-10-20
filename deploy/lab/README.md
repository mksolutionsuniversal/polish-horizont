# HORIZONT / first isolated Docker LAB (macOS Sonoma OCLP)

**Status: deployment packaging only. No owner device, existing service or live data is changed by this PR.** This is an operator-private, non-commercial HORIZONT project. No company owns or operates the project merely because the source repository resides on a particular GitHub account.

This LAB deliberately contains **only the already approved static PL/EN preview with synthetic `example.test` fixtures**. It is **not** a connected OSINT dashboard, backend, MCP gateway, SQLite API or collector. Those will be built as independently reviewed services in later PRs. None of our raw GDELT or GOV.UK adapters run here.

## Why this platform

The latest internal ADR-011 designates **MacBook Pro Mid-2014 / macOS Sonoma 14 via OCLP / Intel amd64 / Docker Desktop** as the preferred candidate LAB, following the owner's successful personal Docker test. This is not yet an independently validated co-residency test. Ubuntu is fallback. No existing macOS services, tunnel, auth config or ports can be changed as part of this package.

## 0. Read-only preflight, executed locally by owner

In a fresh checkout of this Git commit from the repository root:

```sh
sh deploy/lab/preflight-macos.sh
```

This displays only OS/Docker versions, architecture, available RAM/disk, checks that localhost port 15480 is not busy, and validates Compose syntax. No credentials, Docker volumes or other service names are read or reported; no containers start. It does **not** independently establish available Docker Desktop VM RAM, persistence or the health of other services.

**Before running `up`:** privately record current health/latency of all important services running on this Mac, assess Docker VM memory and disk settings, and arrange/verify recoverable backup of **only HORIZONT-created assets**. Ensure the machine can run containers alongside existing workloads without unacceptable pressure. Do not share private operational logs in a public issue or PR.

If port 15480 is in use, choose another unused unprivileged port locally, for example `HORIZONT_LAB_PORT=15481`, and rerun preflight. Every Compose operation is explicitly scoped with `-p horizont-lab` so a shell `COMPOSE_PROJECT_NAME` cannot point to another project, and `--env-file /dev/null` disables automatic repository `.env` overrides. The optional exported `HORIZONT_LAB_PORT` still works, but it must be used consistently for preflight, launch, and the browser address. Check the selected Docker context is a **local Unix socket** (preflight rejects remote contexts).

Only host address **127.0.0.1** is published. This first preview has no persistent volumes, databases, secrets, application-initiated external requests or authentication service. **Security tradeoff:** Docker Desktop needs the preview attached to a standard project-scoped bridge for host-port publishing. A standard bridge permits potential outbound network access; we therefore do **not** claim egress is blocked by this Compose file. The application currently serves only bundled synthetic files and has no fetcher. If future code starts making outbound requests, add an explicit egress policy before enabling it. Future SQLite/DB services belong to a separate `internal: true` network.

## 1. Manual first launch — only after preflight is satisfactory

```sh
docker compose --env-file /dev/null -p horizont-lab -f deploy/lab/compose.yaml config --quiet
docker compose --env-file /dev/null -p horizont-lab -f deploy/lab/compose.yaml up -d --build preview
docker compose --env-file /dev/null -p horizont-lab -f deploy/lab/compose.yaml ps
```

Browse `http://127.0.0.1:15480/` on this machine. You should see the same PL/EN synthetic preview as opening `preview/index.html` locally. Check that PL/EN toggles and time filters work; this does not establish compatibility with a real SQLite-backed API.

If a different port was selected, prefix all relevant commands with the same `HORIZONT_LAB_PORT=...` environment value and open that local port instead.

**Isolation:** Compose project is named `horizont-lab`; its bridge network is project-scoped, **not** Docker `internal` (to make macOS localhost port forwarding work). This allows potential egress, although the preview itself has no outbound application logic. Container root FS is read-only; nonroot UID 10001; drops Linux capabilities and blocks privilege elevation; RAM 128 MiB, CPU 0.5, process limit 64. No shared mounts or Docker socket. The preview web server listens on `0.0.0.0:8080` **inside the isolated container only**; macOS bind is localhost. `restart: no`, so it should not be restarted as a resident daemon by this Compose project after a stop. Docker Desktop's VM still has overhead: measure actual host usage.

### Repairing an already-running LAB created with the earlier internal network

The first Mid-2014 pilot produced `Health=healthy` and `HTTP 200` **inside** the container, but Docker Desktop showed only `8080/tcp` and returned `curl (7)` from the host. The original `internal: true` network intentionally had no external gateway and prevented the Desktop port forward. The updated project-scoped bridge corrects that, at the explicitly documented egress tradeoff above.

**After pulling this reviewed fix and checking that this is still the correct local Docker Desktop context**, use a scoped stop/recreate. It touches only the `horizont-lab` preview service and its network. Do not rebuild or pull the already-installed image `horizont-preview:lab`:

```sh
docker compose --env-file /dev/null -p horizont-lab -f deploy/lab/compose.yaml down
docker compose --env-file /dev/null -p horizont-lab -f deploy/lab/compose.yaml up -d --no-build --pull never preview
docker compose --env-file /dev/null -p horizont-lab -f deploy/lab/compose.yaml ps
docker compose --env-file /dev/null -p horizont-lab -f deploy/lab/compose.yaml port preview 8080
curl -fsSI --max-time 5 http://127.0.0.1:15480/
```

Expected: `127.0.0.1:15480->8080/tcp` in the PORTS column, `healthy` after startup, and HTTP 200. If unsuccessful, do not change other containers, networks, firewall rules, credentials or Docker Desktop global settings; read scoped logs and inspect only HORIZONT. Before and after the recreation, separately check that Apple Bridge/SKS and other existing services continue working.

## 2. Acceptance and regression check

- Healthy Compose status, correct page on loopback, no external/public reachability.
- Existing local services have the same healthy responses before and after launch.
- PL/EN toggle, mobile viewport, keyboard operation and UTC filters behave as in the accepted mock.
- Host CPU/RAM remains safe during idle and interaction; check sleep/wake and restart implications before leaving the LAB running.
- Verify no real source/API requests are made by this mock; nothing operational is displayed as confirmed data.
- Confirm `docker compose ... down` affects only this uniquely named project.

## 3. Rollback — does not destroy unrelated service data

```sh
docker compose --env-file /dev/null -p horizont-lab -f deploy/lab/compose.yaml down
```

This stops/removes **only** the containers and private network belonging to the explicit `horizont-lab` project. Do **not** run `docker system prune`, `docker volume prune`, `down -v`, or delete unknown anonymous volumes. The first preview service has no persistent data to restore; future SQLite LABs must add separate volume backup/restore tests before starting.

If other services regress, stop **only** this Compose project and verify the preflight health baseline. Rollback to a previous source commit or rebuilt image is available separately.

## 4. Future services (not implemented yet)

Introduce one service at a time after its own design/testing: authenticated local read-only API -> isolated SQLite data volume with verified backups -> bounded collector/checkpoints for legally approved feeds -> UI data binding -> optional private MCP. A full-stack test and longer reliability assessment are required before any external hosting. Existing VPS/tunnels/auth infrastructure remain untouched. Public GitHub Actions never deploy to the LAB.

**Do not treat this packaging PR or GitHub CI as a successful MacBook deployment.** Actual host deployment must be executed and checked locally after a satisfactory preflight.
