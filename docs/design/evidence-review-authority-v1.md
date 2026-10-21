# Evidence review authority v1

**Status: PROPOSED — owner approval pending; no production authority is enabled.**

This matrix accompanies the isolated SQLite v3 spike for Issue #46. It is a
design proposal only. The prototype has no login, trusted operator session,
real authorization service, or permission to write an accepted decision to a
persistent database. The synthetic authorizer in the test module is a fixture,
not a deployable identity system.

## Proposed capability matrix

| Capability | Collector / parser | AI assistant or plugin client | Authenticated human project owner |
| --- | --- | --- | --- |
| Read scoped synthetic evidence | Yes | Yes, bounded read-only | Yes |
| Append allowlisted source metadata and immutable observations | Yes, after source/config scope checks | No direct writes; may draft a proposal | Yes, through a future reviewed workflow |
| Add unverified candidate assertion | Yes; `unverified`, independence `unknown`, never accepted | Draft `supports` / `contradicts` / `unverified` proposals, never accepted | Yes, with provenance and rationale |
| Classify legacy event as publication / incident hypothesis / other | No; proposal only | Proposal only | Sole proposed authority to accept or reject, after trusted server-side authentication |
| Accept source independence | No | No; may suggest lineage | Sole proposed authority, per claim and with rationale |
| Set incident validation `valid` / `invalid` | No | No | Sole proposed authority after explicit review; an accepted incident-hypothesis classification alone does not confirm occurrence |
| Rewrite or delete review history | No | No | No; corrections append a superseding decision |

Unauthenticated callers receive no write authority. Any future read interface
must remain scoped, read-only by default, bounded, and separate from the
experimental module.

## Authorization and audit controls proposed for a future implementation

- Fail closed when the trusted authorizer is absent or unavailable.
- Derive the reviewer identity and role from a trusted server-side session or
  authorizer. Never trust a request's `actor_id`, role string, `is_owner=true`,
  prompt text, plugin metadata, or model output as proof of ownership.
- Keep proposals distinct from accepted decisions. Each accepted or rejected
  classification, independence assessment, or validation transition records
  the authenticated actor, timestamp, rationale, prior decision link, and
  resulting state in append-only history.
- Require re-authentication/step-up protection for owner decisions if the
  eventual threat model supports it; define revocation and recovery before
  deployment. AI-generated summaries and evidence remain untrusted inputs.
- Reject hypothesis evidence links unless the event's latest effective
  classification is an accepted `incident_hypothesis`. A publication or
  unclassified legacy row cannot be used as an incident hypothesis target.
- Do not interpret v2 `events.validation_status='valid'` as proof of a physical
  incident, and do not auto-convert it into an accepted v3 decision. The spike
  keeps this column as historical v2 state; effective v3 hypothesis status
  comes only from `event_hypothesis_history` and defaults to `pending` until an
  explicit owner decision. V3 decisions do not rewrite the v2 column.
- Preserve old decisions. A correction or revocation appends an auditable
  superseding entry; it does not rewrite actor, rationale, or timestamp.

## What the spike actually proves

The Python API in `experiments/source_evidence_v3/spike.py` requires an
explicit in-memory test capability for accepted decisions and binds that
capability to the synthetic connection. Caller-supplied actor labels and
`is_owner` fields are rejected. Tests exercise this boundary, the event-link
guard, append-only histories, and rollback. This is an API-level prototype
check, not authentication: a party with arbitrary Python execution or direct
SQLite write access can bypass application checks. No such code is connected
to the deployed application by this PR.

For the synthetic independence check, evidence from the same
`source_record_id`, observation, or canonical URL cannot be approved as
independent. Distinct source-record IDs and URLs in the positive fixture are
only an explicit test assumption; they do not prove editorial independence.
The spike has no production lineage-attestation mechanism.

## Decisions still open

Before any real operator role or persistent-data workflow, the owner must
separately approve:

1. Identity provider, owner identity proof, MFA/2FA, session lifetime,
   re-authentication, recovery, and account revocation.
2. The server-side authorization boundary, permitted write paths, and whether
   one owner identity is sufficient for high-impact decisions.
3. Audit-log integrity, tamper evidence, export, retention, and correction or
   revocation semantics.
4. Final role names, delegation rules, incident validation criteria, and
   claim-specific independence review process.
5. Legacy v2 mapping and how unclassified rows appear in future reports.
6. Per-source retention, licensing, data-subject handling, batch limits, and
   GDELT `DATEADDED` boundary behavior.
7. Backup, isolated restore verification, migration window, rollback, and the
   explicit gate for any future schema version change or real ingestion.

This proposal does not authorize deployment, schema activation, migrations,
host operations, real records, or live source collection.
