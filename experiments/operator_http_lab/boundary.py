"""Offline HTTP boundary experiment; never imported by the production app.

The module contains no identity provider, credentials, persistent storage, or
network client. A caller must explicitly inject both authentication and
authorization adapters. The optional server factory binds only to IPv4
loopback and an ephemeral port for short-lived tests.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import re
import secrets
import threading
import time
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import urlsplit


class AuthNRejected(PermissionError):
    """The injected assertion verifier rejected a fictional assertion."""


class AuthNUnavailable(RuntimeError):
    """The injected assertion verifier is unavailable; requests fail closed."""


class BoundaryDenied(PermissionError):
    """Synthetic authorization denied a request."""


class BoundaryConflict(RuntimeError):
    """The requested optimistic version is stale."""


class AuthorityUnavailable(RuntimeError):
    """The isolated audit/decision backend is unavailable."""


@dataclass(frozen=True)
class VerifiedIdentity:
    """Verified claims returned by an explicitly injected AuthN adapter."""

    issuer: str
    subject: str
    audience: str
    authenticated_at: int


class AuthNAdapter(Protocol):
    def verify_user_assertion(self, assertion: str) -> VerifiedIdentity: ...


class AuthorityBackend(Protocol):
    """Narrow AuthZ/decision port. Implementations are injected by tests only."""

    def read_claim(self, claim_id: str) -> Mapping[str, Any]: ...
    def graph_digest(self, claim_id: str) -> str: ...
    def issue_step_up(self, actor_ref: str, **request: Any) -> str: ...
    def decide(self, actor_ref: str, **request: Any) -> int: ...


class DenyAllAuthNAdapter:
    """Default AuthN adapter. It always rejects; it has no login fallback."""

    def verify_user_assertion(self, assertion: str) -> VerifiedIdentity:
        del assertion
        raise AuthNRejected("authentication unavailable")


@dataclass(frozen=True)
class BoundaryConfig:
    approved_origin: str = "https://operator.example.test"
    expected_host: str = "operator.example.test"
    owner_issuer: str = "https://identity.example.test"
    owner_audience: str = "horizont-operator-lab"
    owner_subject: str = "fictional-owner"
    session_ttl_seconds: int = 300
    step_up_max_age_seconds: int = 60
    max_body_bytes: int = 8192

    def __post_init__(self) -> None:
        parsed = urlsplit(self.approved_origin)
        if (parsed.scheme != "https" or parsed.hostname != self.expected_host or
                parsed.path or parsed.query or parsed.fragment or parsed.username or
                parsed.password or parsed.port is not None):
            raise ValueError("approved origin must be one exact fictional HTTPS origin")
        if not self.owner_issuer or not self.owner_audience or not self.owner_subject:
            raise ValueError("synthetic identity policy fields are required")
        if (self.session_ttl_seconds <= 0 or self.step_up_max_age_seconds <= 0 or
                not 1 <= self.max_body_bytes <= 65536):
            raise ValueError("invalid synthetic session/body limit")


@dataclass
class _Session:
    identity: VerifiedIdentity
    csrf_token: str
    expires_at: int
    revoked: bool = False
    commit_lock: Any = None


class _SessionStore:
    """Opaque server-side sessions; only token digests are indexed."""

    def __init__(self, *, clock: Callable[[], int], ttl: int):
        self._clock = clock
        self._ttl = ttl
        self._sessions: dict[str, _Session] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _key(token: str) -> str:
        return sha256(token.encode("ascii")).hexdigest()

    def issue(self, identity: VerifiedIdentity) -> tuple[str, _Session]:
        token = secrets.token_urlsafe(32)
        session = _Session(identity, secrets.token_urlsafe(24), self._clock() + self._ttl, commit_lock=threading.RLock())
        with self._lock:
            self._sessions[self._key(token)] = session
        return token, session

    def lookup(self, token: str) -> _Session | None:
        if not token or len(token) > 128:
            return None
        try:
            key = self._key(token)
        except UnicodeEncodeError:
            return None
        with self._lock:
            session = self._sessions.get(key)
            if session is None or session.revoked or self._clock() >= session.expires_at:
                if session is not None:
                    session.revoked = True
                return None
            return session

    def revoke(self, token: str) -> None:
        if not token:
            return
        try:
            key = self._key(token)
        except UnicodeEncodeError:
            return
        with self._lock:
            session = self._sessions.get(key)
            if session is not None:
                with session.commit_lock:
                    session.revoked = True


class OperatorHttpLab:
    """Explicitly injected AuthN and AuthZ boundary used only by test runners."""

    SESSION_COOKIE = "__Host-horizont_operator"
    _CLAIM_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
    _ACTIONS = {"accept_classification", "validate_incident"}

    def __init__(
        self,
        *,
        authn_adapter: AuthNAdapter | None = None,
        authority: AuthorityBackend | None = None,
        config: BoundaryConfig | None = None,
        clock: Callable[[], int] | None = None,
    ):
        self.config = config or BoundaryConfig()
        self.authn_adapter = authn_adapter or DenyAllAuthNAdapter()
        self.authority = authority
        self.clock = clock or (lambda: int(time.time()))
        self.sessions = _SessionStore(clock=self.clock, ttl=self.config.session_ttl_seconds)
        # Kept server-side, bounded, and bound to the browser session + action.
        self._stepups: dict[str, tuple[str, str, dict[str, Any]]] = {}
        self._lock = threading.RLock()

    def _revoke_session(self, token: str) -> None:
        try:
            key = _SessionStore._key(token)
        except UnicodeEncodeError:
            return
        self.sessions.revoke(token)
        with self._lock:
            self._stepups = {
                step_id: binding for step_id, binding in self._stepups.items()
                if binding[0] != key
            }

    def build_loopback_server(self) -> ThreadingHTTPServer:
        """Create an ephemeral IPv4-loopback test server; never bind publicly."""
        lab = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, fmt: str, *args: Any) -> None:
                # Do not log paths, headers, assertion values, cookies, or bodies.
                del fmt, args

            def _send(self, status: int, payload: Mapping[str, Any] | None = None,
                      *, cookie: str | None = None) -> None:
                body = b"" if payload is None else json.dumps(
                    payload, ensure_ascii=True, separators=(",", ":"),
                ).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("X-Frame-Options", "DENY")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Cross-Origin-Resource-Policy", "same-origin")
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
                )
                if cookie is not None:
                    self.send_header("Set-Cookie", cookie)
                self.end_headers()
                if body:
                    self.wfile.write(body)

            def _fail(self, status: int, code: str) -> None:
                self._send(status, {"error": code})

            def _host_ok(self) -> bool:
                values = self.headers.get_all("Host", [])
                return len(values) == 1 and values[0] == lab.config.expected_host

            def _origin_ok(self) -> bool:
                values = self.headers.get_all("Origin", [])
                return len(values) == 1 and values[0] == lab.config.approved_origin

            def _session_token(self) -> str | None:
                values = self.headers.get_all("Cookie", [])
                if len(values) != 1:
                    return None
                matches: list[str] = []
                for item in values[0].split(";"):
                    name, sep, value = item.strip().partition("=")
                    if sep and name == lab.SESSION_COOKIE:
                        matches.append(value)
                if len(matches) != 1:
                    return None
                return matches[0]

            def _session(self) -> tuple[str, _Session] | None:
                token = self._session_token()
                session = lab.sessions.lookup(token or "")
                if token is None or session is None:
                    return None
                return token, session

            def _owner(self, identity: VerifiedIdentity) -> bool:
                return (
                    identity.issuer == lab.config.owner_issuer
                    and identity.audience == lab.config.owner_audience
                    and identity.subject == lab.config.owner_subject
                )

            def _csrf(self, session: _Session) -> bool:
                values = self.headers.get_all("X-CSRF-Token", [])
                return (len(values) == 1 and values[0].isascii() and
                        hmac.compare_digest(values[0], session.csrf_token))

            def _json(self) -> dict[str, Any] | None:
                if self.headers.get_all("Authorization", []):
                    self._fail(400, "invalid_request")
                    return None
                if self.headers.get_all("Transfer-Encoding", []):
                    self._fail(400, "invalid_request")
                    return None
                lengths = self.headers.get_all("Content-Length", [])
                if (len(lengths) != 1 or
                        re.fullmatch(r"[0-9]{1,10}", lengths[0]) is None):
                    self._fail(400, "invalid_request")
                    return None
                length = int(lengths[0])
                if length > lab.config.max_body_bytes:
                    self._fail(413, "request_too_large")
                    return None
                content_types = self.headers.get_all("Content-Type", [])
                if content_types != ["application/json"]:
                    self._fail(415, "unsupported_media_type")
                    return None
                raw = self.rfile.read(length)
                if len(raw) != length:
                    self._fail(400, "invalid_request")
                    return None

                def unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
                    result: dict[str, Any] = {}
                    for key, value in pairs:
                        if key in result:
                            raise ValueError("duplicate JSON field")
                        result[key] = value
                    return result

                try:
                    data = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_pairs)
                except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
                    self._fail(400, "invalid_request")
                    return None
                if not isinstance(data, dict):
                    self._fail(400, "invalid_request")
                    return None
                return data

            def _identity(self, assertion: Any) -> VerifiedIdentity | None:
                if not isinstance(assertion, str) or not assertion or len(assertion) > 2048:
                    self._fail(401, "authentication_required")
                    return None
                try:
                    identity = lab.authn_adapter.verify_user_assertion(assertion)
                except AuthNUnavailable:
                    self._fail(503, "authentication_unavailable")
                    return None
                except Exception:
                    self._fail(401, "authentication_required")
                    return None
                if (not isinstance(identity, VerifiedIdentity) or not identity.subject or
                        identity.issuer != lab.config.owner_issuer or
                        identity.audience != lab.config.owner_audience):
                    self._fail(401, "authentication_required")
                    return None
                return identity

            def _authority(self) -> AuthorityBackend | None:
                if lab.authority is None:
                    self._fail(503, "decision_service_unavailable")
                    return None
                return lab.authority

            def _read_state(self, backend: AuthorityBackend, claim_id: str) -> dict[str, Any] | None:
                if not lab._CLAIM_ID.fullmatch(claim_id):
                    self._fail(404, "not_found")
                    return None
                try:
                    state = dict(backend.read_claim(claim_id))
                    state["evidence_digest"] = backend.graph_digest(claim_id)
                except AuthorityUnavailable:
                    self._fail(503, "decision_service_unavailable")
                    return None
                except Exception:
                    self._fail(404, "not_found")
                    return None
                return state

            def do_GET(self) -> None:
                if not self._host_ok():
                    self._fail(400, "invalid_host")
                    return
                if self.path == "/api/csrf":
                    current = self._session()
                    if current is None:
                        self._fail(401, "authentication_required")
                        return
                    self._send(200, {"csrf_token": current[1].csrf_token})
                    return
                claim_match = re.fullmatch(r"/api/claims/([A-Za-z0-9_-]{1,64})", self.path)
                if claim_match:
                    current = self._session()
                    if current is None:
                        self._fail(401, "authentication_required")
                        return
                    if not self._owner(current[1].identity):
                        self._fail(403, "forbidden")
                        return
                    backend = self._authority()
                    if backend is None:
                        return
                    state = self._read_state(backend, claim_match.group(1))
                    if state is None:
                        return
                    # Bounded allow-list. Never expose rationale, identity, or source text.
                    self._send(200, {key: state[key] for key in (
                        "claim_id", "semantic_kind", "effective_status", "version",
                        "graph_version", "evidence_digest", "active_evidence_count",
                    ) if key in state})
                    return
                if "/decision" in self.path or "/step-up" in self.path:
                    self._fail(405, "method_not_allowed")
                    return
                self._fail(404, "not_found")

            def do_POST(self) -> None:
                if not self._host_ok():
                    self._fail(400, "invalid_host")
                    return
                if not self._origin_ok():
                    self._fail(403, "origin_denied")
                    return
                data = self._json()
                if data is None:
                    return
                if self.path == "/__test__/session":
                    if set(data) != {"assertion"}:
                        self._fail(400, "invalid_request")
                        return
                    identity = self._identity(data.get("assertion"))
                    if identity is None:
                        return
                    old_token = self._session_token()
                    if old_token:
                        lab._revoke_session(old_token)
                    token, _ = lab.sessions.issue(identity)
                    cookie = (
                        f"{lab.SESSION_COOKIE}={token}; Path=/; Secure; HttpOnly; SameSite=Strict"
                    )
                    self._send(204, cookie=cookie)
                    return

                current = self._session()
                if current is None:
                    self._fail(401, "authentication_required")
                    return
                token, session = current
                if not self._csrf(session):
                    self._fail(403, "csrf_denied")
                    return
                if self.path == "/api/session/revoke":
                    if data:
                        self._fail(400, "invalid_request")
                        return
                    lab._revoke_session(token)
                    self._send(204, cookie=f"{lab.SESSION_COOKIE}=; Path=/; Secure; HttpOnly; SameSite=Strict; Max-Age=0")
                    return

                match = re.fullmatch(r"/api/claims/([A-Za-z0-9_-]{1,64})/(step-up|decision)", self.path)
                if not match:
                    self._fail(404, "not_found")
                    return
                if not self._owner(session.identity):
                    self._fail(403, "forbidden")
                    return
                claim_id, operation = match.groups()
                required = {
                    "action", "outcome", "expected_version", "graph_version",
                    "evidence_digest", "idempotency_key", "rationale", "details",
                }
                if operation == "step-up":
                    required.add("assertion")
                else:
                    required.add("step_up_id")
                if set(data) != required:
                    self._fail(400, "invalid_request")
                    return
                if (not isinstance(data["action"], str) or data["action"] not in lab._ACTIONS or
                        not isinstance(data["outcome"], str) or
                        type(data["expected_version"]) is not int or data["expected_version"] < 0 or
                        type(data["graph_version"]) is not int or data["graph_version"] < 0 or
                        not isinstance(data["evidence_digest"], str) or
                        not isinstance(data["idempotency_key"], str) or not data["idempotency_key"] or
                        len(data["idempotency_key"]) > 128 or
                        not isinstance(data["rationale"], str) or not data["rationale"].strip() or
                        len(data["rationale"]) > 1000 or not isinstance(data["details"], dict)):
                    self._fail(400, "invalid_request")
                    return
                allowed = {"accept_classification": {"incident_hypothesis", "publication_record", "other_or_unknown"},
                           "validate_incident": {"valid", "invalid", "pending"}}
                if data["outcome"] not in allowed[data["action"]]:
                    self._fail(400, "invalid_request")
                    return
                backend = self._authority()
                if backend is None:
                    return
                details = {
                    "evidence_digest": data["evidence_digest"],
                    "idempotency_key": data["idempotency_key"],
                    "rationale": data["rationale"].strip(),
                    "operation_details": data["details"],
                }
                if operation == "step-up":
                    identity = self._identity(data["assertion"])
                    if identity is None:
                        return
                    if identity.subject != session.identity.subject:
                        self._fail(403, "forbidden")
                        return
                    if (identity.authenticated_at > lab.clock() or
                            lab.clock() - identity.authenticated_at > lab.config.step_up_max_age_seconds):
                        self._fail(401, "reauthentication_required")
                        return
                    state = self._read_state(backend, claim_id)
                    if state is None:
                        return
                    if (state.get("version") != data["expected_version"] or
                            state.get("graph_version") != data["graph_version"] or
                            state.get("evidence_digest") != data["evidence_digest"]):
                        self._fail(409, "stale_review")
                        return
                    try:
                        step_up_id = backend.issue_step_up(
                            session.identity.subject, action=data["action"], claim_id=claim_id,
                            outcome=data["outcome"], expected_version=data["expected_version"],
                            graph_version=data["graph_version"], details=details,
                        )
                    except BoundaryDenied:
                        self._fail(403, "forbidden")
                        return
                    except (BoundaryConflict,):
                        self._fail(409, "stale_review")
                        return
                    except AuthorityUnavailable:
                        self._fail(503, "decision_service_unavailable")
                        return
                    binding = {
                        "action": data["action"], "claim_id": claim_id,
                        "outcome": data["outcome"], "expected_version": data["expected_version"],
                        "graph_version": data["graph_version"], "evidence_digest": data["evidence_digest"],
                        "idempotency_key": data["idempotency_key"], "rationale": data["rationale"].strip(),
                        "details": data["details"],
                    }
                    session_key = _SessionStore._key(token)
                    with lab._lock:
                        if len(lab._stepups) >= 1024:
                            lab._stepups.clear()
                        lab._stepups[step_up_id] = (session_key, session.identity.subject, binding)
                    self._send(200, {"step_up_id": step_up_id})
                    return

                step_up_id = data["step_up_id"]
                if not isinstance(step_up_id, str):
                    self._fail(400, "invalid_request")
                    return
                binding = lab._stepups.get(step_up_id)
                expected_binding = {
                    "action": data["action"], "claim_id": claim_id,
                    "outcome": data["outcome"], "expected_version": data["expected_version"],
                    "graph_version": data["graph_version"], "evidence_digest": data["evidence_digest"],
                    "idempotency_key": data["idempotency_key"], "rationale": data["rationale"].strip(),
                    "details": data["details"],
                }
                if (binding is None or binding[0] != _SessionStore._key(token) or
                        binding[1] != session.identity.subject or binding[2] != expected_binding):
                    self._fail(403, "step_up_denied")
                    return
                @contextmanager
                def commit_guard():
                    # Backend must enter this immediately around its local atomic
                    # write, after any blocking preparation. No network IO here.
                    with session.commit_lock:
                        if session.revoked or lab.clock() >= session.expires_at:
                            raise BoundaryDenied("session no longer active")
                        yield

                try:
                    decision_id = backend.decide(
                        session.identity.subject, action=data["action"], claim_id=claim_id,
                        outcome=data["outcome"], expected_version=data["expected_version"],
                        graph_version=data["graph_version"], step_up_id=step_up_id,
                        idempotency_key=data["idempotency_key"], rationale=data["rationale"].strip(),
                        details=details, commit_guard=commit_guard,
                    )
                except BoundaryDenied:
                    self._fail(403, "forbidden")
                    return
                except BoundaryConflict:
                    self._fail(409, "stale_review")
                    return
                except AuthorityUnavailable:
                    self._fail(503, "decision_service_unavailable")
                    return
                except Exception:
                    self._fail(503, "decision_service_unavailable")
                    return
                state = self._read_state(backend, claim_id)
                if state is None:
                    # _read_state already emitted a fail-closed error response.
                    # The committed decision remains auditable and retryable by
                    # its idempotency key if projection refresh is unavailable.
                    return
                # The decision id is a historical response; current claim state
                # is returned separately so stale evidence is never implied valid.
                self._send(200, {
                    "decision_id": decision_id,
                    "current_claim": None if state is None else {
                        key: state.get(key) for key in (
                            "semantic_kind", "effective_status", "version", "graph_version",
                            "active_evidence_count",
                        )
                    },
                })

            def _reject_method(self) -> None:
                self.send_response(405)
                self.send_header("Allow", "GET, POST")
                self.send_header("Content-Length", "0")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()

            do_PUT = _reject_method
            do_PATCH = _reject_method
            do_DELETE = _reject_method
            do_OPTIONS = _reject_method
            do_HEAD = _reject_method

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        if server.server_address[0] != "127.0.0.1":
            server.server_close()
            raise RuntimeError("HTTP lab must bind only to IPv4 loopback")
        return server
