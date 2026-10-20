"""Private, standard-library WSGI read-only API over an *existing* SQLite DB.

Does not create/migrate/write databases, fetch feeds or start background jobs.
The default listener is localhost only; HTTP is not an internet-facing API.
"""

from __future__ import annotations

import hmac
import json
import os
from pathlib import Path
import re
import sqlite3
from urllib.parse import parse_qs
from wsgiref.simple_server import make_server

from horizont.core.queries import (
    get_event_detail, list_recent_events, list_source_statuses, open_readonly,
)

STATUS = {
    200: "200 OK",
    400: "400 Bad Request",
    401: "401 Unauthorized",
    404: "404 Not Found",
    405: "405 Method Not Allowed",
    503: "503 Service Unavailable",
}
EVENT_PATH = re.compile(r"/api/v1/events/([1-9][0-9]{0,17})\Z")
EVENT_PARAMS = {"time_field", "start", "end", "source_id", "country", "limit", "offset"}
SOURCE_PARAMS = {"limit", "offset"}
MAX_QUERY_BYTES = 2048
MAX_AUTH_BYTES = 2048


def _number(value: str | None, default: int, minimum: int, maximum: int) -> int:
    if value is None:
        return default
    if not re.fullmatch(r"[0-9]{1,9}", value):
        raise ValueError("invalid numeric parameter")
    result = int(value)
    if not minimum <= result <= maximum:
        raise ValueError("numeric parameter outside allowed range")
    return result


def _params(raw: str, allowed: set[str]) -> dict[str, str]:
    if len(raw.encode("utf-8")) > MAX_QUERY_BYTES:
        raise ValueError("query exceeds size budget")
    values = parse_qs(raw, keep_blank_values=True, strict_parsing=True, max_num_fields=12)
    if set(values) - allowed or any(len(v) != 1 for v in values.values()):
        raise ValueError("unknown or duplicate query parameter")
    params = {key: val[0] for key, val in values.items()}
    if any(len(value) > 256 for value in params.values()):
        raise ValueError("query parameter exceeds length limit")
    return params


def create_app(database: str | Path, token: str):
    """Return a WSGI app; bearer required for all data and readiness endpoints.

    The database is opened fresh with mode=ro/query_only on each request.
    This app is not a write gateway, feed downloader, or browser-side API.
    """
    if not isinstance(token, str) or len(token) < 32 or len(token) > 1024:
        raise ValueError("a private bearer token of at least 32 chars is required")
    if not isinstance(database, (str, Path)) or not str(database):
        raise ValueError("existing database path is required")
    database_path = Path(database)

    def application(environ, start_response):
        method = environ.get("REQUEST_METHOD", "")
        path = environ.get("PATH_INFO", "")
        status = 200
        extra_headers: list[tuple[str, str]] = []
        payload: dict[str, object] = {}

        if method not in ("GET", "HEAD"):
            status, payload = 405, {"error": "method_not_allowed"}
            extra_headers.append(("Allow", "GET, HEAD"))
        elif path == "/health":
            payload = {"status": "running", "service": "horizont-readonly-api"}
        else:
            header = environ.get("HTTP_AUTHORIZATION", "")
            if (
                not isinstance(header, str) or len(header) > MAX_AUTH_BYTES
                or not header.startswith("Bearer ")
                or not hmac.compare_digest(header[7:], token)
            ):
                status, payload = 401, {"error": "unauthorized"}
                extra_headers.append(("WWW-Authenticate", 'Bearer realm="horizont"'))
            else:
                try:
                    raw = environ.get("QUERY_STRING", "")
                    if path == "/api/v1/events":
                        p = _params(raw, EVENT_PARAMS)
                        args = {
                            "time_field": p.get("time_field", "collected_at"),
                            "start": p.get("start"),
                            "end": p.get("end"),
                            "source_id": p.get("source_id"),
                            "country": p.get("country"),
                            "limit": _number(p.get("limit"), 50, 1, 100),
                            "offset": _number(p.get("offset"), 0, 0, 100000),
                        }
                        with open_readonly(database_path) as connection:
                            payload = list_recent_events(connection, **args)
                    elif match := EVENT_PATH.fullmatch(path):
                        _params(raw, set())
                        with open_readonly(database_path) as connection:
                            data = get_event_detail(connection, int(match.group(1)))
                        if data is None:
                            status, payload = 404, {"error": "not_found"}
                        else:
                            payload = data
                    elif path == "/api/v1/sources":
                        p = _params(raw, SOURCE_PARAMS)
                        limit = _number(p.get("limit"), 50, 1, 100)
                        offset = _number(p.get("offset"), 0, 0, 100000)
                        with open_readonly(database_path) as connection:
                            records = list_source_statuses(connection)
                        payload = {"items": records[offset:offset + limit],
                                   "limit": limit, "offset": offset}
                    elif path == "/ready":
                        _params(raw, set())
                        with open_readonly(database_path) as connection:
                            connection.execute("SELECT id FROM sources LIMIT 1").fetchall()
                        payload = {"status": "ready"}
                    else:
                        status, payload = 404, {"error": "not_found"}
                except (ValueError, TypeError, UnicodeError):
                    status, payload = 400, {"error": "invalid_request"}
                except (OSError, sqlite3.Error):
                    status, payload = 503, {"error": "data_unavailable"}

        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
        headers = [
            ("Content-Type", "application/json; charset=utf-8"),
            ("Content-Length", str(len(body))),
            ("Cache-Control", "no-store"),
            ("X-Content-Type-Options", "nosniff"),
            ("Referrer-Policy", "no-referrer"),
            ("Content-Security-Policy", "default-src 'none'"),
            *extra_headers,
        ]
        start_response(STATUS[status], headers)
        return [b"" if method == "HEAD" else body]

    return application


def main() -> None:
    """Explicit local-only run mode; never started by importing this module."""
    secret = os.environ.get("HORIZONT_API_TOKEN", "")
    database = os.environ.get("HORIZONT_API_DB", "")
    if not database or len(secret) < 32:
        raise SystemExit("Refusing startup: database and private bearer token required")
    app = create_app(database, secret)
    # Only loopback. An explicit, separately reviewed Docker configuration
    # will be required before this is ever containerized or exposed.
    with make_server("127.0.0.1", 15481, app) as server:
        print("HORIZONT read-only API on local loopback (not a public service)")
        server.serve_forever()


if __name__ == "__main__":
    main()
