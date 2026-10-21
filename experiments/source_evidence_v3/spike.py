"""Isolated SQLite v3 evidence spike for synthetic, in-memory fixtures only.

This module does not import or alter the production SQLite module. It has no
file-opening API, network access, or application entry point. The synthetic
owner capability below is a test seam, not authentication or a security
boundary for a deployed application.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import json
import re
import sqlite3
from typing import Iterable, Iterator, Mapping, Any
from urllib.parse import urlsplit


SPIKE_SCHEMA_VERSION = 3
_FIXTURE_MARKER = "POLISH-HORIZONT-SYNTHETIC-V2-ONLY"
_CAPABILITY = object()
_RFC3339 = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?"
    r"(?:Z|[+-]\d{2}:\d{2})$"
)
_ROLES = {"collector", "ai"}
_CLASSIFICATIONS = {"publication_record", "incident_hypothesis", "other_or_unknown"}


def normalize_utc(value: str | None, *, required: bool = False) -> str | None:
    """Strict RFC3339 timestamp validation followed by UTC normalization."""
    if value is None:
        if required:
            raise ValueError("timestamp is required")
        return None
    if not isinstance(value, str) or not _RFC3339.fullmatch(value):
        raise ValueError("timestamp must be RFC3339 with an explicit offset")
    if value.endswith("Z"):
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    else:
        offset = value[-6:]
        hours, minutes = int(offset[1:3]), int(offset[4:6])
        if hours > 23 or minutes > 59:
            raise ValueError("invalid RFC3339 UTC offset")
        parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include a UTC offset")
    return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def normalize_sql_date(value: str | None) -> str | None:
    """Preserve GDELT SQLDATE as a calendar day, never as midnight UTC."""
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(r"\d{8}", value):
        raise ValueError("SQLDATE must use YYYYMMDD")
    try:
        return date(int(value[:4]), int(value[4:6]), int(value[6:8])).isoformat()
    except ValueError as exc:
        raise ValueError("invalid SQLDATE calendar date") from exc


def new_synthetic_v2() -> sqlite3.Connection:
    """Create a fictional v2 baseline in memory, including heterogeneous events."""
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.executescript(
        """
        CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
        CREATE TABLE spike_fixture_meta(marker TEXT NOT NULL);
        CREATE TABLE sources(
            id TEXT PRIMARY KEY, name TEXT NOT NULL, home_url TEXT,
            status TEXT NOT NULL DEFAULT 'never_run'
                CHECK(status IN ('ok','empty','stale','unknown','failed','unavailable','never_run')),
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE ingest_runs(
            id INTEGER PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id),
            started_at TEXT NOT NULL, finished_at TEXT,
            status TEXT NOT NULL CHECK(status IN ('running','ok','empty','failed','unavailable')),
            items_seen INTEGER NOT NULL DEFAULT 0 CHECK(items_seen >= 0),
            items_inserted INTEGER NOT NULL DEFAULT 0 CHECK(items_inserted >= 0),
            error_message TEXT,
            UNIQUE(id, source_id),
            CHECK (finished_at IS NULL OR finished_at >= started_at),
            CHECK (items_inserted <= items_seen)
        );
        CREATE TABLE events(
            id INTEGER PRIMARY KEY, dedup_key TEXT NOT NULL UNIQUE, title TEXT NOT NULL,
            summary TEXT, occurred_at TEXT, published_at TEXT, collected_at TEXT NOT NULL,
            latitude REAL, longitude REAL,
            geo_precision TEXT NOT NULL DEFAULT 'unknown'
                CHECK(geo_precision IN ('unknown','country','region','locality','point')),
            geo_confidence REAL CHECK(geo_confidence IS NULL OR (geo_confidence >= 0 AND geo_confidence <= 1)),
            validation_status TEXT NOT NULL DEFAULT 'pending' CHECK(validation_status IN ('pending','valid','invalid')),
            created_at TEXT NOT NULL,
            CHECK((latitude IS NULL AND longitude IS NULL) OR (latitude IS NOT NULL AND longitude IS NOT NULL)),
            CHECK(latitude IS NULL OR (latitude >= -90 AND latitude <= 90)),
            CHECK(longitude IS NULL OR (longitude >= -180 AND longitude <= 180))
        );
        CREATE TABLE event_sources(
            event_id INTEGER NOT NULL REFERENCES events(id),
            source_id TEXT NOT NULL REFERENCES sources(id),
            ingest_run_id INTEGER REFERENCES ingest_runs(id), original_url TEXT NOT NULL,
            language TEXT, country TEXT,
            first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
            PRIMARY KEY(event_id, source_id),
            FOREIGN KEY(ingest_run_id, source_id) REFERENCES ingest_runs(id, source_id) ON DELETE RESTRICT,
            CHECK(country IS NULL OR (length(country) = 2 AND country = upper(country))),
            CHECK(language IS NULL OR length(language) BETWEEN 2 AND 35)
        );
        INSERT INTO schema_migrations VALUES (1, 'synthetic-v2');
        INSERT INTO schema_migrations VALUES (2, 'synthetic-v2');
        INSERT INTO spike_fixture_meta VALUES ('POLISH-HORIZONT-SYNTHETIC-V2-ONLY');
        INSERT INTO sources(id,name,home_url,created_at,updated_at) VALUES ('govuk-news-atom-v1', 'Fictional GOV.UK source', 'https://example.test/govuk', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z');
        INSERT INTO sources(id,name,created_at,updated_at) VALUES ('fictional-local-source', 'Fictional local source', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z');
        INSERT INTO sources(id,name,created_at,updated_at) VALUES ('gdelt-raw-events-v2', 'Fictional GDELT source', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z');
        INSERT INTO ingest_runs(id,source_id,started_at,finished_at,status,items_seen,items_inserted) VALUES (1, 'govuk-news-atom-v1', '2026-01-01T00:00:00Z', '2026-01-01T00:01:00Z', 'ok', 1, 1);
        INSERT INTO ingest_runs(id,source_id,started_at,finished_at,status,items_seen,items_inserted) VALUES (2, 'fictional-local-source', '2026-01-01T00:00:00Z', '2026-01-01T00:02:00Z', 'ok', 1, 1);
        INSERT INTO ingest_runs(id,source_id,started_at,finished_at,status,items_seen,items_inserted) VALUES (3, 'gdelt-raw-events-v2', '2026-01-02T00:00:00Z', '2026-01-02T00:01:00Z', 'ok', 1, 1);
        INSERT INTO ingest_runs(id,source_id,started_at,finished_at,status,items_seen,items_inserted) VALUES (4, 'gdelt-raw-events-v2', '2026-01-03T00:00:00Z', '2026-01-03T00:01:00Z', 'ok', 1, 1);
        INSERT INTO events(id,dedup_key,title,occurred_at,published_at,collected_at,validation_status,created_at) VALUES (1, 'synthetic:govuk:article:1', 'Fictional official announcement', NULL, '2026-01-01T00:00:00Z', '2026-01-01T00:01:00Z', 'pending', '2026-01-01T00:01:00Z');
        INSERT INTO events(id,dedup_key,title,occurred_at,published_at,collected_at,validation_status,created_at) VALUES (2, 'synthetic:incident:candidate:1', 'Fictional incident candidate', NULL, NULL, '2026-01-01T00:02:00Z', 'pending', '2026-01-01T00:02:00Z');
        INSERT INTO event_sources(event_id,source_id,ingest_run_id,original_url,first_seen_at,last_seen_at) VALUES (1, 'govuk-news-atom-v1', 1, 'https://example.test/news/one', '2026-01-01T00:01:00Z', '2026-01-01T00:01:00Z');
        INSERT INTO event_sources(event_id,source_id,ingest_run_id,original_url,first_seen_at,last_seen_at) VALUES (2, 'fictional-local-source', 2, 'https://example.test/report/one', '2026-01-01T00:02:00Z', '2026-01-01T00:02:00Z');
        """
    )
    db.commit()
    return db


def _require_synthetic_memory_db(db: sqlite3.Connection, *, versions: tuple[int, ...] = (2,)) -> None:
    main = db.execute("PRAGMA database_list").fetchone()
    if main is None or main[2] != "":
        raise PermissionError("spike accepts only an in-memory synthetic database")
    try:
        marker = db.execute("SELECT marker FROM spike_fixture_meta").fetchall()
        version = db.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
    except sqlite3.DatabaseError as exc:
        raise PermissionError("database is not a generated synthetic v2 fixture") from exc
    if [row[0] for row in marker] != [_FIXTURE_MARKER] or version not in versions:
        raise PermissionError("database is not a generated synthetic v2 fixture")


def _create_v3_tables(db: sqlite3.Connection) -> None:
    db.executescript(
        """
        BEGIN IMMEDIATE;
        CREATE TABLE source_records(
            id INTEGER PRIMARY KEY,
            source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE RESTRICT,
            record_kind TEXT NOT NULL CHECK(record_kind IN ('publication','provider_observation','legacy_provenance')),
            provider_namespace TEXT,
            external_record_id TEXT,
            UNIQUE(id, source_id),
            CHECK((provider_namespace IS NULL) = (external_record_id IS NULL)),
            CHECK((record_kind = 'legacy_provenance' AND external_record_id IS NULL)
               OR (record_kind != 'legacy_provenance' AND external_record_id IS NOT NULL))
        );
        CREATE UNIQUE INDEX uq_source_records_provider_identity
            ON source_records(source_id, provider_namespace, external_record_id)
            WHERE external_record_id IS NOT NULL;

        CREATE TABLE source_record_observations(
            id INTEGER PRIMARY KEY,
            source_record_id INTEGER NOT NULL,
            source_id TEXT NOT NULL,
            ingest_run_id INTEGER NOT NULL,
            revision_hash TEXT NOT NULL CHECK(length(revision_hash) = 64),
            canonical_url TEXT,
            title TEXT,
            source_published_at TEXT,
            source_updated_at TEXT,
            collected_at TEXT NOT NULL,
            gdelt_sqldate TEXT,
            gdelt_dateadded_at TEXT,
            attribution TEXT,
            licence_uri TEXT,
            geo_source_fields TEXT,
            UNIQUE(source_record_id, ingest_run_id, revision_hash),
            FOREIGN KEY(source_record_id, source_id) REFERENCES source_records(id, source_id) ON DELETE RESTRICT,
            FOREIGN KEY(ingest_run_id, source_id) REFERENCES ingest_runs(id, source_id) ON DELETE RESTRICT
        );

        CREATE TABLE event_semantic_history(
            id INTEGER PRIMARY KEY,
            event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE RESTRICT,
            semantic_kind TEXT NOT NULL CHECK(semantic_kind IN ('publication_record','incident_hypothesis','other_or_unknown')),
            decision_state TEXT NOT NULL CHECK(decision_state IN ('proposed','accepted','rejected')),
            actor_kind TEXT NOT NULL CHECK(actor_kind IN ('collector','ai','synthetic-owner')),
            actor_label TEXT NOT NULL,
            decided_at TEXT NOT NULL,
            rationale TEXT NOT NULL CHECK(length(trim(rationale)) > 0),
            supersedes_id INTEGER REFERENCES event_semantic_history(id) ON DELETE RESTRICT
        );

        CREATE TABLE event_evidence(
            id INTEGER PRIMARY KEY,
            observation_id INTEGER NOT NULL REFERENCES source_record_observations(id) ON DELETE RESTRICT,
            event_id INTEGER REFERENCES events(id) ON DELETE RESTRICT,
            claim_key TEXT NOT NULL,
            relation TEXT NOT NULL CHECK(relation IN ('supports','contradicts','unverified')),
            independence_state TEXT NOT NULL CHECK(independence_state IN ('unknown','dependent','independent')),
            decision_state TEXT NOT NULL CHECK(decision_state IN ('proposed','accepted','rejected')),
            actor_kind TEXT NOT NULL CHECK(actor_kind IN ('collector','ai','synthetic-owner')),
            actor_label TEXT NOT NULL,
            decided_at TEXT NOT NULL,
            rationale TEXT NOT NULL,
            supersedes_id INTEGER REFERENCES event_evidence(id) ON DELETE RESTRICT,
            CHECK(event_id IS NOT NULL OR (decision_state = 'proposed' AND independence_state = 'unknown'))
        );
        CREATE INDEX idx_event_evidence_claim ON event_evidence(event_id, claim_key, id);
        CREATE INDEX idx_observation_revisions ON source_record_observations(source_record_id, collected_at, id);

        CREATE TABLE event_hypothesis_history(
            id INTEGER PRIMARY KEY,
            event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE RESTRICT,
            validation_status TEXT NOT NULL CHECK(validation_status IN ('pending','valid','invalid')),
            actor_label TEXT NOT NULL,
            decided_at TEXT NOT NULL,
            rationale TEXT NOT NULL CHECK(length(trim(rationale)) > 0)
        );

        CREATE TRIGGER trg_event_evidence_requires_accepted_incident_hypothesis
        BEFORE INSERT ON event_evidence
        WHEN NEW.event_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM event_semantic_history h
            WHERE h.id = (SELECT MAX(h2.id) FROM event_semantic_history h2 WHERE h2.event_id = NEW.event_id AND h2.decision_state = 'accepted')
              AND h.semantic_kind = 'incident_hypothesis'
        )
        BEGIN SELECT RAISE(ABORT, 'event must have accepted incident_hypothesis classification'); END;

        CREATE TRIGGER trg_semantic_history_no_update
        BEFORE UPDATE ON event_semantic_history BEGIN SELECT RAISE(ABORT, 'semantic history is append-only'); END;
        CREATE TRIGGER trg_semantic_history_no_delete
        BEFORE DELETE ON event_semantic_history BEGIN SELECT RAISE(ABORT, 'semantic history is append-only'); END;
        CREATE TRIGGER trg_valid_incident_classification_is_sticky
        BEFORE INSERT ON event_semantic_history
        WHEN NEW.decision_state = 'accepted'
          AND NEW.semantic_kind <> 'incident_hypothesis'
          AND EXISTS (SELECT 1 FROM events WHERE id=NEW.event_id AND validation_status='valid')
          AND EXISTS (
            SELECT 1 FROM event_semantic_history h
            WHERE h.id=(SELECT MAX(h2.id) FROM event_semantic_history h2
                        WHERE h2.event_id=NEW.event_id AND h2.decision_state='accepted')
              AND h.semantic_kind='incident_hypothesis'
          )
        BEGIN SELECT RAISE(ABORT, 'valid incident classification cannot be withdrawn in this prototype'); END;
        CREATE TRIGGER trg_source_records_no_update
        BEFORE UPDATE ON source_records BEGIN SELECT RAISE(ABORT, 'source record identity is immutable'); END;
        CREATE TRIGGER trg_source_records_no_delete
        BEFORE DELETE ON source_records BEGIN SELECT RAISE(ABORT, 'source record identity is immutable'); END;
        CREATE TRIGGER trg_observations_no_update
        BEFORE UPDATE ON source_record_observations BEGIN SELECT RAISE(ABORT, 'source observations are append-only'); END;
        CREATE TRIGGER trg_observations_no_delete
        BEFORE DELETE ON source_record_observations BEGIN SELECT RAISE(ABORT, 'source observations are append-only'); END;
        CREATE TRIGGER trg_evidence_no_update
        BEFORE UPDATE ON event_evidence BEGIN SELECT RAISE(ABORT, 'evidence is append-only'); END;
        CREATE TRIGGER trg_evidence_no_delete
        BEFORE DELETE ON event_evidence BEGIN SELECT RAISE(ABORT, 'evidence is append-only'); END;
        CREATE TRIGGER trg_hypothesis_history_no_update
        BEFORE UPDATE ON event_hypothesis_history BEGIN SELECT RAISE(ABORT, 'hypothesis history is append-only'); END;
        CREATE TRIGGER trg_hypothesis_history_no_delete
        BEFORE DELETE ON event_hypothesis_history BEGIN SELECT RAISE(ABORT, 'hypothesis history is append-only'); END;
        CREATE TRIGGER trg_hypothesis_history_requires_incident_classification
        BEFORE INSERT ON event_hypothesis_history
        WHEN NOT EXISTS (
            SELECT 1 FROM event_semantic_history h
            WHERE h.id = (SELECT MAX(h2.id) FROM event_semantic_history h2 WHERE h2.event_id = NEW.event_id AND h2.decision_state = 'accepted')
              AND h.semantic_kind = 'incident_hypothesis'
        )
        BEGIN SELECT RAISE(ABORT, 'history requires accepted incident_hypothesis classification'); END;

        CREATE TRIGGER trg_validation_requires_incident_classification
        BEFORE UPDATE OF validation_status ON events
        WHEN NEW.validation_status != OLD.validation_status AND NOT EXISTS (
            SELECT 1 FROM event_semantic_history h
            WHERE h.id = (SELECT MAX(h2.id) FROM event_semantic_history h2 WHERE h2.event_id = NEW.id AND h2.decision_state = 'accepted')
              AND h.semantic_kind = 'incident_hypothesis'
        )
        BEGIN SELECT RAISE(ABORT, 'validation status requires accepted incident_hypothesis classification'); END;
        """
    )


def _record_v3_version(db: sqlite3.Connection) -> None:
    db.execute("INSERT INTO schema_migrations(version, applied_at) VALUES (3, 'synthetic-spike')")


def apply_candidate_v3(db: sqlite3.Connection) -> None:
    """Apply candidate DDL only to a fresh in-memory fixture generated above."""
    _require_synthetic_memory_db(db)
    if db.in_transaction:
        raise ValueError("spike DDL requires no active transaction")
    try:
        _create_v3_tables(db)
        _record_v3_version(db)
        db.commit()
    except Exception:
        db.rollback()
        raise


@contextmanager
def _atomic(db: sqlite3.Connection) -> Iterator[None]:
    _require_synthetic_memory_db(db, versions=(3,))
    if db.in_transaction:
        raise ValueError("spike operation requires no active transaction")
    try:
        db.execute("BEGIN IMMEDIATE")
        yield
        db.commit()
    except Exception:
        db.rollback()
        raise


@dataclass(frozen=True)
class _SyntheticOwnerCapability:
    connection: sqlite3.Connection
    token: object
    actor_label: str


@dataclass(frozen=True)
class _SyntheticCollectorCapability:
    connection: sqlite3.Connection
    token: object
    actor_label: str


def synthetic_test_collector_authorizer(db: sqlite3.Connection) -> _SyntheticCollectorCapability:
    """Inject fictional collection authority in tests; this is not auth."""
    _require_synthetic_memory_db(db, versions=(3,))
    return _SyntheticCollectorCapability(db, _CAPABILITY, "synthetic-collector-fixture")


def synthetic_test_owner_authorizer(db: sqlite3.Connection) -> _SyntheticOwnerCapability:
    """Inject explicit fictional authority in tests; this is not login/auth."""
    _require_synthetic_memory_db(db, versions=(3,))
    return _SyntheticOwnerCapability(db, _CAPABILITY, "synthetic-owner-fixture")


def _require_collector(db: sqlite3.Connection, authorizer: object) -> _SyntheticCollectorCapability:
    if (
        not isinstance(authorizer, _SyntheticCollectorCapability)
        or authorizer.token is not _CAPABILITY
        or authorizer.connection is not db
    ):
        raise PermissionError("trusted synthetic collector authorizer required")
    return authorizer


def _require_owner(db: sqlite3.Connection, authorizer: object) -> _SyntheticOwnerCapability:
    if (
        not isinstance(authorizer, _SyntheticOwnerCapability)
        or authorizer.token is not _CAPABILITY
        or authorizer.connection is not db
    ):
        raise PermissionError("trusted synthetic owner authorizer required")
    return authorizer


def add_source_record(
    db: sqlite3.Connection,
    *,
    authorizer: object,
    source_id: str,
    record_kind: str,
    provider_namespace: str | None,
    external_record_id: str | None,
) -> int:
    _require_collector(db, authorizer)
    if record_kind not in {"publication", "provider_observation", "legacy_provenance"}:
        raise ValueError("invalid source record kind")
    with _atomic(db):
        cur = db.execute(
            "INSERT INTO source_records(source_id,record_kind,provider_namespace,external_record_id) VALUES (?,?,?,?)",
            (source_id, record_kind, provider_namespace, external_record_id),
        )
        return int(cur.lastrowid)


def add_observations(
    db: sqlite3.Connection, observations: Iterable[Mapping[str, Any]], *, authorizer: object
) -> list[int]:
    """Atomically append normalized allowlisted observation metadata."""
    _require_collector(db, authorizer)
    inserted: list[int] = []
    with _atomic(db):
        for item in observations:
            for url_field in ("canonical_url", "licence_uri"):
                candidate_url = item.get(url_field)
                if candidate_url is not None:
                    parts = urlsplit(candidate_url)
                    if parts.scheme != "https" or parts.hostname != "example.test":
                        raise ValueError(f"{url_field} must use the synthetic example.test domain")
            normalized = {
                "canonical_url": item.get("canonical_url"),
                "title": item.get("title"),
                "source_published_at": normalize_utc(item.get("source_published_at")),
                "source_updated_at": normalize_utc(item.get("source_updated_at")),
                "gdelt_sqldate": normalize_sql_date(item.get("gdelt_sqldate")),
                "gdelt_dateadded_at": normalize_utc(item.get("gdelt_dateadded_at")),
                "attribution": item.get("attribution"),
                "licence_uri": item.get("licence_uri"),
                "geo_source_fields": item.get("geo_source_fields"),
            }
            revision_hash = _revision_hash(normalized)
            values = (
                int(item["source_record_id"]), item["source_id"], int(item["ingest_run_id"]),
                revision_hash, normalized["canonical_url"], normalized["title"],
                normalized["source_published_at"], normalized["source_updated_at"],
                normalize_utc(item.get("collected_at"), required=True),
                normalized["gdelt_sqldate"], normalized["gdelt_dateadded_at"], normalized["attribution"],
                normalized["licence_uri"], normalized["geo_source_fields"],
            )
            cur = db.execute(
                """INSERT INTO source_record_observations(
                    source_record_id,source_id,ingest_run_id,revision_hash,canonical_url,title,
                    source_published_at,source_updated_at,collected_at,gdelt_sqldate,
                    gdelt_dateadded_at,attribution,licence_uri,geo_source_fields
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                values,
            )
            inserted.append(int(cur.lastrowid))
    return inserted


def _revision_hash(metadata: Mapping[str, Any]) -> str:
    """Hash the fixed normalized source-metadata allowlist; collection time is excluded."""
    canonical = json.dumps(
        metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def propose_classification(
    db: sqlite3.Connection, *, event_id: int, semantic_kind: str, role: str, rationale: str
) -> int:
    """Record a non-authoritative proposal by a collector or AI only."""
    if role not in _ROLES:
        raise PermissionError("only collector/AI proposal roles are accepted here")
    if semantic_kind not in _CLASSIFICATIONS:
        raise ValueError("invalid semantic classification")
    rationale = rationale.strip()
    if not rationale:
        raise ValueError("proposal rationale is required")
    with _atomic(db):
        cur = db.execute(
            """INSERT INTO event_semantic_history(
                event_id,semantic_kind,decision_state,actor_kind,actor_label,decided_at,rationale
            ) VALUES (?,?, 'proposed',?,?,?,?)""",
            (event_id, semantic_kind, role, f"{role}-proposal", _now(), rationale),
        )
        return int(cur.lastrowid)


def accept_classification(
    db: sqlite3.Connection, *, event_id: int, semantic_kind: str,
    rationale: str, authorizer: object,
) -> int:
    actor = _require_owner(db, authorizer)
    if semantic_kind not in _CLASSIFICATIONS:
        raise ValueError("invalid semantic classification")
    rationale = rationale.strip()
    if not rationale:
        raise ValueError("owner rationale is required")
    with _atomic(db):
        current = db.execute("SELECT validation_status FROM events WHERE id=?", (event_id,)).fetchone()
        if current is None:
            raise ValueError("unknown event")
        if current[0] == "valid" and _latest_classification(db, event_id) == "incident_hypothesis" and semantic_kind != "incident_hypothesis":
            raise PermissionError("valid incident classification cannot be withdrawn in this prototype")
        previous = db.execute(
            "SELECT id FROM event_semantic_history WHERE event_id=? ORDER BY id DESC LIMIT 1", (event_id,)
        ).fetchone()
        cur = db.execute(
            """INSERT INTO event_semantic_history(
                event_id,semantic_kind,decision_state,actor_kind,actor_label,decided_at,rationale,supersedes_id
            ) VALUES (?,?, 'accepted','synthetic-owner',?,?,?,?)""",
            (event_id, semantic_kind, actor.actor_label, _now(), rationale, previous[0] if previous else None),
        )
        return int(cur.lastrowid)


def reject_classification_proposal(
    db: sqlite3.Connection, *, proposal_id: int, rationale: str, authorizer: object
) -> int:
    """Append an owner rejection for a proposal without changing accepted state."""
    actor = _require_owner(db, authorizer)
    rationale = rationale.strip()
    if not rationale:
        raise ValueError("owner rationale is required")
    with _atomic(db):
        proposal = db.execute(
            "SELECT event_id,semantic_kind,decision_state FROM event_semantic_history WHERE id=?",
            (proposal_id,),
        ).fetchone()
        if proposal is None or proposal["decision_state"] != "proposed":
            raise ValueError("only an outstanding proposal can be rejected")
        cur = db.execute(
            """INSERT INTO event_semantic_history(
                event_id,semantic_kind,decision_state,actor_kind,actor_label,decided_at,rationale,supersedes_id
            ) VALUES (?,?, 'rejected','synthetic-owner',?,?,?,?)""",
            (proposal["event_id"],proposal["semantic_kind"],actor.actor_label,_now(),rationale,proposal_id),
        )
        return int(cur.lastrowid)


def add_evidence_proposal(
    db: sqlite3.Connection, *, observation_id: int, event_id: int | None,
    claim_key: str, relation: str, role: str, rationale: str = "synthetic proposal",
) -> int:
    if role not in _ROLES:
        raise PermissionError("only collector/AI proposal roles are accepted here")
    if role == "collector" and relation != "unverified":
        raise PermissionError("collector may append only unverified evidence")
    if relation not in {"supports", "contradicts", "unverified"}:
        raise ValueError("invalid evidence relation")
    if not claim_key.strip():
        raise ValueError("claim_key is required")
    if event_id is not None and _latest_classification(db, event_id) != "incident_hypothesis":
        raise PermissionError("evidence cannot link to an unclassified or non-incident event")
    with _atomic(db):
        cur = db.execute(
            """INSERT INTO event_evidence(
                observation_id,event_id,claim_key,relation,independence_state,decision_state,
                actor_kind,actor_label,decided_at,rationale
            ) VALUES (?,?,?,?, 'unknown','proposed',?,?,?,?)""",
            (observation_id,event_id,claim_key,relation,role,f"{role}-proposal",_now(),rationale),
        )
        return int(cur.lastrowid)


def accept_independence(
    db: sqlite3.Connection, *, evidence_id: int, independent: bool,
    rationale: str, authorizer: object,
) -> int:
    actor = _require_owner(db, authorizer)
    rationale = rationale.strip()
    if not rationale:
        raise ValueError("owner rationale is required")
    with _atomic(db):
        prior = db.execute("SELECT * FROM event_evidence WHERE id=?", (evidence_id,)).fetchone()
        if prior is None or prior["event_id"] is None:
            raise ValueError("evidence must be linked to an accepted incident hypothesis")
        if _latest_classification(db, prior["event_id"]) != "incident_hypothesis":
            raise PermissionError("event is not an accepted incident hypothesis")
        if independent:
            origin = db.execute(
                """SELECT o.canonical_url FROM source_record_observations o WHERE o.id=?""",
                (prior["observation_id"],),
            ).fetchone()
            if origin is None or not origin[0]:
                raise PermissionError("independence cannot be approved without a canonical URL")
            comparators = db.execute(
                """SELECT e.observation_id,o.canonical_url
                   FROM event_evidence e JOIN source_record_observations o ON o.id=e.observation_id
                   WHERE e.event_id=? AND e.claim_key=? AND e.id<>?""",
                (prior["event_id"], prior["claim_key"], evidence_id),
            ).fetchall()
            if not comparators or any(
                row["observation_id"] == prior["observation_id"]
                or not row["canonical_url"]
                or row["canonical_url"] == origin[0]
                for row in comparators
            ):
                raise PermissionError("independence cannot be demonstrated from distinct observations and canonical URLs")
        cur = db.execute(
            """INSERT INTO event_evidence(
                observation_id,event_id,claim_key,relation,independence_state,decision_state,
                actor_kind,actor_label,decided_at,rationale,supersedes_id
            ) VALUES (?,?,?,?,?,'accepted','synthetic-owner',?,?,?,?)""",
            (prior["observation_id"],prior["event_id"],prior["claim_key"],prior["relation"],
             "independent" if independent else "dependent",actor.actor_label,_now(),rationale,evidence_id),
        )
        return int(cur.lastrowid)


def set_incident_validation(
    db: sqlite3.Connection, *, event_id: int, status: str,
    rationale: str, authorizer: object,
) -> None:
    actor = _require_owner(db, authorizer)
    if status not in {"valid", "invalid", "pending"}:
        raise ValueError("invalid incident validation status")
    if _latest_classification(db, event_id) != "incident_hypothesis":
        raise PermissionError("only an accepted incident hypothesis can receive validation status")
    rationale = rationale.strip()
    if not rationale:
        raise ValueError("owner rationale is required")
    with _atomic(db):
        row = db.execute("SELECT validation_status FROM events WHERE id=?", (event_id,)).fetchone()
        if row is None:
            raise ValueError("unknown event")
        db.execute("UPDATE events SET validation_status=? WHERE id=?", (status, event_id))
        db.execute(
            "INSERT INTO event_hypothesis_history(event_id,validation_status,actor_label,decided_at,rationale) VALUES (?,?,?,?,?)",
            (event_id,status,actor.actor_label,_now(),rationale),
        )


def _latest_classification(db: sqlite3.Connection, event_id: int) -> str | None:
    row = db.execute(
        """SELECT semantic_kind, decision_state FROM event_semantic_history
           WHERE event_id=? AND decision_state = 'accepted' ORDER BY id DESC LIMIT 1""",
        (event_id,),
    ).fetchone()
    if row is None or row["decision_state"] != "accepted":
        return None
    return str(row["semantic_kind"])


def legacy_event_semantics(db: sqlite3.Connection) -> list[tuple[int, str, str]]:
    """Read-only synthetic view: accepted decisions only; absent = unclassified."""
    _require_synthetic_memory_db(db, versions=(3,))
    if db.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] != 3:
        raise ValueError("v3 spike has not been applied")
    result = []
    rows = db.execute("SELECT id, validation_status FROM events ORDER BY id").fetchall()
    for row in rows:
        classification = _latest_classification(db, int(row["id"])) or "unclassified"
        result.append((int(row["id"]), classification, str(row["validation_status"])))
    return result


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
