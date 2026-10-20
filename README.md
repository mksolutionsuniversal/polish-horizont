# POLISH HORIZONT

**Public code · Private data** — independent, privately operated, bilingual (PL/EN) OSINT research platform. This repository contains only source code, tests and non-sensitive configuration examples.

## Status

Early planning and CI bootstrap. No HORIZONT application has been deployed. No production host, database, feed credentials, Apple Bridge/SKS or cloud infrastructure is connected to this repo.

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
