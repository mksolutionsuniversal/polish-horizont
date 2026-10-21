# Operator authentication and review gate v1

**Status: PROPOSED / NOT ACCEPTED / NOT DEPLOYABLE**  
**Scope:** design only for a future human decision boundary; no login, identity provider, account, secret, SQLite change, MCP change, or runtime code is introduced.  
**Decision owner:** project owner after independent Vexi review.

## Context and current boundary

The repository is code-only. Production SQLite remains schema v2. The read-only
SQLite API is a loopback-oriented bearer-token interface; its bearer credential
does not identify a human reviewer and must not be forwarded to browser code or
promoted into write authority. The query module is not an MCP server. README
describes a future read-only MCP interface, but no production MCP identity or
authorization boundary exists here.

PR #45 is a proposed source/evidence model, not an approved migration. PR #47
adds a synthetic, in-memory v3 spike and test-only owner capability. That
capability is not authentication: direct process or SQLite access can bypass
it. Its classification, validation, independence, and revision semantics are
design evidence only. Do not infer that GitHub identity, a model identity, a
plugin, an MCP client, request fields, `actor_id`, `is_owner`, or forwarded
headers prove an operator identity.

## Terms and authority

- **Authentication (AuthN):** establish which human-controlled identity
  completed a protocol ceremony, with its issuer, subject, assurance context,
  and time. Authentication does not grant project permissions.
- **Authorization (AuthZ):** server-side decision that the authenticated
  principal may perform one named action on one claim/hypothesis under the
  current policy and evidence state.
- **Decision record:** immutable audit fact that the authorized action was
  accepted, rejected, superseded, or revoked. It is not itself proof that the
  underlying event occurred.
- **Collector:** narrowly scoped machine principal for append-only source
  observations and unverified proposals. It cannot accept a classification,
  independent lineage, or incident status.
- **AI/MCP/read client:** read-only and proposal-only. Model output, tool
  routing, prompt text, or a caller-provided role never grants decision power.
- **Human owner/operator:** the only proposed principal allowed to accept a
  claim-specific lineage review or incident validation after explicit
  authentication, authorization, and step-up checks.

Default is deny. Missing, stale, ambiguous, or unverifiable identity or policy
means no write. The application server is the only future decision writer;
browser, AI, MCP, collector, and read-only API processes do not receive direct
write access to the database. This separation must be enforced by process and
file permissions as well as application code.

## Identity alternatives

| Option | Benefits | Costs, failure modes, and open questions |
| --- | --- | --- |
| **A. Local single-operator passkeys / WebAuthn** | No general-purpose password; relying-party and origin scoping; phishing-resistant public-key ceremony; can operate without an external identity provider after enrollment. | HORIZONT must own credential enrollment, revocation, secure session handling, recovery, device loss, origin/RP ID stability, and availability. Synced passkeys alter recovery and device trust assumptions. Offline operation helps only if the application host and registered authenticators remain reachable. A recovery bypass can defeat the primary factor. |
| **B. Separate OIDC provider with MFA** | Delegates identity lifecycle and authentication ceremony; OIDC separates authentication claims from app authorization; provider may support phishing-resistant MFA and account recovery operations. | Provider choice, assurance/`acr`/`amr` claim contract, enrollment, outage, tenant lockout, claim changes, redirect configuration, and account recovery remain owner decisions. OIDC login does not decide HORIZONT roles. Client must validate issuer, audience, signature, nonce, state, timestamps, exact registered redirect, and authorization-code binding. Use Authorization Code + PKCE S256; do not use implicit flow or browser-held access tokens. |
| **C. Separate access gateway** | Can reduce network exposure and add a perimeter identity check in front of a private app. | It is not application AuthZ and does not make a forwarded username header intrinsically trustworthy. Requires a separately approved gateway/IdP, TLS, network isolation that prevents bypass, and a proxy that strips caller-supplied identity headers and authenticates the upstream assertion. It creates coupling, outage/lockout and local/offline constraints. Do not reuse or modify any existing Cloudflare Access/OAuth or MCP integration under this ADR. |

### Conditional recommendation; owner decision required

For a single human operator, evaluate **A (local passkeys)** first if secure
enrollment and an independent, auditable recovery route are acceptable. Evaluate
**B (OIDC + phishing-resistant MFA)** if the owner prefers delegated account
lifecycle and accepts provider availability and lockout dependencies. Consider
**C only as a network exposure layer in addition to application AuthN/AuthZ**,
not as a replacement for either. This is not a selected design or provider.
Owner must choose a deployment and recovery model before any implementation.

Protocol references checked for this proposal: [W3C Web Authentication Level
3](https://www.w3.org/TR/webauthn-3/), [OpenID Connect Core 1.0](https://openid.net/specs/openid-connect-core-1_0-18.html),
[IETF OAuth 2.0 Security BCP, RFC 9700](https://www.rfc-editor.org/rfc/rfc9700.html),
[NIST SP 800-63B-4](https://pages.nist.gov/800-63-4/sp800-63b.html),
[OWASP Session Management](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html),
and [OWASP CSRF Prevention](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html).
These references inform protocol and control requirements; they do not choose
an IdP or establish that this project conforms to a NIST assurance level.

## Proposed trust boundary and decision flow

```text
untrusted browser / AI / MCP / collector
       | read or proposal-only requests
       v
read-only API / query process --------------------> sealed SQLite read view

human browser -- TLS + AuthN ceremony --> private operator app backend
       |                    |                          |
       | opaque session     | AuthZ + step-up          | scoped decision command
       +------------------->| validate current policy  v
                                                     sole writer
                                                        |
                                      atomic compare-and-append audit journal
                                                        |
                                                versioned read projection
```

The operator backend must terminate TLS, validate the chosen authentication
protocol, bind a server-side session to an immutable issuer/subject pair, and
derive permissions from server-side policy. It must not accept roles or
principal IDs supplied by the caller. A reverse proxy or gateway may contribute
identity only when the application is unreachable except through that proxy,
untrusted copies of identity headers are removed, and the assertion has a
verifiable issuer, audience, integrity, freshness, and replay boundary.

For a high-impact decision, show the exact claim text, source/lineage graph
version, current status, evidence changes, and proposed outcome to the human.
Require a fresh step-up ceremony bound to that operation. On submit, atomically
check expected claim/evidence version (compare-and-swap), policy version,
session validity, permission, step-up age, CSRF token, and idempotency key. If
any value changed since review, return conflict and require a fresh review.
Never accept a decision from a background job or AI suggestion.

## Session and request protections (proposed)

- Issue a high-entropy opaque session identifier after authentication, rotate
  it at login and after privilege/step-up changes, and keep session state
  server-side. No bearer/access token in a URL, browser JavaScript, local
  storage, logs, or MCP context.
- Cookie proposal: `Secure`, `HttpOnly`, `SameSite=Strict` where compatible
  (otherwise `Lax` with explicit CSRF protection), `Path=/`, no `Domain`, and
  `__Host-` prefix for HTTPS origins. All write endpoints require a
  synchronizer CSRF token plus exact `Origin` validation; reject absent,
  null, or unapproved origins. SameSite is defense in depth, not the only CSRF
  control. State-changing GET/HEAD requests are forbidden.
- Enforce inactivity and absolute session lifetimes, bounded concurrent
  sessions, server-side revocation, and reauthentication after recovery or
  account/credential changes. Candidate idle/absolute/step-up age values must
  be chosen and reviewed by the owner before implementation; this ADR does not
  silently set production values.
- Apply rate limits and generic failure responses to login, recovery, and
  step-up attempts. Record security-relevant failures with bounded retention;
  never log credentials, session IDs, recovery codes, tokens, or raw assertion
  payloads.
- Use a one-time, operation-bound nonce/challenge, CSRF protection, strict
  redirect allowlists, and replay/idempotency controls. For OIDC, use the
  Authorization Code flow with PKCE S256, transaction-bound state/nonce,
  exact redirect matching, issuer/audience/signature/time validation, and
  reject token/authorization response reuse.
- Apply CSP and output encoding to reduce XSS risk. HttpOnly cookies reduce
  cookie theft but do not make an XSS-compromised browser safe to approve a
  decision.

## Reauthentication, revocation, and account recovery

Step-up is required for accepting/rejecting a claim-specific independence
assessment, classifying an event, setting incident `valid`/`invalid`, changing
an earlier decision, or revoking an authenticator. Step-up must be recent,
single-use, bound to the decision digest and expected prior version, and must
expire on any change to the reviewed claim/evidence set.

Revocation must invalidate all server-side sessions and outstanding
step-up/challenge state immediately, remove the credential from the allowed
operator set, and append an audit event with actor, reason category, time, and
revocation scope. Existing read-only access does not implicitly gain writes.

Recovery is a first-class authentication path, not a support shortcut. Proposed
guardrails: enroll at least two independent authenticators where the selected
method allows it; issue one-time recovery material only through a separately
reviewed ceremony; store only protected verifier material; require strong
identity re-establishment and an out-of-band notice; revoke old sessions and
authenticators; impose a review/cooldown period before high-impact decisions.
If the owner cannot be independently verified, stay locked out and use a
pre-agreed offline recovery procedure. No email-only reset, shared secret in
repository, AI-assisted account recovery, or hidden administrator bypass.
Exact identity proofing, custody, and recovery time are unresolved owner
decisions.

## Decision audit record and invariants

Future accepted/rejected/revoked decisions should append a record containing at
least: immutable actor issuer+subject; authentication method and assurance
context; step-up time/result; action and outcome; hypothesis ID and claim ID;
prior decision ID and expected version; evidence IDs and lineage graph version;
policy version; rationale; UTC decision time; request/idempotency ID; and
`supersedes_id` or revocation target. Store no raw tokens, secrets, unnecessary
identity attributes, or full third-party article content. Correct by a new
entry, never by editing the previous actor, rationale, timestamp, or outcome.

The audit append and status projection update must be atomic. Deny duplicate
idempotency keys with conflicting content, stale expected versions, missing
lineage review, unavailable audit storage, or policy-version mismatch. Use
restricted writer access and independently tested export/backup/restore; a
database trigger alone is not an authorization boundary against a process that
can write the file.

## Threats and proposed response

| Threat | Proposed fail-closed response |
| --- | --- |
| Forged actor/role/header or plugin claims owner | Ignore request-supplied identity; verify an issuer-bound session at the backend. |
| Session fixation, theft, replay, or post-revocation use | Rotate opaque session IDs; short lifetimes; server-side revocation; operation-bound one-time step-up. |
| CSRF or OAuth callback injection | Synchronizer token + exact Origin for app writes; exact OIDC redirect, transaction-bound state/nonce and PKCE. |
| XSS or browser token exposure | No browser bearer tokens; CSP/output encoding; HttpOnly cookie; still require human review display and step-up. |
| Stale/concurrent review | Compare expected claim and evidence version atomically; return conflict and require re-review. |
| IdP/gateway outage, bad claims, or redirect change | No accepted decisions; preserve read-only mode only if separately safe; alert without leaking secrets. |
| Lost authenticator / recovery abuse | No bypass; controlled independent recovery, revoke sessions/old keys, audit and delay high-impact actions. |
| Direct SQLite writer or compromised backend | Restrict OS/process access; isolate writer; append-only external audit export and restore review; treat as residual privileged threat. |
| AI prompt injection or tool impersonation | AI remains proposal/read-only; no model-derived owner assertion, session, or signing authority. |
| Public exposure of existing loopback bearer API | Keep loopback-only; no proxy/network exposure until a separate authenticated transport design is approved. |

## Rollback and owner decisions

This PR adds no runtime behavior, account, secret, session, schema, or
infrastructure. Reverting the design document is the complete rollback. Before
Stage B, the owner must decide:

1. Which alternative (A, B, or C+application AuthN/AuthZ) and whether the
   service needs to work offline.
2. Identity provider/issuer, MFA assurance, enrollment, trusted origin/RP ID,
   and authoritative operator subject.
3. Session idle/absolute TTL, step-up recency, recovery proof, cooldown, and
   authenticator revocation policy.
4. Whether one owner may approve high-impact status alone or whether a second
   human is required.
5. Audit journal storage, tamper evidence, retention, privacy minimization,
   export, backup, and recovery ownership.
6. Whether any future MCP write surface is forbidden permanently (recommended
   for this project) or could only submit a human-confirmed proposal.

Until approved, no identity mechanism, login, account, secret, or write path is
authorized. No compliance or production-security claim is made.
