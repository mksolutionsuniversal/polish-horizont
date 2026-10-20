# HORIZONT read-only SQLite HTTP API — offline interface boundary

**Status:** Python standard-library WSGI API code; not yet a container, not started on Mid-2014, not connected to the approved PL/EN static preview, and no live data. Private/non-commercial HORIZONT; GitHub repo account is a hosting tool, not a business operator.

## Scope

Use the existing `horizont.core.queries` without modifying SQL semantics. All data endpoints require a strong private bearer token (at least 32 characters), set explicitly as a process environment variable and NEVER committed, printed, added to Google Drive or passed to browser JS. The server defaults to loopback 127.0.0.1:15481. Production/remote access will require independent authorization, TLS and further review. No frontend AJAX has been added.

| Endpoint | Auth | Function |
| --- | --- | --- |
| `GET /health` | No | Generic **process** health only, not data freshness |
| `GET /ready` | Bearer | Verify DB can be opened and its sources table read |
| `GET /api/v1/events` | Bearer | Query recent events using fixed parameterized SQL |
| `GET /api/v1/events/{positive_id}` | Bearer | One event with provenance, or 404 |
| `GET /api/v1/sources` | Bearer | Paginated source states and last-run summaries |

`HEAD` mirrors allowed `GET` status/headers without body. All other methods 405. No CORS; no arbitrary SQL, file reads by request path, writes, external HTTP, feed pulls, background schedules or auto database initialization.

Event filters: `time_field=collected_at|published_at|occurred_at`; `start` and `end` are aware ISO UTC offsets, half-open; `source_id` is an existing source identity; `country` is uppercase 2-character source-observed country, **not verified incident location**; `limit` 1–100 (default 50); `offset` 0–100000. Sources endpoint supports limit/offset with same bounds. Unknown keys, duplicates, oversized queries, bad dates and invalid numeric bounds result in 400, with no raw error echo.

**No fake zero on upstream failures:** missing or uninitialized local DB => HTTP 503 `data_unavailable` (not `{items:[]}`). An existing valid DB with no matching records returns a genuine empty result, distinguishable from source freshness values `failed`, `unavailable`, `never_run`, etc. `/health` is *not* readiness. Errors must not leak filesystem paths, SQL, raw provider errors or credentials.

## Security and data lifecycle

- Uses `open_readonly`, an existing file only, SQLite URI `mode=ro`, `PRAGMA query_only=ON`; closes connection on every call. Does not create or migrate any DB.
- Per-source status response excludes stored ingest-run `error_message`.
- Responses carry `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, and `CSP default-src 'none'`. No cookies, no CORS headers.
- This plain HTTP interface is **only for loopback laboratory development**; bearer token is not a substitute for end-to-end authenticated HTTPS over networks. **Do not expose it over LAN, Cloudflare, MCP or Contabo, or embed bearer in the public UI**.
- No demo user SQLite file, private data, tokens or host configuration is added to the public repository. Automated tests generate synthetic tempfile databases.
- The **existing preview Docker Compose stays untouched**; no new service, port, volume or image is activated by this change. Do not run on Mid-2014 yet.

## Testing and rollback

Run offline tests through `python3 -m unittest discover -s tests -v` (also on GitHub Actions). They cover bearer missing/wrong, event query filtering, provenance, status paging, 503 on missing DB, unrecognized query parameters, no mutations, and HTTP method safeguards.

Rollback of this feature: revert this PR's additive Python API module, tests and documentation. No runtime/binary service or persistent data exists to undo. Later container packaging and frontend integration require separate gates, explicit resource budgets and operator-side health comparison with Apple Bridge/SKS.

## Next integration gate

Develop a separate opt-in container for the authenticated API and ephemeral synthetic data first; inspect API response against test contract. Only after safe credentials handling, SQLite backup/restore and a vetted browser session/auth design may the static panel receive API data. GDELT Raw Events (Issue #24) and GOV.UK Atom (Issue #17) remain independent source tasks.
