# Source lineage and claim-level attestation v1

**Status: PROPOSED / NOT ACCEPTED / NOT DEPLOYABLE**  
**Scope:** design only for future provenance and human review. No SQLite changes, migration, real-source inspection, source download, or runtime lineage code.

## Context and invariants

The proposed [source/evidence model v1](source-evidence-v1-adr.md) distinguishes
provider observations, publisher records, event hypotheses, evidence, and
audited decisions. The current production schema is still v2. The v3 spike
merged in [PR #47](https://github.com/mksolutionsuniversal/polish-horizont/pull/47)
is synthetic and in-memory only; it is not a production lineage mechanism or
trusted identity system. See also the [operator review matrix](evidence-review-authority-v1.md).

Core rules for any future design:

1. A GDELT Events row describes what GDELT emitted. The publisher article it
   cites is the underlying publication; these are one lineage for a claim, not
   two independent confirmations.
2. A GOV.UK Atom item is primary evidence that GOV.UK issued that announcement.
   Its existence alone does not verify the physical event described.
3. Same `observation_id`, same `source_record_id`, or same canonical URL is a
   hard negative for an independence decision between the compared evidence.
4. Different record IDs, providers, domains, URLs, or identifiers are
   insufficient for a positive decision. They are only identity distinctions.
5. Positive independence is claim-specific and requires traceable source
   lineage plus a human owner attestation. Unknown or disputed lineage never
   contributes to an independent count. AI/collectors can propose edges or
   concerns only.

## Proposed lineage graph

Model the path of the claim, not just URL deduplication. Graph changes and
review decisions are versioned. Each edge has a direction from origin/parent to
derived/child and a precise scope:

| Relation | Meaning | Default claim effect |
| --- | --- | --- |
| `cites` / `quotes` | Child publication attributes a statement to the parent. | Dependent for the quoted claim; other new claims in the child require separate review. |
| `syndicated_from` | Child republishes agency copy or common wire text. | Dependent for claims carried in that material. |
| `translated_from` | Child translates the parent. | Dependent for translated claims, even if wording and URL differ. |
| `copied_from` / `mirrors` | Near-verbatim copy, mirror, or repost. | Dependent for copied claims. |
| `summarizes` / `derived_from` | Child summarizes, rewrites, or otherwise relies on parent. | Dependent where claim provenance traces to parent; otherwise unknown until claim-level review. |
| `same_official_release` | Several pages reproduce one government or organization release. | Dependent for claims sourced from that release. |
| `controlled_by` / `editorially_controlled_by` | Publication belongs to an ownership or editorial-control entity over a validity interval. | Dependency signal; never automatic proof of independence or dependence across unrelated claims. Requires evidence and review. |
| `separately_reported` | A reviewer has traceable evidence that this claim has separately gathered reporting. | Candidate basis for independence, not an automatic acceptance. Requires owner attestation. |
| `corrects` / `retracts` | A later source revision corrects or withdraws earlier content. | Marks affected claim/source revision for re-review and excludes stale support from active corroboration. |

Edges must attach to `claim_id` or a narrowly defined claim scope when that
relationship does not apply to the whole item. Do not propagate a quoted claim
to every statement in a publication. Store the edge's basis (source reference
or reviewer note), creator, observed time, asserted valid interval where
known, graph version, and decision state. Machine extraction can suggest a
candidate edge but it remains `proposed` until review.

Transitive dependency is conservative: if A is syndicated from B and B cites
C for claim X, A is dependent on C for X unless a reviewer records evidence of
separate original reporting on X. A chain containing unknown ownership,
unreviewed translation, or an unresolved relationship is `unknown`, not
independent. Cycles, inconsistent time intervals, or corrupted graph versions
make the affected claim `unknown` and remove it from independent corroboration
until resolved.

## What qualifies for positive independence

For one named claim and one current evidence revision, a human reviewer must
inspect an auditable lineage snapshot and a separately described basis for
original reporting. Possible basis categories include direct witness
interviews, independently obtained primary documents, or independently
collected observations. Merely presenting a different publisher name, a
different URL, a separate database row, an AI similarity score, or the
absence of a known citation is insufficient.

The accepted attestation should record:

- exact `hypothesis_id`, `claim_id`, evidence revision IDs, source-record IDs,
  and current lineage graph version/digest;
- outcome `independent`, `dependent`, `unknown`, or `disputed`;
- relationship edges reviewed and separately described reporting basis;
- reviewer subject from the future authenticated operator session, policy
  version, decision time, rationale, and step-up/authentication reference;
- optional review-expiry time and invalidation triggers;
- `supersedes_id` for every later correction, revocation, or reassessment.

Positive outcome requires all referenced sources and graph facts to be
available and traceable. Do not store article bodies, private account data, or
unnecessary personal information to support the attestation. Store minimal
references, fingerprints only where rights permit, and license/attribution
metadata under separately approved retention rules.

## Corrections, retractions, and changing decisions

Source publication revisions are append-only. A later correction/retraction
creates a new source observation and a relation to the affected earlier
publication and claims. It does not overwrite or delete the old observation.
An owner may append a superseding lineage decision (`dependent`, `unknown`, or
`disputed`) that refers to the earlier decision and the newly discovered
relationship.

Whenever a dependency edge, ownership fact, source correction, retraction, or
lineage decision changes the graph snapshot used by an accepted independence
assessment:

1. The earlier decision remains in history but is no longer effective for the
   current graph version.
2. The active corroboration projection excludes that evidence pair and marks
   the claim `review_required`/`pending` until a human re-reviews it.
3. Any previously `valid` incident whose active support materially changed
   moves to a review-required state in the new projection; it must not silently
   remain confirmed. A new owner decision is required to restore `valid`.
4. A correction or retraction can contradict only the specific affected claim;
   preserve other claims and conflicting evidence for inspection.
5. If lineage evidence is missing, stale, or irreconcilable, fail closed to
   `unknown`; do not infer independence from a score or count.

Do not conflate the `review_required` projection with existing v2
`events.validation_status`. As demonstrated by the PR #47 synthetic spike,
legacy v2 `valid`/`invalid` values remain historical data; effective v3
verification would be derived only from explicit, append-only v3 decisions.

## Conceptual v3 data dictionary and query limits

Names below are design vocabulary, not migration-ready SQL.

| Conceptual record | Required contents and constraints |
| --- | --- |
| `publisher_entities` | Stable internal entity ID; entity kind (publisher, syndication agency, ownership/control group); display name and aliases; evidence reference; source and observed times; valid-from/to when known. Unknown identity is explicit. |
| `source_records` / observations | Existing proposed provider-neutral source identity and immutable revisions; retain provider namespace/ID, canonical URL as observed and normalized, publication times, source attribution/license, and collector time separately. URLs are not editorial identity. |
| `lineage_edges` | Parent/child source record or entity IDs; typed relation; claim scope; evidence basis; asserted and observed times; confidence as descriptive extraction metadata only; proposal/review state; actor and graph version. Unique key should scope duplicate edge detection by endpoints, relation, claim scope, and graph version; do not make URL alone unique. |
| `claim_lineage_reviews` | One versioned decision for a hypothesis claim and evidence revision set; graph version/digest; outcome; reviewer identity/policy version; rationale and reporting basis; reviewed time/optional expiry; superseding decision ID. Only a human owner may accept a positive result. |
| `source_revision_notices` | Correction, retraction, replacement, or re-publication relation to old revision(s) and affected claim(s); preserve prior versions and attribution. |
| `active_claim_corroboration` projection | Bounded read-only view over latest effective decisions only. Exclude proposals, stale graph versions, missing/unknown/disputed lineage, retracted claims, duplicate source record/observation/URL, and superseded decisions. Never treat row count as a truth score. |

Conceptual indexes: edge traversal by `(parent_record_id, claim_id, graph_version)`
and reverse lookup `(child_record_id, claim_id, graph_version)`; current-review
lookup by `(hypothesis_id, claim_id, decision sequence)`; revision notices by
source record and affected claim; immutable uniqueness over scoped decision
version/idempotency key. Exact SQLite partial/unique indexes, composite keys,
FKs and query plans require a separate schema design and acceptance review.
Bound graph traversal depth/node count and response size; detect cycles; return
`unknown` on cap exhaustion. A recursive traversal over a local SQLite graph is
not proof of completeness if a source/edge has not been collected or reviewed.

Keep read paths read-only. A future MCP query interface, if separately approved,
must expose only bounded lineage/read projections and must not inherit the
operator decision writer's authority. No production MCP surface is enabled by
this proposal.

## Licensing, privacy, retention, and rollback

For each source, preserve required attribution and license references and
evaluate whether storing content, snippets, hashes, derived metadata, or
ownership records is permitted. Prefer URL, normalized metadata, relation,
minimal evidence reference, and reviewer rationale; do not bulk copy articles.
Set source-specific retention and correction handling before real data. Keep
private identity attributes out of public outputs; expose a reviewer pseudonym
or internal immutable subject reference only where necessary. Restrict detailed
audit export to the owner and set retention/backup protection separately.

If a graph version is corrupted, an edge cannot be traced, or the policy engine
is unavailable, show no independent confirmation and require review. Recovery
uses a verified prior audit snapshot plus a new owner re-review; never restore a
stale positive status silently. This is conceptual rollback behavior only; no
database or application rollback code is proposed.

## Owner decisions required

1. Which relationship vocabulary and claim granularity are sufficient?
2. What evidence can establish separate original reporting, and how is it
   reviewed without retaining copyrighted/private source content?
3. How are ownership/control groups established and time-versioned?
4. Who may attest, how often does attestation expire, and what triggers an
   immediate re-review?
5. How should retractions and changes affect old `valid` incidents and
   country reports?
6. What source-specific licensing, privacy, retention, and audit-export rules
   apply?
7. Which future v3 query bounds, indexes, deletion/retention behavior, and
   backup/recovery policy should be implemented?

Until those decisions and a separate implementation issue are accepted, all
relationships and positive lineage outcomes remain proposals in documentation
only. No migration, schema activation, real source data, or runtime writer is
authorized.
