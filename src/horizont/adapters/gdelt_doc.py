"""Offline normalizer and bounded URL builder for GDELT Project DOC 2.0.

This module never performs HTTP requests. It accepts caller-supplied JSON and
returns publisher URLs as provenance; it does not fetch those URLs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import sqlite3
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit

from ..core.db import (
    add_event,
    create_source,
    finish_ingest_run,
    normalize_utc,
    start_ingest_run,
)

SOURCE_ID = "gdelt-project-doc-v2"
SOURCE_NAME = "GDELT Project DOC 2.0"
GDELT_PROJECT_URL = "https://www.gdeltproject.org/"
DOC_ENDPOINT = "https://api.gdeltproject.org/api/v2/doc/doc"
MAX_RECORDS = 10


class GDELTDocError(ValueError):
    """A supplied request or synthetic response violates this adapter contract."""


@dataclass(frozen=True)
class Article:
    """Safe article metadata; this represents an unverified news mention."""

    title: str
    original_url: str
    dedup_key: str
    language: str | None
    source_country_label: str | None
    gdelt_seen_at: str | None
    source_id: str = SOURCE_ID
    attribution: str = "GDELT Project"
    attribution_url: str = GDELT_PROJECT_URL
    occurred_at: None = None
    published_at: None = None
    country: None = None
    latitude: None = None
    longitude: None = None
    geo_precision: str = "unknown"
    geo_confidence: None = None
    validation_status: str = "pending"


@dataclass(frozen=True)
class ArticleBatch:
    articles: tuple[Article, ...]
    possibly_truncated: bool


@dataclass(frozen=True)
class IngestResult:
    run_id: int
    status: str
    items_seen: int
    items_inserted: int
    possibly_truncated: bool
    articles: tuple[Article, ...]


def _doc_timestamp(value: str | datetime, name: str) -> tuple[str, str]:
    try:
        normalized = normalize_utc(value, required=True)
    except (TypeError, ValueError) as exc:
        raise GDELTDocError(f"{name} must be a timezone-aware ISO-8601 timestamp") from exc
    parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    if parsed.microsecond:
        raise GDELTDocError(f"{name} must have whole-second precision")
    return normalized, parsed.strftime("%Y%m%d%H%M%S")


def build_search_url(
    query: str,
    start: str | datetime,
    end: str | datetime,
    *,
    max_records: int = MAX_RECORDS,
) -> str:
    """Build a bounded DOC API 2.0 URL; does not connect to the endpoint."""
    if not isinstance(query, str) or not query.strip():
        raise GDELTDocError("query must be a non-empty search term")
    if len(query) > 500:
        raise GDELTDocError("query exceeds 500 characters")
    if type(max_records) is not int or not 1 <= max_records <= MAX_RECORDS:
        raise GDELTDocError(f"max_records must be from 1 to {MAX_RECORDS}")
    start_utc, start_value = _doc_timestamp(start, "start")
    end_utc, end_value = _doc_timestamp(end, "end")
    if start_utc >= end_utc:
        raise GDELTDocError("search time range must satisfy start < end")
    parameters = urlencode(
        {
            "query": query.strip(),
            "mode": "artlist",
            "format": "json",
            "startdatetime": start_value,
            "enddatetime": end_value,
            "maxrecords": max_records,
        }
    )
    return f"{DOC_ENDPOINT}?{parameters}"


def canonicalize_article_url(value: str) -> str:
    """Validate an absolute HTTP(S) publisher URL, dropping only its fragment."""
    if not isinstance(value, str) or not value or value != value.strip():
        raise GDELTDocError("article URL must be a non-empty absolute HTTP(S) URL")
    if any(ord(char) < 32 or char.isspace() for char in value):
        raise GDELTDocError("article URL must not contain whitespace or control characters")
    try:
        parts = urlsplit(value)
        hostname = parts.hostname
        port = parts.port
    except ValueError as exc:
        raise GDELTDocError("article URL has an invalid host or port") from exc
    if parts.scheme.lower() not in {"http", "https"} or not hostname:
        raise GDELTDocError("article URL must be an absolute HTTP(S) URL")
    if parts.username is not None or parts.password is not None:
        raise GDELTDocError("article URL must not contain user information")
    try:
        host = hostname.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise GDELTDocError("article URL host is invalid") from exc
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    scheme = parts.scheme.lower()
    if port is not None and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        host = f"{host}:{port}"
    return urlunsplit((scheme, host, parts.path or "/", parts.query, ""))


def _seen_timestamp(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise GDELTDocError("article seendate must be a DOC timestamp string or null")
    try:
        parsed = datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise GDELTDocError("article seendate is invalid") from exc
    return normalize_utc(parsed, required=True)


def normalize_response(payload: dict[str, Any] | str | bytes, *, max_records: int = MAX_RECORDS) -> ArticleBatch:
    """Validate and normalize a synthetic DOC ``mode=artlist&format=json`` payload.

    Any malformed record, duplicate canonical URL, or over-limit batch rejects
    the whole payload. A valid empty ``articles`` list remains an empty result.
    ``seendate`` is retained separately as GDELT discovery time, never mapped to
    publisher ``published_at`` or event ``occurred_at``.
    """
    if type(max_records) is not int or not 1 <= max_records <= MAX_RECORDS:
        raise GDELTDocError(f"max_records must be from 1 to {MAX_RECORDS}")
    if isinstance(payload, (str, bytes)):
        try:
            payload = json.loads(payload)
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError) as exc:
            raise GDELTDocError("DOC response is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise GDELTDocError("DOC response must be a JSON object")
    records = payload.get("articles")
    if not isinstance(records, list):
        raise GDELTDocError("DOC response must contain an articles array")
    if len(records) > max_records:
        raise GDELTDocError("DOC response exceeds the requested max_records")

    articles: list[Article] = []
    seen_urls: set[str] = set()
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise GDELTDocError(f"article {index} must be an object")
        title = record.get("title")
        if not isinstance(title, str) or not title.strip():
            raise GDELTDocError(f"article {index} is missing a title")
        canonical_url = canonicalize_article_url(record.get("url"))
        if canonical_url in seen_urls:
            raise GDELTDocError(f"article {index} duplicates a canonical URL")
        seen_urls.add(canonical_url)
        language = record.get("language")
        if language is not None and (not isinstance(language, str) or not 2 <= len(language.strip()) <= 35):
            raise GDELTDocError(f"article {index} has an invalid language")
        source_country = record.get("sourcecountry")
        if source_country is not None and not isinstance(source_country, str):
            raise GDELTDocError(f"article {index} has an invalid source-country label")
        article_id = hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()
        articles.append(
            Article(
                title=title.strip(),
                original_url=canonical_url,
                dedup_key=f"{SOURCE_ID}:{article_id}",
                language=language.strip() if language else None,
                source_country_label=source_country.strip() if source_country else None,
                gdelt_seen_at=_seen_timestamp(record.get("seendate")),
            )
        )
    return ArticleBatch(tuple(articles), possibly_truncated=len(records) == max_records)


def ingest_payload(
    connection: sqlite3.Connection,
    payload: dict[str, Any] | str | bytes,
    *,
    collected_at: str | datetime,
    max_records: int = MAX_RECORDS,
) -> IngestResult:
    """Record a supplied payload using the existing SQLite core; performs no fetch."""
    collection_time = normalize_utc(collected_at, required=True)
    if connection.in_transaction:
        raise GDELTDocError("ingest_payload requires no caller-owned active transaction")
    if connection.execute("SELECT 1 FROM sources WHERE id=?", (SOURCE_ID,)).fetchone() is None:
        create_source(connection, SOURCE_ID, SOURCE_NAME, GDELT_PROJECT_URL)
    run_id = start_ingest_run(connection, SOURCE_ID, collection_time)
    try:
        batch = normalize_response(payload, max_records=max_records)
    except GDELTDocError as exc:
        finish_ingest_run(
            connection,
            run_id,
            "failed",
            finished_at=collection_time,
            error_message="invalid GDELT DOC response",
        )
        raise exc

    inserted = 0
    try:
        # Legacy sqlite3's nested `with connection` helpers commit a caller's
        # transaction unless they explicitly respect an active transaction.
        # add_event() and finish_ingest_run() now do so, allowing this BEGIN
        # to cover every event and the successful run finalization.
        with connection:
            connection.execute("BEGIN")
            for article in batch.articles:
                _, was_inserted = add_event(
                    connection,
                    source_id=SOURCE_ID,
                    ingest_run_id=run_id,
                    original_url=article.original_url,
                    dedup_key=article.dedup_key,
                    title=article.title,
                    occurred_at=None,
                    published_at=None,
                    collected_at=collection_time,
                    language=article.language,
                    country=None,
                    latitude=None,
                    longitude=None,
                    geo_precision="unknown",
                    geo_confidence=None,
                    validation_status="pending",
                )
                inserted += int(was_inserted)
            status = "empty" if not batch.articles else "ok"
            finish_ingest_run(
                connection,
                run_id,
                status,
                finished_at=collection_time,
                items_seen=len(batch.articles),
                items_inserted=inserted,
            )
    except (sqlite3.Error, TypeError, ValueError) as exc:
        # The context above rolled back *all* event and provenance writes.
        # Mark only the previously committed run record as failed.
        finish_ingest_run(
            connection,
            run_id,
            "failed",
            finished_at=collection_time,
            error_message="GDELT DOC metadata could not be stored",
        )
        raise GDELTDocError("GDELT DOC metadata could not be stored") from exc
    return IngestResult(run_id, status, len(batch.articles), inserted, batch.possibly_truncated, batch.articles)
