# Stage C AuthN / HTTP lab

This is a dormant, standard-library-only experiment. Nothing imports it from
the production application. The server factory binds only to `127.0.0.1` on an
ephemeral port and is started only by the synthetic tests. No identity service,
database migration, persistent session store, browser UI, or outbound client is
provided. Test fixtures use fictional identities and `example.test` records.

The test server speaks plain HTTP on loopback. `https://operator.example.test`
is only the configured expected `Origin`; the `Secure` cookie attribute is
asserted and the test client manually carries the cookie. This does not test or
claim TLS, browser enforcement, WebAuthn, OIDC, or production session security.

## Boundary and authority

Authentication is an injected `AuthNAdapter`; its default implementation
denies every assertion. The server accepts a verified identity only when its
issuer and audience match the fictional configuration. Authorization is a
separate exact-subject check for the one synthetic owner. Request fields,
cookies, and reverse-proxy headers never supply roles or owner status. The
decision port is injected and the test adapter delegates to the isolated
in-memory Stage B contract. Missing or unavailable authority fails closed.

Sessions are opaque, short-lived, server-side entries; only a SHA-256 digest of
the session token is indexed. Login rotates any presented session, the cookie
is `Secure`, `HttpOnly`, `SameSite=Strict`, and `__Host-` scoped, and state
changes require the exact configured Origin plus a synchronizer CSRF token.
Step-up reauthentication is recent, bound to the same subject, session, claim,
outcome, evidence digest, graph/version, rationale, and idempotency key. The
underlying contract is responsible for atomic decision/audit persistence and
the HTTP layer returns the historical decision identifier separately from the
current claim projection.

The tests do not represent a complete browser threat model. In particular,
there is no TLS termination, proxy deployment, browser automation, CSP report
collector, persistent audit backend, distributed race test, or production
recovery mechanism. The loopback server is not a deployable service.

## Identity option decision memo

| Option | Strengths | Costs and failure modes |
|---|---|---|
| Owner-held local passkeys/WebAuthn | Phishing-resistant origin-bound authentication; no external IdP availability dependency; small single-owner trust boundary | Requires a stable approved origin/RP ID, compatible authenticators, secure enrollment and revocation, and a tested recovery path; losing all authenticators can lock out the owner |
| Separate OIDC with MFA | Mature account lifecycle and revocation; can support delegated operators; phishing-resistant MFA can be required by policy | Adds IdP, issuer, redirect, availability, account-recovery, and configuration dependencies; MFA method and assurance mapping need explicit review |

**Provisional recommendation:** prefer owner-held passkeys for a single-owner
deployment if the owner approves a stable origin and recovery can be tested
without a bypass. Keep OIDC plus phishing-resistant MFA as the alternative if
delegated administration or centralized account lifecycle becomes necessary.
This is a design recommendation only; no provider, RP ID, origin, enrollment,
or production policy is selected here.

### Proposed recovery procedure (not implemented)

1. Freeze all approval writes when no enrolled authenticator remains; do not
   fall back to email, an AI agent, a collector, proxy headers, or a support
   override.
2. Start a separately approved recovery ceremony using owner-controlled
   offline recovery material and an independently verified owner identity.
   Require a second human reviewer for recovery and record the reason.
3. Revoke every prior authenticator, session, and outstanding step-up; enroll
   replacement authenticators and notify the owner through a pre-registered
   channel.
4. Apply a review/cooldown window before enabling incident approval again and
   append the recovery and re-enablement decisions to the audit history.
5. If recovery evidence or reviewer availability is insufficient, remain
   locked out and use the separately approved organizational recovery route.

The owner must decide what offline recovery material, second reviewer,
notification channel, and cooldown are acceptable before a real identity
system is considered. Recovery must not create an alternate unaudited owner
credential.

## Acceptance evidence

`tests/test_operator_http_lab.py` exercises fictional HTTP traffic on loopback:
authentication denial/outage, Host and Origin checks, CSRF, cookie flags and
duplication, rotation/fixation, expiry/revocation, forged roles and proxy
headers, same-user step-up, replay/mutation, stale graph, idempotent retries,
concurrent decisions, bounded JSON, sanitized reads, and authority/audit
failure. `tests/test_operator_lineage_contract.py` retains the PR #51 P3
regression: direct `_decide_claim` denial is supplied a valid unused `step_up_id`
so the test reaches the classification guard, then verifies there was no
positive audit, version increment, or step-up consumption.

These checks validate only the local contract fixture. They do not establish
real identity assurance, cryptographic assertion verification, browser CSRF
behavior, or production audit durability. Rollback is limited to deleting this
experiment, its tests, and this decision memo; no production data or schema is
modified.

### Review hardening

The injected backend must enter the supplied `commit_guard` immediately around
its local atomic write, after any potentially blocking preparation. This guard
serializes commit with revocation per HTTP session and rechecks expiry. The
synthetic backend follows that contract; adapting an external backend requires
its own transaction-bound capability design. A commit already inside the guard
linearizes before a concurrent revocation; revocation returns only after that
write completes. No global lock surrounds backend preparation.

Known LAB limitation (P3): this dormant loopback server still uses unbounded
thread-per-connection handling and has no incomplete-request read timeout.
It must not be exposed or reused as a deployment server. Bounded connections
and slow-client/timeouts remain required before any broader HTTP experiment;
this change makes no claim about the earlier host stalled-socket issue.
