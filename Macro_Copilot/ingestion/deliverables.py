"""ingestion.deliverables — pure-Python helpers for the deliverables flow
(work order C3, ADR 0011).

The deliverables flow moves data through three layers:

  1. ``utils/historical_extractor.py --mode deliverables`` and
     ``utils/incremental_extractor.py --mode deliverables`` — enumerate the
     contract chain per bond-future generic, fetch the deliverable basket
     (``bds(<contract>, FUT_DLVRBL_BNDS_*)``) and per-contract delivery /
     notice dates (``bdp(<contract>, …)``), assemble a wide parquet (one row
     per ``(generic, contract_code, deliverable_cusip)``) and upload to
     ``gs://<bucket>/deliverables/<dataset>/``.

  2. ``ingestion/ingest_parquet.py`` (this module's parent) — downloads those
     parquets, validates them again as defence-in-depth, resolves
     ``vendor_ticker`` → ``instrument_id`` via ``macro_data.instrument_master``
     for the GENERIC future, resolves ``deliverable_cusip`` → optional
     ``deliverable_instrument_id`` against the same table (NULL when the
     deliverable bond is not registered — ADR 0011 §Decision 2), and writes
     via :func:`database.database.upsert_futures_deliverables`.

  3. ``macro_data.futures_deliverables`` — the reference-data table from
     ADR 0011, with one row per ``(generic_instrument_id, contract_code,
     deliverable_cusip)`` and ``UNIQUE`` on that natural key.

----------------------------------------------------------------------------
CANONICAL / SYNC INVARIANT
----------------------------------------------------------------------------
This module is the **canonical** implementation of:

  * :data:`DELIVERABLES_TYPED_COLUMNS` — the typed-column set on
    ``macro_data.futures_deliverables``.

The two extractor scripts (``utils/historical_extractor.py`` and
``utils/incremental_extractor.py``) carry **inlined byte-identical copies**
because they are designed to run as single-file scripts on the Bloomberg
terminal host with NO project-internal imports (the operator copies one
file onto the Bloomberg PC and runs it). The inlined copies let the
extractors emit a parquet whose typed columns line up with the DB columns
WITHOUT importing this module.

If you change :data:`DELIVERABLES_TYPED_COLUMNS` here, you MUST update the
matching inlined copies in:

  * ``utils/historical_extractor.py``   (search for "_DELIVERABLES_TYPED_COLUMNS")
  * ``utils/incremental_extractor.py``  (same name)

The DB ``UNIQUE`` constraint on the natural key is the ultimate enforcement;
if the three copies ever drift, the DB will reject the bad parquet at the
INSERT step (the ingester's atomic txn rolls back, the audit row is flipped
to FAILED, prior data preserved). The inlined extractor copies exist to
catch shape errors before the parquet is uploaded.

Related contract: ADR 0011 — ``docs_revamped/05_decisions/0011-futures-deliverables-substrate.md``.
"""

from __future__ import annotations

import json
from datetime import date as _date, datetime as _datetime
from typing import Any, Dict, List, Optional

import pandas as pd


# Typed columns on ``macro_data.futures_deliverables`` (per ``database/schema.sql``
# Section 8). Any parquet column matching one of these by exact name is forwarded
# to the matching typed DB column; everything else is bundled into the JSONB
# ``attributes`` blob (lineage / mode stamps are explicitly excluded — see
# :data:`_EXCLUDE_FOR_ATTRIBUTES` below). The natural-key columns
# (``instrument_id``, ``contract_code``, ``deliverable_cusip``) and the
# optional FK column (``deliverable_instrument_id``) are not in this tuple —
# they are handled explicitly by the parser / ingester (instrument_id is
# resolved from ``vendor_ticker``; the optional FK is resolved by the route).
DELIVERABLES_TYPED_COLUMNS: tuple = (
    "deliverable_isin",
    "conversion_factor",
    "first_delivery_date",
    "last_delivery_date",
    "first_notice_date",
    "last_notice_date",
)


# Parquet columns that exist for lineage / routing but should NOT be forwarded
# into either typed columns or the JSONB attributes blob.
_EXCLUDE_FOR_ATTRIBUTES: frozenset = frozenset({
    "vendor_ticker",
    "vendor",
    "contract_code",
    "deliverable_cusip",
    "deliverable_isin",
    "playbook_name",
    "playbook_version",
    "playbook_hash",
    "git_commit_hash",
    "extractor_version",
    "requested_start_date",
    "requested_end_date",
    "extracted_at",
    "extraction_mode",
    "dataset_name",
    "asset_class",
    "instrument_type",
    "load_id",
})


# ============================================================================
# Scalar normalisation — mirrors the small helpers in ingestion/event_calendar.py.
# Kept here so the parser is import-light (no DB import).
# ============================================================================
def _is_missing(value: Any) -> bool:
    """True if ``value`` represents a missing cell across the four shapes a
    parquet round-trip can produce: ``None``, empty string, ``pd.NaT``,
    ``float('nan')``. Mirrors :func:`ingestion.metadata_history._is_missing`."""
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _str_or_none(value: Any) -> Optional[str]:
    if _is_missing(value):
        return None
    text = str(value).strip()
    return text or None


def _num_or_none(value: Any) -> Optional[float]:
    if _is_missing(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> Optional[int]:
    if _is_missing(value):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_iso_date(value: Any) -> Optional[str]:
    """Normalise any date-ish value to ``YYYY-MM-DD``; None when unparseable."""
    if _is_missing(value):
        return None
    if isinstance(value, _date) and not isinstance(value, _datetime):
        return value.isoformat()
    ts = pd.to_datetime(value, errors="coerce")
    if ts is None or pd.isna(ts):
        return None
    return ts.date().isoformat()


def _to_attributes(value: Any) -> Optional[Dict[str, Any]]:
    """Normalise an ``attributes`` cell to a dict or None.

    Parquet cannot store nested dicts per cell directly, so the extractor
    serialises ``attributes`` as JSON; a dict is accepted for in-process
    callers. Non-object / unparseable values yield None.
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
# Parquet parsing
# ============================================================================
def parquet_to_deliverables_records(
    df: pd.DataFrame,
    instrument_id_map: Dict[str, int],
    load_id: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Convert a wide deliverables parquet (one row per ``(generic,
    contract_code, deliverable_cusip)``) into a list of dicts ready for
    :func:`database.database.upsert_futures_deliverables`.

    Mapping rules:
      * ``vendor_ticker`` → resolved to ``instrument_id`` (the GENERIC future's
        ``instrument_master`` row) via ``instrument_id_map``. A vendor_ticker
        missing from the map raises ``ValueError`` — the bond-futures
        time-series playbook has not been ingested yet (operator ordering
        bug, not a substrate bug).
      * ``contract_code`` → typed column. Required.
      * ``deliverable_cusip`` → typed column. Required.
      * Every column in :data:`DELIVERABLES_TYPED_COLUMNS` (``deliverable_isin``,
        ``conversion_factor``, the four delivery / notice dates) → matching
        typed DB column.
      * ``deliverable_instrument_id`` is NOT resolved here — the ingester
        route does the CUSIP → ``instrument_master`` lookup against the live
        DB (this parser is pure-Python, no DB). It is left None on the
        record; the route populates it before the upsert.
      * Any other column (apart from the lineage / mode stamps in
        :data:`_EXCLUDE_FOR_ATTRIBUTES`) is bundled into ``attributes``.

    ``load_id`` is optional — when provided it is stamped onto every record so
    each row records the audit row that wrote it. Lineage symmetry with how
    :func:`ingestion.metadata_history.parquet_to_history_records` stamps it.
    """
    if df is None or df.empty:
        return []

    records: List[Dict[str, Any]] = []
    for raw in df.to_dict(orient="records"):
        vendor_ticker = raw.get("vendor_ticker")
        if not vendor_ticker:
            raise ValueError(
                "deliverables parquet row missing vendor_ticker"
            )
        if vendor_ticker not in instrument_id_map:
            raise ValueError(
                f"deliverables parquet references vendor_ticker {vendor_ticker!r} "
                "which is not present in instrument_master. Ingest the bond-futures "
                "time-series playbook owning this ticker first."
            )

        contract_code = _str_or_none(raw.get("contract_code"))
        deliverable_cusip = _str_or_none(raw.get("deliverable_cusip"))

        record: Dict[str, Any] = {
            "instrument_id": int(instrument_id_map[vendor_ticker]),
            "contract_code": contract_code,
            "deliverable_cusip": deliverable_cusip,
            # deliverable_instrument_id is left to the ingester route — see docstring.
            "deliverable_instrument_id": None,
        }

        # Typed columns: deliverable_isin (string), conversion_factor (numeric),
        # four delivery/notice dates.
        record["deliverable_isin"]    = _str_or_none(raw.get("deliverable_isin"))
        record["conversion_factor"]   = _num_or_none(raw.get("conversion_factor"))
        for col in ("first_delivery_date", "last_delivery_date",
                    "first_notice_date",   "last_notice_date"):
            record[col] = _to_iso_date(raw.get(col))

        # Everything else lands in attributes JSONB.
        attrs: Dict[str, Any] = {}
        for k, v in raw.items():
            if k in _EXCLUDE_FOR_ATTRIBUTES:
                continue
            if k in DELIVERABLES_TYPED_COLUMNS:
                continue
            if _is_missing(v):
                continue
            attrs[k] = v.item() if hasattr(v, "item") else v
        record["attributes"] = attrs or None

        if load_id is not None:
            record["load_id"] = int(load_id)

        records.append(record)
    return records


def is_ingestable_deliverable(rec: Dict[str, Any]) -> bool:
    """True iff a parsed deliverables record carries everything a
    ``futures_deliverables`` write needs: the natural-key triple
    (``instrument_id``, ``contract_code``, ``deliverable_cusip``).

    A row failing this is skipped (logged, not written) — honest non-action.
    Letting it reach :func:`database.database._normalize_deliverables_record`
    would raise and abort the whole parquet's atomic transaction.
    """
    instrument_id = rec.get("instrument_id")
    if not isinstance(instrument_id, int) or instrument_id <= 0:
        return False
    if not rec.get("contract_code"):
        return False
    if not rec.get("deliverable_cusip"):
        return False
    return True


def group_rows_by_contract(
    df: pd.DataFrame,
) -> Dict[tuple, List[Dict[str, Any]]]:
    """Group incoming parquet rows by ``(vendor_ticker, contract_code)``.
    A small utility for callers that want a per-contract slice of the parquet;
    the actual ingester-side date-consistency check is
    :func:`validate_per_contract_dates`, which runs on parsed records
    (post-vendor-ticker resolution).
    """
    if df is None or df.empty:
        return {}
    grouped: Dict[tuple, List[Dict[str, Any]]] = {}
    for raw in df.to_dict(orient="records"):
        vt = raw.get("vendor_ticker")
        cc = raw.get("contract_code")
        # ``not vt``/``not cc`` alone is insufficient: pandas surfaces
        # missing cells as float NaN, which is truthy and would
        # otherwise be grouped under a 'nan' key.
        if (
            not vt
            or not cc
            or (isinstance(vt, float) and pd.isna(vt))
            or (isinstance(cc, float) and pd.isna(cc))
        ):
            continue
        key = (str(vt), str(cc))
        grouped.setdefault(key, []).append(raw)
    return grouped


# Per-contract dates are denormalised across every deliverable row of the
# basket (ADR 0011 §Alternatives). These are the columns that MUST be
# identical for every row sharing one (instrument_id, contract_code).
_PER_CONTRACT_DATE_COLUMNS: tuple = (
    "first_delivery_date",
    "last_delivery_date",
    "first_notice_date",
    "last_notice_date",
)


def validate_per_contract_dates(
    records: List[Dict[str, Any]],
) -> List[str]:
    """Verify per-contract date denormalisation invariants on parsed records
    (ADR 0011 v2, Codex finding 5).

    The first/last delivery dates and first/last notice dates are PER CONTRACT
    — but the substrate stores them on every basket row (denormalised, ADR
    0011 §Alternatives). A parquet where the same ``(instrument_id,
    contract_code)`` has rows disagreeing on those dates is malformed and
    would silently corrupt downstream joins (a CTD-identifier primitive
    joining a deliverable bond to its basket's delivery dates would get
    different answers per row).

    Returns a list of human-readable conflict descriptions. An empty list
    means every contract's basket rows agree on the per-contract dates and
    the ingester may proceed to upsert. A non-empty list means the ingester
    MUST abort and mark the load FAILED — never silently fold inconsistent
    denormalised data into ``futures_deliverables``.
    """
    if not records:
        return []

    conflicts: List[str] = []
    grouped: Dict[tuple, List[Dict[str, Any]]] = {}
    for rec in records:
        instrument_id = rec.get("instrument_id")
        contract_code = rec.get("contract_code")
        if instrument_id is None or not contract_code:
            # The ingestability gate is the right place to catch missing
            # natural-key fields; this validator ignores them rather than
            # double-reporting.
            continue
        key = (int(instrument_id), str(contract_code))
        grouped.setdefault(key, []).append(rec)

    for (instrument_id, contract_code), rows in grouped.items():
        if len(rows) <= 1:
            continue
        for col in _PER_CONTRACT_DATE_COLUMNS:
            distinct = {rec.get(col) for rec in rows}
            if len(distinct) > 1:
                # Sort for deterministic message ordering; render Nones as
                # NULL for human readability.
                rendered = sorted(
                    "NULL" if v is None else str(v) for v in distinct
                )
                conflicts.append(
                    f"instrument_id={instrument_id} contract_code="
                    f"{contract_code!r}: inconsistent {col} across "
                    f"{len(rows)} basket row(s) — distinct values: "
                    f"{rendered}"
                )
    return conflicts


__all__ = [
    "DELIVERABLES_TYPED_COLUMNS",
    "parquet_to_deliverables_records",
    "is_ingestable_deliverable",
    "group_rows_by_contract",
    "validate_per_contract_dates",
]
