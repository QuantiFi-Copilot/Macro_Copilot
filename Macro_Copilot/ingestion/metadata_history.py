"""ingestion.metadata_history — canonical helpers for the metadata-history flow.

The metadata-history path moves data through three layers:

  1. ``utils/historical_extractor.py --mode metadata-history`` and
     ``utils/incremental_extractor.py --mode metadata-history`` —
     enumerate the underlying-contract chain per rolling generic,
     fetch per-contract static fields, compute effective windows under
     the ADR-0002 ``expiry_roll`` convention, and write a wide parquet
     to ``gs://<bucket>/metadata_history/<dataset>/``.

  2. ``ingestion/ingest_parquet.py`` (this module's parent) —
     downloads those parquets, validates them again as defence-in-depth,
     resolves vendor tickers to ``instrument_id`` via
     ``macro_data.instrument_master``, and writes effective-dated rows
     to ``macro_data.instrument_metadata_history`` using the helpers
     shipped in Step 0 (``upsert_instrument_metadata_history`` +
     ``close_open_metadata_window``).

  3. ``macro_data.instrument_metadata_history`` — the SCD2 sibling
     table from ADR 0001, guarded at the database layer by an
     ``EXCLUDE USING GIST`` constraint that rejects any overlapping
     or boundary-sharing effective window per instrument.

----------------------------------------------------------------------------
CANONICAL / SYNC INVARIANT
----------------------------------------------------------------------------
This module is the **canonical** implementation of:
  * ``HISTORY_TYPED_COLUMNS`` — the typed-column set on
    ``macro_data.instrument_metadata_history``.
  * ``validate_no_overlaps`` — the overlap detector that mirrors the
    DB ``EXCLUDE USING GIST`` constraint's semantics.

The two extractor scripts (``utils/historical_extractor.py`` and
``utils/incremental_extractor.py``) carry **inlined byte-identical
copies** of both, because they are designed to run as single-file scripts
on the Bloomberg terminal host with NO project-internal imports (the
operator copies one file onto the Bloomberg PC and runs it). The inlined
copies exist so the extractors can surface human-readable pre-write
failure messages BEFORE a parquet is uploaded to GCS.

If you change either ``HISTORY_TYPED_COLUMNS`` or ``validate_no_overlaps``
in this file, you MUST update the matching inlined copies in:
  * ``utils/historical_extractor.py``  (search for "_HISTORY_TYPED_COLUMNS" / "_validate_no_overlaps")
  * ``utils/incremental_extractor.py`` (same names)

The DB ``EXCLUDE`` constraint is the ultimate enforcement; if the three
copies ever drift, the DB will reject the bad parquet at the destructive
transaction's INSERT step (the ingester's atomic txn rolls back, audit
row is flipped to FAILED, prior data preserved). The inlined extractor
copies exist to make that failure surface earlier and more legibly on the
Bloomberg host.

References:
  * ADR 0001 — sibling SCD2 table:
    ``docs_revamped/05_decisions/0001-instrument-metadata-history.md``
  * ADR 0002 — playbook ``metadata_history`` section + extractor / ingester
    routing: ``docs_revamped/05_decisions/0002-playbook-metadata-history-section.md``
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

import pandas as pd


# Typed columns on ``macro_data.instrument_metadata_history`` (per
# ``database/schema.sql`` section 4). Any parquet column matching one of
# these by exact name is forwarded to the matching typed column on the
# DB row; everything else is bundled into ``attributes`` JSONB. Kept here
# as a single source so the extractor's parquet-builder and the ingester's
# parquet-reader agree on the schema without needing to import each other.
HISTORY_TYPED_COLUMNS: tuple = (
    "contract_code",
    "expiry_date",
    "maturity_date",
    "security_name",
    "settlement_date",
    "accrual_start_date",
    "accrual_end_date",
    "tick_size",
    "tick_value",
    "contract_size",
    "exchange_code",
    "underlying_ticker",
)


def _is_missing(value: Any) -> bool:
    """
    True if ``value`` represents a missing effective-date field.

    A missing date reaches this module in one of four shapes depending on
    how the rows got here:

      * ``None``          — in-memory dicts straight from the extractor
                            (the terminal open window's ``effective_to``).
      * ``""``            — empty-string sentinel.
      * ``pd.NaT``        — a date column round-tripped through parquet:
                            ``pd.read_parquet`` reads a NULL in a datetime
                            column back as ``NaT``, NOT ``None``.
      * ``float('nan')``  — a NULL in an object / float column.

    The extractor-side gate only ever sees genuine ``None`` (the open
    window is a real Python ``None`` before serialisation), so the original
    ``x in (None, "")`` check sufficed there and ADR-0002's unit tests —
    which build row dicts by hand — never exercised the parquet path. The
    ingester-side gate reads the *parquet*, where the same NULL surfaces as
    ``NaT``; ``NaT not in (None, "")`` is ``True``, so the old check let it
    through and the subsequent ``NaT < date`` comparison raised
    "Cannot compare NaT with datetime.date object". Treating all four
    shapes uniformly keeps both callers correct.
    """
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def validate_no_overlaps(
    rows_by_vendor_ticker: Dict[str, List[Dict[str, Any]]],
) -> List[str]:
    """
    Defence-in-depth overlap detector for the metadata-history flow.

    For each generic ticker, walks rows sorted by ``effective_from`` and
    reports conflicts where consecutive windows overlap or share a boundary
    day. Returns a list of human-readable conflict descriptions; an empty
    list means the input is clean and may be written.

    Rules (matching the ``EXCLUDE USING GIST`` constraint on
    ``instrument_metadata_history`` shipped in ADR 0001):

      * Every row must carry ``effective_from``.
      * If a row has ``effective_to``, it must be ``>= effective_from``.
      * ``effective_to=None`` is only valid on the chronologically-last
        row per ticker (the currently-effective window).
      * Consecutive rows must not overlap: ``next.effective_from >
        prior.effective_to`` (strict inequality — boundary day is treated
        as overlap, matching the DB constraint's ``[]`` inclusive bounds).

    This is identical to the gate applied by ADR-0002's extractor before
    parquet upload and by the ingester before the destructive transaction,
    so both callers fail with the same human-readable message.
    """
    conflicts: List[str] = []
    for vendor_ticker, rows in rows_by_vendor_ticker.items():
        if not rows:
            continue

        bad_missing_from = [r for r in rows if _is_missing(r.get("effective_from"))]
        if bad_missing_from:
            conflicts.append(
                f"{vendor_ticker}: {len(bad_missing_from)} row(s) missing effective_from"
            )
            continue

        try:
            sorted_rows = sorted(
                rows,
                key=lambda r: pd.to_datetime(r["effective_from"]).date(),
            )
        except Exception as exc:
            conflicts.append(
                f"{vendor_ticker}: failed to parse effective_from on at least one row: {exc}"
            )
            continue

        n = len(sorted_rows)
        for i, curr in enumerate(sorted_rows):
            try:
                curr_from = pd.to_datetime(curr["effective_from"]).date()
            except Exception as exc:
                conflicts.append(
                    f"{vendor_ticker}: row {i} has unparseable effective_from "
                    f"({curr['effective_from']!r}): {exc}"
                )
                continue

            curr_to_raw = curr.get("effective_to")
            curr_to = None
            if not _is_missing(curr_to_raw):
                try:
                    curr_to = pd.to_datetime(curr_to_raw).date()
                except Exception as exc:
                    conflicts.append(
                        f"{vendor_ticker}: row {i} has unparseable effective_to "
                        f"({curr_to_raw!r}): {exc}"
                    )
                    continue

            if curr_to is not None and curr_to < curr_from:
                conflicts.append(
                    f"{vendor_ticker}: row {i} has effective_to ({curr_to}) "
                    f"before effective_from ({curr_from})"
                )
                continue

            is_last = i == n - 1
            if curr_to is None and not is_last:
                later = sorted_rows[i + 1]
                conflicts.append(
                    f"{vendor_ticker}: row {i} has effective_to=NULL but is not "
                    f"the latest window (next row effective_from="
                    f"{later['effective_from']})"
                )
                continue

            if is_last or curr_to is None:
                continue

            nxt = sorted_rows[i + 1]
            try:
                nxt_from = pd.to_datetime(nxt["effective_from"]).date()
            except Exception:
                # Already reported on the next iteration.
                continue
            if nxt_from <= curr_to:
                conflicts.append(
                    f"{vendor_ticker}: row {i} (effective_to={curr_to}) "
                    f"overlaps row {i + 1} (effective_from={nxt_from})"
                )
    return conflicts


def parquet_to_history_records(
    df: pd.DataFrame,
    instrument_id_map: Dict[str, int],
    load_id: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    Convert a metadata-history parquet (one row per (generic_ticker,
    effective_window)) into a list of dicts ready for
    ``database.database.upsert_instrument_metadata_history``.

    Mapping rules:
      * ``vendor_ticker`` -> resolved to ``instrument_id`` via
        ``instrument_id_map``. A vendor_ticker missing from the map raises
        ``ValueError`` — the time-series playbook owning that ticker has not
        been ingested yet (operator ordering bug, not a substrate bug).
      * ``effective_from`` -> required. Parquet writes it as ISO string;
        the upsert helper accepts ISO strings.
      * ``effective_to`` -> ``None`` is preserved (currently in effect).
      * Any column matching one of ``HISTORY_TYPED_COLUMNS`` is forwarded
        to the matching typed column on the DB row.
      * Any other column (apart from lineage / mode stamps and ``vendor``)
        is bundled into ``attributes`` JSONB.

    ``load_id`` is optional — when provided it is stamped onto every
    record's ``load_id`` column so each history row records the audit row
    that wrote it. Lineage symmetry with how ``upsert_market_data_daily``
    stamps ``load_id`` per row.
    """
    if df is None or df.empty:
        return []

    # Columns that exist in the parquet for lineage / routing but should NOT
    # be forwarded into either typed columns or the JSONB attributes blob.
    exclude_for_attributes = {
        "vendor_ticker",
        "vendor",
        "effective_from",
        "effective_to",
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
    }
    exclude_for_attributes.update(HISTORY_TYPED_COLUMNS)

    records: List[Dict[str, Any]] = []
    for raw in df.to_dict(orient="records"):
        vendor_ticker = raw.get("vendor_ticker")
        if not vendor_ticker:
            raise ValueError(
                "metadata-history parquet row missing vendor_ticker"
            )
        if vendor_ticker not in instrument_id_map:
            raise ValueError(
                f"metadata-history parquet references vendor_ticker {vendor_ticker!r} "
                "which is not present in instrument_master. Ingest the time-series "
                "playbook owning this ticker first."
            )
        instrument_id = instrument_id_map[vendor_ticker]

        effective_from = raw.get("effective_from")
        if _is_missing(effective_from):
            raise ValueError(
                f"metadata-history parquet row for {vendor_ticker!r} missing effective_from"
            )

        effective_to_raw = raw.get("effective_to")
        effective_to = None if _is_missing(effective_to_raw) else effective_to_raw

        record: Dict[str, Any] = {
            "instrument_id": int(instrument_id),
            "effective_from": effective_from,
            "effective_to": effective_to,
        }

        for col in HISTORY_TYPED_COLUMNS:
            val = raw.get(col)
            if _is_missing(val):
                # Catches None, "", NaN AND NaT — a date column that
                # round-tripped through parquet as datetime64 surfaces its
                # NULLs as NaT, which is neither a float nor a pd.Timestamp.
                record[col] = None
            elif isinstance(val, pd.Timestamp):
                # The extractor already stringifies dates; this is defensive
                # for parquets that round-tripped through datetime64.
                record[col] = val.strftime("%Y-%m-%d")
            else:
                record[col] = val

        attributes: Dict[str, Any] = {}
        for k, v in raw.items():
            if k in exclude_for_attributes:
                continue
            if _is_missing(v):
                continue
            attributes[k] = v.item() if hasattr(v, "item") else v
        record["attributes"] = attributes or None

        if load_id is not None:
            record["load_id"] = int(load_id)

        records.append(record)
    return records


def group_rows_by_vendor_ticker(
    df: pd.DataFrame,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Helper for the ingester's defence-in-depth overlap gate: groups the
    incoming parquet rows by ``vendor_ticker`` so :func:`validate_no_overlaps`
    can walk each ticker's windows independently.
    """
    if df is None or df.empty:
        return {}
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for raw in df.to_dict(orient="records"):
        vt = raw.get("vendor_ticker")
        # ``not vt`` alone is insufficient: pandas surfaces missing
        # cells as float NaN, which is truthy and would otherwise be
        # grouped under the key 'nan' — poisoning the overlap gate.
        if not vt or (isinstance(vt, float) and pd.isna(vt)):
            continue
        grouped.setdefault(str(vt), []).append(raw)
    return grouped


__all__ = [
    "HISTORY_TYPED_COLUMNS",
    "validate_no_overlaps",
    "parquet_to_history_records",
    "group_rows_by_vendor_ticker",
]
