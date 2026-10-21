# POLISH HORIZONT

**Public code · Private data** — independent, privately operated, bilingual (PL/EN) OSINT research platform. This repository contains only source code, tests and non-sensitive configuration examples.

## Status (21 October 2026)

**Non-production, synthetic LAB prototype — not a live OSINT service.**

The source repository now includes a tested SQLite event/provenance core, offline research adapters, a bilingual PL/EN dashboard mock, and four separate **opt-in** Docker LAB examples:

- Static dashboard preview with fictional records.
- Read-only, bearer-protected API serving an isolated synthetic SQLite database.
- Same-origin dashboard rendering records from its own synthetic SQLite snapshot.
- Persistent SQLite WAL writer, sealed read-only snapshot API, and distinct Docker volumes for backup and restore exercises.

The opt-in LAB examples were run and tested on a local development machine; their operation is **not** evidence of production readiness. The latest persistence exercise and outstanding host checks are tracked in [Issue #39](https://github.com/mksolutionsuniversal/polish-horizont/issues/39). No live news feeds, scheduled collectors, trusted incident map, production MCP, or remote production deployment have been enabled. Synthetic events must never be presented as current real-world incidents.

Named Docker volumes used for LAB exercises are not, by themselves, an independent encrypted off-host backup. Before using real data, the project still needs fresh coexistence checks, sleep/wake and VM restart tests, and separately verified encrypted recovery.

The project is **private and non-commercial in purpose**; public code hosting by the repository account does not make it a commercial product. Private operator data, credentials and operational configuration stay outside GitHub.

## Planned modules

- Ingestion with source provenance, freshness and transparent failure states
- Timeline with separate occurred / published / collected timestamps
- MapLibre-based map and PL/EN dashboard
- Optional source-approved LIVE embeds
- Authenticated read-only MCP query interface

## Development & security

See [development workflow](docs/WORKFLOW.md), [security policy](SECURITY.md) and the [CI workflow](.github/workflows/ci.yml). Pull requests run basic hygiene checks and Python standard-library unit tests. The hygiene check is defense in depth, **not a guarantee that a change is safe**.

No automatic deployment, secrets in Actions or self-hosted runner. Repository discussions and test fixtures must never include data from real systems.

## Project governance

The private canonical project documentation and architecture decisions are held outside this repository. Public commits alone do not authorize any host changes.

No open-source license has been granted yet; licensing will be decided before substantive external contributions.
