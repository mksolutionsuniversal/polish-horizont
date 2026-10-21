"""Loopback-only HTTP tests with fictional AuthN and in-memory authority."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import json
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from experiments.operator_http_lab.boundary import (
    AuthNRejected,
    AuthNUnavailable,
    AuthorityUnavailable,
    BoundaryConflict,
    BoundaryDenied,
    BoundaryConfig,
    OperatorHttpLab,
    VerifiedIdentity,
)
from experiments.operator_lineage_contract.contract import (
    ContractConflict,
    ContractDenied,
    FakeClock,
    OperatorLineageContract,
)


@dataclass
class FakeAuthN:
    clock: FakeClock
    unavailable: bool = False
    identities: dict[str, str] | None = None
    auth_times: dict[str, int] | None = None

    def __post_init__(self):
        self.identities = self.identities or {
            "fictional-owner-assertion": "fictional-owner",
            "fictional-owner-stepup": "fictional-owner",
            "fictional-reader-assertion": "fictional-reader",
        }
        self.auth_times = self.auth_times or {}

    def verify_user_assertion(self, assertion: str) -> VerifiedIdentity:
        if self.unavailable:
            raise AuthNUnavailable("synthetic verifier outage")
        subject = self.identities.get(assertion)
        if subject is None:
            raise AuthNRejected("invalid synthetic assertion")
        return VerifiedIdentity(
            issuer="https://identity.example.test", subject=subject,
            audience="horizont-operator-lab",
            authenticated_at=self.auth_times.get(assertion, self.clock.now()),
        )


class ContractBackend:
    """Test-only AuthZ port mapping verified subjects to Stage B fake contexts."""

    def __init__(self, contract: OperatorLineageContract, contexts: dict[str, object]):
        self.contract = contract
        self.contexts = contexts
        self.available = True

    def _context(self, actor_ref: str):
        if not self.available:
            raise AuthorityUnavailable("synthetic audit/decision backend outage")
        context = self.contexts.get(actor_ref)
        if context is None:
            raise BoundaryDenied("no synthetic authority for subject")
        return context

    def read_claim(self, claim_id: str):
        state = self.contract.claim_state(claim_id)
        return {
            "claim_id": claim_id,
            "semantic_kind": state["semantic_kind"],
            "effective_status": state["effective_status"],
            "version": state["version"],
            "graph_version": state["graph_version"],
            "active_evidence_count": len(self.contract.active_lineage(claim_id)),
        }

    def graph_digest(self, claim_id: str) -> str:
        if not self.available:
            raise AuthorityUnavailable("synthetic graph reader outage")
        return self.contract.graph_digest(claim_id)

    def issue_step_up(self, actor_ref: str, **request):
        context = self._context(actor_ref)
        try:
            return self.contract.issue_step_up(context, **request)
        except ContractDenied as exc:
            raise BoundaryDenied(str(exc)) from exc
        except ContractConflict as exc:
            raise BoundaryConflict(str(exc)) from exc

    def decide(self, actor_ref: str, **request):
        context = self._context(actor_ref)
        try:
            guard = request.pop("commit_guard")
            with guard():
                return self.contract.decide(context, **request)
        except ContractDenied as exc:
            raise BoundaryDenied(str(exc)) from exc
        except ContractConflict as exc:
            raise BoundaryConflict(str(exc)) from exc


class OperatorHttpLabTests(unittest.TestCase):
    ORIGIN = "https://operator.example.test"
    HOST = "operator.example.test"

    def setUp(self):
        self.clock = FakeClock()
        self.contract = OperatorLineageContract(clock=self.clock)
        self.owner_context = self.contract.issue_test_session(
            actor_ref="fictional-owner", role="owner",
        )
        self.reader_context = self.contract.issue_test_session(
            actor_ref="fictional-reader", role="ai_readonly",
        )
        self.contract.create_claim("claim-1")
        self.contract.add_synthetic_record(
            claim_id="claim-1", record_id="record-a", source_record_id="source-a",
            observation_id="observation-a", canonical_url="https://example.test/a",
        )
        self.contract.add_synthetic_record(
            claim_id="claim-1", record_id="record-b", source_record_id="source-b",
            observation_id="observation-b", canonical_url="https://example.test/b",
        )
        self.backend = ContractBackend(self.contract, {
            "fictional-owner": self.owner_context,
            "fictional-reader": self.reader_context,
        })
        self.authn = FakeAuthN(self.clock)
        self.lab = OperatorHttpLab(
            authn_adapter=self.authn,
            authority=self.backend,
            config=BoundaryConfig(session_ttl_seconds=120, step_up_max_age_seconds=60),
            clock=self.clock.now,
        )
        self.server, self.thread, self.base_url = self._start(self.lab)
        self.addCleanup(self._stop, self.server, self.thread)
        self.addCleanup(self.contract.close)

    @staticmethod
    def _start(lab):
        server = lab.build_loopback_server()
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server, thread, f"http://127.0.0.1:{server.server_address[1]}"

    @staticmethod
    def _stop(server, thread):
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    def request(self, method, path, *, body=None, raw=None, cookie=None, csrf=None,
                origin=ORIGIN, host=HOST, headers=None, content_type="application/json"):
        url = self.base_url + path
        if raw is None and body is not None:
            raw = json.dumps(body, separators=(",", ":")).encode()
        request = Request(url, data=raw, method=method)
        request.add_header("Host", host)
        if origin is not None:
            request.add_header("Origin", origin)
        if raw is not None:
            request.add_header("Content-Type", content_type)
        if cookie:
            request.add_header("Cookie", cookie)
        if csrf is not None:
            request.add_header("X-CSRF-Token", csrf)
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        try:
            with urlopen(request, timeout=3) as response:
                payload = response.read()
                return response.status, payload, response.headers
        except HTTPError as error:
            payload = error.read()
            headers_out = error.headers
            status = error.code
            error.close()
            return status, payload, headers_out

    def login(self, assertion="fictional-owner-assertion", *, prior_cookie=None,
              origin=ORIGIN, host=HOST):
        status, body, headers = self.request(
            "POST", "/__test__/session", body={"assertion": assertion},
            cookie=prior_cookie, origin=origin, host=host,
        )
        set_cookie = headers.get("Set-Cookie", "")
        return status, set_cookie.split(";", 1)[0] if set_cookie else None, body, headers

    def csrf(self, cookie):
        status, body, _ = self.request("GET", "/api/csrf", cookie=cookie)
        return status, json.loads(body).get("csrf_token") if body else None

    def prepare_operation(self, cookie, csrf, *, outcome="other_or_unknown", key="http-decision",
                          assertion="fictional-owner-stepup", rationale="synthetic owner decision"):
        status, state_body, _ = self.request("GET", "/api/claims/claim-1", cookie=cookie)
        self.assertEqual(status, 200)
        state = json.loads(state_body)
        fields = {
            "action": "accept_classification", "outcome": outcome,
            "expected_version": state["version"], "graph_version": state["graph_version"],
            "evidence_digest": state["evidence_digest"], "idempotency_key": key,
            "rationale": rationale, "details": {},
        }
        status, body, _ = self.request(
            "POST", "/api/claims/claim-1/step-up",
            body={**fields, "assertion": assertion}, cookie=cookie, csrf=csrf,
        )
        self.assertEqual(status, 200, body)
        step_up_id = json.loads(body)["step_up_id"]
        return fields, step_up_id

    def submit_operation(self, cookie, csrf, fields, step_up_id, *, headers=None):
        return self.request(
            "POST", "/api/claims/claim-1/decision",
            body={**fields, "step_up_id": step_up_id}, cookie=cookie,
            csrf=csrf, headers=headers,
        )

    def test_malformed_action_csrf_and_old_cookie_are_controlled(self):
        _, cookie, _, _ = self.login()
        _, csrf = self.csrf(cookie)
        fields, challenge = self.prepare_operation(cookie, csrf)
        for action in ([], {}):
            self.assertEqual(self.submit_operation(cookie, csrf, {**fields, "action": action}, challenge)[0], 400)
        self.assertEqual(self.submit_operation(cookie, "é", fields, challenge)[0], 403)
        self.assertEqual(self.login(prior_cookie=f"{OperatorHttpLab.SESSION_COOKIE}=é")[0], 204)
        self.assertEqual(self.contract.decision_count(), 0)
        self.assertEqual(self.csrf(cookie)[0], 200)

    def test_revoke_or_expiry_before_commit_guard_denies_paused_write(self):
        for expire in (False, True):
            with self.subTest(expire=expire):
                _, cookie, _, _ = self.login()
                _, csrf = self.csrf(cookie)
                fields, challenge = self.prepare_operation(cookie, csrf, key=f"paused-{expire}")
                entered, release = threading.Event(), threading.Event()
                original = self.backend.decide
                def paused(*args, **kwargs):
                    entered.set()
                    if not release.wait(3):
                        raise AuthorityUnavailable("test timeout")
                    return original(*args, **kwargs)
                self.backend.decide = paused
                try:
                    with ThreadPoolExecutor(max_workers=1) as pool:
                        pending = pool.submit(self.submit_operation, cookie, csrf, fields, challenge)
                        self.assertTrue(entered.wait(2))
                        if expire:
                            self.clock.advance(121)
                        else:
                            self.assertEqual(self.request("POST", "/api/session/revoke", body={}, cookie=cookie, csrf=csrf)[0], 204)
                        release.set()
                        self.assertEqual(pending.result(timeout=3)[0], 403)
                finally:
                    release.set()
                    self.backend.decide = original
                self.assertEqual(self.contract.decision_count(), 0)

    def test_invalid_outcome_is_client_error(self):
        _, cookie, _, _ = self.login()
        _, csrf = self.csrf(cookie)
        fields, challenge = self.prepare_operation(cookie, csrf)
        self.assertEqual(self.submit_operation(cookie, csrf, {**fields, "outcome": "nonsense"}, challenge)[0], 400)
        self.assertEqual(self.contract.decision_count(), 0)

    def test_loopback_cookie_origin_csp_and_sanitized_read_model(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        status, cookie, body, headers = self.login()
        self.assertEqual(status, 204)
        self.assertEqual(body, b"")
        self.assertIn("; Path=/", headers["Set-Cookie"])
        self.assertIn("; Secure", headers["Set-Cookie"])
        self.assertIn("; HttpOnly", headers["Set-Cookie"])
        self.assertIn("; SameSite=Strict", headers["Set-Cookie"])
        self.assertNotIn("Domain=", headers["Set-Cookie"])
        self.assertNotIn(cookie.split("=", 1)[1].encode(), body)
        self.assertEqual(headers["Content-Security-Policy"], "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        status, csrf = self.csrf(cookie)
        self.assertEqual(status, 200)
        status, body, read_headers = self.request("GET", "/api/claims/claim-1", cookie=cookie)
        self.assertEqual(status, 200)
        state = json.loads(body)
        self.assertEqual(set(state), {"claim_id", "semantic_kind", "effective_status", "version", "graph_version", "evidence_digest", "active_evidence_count"})
        self.assertNotIn("rationale", body.decode())
        self.assertNotIn("Access-Control-Allow-Origin", read_headers)

    def test_default_authentication_adapter_is_deny_all(self):
        closed = OperatorHttpLab(authority=self.backend, clock=self.clock.now)
        server, thread, base = self._start(closed)
        try:
            request = Request(
                base + "/__test__/session",
                data=b'{"assertion":"fictional-owner-assertion"}', method="POST",
                headers={"Host": self.HOST, "Origin": self.ORIGIN, "Content-Type": "application/json"},
            )
            with self.assertRaises(HTTPError) as error:
                urlopen(request, timeout=3)
            self.assertEqual(error.exception.code, 401)
            self.assertIsNone(error.exception.headers.get("Set-Cookie"))
            error.exception.close()
        finally:
            self._stop(server, thread)

    def test_invalid_assertion_or_unavailable_verifier_fails_closed(self):
        status, cookie, _, _ = self.login(assertion="not-a-test-assertion")
        self.assertEqual(status, 401)
        self.assertIsNone(cookie)
        self.authn.unavailable = True
        status, cookie, _, _ = self.login()
        self.assertEqual(status, 503)
        self.assertIsNone(cookie)
        self.assertEqual(self.contract.decision_count(), 0)

    def test_host_origin_and_csrf_are_required_for_state_changes(self):
        self.assertEqual(self.login(origin="https://attacker.example.test")[0], 403)
        self.assertEqual(self.login(host="attacker.example.test")[0], 400)
        _, cookie, _, _ = self.login()
        fields, step_up_id = self.prepare_operation(cookie, self.csrf(cookie)[1])
        status, _, _ = self.submit_operation(cookie, "wrong-csrf", fields, step_up_id)
        self.assertEqual(status, 403)
        status, _, _ = self.request(
            "POST", "/api/claims/claim-1/step-up", body={**fields, "assertion": "fictional-owner-stepup"},
            cookie=cookie, csrf=self.csrf(cookie)[1], origin=None,
        )
        self.assertEqual(status, 403)
        self.assertEqual(self.contract.decision_count(), 0)

    def test_cookie_duplication_and_cookie_without_server_session_do_not_authenticate(self):
        status, _, _ = self.request("GET", "/api/csrf", cookie=f"{OperatorHttpLab.SESSION_COOKIE}=invented")
        self.assertEqual(status, 401)
        status, _, _ = self.request(
            "GET", "/api/csrf",
            headers={"Cookie": f"{OperatorHttpLab.SESSION_COOKIE}=one; {OperatorHttpLab.SESSION_COOKIE}=two"},
        )
        self.assertEqual(status, 401)

    def test_session_rotation_invalidates_fixated_cookie(self):
        _, old_cookie, _, _ = self.login()
        _, new_cookie, _, _ = self.login(prior_cookie=old_cookie)
        self.assertNotEqual(old_cookie, new_cookie)
        self.assertEqual(self.csrf(old_cookie)[0], 401)
        self.assertEqual(self.csrf(new_cookie)[0], 200)

    def test_expired_and_revoked_sessions_cannot_read_or_write(self):
        _, expired_cookie, _, _ = self.login()
        self.clock.advance(121)
        self.assertEqual(self.csrf(expired_cookie)[0], 401)
        self.clock.value = 1000
        _, cookie, _, _ = self.login()
        _, csrf = self.csrf(cookie)
        status, _, headers = self.request(
            "POST", "/api/session/revoke", body={}, cookie=cookie, csrf=csrf,
        )
        self.assertEqual(status, 204)
        self.assertIn("Max-Age=0", headers["Set-Cookie"])
        self.assertEqual(self.csrf(cookie)[0], 401)

    def test_forged_roles_and_reverse_proxy_headers_never_grant_authority(self):
        status, _, _, _ = self.login(assertion="fictional-reader-assertion")
        self.assertEqual(status, 204)
        _, reader_cookie, _, _ = self.login(assertion="fictional-reader-assertion")
        _, csrf = self.csrf(reader_cookie)
        state = self.backend.read_claim("claim-1")
        fields = {
            "action": "accept_classification", "outcome": "other_or_unknown",
            "expected_version": state["version"], "graph_version": state["graph_version"],
            "evidence_digest": self.backend.graph_digest("claim-1"),
            "idempotency_key": "reader-forgery", "rationale": "synthetic probe", "details": {},
        }
        status, _, _ = self.request(
            "POST", "/api/claims/claim-1/step-up",
            body={**fields, "assertion": "fictional-reader-assertion"},
            cookie=reader_cookie, csrf=csrf,
        )
        self.assertEqual(status, 403)
        status, _, _ = self.submit_operation(
            reader_cookie, csrf, fields, "forged-challenge",
            headers={"X-Forwarded-User": "fictional-owner", "X-Forwarded-Role": "owner"},
        )
        self.assertEqual(status, 403)
        _, owner_cookie, _, _ = self.login()
        _, owner_csrf = self.csrf(owner_cookie)
        status, _, _ = self.request(
            "POST", "/api/claims/claim-1/decision",
            body={**fields, "step_up_id": "forged", "actor_id": "fictional-owner", "role": "owner", "is_owner": True},
            cookie=owner_cookie, csrf=owner_csrf,
        )
        self.assertEqual(status, 400)
        self.assertEqual(self.contract.decision_count(), 0)
        self.assertEqual(self.request("GET", "/api/claims/claim-1", headers={"X-Forwarded-User": "fictional-owner"})[0], 401)

    def test_step_up_reauth_requires_same_recent_identity(self):
        _, cookie, _, _ = self.login()
        _, csrf = self.csrf(cookie)
        fields, _ = self.prepare_operation(cookie, csrf)
        status, _, _ = self.request(
            "POST", "/api/claims/claim-1/step-up",
            body={**fields, "assertion": "fictional-reader-assertion"}, cookie=cookie, csrf=csrf,
        )
        self.assertEqual(status, 403)
        self.authn.identities["stale-owner-stepup"] = "fictional-owner"
        self.authn.auth_times["stale-owner-stepup"] = self.clock.now() - 61
        status, _, _ = self.request(
            "POST", "/api/claims/claim-1/step-up",
            body={**fields, "assertion": "stale-owner-stepup"}, cookie=cookie, csrf=csrf,
        )
        self.assertEqual(status, 401)

    def test_successful_decision_has_audit_and_exact_retry_is_idempotent(self):
        _, cookie, _, _ = self.login()
        _, csrf = self.csrf(cookie)
        fields, step_up_id = self.prepare_operation(cookie, csrf)
        forwarded = {"X-Forwarded-User": "forged-other-owner"}
        status, first_body, _ = self.submit_operation(cookie, csrf, fields, step_up_id, headers=forwarded)
        self.assertEqual(status, 200, first_body)
        first = json.loads(first_body)
        before = self.contract.decision_count()
        status, retry_body, _ = self.submit_operation(cookie, csrf, fields, step_up_id, headers=forwarded)
        self.assertEqual(status, 200, retry_body)
        self.assertEqual(json.loads(retry_body)["decision_id"], first["decision_id"])
        self.assertEqual(self.contract.decision_count(), before)
        audit = self.contract.audit_history(claim_id="claim-1")
        accepted = [row for row in audit if row["id"] == first["decision_id"]]
        self.assertEqual(len(accepted), 1)
        self.assertEqual(accepted[0]["actor_ref"], "fictional-owner")
        self.assertEqual(accepted[0]["outcome"], "other_or_unknown")

    def test_mutated_stepup_operation_and_replayed_challenge_are_denied(self):
        _, cookie, _, _ = self.login()
        _, csrf = self.csrf(cookie)
        fields, step_up_id = self.prepare_operation(cookie, csrf)
        changed = {**fields, "outcome": "publication_record"}
        status, _, _ = self.submit_operation(cookie, csrf, changed, step_up_id)
        self.assertEqual(status, 403)
        status, _, _ = self.submit_operation(cookie, csrf, fields, step_up_id)
        self.assertEqual(status, 200)
        different_key = {**fields, "idempotency_key": "new-key"}
        status, _, _ = self.submit_operation(cookie, csrf, different_key, step_up_id)
        self.assertEqual(status, 403)
        self.assertEqual(self.contract.decision_count(), 1)

    def test_stale_graph_stepup_returns_conflict_without_audit(self):
        _, cookie, _, _ = self.login()
        _, csrf = self.csrf(cookie)
        fields, step_up_id = self.prepare_operation(cookie, csrf)
        self.contract.add_lineage_edge(
            claim_id="claim-1", parent_record_id="record-a", child_record_id="record-b",
            relation="cites", basis="synthetic graph changed after step-up",
        )
        status, _, _ = self.submit_operation(cookie, csrf, fields, step_up_id)
        self.assertEqual(status, 409)
        self.assertEqual(self.contract.decision_count(), 0)

    def test_same_idempotent_retry_returns_historical_id_but_current_view_is_separate(self):
        _, cookie, _, _ = self.login()
        _, csrf = self.csrf(cookie)
        fields, step_up_id = self.prepare_operation(cookie, csrf)
        status, body, _ = self.submit_operation(cookie, csrf, fields, step_up_id)
        self.assertEqual(status, 200)
        first = json.loads(body)
        self.contract.add_lineage_edge(
            claim_id="claim-1", parent_record_id="record-a", child_record_id="record-b",
            relation="derived_from", basis="later synthetic graph revision",
        )
        status, replay_body, _ = self.submit_operation(cookie, csrf, fields, step_up_id)
        self.assertEqual(status, 200)
        replay = json.loads(replay_body)
        self.assertEqual(replay["decision_id"], first["decision_id"])
        self.assertGreater(replay["current_claim"]["graph_version"], fields["graph_version"])
        self.assertEqual(self.contract.decision_count(), 1)

    def test_two_competing_http_decisions_have_one_winner(self):
        _, cookie, _, _ = self.login()
        _, csrf = self.csrf(cookie)
        fields_a, challenge_a = self.prepare_operation(cookie, csrf, outcome="publication_record", key="race-a")
        fields_b, challenge_b = self.prepare_operation(cookie, csrf, outcome="other_or_unknown", key="race-b")
        barrier = threading.Barrier(3)

        def send(fields, challenge):
            barrier.wait()
            return self.submit_operation(cookie, csrf, fields, challenge)[0]

        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(send, fields_a, challenge_a)
            b = pool.submit(send, fields_b, challenge_b)
            barrier.wait()
            statuses = sorted((a.result(timeout=5), b.result(timeout=5)))
        self.assertEqual(statuses, [200, 409])
        self.assertEqual(self.contract.decision_count(), 1)

    def test_origin_host_method_headers_and_body_limits_fail_closed(self):
        _, cookie, _, _ = self.login()
        _, csrf = self.csrf(cookie)
        status, _, _ = self.request("GET", "/api/claims/claim-1/decision", cookie=cookie)
        self.assertEqual(status, 405)
        status, _, headers = self.request("OPTIONS", "/api/claims/claim-1/decision", cookie=cookie)
        self.assertEqual(status, 405)
        self.assertEqual(headers.get("Allow"), "GET, POST")
        status, _, _ = self.request(
            "POST", "/__test__/session", raw=b'{"assertion":"bad","assertion":"bad"}',
        )
        self.assertEqual(status, 400)
        status, _, _ = self.request(
            "POST", "/__test__/session", raw=b"x" * 9000,
        )
        self.assertEqual(status, 413)
        status, _, _ = self.request(
            "POST", "/api/session/revoke", body={}, cookie=cookie, csrf=csrf,
            headers={"Transfer-Encoding": "chunked"},
        )
        self.assertEqual(status, 400)
        self.assertEqual(self.request("POST", "/api/recovery", body={}, cookie=cookie, csrf=csrf)[0], 404)
        self.assertEqual(self.contract.decision_count(), 0)

    def test_authority_and_audit_outage_never_fall_back_to_write(self):
        _, cookie, _, _ = self.login()
        _, csrf = self.csrf(cookie)
        self.backend.available = False
        status, _, _ = self.request("GET", "/api/claims/claim-1", cookie=cookie)
        self.assertEqual(status, 503)
        self.backend.available = True
        fields, step_up_id = self.prepare_operation(cookie, csrf)
        self.backend.available = False
        status, _, _ = self.submit_operation(cookie, csrf, fields, step_up_id)
        self.assertEqual(status, 503)
        self.assertEqual(self.contract.decision_count(), 0)

    def test_no_html_rationale_reflection_and_security_headers_are_present(self):
        _, cookie, _, _ = self.login()
        _, csrf = self.csrf(cookie)
        rationale = "<script>fictional XSS probe</script>"
        fields, step_up_id = self.prepare_operation(cookie, csrf, rationale=rationale)
        status, _, _ = self.submit_operation(cookie, csrf, fields, step_up_id)
        self.assertEqual(status, 200)
        status, body, headers = self.request("GET", "/api/claims/claim-1", cookie=cookie)
        self.assertEqual(status, 200)
        self.assertNotIn("fictional XSS probe", body.decode())
        self.assertEqual(headers["Content-Type"], "application/json; charset=utf-8")
        self.assertIn("default-src 'none'", headers["Content-Security-Policy"])


if __name__ == "__main__":
    unittest.main()
