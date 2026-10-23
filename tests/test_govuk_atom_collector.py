"""Fully synthetic HTTP transport tests. Never open a real network connection."""

from __future__ import annotations

from datetime import datetime, timezone
from email.message import Message
from pathlib import Path
from unittest import TestCase, mock
import sys
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from horizont.adapters import govuk_atom_collector as collector  # noqa: E402
from horizont.adapters.govuk_atom import MAX_XML_BYTES, SOURCE_ID  # noqa: E402

COLLECTED_AT = datetime(2026, 10, 23, 15, 0, tzinfo=timezone.utc)
NAMESPACE = "http://www.w3.org/2005/Atom"


def _entry(number: int, url: str | None = None) -> str:
    return (
        f"<entry><id>urn:fictional:{number}</id>"
        f"<title>Fictional official notice {number}</title>"
        f'<link rel="alternate" href="{url or "https://example.test/notices/" + str(number)}"/>'
        f"<published>2026-10-23T08:00:00Z</published></entry>"
    )


def _feed(number: int = 20) -> bytes:
    items = "".join(_entry(index) for index in range(number))
    return (
        f'<feed xmlns="{NAMESPACE}"><id>urn:fictional:feed</id>'
        f"<title>Fictional feed</title><updated>2026-10-23T08:00:00Z</updated>"
        f"{items}</feed>"
    ).encode("utf-8")


class FakeResponse:
    def __init__(
        self,
        body: bytes = b"",
        *,
        status: int = 200,
        url: str = collector.ATOM_FEED_URL,
        media_type: str = "application/atom+xml",
        encoding: str = "identity",
        content_length: str | None = "auto",
    ):
        self.status = status
        self.url = url
        self.body = body
        self.headers = Message()
        self.headers["Content-Type"] = media_type
        self.headers["Content-Encoding"] = encoding
        if content_length == "auto":
            content_length = str(len(body))
        if content_length is not None:
            self.headers["Content-Length"] = content_length
        self.reads: list[int] = []

    def geturl(self):
        return self.url

    def read(self, limit: int):
        self.reads.append(limit)
        return self.body[:limit]

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class FakeOpener:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def open(self, request, *, timeout: int):
        self.calls.append((request, timeout))
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


class GOVUKAtomCollectorTests(TestCase):
    def _exercise(self, result, *, collected_at=COLLECTED_AT):
        opener = FakeOpener(result)
        with mock.patch.object(collector, "_build_opener", return_value=opener):
            try:
                data = collector.collect_once(
                    collected_at=collected_at, permit_network=True
                )
            except collector.GOVUKCollectorError as exc:
                data = exc
        return data, opener

    def test_opt_in_guard_blocks_without_any_opener(self):
        with mock.patch.object(collector, "_build_opener") as creator:
            with self.assertRaises(collector.GOVUKCollectorError) as error:
                collector.collect_once(collected_at=COLLECTED_AT)
            self.assertEqual(error.exception.reason, "network_approval_required")
            creator.assert_not_called()

    def test_invalid_collection_timestamp_rejects_before_network(self):
        with mock.patch.object(collector, "_build_opener") as creator:
            with self.assertRaises(collector.GOVUKCollectorError) as error:
                collector.collect_once(
                    collected_at="2026-10-23T15:00:00", permit_network=True
                )
            self.assertEqual(error.exception.reason, "invalid_collection_time")
            creator.assert_not_called()

    def test_exactly_one_request_and_all_twenty_unverified_publications(self):
        response = FakeResponse(_feed(20))
        parsed, opener = self._exercise(response)
        self.assertEqual(len(parsed.publications), 20)
        self.assertEqual(parsed.publications[0].source_id, SOURCE_ID)
        self.assertTrue(
            all(
                item.validation_status == "pending"
                and item.occurred_at is None
                and item.country is None
                and item.latitude is None
                and item.longitude is None
                and item.geo_precision == "unknown"
                for item in parsed.publications
            )
        )
        self.assertEqual(len(opener.calls), 1)
        request, timeout = opener.calls[0]
        self.assertEqual(request.full_url, collector.ATOM_FEED_URL)
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(timeout, collector.REQUEST_TIMEOUT_SECONDS)
        self.assertEqual(request.get_header("Accept-encoding"), "identity")
        self.assertEqual(request.get_header("Accept"), "application/atom+xml")
        self.assertEqual(response.reads, [MAX_XML_BYTES + 1])

    def test_response_without_content_length_is_accepted(self):
        parsed, opener = self._exercise(
            FakeResponse(_feed(2), content_length=None)
        )
        self.assertEqual(len(parsed.publications), 2)
        self.assertEqual(len(opener.calls), 1)

    def test_429_500_and_redirect_http_errors_never_retry_or_read_body(self):
        for status in (301, 302, 429, 500):
            with self.subTest(status=status):
                error = urllib.error.HTTPError(
                    collector.ATOM_FEED_URL, status, "do not expose this", {}, None
                )
                actual, opener = self._exercise(error)
                self.assertIsInstance(actual, collector.GOVUKCollectorError)
                self.assertEqual(actual.reason, "http_error_or_redirect")
                self.assertNotIn("do not expose", str(actual))
                self.assertEqual(len(opener.calls), 1)

    def test_failed_transport_never_exposes_url_or_credential(self):
        actual, opener = self._exercise(
            urllib.error.URLError("secret=top-secret transport failure")
        )
        self.assertEqual(actual.reason, "transport_error")
        self.assertNotIn("secret", str(actual))
        self.assertEqual(len(opener.calls), 1)

    def test_http_status_and_final_url_rejection_precede_body_read(self):
        cases = [
            (FakeResponse(_feed(1), status=204), "unexpected_http_status"),
            (FakeResponse(_feed(1), url="https://evil.example/redirect"),
             "unexpected_response_url"),
        ]
        for response, reason in cases:
            with self.subTest(reason=reason):
                actual, opener = self._exercise(response)
                self.assertEqual(actual.reason, reason)
                self.assertEqual(response.reads, [])
                self.assertEqual(len(opener.calls), 1)

    def test_reject_media_type_encoding_and_content_length_pre_read(self):
        cases = [
            (FakeResponse(_feed(1), media_type="text/html"), "unexpected_media_type"),
            (FakeResponse(_feed(1), encoding="gzip"), "unexpected_content_encoding"),
            (FakeResponse(_feed(1), content_length="-1"), "invalid_content_length"),
            (FakeResponse(_feed(1), content_length="infinite"), "invalid_content_length"),
            (FakeResponse(_feed(1), content_length="131073"), "response_too_large"),
        ]
        for response, reason in cases:
            with self.subTest(reason=reason):
                actual, opener = self._exercise(response)
                self.assertEqual(actual.reason, reason)
                self.assertEqual(response.reads, [])
                self.assertEqual(len(opener.calls), 1)

    def test_actual_oversized_body_or_short_body_rejected(self):
        responses = (
            (FakeResponse(b"x" * (MAX_XML_BYTES + 1), content_length=None),
             "response_too_large"),
            (FakeResponse(_feed(1), content_length="999"),
             "content_length_mismatch"),
        )
        for response, reason in responses:
            with self.subTest(reason=reason):
                actual, opener = self._exercise(response)
                self.assertEqual(actual.reason, reason)
                self.assertEqual(response.reads, [MAX_XML_BYTES + 1])
                self.assertEqual(len(opener.calls), 1)

    def test_malformed_xml_dtd_and_twenty_one_entries_fail_closed(self):
        cases = (
            b"<broken",
            b'<!DOCTYPE feed [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
            + _feed(1),
            _feed(21),
        )
        for payload in cases:
            with self.subTest(length=len(payload)):
                actual, opener = self._exercise(FakeResponse(payload))
                self.assertEqual(actual.reason, "atom_contract_rejected")
                self.assertEqual(len(opener.calls), 1)

    def test_default_transport_disables_proxy_and_redirects_and_verifies_tls(self):
        # This only instantiates the opener; it never calls opener.open().
        with mock.patch.object(collector.ssl, "create_default_context") as tls:
            opener = collector._build_opener()
        tls.assert_called_once_with()
        self.assertFalse(
            any(
                isinstance(handler, collector.urllib.request.ProxyHandler)
                and handler.proxies
                for handler in opener.handlers
            )
        )
        redirects = [
            handler for handler in opener.handlers
            if isinstance(handler, collector._RejectRedirects)
        ]
        self.assertEqual(len(redirects), 1)
        self.assertIsNone(
            redirects[0].redirect_request(None, None, 302, "", None, "https://evil.example")
        )


if __name__ == "__main__":
    import unittest
    unittest.main()
