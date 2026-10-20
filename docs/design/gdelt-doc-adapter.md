# GDELT Project DOC 2.0 offline adapter

The standard-library adapter in `horizont.adapters.gdelt_doc` builds bounded
DOC 2.0 article-list URLs and normalizes caller-supplied JSON. It does not make
HTTP requests and does not fetch publisher URLs. Its fixture and tests use
synthetic content and the reserved `example.test` domain only.

## Meaning and provenance

Each returned item is a **news mention**, not a confirmed real-world event.
The stable `gdelt-project-doc-v2` source ID records the GDELT index as the
ingestion/provenance source. `original_url` retains the publisher URL (with
fragment removed) and the title and language are metadata supplied by the
index. A URL-scoped SHA-256 key enables repeat-import deduplication; the title
does not form part of that key.

GDELT DOC `seendate` represents when GDELT saw/indexed an article. It is kept
as `Article.gdelt_seen_at` in normalized output and is never treated as the
publisher's publication date or the date an incident occurred. The current
SQLite core has no dedicated GDELT-seen timestamp, so ingestion intentionally
does not persist it. `collected_at` records when this local import was made;
`published_at` and `occurred_at` remain NULL unless a future, separately
validated process can establish those dates.

The GDELT `sourcecountry` label describes the source and may use a GDELT/FIPS
label. It is exposed only as `Article.source_country_label`; it is not mapped
to the event's `country`. Event country and coordinates remain NULL, with
unknown precision and pending validation. The adapter therefore creates no
geolocated or confirmed incident pins.

## Request and response contract

`build_search_url` produces the GDELT Project DOC endpoint URL with
`mode=artlist`, `format=json`, UTC-normalized whole-second bounds, and at most
10 requested records. Callers own any later transport; this module does not
provide one. `normalize_response` accepts supplied JSON only and rejects a
malformed object, invalid record, missing title, invalid URL/date, duplicate
canonical URL, or response beyond the requested bound. A valid empty
`articles` array is an empty result. `ingest_payload` validates the full batch
before writing through the existing SQLite core; valid empty and malformed
responses are recorded as distinct `empty` and `failed` runs.

`possibly_truncated` is set when the response count reaches the requested
limit. This is a conservative signal, not proof that additional matching
articles exist.

## Attribution and limits

Credit the **GDELT Project** and link to [gdeltproject.org](https://www.gdeltproject.org/).
This adapter targets the GDELT Project DOC 2.0 endpoint, not GDELT Cloud.
It stores metadata and publisher links only; it does not store article body
text, excerpts, images, or image URLs. Rights to publisher content and media
are separate from GDELT attribution. Review the applicable GDELT Project and
publisher terms before redistribution or broader use; this MVP does not make
a legal determination or claim blanket reuse permission.

No live endpoint, rate limit, uptime, or result quality was checked. The
adapter does not validate whether a title is accurate, whether a publisher
URL is still available, whether a date is truly a publication date, or
whether a report describes a confirmed event. GDELT-seen time is not stored
in SQLite pending an explicitly approved schema extension.
