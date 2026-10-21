"""Synthetic Stage B operator and lineage contract tests; no real auth/data."""

from __future__ import annotations

from queue import Queue
import threading
import unittest
from unittest.mock import patch

from experiments.operator_lineage_contract.contract import (
    ContractConflict,
    ContractDenied,
    ContractError,
    FakeClock,
    OperatorLineageContract,
)


class OperatorLineageContractTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.contract = OperatorLineageContract(clock=self.clock)
        self.addCleanup(self.contract.close)
        self.owner = self.contract.issue_test_session(actor_ref="synthetic-owner-A", role="owner")
        self.ai = self.contract.issue_test_session(actor_ref="synthetic-ai", role="ai_readonly")
        self.collector = self.contract.issue_test_session(actor_ref="synthetic-collector", role="collector", claim_scope=("claim-1",))
        self.contract.create_claim("claim-1")

    def decision(self, *, context=None, action="accept_classification", outcome="other_or_unknown",
                 claim_id="claim-1", expected_version=0, details=None, key="decision-1", fail=False,
                 step_up_id=None):
        details = details or {}
        if step_up_id is None:
            step_up_id = self.contract.issue_step_up(
                context or self.owner, action=action, claim_id=claim_id, outcome=outcome,
                expected_version=expected_version, details=details,
            )
        return self.contract.decide(
            context or self.owner, action=action, claim_id=claim_id, outcome=outcome,
            expected_version=expected_version, step_up_id=step_up_id, idempotency_key=key,
            rationale="fictional owner-reviewed decision", details=details, fail_after_audit=fail,
        )

    def add_pair(self, *, claim_id="claim-1", prefix="p", same_observation=False,
                 same_source_record=False, same_url=False):
        record_a, record_b = f"{prefix}-a", f"{prefix}-b"
        self.contract.add_synthetic_record(
            claim_id=claim_id, record_id=record_a,
            source_record_id=f"src-{prefix}" if same_source_record else f"src-{prefix}-a",
            observation_id=f"obs-{prefix}-same" if same_observation else f"obs-{prefix}-a",
            canonical_url="https://example.test/shared" if same_url else f"https://example.test/{prefix}/a",
            record_kind="provider_observation" if prefix.startswith("gdelt") else "publication",
        )
        self.contract.add_synthetic_record(
            claim_id=claim_id, record_id=record_b,
            source_record_id=f"src-{prefix}" if same_source_record else f"src-{prefix}-b",
            observation_id=f"obs-{prefix}-same" if same_observation else f"obs-{prefix}-b",
            canonical_url="https://example.test/shared" if same_url else f"https://example.test/{prefix}/b",
        )
        return record_a, record_b

    def positive_review(self, record_a, record_b, *, claim_id="claim-1", key="lineage-1"):
        basis_a = "fictional first-hand interview fixture A"
        basis_b = "fictional independently obtained public record fixture B"
        details = self.contract.independence_details(
            claim_id, record_a, record_b, reporting_basis_a=basis_a,
            reporting_basis_b=basis_b, owner_attests_distinct_origins=True,
        )
        state = self.contract.claim_state(claim_id)
        challenge = self.contract.issue_step_up(
            self.owner, action="accept_independence", claim_id=claim_id,
            outcome="independent", expected_version=state["version"],
            graph_version=state["graph_version"], details=details,
        )
        decision = self.contract.review_independence(
            self.owner, claim_id=claim_id, record_a=record_a, record_b=record_b,
            outcome="independent", reporting_basis_a=basis_a, reporting_basis_b=basis_b,
            owner_attests_distinct_origins=True, expected_version=state["version"],
            step_up_id=challenge, idempotency_key=key,
            rationale="synthetic fixture assumes separately originated reporting",
        )
        return decision

    def test_anonymous_expired_and_forged_owner_contexts_are_denied(self):
        anonymous = self.contract.issue_test_session(actor_ref="anonymous-fixture", role="anonymous")
        with self.assertRaises(ContractDenied):
            self.contract.issue_step_up(anonymous, action="accept_classification", claim_id="claim-1", outcome="incident_hypothesis", expected_version=0)
        with self.assertRaises(ContractDenied):
            self.decision(context=type(self.owner)(self.owner.session_id, "forged-owner"))
        forged_role = type(self.ai)(self.ai.session_id, self.ai.actor_ref, claimed_role="owner")
        with self.assertRaises(ContractDenied):
            self.contract.issue_step_up(forged_role, action="accept_classification", claim_id="claim-1", outcome="incident_hypothesis", expected_version=0)
        expired = self.contract.issue_test_session(actor_ref="expired-owner", role="owner", ttl=2)
        self.clock.advance(2)
        with self.assertRaises(ContractDenied):
            self.contract.issue_step_up(expired, action="accept_classification", claim_id="claim-1", outcome="incident_hypothesis", expected_version=0)

    def test_ai_and_collector_can_propose_but_cannot_accept(self):
        self.contract.propose(self.ai, claim_id="claim-1", proposal_type="classification", body="fictional suggestion")
        self.contract.propose(self.collector, claim_id="claim-1", proposal_type="source-observation", body="fictional unverified item")
        with self.assertRaises(ContractDenied):
            self.contract.issue_step_up(self.ai, action="accept_independence", claim_id="claim-1", outcome="independent", expected_version=0)
        with self.assertRaises(ContractDenied):
            self.contract.issue_step_up(self.collector, action="validate_incident", claim_id="claim-1", outcome="valid", expected_version=0)
        self.assertEqual(self.contract.proposal_count(), 2)
        self.assertEqual(self.contract.decision_count(), 0)

    def test_one_time_stepup_replay_and_idempotency(self):
        challenge = self.contract.issue_step_up(self.owner, action="accept_classification", claim_id="claim-1", outcome="other_or_unknown", expected_version=0)
        first = self.decision(key="retry-stable", step_up_id=challenge)
        replay = self.decision(key="retry-stable", step_up_id=challenge)
        self.assertEqual(first, replay)
        self.assertEqual(self.contract.decision_count(), 1)
        with self.assertRaises((ContractDenied, ContractConflict)):
            self.decision(key="replay-as-new-action", step_up_id=challenge)
        self.assertEqual(self.contract.decision_count(), 1)

    def test_idempotency_key_cannot_be_reused_for_mutated_request(self):
        self.decision(key="same-key")
        with self.assertRaises((ContractDenied, ContractConflict)):
            self.decision(outcome="publication_record", key="same-key")
        self.assertEqual(self.contract.decision_count(), 1)

    def test_expired_or_wrong_operation_stepup_is_denied(self):
        challenge = self.contract.issue_step_up(self.owner, action="accept_classification", claim_id="claim-1", outcome="other_or_unknown", expected_version=0, ttl=1)
        self.clock.advance(1)
        with self.assertRaises(ContractDenied):
            self.decision(step_up_id=challenge)
        other = self.contract.issue_step_up(self.owner, action="accept_classification", claim_id="claim-1", outcome="other_or_unknown", expected_version=0)
        with self.assertRaises(ContractDenied):
            self.contract.decide(self.owner, action="accept_classification", claim_id="claim-1", outcome="publication_record", expected_version=0, step_up_id=other, idempotency_key="wrong-outcome", rationale="synthetic mismatch")

    def test_session_revocation_between_display_and_submit_denies_write(self):
        challenge = self.contract.issue_step_up(self.owner, action="accept_classification", claim_id="claim-1", outcome="other_or_unknown", expected_version=0)
        second_owner = self.contract.issue_test_session(actor_ref="synthetic-owner-B", role="owner")
        target_version = 0
        revoke_details = {"target_session_id": self.owner.session_id}
        revoke_challenge = self.contract.issue_step_up(
            second_owner, action="revoke", claim_id=f"session:{self.owner.session_id}",
            outcome="revoked", expected_version=target_version, details=revoke_details,
        )
        self.contract.revoke_session(second_owner, target_session_id=self.owner.session_id,
                                     step_up_id=revoke_challenge, expected_version=target_version,
                                     idempotency_key="revoke-owner-A", rationale="synthetic credential revocation")
        prior_count = self.contract.decision_count()
        repeated = self.contract.revoke_session(
            second_owner, target_session_id=self.owner.session_id,
            step_up_id=revoke_challenge, expected_version=target_version,
            idempotency_key="revoke-owner-A", rationale="synthetic credential revocation",
        )
        self.assertEqual(repeated, self.contract.audit_history()[-1]["id"])
        self.assertEqual(self.contract.decision_count(), prior_count)
        with self.assertRaises(ContractDenied):
            self.contract.revoke_session(
                second_owner, target_session_id=self.owner.session_id,
                step_up_id=revoke_challenge, expected_version=target_version,
                idempotency_key="revoke-owner-A", rationale="changed replay payload",
            )
        with self.assertRaises(ContractDenied):
            self.decision(step_up_id=challenge)
        self.assertEqual(self.contract.claim_state("claim-1")["version"], 0)

    def test_stale_decision_conflicts_and_only_one_racing_decision_appends(self):
        owner_b = self.contract.issue_test_session(actor_ref="synthetic-owner-B", role="owner")
        challenges = [
            self.contract.issue_step_up(self.owner, action="accept_classification", claim_id="claim-1", outcome="publication_record", expected_version=0),
            self.contract.issue_step_up(owner_b, action="accept_classification", claim_id="claim-1", outcome="other_or_unknown", expected_version=0),
        ]
        barrier = threading.Barrier(3)
        results: Queue = Queue()

        def run(context, outcome, key, challenge):
            barrier.wait()
            try:
                decision_id = self.decision(context=context, outcome=outcome, key=key, step_up_id=challenge)
                results.put(("ok", decision_id))
            except ContractConflict:
                results.put(("conflict", None))

        workers = [
            threading.Thread(target=run, args=(self.owner, "publication_record", "race-A", challenges[0])),
            threading.Thread(target=run, args=(owner_b, "other_or_unknown", "race-B", challenges[1])),
        ]
        for worker in workers:
            worker.start()
        barrier.wait()
        for worker in workers:
            worker.join(timeout=3)
        outcomes = sorted(results.get_nowait()[0] for _ in workers)
        self.assertEqual(outcomes, ["conflict", "ok"])
        self.assertEqual(self.contract.decision_count(), 1)
        self.assertEqual(self.contract.claim_state("claim-1")["version"], 1)

    def test_audit_and_projection_rollback_together_after_injected_failure(self):
        challenge = self.contract.issue_step_up(self.owner, action="accept_classification", claim_id="claim-1", outcome="other_or_unknown", expected_version=0)
        with self.assertRaises(RuntimeError):
            self.decision(step_up_id=challenge, fail=True)
        self.assertEqual(self.contract.decision_count(), 0)
        self.assertEqual(self.contract.claim_state("claim-1")["version"], 0)
        self.assertEqual(self.decision(step_up_id=challenge), 1)

    def test_policy_outage_or_stale_policy_fails_closed(self):
        stale = self.contract.issue_test_session(actor_ref="stale-policy-owner", role="owner", policy_version="old-policy")
        with self.assertRaises(ContractDenied):
            self.contract.issue_step_up(stale, action="accept_classification", claim_id="claim-1", outcome="other_or_unknown", expected_version=0)
        with self.assertRaises(ContractDenied):
            self.contract.decide(self.owner, action="validate_incident", claim_id="claim-1", outcome="valid", expected_version=0, step_up_id=None, idempotency_key="no-audit", rationale="no step-up")
        self.assertEqual(self.contract.decision_count(), 0)

    def test_claim_scope_is_enforced_server_side(self):
        self.contract.create_claim("claim-2")
        with self.assertRaises(ContractDenied):
            self.contract.issue_step_up(self.collector, action="validate_incident", claim_id="claim-2", outcome="valid", expected_version=0)
        with self.assertRaises(ContractDenied):
            self.contract.propose(self.collector, claim_id="claim-2", proposal_type="observation", body="outside scope")

    def test_legacy_v2_status_is_historical_until_explicit_v3_validation(self):
        self.contract.create_claim("legacy-valid", legacy_v2_status="valid")
        self.contract.create_claim("legacy-invalid", legacy_v2_status="invalid")
        self.assertEqual(self.contract.claim_state("legacy-valid")["effective_status"], "pending")
        self.assertEqual(self.contract.claim_state("legacy-invalid")["effective_status"], "pending")
        self.assertEqual(self.contract.claim_state("legacy-valid")["legacy_v2_status"], "valid")
        self.assertEqual(self.contract.claim_state("legacy-invalid")["legacy_v2_status"], "invalid")
        state = self.contract.claim_state("legacy-valid")
        challenge = self.contract.issue_step_up(self.owner, action="validate_incident", claim_id="legacy-valid", outcome="invalid", expected_version=state["version"])
        self.contract.decide(self.owner, action="validate_incident", claim_id="legacy-valid", outcome="invalid", expected_version=0, step_up_id=challenge, idempotency_key="legacy-valid-explicit", rationale="fictional owner decision")
        self.assertEqual(self.contract.claim_state("legacy-valid")["effective_status"], "invalid")
        self.assertEqual(self.contract.claim_state("legacy-valid")["legacy_v2_status"], "valid")

    def test_two_legacy_statuses_can_be_explicitly_overridden_without_mutating_fixture(self):
        self.contract.create_claim("old-valid", legacy_v2_status="valid")
        self.contract.create_claim("old-invalid", legacy_v2_status="invalid")
        a, b = self.add_pair(claim_id="old-invalid", prefix="legacy")
        basis_a, basis_b = "fixture report A", "fixture report B"
        details = self.contract.independence_details("old-invalid", a, b, reporting_basis_a=basis_a, reporting_basis_b=basis_b, owner_attests_distinct_origins=True)
        step = self.contract.issue_step_up(self.owner, action="accept_independence", claim_id="old-invalid", outcome="independent", expected_version=0, details=details)
        self.contract.review_independence(self.owner, claim_id="old-invalid", record_a=a, record_b=b, outcome="independent", reporting_basis_a=basis_a, reporting_basis_b=basis_b, owner_attests_distinct_origins=True, expected_version=0, step_up_id=step, idempotency_key="legacy-independent", rationale="fixture-only distinct origins")
        for claim_id, outcome in (("old-valid", "invalid"), ("old-invalid", "valid")):
            state = self.contract.claim_state(claim_id)
            challenge = self.contract.issue_step_up(self.owner, action="validate_incident", claim_id=claim_id, outcome=outcome, expected_version=state["version"])
            self.contract.decide(self.owner, action="validate_incident", claim_id=claim_id, outcome=outcome, expected_version=state["version"], step_up_id=challenge, idempotency_key=f"explicit-{claim_id}", rationale="explicit fictional owner review")
        self.assertEqual(self.contract.claim_state("old-valid")["effective_status"], "invalid")
        self.assertEqual(self.contract.claim_state("old-valid")["legacy_v2_status"], "valid")
        self.assertEqual(self.contract.claim_state("old-invalid")["effective_status"], "valid")
        self.assertEqual(self.contract.claim_state("old-invalid")["legacy_v2_status"], "invalid")

    def test_identical_observation_source_identity_and_url_are_hard_negative(self):
        for prefix, options in (
            ("same-observation", {"same_observation": True}),
            ("same-source", {"same_source_record": True}),
            ("same-url", {"same_url": True}),
        ):
            with self.subTest(prefix=prefix):
                a, b = self.add_pair(prefix=prefix, **options)
                self.assertEqual(self.contract.pair_assessment("claim-1", a, b), "dependent")
                with self.assertRaises(ContractDenied):
                    self.positive_review(a, b, key=f"deny-{prefix}")

    def test_gdelt_and_cited_publisher_and_transitive_syndication_are_dependent(self):
        self.contract.add_synthetic_record(claim_id="claim-1", record_id="publisher", source_record_id="pub-1", observation_id="obs-pub", canonical_url="https://example.test/news/original", record_kind="publication")
        self.contract.add_synthetic_record(claim_id="claim-1", record_id="gdelt-row", source_record_id="gdelt-1", observation_id="obs-gdelt", canonical_url="https://example.test/news/gdelt", record_kind="provider_observation")
        self.contract.add_synthetic_record(claim_id="claim-1", record_id="wire-copy", source_record_id="wire-1", observation_id="obs-wire", canonical_url="https://example.test/wire/copy", record_kind="agency_copy")
        self.contract.add_lineage_edge(claim_id="claim-1", parent_record_id="publisher", child_record_id="gdelt-row", relation="cites", basis="synthetic GDELT row cites publisher URL")
        self.contract.add_lineage_edge(claim_id="claim-1", parent_record_id="publisher", child_record_id="wire-copy", relation="syndicated_from", basis="synthetic wire distribution")
        self.assertEqual(self.contract.pair_assessment("claim-1", "gdelt-row", "publisher"), "dependent")
        self.assertEqual(self.contract.pair_assessment("claim-1", "gdelt-row", "wire-copy"), "dependent")
        with self.assertRaises(ContractDenied):
            self.positive_review("publisher", "gdelt-row", key="gdelt-not-independent")

    def test_translations_official_releases_and_editorial_control_are_not_independent(self):
        ids = ("origin", "translation", "mirror", "official-copy", "controlled-copy")
        for index, record_id in enumerate(ids):
            self.contract.add_synthetic_record(claim_id="claim-1", record_id=record_id, source_record_id=f"sr-{record_id}", observation_id=f"ob-{record_id}", canonical_url=f"https://example.test/{record_id}", record_kind="official_announcement" if record_id == "official-copy" else "publication")
        for parent, child, relation in (
            ("origin", "translation", "translated_from"),
            ("translation", "mirror", "copied_from"),
            ("origin", "official-copy", "same_official_release"),
            ("origin", "controlled-copy", "editorially_controlled_by"),
        ):
            self.contract.add_lineage_edge(claim_id="claim-1", parent_record_id=parent, child_record_id=child, relation=relation, basis=f"synthetic relation {relation}")
        for child in ids[1:]:
            self.assertEqual(self.contract.pair_assessment("claim-1", "origin", child), "dependent")

    def test_unknown_or_disjoint_graph_needs_explicit_reporting_attestation(self):
        a, b = self.add_pair(prefix="unknown-origin")
        self.assertEqual(self.contract.pair_assessment("claim-1", a, b), "candidate")
        with self.assertRaises(ContractDenied):
            self.contract.review_independence(self.owner, claim_id="claim-1", record_a=a, record_b=b, outcome="independent", expected_version=0, step_up_id=None, idempotency_key="no-basis", rationale="different urls are not evidence")
        self.assertEqual(self.contract.active_lineage("claim-1"), [])

    def test_graph_cycle_depth_and_node_caps_fail_closed(self):
        a, b = self.add_pair(prefix="cycle")
        self.contract.add_lineage_edge(claim_id="claim-1", parent_record_id=a, child_record_id=b, relation="cites", basis="synthetic edge A")
        self.contract.add_lineage_edge(claim_id="claim-1", parent_record_id=b, child_record_id=a, relation="derived_from", basis="synthetic cycle edge")
        self.assertEqual(self.contract.pair_assessment("claim-1", a, b), "unknown")

        depth_limited = OperatorLineageContract(clock=self.clock, max_depth=1)
        self.addCleanup(depth_limited.close)
        depth_limited.create_claim("depth")
        for i in range(4):
            depth_limited.add_synthetic_record(claim_id="depth", record_id=f"d{i}", source_record_id=f"ds{i}", observation_id=f"do{i}", canonical_url=f"https://example.test/depth/{i}")
        for i in range(3):
            depth_limited.add_lineage_edge(claim_id="depth", parent_record_id=f"d{i}", child_record_id=f"d{i+1}", relation="derived_from", basis="synthetic chain")
        self.assertEqual(depth_limited.pair_assessment("depth", "d0", "d3"), "unknown")

        capped = OperatorLineageContract(clock=self.clock, max_nodes=2)
        self.addCleanup(capped.close)
        capped.create_claim("cap")
        for i in range(3):
            capped.add_synthetic_record(claim_id="cap", record_id=f"c{i}", source_record_id=f"cs{i}", observation_id=f"co{i}", canonical_url=f"https://example.test/cap/{i}")
        self.assertEqual(capped.pair_assessment("cap", "c0", "c1"), "unknown")

    def test_stepup_becomes_stale_when_lineage_graph_changes(self):
        a, b = self.add_pair(prefix="stale-graph")
        details = self.contract.independence_details("claim-1", a, b, reporting_basis_a="basis a", reporting_basis_b="basis b", owner_attests_distinct_origins=True)
        challenge = self.contract.issue_step_up(self.owner, action="accept_independence", claim_id="claim-1", outcome="independent", expected_version=0, graph_version=self.contract.graph_version("claim-1"), details=details)
        self.contract.add_lineage_edge(claim_id="claim-1", parent_record_id=a, child_record_id=b, relation="separately_reported", basis="synthetic graph revision after review")
        with self.assertRaises(ContractDenied):
            self.contract.review_independence(self.owner, claim_id="claim-1", record_a=a, record_b=b, outcome="independent", reporting_basis_a="basis a", reporting_basis_b="basis b", owner_attests_distinct_origins=True, expected_version=0, step_up_id=challenge, idempotency_key="stale-graph", rationale="stale review")

    def test_positive_review_requires_owner_attestation_and_is_claim_specific(self):
        a, b = self.add_pair(prefix="positive")
        decision_id = self.positive_review(a, b)
        self.assertEqual(len(self.contract.active_lineage("claim-1")), 1)
        row = self.contract.audit_history(claim_id="claim-1")[-1]
        self.assertEqual(row["id"], decision_id)
        self.assertEqual(row["actor_ref"], "synthetic-owner-A")
        self.assertIn("distinct", row["details"])
        self.assertEqual(self.contract.claim_state("claim-1")["effective_status"], "pending")

    def test_validation_requires_current_positive_evidence(self):
        with self.assertRaises(ContractDenied):
            self.decision(action="validate_incident", outcome="valid", key="valid-without-evidence")
        a, b = self.add_pair(prefix="valid-claim")
        self.positive_review(a, b, key="valid-lineage")
        current = self.contract.claim_state("claim-1")
        challenge = self.contract.issue_step_up(self.owner, action="validate_incident", claim_id="claim-1", outcome="valid", expected_version=current["version"], graph_version=current["graph_version"])
        self.contract.decide(self.owner, action="validate_incident", claim_id="claim-1", outcome="valid", expected_version=current["version"], step_up_id=challenge, idempotency_key="valid-after-lineage", rationale="fictional owner review")
        self.assertEqual(self.contract.claim_state("claim-1")["effective_status"], "valid")

    def test_new_retraction_invalidates_confirmed_projection_and_preserves_history(self):
        a, b = self.add_pair(prefix="retract")
        correction = "correction-record"
        self.contract.add_synthetic_record(claim_id="claim-1", record_id=correction, source_record_id="sr-correction", observation_id="obs-correction", canonical_url="https://example.test/retraction", record_kind="correction")
        accepted_id = self.positive_review(a, b, key="pre-retraction-independence")
        current = self.contract.claim_state("claim-1")
        challenge = self.contract.issue_step_up(self.owner, action="validate_incident", claim_id="claim-1", outcome="valid", expected_version=current["version"])
        self.contract.decide(self.owner, action="validate_incident", claim_id="claim-1", outcome="valid", expected_version=current["version"], step_up_id=challenge, idempotency_key="pre-retraction-valid", rationale="fictional owner confirmation")
        self.contract.add_lineage_edge(claim_id="claim-1", parent_record_id=correction, child_record_id=a, relation="retracts", basis="synthetic correction retracts source A")
        state = self.contract.claim_state("claim-1")
        self.assertEqual(state["effective_status"], "review_required")
        self.assertEqual(self.contract.active_lineage("claim-1"), [])
        history = self.contract.audit_history(claim_id="claim-1")
        self.assertTrue(any(row["id"] == accepted_id and row["outcome"] == "independent" for row in history))
        self.assertTrue(any(row["action"] == "validate_incident" and row["outcome"] == "valid" for row in history))
        invalidation = next(row for row in history if row["action"] == "lineage_invalidated")
        self.assertEqual(invalidation["supersedes_id"], accepted_id)

    def test_revoking_last_positive_confirmation_requires_review_atomically(self):
        a, b = self.add_pair(prefix="revoke-lineage")
        self.positive_review(a, b, key="revoke-lineage-positive")
        state = self.contract.claim_state("claim-1")
        validation = self.contract.issue_step_up(
            self.owner, action="validate_incident", claim_id="claim-1", outcome="valid",
            expected_version=state["version"],
        )
        self.contract.decide(
            self.owner, action="validate_incident", claim_id="claim-1", outcome="valid",
            expected_version=state["version"], step_up_id=validation,
            idempotency_key="revoke-lineage-valid", rationale="synthetic owner validation",
        )
        state = self.contract.claim_state("claim-1")
        details = self.contract.independence_details("claim-1", a, b)
        challenge = self.contract.issue_step_up(
            self.owner, action="accept_independence", claim_id="claim-1", outcome="revoked",
            expected_version=state["version"], details=details,
        )
        before_history = self.contract.decision_count()
        self.contract.review_independence(
            self.owner, claim_id="claim-1", record_a=a, record_b=b, outcome="revoked",
            expected_version=state["version"], step_up_id=challenge,
            idempotency_key="revoke-last-confirmation", rationale="synthetic owner revocation",
        )
        self.assertEqual(self.contract.claim_state("claim-1")["effective_status"], "review_required")
        self.assertEqual(self.contract.active_lineage("claim-1"), [])
        self.assertEqual(self.contract.decision_count(), before_history + 1)
        self.assertEqual(self.contract.audit_history(claim_id="claim-1")[-1]["outcome"], "revoked")

    def test_fresh_current_graph_evidence_allows_explicit_revalidation_only(self):
        a, b = self.add_pair(prefix="revalidate-old")
        correction = "revalidate-correction"
        self.contract.add_synthetic_record(
            claim_id="claim-1", record_id=correction, source_record_id="sr-revalidate-correction",
            observation_id="obs-revalidate-correction", canonical_url="https://example.test/revalidate/correction",
            record_kind="correction",
        )
        self.positive_review(a, b, key="revalidate-old-positive")
        state = self.contract.claim_state("claim-1")
        challenge = self.contract.issue_step_up(
            self.owner, action="validate_incident", claim_id="claim-1", outcome="valid",
            expected_version=state["version"],
        )
        self.contract.decide(
            self.owner, action="validate_incident", claim_id="claim-1", outcome="valid",
            expected_version=state["version"], step_up_id=challenge,
            idempotency_key="revalidate-initial", rationale="synthetic owner validation",
        )
        self.contract.add_lineage_edge(
            claim_id="claim-1", parent_record_id=correction, child_record_id=a,
            relation="retracts", basis="synthetic correction invalidates earlier basis",
        )
        self.assertEqual(self.contract.claim_state("claim-1")["effective_status"], "review_required")
        self.assertEqual(self.contract.active_lineage("claim-1"), [])

        fresh_a, fresh_b = self.add_pair(prefix="revalidate-fresh")
        self.positive_review(fresh_a, fresh_b, key="revalidate-fresh-positive")
        self.assertEqual(self.contract.claim_state("claim-1")["effective_status"], "review_required")
        self.assertEqual(len(self.contract.active_lineage("claim-1")), 1)
        state = self.contract.claim_state("claim-1")
        challenge = self.contract.issue_step_up(
            self.owner, action="validate_incident", claim_id="claim-1", outcome="valid",
            expected_version=state["version"], graph_version=state["graph_version"],
        )
        self.contract.decide(
            self.owner, action="validate_incident", claim_id="claim-1", outcome="valid",
            expected_version=state["version"], graph_version=state["graph_version"],
            step_up_id=challenge, idempotency_key="revalidate-explicit-owner",
            rationale="explicit review of fresh current-graph evidence",
        )
        self.assertEqual(self.contract.claim_state("claim-1")["effective_status"], "valid")

    def test_multiple_lineage_invalidations_have_sequential_audit_versions(self):
        first = self.add_pair(prefix="sequential-audit-a")
        second = self.add_pair(prefix="sequential-audit-b")
        correction = "sequential-audit-correction"
        self.contract.add_synthetic_record(
            claim_id="claim-1", record_id=correction, source_record_id="sr-sequential-correction",
            observation_id="obs-sequential-correction", canonical_url="https://example.test/sequential/correction",
            record_kind="correction",
        )
        self.positive_review(*first, key="sequential-positive-a")
        self.positive_review(*second, key="sequential-positive-b")
        state = self.contract.claim_state("claim-1")
        challenge = self.contract.issue_step_up(
            self.owner, action="validate_incident", claim_id="claim-1", outcome="valid",
            expected_version=state["version"],
        )
        self.contract.decide(
            self.owner, action="validate_incident", claim_id="claim-1", outcome="valid",
            expected_version=state["version"], step_up_id=challenge,
            idempotency_key="sequential-valid", rationale="synthetic owner validation",
        )
        prior_version = self.contract.claim_state("claim-1")["version"]
        self.contract.add_lineage_edge(
            claim_id="claim-1", parent_record_id=correction, child_record_id=first[0],
            relation="retracts", basis="synthetic correction invalidates both reviewed graph decisions",
        )
        invalidations = [row for row in self.contract.audit_history(claim_id="claim-1")
                         if row["action"] == "lineage_invalidated"]
        self.assertEqual(len(invalidations), 2)
        self.assertEqual([row["prior_version"] for row in invalidations], [prior_version, prior_version + 1])
        self.assertEqual([row["resulting_version"] for row in invalidations], [prior_version + 1, prior_version + 2])

    def test_graph_invalidation_and_projection_rollback_as_one_transaction(self):
        a, b = self.add_pair(prefix="atomic-invalidation")
        correction = "atomic-invalidation-correction"
        self.contract.add_synthetic_record(
            claim_id="claim-1", record_id=correction, source_record_id="sr-atomic-correction",
            observation_id="obs-atomic-correction", canonical_url="https://example.test/atomic/correction",
            record_kind="correction",
        )
        self.positive_review(a, b, key="atomic-invalidation-positive")
        state = self.contract.claim_state("claim-1")
        challenge = self.contract.issue_step_up(
            self.owner, action="validate_incident", claim_id="claim-1", outcome="valid",
            expected_version=state["version"],
        )
        self.contract.decide(
            self.owner, action="validate_incident", claim_id="claim-1", outcome="valid",
            expected_version=state["version"], step_up_id=challenge,
            idempotency_key="atomic-invalidation-valid", rationale="synthetic owner validation",
        )
        before_state = self.contract.claim_state("claim-1")
        before_history = self.contract.decision_count()
        original = self.contract._graph_changed

        def fail_after_invalidation(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError("injected graph invalidation failure")

        with patch.object(self.contract, "_graph_changed", side_effect=fail_after_invalidation):
            with self.assertRaises(RuntimeError):
                self.contract.add_lineage_edge(
                    claim_id="claim-1", parent_record_id=correction, child_record_id=a,
                    relation="retracts", basis="synthetic rollback exercise",
                )
        self.assertEqual(self.contract.claim_state("claim-1"), before_state)
        self.assertEqual(self.contract.decision_count(), before_history)
        self.assertEqual(self.contract.active_lineage("claim-1")[0]["record_a"], min(a, b))
        self.assertEqual(self.contract._db.execute("SELECT COUNT(*) FROM lineage_edges WHERE claim_id='claim-1'").fetchone()[0], 0)

    def test_lineage_edge_endpoints_must_belong_to_claim(self):
        a, b = self.add_pair(prefix="edge-owner")
        other_claim_record = "other-claim-only"
        self.contract.create_claim("claim-2")
        self.contract.add_synthetic_record(
            claim_id="claim-2", record_id=other_claim_record, source_record_id="sr-other-claim",
            observation_id="obs-other-claim", canonical_url="https://example.test/other-claim",
        )
        with self.assertRaises(ContractError):
            self.contract.add_lineage_edge(
                claim_id="claim-1", parent_record_id=a, child_record_id=other_claim_record,
                relation="cites", basis="invalid cross-claim synthetic edge",
            )
        self.assertEqual(self.contract._db.execute("SELECT COUNT(*) FROM lineage_edges").fetchone()[0], 0)
        # Defensive handling for a malformed historical/test-only row bypassing
        # the public API must fail closed instead of indexing a missing node.
        self.contract._db.execute(
            "INSERT INTO lineage_edges(claim_id,parent_record_id,child_record_id,relation,basis,graph_version,created_at) VALUES(?,?,?,?,?,?,?)",
            ("claim-1", other_claim_record, a, "cites", "synthetic malformed fixture", 1, self.clock.now()),
        )
        self.assertEqual(self.contract.pair_assessment("claim-1", a, b), "unknown")

    def test_dependent_unknown_and_disputed_remove_last_valid_confirmation(self):
        for outcome in ("dependent", "unknown", "disputed"):
            with self.subTest(outcome=outcome):
                contract = OperatorLineageContract(clock=FakeClock())
                self.addCleanup(contract.close)
                owner = contract.issue_test_session(actor_ref=f"owner-{outcome}", role="owner")
                contract.create_claim("claim-x")
                a = f"{outcome}-a"
                b = f"{outcome}-b"
                for record_id, source_id in ((a, f"src-{a}"), (b, f"src-{b}")):
                    contract.add_synthetic_record(
                        claim_id="claim-x", record_id=record_id, source_record_id=source_id,
                        observation_id=f"obs-{record_id}", canonical_url=f"https://example.test/{record_id}",
                    )
                def accept(review_outcome, key, version):
                    basis_a, basis_b = "synthetic report A", "synthetic report B"
                    attests = review_outcome == "independent"
                    details = contract.independence_details(
                        "claim-x", a, b, reporting_basis_a=basis_a if attests else "",
                        reporting_basis_b=basis_b if attests else "",
                        owner_attests_distinct_origins=attests,
                    )
                    step = contract.issue_step_up(
                        owner, action="accept_independence", claim_id="claim-x",
                        outcome=review_outcome, expected_version=version, details=details,
                    )
                    contract.review_independence(
                        owner, claim_id="claim-x", record_a=a, record_b=b,
                        outcome=review_outcome,
                        reporting_basis_a=basis_a if attests else "",
                        reporting_basis_b=basis_b if attests else "",
                        owner_attests_distinct_origins=attests, expected_version=version,
                        step_up_id=step, idempotency_key=key,
                        rationale=f"synthetic {review_outcome} review",
                    )

                accept("independent", f"positive-{outcome}", 0)
                state = contract.claim_state("claim-x")
                validation = contract.issue_step_up(
                    owner, action="validate_incident", claim_id="claim-x", outcome="valid",
                    expected_version=state["version"],
                )
                contract.decide(
                    owner, action="validate_incident", claim_id="claim-x", outcome="valid",
                    expected_version=state["version"], step_up_id=validation,
                    idempotency_key=f"valid-{outcome}", rationale="synthetic confirmation",
                )
                state = contract.claim_state("claim-x")
                accept(outcome, f"withdraw-{outcome}", state["version"])
                self.assertEqual(contract.claim_state("claim-x")["effective_status"], "review_required")
                self.assertEqual(contract.active_lineage("claim-x"), [])

    def test_classification_roundtrip_does_not_restore_old_lineage_attestation(self):
        a, b = self.add_pair(prefix="classification-roundtrip")
        self.positive_review(a, b, key="roundtrip-original-attestation")
        state = self.contract.claim_state("claim-1")
        self.decision(
            action="accept_classification", outcome="publication_record",
            expected_version=state["version"], key="roundtrip-to-publication",
        )
        self.assertEqual(self.contract.active_lineage("claim-1"), [])
        state = self.contract.claim_state("claim-1")
        self.decision(
            action="accept_classification", outcome="incident_hypothesis",
            expected_version=state["version"], key="roundtrip-back-to-incident",
        )
        self.assertEqual(self.contract.claim_state("claim-1")["semantic_kind"], "incident_hypothesis")
        self.assertEqual(self.contract.active_lineage("claim-1"), [])
        with self.assertRaises(ContractDenied):
            self.decision(
                action="validate_incident", outcome="valid",
                expected_version=self.contract.claim_state("claim-1")["version"],
                key="roundtrip-without-new-attestation",
            )

    def test_publication_claim_cannot_receive_positive_independence_audit(self):
        self.contract.create_claim("publication-claim", semantic_kind="publication_record")
        a, b = self.add_pair(claim_id="publication-claim", prefix="publication-positive-deny")
        details = self.contract.independence_details(
            "publication-claim", a, b, reporting_basis_a="synthetic basis A",
            reporting_basis_b="synthetic basis B", owner_attests_distinct_origins=True,
        )
        challenge = self.contract.issue_step_up(
            self.owner, action="accept_independence", claim_id="publication-claim",
            outcome="independent", expected_version=0, details=details,
        )
        before = self.contract.decision_count()
        with self.assertRaises(ContractDenied):
            self.contract.review_independence(
                self.owner, claim_id="publication-claim", record_a=a, record_b=b,
                outcome="independent", reporting_basis_a="synthetic basis A",
                reporting_basis_b="synthetic basis B", owner_attests_distinct_origins=True,
                expected_version=0, step_up_id=challenge,
                idempotency_key="publication-positive-denied", rationale="must be rejected",
            )
        with self.assertRaises(ContractDenied):
            self.contract._decide_claim(
                self.owner, action="accept_independence", claim_id="publication-claim",
                outcome="independent", expected_version=0, step_up_id=challenge,
                idempotency_key="publication-private-path-denied", rationale="must be rejected",
                details=details, graph_version=self.contract.graph_version("publication-claim"),
                pair=tuple(sorted((a, b))),
            )
        self.assertEqual(self.contract.decision_count(), before)
        self.assertEqual(self.contract.claim_state("publication-claim")["version"], 0)
        self.assertIsNone(
            self.contract._db.execute(
                "SELECT consumed_at FROM stepups WHERE challenge_id=?", (challenge,),
            ).fetchone()[0]
        )
        self.assertEqual(self.contract.active_lineage("publication-claim"), [])

    def test_publication_roundtrip_requires_new_positive_owner_attestation(self):
        a, b = self.add_pair(prefix="publication-roundtrip")
        self.positive_review(a, b, key="publication-roundtrip-original")
        state = self.contract.claim_state("claim-1")
        self.decision(
            action="accept_classification", outcome="publication_record",
            expected_version=state["version"], key="publication-roundtrip-to-publication",
        )
        state = self.contract.claim_state("claim-1")
        rejected_details = self.contract.independence_details(
            "claim-1", a, b, reporting_basis_a="synthetic re-attestation A",
            reporting_basis_b="synthetic re-attestation B", owner_attests_distinct_origins=True,
        )
        rejected_stepup = self.contract.issue_step_up(
            self.owner, action="accept_independence", claim_id="claim-1",
            outcome="independent", expected_version=state["version"], details=rejected_details,
        )
        before = self.contract.decision_count()
        with self.assertRaises(ContractDenied):
            self.contract.review_independence(
                self.owner, claim_id="claim-1", record_a=a, record_b=b,
                outcome="independent", reporting_basis_a="synthetic re-attestation A",
                reporting_basis_b="synthetic re-attestation B", owner_attests_distinct_origins=True,
                expected_version=state["version"], step_up_id=rejected_stepup,
                idempotency_key="publication-roundtrip-denied", rationale="cannot attest publication",
            )
        self.assertEqual(self.contract.decision_count(), before)
        state = self.contract.claim_state("claim-1")
        self.decision(
            action="accept_classification", outcome="incident_hypothesis",
            expected_version=state["version"], key="publication-roundtrip-back-to-incident",
        )
        self.assertEqual(self.contract.active_lineage("claim-1"), [])
        basis_a, basis_b = "fresh synthetic origin A", "fresh synthetic origin B"
        fresh_details = self.contract.independence_details(
            "claim-1", a, b, reporting_basis_a=basis_a, reporting_basis_b=basis_b,
            owner_attests_distinct_origins=True,
        )
        state = self.contract.claim_state("claim-1")
        fresh_stepup = self.contract.issue_step_up(
            self.owner, action="accept_independence", claim_id="claim-1",
            outcome="independent", expected_version=state["version"],
            graph_version=state["graph_version"], details=fresh_details,
        )
        self.contract.review_independence(
            self.owner, claim_id="claim-1", record_a=a, record_b=b, outcome="independent",
            reporting_basis_a=basis_a, reporting_basis_b=basis_b,
            owner_attests_distinct_origins=True, expected_version=state["version"],
            step_up_id=fresh_stepup, idempotency_key="publication-roundtrip-fresh-attestation",
            rationale="explicit fresh owner review after classification roundtrip",
        )
        self.assertEqual(len(self.contract.active_lineage("claim-1")), 1)

    def test_independence_retry_after_graph_change_returns_history_not_current_validity(self):
        a, b = self.add_pair(prefix="historical-retry")
        basis_a = "fictional separately gathered interview"
        basis_b = "fictional separately obtained record"
        accepted_id = self.contract.review_independence(
            self.owner, claim_id="claim-1", record_a=a, record_b=b, outcome="independent",
            reporting_basis_a=basis_a, reporting_basis_b=basis_b,
            owner_attests_distinct_origins=True, expected_version=0,
            step_up_id=self.contract.issue_step_up(
                self.owner, action="accept_independence", claim_id="claim-1",
                outcome="independent", expected_version=0,
                details=self.contract.independence_details(
                    "claim-1", a, b, reporting_basis_a=basis_a,
                    reporting_basis_b=basis_b, owner_attests_distinct_origins=True,
                ),
            ),
            idempotency_key="historical-lineage-retry", rationale="synthetic distinct origins",
        )
        self.contract.add_lineage_edge(
            claim_id="claim-1", parent_record_id=a, child_record_id=b,
            relation="cites", basis="later synthetic graph fact invalidates independence",
        )
        replayed_id = self.contract.review_independence(
            self.owner, claim_id="claim-1", record_a=a, record_b=b, outcome="independent",
            reporting_basis_a=basis_a, reporting_basis_b=basis_b,
            owner_attests_distinct_origins=True, expected_version=0,
            step_up_id=None, idempotency_key="historical-lineage-retry",
            rationale="synthetic distinct origins",
        )
        self.assertEqual(replayed_id, accepted_id)
        self.assertEqual(self.contract.audit_history(claim_id="claim-1")[0]["outcome"], "independent")
        self.assertEqual(self.contract.pair_assessment("claim-1", a, b), "dependent")
        self.assertEqual(self.contract.active_lineage("claim-1"), [])

    def test_reclassification_excludes_active_lineage_without_deleting_history(self):
        a, b = self.add_pair(prefix="reclass")
        self.positive_review(a, b, key="before-reclass")
        self.assertEqual(len(self.contract.active_lineage("claim-1")), 1)
        current = self.contract.claim_state("claim-1")
        self.decision(action="accept_classification", outcome="publication_record", expected_version=current["version"], key="reclass-publication")
        self.assertEqual(self.contract.active_lineage("claim-1"), [])
        self.assertEqual(self.contract.claim_state("claim-1")["semantic_kind"], "publication_record")
        self.assertTrue(any(row["action"] == "accept_independence" for row in self.contract.audit_history(claim_id="claim-1")))

    def test_valid_incident_cannot_be_silently_reclassified(self):
        a, b = self.add_pair(prefix="sticky")
        self.positive_review(a, b, key="sticky-positive")
        current = self.contract.claim_state("claim-1")
        challenge = self.contract.issue_step_up(self.owner, action="validate_incident", claim_id="claim-1", outcome="valid", expected_version=current["version"])
        self.contract.decide(self.owner, action="validate_incident", claim_id="claim-1", outcome="valid", expected_version=current["version"], step_up_id=challenge, idempotency_key="sticky-valid", rationale="fictional owner confirmation")
        current = self.contract.claim_state("claim-1")
        challenge = self.contract.issue_step_up(self.owner, action="accept_classification", claim_id="claim-1", outcome="publication_record", expected_version=current["version"])
        with self.assertRaises(ContractDenied):
            self.contract.decide(self.owner, action="accept_classification", claim_id="claim-1", outcome="publication_record", expected_version=current["version"], step_up_id=challenge, idempotency_key="silent-reclass", rationale="must fail closed")


if __name__ == "__main__":
    unittest.main()
