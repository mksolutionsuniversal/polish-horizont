# EXPERIMENTAL — SYNTHETIC, NOT DEPLOYABLE

This Stage B contract is a standalone Python test harness. It creates SQLite
only with `:memory:` internally, uses deterministic fake time and generated
identities, accepts only `https://example.test/...` source URLs, and performs no
network access or application startup. It is not imported by `src/horizont/`
and does not read or migrate production SQLite. `src/horizont/core/db.py` and
`SCHEMA_VERSION=2` remain unchanged.

## Synthetic operator contract

`FakeTrustedAuthContext` and `issue_test_session` are fixture injection seams,
not AuthN, identity proof, cryptographic tokens, WebAuthn/OIDC/MFA, or a real
authorization boundary. Roles and scopes are loaded from the harness's private
in-memory session table; the context's role assertion is untrusted. A
one-use synthetic step-up is bound to the action, actor/session, claim,
outcome, expected version, policy version, graph version, and request digest.
Decisions use in-process serialization, SQLite `BEGIN IMMEDIATE`, version
checks, idempotency, an append-only decision table, and an atomic projection.
This demonstrates a contract under unit-test conditions only. Any caller with
arbitrary Python execution can import the harness, issue fake sessions, or
reach its private connection; it is not safe to expose or reuse in an app.

AI/read-only clients and collectors can submit proposals only. They cannot
issue owner step-up, accept classifications, positive lineage, or incident
validation. Test-only session expiry/revocation constants are not production
TTL or recovery policy. No login, cookie, CSRF HTTP handler, provider, public
port, credential, account, or recovery bypass is implemented.

## Synthetic lineage contract

The graph models citations, syndication, translation, copies, shared official
releases, derivatives, separate-reporting claims, editorial control,
corrections and retractions. Shared observation, source-record identity,
canonical URL, or a traceable dependency path fails closed. Missing nodes,
cycles, traversal depth/node limits, and stale graph versions return unknown or
deny positive review. Structural graph disjointness alone never grants
independence: an owner step-up must attest to distinct original-reporting
bases for the named claim. Even this assertion is only a synthetic fixture
assumption, not verified journalism or proof of real editorial independence.

Adding lineage after a positive review appends a synthetic invalidation entry,
supersedes the previous assessment, removes it from the active view, and moves
a previously valid claim to `review_required`. The prior positive/valid audit
entries remain readable. Reclassifying a non-valid incident hypothesis as a
publication or unknown excludes its old lineage from the active incident view
without deleting that history. A valid incident cannot be silently
reclassified.

Revoking the final active positive review also moves a valid claim to
`review_required` atomically with the revocation audit. New positive reviews
for the current graph can then appear in the active evidence view, but do not
change that status; a fresh, explicit owner validation is required to return it
to `valid`.

An identical idempotent independence retry returns its original audit decision
even if the graph has since changed. That is only the historical result of the
request: current corroboration is evaluated separately against the latest
lineage review and graph version, so the replay cannot restore stale evidence.

`legacy_v2_status` is an immutable fixture-only value. Effective synthetic v3
status always starts `pending` and changes only through an explicit audited
decision. This does not modify the production v2 schema or implement a
migration.

## Focused tests and design limits

Run the focused tests with:

```sh
python3 -m unittest tests.test_operator_lineage_contract -v
```

The tests map to AUTH-N01/N02/N07/N09/N11/N12/N13/N14/N17, LIN-N01–N18,
LIN-P01/P02, and X-N01/N02/X-P02 as applicable. Real browser CSRF, session
cookies, WebAuthn, OIDC, TLS, MFA/recovery, provider outages, copyrighted
content rights, true newsroom ownership, and actual journalistic
independence are **NOT TESTABLE** in this in-process synthetic contract and
remain Stage C or owner-review decisions. A passing suite is not evidence of
production security, legal permission, or incident truth.
