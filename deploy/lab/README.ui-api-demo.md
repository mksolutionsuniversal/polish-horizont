# POLISH HORIZONT — optional UI ↔ fictional SQLite Docker LAB (Issue #34)

**This is a third, independent demonstration project**, not a change to the accepted PL/EN preview or the existing bearer API. Private/noncommercial HORIZONT is not an MK Universal Solutions Ltd project merely because the GitHub hosting account belongs to it.

## What it is

- Project: `horizont-ui-api-lab`, container/image `horizont-ui-api:demo`, browser URL `http://127.0.0.1:15482/` on the **Mid-2014 itself**. Host bind is **127.0.0.1 only**.
- It serves the original layout/CSS and event component JS from `preview/`, but the embedded render data is **built server-side from the fictional image-baked SQLite database**. It renders **two** synthetic events and **two** fictional sources instead of the independent static preview's 4/6 fixtures. Original title is deliberately shown unaltered in both PL/EN UI modes; the database has no trustworthy title language, so only each source's actual language is tagged.
- Its read-only JSON projections `GET /data/v1/events`, `GET /data/v1/sources` are on the **same origin**. Public `GET /health` is generic process health only. No browser bearer or cross-origin access; the original 15481 API remains bearer-protected and **is not proxied or changed by this project**.
- This demo's data is fictional and cannot be switched to a real DB by a Compose flag. No bind volumes, secrets, real provider requests, cron or host DB. The standalone image build generates the same synthetic SQLite fixture as our API demo. **Unprotected data routes are permitted only for fictional samples. They are NOT a private-data auth design.**
- A regular project-scoped Docker bridge is required for localhost port forwarding and allows potential network egress. No external requests exist in this server. Nonroot UID 10001, read-only image filesystem, drop capabilities, resource caps, no restart policy and no arbitrary file access endpoints.

## Gates before running on Mid-2014

Verify locally that `horizont-lab-preview-1` and `horizont-api-lab-api-1` stay healthy, and separately check fresh Apple Bridge/SKS health and host Docker Desktop VM memory. Review the **accepted PR, green CI, security notes** and code before running. This image has not yet been tested on the Mid-2014 when the implementation PR is first opened.

After merge only, in the **interactive local Terminal** of Mid-2014:

```sh
cd ~/polish-horizont
git pull --ff-only
docker compose --env-file /dev/null -p horizont-ui-api-lab -f deploy/lab/compose.ui-api-demo.yaml config --quiet
docker compose --env-file /dev/null -p horizont-ui-api-lab -f deploy/lab/compose.ui-api-demo.yaml up -d --build ui
docker compose --env-file /dev/null -p horizont-ui-api-lab -f deploy/lab/compose.ui-api-demo.yaml ps
curl -fsSI --max-time 8 http://127.0.0.1:15482/
curl -fsS --max-time 8 http://127.0.0.1:15482/data/v1/events
```

Observe `healthy`, HTML HTTP200, exactly two fictional items with embedded provenance, and a distinct synthetic-only label; review PL/EN and UTC filters. The synthetic records should **not** imply original translations, verified incident coordinates, news source freshness or active collectors. Observe >=15 minutes and compare host/Apple Bridge/SKS health without restarting them. If viewing remotely from MBP2019, use a dedicated SSH local tunnel, not a LAN bind.

## Security caveats

The server guards the Host header, rejects cross-origin and cross-site requests when indicated, prevents embedding via CSP frame-ancestors and X-Frame-Options, and does not set `Access-Control-Allow-Origin`. It is a **small WSGI LAB HTTP server**, not a hardened production auth gateway. It has zero real operator data. Do not attach real persistent SQLite volumes or expose this service on LAN, Cloudflare, MCP or Contabo. Before any such use, implement and test private sessions, authorization, CSP, input controls, audit handling and backup/restore as a *separate* reviewed phase. This is a fictional data demonstration, not real GDELT/GOV.UK monitoring.

## Scoped rollback

```sh
docker compose --env-file /dev/null -p horizont-ui-api-lab -f deploy/lab/compose.ui-api-demo.yaml down
```

Never use `down -v`, `docker system prune`, delete other networks, or change other Compose projects. Existing `horizont-lab` on 15480 and `horizont-api-lab` on 15481 are untouched. To revert the code, use an explicit Git commit revert via reviewed PR rather than wiping the host checkout.
