"""Offline, bounded GDELT 2.0 raw-feed manifest and ZIP/TSV validator.

No networking, scheduling, SQLite writes, or ZIP extraction happens here.
The caller supplies manifest text and an existing local archive file.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import os
from pathlib import Path
import re
import stat
import zipfile
from urllib.parse import urlsplit, urlunsplit

SOURCE_HOST = "data.gdeltproject.org"
KINDS = ("events", "mentions", "gkg")
SUFFIXES = {
    "events": "export.CSV",
    "mentions": "mentions.CSV",
    "gkg": "gkg.csv",
}
MAX_MANIFEST_BYTES = 4096
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
DEFAULT_MAX_UNCOMPRESSED = 8 * 1024 * 1024
MAX_UNCOMPRESSED_CAP = 128 * 1024 * 1024
MAX_LINE_BYTES = 32 * 1024
DEFAULT_MAX_ROWS = 5000
MAX_COMPRESSION_RATIO = 200


class GDELTRawError(ValueError):
    """A supplied GDELT raw manifest or ZIP violates our offline contract."""


@dataclass(frozen=True)
class RawEntry:
    kind: str
    timestamp: str
    stream: str
    compressed_bytes: int
    md5_hex: str
    url: str


@dataclass(frozen=True)
class RawManifest:
    stream: str
    timestamp: str
    entries: tuple[RawEntry, RawEntry, RawEntry]


def _validate_timestamp(value: str) -> None:
    if not re.fullmatch(r"\d{14}", value):
        raise GDELTRawError("invalid GDELT timestamp")
    try:
        moment = datetime.strptime(value, "%Y%m%d%H%M%S")
    except ValueError as exc:
        raise GDELTRawError("invalid GDELT calendar timestamp") from exc
    if moment.minute % 15 or moment.second:
        raise GDELTRawError("GDELT raw boundary must be a 15-minute timestamp")


def parse_manifest(
    content: str | bytes,
    *,
    stream: str = "english",
    last_timestamp: str | None = None,
) -> RawManifest:
    """Parse exactly one offline GDELT v2 manifest (3 archives, one boundary).

    Legacy HTTP archive links are upgraded to HTTPS *as data*, never fetched.
    The official link pattern is validated strictly; userinfo, ports, query
    parameters and all unknown hosts/paths are rejected.
    """
    if stream not in {"english", "translation"}:
        raise GDELTRawError("unsupported raw feed stream")
    if last_timestamp is not None:
        _validate_timestamp(last_timestamp)
    if not isinstance(content, (str, bytes)):
        raise GDELTRawError("manifest must be text or bytes")
    if isinstance(content, bytes):
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise GDELTRawError("manifest must be UTF-8") from exc
    else:
        text = content
    if len(text.encode("utf-8")) > MAX_MANIFEST_BYTES:
        raise GDELTRawError("manifest exceeds size limit")
    lines = text.strip().splitlines()
    if len(lines) != 3:
        raise GDELTRawError("manifest must contain exactly three lines")
    found: dict[str, RawEntry] = {}
    for line in lines:
        fields = line.split()
        if len(fields) != 3:
            raise GDELTRawError("manifest entry must have size, MD5, URL")
        size_text, digest, given_url = fields
        if not re.fullmatch(r"[0-9]{1,10}", size_text):
            raise GDELTRawError("manifest archive size is invalid")
        size = int(size_text)
        if not 0 < size <= MAX_ARCHIVE_BYTES:
            raise GDELTRawError("manifest archive exceeds compressed size budget")
        if not re.fullmatch(r"[0-9a-fA-F]{32}", digest):
            raise GDELTRawError("manifest MD5 is invalid")
        try:
            parts = urlsplit(given_url)
        except ValueError as exc:
            raise GDELTRawError("manifest archive URL is invalid") from exc
        if (
            parts.scheme not in {"http", "https"}
            or parts.netloc != SOURCE_HOST
            or parts.query or parts.fragment or parts.username is not None
        ):
            raise GDELTRawError("manifest archive URL is not allowlisted")
        match = re.fullmatch(
            r"/gdeltv2/(\d{14})(\.translation)?\.(export\.CSV|mentions\.CSV|gkg\.csv)\.zip",
            parts.path,
        )
        if not match:
            raise GDELTRawError("manifest archive path is not allowlisted")
        timestamp, translation, suffix = match.groups()
        _validate_timestamp(timestamp)
        if bool(translation) != (stream == "translation"):
            raise GDELTRawError("manifest stream does not match archive")
        kind = next((k for k, v in SUFFIXES.items() if v == suffix), None)
        if kind is None or kind in found:
            raise GDELTRawError("manifest has duplicate or unsupported archive kinds")
        canonical = urlunsplit(("https", SOURCE_HOST, parts.path, "", ""))
        found[kind] = RawEntry(kind, timestamp, stream, size, digest.lower(), canonical)
    if set(found) != set(KINDS):
        raise GDELTRawError("manifest must include Events, Mentions and GKG")
    times = {entry.timestamp for entry in found.values()}
    if len(times) != 1:
        raise GDELTRawError("manifest entries must share a 15-minute boundary")
    timestamp = times.pop()
    if last_timestamp is not None and timestamp <= last_timestamp:
        raise GDELTRawError("stale or replayed GDELT manifest")
    return RawManifest(stream, timestamp, tuple(found[k] for k in KINDS))


def parse_zip_rows(
    archive_path: str | Path,
    entry: RawEntry,
    *,
    expected_columns: int | None = None,
    max_uncompressed_bytes: int = DEFAULT_MAX_UNCOMPRESSED,
    max_rows: int = DEFAULT_MAX_ROWS,
) -> tuple[tuple[str, ...], ...]:
    """Validate a *local* ZIP, then return bounded UTF-8 tabular rows.

    This prototype intentionally does not extract files or interpret raw event
    columns. Later production ingestion will stream rows to a controlled
    destination; this research path caps rows and bytes to limit memory use.
    """
    if (
        not isinstance(max_uncompressed_bytes, int)
        or not 1 <= max_uncompressed_bytes <= MAX_UNCOMPRESSED_CAP
        or not isinstance(max_rows, int)
        or not 1 <= max_rows <= DEFAULT_MAX_ROWS
    ):
        raise GDELTRawError("invalid offline read limits")
    if expected_columns is not None and (
        type(expected_columns) is not int or not 1 <= expected_columns <= 80
    ):
        raise GDELTRawError("invalid expected column count")
    path = Path(archive_path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise GDELTRawError("archive must be an existing regular local file") from exc
    rows: list[tuple[str, ...]] = []
    with os.fdopen(fd, "rb") as handle:
        meta = os.fstat(handle.fileno())
        if not stat.S_ISREG(meta.st_mode):
            raise GDELTRawError("archive must be an existing regular local file")
        if meta.st_size != entry.compressed_bytes:
            raise GDELTRawError("ZIP byte count differs from manifest")
        if meta.st_size > MAX_ARCHIVE_BYTES:
            raise GDELTRawError("ZIP exceeds compressed size limit")
        md5 = hashlib.md5(usedforsecurity=False)
        while chunk := handle.read(65536):
            md5.update(chunk)
        if md5.hexdigest() != entry.md5_hex:
            raise GDELTRawError("ZIP MD5 mismatch (integrity, not authenticity)")
        handle.seek(0)
        expected_name = entry.url.rsplit("/", 1)[-1][:-4]
        try:
            # Keep the same validated descriptor: never reopen the pathname
            # between checksum verification and ZIP parsing (TOCTOU).
            with zipfile.ZipFile(handle, "r") as archive:
                members = archive.infolist()
                if len(members) != 1:
                    raise GDELTRawError("archive must have exactly one member")
                member = members[0]
                if (
                    member.filename != expected_name
                    or member.is_dir()
                    or stat.S_ISLNK(member.external_attr >> 16)
                    or member.flag_bits & 1
                    or member.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
                ):
                    raise GDELTRawError("unsafe ZIP member name or metadata")
                if member.file_size > max_uncompressed_bytes:
                    raise GDELTRawError("ZIP expands beyond declared byte budget")
                if member.file_size > MAX_COMPRESSION_RATIO * max(1, member.compress_size):
                    raise GDELTRawError("ZIP compression ratio exceeds safety budget")
                total = 0
                with archive.open(member, "r") as raw:
                    while line := raw.readline(MAX_LINE_BYTES + 1):
                        total += len(line)
                        if len(line) > MAX_LINE_BYTES or total > max_uncompressed_bytes:
                            raise GDELTRawError("ZIP line or decompressed byte limit exceeded")
                        if len(rows) >= max_rows:
                            raise GDELTRawError("ZIP row limit exceeded")
                        try:
                            decoded = line.rstrip(b"\r\n").decode("utf-8")
                        except UnicodeDecodeError as exc:
                            raise GDELTRawError("ZIP contains invalid UTF-8") from exc
                        fields = tuple(decoded.split("\t"))
                        if not decoded or len(fields) > 80:
                            raise GDELTRawError("ZIP has invalid tab-delimited row")
                        if expected_columns is not None and len(fields) != expected_columns:
                            raise GDELTRawError("ZIP row has incorrect number of columns")
                        rows.append(fields)
        except (zipfile.BadZipFile, EOFError, RuntimeError, OSError) as exc:
            raise GDELTRawError("ZIP archive is corrupt or cannot be decoded") from exc
    return tuple(rows)
