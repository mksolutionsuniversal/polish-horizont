"""Codebook-grounded, offline projection of synthetic GDELT 2.0 Events rows.

This module decodes TSV records only. It performs no transport or SQLite writes.
The field order follows the GDELT 2.0 Event Database Codebook (V2.0), with
its ``Day`` field named ``SQLDATE`` as in the 61-column export header.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import re

from .gdelt_doc import GDELT_PROJECT_URL, SOURCE_ID, canonicalize_article_url
from ..core.db import normalize_utc

CODEBOOK_URL = "https://data.gdeltproject.org/documentation/GDELT-Event_Codebook-V2.0.pdf"
CODEBOOK_VERSION = "GDELT Event Database Codebook V2.0"
EVENTS_SOURCE_ID = "gdelt-raw-events-v2"
EXPECTED_COLUMNS = 61
MAX_EVENT_ID = (1 << 63) - 1

# Zero-based source positions (1-based codebook positions are these + 1).
# Codebook ``Day`` is the CSV export's ``SQLDATE`` column.
EVENT_COLUMNS = (
    "GLOBALEVENTID", "SQLDATE", "MonthYear", "Year", "FractionDate",
    "Actor1Code", "Actor1Name", "Actor1CountryCode", "Actor1KnownGroupCode",
    "Actor1EthnicCode", "Actor1Religion1Code", "Actor1Religion2Code",
    "Actor1Type1Code", "Actor1Type2Code", "Actor1Type3Code",
    "Actor2Code", "Actor2Name", "Actor2CountryCode", "Actor2KnownGroupCode",
    "Actor2EthnicCode", "Actor2Religion1Code", "Actor2Religion2Code",
    "Actor2Type1Code", "Actor2Type2Code", "Actor2Type3Code",
    "IsRootEvent", "EventCode", "EventBaseCode", "EventRootCode", "QuadClass",
    "GoldsteinScale", "NumMentions", "NumSources", "NumArticles", "AvgTone",
    "Actor1Geo_Type", "Actor1Geo_FullName", "Actor1Geo_CountryCode",
    "Actor1Geo_ADM1Code", "Actor1Geo_ADM2Code", "Actor1Geo_Lat",
    "Actor1Geo_Long", "Actor1Geo_FeatureID",
    "Actor2Geo_Type", "Actor2Geo_FullName", "Actor2Geo_CountryCode",
    "Actor2Geo_ADM1Code", "Actor2Geo_ADM2Code", "Actor2Geo_Lat",
    "Actor2Geo_Long", "Actor2Geo_FeatureID",
    "ActionGeo_Type", "ActionGeo_FullName", "ActionGeo_CountryCode",
    "ActionGeo_ADM1Code", "ActionGeo_ADM2Code", "ActionGeo_Lat",
    "ActionGeo_Long", "ActionGeo_FeatureID", "DATEADDED", "SOURCEURL",
)


class GDELTEventError(ValueError):
    """A supplied synthetic Events row violates the offline projection contract."""


@dataclass(frozen=True)
class GeographyCandidate:
    """Validated GDELT action-geography values; not an HORIZONT location."""

    match_type: int
    fullname: str | None
    fips_country_code: str | None
    adm1_code: str | None
    adm2_code: str | None
    latitude: float
    longitude: float
    feature_id: str | None
    validation_status: str = "pending"


@dataclass(frozen=True)
class GDELTEventProjection:
    """A GDELT-coded report of an event, pending independent confirmation."""

    gdelt_event_id: int
    gdelt_sql_date: date
    gdelt_date_added_at: datetime
    collected_at: str
    publisher_url: str
    cameo_event_code: str
    actor1_label: str | None
    actor2_label: str | None
    action_geography: GeographyCandidate | None
    source_id: str = EVENTS_SOURCE_ID
    source_attribution: str = "GDELT Project"
    source_attribution_url: str = GDELT_PROJECT_URL
    dedup_key: str = ""
    occurred_at: None = None
    published_at: None = None
    validation_status: str = "pending"


def _date(value: str, field: str) -> date:
    if not re.fullmatch(r"\d{8}", value):
        raise GDELTEventError(f"{field} must be YYYYMMDD")
    try:
        return datetime.strptime(value, "%Y%m%d").date()
    except ValueError as exc:
        raise GDELTEventError(f"{field} is not a calendar date") from exc


def _date_added(value: str) -> datetime:
    if not re.fullmatch(r"\d{14}", value):
        raise GDELTEventError("DATEADDED must be YYYYMMDDHHMMSS UTC")
    try:
        result = datetime.strptime(value, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise GDELTEventError("DATEADDED is not a calendar timestamp") from exc
    if result.minute % 15 or result.second:
        raise GDELTEventError("DATEADDED must be a 15-minute GDELT boundary")
    return result


def _number(value: str, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise GDELTEventError(f"{field} must be numeric") from exc
    if result != result or result in {float("inf"), float("-inf")}:
        raise GDELTEventError(f"{field} must be finite")
    return result


def _action_geography(row: dict[str, str]) -> GeographyCandidate | None:
    fields = (
        "ActionGeo_Type", "ActionGeo_FullName", "ActionGeo_CountryCode",
        "ActionGeo_ADM1Code", "ActionGeo_ADM2Code", "ActionGeo_Lat",
        "ActionGeo_Long", "ActionGeo_FeatureID",
    )
    values = [row[name].strip() for name in fields]
    match_type, fullname, country, adm1, adm2, lat, lon, feature_id = values
    location_values = (fullname, country, adm1, adm2, lat, lon, feature_id)
    if not any(values):
        return None
    if not match_type:
        raise GDELTEventError("action geography has values without ActionGeo_Type")
    if match_type == "0":
        if any(location_values):
            raise GDELTEventError("ActionGeo_Type 0 contradicts populated action geography")
        return None
    if not re.fullmatch(r"[1-5]", match_type):
        raise GDELTEventError("ActionGeo_Type is outside the codebook vocabulary")
    if not lat or not lon:
        # Preserve no partial/unplaced coordinates as a map point.
        return None
    latitude = _number(lat, "ActionGeo_Lat")
    longitude = _number(lon, "ActionGeo_Long")
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        raise GDELTEventError("action geography coordinates are out of range")
    return GeographyCandidate(
        int(match_type), fullname or None, country or None, adm1 or None, adm2 or None,
        latitude, longitude, feature_id or None,
    )


def project_tsv_row(row: str | tuple[str, ...] | list[str], *, collected_at: str | datetime) -> GDELTEventProjection:
    """Decode exactly one supplied 61-column Events row; never persists it."""
    if isinstance(row, str):
        values = row.rstrip("\r\n").split("\t")
    elif isinstance(row, (tuple, list)):
        values = list(row)
    else:
        raise GDELTEventError("row must be one TSV line or a sequence of columns")
    if len(values) != EXPECTED_COLUMNS:
        raise GDELTEventError(f"Events row must have exactly {EXPECTED_COLUMNS} columns")
    if any(not isinstance(value, str) for value in values):
        raise GDELTEventError("all Events columns must be text")
    if any("\t" in value or "\r" in value or "\n" in value for value in values):
        raise GDELTEventError("column values must not contain TSV delimiters")
    data = dict(zip(EVENT_COLUMNS, values))

    event_id_text = data["GLOBALEVENTID"]
    if not re.fullmatch(r"[1-9]\d{0,18}", event_id_text) or int(event_id_text) > MAX_EVENT_ID:
        raise GDELTEventError("GLOBALEVENTID must be a positive bounded integer")
    event_code = data["EventCode"]
    if not re.fullmatch(r"\d{1,4}", event_code):
        raise GDELTEventError("EventCode must retain its 1-4 digit CAMEO string form")
    url = data["SOURCEURL"]
    try:
        publisher_url = canonicalize_article_url(url)
    except (TypeError, ValueError) as exc:
        raise GDELTEventError("SOURCEURL must be an absolute HTTP(S) publisher URL") from exc

    try:
        collected = normalize_utc(collected_at, required=True)
    except (TypeError, ValueError) as exc:
        raise GDELTEventError("collected_at must be a timezone-aware timestamp") from exc

    return GDELTEventProjection(
        gdelt_event_id=int(event_id_text),
        gdelt_sql_date=_date(data["SQLDATE"], "SQLDATE"),
        gdelt_date_added_at=_date_added(data["DATEADDED"]),
        collected_at=collected,
        publisher_url=publisher_url,
        cameo_event_code=event_code,
        actor1_label=data["Actor1Name"].strip() or None,
        actor2_label=data["Actor2Name"].strip() or None,
        action_geography=_action_geography(data),
        dedup_key=f"{EVENTS_SOURCE_ID}:{event_id_text}",
    )


def project_tsv_rows(rows, *, collected_at: str | datetime) -> tuple[GDELTEventProjection, ...]:
    """Project a batch atomically in memory and reject duplicate GDELT IDs."""
    results: list[GDELTEventProjection] = []
    ids: set[int] = set()
    for index, row in enumerate(rows):
        try:
            item = project_tsv_row(row, collected_at=collected_at)
        except GDELTEventError as exc:
            raise GDELTEventError(f"row {index}: {exc}") from exc
        if item.gdelt_event_id in ids:
            raise GDELTEventError(f"row {index}: duplicate GLOBALEVENTID")
        ids.add(item.gdelt_event_id)
        results.append(item)
    return tuple(results)
