"""Opt-in, bounded GOV.UK Atom HTTPS transport; no scheduler or persistence.

This module is NOT enabled by an import. The caller must explicitly opt in to
one network request. The transport is separate from the offline parser and
never imports, calls, or schedules a SQLite importer.

A per-socket timeout is not an overall wall-clock deadline; any future
production runner must enforce a separate process-level deadline and approval.
"""

from __future__ import annotations

from datetime import datetime
from email.message import Message
import http.client
import re
import ssl
import urllib.error
import urllib.request

from .govuk_atom import (
    GOVUKAtomError,
    MAX_XML_BYTES,
    PublicationFeed,
    _collected_timestamp,
    parse_feed,
)

ATOM_FEED_URL = "https://www.gov.uk/search/news-and-communications.atom"
REQUEST_TIMEOUT_SECONDS = 8
USER_AGENT = (
    "POLISH-HORIZONT-OSINT-LAB/1.0 "
    "(one-shot opt-in; github.com/mksolutionsuniversal/polish-horizont)"
)
_CONTENT_LENGTH = re.compile(r"[0-9]{1,10}\Z")


class GOVUKCollectorError(RuntimeError):
    """A bounded error category without raw feed content, URLs or credentials."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _build_opener() -> urllib.request.OpenerDirector:
    """Verify TLS, ignore ambient proxy variables, and reject redirects."""
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _RejectRedirects(),
        urllib.request.HTTPSHandler(context=ssl.create_default_context()),
    )


def collect_once(
    *, collected_at: str | datetime, permit_network: bool = False
) -> PublicationFeed:
    """Fetch exactly one official Atom response on explicit permission.

    This call returns parsed, unverified publication metadata *only in memory*.
    No retries, article dereferencing, output files, logging, database writes,
    service setup or scheduled polling are performed. The caller must not infer
    incidents, location or occurrence dates from publication metadata.

    The opt-in boolean is a guard against accidental calls, not a substitute
    for operator approval and deployment controls.
    """
    if permit_network is not True:
        raise GOVUKCollectorError("network_approval_required")
    try:
        collected = _collected_timestamp(collected_at)
    except GOVUKAtomError:
        raise GOVUKCollectorError("invalid_collection_time") from None

    request = urllib.request.Request(
        ATOM_FEED_URL,
        method="GET",
        headers={
            "Accept": "application/atom+xml",
            "Accept-Encoding": "identity",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        opener = _build_opener()
        with opener.open(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            if response.status != 200:
                raise GOVUKCollectorError("unexpected_http_status")
            if response.geturl() != ATOM_FEED_URL:
                raise GOVUKCollectorError("unexpected_response_url")

            headers: Message = response.headers
            if headers.get_content_type() != "application/atom+xml":
                raise GOVUKCollectorError("unexpected_media_type")
            if headers.get("Content-Encoding", "identity").strip().lower() != "identity":
                raise GOVUKCollectorError("unexpected_content_encoding")

            declared_size = headers.get("Content-Length")
            if declared_size is not None:
                if not _CONTENT_LENGTH.fullmatch(declared_size):
                    raise GOVUKCollectorError("invalid_content_length")
                if int(declared_size) > MAX_XML_BYTES:
                    raise GOVUKCollectorError("response_too_large")

            # One bounded read, never read() without a size or download links.
            body = response.read(MAX_XML_BYTES + 1)
            if not isinstance(body, bytes):
                raise GOVUKCollectorError("invalid_response_body")
            if len(body) > MAX_XML_BYTES:
                raise GOVUKCollectorError("response_too_large")
            if declared_size is not None and len(body) != int(declared_size):
                raise GOVUKCollectorError("content_length_mismatch")
    except GOVUKCollectorError:
        raise
    except urllib.error.HTTPError:
        # Includes 3xx (redirects refused) and 429; never retry.
        raise GOVUKCollectorError("http_error_or_redirect") from None
    except (http.client.HTTPException, urllib.error.URLError, OSError, TimeoutError,
            ValueError, TypeError, AttributeError):
        # Do not echo transport exceptions (they may contain URLs or headers).
        raise GOVUKCollectorError("transport_error") from None

    try:
        return parse_feed(body, collected_at=collected)
    except GOVUKAtomError:
        # Do not echo real Atom title, URL, or provider data in diagnostics.
        raise GOVUKCollectorError("atom_contract_rejected") from None
