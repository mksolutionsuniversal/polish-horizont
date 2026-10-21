# GOV.UK News Atom offline adapter

## Scope and safety

`src/horizont/adapters/govuk_atom.py` parses caller-supplied Atom 1.0 XML with
Python's standard library. It has no HTTP client, scheduler, background task,
third-party dependency, or feed fixture containing a real article. The fixture
uses fictional text and `example.test`. A future collector needs its own review
and authorization; this adapter does not download anything.

The parser accepts at most 128 KiB and 10 entries. It requires the Atom 1.0
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

## MVP limitations

- No live feed fetch, retry policy, scheduling, or response transport.
- No HTML/body extraction, language detection, country assignment or
  geolocation.
- Atom `updated` and `id` are not persisted by the current SQLite schema.
- URL allowlisting is intentionally narrow; GOV.UK publishing/link conventions
  should be checked again before any separately approved live collection.
- A publication's factual claims and any event described require independent
  validation.
