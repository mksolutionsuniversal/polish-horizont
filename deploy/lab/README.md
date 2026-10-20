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

If port 15480 is in use, choose another unused unprivileged port locally, for example `HORIZONT_LAB_PORT=15481`, and rerun preflight. Only host address **127.0.0.1** is published. This first preview has no persistent volumes, databases, secrets, external requests or authentication service.

## 1. Manual first launch — only after preflight is satisfactory

```sh
docker compose -f deploy/lab/compose.yaml config --quiet
docker compose -f deploy/lab/compose.yaml up -d --build preview
docker compose -f deploy/lab/compose.yaml ps
```

Browse `http://127.0.0.1:15480/` on this machine. You should see the same PL/EN synthetic preview as opening `preview/index.html` locally. Check that PL/EN toggles and time filters work; this does not establish compatibility with a real SQLite-backed API.

If a different port was selected, prefix all relevant commands with the same `HORIZONT_LAB_PORT=...` environment value and open that local port instead.

**Isolation:** Compose project is named `horizont-lab`; its network is private/internal; container root FS is read-only; nonroot UID 10001; drops Linux capabilities and blocks privilege elevation; RAM 128 MiB, CPU 0.5, process limit 64. No shared mounts or Docker socket. The preview web server listens on `0.0.0.0:8080` **inside the isolated container only**; macOS bind is localhost. `restart: no`, so it should not be restarted as a resident daemon by this Compose project after a stop. Docker Desktop's VM still has overhead: measure actual host usage.

## 2. Acceptance and regression check

- Healthy Compose status, correct page on loopback, no external/public reachability.
- Existing local services have the same healthy responses before and after launch.
- PL/EN toggle, mobile viewport, keyboard operation and UTC filters behave as in the accepted mock.
- Host CPU/RAM remains safe during idle and interaction; check sleep/wake and restart implications before leaving the LAB running.
- Verify no real source/API requests are made by this mock; nothing operational is displayed as confirmed data.
- Confirm `docker compose ... down` affects only this uniquely named project.

## 3. Rollback — does not destroy unrelated service data

```sh
docker compose -f deploy/lab/compose.yaml down
```

This stops/removes **only** the containers and private network belonging to the explicit `horizont-lab` project. Do **not** run `docker system prune`, `docker volume prune`, `down -v`, or delete unknown anonymous volumes. The first preview service has no persistent data to restore; future SQLite LABs must add separate volume backup/restore tests before starting.

If other services regress, stop **only** this Compose project and verify the preflight health baseline. Rollback to a previous source commit or rebuilt image is available separately.

## 4. Future services (not implemented yet)

Introduce one service at a time after its own design/testing: authenticated local read-only API -> isolated SQLite data volume with verified backups -> bounded collector/checkpoints for legally approved feeds -> UI data binding -> optional private MCP. A full-stack test and longer reliability assessment are required before any external hosting. Existing VPS/tunnels/auth infrastructure remain untouched. Public GitHub Actions never deploy to the LAB.

**Do not treat this packaging PR or GitHub CI as a successful MacBook deployment.** Actual host deployment must be executed and checked locally after a satisfactory preflight.
