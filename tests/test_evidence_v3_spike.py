"""Synthetic-only tests for the isolated source/evidence v3 spike."""

from __future__ import annotations

from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from experiments.source_evidence_v3 import spike


def observation(record_id: int, run_id: int, digest: str, collected: str, **extra):
    return {
        "source_record_id": record_id,
        "source_id": "gdelt-raw-events-v2",
        "ingest_run_id": run_id,
        "revision_hash": digest * 64,
        "collected_at": collected,
        "canonical_url": "https://example.test/synthetic/item",
        "title": "Fictional generated record",
        **extra,
    }


class EvidenceV3SpikeTests(unittest.TestCase):
    def setUp(self):
        self.db = spike.new_synthetic_v2()
        self.addCleanup(self.db.close)
        self.baseline = [tuple(row) for row in self.db.execute("SELECT * FROM events ORDER BY id")]
        self.baseline_sources = [tuple(row) for row in self.db.execute("SELECT * FROM event_sources ORDER BY event_id,source_id")]
        spike.apply_candidate_v3(self.db)
        self.collector = spike.synthetic_test_collector_authorizer(self.db)

    def make_record(self, namespace="gdelt-events-v2", external_id="fictional-100", source_id="gdelt-raw-events-v2", kind="provider_observation"):
        return spike.add_source_record(
            self.db, authorizer=self.collector, source_id=source_id, record_kind=kind,
            provider_namespace=namespace, external_record_id=external_id,
        )

    def test_legacy_publication_and_incident_candidate_remain_unclassified(self):
        self.assertEqual(self.baseline, [tuple(row) for row in self.db.execute("SELECT * FROM events ORDER BY id")])
        self.assertEqual(self.baseline_sources, [tuple(row) for row in self.db.execute("SELECT * FROM event_sources ORDER BY event_id,source_id")])
        self.assertEqual(
            spike.legacy_event_semantics(self.db),
            [(1, "unclassified", "pending"), (2, "unclassified", "pending")],
        )
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM event_semantic_history").fetchone()[0], 0)
        self.assertEqual(self.db.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0], 3)

    def test_old_valid_status_does_not_create_incident_classification(self):
        self.db.execute(
            "INSERT INTO events(id,dedup_key,title,collected_at,validation_status,created_at) VALUES (3,'synthetic:old-valid','Fictional legacy row','2026-01-01T00:00:00Z','valid','2026-01-01T00:00:00Z')"
        )
        self.db.commit()
        # This fixture row was inserted before v3; its old status is preserved but
        # is not treated as a semantic or independent confirmation.
        self.assertEqual(spike.legacy_event_semantics(self.db)[-1], (3, "unclassified", "valid"))

    def test_foreign_keys_enforce_source_consistent_run_and_record(self):
        rid = self.make_record()
        with self.assertRaises(sqlite3.IntegrityError):
            spike.add_observations(self.db, [observation(rid, 2, "a", "2026-01-02T00:00:00Z")], authorizer=self.collector)
        self.assertEqual(self.db.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_provider_identity_is_namespace_scoped_and_deduplicated(self):
        self.make_record(external_id="same-generated-id")
        with self.assertRaises(sqlite3.IntegrityError):
            self.make_record(external_id="same-generated-id")
        second = self.make_record(namespace="another-fictional-namespace", external_id="same-generated-id")
        self.assertIsInstance(second, int)

    def test_missing_provider_id_is_reserved_for_explicit_legacy_provenance(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.make_record(external_id=None)
        legacy_id = spike.add_source_record(
            self.db, authorizer=self.collector, source_id="fictional-local-source", record_kind="legacy_provenance",
            provider_namespace=None, external_record_id=None,
        )
        self.assertIsInstance(legacy_id, int)

    def test_changed_revisions_append_and_out_of_order_backfill_is_preserved(self):
        rid = self.make_record()
        first, second = spike.add_observations(self.db, [
            observation(rid, 3, "a", "2026-01-02T10:00:00Z", source_published_at="2026-01-02T10:00:00+02:00", source_updated_at="2026-01-02T10:15:00+02:00", gdelt_sqldate="20260101", gdelt_dateadded_at="2026-01-02T09:45:00Z", attribution="Fictional provider", licence_uri="https://example.test/licence", geo_source_fields=None),
            observation(rid, 4, "b", "2026-01-01T10:00:00Z", gdelt_sqldate="20251231", gdelt_dateadded_at="2026-01-01T09:45:00Z"),
        ], authorizer=self.collector)
        self.assertNotEqual(first, second)
        rows = self.db.execute(
            "SELECT collected_at,gdelt_sqldate,gdelt_dateadded_at FROM source_record_observations WHERE source_record_id=? ORDER BY collected_at",
            (rid,),
        ).fetchall()
        self.assertEqual([row[0] for row in rows], ["2026-01-01T10:00:00.000000Z", "2026-01-02T10:00:00.000000Z"])
        self.assertEqual(rows[0][1], "2025-12-31")
        self.assertNotIn("T00:00", rows[0][1])
        first_metadata = self.db.execute(
            "SELECT source_published_at,source_updated_at,collected_at,gdelt_dateadded_at,attribution,licence_uri,geo_source_fields FROM source_record_observations WHERE id=?",
            (first,),
        ).fetchone()
        self.assertEqual(first_metadata[0], "2026-01-02T08:00:00.000000Z")
        self.assertEqual(first_metadata[1], "2026-01-02T08:15:00.000000Z")
        self.assertEqual(first_metadata[2], "2026-01-02T10:00:00.000000Z")
        self.assertEqual(first_metadata[3], "2026-01-02T09:45:00.000000Z")
        self.assertEqual(first_metadata[4:6], ("Fictional provider", "https://example.test/licence"))
        self.assertIsNone(first_metadata[6])
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("UPDATE source_record_observations SET title='rewritten' WHERE id=?", (first,))
        self.db.rollback()
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("UPDATE source_records SET external_record_id='rewritten' WHERE id=?", (rid,))
        self.db.rollback()

    def test_same_revision_replay_in_one_run_is_rejected_atomically(self):
        rid = self.make_record()
        item = observation(rid, 3, "c", "2026-01-02T10:00:00Z")
        spike.add_observations(self.db, [item], authorizer=self.collector)
        with self.assertRaises(sqlite3.IntegrityError):
            spike.add_observations(self.db, [item], authorizer=self.collector)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM source_record_observations").fetchone()[0], 1)

    def test_same_canonical_url_across_providers_is_not_a_unique_source_identity(self):
        gdelt_id = self.make_record()
        govuk_id = spike.add_source_record(
            self.db, authorizer=self.collector, source_id="govuk-news-atom-v1", record_kind="publication",
            provider_namespace="atom-v1", external_record_id="urn:uuid:fictional-1",
        )
        spike.add_observations(self.db, [
            observation(gdelt_id, 3, "d", "2026-01-02T00:00:00Z"),
            {"source_record_id": govuk_id, "source_id": "govuk-news-atom-v1", "ingest_run_id": 1,
             "revision_hash": "e" * 64, "canonical_url": "https://example.test/synthetic/item",
             "collected_at": "2026-01-01T00:00:00Z", "attribution": "Fictional publisher"},
        ], authorizer=self.collector)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM source_record_observations WHERE canonical_url=?", ("https://example.test/synthetic/item",)).fetchone()[0], 2)
        owner = spike.synthetic_test_owner_authorizer(self.db)
        spike.accept_classification(self.db, event_id=2, semantic_kind="incident_hypothesis", rationale="fictional test event", authorizer=owner)
        obs_ids = [row[0] for row in self.db.execute("SELECT id FROM source_record_observations ORDER BY id")]
        for obs_id in obs_ids:
            spike.add_evidence_proposal(self.db, observation_id=obs_id, event_id=2, claim_key="occurrence", relation="unverified", role="ai")
        self.assertTrue(all(row[0] == "unknown" for row in self.db.execute("SELECT independence_state FROM event_evidence")))

    def test_invalid_offsets_and_missing_times_are_not_inferred(self):
        for bad in ("2026-01-01T10:00:00+00:99", "2026-01-01T10:00:00+24:00", "2026-01-01T10:00:00"):
            with self.subTest(value=bad), self.assertRaises(ValueError):
                spike.normalize_utc(bad)
        self.assertEqual(spike.normalize_utc("2026-01-01T10:00:00+01:00"), "2026-01-01T09:00:00.000000Z")
        self.assertIsNone(spike.normalize_utc(None))
        self.assertIsNone(spike.normalize_sql_date(None))
        with self.assertRaises(ValueError):
            spike.normalize_sql_date("20260230")

    def test_late_batch_failure_rolls_back_prior_insert(self):
        rid = self.make_record()
        batch = [observation(rid, 3, "f", "2026-01-02T00:00:00Z"), observation(rid, 3, "f", "2026-01-02T00:01:00Z")]
        with self.assertRaises(sqlite3.IntegrityError):
            spike.add_observations(self.db, batch, authorizer=self.collector)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM source_record_observations").fetchone()[0], 0)

    def test_v3_migration_failure_rolls_back_all_ddl_and_retry_succeeds(self):
        db = spike.new_synthetic_v2()
        self.addCleanup(db.close)
        with patch.object(spike, "_record_v3_version", side_effect=RuntimeError("synthetic injected failure")):
            with self.assertRaises(RuntimeError):
                spike.apply_candidate_v3(db)
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertNotIn("source_records", tables)
        self.assertEqual(db.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0], 2)
        spike.apply_candidate_v3(db)
        self.assertEqual(db.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0], 3)

    def test_v3_refuses_file_database_and_unknown_fixture(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "synthetic.sqlite"
            file_db = sqlite3.connect(path)
            self.addCleanup(file_db.close)
            with self.assertRaises(PermissionError):
                spike.apply_candidate_v3(file_db)
        unknown_db = sqlite3.connect(":memory:")
        self.addCleanup(unknown_db.close)
        with self.assertRaises(PermissionError):
            spike.apply_candidate_v3(unknown_db)

    def test_observation_urls_are_restricted_to_synthetic_domain(self):
        rid = self.make_record()
        with self.assertRaises(ValueError):
            spike.add_observations(self.db, [observation(rid, 3, "4", "2026-01-02T00:00:00Z", canonical_url="https://real.example/article")], authorizer=self.collector)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM source_record_observations").fetchone()[0], 0)

    def test_untrusted_actor_cannot_impersonate_owner_or_accept_classification(self):
        fake_owner = {"actor_label": "synthetic-owner-fixture", "is_owner": True}
        with self.assertRaises(PermissionError):
            spike.accept_classification(
                self.db, event_id=2, semantic_kind="incident_hypothesis",
                rationale="fake caller assertion", authorizer=fake_owner,
            )
        with self.assertRaises(PermissionError):
            spike.propose_classification(self.db, event_id=2, semantic_kind="incident_hypothesis", role="owner", rationale="forged role")
        with self.assertRaises(PermissionError):
            spike.add_source_record(self.db, authorizer=fake_owner, source_id="fictional-local-source", record_kind="publication", provider_namespace="fake", external_record_id="fake")
        self.assertEqual(spike.legacy_event_semantics(self.db)[1][1], "unclassified")

    def test_owner_rejection_is_audited_and_keeps_legacy_event_unclassified(self):
        proposal = spike.propose_classification(self.db, event_id=2, semantic_kind="incident_hypothesis", role="ai", rationale="fictional model suggestion")
        owner = spike.synthetic_test_owner_authorizer(self.db)
        rejected = spike.reject_classification_proposal(self.db, proposal_id=proposal, rationale="synthetic owner rejected proposal", authorizer=owner)
        row = self.db.execute("SELECT decision_state,actor_kind,supersedes_id FROM event_semantic_history WHERE id=?", (rejected,)).fetchone()
        self.assertEqual(tuple(row), ("rejected","synthetic-owner",proposal))
        self.assertEqual(spike.legacy_event_semantics(self.db)[1][1], "unclassified")

    def test_test_authorizer_is_explicit_and_bound_to_its_memory_database(self):
        owner = spike.synthetic_test_owner_authorizer(self.db)
        other = spike.new_synthetic_v2()
        self.addCleanup(other.close)
        spike.apply_candidate_v3(other)
        with self.assertRaises(PermissionError):
            spike.accept_classification(other, event_id=2, semantic_kind="incident_hypothesis", rationale="synthetic", authorizer=owner)
        decision = spike.accept_classification(self.db, event_id=2, semantic_kind="incident_hypothesis", rationale="fictional owner review", authorizer=owner)
        self.assertGreater(decision, 0)
        self.assertEqual(self.db.execute("SELECT validation_status FROM events WHERE id=2").fetchone()[0], "pending")

    def test_unclassified_and_publication_rows_cannot_receive_hypothesis_evidence(self):
        rid = self.make_record()
        observation_id = spike.add_observations(self.db, [observation(rid, 3, "1", "2026-01-02T00:00:00Z")], authorizer=self.collector)[0]
        with self.assertRaises(PermissionError):
            spike.add_evidence_proposal(self.db, observation_id=observation_id, event_id=1, claim_key="occurrence", relation="supports", role="ai")
        with self.assertRaises(PermissionError):
            spike.add_evidence_proposal(self.db, observation_id=observation_id, event_id=2, claim_key="occurrence", relation="supports", role="ai")
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(
                """INSERT INTO event_evidence(observation_id,event_id,claim_key,relation,independence_state,decision_state,actor_kind,actor_label,decided_at,rationale)
                   VALUES (?,1,'occurrence','supports','unknown','proposed','ai','ai-proposal','2026-01-02T00:00:00Z','synthetic')""",
                (observation_id,),
            )
        self.db.rollback()
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(
                "INSERT INTO event_hypothesis_history(event_id,validation_status,actor_label,decided_at,rationale) VALUES (1,'valid','fake','2026-01-02T00:00:00Z','synthetic')"
            )
        self.db.rollback()
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM event_evidence").fetchone()[0], 0)

    def test_owner_classified_publication_is_still_not_an_incident(self):
        owner = spike.synthetic_test_owner_authorizer(self.db)
        spike.accept_classification(self.db, event_id=1, semantic_kind="publication_record", rationale="fictional publication review", authorizer=owner)
        rid = self.make_record()
        obs_id = spike.add_observations(self.db, [observation(rid, 3, "5", "2026-01-02T00:00:00Z")], authorizer=self.collector)[0]
        with self.assertRaises(PermissionError):
            spike.add_evidence_proposal(self.db, observation_id=obs_id, event_id=1, claim_key="occurrence", relation="supports", role="ai")
        with self.assertRaises(PermissionError):
            spike.set_incident_validation(self.db, event_id=1, status="valid", rationale="publication is not incident", authorizer=owner)
        self.assertEqual(spike.legacy_event_semantics(self.db)[0][1], "publication_record")
        self.assertEqual(self.db.execute("SELECT validation_status FROM events WHERE id=1").fetchone()[0], "pending")

    def test_ai_and_collector_only_propose_evidence_and_cannot_accept_independence(self):
        owner = spike.synthetic_test_owner_authorizer(self.db)
        spike.accept_classification(self.db, event_id=2, semantic_kind="incident_hypothesis", rationale="synthetic", authorizer=owner)
        rid = self.make_record()
        obs_id = spike.add_observations(self.db, [observation(rid, 3, "2", "2026-01-02T00:00:00Z")], authorizer=self.collector)[0]
        ai_evidence = spike.add_evidence_proposal(self.db, observation_id=obs_id, event_id=2, claim_key="occurrence", relation="supports", role="ai")
        other_evidence = spike.add_evidence_proposal(self.db, observation_id=obs_id, event_id=2, claim_key="occurrence", relation="contradicts", role="ai")
        collector = spike.add_evidence_proposal(self.db, observation_id=obs_id, event_id=2, claim_key="location", relation="unverified", role="collector")
        with self.assertRaises(PermissionError):
            spike.add_evidence_proposal(self.db, observation_id=obs_id, event_id=2, claim_key="occurrence", relation="supports", role="collector")
        with self.assertRaises(PermissionError):
            spike.accept_independence(self.db, evidence_id=ai_evidence, independent=True, rationale="fake", authorizer={"is_owner": True})
        with self.assertRaises(PermissionError):
            spike.set_incident_validation(self.db, event_id=2, status="valid", rationale="fake", authorizer={"is_owner": True})
        rows = self.db.execute("SELECT id,relation,decision_state,independence_state FROM event_evidence ORDER BY id").fetchall()
        self.assertEqual([(r["relation"],r["decision_state"],r["independence_state"]) for r in rows], [
            ("supports","proposed","unknown"),("contradicts","proposed","unknown"),("unverified","proposed","unknown")
        ])
        self.assertNotEqual(ai_evidence, other_evidence)
        self.assertNotEqual(other_evidence, collector)

    def test_owner_decisions_are_append_only_and_classification_does_not_verify_incident(self):
        owner = spike.synthetic_test_owner_authorizer(self.db)
        proposed_id = spike.propose_classification(self.db, event_id=2, semantic_kind="incident_hypothesis", role="ai", rationale="synthetic AI proposal")
        accepted_id = spike.accept_classification(self.db, event_id=2, semantic_kind="incident_hypothesis", rationale="synthetic owner review", authorizer=owner)
        self.assertGreater(accepted_id, proposed_id)
        spike.propose_classification(self.db, event_id=2, semantic_kind="publication_record", role="ai", rationale="later proposal does not change accepted classification")
        self.assertEqual(spike.legacy_event_semantics(self.db)[1][1], "incident_hypothesis")
        self.assertEqual(self.db.execute("SELECT validation_status FROM events WHERE id=2").fetchone()[0], "pending")
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("UPDATE event_semantic_history SET rationale='rewritten' WHERE id=?", (accepted_id,))
        self.db.rollback()
        with self.assertRaises(PermissionError):
            spike.set_incident_validation(self.db, event_id=1, status="valid", rationale="publication cannot be incident", authorizer=owner)
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("UPDATE events SET validation_status='valid' WHERE id=1")
        self.db.rollback()
        spike.set_incident_validation(self.db, event_id=2, status="valid", rationale="explicit fictional owner decision", authorizer=owner)
        self.assertEqual(self.db.execute("SELECT validation_status FROM events WHERE id=2").fetchone()[0], "valid")
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("UPDATE event_hypothesis_history SET rationale='rewritten' WHERE event_id=2")
        self.db.rollback()

    def test_independence_review_is_explicit_owner_action_and_keeps_history(self):
        owner = spike.synthetic_test_owner_authorizer(self.db)
        spike.accept_classification(self.db, event_id=2, semantic_kind="incident_hypothesis", rationale="fictional", authorizer=owner)
        rid = self.make_record()
        obs_id = spike.add_observations(self.db, [observation(rid, 3, "3", "2026-01-02T00:00:00Z")], authorizer=self.collector)[0]
        evidence = spike.add_evidence_proposal(self.db, observation_id=obs_id, event_id=2, claim_key="occurrence", relation="supports", role="ai")
        decision = spike.accept_independence(self.db, evidence_id=evidence, independent=False, rationale="same fictional lineage", authorizer=owner)
        old, new = self.db.execute("SELECT decision_state,independence_state FROM event_evidence WHERE id IN (?,?) ORDER BY id", (evidence,decision)).fetchall()
        self.assertEqual(tuple(old), ("proposed","unknown"))
        self.assertEqual(tuple(new), ("accepted","dependent"))
        self.assertEqual(self.db.execute("SELECT supersedes_id FROM event_evidence WHERE id=?", (decision,)).fetchone()[0], evidence)


if __name__ == "__main__":
    unittest.main()
