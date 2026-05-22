"""ingestion/event_calendar.py — pure-Python helpers for the event-calendar
ingestion flow (work order B2, ADR 0004).

The event extractor (``--mode event-calendar``) emits a WIDE parquet whose
columns are ``macro_data.event_calendar``'s columns — one row per macro event
(an economic release, a central-bank meeting, or a sovereign auction). Local
ingestion folds that parquet into ``event_calendar`` through
``upsert_event_calendar``. This module holds the two pieces of that flow that
are pure functions of their inputs — no Bloomberg, no GCS, no DB — so they are
unit-testable in isolation:

  * :func:`parquet_to_event_records` — parse a wide event parquet into clean
    event-record dicts (dates → ISO strings, ``release_time`` → ``HH:MM:SS``,
    numerics → float|None, ``attributes`` → dict|None).
  * :func:`is_ingestable_event` — the gate deciding which parsed rows actually
    drive an ``event_calendar`` write (the natural-key fields must be present
    and ``event_category`` must be in the closed family).

Mirrors ``ingestion/metadata_history.py`` and ``ingestion/otr_resolution.py``.
The DB-side ``database.database._normalize_event_record`` /
``upsert_event_calendar`` remain the enforcing source of truth (closed-family
check, uniform-key shaping, full-row upsert); this module is the parquet-side
preparation that runs in front of them.
"""

from __future__ import annotations

import json
from datetime import date as _date, datetime as _datetime, time as _time
from typing import Any, Dict, List, Optional

import pandas as pd

# The ``event_category`` closed family. Duplicated from
# ``database.database.EVENT_CATEGORIES`` so this pure module stays import-light
# (no DB import); ``_normalize_event_record`` is the enforcing source of truth.
EVENT_CATEGORIES = ("economic_release", "central_bank_meeting", "auction")

# The fields an ingestable event row MUST carry — the ``event_calendar``
# natural key ``(event_type, country, release_date)`` plus ``event_category``.
_REQUIRED_FIELDS = ("event_type", "event_category", "country", "release_date")

# Optional plain-string columns.
_STRING_COLUMNS = ("currency", "central_bank", "period")

# Numeric columns — the 8 economic-release fields + the 4 auction-result
# fields. NULL for the categories they do not apply to (ADR 0004).
_NUMERIC_COLUMNS = (
    "actual",
    "consensus_median",
    "consensus_high",
    "consensus_low",
    "prior",
    "revised_prior",
    "surprise",
    "surprise_std_dev",
    "high_yield",
    "bid_to_cover",
    "tail_bps",
    "indirect_pct",
)

# Every key :func:`parquet_to_event_records` puts on a record. ``load_id`` is
# injected by the ingester (it is the run's own load_id), not by the parser.
EVENT_RECORD_COLUMNS = (
    "event_type",
    "event_category",
    "country",
    "release_date",
    "release_time",
    *_STRING_COLUMNS,
    *_NUMERIC_COLUMNS,
    "related_instrument_id",
    "attributes",
)


# ============================================================================
# Scalar normalisation
# ============================================================================
def _str_or_none(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, float) and pd.isna(value):
        return None
    text = str(value).strip()
    return text or None


def _lower_str(value: Any) -> Optional[str]:
    text = _str_or_none(value)
    return text.lower() if text else None


def _num_or_none(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_iso_date(value: Any) -> Optional[str]:
    """Normalise any date-ish value to ``YYYY-MM-DD``; None when unparseable."""
    if value is None:
        return None
    try:
        if isinstance(value, float) and pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    ts = pd.to_datetime(value, errors="coerce")
    if ts is None or pd.isna(ts):
        return None
    return ts.date().isoformat()


def _to_time_str(value: Any) -> Optional[str]:
    """Normalise a release-time value to ``HH:MM:SS``; None when absent.

    ``release_time`` is nullable on ``event_calendar`` (it is not always known)
    — an unparseable value yields None rather than raising.
    """
    if value is None:
        return None
    try:
        if isinstance(value, float) and pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, _time):
        return value.strftime("%H:%M:%S")
    if isinstance(value, _datetime):
        return value.strftime("%H:%M:%S")
    text = str(value).strip()
    if not text or text.lower() in {"nat", "nan", "none"}:
        return None
    try:
        return pd.to_datetime(text).strftime("%H:%M:%S")
    except Exception:
        return None


def _to_attributes(value: Any) -> Optional[Dict[str, Any]]:
    """Normalise the ``attributes`` cell to a dict or None.

    Parquet cannot store a nested dict per cell directly, so the extractor
    serialises ``attributes`` as a JSON string; a dict is also accepted for
    in-process callers. A non-object / unparseable value yields None.
    """
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    try:
        if isinstance(value, float) and pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            parsed = json.loads(text)
        except (ValueError, TypeError):
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


# ============================================================================
# Parquet parsing + the ingestability gate
# ============================================================================
def parquet_to_event_records(df: pd.DataFrame) -> List[Dict[str, Any]]:
    """Parse a wide event-calendar parquet into per-event record dicts.

    One dict per row, with every :data:`EVENT_RECORD_COLUMNS` key present
    (None for absent columns) so the downstream ``upsert_event_calendar`` bulk
    insert derives one coherent column list. ``event_category`` is lower-cased
    defensively. Every row is returned — :func:`is_ingestable_event` is the
    separate gate for which rows actually drive a write.
    """
    records: List[Dict[str, Any]] = []
    for _, row in df.iterrows():
        rec: Dict[str, Any] = {
            "event_type": _str_or_none(row.get("event_type")),
            "event_category": _lower_str(row.get("event_category")),
            "country": _str_or_none(row.get("country")),
            "release_date": _to_iso_date(row.get("release_date")),
            "release_time": _to_time_str(row.get("release_time")),
            "related_instrument_id": _int_or_none(row.get("related_instrument_id")),
            "attributes": _to_attributes(row.get("attributes")),
        }
        for col in _STRING_COLUMNS:
            rec[col] = _str_or_none(row.get(col))
        for col in _NUMERIC_COLUMNS:
            rec[col] = _num_or_none(row.get(col))
        records.append(rec)
    return records


def is_ingestable_event(rec: Dict[str, Any]) -> bool:
    """True iff a parsed event record carries everything an ``event_calendar``
    write needs: the natural-key fields (``event_type``, ``country``,
    ``release_date``) plus an ``event_category`` inside the closed family.

    A row failing this is skipped (logged, not written) — honest non-action.
    Letting it reach ``_normalize_event_record`` would raise and abort the
    whole parquet's atomic transaction.
    """
    for key in _REQUIRED_FIELDS:
        if not rec.get(key):
            return False
    return rec["event_category"] in EVENT_CATEGORIES
