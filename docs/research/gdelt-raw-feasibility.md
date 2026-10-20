# GDELT 2.0 raw 15-minute feeds — offline feasibility spike

**Status:** research/prototype, not a network collector or production event source. This is the **private, non-commercial POLISH HORIZONT** project; GitHub hosting identity is not project ownership. No host changes.

## Verified background and references

Official GDELT 2.0 introduction: https://blog.gdeltproject.org/gdelt-2-0-our-global-world-in-realtime/
Official datasets: https://www.gdeltproject.org/data.html
Official combining guide: https://blog.gdeltproject.org/complex-queries-combining-events-eventmentions-and-gkg/
Terms/attribution: https://www.gdeltproject.org/about.html#termsofuse

GDELT advertises three continuously published 15-minute datasets:
- **Events / `*.export.CSV.zip`**: automatically extracted coded event relations (CAMEO, actors, tone and extracted places). Neither a confirmed real-world event nor a ground-truth geolocation.
- **Mentions / `*.mentions.CSV.zip`**: associations of detected event identifiers and news-document mentions. `GLOBALEVENTID` is a join key *within GDELT*, not proof of independent corroboration.
- **GKG / `*.gkg.csv.zip`**: article/document-level thematic, person, organization and location extractions. Join GKG to mentions via originating article/document URL only after schema validation; do not assume one-to-one matching.

There are two manifest streams: `lastupdate.txt` (English) and `lastupdate-translation.txt` (translingual), alongside corresponding historic master lists. They are distinct populations, not duplicate mirrors. Official references indicate an approximately 15-minute publish cadence; current live availability, transport security, size, latency and any rate limits **were not measured** during this spike.

## Manifest and local ZIP contract

The manifest has **exactly three rows**, each containing decimal compressed byte length, 32-hex-character MD5, and an archive URL on `data.gdeltproject.org/gdeltv2/`. All rows must share the same valid UTC 15-minute timestamp and the appropriate `.translation` designation. The parser accepts legacy HTTP link *text* but returns an HTTPS URL; **it does not perform a request**. We cannot assume a particular historical host's HTTPS availability until a separate, approved test.

The prototype `horizont.adapters.gdelt_raw` performs only:
- strict, bounded in-memory manifest decoding and timestamp/checkpoint validation;
- allowlist for exact file type, URL host, directory, suffix, archive counts, no URL query/userinfo;
- optional bounded validation of an **existing local** ZIP with MD5, size, exactly one expected member, anti-symlink/traversal guard, capped compressed and decompressed bytes and compression ratio, and capped UTF-8 TSV line length, columns and row count;
- return in-memory rows for a small synthetic test. No extraction to disk, HTTP client, scheduler, SQLite/schema migration or rendered UI change.

MD5 is an **accidental corruption check, not an authenticity/security guarantee**. The future transport must establish an authenticated channel, respect provider terms, and never trust a manifest to choose arbitrary hosts. For real large feeds, replace in-memory tuple aggregation with a streaming consumer and atomic checkpoint persistence. No claims of unrestricted access, 429 immunity or safe automatic downloading are made.

The parser deliberately **does not hardcode 61/16/27 columns** without reconciling the official Event/Mentions/GKG codebooks and inspecting an approved bounded sample. A caller may specify an expected width after independent confirmation. The synthetic examples in tests are not representative content from GDELT or any publisher.

Default research limits: manifest 4 KiB, archive 64 MiB compressed, parsed content 8 MiB decompressed (128 MiB ceiling), 32 KiB per line, at most 5000 rows, compression ratio at most 200:1. **These are safety ceilings for an offline spike**, not measurements of actual GDELT files; genuine feeds may exceed them. The code will fail closed rather than quietly truncate data.

## Timestamp semantics — do not conflate these

| Data field | Treat as | Must NOT automatically mean |
| --- | --- | --- |
| Events `SQLDATE` | Day associated with detected event, as coded by GDELT | Verified precise occurrence instant |
| Events `DATEADDED` | When a GDELT event record was added | Publisher publication time |
| Mentions `EventTimeDate` | GDELT event-related timestamp per codebook | Verified incident timestamp |
| Mentions `MentionTimeDate` | GDELT mention processing/update interval | Guaranteed original publication time |
| GKG `DATE` | GDELT record/document detection-related time | Verified publication date or incident date |
| HORIZONT `collected_at` | When our adapter actually observed/saved data | Any upstream date |

A future schema should separate **publication observations** (origin URL, attributed publisher, seen-at, collected-at) from **correlated machine events**, with source and confidence/precision explicit. No automatic certainty upgrade to `confirmed`; extracted geography remains suspect until validated, including known GDELT geocoding errors.

## Checkpoint and safety gates

Future fetcher plan: download only the 4-KiB-ish manifest with explicit maximum bytes; compare tuple `(stream, timestamp)` against a durable checkpoint; refuse replay/stale timestamps; allow a maximum one approved archive boundary; validate all three entries before any data write; verify archive size and checksum; stream parse with byte/row budgets, write selected observations atomically, **then** advance checkpoint in the same durable transaction. Track English/translation separately, also avoid double-counting a publisher URL across streams. Enforce bounded retries/backoff rather than shifting IPs to evade quotas.

Never map `ActionGeo_*` directly to a real-world map pin without verification, or `SOURCEURL` publisher to incident country. Source classification and incident confidence must be separate.

**Risks to verify before the next phase:** official transport HTTPS support for current manifests and archive URLs; current real file sizes; provider load and licence/attribution; GDELT codebooks and actual field widths; timestamp/data-quality semantics; CPU/RAM/disk performance on isolated temporary fixtures; availability under reasonable fetch cadence. No implementation or connection to our running hardware, Apple Bridge/SKS, Cloudflare, Contabo or the existing PL/EN preview was authorized or performed by this spike.

## Rollback

Delete the additive module, tests and this design document (or revert the PR). The module never touched a persistent database or any running service.
