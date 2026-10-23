# GOV.UK News Atom offline adapter

## Scope and safety

`src/horizont/adapters/govuk_atom.py` parses caller-supplied Atom 1.0 XML with
Python's standard library. It has no HTTP client, scheduler, background task,
third-party dependency, or feed fixture containing a real article. The fixture
uses fictional text and `example.test`. A future collector needs its own review
and authorization; this adapter does not download anything.

The parser accepts at most 128 KiB and 20 entries. It requires the Atom 1.0
namespace and feed metadata, rejects DTD/entity declarations, malformed XML,
unsafe article URLs, duplicate Atom IDs, titles or canonical URLs, and invalid
timestamps. It only accepts HTTPS links on `www.gov.uk` or the synthetic test
host `example.test`; it never dereferences XML links. HTML summaries, content,
images, authors and extensions are not stored.

## Data semantics and provenance

Each entry is an official publication or announcement, **not a verified
incident**. Its row is `pending`, has unknown geography and no coordinates.
GOV.UK's jurisdiction is not the event's location; this adapter never assigns
one. A GOV.UK publication must not be used as independent corroboration of its
own reported event.

| Field | Meaning |
| --- | --- |
| `published_at` | Atom entry `<published>`, if present; normalized to UTC. |
| `updated_at` | Atom entry `<updated>`, if present; kept in the in-memory normalized object only because schema v2 has no such column. |
| `collected_at` | Caller-supplied, timezone-aware time the payload was collected; normalized to UTC. |
| `occurred_at` | Always unknown (`NULL`); a publication time is not an event time. |

The canonical alternate article URL is preserved in event provenance. The
fixed source ID `govuk-news-atom-v1` and URL-derived SHA-256 dedup key are
independent of GDELT. GOV.UK is recorded as source attribution. No entry body,
summary or raw feed XML is persisted. The optional importer uses the existing
SQLite core and atomic run path; any write failure rolls back the whole batch
and records a failed run. A well-formed empty feed is `empty`, while malformed
XML or a failed import is `failed`, never an assertion of zero incidents.

No database migration is included. Because schema v2 has no `updated_at` or
Atom ID field, those values are available from the parser result but are not
persisted by the optional importer. Persisting them requires a separately
reviewed architecture/schema decision.

## Open Government Licence v3

GOV.UK says its content is available under the Open Government Licence v3.0
except where otherwise stated. Reuse must retain attribution and follow the
licence. For any future display or export of licensed GOV.UK information, use
an attribution in this form, populated from the specific item:

> [Title], GOV.UK / [publishing department], [publication date], source: [canonical article URL]. Contains public sector information licensed under the Open Government Licence v3.0: https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/

The National Archives guidance says attribution should identify the
information provider and source; where available it should also name the
copyright owner and publication/creation date and link to the OGL. The licence
is not blanket permission for material identified as third-party copyright,
personal data, or excluded items such as logos and insignia. Check an item's
own rights notice before reusing its text or media. This MVP stores only title,
dates and canonical URL; it does not copy article body or media.

References:

- [GOV.UK reuse of content](https://www.gov.uk/help/reuse-govuk-content)
- [GOV.UK terms and conditions](https://www.gov.uk/help/terms-conditions)
- [Open Government Licence v3.0](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/)
- [The National Archives attribution guidance](https://www.nationalarchives.gov.uk/information-management/re-using-public-sector-information/uk-government-licensing-framework/open-government-licence/copyright-notices-attribution-statements/)

## Real-feed one-shot observation (2026-10-23)

A separately approved single HTTPS GET from an isolated GitHub Actions runner
returned HTTP 200, `application/atom+xml`, 12,531 response bytes and **20 Atom
entries**, with an Atom 1.0 feed root and feed-level id/title/updated elements
present. The approved request had no redirects/retries/proxies and did not
persist live XML or titles. The original 10-entry limit correctly rejected
that entire response. See Issue #56 and the read-only format report in run
39970594333 (GitHub Actions). The temporary workflow was removed after the run.

This isolated offline-only adjustment raises the *strict whole-feed bound*
to **20**, leaving the XML byte cap (128 KiB), URL restrictions, and failure
semantics unchanged. Synthetic tests prove 20 unique entries are parsed and
imported without silent truncation, while 21 entries fail closed and do not
change persisted event counts.

**Do not claim full real-feed compatibility** from the observed response:
the runtime did not retain the real body, and the parser may have other
unsupported live fields/URLs/timestamp variants beyond the original
10-entry rejection. Never silently take only the first ten. A new explicit
approval and separate isolated gate would be necessary for another live
compatibility check. This change does not enable fetching or scheduling.

## Follow-up live parser gate #59 (2026-10-23)

The owner's separately authorized **one-shot** GitHub Actions run 39987663445
received a second actual GOV.UK Atom response: HTTP 200,
`application/atom+xml`, 12,134 bytes, 20 entries. The merged parser accepted
20/20 items in RAM, with `pending` validation, unknown event occurrence, and
unknown geography. No real data was imported or stored. The temporary workflow
was removed. This is **one real-feed snapshot only**, not ongoing availability,
continuous collection authorization or confirmation of the announcements.

## Proposed one-shot transport (Issue #60; not activated)

The standalone `src/horizont/adapters/govuk_atom_collector.py` module adds
`collect_once(collected_at=..., permit_network=True)`. Importing it or calling
it without explicit opt-in performs **zero HTTP requests**. The module is not
imported by any live service, CLI, scheduler, cron, Docker Compose or pipeline.
This PR includes no live requests and its tests use fake in-memory responses
only.

The collector asks for a single immutable official Atom URL over HTTPS using
the Python standard library: default verified TLS, ambient proxy bypass,
HTTP redirects rejected, and *no retries*, including for HTTP 429. It requests
`application/atom+xml`, identity encoding, uses a short socket timeout and
requires exactly HTTP 200, exact response URL, Atom media type, acceptable
Content-Length and a complete, bounded body of at most 128 KiB. The reader
takes at most 128 KiB + 1 byte once to detect over-size, then calls existing
`parse_feed`. It returns a `PublicationFeed` object in memory. It neither
dereferences article URLs nor calls `ingest_payload` or changes SQLite.

Failures use short, fixed reason categories; raw XML, real article titles,
URLs, response headers and credentials are never used in exception text.
Testing covers synthetic 20-entry acceptance/21-entry rejection, empty/missing
Content-Length, HTTP errors and redirects, unexpected URL or media type,
compression, over-large/truncated payload, invalid date and unsafe XML.

**Outstanding operator gates:** (1) the opt-in flag is only an accidental-call
guard, not actual authorization; (2) the HTTP socket timeout is **not** a hard
end-to-end wall-clock deadline against slowly dripping responses. Any real
scheduled or host-based use must first add an isolated process-level deadline,
bounded rate/schedule, runtime log review, source-licence attribution policy,
explicit owner go/no-go, and separate persistence approval. No new provider
GET is authorized by this PR. No application-wide import/wiring/deployment.

## MVP limitations

- The parser remains offline; the opt-in one-shot transport is separate and not enabled.
- No active provider polls, scheduling, API/UI wiring, host deployment or live SQLite import.
- No HTML/body extraction, language detection, country assignment or
  geolocation.
- Atom `updated` and `id` are not persisted by the current SQLite schema.
- URL allowlisting is intentionally narrow; GOV.UK publishing/link conventions
  should be checked again before any separately approved live collection.
- A publication's factual claims and any event described require independent
  validation.
