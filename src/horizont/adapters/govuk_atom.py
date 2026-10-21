"""Offline GOV.UK News Atom parser and optional SQLite importer.

This module accepts caller-supplied XML only. It never performs HTTP requests
and treats feed entries as official publications, not verified incidents.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import re
import sqlite3
from urllib.parse import urlsplit, urlunsplit
import xml.etree.ElementTree as ET

from ..core.db import add_event, create_source, finish_ingest_run, normalize_utc, start_ingest_run

SOURCE_ID = "govuk-news-atom-v1"
SOURCE_NAME = "GOV.UK News and communications"
GOVUK_HOME_URL = "https://www.gov.uk/search/news-and-communications"
OGL_URL = "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/"
MAX_XML_BYTES = 128 * 1024
MAX_ENTRIES = 10
ATOM_NS = "http://www.w3.org/2005/Atom"
_ATOM = f"{{{ATOM_NS}}}"
_FORBIDDEN_DECLARATION = re.compile(r"<!\s*(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)
_RFC3339 = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?"
    r"(?P<offset>Z|(?P<sign>[+-])(?P<offset_hour>\d{2}):(?P<offset_minute>\d{2}))$"
)


class GOVUKAtomError(ValueError):
    """The supplied Atom document violates the offline adapter contract."""


@dataclass(frozen=True)
class Publication:
    """Normalized safe metadata for one unverified official publication."""

    atom_id: str
    title: str
    original_url: str
    dedup_key: str
    published_at: str | None
    updated_at: str | None
    source_id: str = SOURCE_ID
    attribution: str = "GOV.UK"
    attribution_url: str = GOVUK_HOME_URL
    license_url: str = OGL_URL
    occurred_at: None = None
    country: None = None
    latitude: None = None
    longitude: None = None
    geo_precision: str = "unknown"
    geo_confidence: None = None
    validation_status: str = "pending"


@dataclass(frozen=True)
class PublicationFeed:
    publications: tuple[Publication, ...]
    collected_at: str


@dataclass(frozen=True)
class IngestResult:
    run_id: int
    status: str
    items_seen: int
    items_inserted: int
    publications: tuple[Publication, ...]


def _timestamp(value: str | None, field: str) -> str | None:
    if value is None or not value.strip():
        return None
    try:
        value = value.strip()
        match = _RFC3339.fullmatch(value)
        if not match:
            raise ValueError("RFC3339 timestamp required")
        if match.group("offset") != "Z":
            offset_hour = int(match.group("offset_hour"))
            offset_minute = int(match.group("offset_minute"))
            if offset_hour > 23 or offset_minute > 59:
                raise ValueError("invalid RFC3339 UTC offset")
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("timezone required")
        return normalize_utc(parsed, required=True)
    except (TypeError, ValueError, OverflowError) as exc:
        raise GOVUKAtomError(f"{field} must be a valid timezone-aware timestamp") from exc


def _collected_timestamp(value: str | datetime) -> str:
    """Validate text offsets locally before shared UTC normalization."""
    if isinstance(value, str):
        normalized = _timestamp(value, "collected_at")
        if normalized is None:
            raise GOVUKAtomError("collected_at must be a timezone-aware timestamp")
        return normalized
    try:
        return normalize_utc(value, required=True)
    except (TypeError, ValueError, OverflowError) as exc:
        raise GOVUKAtomError("collected_at must be a timezone-aware timestamp") from exc


def _text(element: ET.Element | None, field: str, *, required: bool) -> str | None:
    if element is None:
        if required:
            raise GOVUKAtomError(f"missing Atom {field}")
        return None
    value = "".join(element.itertext()).strip()
    if not value and required:
        raise GOVUKAtomError(f"empty Atom {field}")
    return value or None


def canonicalize_article_url(value: str) -> str:
    """Require a canonical HTTPS GOV.UK article URL (or synthetic test host)."""
    if not isinstance(value, str) or not value or value != value.strip():
        raise GOVUKAtomError("article URL must be an absolute HTTPS URL")
    if any(ord(ch) < 32 or ch.isspace() for ch in value):
        raise GOVUKAtomError("article URL contains whitespace or control characters")
    try:
        parts = urlsplit(value)
        hostname = parts.hostname
        port = parts.port
    except ValueError as exc:
        raise GOVUKAtomError("article URL has an invalid host or port") from exc
    if (parts.scheme.lower() != "https" or hostname is None or
            parts.username is not None or parts.password is not None or
            port not in (None, 443)):
        raise GOVUKAtomError("article URL must use safe HTTPS without credentials or a custom port")
    try:
        host = hostname.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise GOVUKAtomError("article URL host is invalid") from exc
    if host not in {"www.gov.uk", "example.test"}:
        raise GOVUKAtomError("article URL host is not an approved publisher host")
    if parts.query:
        raise GOVUKAtomError("article URL must not contain a query string")
    path = parts.path or "/"
    if not path.startswith("/") or path == "/":
        raise GOVUKAtomError("article URL must identify an article path")
    return urlunsplit(("https", host, path, "", ""))


def parse_feed(xml_payload: bytes | str, *, collected_at: str | datetime) -> PublicationFeed:
    """Parse bounded synthetic or caller-supplied Atom XML without network I/O.

    Malformed documents, DTD/entity declarations, unsafe URLs, missing or
    duplicate canonical article URLs/titles, and over-limit feeds fail closed.
    Entry timestamps remain distinct: publication/update times never imply an
    occurrence time; collection time is supplied by the caller.
    """
    collected = _collected_timestamp(collected_at)
    if isinstance(xml_payload, str):
        try:
            raw = xml_payload.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise GOVUKAtomError("XML must be valid UTF-8") from exc
    elif isinstance(xml_payload, bytes):
        raw = xml_payload
    else:
        raise GOVUKAtomError("XML payload must be bytes or text")
    if len(raw) > MAX_XML_BYTES:
        raise GOVUKAtomError(f"XML exceeds {MAX_XML_BYTES} bytes")
    try:
        xml_text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise GOVUKAtomError("XML must be UTF-8") from exc
    if _FORBIDDEN_DECLARATION.search(xml_text):
        raise GOVUKAtomError("DTD and entity declarations are forbidden")
    try:
        root = ET.fromstring(xml_text)
    except (ET.ParseError, ValueError) as exc:
        raise GOVUKAtomError("malformed Atom XML") from exc
    if root.tag != f"{_ATOM}feed":
        raise GOVUKAtomError("root element must be Atom 1.0 feed")
    if any(child.tag.rsplit("}", 1)[-1] == "entry" and child.tag != f"{_ATOM}entry" for child in root):
        raise GOVUKAtomError("feed contains an entry outside the Atom 1.0 namespace")
    # Atom requires feed-level id, title and updated metadata.
    feed_id = _text(root.find(f"{_ATOM}id"), "feed id", required=True)
    _text(root.find(f"{_ATOM}title"), "feed title", required=True)
    if not feed_id:
        raise GOVUKAtomError("empty Atom feed id")
    _timestamp(_text(root.find(f"{_ATOM}updated"), "feed updated", required=True), "feed updated")
    entries = root.findall(f"{_ATOM}entry")
    if len(entries) > MAX_ENTRIES:
        raise GOVUKAtomError(f"feed exceeds {MAX_ENTRIES} entries")

    publications: list[Publication] = []
    seen_ids: set[str] = set()
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    for index, entry in enumerate(entries):
        atom_id = _text(entry.find(f"{_ATOM}id"), f"entry {index} id", required=True)
        title = _text(entry.find(f"{_ATOM}title"), f"entry {index} title", required=True)
        assert atom_id is not None and title is not None
        if atom_id in seen_ids:
            raise GOVUKAtomError(f"entry {index} duplicates an Atom id")
        seen_ids.add(atom_id)
        folded_title = title.casefold()
        if folded_title in seen_titles:
            raise GOVUKAtomError(f"entry {index} duplicates a title")
        seen_titles.add(folded_title)
        links = [link for link in entry.findall(f"{_ATOM}link")
                 if link.get("rel", "alternate") == "alternate"]
        if len(links) != 1 or not links[0].get("href"):
            raise GOVUKAtomError(f"entry {index} must have one alternate article link")
        url = canonicalize_article_url(links[0].get("href", ""))
        if url in seen_urls:
            raise GOVUKAtomError(f"entry {index} duplicates a canonical article URL")
        seen_urls.add(url)
        published = _timestamp(_text(entry.find(f"{_ATOM}published"), f"entry {index} published", required=False),
                               f"entry {index} published")
        updated = _timestamp(_text(entry.find(f"{_ATOM}updated"), f"entry {index} updated", required=False),
                             f"entry {index} updated")
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
        publications.append(Publication(atom_id, title, url, f"{SOURCE_ID}:{digest}", published, updated))
    return PublicationFeed(tuple(publications), collected)


def ingest_payload(connection: sqlite3.Connection, xml_payload: bytes | str, *,
                   collected_at: str | datetime) -> IngestResult:
    """Import supplied XML via existing SQLite core in one atomic batch."""
    collection_time = _collected_timestamp(collected_at)
    if connection.in_transaction:
        raise GOVUKAtomError("ingest_payload requires no caller-owned active transaction")
    if connection.execute("SELECT 1 FROM sources WHERE id=?", (SOURCE_ID,)).fetchone() is None:
        create_source(connection, SOURCE_ID, SOURCE_NAME, GOVUK_HOME_URL)
    run_id = start_ingest_run(connection, SOURCE_ID, collection_time)
    try:
        feed = parse_feed(xml_payload, collected_at=collection_time)
    except GOVUKAtomError as exc:
        finish_ingest_run(connection, run_id, "failed", finished_at=collection_time,
                          error_message="invalid GOV.UK Atom feed")
        raise exc

    inserted = 0
    try:
        with connection:
            connection.execute("BEGIN")
            for publication in feed.publications:
                _, was_inserted = add_event(
                    connection, source_id=SOURCE_ID, ingest_run_id=run_id,
                    original_url=publication.original_url, dedup_key=publication.dedup_key,
                    title=publication.title, occurred_at=None,
                    published_at=publication.published_at, collected_at=collection_time,
                    country=None, latitude=None, longitude=None, geo_precision="unknown",
                    geo_confidence=None, validation_status="pending", summary=None,
                )
                inserted += int(was_inserted)
            status = "empty" if not feed.publications else "ok"
            finish_ingest_run(connection, run_id, status, finished_at=collection_time,
                              items_seen=len(feed.publications), items_inserted=inserted)
    except (sqlite3.Error, TypeError, ValueError) as exc:
        finish_ingest_run(connection, run_id, "failed", finished_at=collection_time,
                          error_message="GOV.UK Atom metadata could not be stored")
        raise GOVUKAtomError("GOV.UK Atom metadata could not be stored") from exc
    return IngestResult(run_id, status, len(feed.publications), inserted, feed.publications)
