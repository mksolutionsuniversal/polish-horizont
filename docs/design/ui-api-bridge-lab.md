# HORIZONT 2 — Stage A contract for a protected UI → SQLite bridge (Issue #34)

**Decision proposal for private, noncommercial POLISH HORIZONT.** The public GitHub repository holds only code, tests and fictional fixtures; it does not make the HORIZONT project a company product or authorize public service exposure.

## Verified baseline on 20 October 2026

- MacBook Pro Mid-2014, Sonoma 14.8.9/OCLP, Docker Desktop Intel. Current `horizont-lab` static PL/EN preview on host `127.0.0.1:15480`; approved visual layout, healthy/HTTP 200.
- Separate opt-in `horizont-api-lab` on host `127.0.0.1:15481` serves **only fictional SQLite**. Unauthenticated `/api/v1/events` returns 401; authorized returns 2 synthetic rows. Host later observed API healthy, FailingStreak=0, HTTP 200, about 13.72 MiB. PR #33 added bounded threads and socket timeout after a real host hang; **one healthy point-in-time is not yet proof of prolonged stability or proven cause**.
- Source and time semantics matter: SQLite `occurred_at` and `published_at` may be NULL; country is per-source observed provenance rather than verified incident location; `validation_status=pending` for demo; no live feed in either container.
- **Current dashboard has four separately embedded synthetic events + six synthetic source statuses**, not the same data as the API's two fictional events and two sources. Do not call this an integrated system yet.

## Important API mismatch and solution

The existing `GET /api/v1/events` lists rows with event ID/title/summary/timestamps/geolocation/validation, **but does not include event source provenance**. `GET /api/v1/events/{id}` separately returns `{event: {...}, sources: [{source_id, source_name, original_url, language, country, ...}]}`. The existing dashboard needs provenance on every card. Naïvely issuing one browser request per event would cause N+1 traffic, expose the bearer, add latency, and fail partial loads.

**Stage B must implement a bounded server-side read-model/projection** that obtains events *and their source relationships in one coherent SQLite read snapshot*, using existing parameterized queries or a new tested batch projection. No arbitrary SQL inputs. One list request should provide enough provenance to render the cards. Pagination max 100, bounded output; source status list is separate. Do not invent links, locations, translated titles or event times.

## Proposed isolated LAB topology (not yet implemented)

Instead of altering either already-running Compose project, add a third **opt-in** project, `horizont-ui-api-lab`, with a **single origin** at host `127.0.0.1:15482`:
- A nonroot, read-only, resource-limited Python container serves the **approved static PL/EN assets** byte-for-byte where possible (do not redesign CSS/layout) and read-only `/data/v1/events` + `/data/v1/sources` endpoints on the same origin.
- Only a **new fictional SQLite database generated in the image at build time** is read by server-side code. The browser never sees `HORIZONT_API_TOKEN`, any other bearer/secret, raw SQLite file, Docker volumes, or arbitrary filesystem paths. The existing 15481 API continues to be separately bearer-protected and is **not proxied or opened by the browser**. This third LAB demonstrates the same read layer and safe browser projection, not a production connection to live collectors.
- Host port publication strictly `127.0.0.1:15482`. Project, network and image have unique names, no mounts, no host DB paths, no auto restart, no CORS wildcard, no external assets. Dedicated regular bridge may technically allow egress; the demo has no HTTP fetching code. Tight read-only host/source checking is required to address DNS-rebinding/cross-site local access concerns.
- The demo data endpoint may be **unauthenticated only because all records are explicitly fictional and non-sensitive**. Treat this as a demo-only exception. **Before connecting ANY real OSINT/private SQLite**: separate authentication/session and browser boundary design; review CSRF (if introducing mutations), Host/Origin/Fetch-Metadata validation, TLS/identity gateway for remote access, secret lifecycle, backup/restore, rate limits and error/log minimization. No promotion by merely switching a DB path.
- No token in `preview/app.js`, HTML, URL, cookies, localStorage/sessionStorage or browser developer console. Never disable bearer on existing 15481 API.

## Data contract for browser

Server JSON is a **read-model**, not the raw internal database schema:

```json
{
  "synthetic": true,
  "as_of": "2026-10-20T12:00:00Z",
  "items": [{
    "id": "2",
    "title": "Fictional demonstration event",
    "summary": "Synthetic only — not a real incident.",
    "source_language": "en",
    "occurred_at": null,
    "published_at": null,
    "collected_at": "2026-10-20T11:00:00.000000Z",
    "validation_status": "pending",
    "geo_precision": "unknown",
    "latitude": null, "longitude": null,
    "sources": [{
      "source_id": "demo-uk",
      "source_name": "Fictional UK source",
      "original_url": "https://example.test/uk/fictional-1",
      "language": "en",
      "country": "GB"
    }]
  }],
  "limit": 50,
  "offset": 0,
  "country_filter_semantics": "source_observed_country"
}
```

This example is illustrative only; exact keys need tests and a reviewed explicit versioned schema. Original `title`/`summary` are in their observed language: **PL/EN UI localization does NOT imply source-text translation**. To maintain clear UX, render original text in both interface locales with a source-language badge. No synthetic 'translation' that implies real source content. Render any untrusted text with `textContent`, not `innerHTML`. The current translated HTML-only interface copy is static and trusted.

Server time filter semantics: UTC inclusive lower bound, exclusive upper bound. Missing timestamp remains unknown and **must not match a bounded time window**. Data endpoint `HTTP 503` must display an explicit unavailable/error notice (never `0 events`), while HTTP 200 with `items: []` represents a valid empty selection. If a previously loaded dataset is stale after failure, label it stale, never 'LIVE'.

## UI acceptance

- Pixel/layout structure and responsiveness of current preview unchanged; PL/EN, time filters and UTC preserved.
- Clearly visible mode `FICTIONAL SQLITE DEMO` (PL/EN). The original `15480` still shows its four fixtures; the new `15482` initially should display exactly two different fictional DB records. Do not mistake those for real GDELT data.
- Event cards include source provenance, observed country, validation, and unknown time/geolocation; no guessed map pins. Sources sidebar reflects real synthetic database source statuses, *not* six hard-coded example statuses.
- Wrong/missing data return explicit error state; no silent stale fallback to hard-coded fixtures.
- No client bearer, no secrets in static assets or HTTP response.
- No network cross-origin requests from browser.

## Test plan and deployment gate

Stage A (this document) is architecture only. Stage B may add a third opt-in Docker Compose and server-side read-model, unit tests for SQL snapshot and shape, Node DOM tests for PL/EN and error handling, and disposable GitHub Actions Docker smoke of same-origin UI and read-only data. Include case of an intentionally stalled client (cf. Issue #32), method rejection, origin/host checks, no sockets reachable on LAN, and no dangerous script/source injection.

Stage C, **only after code review and green CI**: owner checks Apple Bridge/SKS, Docker Desktop VM resources, captures baseline, manually builds/starts only the third project on Mid-2014 and verifies browser view + stable health >=15 min. Existing `horizont-lab` and `horizont-api-lab` must continue unaffected. Scoped rollback = `docker compose --env-file /dev/null -p horizont-ui-api-lab -f deploy/lab/compose.ui-api-demo.yaml down`; no volume pruning, no other stacks changed.

Stage D (future distinct ADR/Issue): authenticated single-operator UI backed by persistent real SQLite, collector ingestion, legal/quality checks and protected access. GDELT Raw Events mapping Issue #24 and GOV.UK Atom #17 proceed independently. No production Contabo/Cloudflare/MCP changes in these stages.

**Do not equate green CI with healthy actual Sonoma operation, or synthetic SQLite read with functioning live OSINT.**
