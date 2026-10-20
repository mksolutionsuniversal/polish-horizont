"""Optional same-origin fictional SQLite dashboard laboratory.

This app deliberately does NOT connect to real/private HORIZONT databases.
Only the baked synthetic demo SQLite image is supported. It runs independently
from existing 15480 preview and 15481 bearer API projects.
"""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlsplit
from wsgiref.simple_server import make_server

from horizont.api.readonly import BoundedThreadingWSGIServer
from horizont.core.queries import list_recent_events, list_source_statuses, open_readonly

ROOT = Path(__file__).resolve().parents[3]
MAX_BODY_BYTES = 256_000
_ALLOWED_HOSTS = frozenset({"127.0.0.1:15482", "localhost:15482"})
_ALLOWED_STATIC = {"/app.js": ("app.js", "text/javascript; charset=utf-8"),
                   "/styles.css": ("styles.css", "text/css; charset=utf-8")}
STATUS = {200: "200 OK", 400: "400 Bad Request", 404: "404 Not Found",
          405: "405 Method Not Allowed", 503: "503 Service Unavailable"}


def _safe_json(value: object) -> bytes:
    """Script-safe JSON that cannot terminate a non-executable JSON script tag."""
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            .replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
            .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")).encode("utf-8")


def read_snapshot(database: str | Path) -> dict[str, object]:
    """A single consistent SQLite snapshot for event list, provenance and sources."""
    with closing(open_readonly(database)) as conn:
        conn.execute("BEGIN")
        try:
            events = list_recent_events(conn, limit=50, time_field="collected_at")["items"]
            ids = [row["id"] for row in events]
            relations: dict[int, list[dict[str, object]]] = {i: [] for i in ids}
            if ids:
                placeholders = ",".join("?" for _ in ids)
                rows = conn.execute(
                    "SELECT es.event_id,s.id AS source_id,s.name AS source_name,"
                    "es.original_url,es.language,es.country "
                    "FROM event_sources es JOIN sources s ON s.id=es.source_id "
                    f"WHERE es.event_id IN ({placeholders}) "
                    "ORDER BY es.event_id,s.id", ids).fetchall()
                for row in rows:
                    relations[row["event_id"]].append({
                        "source_id": row["source_id"], "source_name": row["source_name"],
                        "original_url": row["original_url"], "language": row["language"],
                        "country": row["country"],
                    })
            sources = list_source_statuses(conn)
        finally:
            conn.rollback()

    snapshot_events = []
    for e in events:
        # No event-level source language: dedup'd event title language is unknown.
        snapshot_events.append({
            "id": e["id"], "title": e["title"], "summary": e["summary"],
            "occurred_at": e["occurred_at"], "published_at": e["published_at"],
            "collected_at": e["collected_at"], "validation_status": e["validation_status"],
            "geo_precision": e["geo_precision"], "latitude": e["latitude"],
            "longitude": e["longitude"], "sources": relations[e["id"]],
        })
    return {"synthetic": True, "as_of": "2026-10-20T12:00:00Z",
            "items": snapshot_events, "sources": sources, "limit": 50,
            "offset": 0, "country_filter_semantics": "source_observed_country"}


def _preview_fixture(snapshot: dict[str, object]) -> dict[str, object]:
    events = []
    for event in snapshot["items"]:
        provenance = []
        for source in event["sources"]:
            provenance.append({
                "id": source["source_id"], "name": source["source_name"],
                "url": source["original_url"], "language": source["language"],
                "country": source["country"],
            })
        events.append({
            "id": f"sql-{event['id']}", "title": {"pl": event["title"], "en": event["title"]},
            "summary": {"pl": event["summary"] or "", "en": event["summary"] or ""},
            "occurred_at": event["occurred_at"], "published_at": event["published_at"],
            "collected_at": event["collected_at"], "validation_status": event["validation_status"],
            "geo_precision": event["geo_precision"], "sources": provenance,
        })
    sources = []
    for source in snapshot["sources"]:
        status = source["source_status"]
        sources.append({
            "id": source["source_id"], "name": source["source_name"],
            "status": status,
            # These are localized STATUS explanations, not invented fetch outcomes.
            "detail": {"pl": "Próbka fikcyjna z lokalnej SQLite — status źródła: " + status + ".",
                       "en": "Fictional local SQLite sample — source status: " + status + "."},
        })
    return {"synthetic": True, "as_of": snapshot["as_of"],
            "events": events, "sources": sources}


def _index(template: str, snapshot: dict[str, object]) -> bytes:
    marker = re.compile(
        r'(<script id="fixture-data" type="application/json">)(.*?)(</script>)',
        re.DOTALL,
    )
    fixture = _safe_json(_preview_fixture(snapshot)).decode("utf-8")
    if len(marker.findall(template)) != 1:
        raise ValueError("unexpected static preview template")
    rendered = marker.sub(lambda m: m.group(1) + fixture + m.group(3), template)
    return rendered.encode("utf-8")


def _demo_app_js(source: str) -> bytes:
    """Only copy labels change in the opt-in demo, no accepted 15480 UI edits."""
    replacements = (
        ("PODGLĄD · DANE SYNTETYCZNE", "DEMO SQLITE · FIKCYJNE DANE"),
        ("PREVIEW · SYNTHETIC DATA", "SQLITE DEMO · FICTIONAL DATA"),
        ("To statyczny podgląd z fikcyjnymi danymi. Nie łączy się z bazą SQLite, źródłami ani siecią.",
         "To odrębne demo czyta WYŁĄCZNIE fikcyjną SQLite z obrazu kontenera. Brak prawdziwego monitoringu i danych prywatnych."),
        ("This is a static preview with fictional data. It does not connect to SQLite, sources, or the network.",
         "This isolated demo reads ONLY fictional SQLite data baked into its container image. No live monitoring or private data."),
        ("POLISH HORIZONT — podgląd syntetyczny", "POLISH HORIZONT — demo SQLite"),
        ("POLISH HORIZONT — synthetic preview", "POLISH HORIZONT — SQLite demo"),
    )
    for old, new in replacements:
        if old not in source:
            raise ValueError("preview JS changed; review demo label mapping before deploy")
        source = source.replace(old, new)
    return source.encode("utf-8")


def _allowed_browser(environ) -> bool:
    host = environ.get("HTTP_HOST", "")
    if host not in _ALLOWED_HOSTS:
        return False
    if environ.get("HTTP_X_FORWARDED_HOST") or environ.get("HTTP_X_FORWARDED_PROTO"):
        return False
    origin = environ.get("HTTP_ORIGIN")
    if origin:
        p = urlsplit(origin)
        if p.scheme != "http" or p.netloc not in _ALLOWED_HOSTS or p.path not in ("", "/"):
            return False
    site = environ.get("HTTP_SEC_FETCH_SITE")
    if site and site not in ("same-origin", "none"):
        return False
    return True


def create_app(database: str | Path, preview_root: str | Path):
    db = Path(database)
    preview = Path(preview_root)

    def app(environ, start_response):
        method = environ.get("REQUEST_METHOD", "")
        path = environ.get("PATH_INFO", "")
        headers = [
            ("Cache-Control", "no-store"), ("X-Content-Type-Options", "nosniff"),
            ("Referrer-Policy", "no-referrer"),
            ("X-Frame-Options", "DENY"),
            ("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'"),
        ]
        code, typ, body = 200, "application/json; charset=utf-8", b""
        if not _allowed_browser(environ):
            code, body = 400, b'{"error":"invalid_host"}'
        elif method not in ("GET", "HEAD"):
            code, body = 405, b'{"error":"method_not_allowed"}'
            headers.append(("Allow", "GET, HEAD"))
        elif path == "/health":
            body = b'{"status":"running","synthetic":true}'
        elif path not in {"/", "/index.html", "/data/v1/events", "/data/v1/sources", *(_ALLOWED_STATIC)}:
            code, body = 404, b'{"error":"not_found"}'
        elif environ.get("QUERY_STRING"):
            code, body = 400, b'{"error":"unexpected_query"}'
        else:
            try:
                if path in _ALLOWED_STATIC:
                    file, typ = _ALLOWED_STATIC[path]
                    body = (preview / file).read_bytes()
                    if path == "/app.js":
                        body = _demo_app_js(body.decode("utf-8"))
                else:
                    snapshot = read_snapshot(db)
                    if path in ("/", "/index.html"):
                        typ = "text/html; charset=utf-8"
                        body = _index((preview / "index.html").read_text(encoding="utf-8"), snapshot)
                    elif path == "/data/v1/events":
                        body = _safe_json({k: v for k, v in snapshot.items() if k != "sources"})
                    else:
                        body = _safe_json({"synthetic": True, "items": snapshot["sources"]})
                if len(body) > MAX_BODY_BYTES:
                    raise ValueError("demo output too large")
            except (OSError, sqlite3.Error, ValueError, UnicodeError):
                code, typ, body = 503, "application/json; charset=utf-8", b'{"error":"data_unavailable"}'
        headers.extend([("Content-Type", typ), ("Content-Length", str(len(body)))])
        start_response(STATUS[code], headers)
        return [b"" if method == "HEAD" else body]

    return app


def main():
    import os
    db = os.environ.get("HORIZONT_UI_DEMO_DB", "/opt/horizont/data/synthetic.sqlite")
    app = create_app(db, "/opt/horizont/preview")
    with make_server("0.0.0.0", 15482, app, server_class=BoundedThreadingWSGIServer) as server:
        print("Fictional SQLite UI LAB listening; host port is restricted by Compose")
        server.serve_forever()


if __name__ == "__main__":
    main()
