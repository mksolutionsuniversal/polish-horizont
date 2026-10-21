"""In-memory synthetic authorization and claim-lineage contract.

This is a test harness, not authentication, cryptography, or a deployable
authorization boundary. Its only database is created internally as :memory:.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import sqlite3
import threading
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit


class ContractDenied(PermissionError):
    """The synthetic policy denied an action."""


class ContractConflict(RuntimeError):
    """The expected state version is no longer current."""


class ContractError(ValueError):
    """Invalid synthetic fixture or request."""


@dataclass
class FakeClock:
    """Deterministic fixture clock; values are test seconds, not wall time."""

    value: int = 1_000

    def now(self) -> int:
        return self.value

    def advance(self, seconds: int) -> None:
        if seconds < 0:
            raise ValueError("fake time cannot move backwards")
        self.value += seconds


@dataclass(frozen=True)
class FakeTrustedAuthContext:
    """Injected test context; it proves nothing outside this in-process harness."""

    session_id: str
    actor_ref: str
    claimed_role: str | None = None  # untrusted assertion, never an authority source


class OperatorLineageContract:
    """Synthetic single-process model using a private in-memory SQLite database."""

    ROLES = {"owner", "ai_readonly", "collector", "anonymous"}
    OWNER_ACTIONS = {"accept_classification", "accept_independence", "validate_incident"}
    CLASSIFICATIONS = {"incident_hypothesis", "publication_record", "other_or_unknown"}
    VALIDATIONS = {"pending", "valid", "invalid", "review_required"}
    EDGE_TYPES = {
        "cites", "syndicated_from", "translated_from", "copied_from",
        "same_official_release", "derived_from", "separately_reported",
        "editorially_controlled_by", "corrects", "retracts",
    }
    DEPENDENCY_EDGES = EDGE_TYPES - {"separately_reported"}
    POLICY_VERSION = "synthetic-policy-v1"

    def __init__(self, *, clock: FakeClock | None = None, max_nodes: int = 64, max_depth: int = 12):
        if max_nodes < 2 or max_depth < 1:
            raise ValueError("synthetic graph bounds must be positive")
        self.clock = clock or FakeClock()
        self.max_nodes = max_nodes
        self.max_depth = max_depth
        self._lock = threading.RLock()
        self._counter = 0
        self._db = sqlite3.connect(":memory:", isolation_level=None, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys=ON")
        if self._db.execute("PRAGMA database_list").fetchone()[2] != "":
            raise RuntimeError("synthetic contract refuses file-backed SQLite")
        self._db.executescript(
            """
            CREATE TABLE sessions(
                session_id TEXT PRIMARY KEY, actor_ref TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('owner','ai_readonly','collector','anonymous')),
                expires_at INTEGER NOT NULL, revoked_at INTEGER,
                policy_version TEXT NOT NULL, claim_scope TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE claims(
                claim_id TEXT PRIMARY KEY,
                semantic_kind TEXT NOT NULL CHECK(semantic_kind IN ('incident_hypothesis','publication_record','other_or_unknown')),
                effective_status TEXT NOT NULL CHECK(effective_status IN ('pending','valid','invalid','review_required')),
                legacy_v2_status TEXT NOT NULL CHECK(legacy_v2_status IN ('pending','valid','invalid')),
                version INTEGER NOT NULL DEFAULT 0,
                graph_version INTEGER NOT NULL DEFAULT 0,
                current_decision_id INTEGER,
                FOREIGN KEY(current_decision_id) REFERENCES decision_history(id)
            );
            CREATE TABLE source_records(
                record_id TEXT PRIMARY KEY, source_record_id TEXT NOT NULL,
                observation_id TEXT NOT NULL, canonical_url TEXT NOT NULL,
                publisher_ref TEXT, record_kind TEXT NOT NULL
            );
            CREATE TABLE claim_records(
                claim_id TEXT NOT NULL REFERENCES claims(claim_id),
                record_id TEXT NOT NULL REFERENCES source_records(record_id),
                PRIMARY KEY(claim_id,record_id)
            );
            CREATE TABLE lineage_edges(
                id INTEGER PRIMARY KEY, claim_id TEXT NOT NULL REFERENCES claims(claim_id),
                parent_record_id TEXT NOT NULL REFERENCES source_records(record_id),
                child_record_id TEXT NOT NULL REFERENCES source_records(record_id),
                relation TEXT NOT NULL, basis TEXT NOT NULL, graph_version INTEGER NOT NULL,
                created_at INTEGER NOT NULL
            );
            CREATE TABLE stepups(
                challenge_id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(session_id),
                actor_ref TEXT NOT NULL, action TEXT NOT NULL, claim_id TEXT NOT NULL,
                outcome TEXT NOT NULL, request_digest TEXT NOT NULL,
                expected_version INTEGER NOT NULL, graph_version INTEGER NOT NULL,
                policy_version TEXT NOT NULL, expires_at INTEGER NOT NULL,
                consumed_at INTEGER
            );
            CREATE TABLE decision_history(
                id INTEGER PRIMARY KEY, idempotency_key TEXT NOT NULL UNIQUE,
                request_digest TEXT NOT NULL, actor_ref TEXT NOT NULL, actor_role TEXT NOT NULL,
                action TEXT NOT NULL, claim_id TEXT NOT NULL, outcome TEXT NOT NULL,
                prior_version INTEGER NOT NULL, resulting_version INTEGER NOT NULL,
                graph_version INTEGER NOT NULL, policy_version TEXT NOT NULL,
                rationale TEXT NOT NULL, details TEXT NOT NULL, created_at INTEGER NOT NULL,
                supersedes_id INTEGER REFERENCES decision_history(id)
            );
            CREATE TABLE lineage_reviews(
                id INTEGER PRIMARY KEY,
                decision_id INTEGER NOT NULL REFERENCES decision_history(id),
                claim_id TEXT NOT NULL REFERENCES claims(claim_id),
                record_a TEXT NOT NULL, record_b TEXT NOT NULL,
                outcome TEXT NOT NULL CHECK(outcome IN ('independent','dependent','unknown','disputed','revoked')),
                graph_version INTEGER NOT NULL,
                FOREIGN KEY(claim_id,record_a) REFERENCES claim_records(claim_id,record_id),
                FOREIGN KEY(claim_id,record_b) REFERENCES claim_records(claim_id,record_id)
            );
            CREATE TABLE proposals(
                id INTEGER PRIMARY KEY, actor_ref TEXT NOT NULL, actor_role TEXT NOT NULL,
                claim_id TEXT NOT NULL REFERENCES claims(claim_id), proposal_type TEXT NOT NULL,
                body TEXT NOT NULL, created_at INTEGER NOT NULL
            );
            CREATE TRIGGER decision_history_no_update BEFORE UPDATE ON decision_history
            BEGIN SELECT RAISE(ABORT,'decision history is append-only'); END;
            CREATE TRIGGER decision_history_no_delete BEFORE DELETE ON decision_history
            BEGIN SELECT RAISE(ABORT,'decision history is append-only'); END;
            CREATE TRIGGER lineage_reviews_no_update BEFORE UPDATE ON lineage_reviews
            BEGIN SELECT RAISE(ABORT,'lineage reviews are append-only'); END;
            CREATE TRIGGER lineage_reviews_no_delete BEFORE DELETE ON lineage_reviews
            BEGIN SELECT RAISE(ABORT,'lineage reviews are append-only'); END;
            """
        )

    def close(self) -> None:
        self._db.close()

    @contextmanager
    def _transaction(self):
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield
            except BaseException:
                self._db.rollback()
                raise
            else:
                self._db.commit()

    def _next(self, prefix: str) -> str:
        self._counter += 1
        return f"{prefix}-{self._counter:06d}"

    def issue_test_session(
        self, *, actor_ref: str, role: str, ttl: int = 100,
        claim_scope: Iterable[str] = ("*",), policy_version: str = POLICY_VERSION,
    ) -> FakeTrustedAuthContext:
        """Fixture-only session injector; not a login or credential issuer."""
        if role not in self.ROLES or not actor_ref or ttl <= 0:
            raise ContractError("invalid synthetic session fixture")
        session_id = self._next("session")
        scope = sorted(set(claim_scope))
        with self._transaction():
            self._db.execute(
                "INSERT INTO sessions(session_id,actor_ref,role,expires_at,policy_version,claim_scope) VALUES(?,?,?,?,?,?)",
                (session_id, actor_ref, role, self.clock.now() + ttl, policy_version, json.dumps(scope)),
            )
        return FakeTrustedAuthContext(session_id, actor_ref)

    def _session(self, context: FakeTrustedAuthContext, *, owner: bool = False, claim_id: str | None = None) -> sqlite3.Row:
        if type(context) is not FakeTrustedAuthContext:
            raise ContractDenied("untrusted auth context")
        row = self._db.execute("SELECT * FROM sessions WHERE session_id=?", (context.session_id,)).fetchone()
        if row is None or row["actor_ref"] != context.actor_ref:
            raise ContractDenied("unknown or forged actor session")
        if context.claimed_role is not None and context.claimed_role != row["role"]:
            raise ContractDenied("caller-supplied role does not match server-side role")
        if row["revoked_at"] is not None or self.clock.now() >= row["expires_at"]:
            raise ContractDenied("session is expired or revoked")
        if row["policy_version"] != self.POLICY_VERSION:
            raise ContractDenied("session policy is stale")
        if owner and row["role"] != "owner":
            raise ContractDenied("owner authority required")
        if claim_id is not None:
            scope = json.loads(row["claim_scope"])
            if "*" not in scope and claim_id not in scope:
                raise ContractDenied("claim is outside session scope")
        return row

    def create_claim(self, claim_id: str, *, semantic_kind: str = "incident_hypothesis", legacy_v2_status: str = "pending") -> None:
        if not claim_id or semantic_kind not in self.CLASSIFICATIONS or legacy_v2_status not in {"pending", "valid", "invalid"}:
            raise ContractError("invalid synthetic claim")
        with self._transaction():
            self._db.execute(
                "INSERT INTO claims(claim_id,semantic_kind,effective_status,legacy_v2_status) VALUES(?,?,'pending',?)",
                (claim_id, semantic_kind, legacy_v2_status),
            )

    def add_synthetic_record(
        self, *, claim_id: str, record_id: str, source_record_id: str,
        observation_id: str, canonical_url: str, record_kind: str = "publication",
        publisher_ref: str | None = None,
    ) -> None:
        parts = urlsplit(canonical_url)
        if not record_id or not source_record_id or not observation_id:
            raise ContractError("synthetic record identifiers are required")
        if parts.scheme != "https" or parts.hostname != "example.test":
            raise ContractError("synthetic URLs must use https://example.test")
        if record_kind not in {"publication", "provider_observation", "official_announcement", "agency_copy", "correction"}:
            raise ContractError("invalid synthetic record kind")
        with self._transaction():
            self._db.execute(
                "INSERT INTO source_records VALUES(?,?,?,?,?,?)",
                (record_id, source_record_id, observation_id, canonical_url, publisher_ref, record_kind),
            )
            self._db.execute("INSERT INTO claim_records VALUES(?,?)", (claim_id, record_id))
            self._graph_changed(claim_id, reason="new source record added")

    def add_lineage_edge(
        self, *, claim_id: str, parent_record_id: str, child_record_id: str,
        relation: str, basis: str,
    ) -> None:
        if relation not in self.EDGE_TYPES or not basis.strip():
            raise ContractError("lineage relation and synthetic trace basis are required")
        if parent_record_id == child_record_id:
            raise ContractError("self edges are not valid lineage facts")
        with self._transaction():
            claim = self._claim(claim_id)
            membership = self._db.execute(
                "SELECT COUNT(*) FROM claim_records WHERE claim_id=? AND record_id IN (?,?)",
                (claim_id, parent_record_id, child_record_id),
            ).fetchone()[0]
            if membership != 2:
                raise ContractError("lineage edge endpoints must belong to the named claim")
            version = claim["graph_version"] + 1
            self._db.execute(
                "INSERT INTO lineage_edges(claim_id,parent_record_id,child_record_id,relation,basis,graph_version,created_at) VALUES(?,?,?,?,?,?,?)",
                (claim_id, parent_record_id, child_record_id, relation, basis.strip(), version, self.clock.now()),
            )
            self._db.execute("UPDATE claims SET graph_version=? WHERE claim_id=?", (version, claim_id))
            self._graph_changed(claim_id, reason=f"lineage edge added: {relation}", graph_already_bumped=True)

    def _claim(self, claim_id: str) -> sqlite3.Row:
        row = self._db.execute("SELECT * FROM claims WHERE claim_id=?", (claim_id,)).fetchone()
        if row is None:
            raise ContractError("unknown synthetic claim")
        return row

    def graph_version(self, claim_id: str) -> int:
        return int(self._claim(claim_id)["graph_version"])

    @staticmethod
    def _digest(value: Any) -> str:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def graph_digest(self, claim_id: str) -> str:
        nodes = [tuple(row) for row in self._db.execute(
            """SELECT r.record_id,r.source_record_id,r.observation_id,r.canonical_url,r.publisher_ref,r.record_kind
               FROM source_records r JOIN claim_records c ON c.record_id=r.record_id
               WHERE c.claim_id=? ORDER BY r.record_id""", (claim_id,),
        )]
        edges = [tuple(row) for row in self._db.execute(
            "SELECT parent_record_id,child_record_id,relation,basis,graph_version FROM lineage_edges WHERE claim_id=? ORDER BY id",
            (claim_id,),
        )]
        return self._digest({"claim_id": claim_id, "nodes": nodes, "edges": edges})

    def independence_details(
        self, claim_id: str, record_a: str, record_b: str, *,
        reporting_basis_a: str = "", reporting_basis_b: str = "",
        owner_attests_distinct_origins: bool = False,
    ) -> dict[str, Any]:
        pair = tuple(sorted((record_a, record_b)))
        return {
            "record_a": pair[0], "record_b": pair[1],
            "graph_digest": self.graph_digest(claim_id),
            "assessment": self.pair_assessment(claim_id, pair[0], pair[1]),
            "reporting_basis_a": reporting_basis_a.strip(),
            "reporting_basis_b": reporting_basis_b.strip(),
            "owner_attests_distinct_origins": bool(owner_attests_distinct_origins),
        }

    def pair_assessment(self, claim_id: str, record_a: str, record_b: str) -> str:
        """Return dependent, unknown, or a structural candidate (never accepted)."""
        claim = self._claim(claim_id)
        rows = self._db.execute(
            """SELECT r.* FROM source_records r JOIN claim_records c ON c.record_id=r.record_id
               WHERE c.claim_id=? AND r.record_id IN (?,?)""", (claim_id, record_a, record_b),
        ).fetchall()
        if len(rows) != 2:
            return "unknown"
        a, b = rows
        if a["record_id"] == b["record_id"]:
            return "dependent"
        if a["observation_id"] == b["observation_id"]:
            return "dependent"
        if a["source_record_id"] == b["source_record_id"]:
            return "dependent"
        if a["canonical_url"] == b["canonical_url"]:
            return "dependent"
        nodes = [row[0] for row in self._db.execute(
            "SELECT record_id FROM claim_records WHERE claim_id=? ORDER BY record_id", (claim_id,),
        )]
        if len(nodes) > self.max_nodes:
            return "unknown"
        edges = self._db.execute(
            """SELECT parent_record_id,child_record_id FROM lineage_edges
               WHERE claim_id=? AND relation IN (%s)""" % ",".join("?" * len(self.DEPENDENCY_EDGES)),
            (claim_id, *sorted(self.DEPENDENCY_EDGES)),
        ).fetchall()
        graph: dict[str, set[str]] = {node: set() for node in nodes}
        directed: dict[str, set[str]] = {node: set() for node in nodes}
        for edge in edges:
            parent, child = edge
            if parent not in graph or child not in graph:
                return "unknown"
            graph[parent].add(child)
            graph[child].add(parent)
            directed[parent].add(child)
        if self._has_cycle(directed):
            return "unknown"
        pending = [(a["record_id"], 0)]
        visited = {a["record_id"]}
        while pending:
            node, depth = pending.pop(0)
            if node == b["record_id"]:
                return "dependent"
            if depth >= self.max_depth and graph[node] - visited:
                return "unknown"
            for neighbor in graph[node] - visited:
                visited.add(neighbor)
                pending.append((neighbor, depth + 1))
        return "candidate"

    @staticmethod
    def _has_cycle(graph: Mapping[str, set[str]]) -> bool:
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node: str) -> bool:
            if node in visiting:
                return True
            if node in visited:
                return False
            visiting.add(node)
            for child in graph[node]:
                if visit(child):
                    return True
            visiting.remove(node)
            visited.add(node)
            return False

        return any(visit(node) for node in graph if node not in visited)

    def issue_step_up(
        self, context: FakeTrustedAuthContext, *, action: str, claim_id: str,
        outcome: str, expected_version: int, details: Mapping[str, Any] | None = None,
        graph_version: int | None = None, ttl: int = 10,
    ) -> str:
        """Inject a synthetic one-use confirmation marker bound to one operation."""
        with self._lock:
            session = self._session(context, owner=True, claim_id=None if action == "revoke" else claim_id)
            if action not in self.OWNER_ACTIONS | {"revoke"} or ttl <= 0:
                raise ContractDenied("action cannot receive a synthetic step-up")
            if action == "revoke":
                if not claim_id.startswith("session:"):
                    raise ContractError("revoke challenge must name a session")
                target = self._db.execute("SELECT version FROM sessions WHERE session_id=?", (claim_id[8:],)).fetchone()
                if target is None:
                    raise ContractError("unknown synthetic session")
                current_graph = 0
            else:
                claim = self._claim(claim_id)
                if expected_version != claim["version"]:
                    raise ContractConflict("claim changed before step-up")
                current_graph = claim["graph_version"]
            if graph_version is not None and graph_version != current_graph:
                raise ContractConflict("lineage graph changed before step-up")
            graph_version = current_graph
            challenge_id = self._next("stepup")
            with self._transaction():
                self._db.execute(
                    """INSERT INTO stepups VALUES(?,?,?,?,?,?,?,?,?,?,?,NULL)""",
                    (challenge_id, context.session_id, session["actor_ref"], action, claim_id, outcome,
                     self._digest(details or {}), expected_version, graph_version,
                     session["policy_version"], self.clock.now() + ttl),
                )
            return challenge_id

    def propose(self, context: FakeTrustedAuthContext, *, claim_id: str, proposal_type: str, body: str) -> int:
        with self._transaction():
            session = self._session(context, claim_id=claim_id)
            if session["role"] not in {"ai_readonly", "collector"}:
                raise ContractDenied("only synthetic AI/collector roles may submit proposals here")
            if not body.strip():
                raise ContractError("proposal body is required")
            cur = self._db.execute(
                "INSERT INTO proposals(actor_ref,actor_role,claim_id,proposal_type,body,created_at) VALUES(?,?,?,?,?,?)",
                (session["actor_ref"], session["role"], claim_id, proposal_type, body.strip(), self.clock.now()),
            )
            return int(cur.lastrowid)

    def decide(
        self, context: FakeTrustedAuthContext, *, action: str, claim_id: str,
        outcome: str, expected_version: int, step_up_id: str | None,
        idempotency_key: str, rationale: str, details: Mapping[str, Any] | None = None,
        graph_version: int | None = None, fail_after_audit: bool = False,
    ) -> int:
        if action not in {"accept_classification", "validate_incident"}:
            raise ContractDenied("use the dedicated method for this decision type")
        return self._decide_claim(
            context, action=action, claim_id=claim_id, outcome=outcome,
            expected_version=expected_version, step_up_id=step_up_id,
            idempotency_key=idempotency_key, rationale=rationale, details=details or {},
            graph_version=graph_version, fail_after_audit=fail_after_audit,
        )

    def _decide_claim(
        self, context: FakeTrustedAuthContext, *, action: str, claim_id: str,
        outcome: str, expected_version: int, step_up_id: str | None,
        idempotency_key: str, rationale: str, details: Mapping[str, Any],
        graph_version: int | None = None, fail_after_audit: bool = False,
        pair: tuple[str, str] | None = None,
    ) -> int:
        if not idempotency_key or not rationale.strip():
            raise ContractError("idempotency key and rationale are required")
        with self._transaction():
            session = self._session(context, owner=True, claim_id=claim_id)
            claim = self._claim(claim_id)
            current_graph = int(claim["graph_version"])
            request_payload = {
                "actor": session["actor_ref"], "action": action, "claim": claim_id,
                "outcome": outcome, "expected_version": expected_version,
                "graph_version": current_graph, "policy_version": session["policy_version"],
                "details": details, "rationale": rationale.strip(),
            }
            request_digest = self._digest(request_payload)
            existing = self._db.execute(
                "SELECT id,actor_ref,request_digest FROM decision_history WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
            if existing:
                if existing["actor_ref"] == session["actor_ref"] and existing["request_digest"] == request_digest:
                    return int(existing["id"])
                raise ContractDenied("idempotency key was reused with different content or actor")
            if claim["version"] != expected_version:
                raise ContractConflict("claim version is stale")
            if graph_version is not None and graph_version != current_graph:
                raise ContractConflict("lineage graph version is stale")
            self._consume_step_up(
                session, step_up_id, action=action, claim_id=claim_id, outcome=outcome,
                expected_version=expected_version, graph_version=current_graph, details=details,
            )
            if action == "accept_classification":
                if outcome not in self.CLASSIFICATIONS:
                    raise ContractError("invalid classification")
                if claim["effective_status"] == "valid" and outcome != "incident_hypothesis":
                    raise ContractDenied("a valid incident requires a separate audited review before reclassification")
            elif action == "validate_incident":
                if outcome not in {"valid", "invalid", "pending"}:
                    raise ContractError("invalid validation outcome")
                if claim["semantic_kind"] != "incident_hypothesis":
                    raise ContractDenied("only an incident hypothesis can be validated")
                if outcome == "valid" and not self.active_lineage(claim_id):
                    raise ContractDenied("valid requires current accepted independent evidence")
            supersedes = claim["current_decision_id"]
            cur = self._db.execute(
                """INSERT INTO decision_history(idempotency_key,request_digest,actor_ref,actor_role,action,claim_id,
                   outcome,prior_version,resulting_version,graph_version,policy_version,rationale,details,created_at,supersedes_id)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (idempotency_key, request_digest, session["actor_ref"], session["role"], action,
                 claim_id, outcome, expected_version, expected_version + 1, current_graph,
                 session["policy_version"], rationale.strip(), json.dumps(details, sort_keys=True),
                 self.clock.now(), supersedes),
            )
            decision_id = int(cur.lastrowid)
            if fail_after_audit:
                raise RuntimeError("injected synthetic failure after audit append")
            semantic_kind = outcome if action == "accept_classification" else claim["semantic_kind"]
            effective_status = outcome if action == "validate_incident" else claim["effective_status"]
            self._db.execute(
                """UPDATE claims SET semantic_kind=?,effective_status=?,version=version+1,current_decision_id=?
                   WHERE claim_id=? AND version=?""",
                (semantic_kind, effective_status, decision_id, claim_id, expected_version),
            )
            if self._db.execute("SELECT changes()").fetchone()[0] != 1:
                raise ContractConflict("compare-and-append failed")
            if pair is not None:
                self._db.execute(
                    "INSERT INTO lineage_reviews(decision_id,claim_id,record_a,record_b,outcome,graph_version) VALUES(?,?,?,?,?,?)",
                    (decision_id, claim_id, pair[0], pair[1], outcome, current_graph),
                )
                if outcome == "revoked" and claim["effective_status"] == "valid":
                    if not self.active_lineage(claim_id):
                        self._db.execute(
                            "UPDATE claims SET effective_status='review_required' WHERE claim_id=?",
                            (claim_id,),
                        )
            return decision_id

    def _consume_step_up(
        self, session: sqlite3.Row, challenge_id: str | None, *, action: str,
        claim_id: str, outcome: str, expected_version: int, graph_version: int,
        details: Mapping[str, Any],
    ) -> None:
        if not challenge_id:
            raise ContractDenied("one-time step-up is required")
        row = self._db.execute("SELECT * FROM stepups WHERE challenge_id=?", (challenge_id,)).fetchone()
        if row is None or row["session_id"] != session["session_id"] or row["actor_ref"] != session["actor_ref"]:
            raise ContractDenied("step-up is not bound to this session")
        if row["consumed_at"] is not None:
            raise ContractDenied("step-up has already been used")
        if self.clock.now() >= row["expires_at"]:
            raise ContractDenied("step-up has expired")
        if (row["action"], row["claim_id"], row["outcome"], row["expected_version"],
                row["graph_version"], row["policy_version"], row["request_digest"]) != (
                action, claim_id, outcome, expected_version, graph_version,
                session["policy_version"], self._digest(details)):
            raise ContractDenied("step-up does not match this exact operation")
        self._db.execute("UPDATE stepups SET consumed_at=? WHERE challenge_id=? AND consumed_at IS NULL", (self.clock.now(), challenge_id))

    def review_independence(
        self, context: FakeTrustedAuthContext, *, claim_id: str, record_a: str,
        record_b: str, outcome: str, reporting_basis_a: str = "",
        reporting_basis_b: str = "", owner_attests_distinct_origins: bool = False,
        expected_version: int, step_up_id: str | None, idempotency_key: str,
        rationale: str,
    ) -> int:
        if record_a == record_b:
            raise ContractDenied("independence requires two distinct evidence records")
        pair = tuple(sorted((record_a, record_b)))
        assessment = self.pair_assessment(claim_id, pair[0], pair[1])
        if outcome == "independent":
            if assessment != "candidate":
                raise ContractDenied(f"lineage is {assessment}; independence fails closed")
            if not (reporting_basis_a.strip() and reporting_basis_b.strip() and owner_attests_distinct_origins):
                raise ContractDenied("positive outcome requires explicit owner attestation and two traceable reporting bases")
        elif outcome not in {"dependent", "unknown", "disputed", "revoked"}:
            raise ContractError("invalid lineage decision")
        details = self.independence_details(
            claim_id, pair[0], pair[1], reporting_basis_a=reporting_basis_a,
            reporting_basis_b=reporting_basis_b,
            owner_attests_distinct_origins=owner_attests_distinct_origins,
        )
        return self._decide_claim(
            context, action="accept_independence", claim_id=claim_id, outcome=outcome,
            expected_version=expected_version, step_up_id=step_up_id,
            idempotency_key=idempotency_key, rationale=rationale, details=details,
            graph_version=self.graph_version(claim_id), pair=pair,
        )

    def revoke_session(
        self, context: FakeTrustedAuthContext, *, target_session_id: str,
        step_up_id: str | None, expected_version: int, idempotency_key: str,
        rationale: str,
    ) -> int:
        if not rationale.strip() or not idempotency_key:
            raise ContractError("rationale and idempotency key are required")
        resource = f"session:{target_session_id}"
        details = {"target_session_id": target_session_id}
        with self._transaction():
            actor = self._session(context, owner=True)
            payload = {"actor": actor["actor_ref"], "action": "revoke", "claim": resource,
                       "outcome": "revoked", "prior_version": expected_version,
                       "policy": actor["policy_version"], "details": details,
                       "rationale": rationale.strip()}
            digest = self._digest(payload)
            existing = self._db.execute("SELECT id,actor_ref,request_digest FROM decision_history WHERE idempotency_key=?", (idempotency_key,)).fetchone()
            if existing:
                if existing["actor_ref"] == actor["actor_ref"] and existing["request_digest"] == digest:
                    return int(existing["id"])
                raise ContractDenied("idempotency key replay mismatch")
            target = self._db.execute("SELECT * FROM sessions WHERE session_id=?", (target_session_id,)).fetchone()
            if target is None or target["version"] != expected_version:
                raise ContractConflict("target session is missing or changed")
            self._consume_step_up(session=actor, challenge_id=step_up_id, action="revoke",
                                  claim_id=resource, outcome="revoked", expected_version=expected_version,
                                  graph_version=0, details=details)
            cur = self._db.execute(
                """INSERT INTO decision_history(idempotency_key,request_digest,actor_ref,actor_role,action,claim_id,
                   outcome,prior_version,resulting_version,graph_version,policy_version,rationale,details,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (idempotency_key,digest,actor["actor_ref"],actor["role"],"revoke",resource,"revoked",
                 expected_version,expected_version+1,0,actor["policy_version"],rationale.strip(),
                 json.dumps(details,sort_keys=True),self.clock.now()),
            )
            decision_id = int(cur.lastrowid)
            self._db.execute("UPDATE sessions SET revoked_at=?,version=version+1 WHERE session_id=? AND version=?",
                             (self.clock.now(),target_session_id,expected_version))
            if self._db.execute("SELECT changes()").fetchone()[0] != 1:
                raise ContractConflict("session revocation compare-and-append failed")
            return decision_id

    def _graph_changed(self, claim_id: str, *, reason: str, graph_already_bumped: bool = False) -> None:
        claim = self._claim(claim_id)
        if not graph_already_bumped:
            new_graph = claim["graph_version"] + 1
            self._db.execute("UPDATE claims SET graph_version=? WHERE claim_id=?", (new_graph, claim_id))
        else:
            new_graph = claim["graph_version"]
        latest = self._db.execute(
            """SELECT lr.* FROM lineage_reviews lr WHERE lr.claim_id=?
               AND lr.id=(SELECT MAX(x.id) FROM lineage_reviews x
                          WHERE x.claim_id=lr.claim_id AND x.record_a=lr.record_a AND x.record_b=lr.record_b)
               AND lr.outcome='independent'""", (claim_id,),
        ).fetchall()
        status = "review_required" if claim["effective_status"] == "valid" and latest else claim["effective_status"]
        if status == "review_required":
            self._db.execute("UPDATE claims SET effective_status=? WHERE claim_id=?", (status, claim_id))
        version = int(claim["version"])
        for review in latest:
            system_key = f"lineage-change:{claim_id}:{new_graph}:{review['record_a']}:{review['record_b']}"
            detail = {"reason": reason, "previous_outcome": "independent", "new_outcome": "unknown"}
            cur = self._db.execute(
                """INSERT INTO decision_history(idempotency_key,request_digest,actor_ref,actor_role,action,claim_id,
                   outcome,prior_version,resulting_version,graph_version,policy_version,rationale,details,created_at,supersedes_id)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (system_key,self._digest(detail),"synthetic-lineage-monitor","system","lineage_invalidated",
                 claim_id,"unknown",version,version+1,new_graph,self.POLICY_VERSION,
                 reason,json.dumps(detail,sort_keys=True),self.clock.now(),review["decision_id"]),
            )
            decision_id = int(cur.lastrowid)
            version += 1
            self._db.execute(
                "UPDATE claims SET version=?,current_decision_id=? WHERE claim_id=?",
                (version,decision_id,claim_id),
            )
            self._db.execute(
                "INSERT INTO lineage_reviews(decision_id,claim_id,record_a,record_b,outcome,graph_version) VALUES(?,?,?,?,'unknown',?)",
                (decision_id,claim_id,review["record_a"],review["record_b"],new_graph),
            )

    def active_lineage(self, claim_id: str) -> list[dict[str, Any]]:
        claim = self._claim(claim_id)
        if claim["semantic_kind"] != "incident_hypothesis":
            return []
        latest = self._db.execute(
            """SELECT lr.* FROM lineage_reviews lr WHERE lr.claim_id=?
               AND lr.id=(SELECT MAX(x.id) FROM lineage_reviews x
                          WHERE x.claim_id=lr.claim_id AND x.record_a=lr.record_a AND x.record_b=lr.record_b)
               AND lr.outcome='independent' AND lr.graph_version=?""",
            (claim_id,claim["graph_version"]),
        ).fetchall()
        return [dict(row) for row in latest]

    def claim_state(self, claim_id: str) -> dict[str, Any]:
        return dict(self._claim(claim_id))

    def audit_history(self, *, claim_id: str | None = None) -> list[dict[str, Any]]:
        if claim_id is None:
            rows = self._db.execute("SELECT * FROM decision_history ORDER BY id").fetchall()
        else:
            rows = self._db.execute("SELECT * FROM decision_history WHERE claim_id=? ORDER BY id", (claim_id,)).fetchall()
        return [dict(row) for row in rows]

    def proposal_count(self) -> int:
        return int(self._db.execute("SELECT COUNT(*) FROM proposals").fetchone()[0])

    def decision_count(self) -> int:
        return int(self._db.execute("SELECT COUNT(*) FROM decision_history").fetchone()[0])
