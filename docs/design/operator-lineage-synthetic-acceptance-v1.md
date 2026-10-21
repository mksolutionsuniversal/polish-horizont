# Operator and lineage synthetic acceptance matrix v1

**Status: PROPOSED / NOT ACCEPTED / NOT DEPLOYABLE**  
**Purpose:** future acceptance contract for separately approved implementation work. This file defines cases only; it adds no test code, runtime behavior, identity configuration, database objects, or real source fixtures.

## Test-fixture rules

- Use generated identities, fake provider records, `example.test` URLs, and
  disposable in-memory or temporary databases only. No real article text,
  credential, secret, account, provider request, or host configuration.
- Each case must assert both the response and the resulting audit/projection
  state. A denied write must leave the hypothesis and current accepted review
  unchanged; an accepted action must produce one auditable append-only entry.
- Test roles at both the service boundary and database/file boundary. Unit
  tests cannot prove protection against a process that has unrestricted access
  to the SQLite file or operator backend.
- Record test ID, expected decision, observed status, audit link, and policy
  version. Never log tokens, cookies, OTPs, recovery codes, raw ID tokens, or
  full identity assertions.

## A. Authentication and authorization cases

| ID | Scenario | Expected result |
| --- | --- | --- |
| AUTH-P01 | Registered operator completes the selected supported ceremony with correct issuer/origin, challenge, subject, and required assurance. | AuthN succeeds; app still evaluates independent AuthZ policy before any action. |
| AUTH-P02 | Operator requests read-only event/lineage data with an active read scope. | Bounded read succeeds; no write capability is added to session. |
| AUTH-P03 | Owner performs step-up bound to a displayed claim, graph version, outcome, and expected prior version; all remain unchanged through submit. | One accepted/rejected decision is appended with matching claim, actor, policy, and step-up references. |
| AUTH-N01 | Anonymous browser, expired session, invalid signature, untrusted issuer, wrong audience/subject, bad WebAuthn origin/RP ID, bad challenge, or replayed challenge requests a write. | Deny; no decision row, no status change; generic response and bounded security audit. |
| AUTH-N02 | Caller submits `actor_id`, `role=owner`, `is_owner=true`, an AI model name, GitHub username, plugin metadata, or an untrusted forwarded identity header. | Ignore/reject caller assertions; no owner capability. |
| AUTH-N03 | Browser session is valid but CSRF token is absent/invalid, Origin is missing/null/unapproved, or a state-changing GET is attempted. | Deny before writer; no audit decision (security-denial event may be separately logged). |
| AUTH-N04 | OIDC callback has open redirect, mismatched exact redirect URI, missing/invalid transaction state or nonce, wrong issuer, PKCE verifier mismatch, stale code, or reused response. | Fail login; do not create or upgrade a session. |
| AUTH-N05 | Client asks for or stores a bearer/access token in browser JavaScript, URL, local storage, MCP prompt, or test output. | Architecture/test lint rejects the flow; no token is emitted or persisted there. |
| AUTH-N06 | Session fixation attempt reuses a pre-login session ID after successful login/step-up. | Server rotates identifier; pre-auth ID cannot authorize any action. |
| AUTH-N07 | AI or unauthenticated/read-only MCP caller invokes accept-independence, classify, validate, revoke, or owner recovery operation. | Deny at tool and backend authorization boundaries; no write credentials available to AI/MCP. |
| AUTH-N08 | Collector attempts to set `valid`/`invalid`, accept independence, classify legacy events, or submit a non-`unverified` accepted relation. | Deny; collector may append only its configured source observations/proposals. |
| AUTH-N09 | Session expires, is revoked, or is invalidated by credential recovery between page render and submit. | Deny; require new authentication and fresh step-up; no write. |
| AUTH-N10 | Human is authenticated but lacks permission for a different source, claim, or action scope. | Deny by default; no role inferred from authentication alone. |
| AUTH-N11 | Step-up is absent, stale, bound to a different claim/outcome, used twice, or claim/evidence graph changed after review. | Deny or return conflict; require fresh review and single-use step-up. |
| AUTH-N12 | Two owner sessions submit different decisions against the same expected prior version concurrently. | At most one compare-and-append succeeds; the other receives conflict and cannot overwrite history. |
| AUTH-N13 | Idempotency key is replayed with identical content, then replayed with different content. | Identical retry returns the prior result without duplicate action; changed content is rejected and audited. |
| AUTH-N14 | Identity provider/gateway is unavailable, assertion claims are missing, policy version cannot be read, or audit writer is unavailable. | Fail closed for decisions. A separately safe read-only surface may remain available; never silently downgrade to a weaker login. |
| AUTH-N15 | XSS-like script attempts to read session cookie or trigger a hidden decision. | Cookie is not script-readable; CSP/output controls apply; CSRF and step-up prevent silent state change. Manual verification UI remains a separate risk. |
| AUTH-N16 | Login/recovery brute force or account enumeration is attempted. | Rate limit, generic response, bounded audit, no existence-sensitive detail. |
| AUTH-N17 | Owner loses all authenticators and requests account reset through email-only, AI, support header, or an unapproved bypass. | Deny; remain locked pending previously approved independent recovery. No hidden admin takeover. |
| AUTH-P04 | Approved recovery procedure is followed with required identity proof, recovery controls, notification, and cooldown. | Revoke old factors/sessions, append recovery audit, create new session only after approval, and require step-up before any decision. |
| AUTH-P05 | Owner revokes a credential/session. | Session/challenge invalid immediately; revocation entry links target and actor; later use is denied. |
| AUTH-N18 | Direct unauthenticated HTTP/MCP access attempts to reach the writer or SQLite file. | Network/process/file controls deny access; API authentication alone is not treated as the database boundary. |

## B. Claim lineage and independent-information cases

| ID | Fixture | Expected result |
| --- | --- | --- |
| LIN-N01 | Same `observation_id` appears twice under different evidence rows or aliases. | Hard negative for independence; duplicate observation contributes once or is excluded. |
| LIN-N02 | Different observations/revisions share one `source_record_id`, even with different URLs and provider revisions. | Hard negative; they are one provider-scoped source identity, not independent reporting. |
| LIN-N03 | Different IDs or publisher domains resolve to the same canonical URL, mirror, or redirect alias. | Hard negative or `unknown` until reviewed URL normalization; never automatic positive. |
| LIN-N04 | GDELT observation and the publisher article cited by its URL support the same claim. | One dependency lineage; GDELT plus article never count as two independent confirmations. |
| LIN-N05 | Several publisher URLs reproduce one syndication-agency copy. | `dependent` for copied claims; preserve all URLs/attribution but count one origin. |
| LIN-N06 | Translation, translated republication, paraphrase, or summary derives from another article. | `dependent` for carried claims; new claims outside that scope remain `unknown` pending their own review. |
| LIN-N07 | Official release copied across departments or hosted at multiple government URLs. | `dependent` for claims from that release; GOV.UK confirms the announcement, not automatically the physical event. |
| LIN-N08 | Different publisher IDs/URLs but ownership/control or editorial lineage is unknown. | `unknown`; no independent count. Different identifiers are not proof. |
| LIN-N09 | Two distinct records and URLs are asserted independent solely because there is no detected citation or a similarity score is low. | Reject positive decision; absence of a known edge or score is insufficient. |
| LIN-N10 | AI generates `independent`, invents a lineage group ID, or submits an attestation as an owner. | Keep as proposal only; reject acceptance and retain model output as untrusted. |
| LIN-P01 | Two synthetic records have separately described, traceable original-reporting bases for the same named claim; a currently authenticated owner reviews the exact graph version and records rationale. | Positive decision may be accepted for that claim/version only. Fixture explicitly assumes the reporting bases are genuinely distinct; this is not established by ID/URL differences. |
| LIN-N11 | Positive review names a different claim, stale graph version, missing evidence reference, expired step-up, or no primary-reporting basis. | Reject; current result remains `unknown`/`pending`. |
| LIN-N12 | Ownership/control group is discovered after two sources were accepted independent. | Append new relationship and superseding review; exclude old positive from active corroboration; mark affected valid incident `review_required` pending human re-review. |
| LIN-N13 | New evidence shows citation, syndicated copy, common release, translation, or common source; earlier review is no longer supported. | Retain old decision, append dependent/unknown/disputed correction with `supersedes_id`; active view excludes stale positive. |
| LIN-N14 | A source retracts an article or issues a correction affecting only one claim. | Append source revision and claim-scoped correction; affected claim is reviewed, unrelated claims are not automatically erased. |
| LIN-N15 | Lineage graph is cyclic, corrupt, exceeds traversal bounds, or has missing edges. | Return `unknown`/`review_required`; do not count independent; bounded response and diagnostic without private source content. |
| LIN-N16 | Reviewer changes an accepted independence outcome. | Append new claim-specific decision and link prior decision; no UPDATE/DELETE or silent projection rewrite. |
| LIN-P02 | Independent owners separately report the same claim, with documented distinct first-hand/primary reporting and no copied/syndicated chain; owner reviews traceable records. | May be accepted for that exact claim after audit; re-evaluate if later source relations change. |
| LIN-N17 | A valid incident loses material active support because its lineage becomes dependent, withdrawn, or unknown. | Do not leave it silently valid; projection becomes `review_required`/`pending` until explicit owner review, while the original valid decision stays in history. |
| LIN-N18 | Historical SQLite v2 row has `validation_status=valid` or `invalid`, then is classified as a v3 incident hypothesis without a v3 audit decision. | Preserve v2 value unchanged; effective v3 hypothesis status is `pending` and not a confirmation. |
| LIN-N19 | Retraction, correction, relation, or ownership fact cannot be retained under the applicable source license/privacy rule. | Do not copy restricted content; store only approved minimal metadata/reference or leave status unknown. No license bypass. |

## C. Cross-boundary and audit cases

| ID | Scenario | Expected result |
| --- | --- | --- |
| X-P01 | Authenticated owner reviews a claim whose evidence has current, complete lineage and a current graph version. | Decision record ties authenticated subject, step-up, policy, rationale, claim, evidence revision set, graph version, and prior version together. |
| X-N01 | A well-formed independence attestation arrives without authenticated reviewer proof or append-only audit availability. | No acceptance; no “best effort” unlogged write. |
| X-N02 | A dataset reader, report, or future MCP query endpoint asks for stale or unbounded lineage graph output. | Bounded, read-only response; stale graph marked unknown/review-required; no raw private article body, secret, or identity detail. |
| X-N03 | Audit history is restored from an unverified/incomplete snapshot. | Disable accepted-decision projection until integrity and sequence are verified; re-review after recovery. |
| X-P02 | Synthetic run tests rollback after failure during audit append or status projection. | No partial decision or changed active projection; prior state/history remains intact. |

## Future gates; not authorized by this design PR

| Stage | Work | Required approval before entry |
| --- | --- | --- |
| **A — Design** | Review this ADR, choose auth/recovery alternative, agree lineage vocabulary, claimant rights, and test policy. | Owner decision after independent Vexi review. |
| **B — Isolated synthetic contract spike** | Separate issue/PR; no auth provider, real data, or production schema; implement only in-memory test seams. | Explicit owner approval of contract and allowed prototype scope. |
| **C — Isolated auth/read-model environment** | Separate issue, identity-provider selection, threat review, audit-store backup/recovery, deployment and security tests. | Independent security review and explicit owner approval. |
| **D — Real records or schema v3** | Source collection, migration, lineage evidence, public report, or production MCP changes. | Separate data/license review, verified backup/rollback, migration plan, and explicit owner sign-off for each scope. |

Passing future synthetic tests would establish only conformance to these test
contracts. It would not prove secure production authentication, real editorial
independence, legal permission, migration safety, or incident truth.
