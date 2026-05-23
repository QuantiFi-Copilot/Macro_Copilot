import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
from google.cloud import storage
from sqlalchemy import MetaData, Table, delete, select

# --- DYNAMIC PATH RESOLUTION ---
# Assumes this script lives under <project_root>/database/
current_dir = Path(__file__).resolve().parent
project_root = current_dir.parent
sys.path.append(str(project_root))

from database.database import (  # noqa: E402
    Connectable,
    _txn,
    get_db_engine,
    upsert_instrument_master,
    insert_load_audit,
    upsert_market_data_daily,
    get_latest_successful_load_for_playbook,
    mark_load_audit_skipped_duplicate,
    update_load_audit_status,
    count_instruments_in_load,
    upsert_instrument_metadata_history,
    close_open_metadata_window,
    upsert_otr_history,
    close_open_otr_window,
    upsert_event_calendar,
    upsert_futures_deliverables,
)

# The dedup hash logic lives in its own module so it can be imported
# (and tested for cross-version stability) without dragging in the
# google.cloud / database imports above.  Re-export the public name
# under its historical alias for any external callers.
from ingestion.hashing import (  # noqa: E402
    NORMALIZED_HASH_EXCLUDED_COLUMNS,
    compute_normalized_data_hash as _compute_normalized_data_hash,
)

# Shared helpers for the metadata-history flow (ADR 0002). Both this
# ingester and ``utils/historical_extractor.py`` import from here so the
# overlap-validation gate uses one implementation in both places.
from ingestion.metadata_history import (  # noqa: E402
    group_rows_by_vendor_ticker as _group_history_rows_by_vendor_ticker,
    parquet_to_history_records as _history_parquet_to_records,
    validate_no_overlaps as _history_validate_no_overlaps,
)

# Pure-Python helpers for the OTR-resolution flow (ADR 0007). The planner is
# the false-roll-protected SCD2 state machine; the parser turns a resolution
# parquet into clean per-slot observations.
from ingestion.otr_resolution import (  # noqa: E402
    ACTION_BOOTSTRAP,
    ACTION_CLEAR_CANDIDATE,
    ACTION_CONFIRM_ROLL,
    ACTION_NO_CHANGE,
    ACTION_RECORD_CANDIDATE,
    ACTION_SKIP_STALE,
    build_otr_instrument_record,
    is_ingestable_observation,
    parquet_to_resolution_records,
    plan_otr_transition,
)

# Pure-Python helpers for the event-calendar flow (ADR 0004). The parser turns
# a wide event parquet into clean per-event records; the gate decides which
# rows carry the natural-key fields a write needs.
from ingestion.event_calendar import (  # noqa: E402
    is_ingestable_event,
    parquet_to_event_records,
)

# Pure-Python helpers for the futures-deliverables flow (ADR 0011). The parser
# turns a wide deliverables parquet into clean per-(generic, contract, deliverable
# bond) records; the gate decides which rows carry the natural-key triple a
# write needs. The optional CUSIP -> instrument_master FK lookup is done by the
# route function (this module needs a DB query), not by the parser.
from ingestion.deliverables import (  # noqa: E402
    is_ingestable_deliverable,
    parquet_to_deliverables_records,
    validate_per_contract_dates,
)

# --- CONFIGURATION ---
BUCKET_NAME = "macro-storage-bucket"
GCP_KEY_FILENAME = "library-extractor-key.json"
DEFAULT_VENDOR = "BLOOMBERG"

# Batch size for the market_data_daily upsert. Large historical reloads
# (>1M rows) must be upserted in bounded batches — see
# ``database.database.upsert_market_data_daily``. The default (10_000)
# works without configuration; override via the env var only if a
# specific deployment needs a different bound.
MARKET_DATA_UPSERT_BATCH_SIZE = int(
    os.getenv("MARKET_DATA_UPSERT_BATCH_SIZE", "10000")
)

# Batch size for the instrument_metadata_history upsert. These rows are
# ~3x wider than market_data_daily rows (~17 bound columns each), so the
# default is lower — see ``database.database.upsert_instrument_metadata_history``.
METADATA_HISTORY_UPSERT_BATCH_SIZE = int(
    os.getenv("METADATA_HISTORY_UPSERT_BATCH_SIZE", "2000")
)


# ==============================================================================================
# HELPERS
# ==============================================================================================
def _first_non_null(df: pd.DataFrame, column: str) -> Optional[Any]:
    """Return the first non-null value from a column, else None."""
    if column not in df.columns:
        return None
    series = df[column].dropna()
    if series.empty:
        return None
    return series.iloc[0]

def _first_non_null_value(series: pd.Series) -> Optional[Any]:
    """Return the first non-null value from a Series, else None."""
    non_null = series.dropna()
    if non_null.empty:
        return None
    return non_null.iloc[0]


def _safe_bool(value: Any, default: bool = False) -> bool:
    """Best-effort normalization of booleans coming from parquet/object columns."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


def _infer_instrument_type(row: pd.Series, dataset_name: Optional[str]) -> str:
    """Infer a coarse instrument type when playbooks do not yet provide one explicitly."""
    explicit = row.get("instrument_type")
    if pd.notna(explicit):
        return str(explicit)

    ds = (dataset_name or "").lower()
    ticker = str(row.get("ticker", "")).lower()

    # `sovereign_cash_bond` (ADR 0003) must be matched BEFORE the generic
    # `sovereign` rule: the cash-bond dataset name contains the substring
    # "sovereign" and cash-bond tickers end " Govt", so without this both
    # generic fallbacks below would mis-type a cash bond as plain "sovereign".
    # Primary path is still the explicit `instrument_type` the cash-bond
    # playbook stamps per the universal contract (handled above); this is the
    # legacy-fallback safety net for a row that arrives without one.
    if "sovereign_cash_bond" in ds:
        return "sovereign_cash_bond"
    if "sovereign" in ds or ticker.endswith(" govt"):
        return "sovereign"
    if "ois" in ds:
        return "ois"
    if "swap" in ds or "irs" in ds:
        return "irs"
    if "future" in ds or "comdty" in ticker:
        return "future"
    if "inflation" in ds or "linker" in ds:
        return "inflation"
    if "swaption" in ds or "vol" in ds:
        return "swaption"

    return "unknown"


def _build_instrument_attributes(row: pd.Series, filename: str) -> Dict[str, Any]:
    """Collect non-core metadata into a flexible JSONB attributes payload."""
    exclude = {
        "trade_date",
        "ticker",
        "field_name",
        "field_value",
        "vendor",
        "vendor_ticker",
        "asset_class",
        "dataset_name",
        "instrument_type",
        "curve_family",
        "country",
        "currency",
        "tenor",
        "underlying_index",
        "contract_code",
        "cusip",
        "isin",
        "expiry_date",
        "maturity_date",
        "is_rolling_contract",
        "is_active",
        "playbook_name",
        "playbook_version",
        "playbook_hash",
        "git_commit_hash",
        "extractor_version",
        "extraction_mode",
        "requested_start_date",
        "requested_end_date",
        "extracted_at",
    }

    attrs: Dict[str, Any] = {"source_file": filename}
    for key, value in row.items():
        if key in exclude:
            continue
        if pd.isna(value):
            continue
        attrs[key] = value.item() if hasattr(value, "item") else value
    return attrs


# Hash + normalization helpers have been moved to ``ingestion.hashing``.
# This module imports them from there at the top of the file so the
# downstream ingestion flow code below uses the same implementation that
# ``tests/state/test_hash_stability.py`` validates for cross-version
# determinism.


def _delete_existing_playbook_scope(
    connectable: Connectable,
    instrument_id_map: Dict[str, int],
    playbook_name: str,
    current_load_id: int,
) -> int:
    """
    Remove existing daily rows for the exact playbook-owned instrument scope being reloaded.

    This keeps `market_data_daily` as the latest clean truth for the current playbook
    scope and removes stale rows for fields that may have been dropped from the playbook
    in newer versions.

    Connection contract
    -------------------
    Accepts either an :class:`Engine` (self-managed transaction) or a
    :class:`Connection` (caller-managed transaction).  The ingestion
    pipeline passes a Connection so this DELETE composes atomically with
    the subsequent upsert + audit-flip inside a single
    ``with engine.begin() as conn:`` block.  See ``_txn`` for the
    dispatch rule.

    Closes ``docs/technical_debt.md`` item #1 for this function.
    """
    instrument_ids: List[int] = sorted({int(v) for v in instrument_id_map.values() if v is not None})

    if not instrument_ids or not playbook_name:
        return 0

    metadata = MetaData(schema="macro_data")
    market_data_table = Table("market_data_daily", metadata, autoload_with=connectable)
    load_audit_table = Table("load_audit", metadata, autoload_with=connectable)

    prior_load_ids_stmt = (
        select(load_audit_table.c.load_id)
        .where(load_audit_table.c.playbook_name == playbook_name)
        .where(load_audit_table.c.load_id != current_load_id)
    )

    stmt = delete(market_data_table).where(
        market_data_table.c.instrument_id.in_(instrument_ids),
        market_data_table.c.load_id.in_(prior_load_ids_stmt),
    )

    with _txn(connectable) as conn:
        result = conn.execute(stmt)

    return result.rowcount or 0


def _delete_existing_playbook_window(
    connectable: Connectable,
    instrument_id_map: Dict[str, int],
    playbook_name: str,
    current_load_id: int,
    requested_start_date: Any,
    requested_end_date: Any,
) -> int:
    """
    Remove existing daily rows only for the requested date window being incrementally refreshed.

    This is the correct behavior for incremental loads: replace the overlapping recent
    window while preserving older history outside the requested window.

    Connection contract
    -------------------
    Accepts either an :class:`Engine` (self-managed transaction) or a
    :class:`Connection` (caller-managed transaction).  The ingestion
    pipeline passes a Connection so this DELETE composes atomically with
    the subsequent upsert + audit-flip.  See ``_txn`` for the dispatch
    rule.

    Closes ``docs/technical_debt.md`` item #1 for this function.
    """
    instrument_ids: List[int] = sorted({int(v) for v in instrument_id_map.values() if v is not None})

    if not instrument_ids or not playbook_name:
        return 0

    if requested_start_date is None or requested_end_date is None:
        raise ValueError(
            "Incremental ingestion requires requested_start_date and requested_end_date."
        )

    start_date = pd.to_datetime(requested_start_date).strftime("%Y-%m-%d")
    end_date = pd.to_datetime(requested_end_date).strftime("%Y-%m-%d")

    metadata = MetaData(schema="macro_data")
    market_data_table = Table("market_data_daily", metadata, autoload_with=connectable)
    load_audit_table = Table("load_audit", metadata, autoload_with=connectable)

    prior_load_ids_stmt = (
        select(load_audit_table.c.load_id)
        .where(load_audit_table.c.playbook_name == playbook_name)
        .where(load_audit_table.c.load_id != current_load_id)
    )

    stmt = delete(market_data_table).where(
        market_data_table.c.instrument_id.in_(instrument_ids),
        market_data_table.c.load_id.in_(prior_load_ids_stmt),
        market_data_table.c.trade_date >= start_date,
        market_data_table.c.trade_date <= end_date,
    )

    with _txn(connectable) as conn:
        result = conn.execute(stmt)

    return result.rowcount or 0


# ==============================================================================================
# METADATA-HISTORY PROCESSING (ADR 0002)
#
# Parquets under gs://<bucket>/metadata_history/<dataset>/ are the output of
# ``utils/historical_extractor.py --mode metadata-history``. They carry a
# DIFFERENT shape from the time-series parquets handled above:
#
#   * Rows are wide effective-dated metadata, one per
#     (generic_ticker, effective_window), not long-format
#     (trade_date, ticker, field_name, field_value).
#   * Destination is ``macro_data.instrument_metadata_history`` (the SCD2
#     sibling table from ADR 0001), not ``market_data_daily``.
#
# This processing function mirrors the time-series flow's structure
# (dedup hash, RUNNING audit row, atomic-txn destructive section, audit
# flip, blob archive) but with the metadata-history destinations.
# ==============================================================================================


def _process_metadata_history_blob(
    bucket: "storage.Bucket",
    blob: "storage.Blob",
    engine,
    temp_dir: Path,
) -> bool:
    """
    Process one metadata-history parquet end-to-end.

    Returns True on success (whether the parquet was ingested or
    skipped-duplicate); False if any failure path was hit. The caller
    aggregates ``False`` into the pipeline's ``any_failures`` flag.

    Failure modes:
      * Empty parquet  -> warn, skip, return False.
      * Dedup hit       -> SKIPPED_DUPLICATE audit row, archive, return True.
      * Sanity gate    -> mark audit FAILED, leave blob in place, return False.
      * Overlap gate   -> mark audit FAILED, leave blob in place, return False.
      * Vendor ticker not in instrument_master -> mark audit FAILED, leave
        blob in place, return False.
      * Any other exception in the destructive section -> the
        ``with engine.begin() as conn:`` block rolls back, the outer
        handler flips the audit row to FAILED on a separate txn,
        return False.
    """
    filename = Path(blob.name).name
    local_path = temp_dir / filename
    load_id: Optional[int] = None

    try:
        print(f"\nProcessing (metadata-history): {filename}")

        blob.download_to_filename(str(local_path))
        df = pd.read_parquet(local_path)
        normalized_data_hash = _compute_normalized_data_hash(df)

        if df.empty:
            print(f"  [WARNING] File {filename} is empty. Skipping.")
            return False

        # --- Lineage / dedup -----------------------------------------------
        playbook_name = _first_non_null(df, "playbook_name") or "unknown_playbook"
        playbook_version = _first_non_null(df, "playbook_version")
        dataset_name = _first_non_null(df, "dataset_name")
        requested_start_date = _first_non_null(df, "requested_start_date")
        requested_end_date = _first_non_null(df, "requested_end_date")
        extracted_at = _first_non_null(df, "extracted_at")
        extraction_mode = str(
            _first_non_null(df, "extraction_mode") or "metadata_history"
        ).strip().lower()

        if extraction_mode != "metadata_history":
            print(
                f"  [WARNING] File {filename} is under metadata_history/ but "
                f"declares extraction_mode={extraction_mode!r}. Skipping."
            )
            return False

        latest_success = get_latest_successful_load_for_playbook(engine, playbook_name)
        latest_success_hash = latest_success.get("source_file_hash") if latest_success else None

        if latest_success_hash and latest_success_hash == normalized_data_hash:
            print(
                f"  [SKIP] Parquet hash matches latest successful load for playbook "
                f"'{playbook_name}'. Skipping ingestion."
            )
            mark_load_audit_skipped_duplicate(
                engine=engine,
                playbook_name=playbook_name,
                playbook_version=playbook_version,
                source_file_name=filename,
                source_file_hash=normalized_data_hash,
                dataset_name=dataset_name,
                requested_start_date=requested_start_date,
                requested_end_date=requested_end_date,
                extracted_at=extracted_at,
                notes=(
                    f"Skipped duplicate metadata-history artifact from GCS object {blob.name}; "
                    "source_file_hash matches the latest successful load for this playbook. "
                    f"(extraction_mode={extraction_mode})"
                ),
            )
            try:
                new_blob_name = blob.name.replace(
                    "metadata_history/", "archive/metadata_history/", 1
                )
                bucket.rename_blob(blob, new_blob_name)
                print(f"  [SUCCESS] Archived duplicate to gs://{BUCKET_NAME}/{new_blob_name}")
            except Exception as archive_err:
                print(
                    f"  [WARNING] Duplicate correctly skipped but GCS archival failed: "
                    f"{archive_err}. File remains in metadata_history/; dedup hash will skip "
                    "it on next run."
                )
            return True

        audit_record = {
            "playbook_name": playbook_name,
            "playbook_version": playbook_version,
            "playbook_hash": _first_non_null(df, "playbook_hash") or normalized_data_hash,
            "git_commit_hash": _first_non_null(df, "git_commit_hash"),
            "extractor_version": _first_non_null(df, "extractor_version"),
            "source_file_name": filename,
            "source_file_hash": normalized_data_hash,
            "dataset_name": dataset_name,
            "requested_start_date": requested_start_date,
            "requested_end_date": requested_end_date,
            "extracted_at": extracted_at,
            "status": "RUNNING",
            "notes": (
                f"Processing GCS object {blob.name} | "
                f"extraction_mode={extraction_mode}"
            ),
        }

        print("  [DB] Inserting load_audit record...")
        load_id = insert_load_audit(engine, audit_record)

        # --- Defence-in-depth overlap gate (mirror of the extractor's pre-write gate) ---
        grouped = _group_history_rows_by_vendor_ticker(df)
        conflicts = _history_validate_no_overlaps(grouped)
        if conflicts:
            preview = "; ".join(conflicts[:5])
            more = f" (+{len(conflicts) - 5} more)" if len(conflicts) > 5 else ""
            msg = (
                f"Overlap-validation gate found {len(conflicts)} conflict(s) in "
                f"metadata-history parquet for playbook '{playbook_name}': "
                f"{preview}{more}. Aborting; the EXCLUDE constraint on "
                "instrument_metadata_history would otherwise reject the bulk insert "
                "mid-batch."
            )
            print(f"  [ABORT] {msg}")
            update_load_audit_status(engine, load_id, "FAILED", msg)
            return False

        # --- Sanity gate (analogue of the 80% time-series gate) -----------
        incoming_ticker_count = len(grouped)
        if latest_success and latest_success.get("load_id"):
            # For metadata-history we count distinct rolling tickers represented
            # in the previous successful load via load_id on the history table.
            try:
                metadata = MetaData(schema="macro_data")
                history_table = Table(
                    "instrument_metadata_history", metadata, autoload_with=engine
                )
                with engine.begin() as conn:
                    prior_count = conn.execute(
                        select(history_table.c.instrument_id.distinct())
                        .where(history_table.c.load_id == latest_success["load_id"])
                    ).rowcount or 0
            except Exception as exc:
                # If the prior-load count cannot be determined, log and proceed
                # (better to allow ingestion than to block on an audit-side
                # observability failure).
                print(f"  [WARNING] Could not count prior-load tickers: {exc}")
                prior_count = 0
            if prior_count > 0:
                coverage = incoming_ticker_count / prior_count
                if coverage < 0.8:
                    msg = (
                        f"Sanity gate FAILED: incoming parquet covers {incoming_ticker_count} "
                        f"rolling tickers vs {prior_count} in the previous successful "
                        f"metadata-history load (coverage {coverage:.0%}, threshold 80%). "
                        "Aborting to prevent silent shrinkage of effective windows."
                    )
                    print(f"  [ABORT] {msg}")
                    update_load_audit_status(engine, load_id, "FAILED", msg)
                    return False
                print(
                    f"  [OK] Sanity gate passed: "
                    f"{incoming_ticker_count}/{prior_count} rolling tickers "
                    f"({coverage:.0%})."
                )

        # --- Resolve vendor_ticker -> instrument_id via instrument_master ---
        unique_tickers = sorted({str(t) for t in df["vendor_ticker"].dropna().unique()})
        if not unique_tickers:
            msg = f"No vendor_ticker values present in metadata-history parquet {filename}"
            print(f"  [ABORT] {msg}")
            update_load_audit_status(engine, load_id, "FAILED", msg)
            return False

        metadata = MetaData(schema="macro_data")
        master_table = Table("instrument_master", metadata, autoload_with=engine)
        vendor_default = _first_non_null(df, "vendor") or DEFAULT_VENDOR
        with engine.begin() as conn:
            rows = conn.execute(
                select(master_table.c.vendor_ticker, master_table.c.instrument_id)
                .where(master_table.c.vendor == vendor_default)
                .where(master_table.c.vendor_ticker.in_(unique_tickers))
            ).fetchall()
        instrument_id_map: Dict[str, int] = {r.vendor_ticker: r.instrument_id for r in rows}
        missing_tickers = [t for t in unique_tickers if t not in instrument_id_map]
        if missing_tickers:
            msg = (
                f"metadata-history parquet references {len(missing_tickers)} vendor_ticker(s) "
                f"not present in instrument_master "
                f"({', '.join(missing_tickers[:5])}"
                f"{'...' if len(missing_tickers) > 5 else ''}). "
                "Ingest the time-series playbook owning these tickers first."
            )
            print(f"  [ABORT] {msg}")
            update_load_audit_status(engine, load_id, "FAILED", msg)
            return False

        # --- Build typed records ready for the upsert helper ---------------
        try:
            records = _history_parquet_to_records(
                df,
                instrument_id_map=instrument_id_map,
                load_id=load_id,
            )
        except ValueError as exc:
            msg = f"Failed to assemble history records from {filename}: {exc}"
            print(f"  [ABORT] {msg}")
            update_load_audit_status(engine, load_id, "FAILED", msg)
            return False

        if not records:
            msg = (
                f"metadata-history parquet {filename} produced 0 records after "
                "instrument_id resolution; nothing to write."
            )
            print(f"  [WARNING] {msg}")
            update_load_audit_status(engine, load_id, "FAILED", msg)
            return False

        # --- Critical destructive section ---------------------------------
        # Same single-transaction shape as the time-series flow: any
        # exception inside the block rolls back close+upsert+audit-flip
        # atomically, and the outer handler flips the audit row to FAILED
        # on a separate transaction.
        with engine.begin() as conn:
            # Per-instrument, close any open prior window before the new
            # rows land. The new windows include their own
            # effective_to=NULL terminal row (for the currently-effective
            # contract), so leaving a prior open row would cause an EXCLUDE
            # violation. close_open_metadata_window sets the prior row's
            # effective_to = new_effective_from - 1, matching the GIST
            # daterange '[]' inclusive bounds.
            per_instrument_min_from: Dict[int, str] = {}
            for r in records:
                iid = r["instrument_id"]
                ef = r["effective_from"]
                cur = per_instrument_min_from.get(iid)
                if cur is None or pd.to_datetime(ef).date() < pd.to_datetime(cur).date():
                    per_instrument_min_from[iid] = ef
            for iid, min_from in per_instrument_min_from.items():
                close_open_metadata_window(conn, iid, min_from)

            # Upserted in bounded batches (METADATA_HISTORY_UPSERT_BATCH_SIZE)
            # but ALL batches run on this same ``conn`` inside the critical
            # transaction — close-window + every upsert batch + audit-flip
            # still commit atomically or roll back together.
            print(f"  [DB] Upserting {len(records)} metadata-history rows...")
            upsert_instrument_metadata_history(
                conn, records, batch_size=METADATA_HISTORY_UPSERT_BATCH_SIZE
            )

            update_load_audit_status(
                conn,
                load_id,
                "SUCCESS",
                (
                    f"Successfully loaded metadata-history from GCS object {blob.name} | "
                    f"extraction_mode={extraction_mode} | "
                    f"rolling_tickers={len(instrument_id_map)} | rows={len(records)}"
                ),
            )

        # --- Archive (best-effort) -----------------------------------------
        try:
            new_blob_name = blob.name.replace(
                "metadata_history/", "archive/metadata_history/", 1
            )
            bucket.rename_blob(blob, new_blob_name)
            print(f"  [SUCCESS] Archived file to gs://{BUCKET_NAME}/{new_blob_name}")
        except Exception as archive_err:
            print(
                f"  [WARNING] DB load succeeded but GCS archival failed: {archive_err}. "
                "File remains in metadata_history/; dedup hash will skip it on next run."
            )

        return True

    except Exception as exc:
        print(f"  [ERROR] Failed to process metadata-history parquet {filename}: {exc}")
        if load_id is not None:
            try:
                update_load_audit_status(
                    engine, load_id, "FAILED",
                    f"Processing failed for GCS object {blob.name}: {exc}",
                )
            except Exception as audit_err:
                print(f"  [ERROR] Could not update load_audit status: {audit_err}")
        return False

    finally:
        if local_path.exists():
            local_path.unlink()


# ==============================================================================================
# OTR-RESOLUTION PROCESSING (ADR 0007)
#
# Parquets under gs://<bucket>/otr_resolution/<dataset>/ are the output of
# ``utils/incremental_extractor.resolve_otr()``. They carry one row per
# (country, tenor) on-the-run slot — the bond ``bdp(<generic>, ID_ISIN)``
# resolved as currently on-the-run. Destination is ``macro_data.otr_history``
# (SCD2), written through the false-roll-protected planner in
# ``ingestion.otr_resolution``.
#
# The flow mirrors the metadata-history flow's structure (dedup hash, RUNNING
# audit row, atomic-txn destructive section, audit flip, archive). The
# instrument_master upsert runs INSIDE the critical transaction (the
# ``Connectable``-migrated helper makes that possible) so the bond identity and
# its otr_history window commit atomically.
# ==============================================================================================


def _apply_otr_plan(
    conn: Connectable,
    plan,
    obs: Dict[str, Any],
    open_row: Optional[Dict[str, Any]],
    load_id: int,
) -> None:
    """Apply one :class:`OtrTransitionPlan` to ``otr_history`` on ``conn``.

    All writes go through the sanctioned ``upsert_otr_history`` /
    ``close_open_otr_window`` helpers (ADR 0003) — no raw SQL. NO_CHANGE and
    SKIP_STALE are deliberate no-ops.
    """
    country = obs["country"]
    tenor = obs["tenor"]

    if plan.action in (ACTION_NO_CHANGE, ACTION_SKIP_STALE):
        return

    if plan.action == ACTION_BOOTSTRAP:
        upsert_otr_history(conn, [{
            "country": country,
            "tenor": tenor,
            "effective_from": plan.new_effective_from,
            "otr_instrument_id": int(plan.new_otr_instrument_id),
            "effective_to": None,
            "attributes": plan.new_attributes,
            "load_id": load_id,
        }])
        return

    if plan.action in (ACTION_RECORD_CANDIDATE, ACTION_CLEAR_CANDIDATE):
        # Re-upsert the still-OPEN row (key = its own effective_from) with the
        # updated attributes blob — only the JSONB candidate state changes; the
        # window itself stays open and points at the same bond.
        upsert_otr_history(conn, [{
            "country": country,
            "tenor": tenor,
            "effective_from": open_row["effective_from"],
            "otr_instrument_id": int(open_row["otr_instrument_id"]),
            "effective_to": None,
            "attributes": plan.open_row_attributes,
            "load_id": load_id,
        }])
        return

    if plan.action == ACTION_CONFIRM_ROLL:
        # Close the prior open window at (new effective_from - 1 day), then open
        # the new one. Order matters: the EXCLUDE GIST constraint is checked
        # per-statement, so the prior window must be closed before the new row
        # is inserted.
        close_open_otr_window(conn, country, tenor, plan.close_prior_at)
        upsert_otr_history(conn, [{
            "country": country,
            "tenor": tenor,
            "effective_from": plan.new_effective_from,
            "otr_instrument_id": int(plan.new_otr_instrument_id),
            "effective_to": None,
            "attributes": plan.new_attributes,
            "load_id": load_id,
        }])
        return

    raise ValueError(f"Unknown OTR transition plan action: {plan.action!r}")


def _process_otr_resolution_blob(
    bucket: "storage.Bucket",
    blob: "storage.Blob",
    engine,
    temp_dir: Path,
) -> bool:
    """Process one OTR-resolution parquet end-to-end.

    Returns True on success (ingested or skipped-duplicate); False on any
    failure path. Mirrors ``_process_metadata_history_blob``.
    """
    filename = Path(blob.name).name
    local_path = temp_dir / filename
    load_id: Optional[int] = None

    try:
        print(f"\nProcessing (otr-resolution): {filename}")

        blob.download_to_filename(str(local_path))
        df = pd.read_parquet(local_path)
        normalized_data_hash = _compute_normalized_data_hash(df)

        if df.empty:
            print(f"  [WARNING] File {filename} is empty. Skipping.")
            return False

        # --- Lineage / dedup -----------------------------------------------
        # playbook_name arrives already SUFFIXED ('<playbook>__otr_resolution')
        # from the resolver, so playbook-keyed dedup / audit is isolated from
        # the market-data load history of the same playbook (ADR 0007).
        playbook_name = _first_non_null(df, "playbook_name") or "unknown__otr_resolution"
        playbook_version = _first_non_null(df, "playbook_version")
        dataset_name = _first_non_null(df, "dataset_name")
        requested_start_date = _first_non_null(df, "requested_start_date")
        requested_end_date = _first_non_null(df, "requested_end_date")
        extracted_at = _first_non_null(df, "extracted_at")
        extraction_mode = str(
            _first_non_null(df, "extraction_mode") or "otr_resolution"
        ).strip().lower()

        if extraction_mode != "otr_resolution":
            print(
                f"  [WARNING] File {filename} is under otr_resolution/ but "
                f"declares extraction_mode={extraction_mode!r}. Skipping."
            )
            return False

        latest_success = get_latest_successful_load_for_playbook(engine, playbook_name)
        latest_success_hash = latest_success.get("source_file_hash") if latest_success else None

        if latest_success_hash and latest_success_hash == normalized_data_hash:
            print(
                f"  [SKIP] Parquet hash matches latest successful otr-resolution "
                f"load for '{playbook_name}'. Skipping ingestion."
            )
            mark_load_audit_skipped_duplicate(
                engine=engine,
                playbook_name=playbook_name,
                playbook_version=playbook_version,
                source_file_name=filename,
                source_file_hash=normalized_data_hash,
                dataset_name=dataset_name,
                requested_start_date=requested_start_date,
                requested_end_date=requested_end_date,
                extracted_at=extracted_at,
                notes=(
                    f"Skipped duplicate otr-resolution artifact from GCS object "
                    f"{blob.name}; source_file_hash matches the latest successful "
                    f"load for this playbook. (extraction_mode={extraction_mode})"
                ),
            )
            try:
                new_blob_name = blob.name.replace(
                    "otr_resolution/", "archive/otr_resolution/", 1
                )
                bucket.rename_blob(blob, new_blob_name)
                print(f"  [SUCCESS] Archived duplicate to gs://{BUCKET_NAME}/{new_blob_name}")
            except Exception as archive_err:
                print(
                    f"  [WARNING] Duplicate correctly skipped but GCS archival "
                    f"failed: {archive_err}. File remains in otr_resolution/."
                )
            return True

        # confirmation_runs is the playbook's roll-confirmation gate, carried
        # through the parquet as a column (the ingester never reads playbooks).
        try:
            confirmation_runs = int(_first_non_null(df, "confirmation_runs") or 2)
        except (TypeError, ValueError):
            confirmation_runs = 2
        confirmation_runs = max(1, confirmation_runs)

        audit_record = {
            "playbook_name": playbook_name,
            "playbook_version": playbook_version,
            "playbook_hash": _first_non_null(df, "playbook_hash") or normalized_data_hash,
            "git_commit_hash": _first_non_null(df, "git_commit_hash"),
            "extractor_version": _first_non_null(df, "extractor_version"),
            "source_file_name": filename,
            "source_file_hash": normalized_data_hash,
            "dataset_name": dataset_name,
            "requested_start_date": requested_start_date,
            "requested_end_date": requested_end_date,
            "extracted_at": extracted_at,
            "status": "RUNNING",
            "notes": (
                f"Processing GCS object {blob.name} | "
                f"extraction_mode={extraction_mode} | "
                f"confirmation_runs={confirmation_runs}"
            ),
        }

        print("  [DB] Inserting load_audit record...")
        load_id = insert_load_audit(engine, audit_record)

        # --- Parse the artifact into per-slot observations -----------------
        records = parquet_to_resolution_records(df)
        ingestable = [r for r in records if is_ingestable_observation(r)]
        non_ok = [r for r in records if r.get("status") != "ok"]
        if non_ok:
            preview = "; ".join(
                f"{r.get('slot_id')}={r.get('status')}" for r in non_ok[:6]
            )
            print(
                f"  [INFO] {len(non_ok)} non-ok slot(s) carried for audit, "
                f"no otr_history change: {preview}"
            )

        if not ingestable:
            msg = (
                f"otr-resolution parquet {filename} produced 0 ingestable slot "
                "observations (all slots failed / rejected / malformed)."
            )
            print(f"  [WARNING] {msg}")
            update_load_audit_status(engine, load_id, "FAILED", msg)
            return False

        print(
            f"  [INFO] {len(ingestable)} ingestable slot observation(s); "
            f"confirmation_runs={confirmation_runs}."
        )

        # --- Critical destructive section ---------------------------------
        # instrument_master upsert + every otr_history transition + audit flip
        # commit atomically or roll back together.
        metadata = MetaData(schema="macro_data")
        otr_table = Table("otr_history", metadata, autoload_with=engine)

        applied: Dict[str, int] = {}

        with engine.begin() as conn:
            # 1. Resolve every observed bond to an instrument_master row. The
            #    /isin/<ISIN> vendor_ticker matches the market-data universe
            #    convention so otr_history and market_data_daily converge on
            #    one instrument_id per bond.
            instrument_records = [build_otr_instrument_record(r) for r in ingestable]
            instrument_id_map = upsert_instrument_master(conn, instrument_records)

            # 2. Per-slot SCD2 transition via the false-roll-protected planner.
            for obs in ingestable:
                ticker = obs["instrument_ticker"]
                resolved_id = instrument_id_map.get(ticker)
                if resolved_id is None:
                    raise ValueError(
                        f"instrument_master upsert returned no instrument_id "
                        f"for resolved bond {ticker}"
                    )

                open_row_raw = conn.execute(
                    select(otr_table)
                    .where(otr_table.c.country == obs["country"])
                    .where(otr_table.c.tenor == obs["tenor"])
                    .where(otr_table.c.effective_to.is_(None))
                    .order_by(otr_table.c.effective_from.desc())
                    .limit(1)
                ).mappings().first()
                open_row = dict(open_row_raw) if open_row_raw else None

                plan = plan_otr_transition(
                    open_row=open_row,
                    resolution_date=obs["resolution_date"],
                    resolved_instrument_id=int(resolved_id),
                    resolved_isin=obs["resolved_isin"],
                    confirmation_runs=confirmation_runs,
                )
                _apply_otr_plan(conn, plan, obs, open_row, load_id)
                applied[plan.action] = applied.get(plan.action, 0) + 1
                print(
                    f"  [{plan.action}] {obs['country']} {obs['tenor']}: {plan.reason}"
                )

            summary = ", ".join(f"{k}={v}" for k, v in sorted(applied.items()))
            update_load_audit_status(
                conn,
                load_id,
                "SUCCESS",
                (
                    f"Successfully processed otr-resolution from GCS object "
                    f"{blob.name} | extraction_mode={extraction_mode} | "
                    f"slots_applied={len(ingestable)} | {summary}"
                ),
            )

        # --- Archive (best-effort) -----------------------------------------
        try:
            new_blob_name = blob.name.replace(
                "otr_resolution/", "archive/otr_resolution/", 1
            )
            bucket.rename_blob(blob, new_blob_name)
            print(f"  [SUCCESS] Archived file to gs://{BUCKET_NAME}/{new_blob_name}")
        except Exception as archive_err:
            print(
                f"  [WARNING] DB load succeeded but GCS archival failed: "
                f"{archive_err}. File remains in otr_resolution/; dedup hash "
                "will skip it on next run."
            )

        return True

    except Exception as exc:
        print(f"  [ERROR] Failed to process otr-resolution parquet {filename}: {exc}")
        if load_id is not None:
            try:
                update_load_audit_status(
                    engine, load_id, "FAILED",
                    f"Processing failed for GCS object {blob.name}: {exc}",
                )
            except Exception as audit_err:
                print(f"  [ERROR] Could not update load_audit status: {audit_err}")
        return False

    finally:
        if local_path.exists():
            local_path.unlink()


# ==============================================================================================
# EVENT-CALENDAR PROCESSING (ADR 0004, work order B2)
#
# Parquets under gs://<bucket>/events/<dataset>/ are the output of the event
# extractor (--mode event-calendar). They carry one row per macro EVENT — an
# economic release, a central-bank meeting, or a sovereign auction — wide and
# structured, with columns matching macro_data.event_calendar. Destination is
# event_calendar via upsert_event_calendar (a full-row upsert: a re-ingest of
# the calendar is idempotent, and an auction's result columns fill in on a
# post-auction re-upsert of the same natural key).
#
# The flow mirrors the metadata-history / otr-resolution flows: dedup hash,
# RUNNING audit row, atomic critical section, audit flip, archive. WIRP does
# NOT come through here — per ADR 0004 it is daily time-series data and rides
# the ordinary data/ → market_data_daily route (PHASE 2).
# ==============================================================================================


def _process_event_blob(
    bucket: "storage.Bucket",
    blob: "storage.Blob",
    engine,
    temp_dir: Path,
) -> bool:
    """Process one event-calendar parquet end-to-end.

    Returns True on success (ingested or skipped-duplicate); False on any
    failure path. Mirrors ``_process_metadata_history_blob`` /
    ``_process_otr_resolution_blob``.
    """
    filename = Path(blob.name).name
    local_path = temp_dir / filename
    load_id: Optional[int] = None

    try:
        print(f"\nProcessing (event-calendar): {filename}")

        blob.download_to_filename(str(local_path))
        df = pd.read_parquet(local_path)
        normalized_data_hash = _compute_normalized_data_hash(df)

        if df.empty:
            print(f"  [WARNING] File {filename} is empty. Skipping.")
            return False

        # --- Lineage / dedup -----------------------------------------------
        # Event artifacts MUST carry lineage. A missing playbook_name is a
        # malformed artifact (the extractor always stamps it) and would also
        # break the playbook-keyed dedup — fail loudly rather than load under a
        # junk name. No load_audit row is created: there is nothing coherent to
        # attribute it to; the blob stays in events/ for inspection.
        playbook_name = _first_non_null(df, "playbook_name")
        if not playbook_name:
            print(
                f"  [ERROR] Event parquet {filename} carries no playbook_name — "
                "malformed artifact (missing lineage). Not ingested."
            )
            return False
        playbook_version = _first_non_null(df, "playbook_version")
        dataset_name = _first_non_null(df, "dataset_name")
        requested_start_date = _first_non_null(df, "requested_start_date")
        requested_end_date = _first_non_null(df, "requested_end_date")
        extracted_at = _first_non_null(df, "extracted_at")
        extraction_mode = str(
            _first_non_null(df, "extraction_mode") or "event_calendar"
        ).strip().lower()

        if extraction_mode != "event_calendar":
            print(
                f"  [WARNING] File {filename} is under events/ but declares "
                f"extraction_mode={extraction_mode!r}. Skipping."
            )
            return False

        latest_success = get_latest_successful_load_for_playbook(engine, playbook_name)
        latest_success_hash = latest_success.get("source_file_hash") if latest_success else None

        if latest_success_hash and latest_success_hash == normalized_data_hash:
            print(
                f"  [SKIP] Parquet hash matches latest successful event load "
                f"for '{playbook_name}'. Skipping ingestion."
            )
            mark_load_audit_skipped_duplicate(
                engine=engine,
                playbook_name=playbook_name,
                playbook_version=playbook_version,
                source_file_name=filename,
                source_file_hash=normalized_data_hash,
                dataset_name=dataset_name,
                requested_start_date=requested_start_date,
                requested_end_date=requested_end_date,
                extracted_at=extracted_at,
                notes=(
                    f"Skipped duplicate event-calendar artifact from GCS object "
                    f"{blob.name}; source_file_hash matches the latest successful "
                    f"load for this playbook. (extraction_mode={extraction_mode})"
                ),
            )
            try:
                new_blob_name = blob.name.replace("events/", "archive/events/", 1)
                bucket.rename_blob(blob, new_blob_name)
                print(f"  [SUCCESS] Archived duplicate to gs://{BUCKET_NAME}/{new_blob_name}")
            except Exception as archive_err:
                print(
                    f"  [WARNING] Duplicate correctly skipped but GCS archival "
                    f"failed: {archive_err}. File remains in events/."
                )
            return True

        audit_record = {
            "playbook_name": playbook_name,
            "playbook_version": playbook_version,
            "playbook_hash": _first_non_null(df, "playbook_hash") or normalized_data_hash,
            "git_commit_hash": _first_non_null(df, "git_commit_hash"),
            "extractor_version": _first_non_null(df, "extractor_version"),
            "source_file_name": filename,
            "source_file_hash": normalized_data_hash,
            "dataset_name": dataset_name,
            "requested_start_date": requested_start_date,
            "requested_end_date": requested_end_date,
            "extracted_at": extracted_at,
            "status": "RUNNING",
            "notes": (
                f"Processing GCS object {blob.name} | "
                f"extraction_mode={extraction_mode}"
            ),
        }

        print("  [DB] Inserting load_audit record...")
        load_id = insert_load_audit(engine, audit_record)

        # --- Parse the artifact; FAIL on any malformed row ----------------
        # Events carry no per-row status field (unlike OTR resolution), so a
        # row failing the natural-key / closed-category gate is not an expected
        # outcome — it is a malformed artifact (an extractor bug). Fail the
        # whole load loudly (P6): upsert_event_calendar is a full-row upsert,
        # so a half-ingested calendar must never be presented as a clean load.
        records = parquet_to_event_records(df)
        ingestable: List[Dict[str, Any]] = []
        malformed: List[Dict[str, Any]] = []
        for rec in records:
            (ingestable if is_ingestable_event(rec) else malformed).append(rec)

        if malformed:
            preview = "; ".join(
                f"{r.get('event_type')}/{r.get('country')}/{r.get('release_date')}"
                for r in malformed[:5]
            )
            msg = (
                f"event-calendar parquet {filename} has {len(malformed)} malformed "
                f"row(s) — each missing a natural-key field (event_type / country / "
                f"release_date) or carrying an event_category outside the closed "
                f"family: {preview}. Aborting; re-extract the artifact clean."
            )
            print(f"  [ABORT] {msg}")
            update_load_audit_status(engine, load_id, "FAILED", msg)
            return False

        if not ingestable:
            msg = f"event-calendar parquet {filename} produced 0 event rows."
            print(f"  [WARNING] {msg}")
            update_load_audit_status(engine, load_id, "FAILED", msg)
            return False

        print(f"  [INFO] {len(ingestable)} event row(s), all well-formed.")

        # --- Critical destructive section ---------------------------------
        # The upsert + audit flip commit atomically or roll back together.
        with engine.begin() as conn:
            for rec in ingestable:
                rec["load_id"] = load_id
            upsert_event_calendar(conn, ingestable)
            update_load_audit_status(
                conn,
                load_id,
                "SUCCESS",
                (
                    f"Successfully loaded event-calendar from GCS object "
                    f"{blob.name} | extraction_mode={extraction_mode} | "
                    f"events={len(ingestable)}"
                ),
            )

        # --- Archive (best-effort) -----------------------------------------
        try:
            new_blob_name = blob.name.replace("events/", "archive/events/", 1)
            bucket.rename_blob(blob, new_blob_name)
            print(f"  [SUCCESS] Archived file to gs://{BUCKET_NAME}/{new_blob_name}")
        except Exception as archive_err:
            print(
                f"  [WARNING] DB load succeeded but GCS archival failed: "
                f"{archive_err}. File remains in events/; dedup hash will skip "
                "it on next run."
            )

        return True

    except Exception as exc:
        print(f"  [ERROR] Failed to process event-calendar parquet {filename}: {exc}")
        if load_id is not None:
            try:
                update_load_audit_status(
                    engine, load_id, "FAILED",
                    f"Processing failed for GCS object {blob.name}: {exc}",
                )
            except Exception as audit_err:
                print(f"  [ERROR] Could not update load_audit status: {audit_err}")
        return False

    finally:
        if local_path.exists():
            local_path.unlink()


# ==============================================================================================
# DELIVERABLES ROUTE (ADR 0011)
# ==============================================================================================
# Per-bond-future-contract deliverable basket + conversion factor + delivery /
# notice dates. Wide parquet (one row per (generic, contract_code, deliverable_
# cusip)) -> macro_data.futures_deliverables via upsert_futures_deliverables
# (a full-row upsert: a re-ingest of the same basket safely overwrites every
# non-key column). The vendor_ticker (the GENERIC, e.g. 'TY1 Comdty') is
# resolved against instrument_master like the metadata-history route does; the
# OPTIONAL deliverable_instrument_id FK is resolved by a separate CUSIP lookup
# against instrument_master.cusip (NULL when the deliverable bond is not
# registered — ADR 0011 Decision 2).
# ==============================================================================================
def _process_deliverables_blob(
    bucket: "storage.Bucket",
    blob: "storage.Blob",
    engine,
    temp_dir: Path,
) -> bool:
    """Process one deliverables parquet end-to-end.

    Returns True on success (ingested or skipped-duplicate); False on any
    failure path. Mirrors ``_process_metadata_history_blob``.
    """
    filename = Path(blob.name).name
    local_path = temp_dir / filename
    load_id: Optional[int] = None

    try:
        print(f"\nProcessing (deliverables): {filename}")

        blob.download_to_filename(str(local_path))
        df = pd.read_parquet(local_path)
        normalized_data_hash = _compute_normalized_data_hash(df)

        if df.empty:
            print(f"  [WARNING] File {filename} is empty. Skipping.")
            return False

        # --- Lineage / dedup -----------------------------------------------
        # Deliverables artifacts MUST carry lineage. A missing playbook_name is
        # a malformed artifact (the extractor always stamps it) and would also
        # break the playbook-keyed dedup -- fail loudly rather than load under
        # a junk name.
        playbook_name = _first_non_null(df, "playbook_name")
        if not playbook_name:
            print(
                f"  [ERROR] Deliverables parquet {filename} carries no "
                "playbook_name -- malformed artifact (missing lineage). "
                "Not ingested."
            )
            return False
        playbook_version = _first_non_null(df, "playbook_version")
        dataset_name = _first_non_null(df, "dataset_name")
        requested_start_date = _first_non_null(df, "requested_start_date")
        requested_end_date = _first_non_null(df, "requested_end_date")
        extracted_at = _first_non_null(df, "extracted_at")
        extraction_mode = str(
            _first_non_null(df, "extraction_mode") or "deliverables"
        ).strip().lower()

        if extraction_mode != "deliverables":
            print(
                f"  [WARNING] File {filename} is under deliverables/ but declares "
                f"extraction_mode={extraction_mode!r}. Skipping."
            )
            return False

        latest_success = get_latest_successful_load_for_playbook(engine, playbook_name)
        latest_success_hash = latest_success.get("source_file_hash") if latest_success else None

        if latest_success_hash and latest_success_hash == normalized_data_hash:
            print(
                f"  [SKIP] Parquet hash matches latest successful deliverables load "
                f"for '{playbook_name}'. Skipping ingestion."
            )
            mark_load_audit_skipped_duplicate(
                engine=engine,
                playbook_name=playbook_name,
                playbook_version=playbook_version,
                source_file_name=filename,
                source_file_hash=normalized_data_hash,
                dataset_name=dataset_name,
                requested_start_date=requested_start_date,
                requested_end_date=requested_end_date,
                extracted_at=extracted_at,
                notes=(
                    f"Skipped duplicate deliverables artifact from GCS object "
                    f"{blob.name}; source_file_hash matches the latest successful "
                    f"load for this playbook. (extraction_mode={extraction_mode})"
                ),
            )
            try:
                new_blob_name = blob.name.replace(
                    "deliverables/", "archive/deliverables/", 1
                )
                bucket.rename_blob(blob, new_blob_name)
                print(f"  [SUCCESS] Archived duplicate to gs://{BUCKET_NAME}/{new_blob_name}")
            except Exception as archive_err:
                print(
                    f"  [WARNING] Duplicate correctly skipped but GCS archival "
                    f"failed: {archive_err}. File remains in deliverables/."
                )
            return True

        audit_record = {
            "playbook_name": playbook_name,
            "playbook_version": playbook_version,
            "playbook_hash": _first_non_null(df, "playbook_hash") or normalized_data_hash,
            "git_commit_hash": _first_non_null(df, "git_commit_hash"),
            "extractor_version": _first_non_null(df, "extractor_version"),
            "source_file_name": filename,
            "source_file_hash": normalized_data_hash,
            "dataset_name": dataset_name,
            "requested_start_date": requested_start_date,
            "requested_end_date": requested_end_date,
            "extracted_at": extracted_at,
            "status": "RUNNING",
            "notes": (
                f"Processing GCS object {blob.name} | "
                f"extraction_mode={extraction_mode}"
            ),
        }

        print("  [DB] Inserting load_audit record...")
        load_id = insert_load_audit(engine, audit_record)

        # --- Resolve vendor_ticker -> instrument_id (the GENERIC) -----------
        unique_tickers = sorted({str(t) for t in df["vendor_ticker"].dropna().unique()})
        if not unique_tickers:
            msg = f"No vendor_ticker values present in deliverables parquet {filename}"
            print(f"  [ABORT] {msg}")
            update_load_audit_status(engine, load_id, "FAILED", msg)
            return False

        metadata = MetaData(schema="macro_data")
        master_table = Table("instrument_master", metadata, autoload_with=engine)
        vendor_default = _first_non_null(df, "vendor") or DEFAULT_VENDOR
        with engine.begin() as conn:
            rows = conn.execute(
                select(master_table.c.vendor_ticker, master_table.c.instrument_id)
                .where(master_table.c.vendor == vendor_default)
                .where(master_table.c.vendor_ticker.in_(unique_tickers))
            ).fetchall()
        instrument_id_map: Dict[str, int] = {r.vendor_ticker: r.instrument_id for r in rows}
        missing_tickers = [t for t in unique_tickers if t not in instrument_id_map]
        if missing_tickers:
            msg = (
                f"deliverables parquet references {len(missing_tickers)} "
                f"vendor_ticker(s) not present in instrument_master "
                f"({', '.join(missing_tickers[:5])}"
                f"{'...' if len(missing_tickers) > 5 else ''}). "
                "Ingest the bond-futures time-series playbook owning these "
                "tickers first."
            )
            print(f"  [ABORT] {msg}")
            update_load_audit_status(engine, load_id, "FAILED", msg)
            return False

        # --- Parse the parquet into record dicts ---------------------------
        # The parser is pure-Python; the optional deliverable_instrument_id FK
        # is resolved AFTER, below, by a separate CUSIP lookup (the parser is
        # DB-free by design).
        try:
            records = parquet_to_deliverables_records(
                df,
                instrument_id_map=instrument_id_map,
                load_id=load_id,
            )
        except ValueError as exc:
            msg = f"Failed to assemble deliverables records from {filename}: {exc}"
            print(f"  [ABORT] {msg}")
            update_load_audit_status(engine, load_id, "FAILED", msg)
            return False

        # --- Ingestability gate (defence-in-depth: parser already validated) -
        # The parser already validates the natural-key triple is present; this
        # gate guards against parser drift. FAIL loudly on any malformed row
        # rather than silently dropping it -- ``upsert_futures_deliverables``
        # is a full-row upsert, so a half-ingested basket must never be
        # presented as a clean load.
        ingestable: List[Dict[str, Any]] = []
        malformed: List[Dict[str, Any]] = []
        for rec in records:
            (ingestable if is_ingestable_deliverable(rec) else malformed).append(rec)

        if malformed:
            preview = "; ".join(
                f"{r.get('instrument_id')}/{r.get('contract_code')}/{r.get('deliverable_cusip')}"
                for r in malformed[:5]
            )
            msg = (
                f"deliverables parquet {filename} has {len(malformed)} malformed "
                f"row(s) -- each missing a natural-key field "
                f"(instrument_id / contract_code / deliverable_cusip): {preview}. "
                "Aborting; re-extract the artifact clean."
            )
            print(f"  [ABORT] {msg}")
            update_load_audit_status(engine, load_id, "FAILED", msg)
            return False

        if not ingestable:
            msg = (
                f"deliverables parquet {filename} produced 0 records after "
                "instrument_id resolution; nothing to write."
            )
            print(f"  [WARNING] {msg}")
            update_load_audit_status(engine, load_id, "FAILED", msg)
            return False

        # --- Per-contract date denormalisation check (ADR 0011 v2) -----------
        # first/last delivery dates and first/last notice dates are PER CONTRACT
        # but are stored on every row of the deliverable basket (denormalised --
        # one (instrument_id, contract_code, deliverable_cusip) row per
        # deliverable bond). If a parquet's basket rows disagree on those dates
        # the artifact is malformed: silently folding it in would corrupt
        # downstream joins. Codex finding 5 / ADR 0011 v2.
        date_conflicts = validate_per_contract_dates(ingestable)
        if date_conflicts:
            preview = " | ".join(date_conflicts[:3])
            extra = f" (+{len(date_conflicts) - 3} more)" if len(date_conflicts) > 3 else ""
            msg = (
                f"deliverables parquet {filename} has {len(date_conflicts)} "
                f"per-contract date conflict(s) -- denormalised "
                f"delivery/notice dates disagree across basket rows for the "
                f"same (instrument_id, contract_code): {preview}{extra}. "
                "Re-extract the artifact clean."
            )
            print(f"  [ABORT] {msg}")
            update_load_audit_status(engine, load_id, "FAILED", msg)
            return False

        # --- Optional FK: resolve deliverable_cusip -> instrument_id --------
        # When a deliverable bond IS registered in instrument_master (ADR 0003
        # gave instruments typed cusip + isin with a partial unique index on
        # cusip WHERE NOT NULL), populate ``deliverable_instrument_id`` so
        # downstream primitives can join via the FK. When the bond is NOT
        # registered (the common case -- most deliverable bonds are seasoned
        # issues outside A4's OTR-bounded universe), leave the FK NULL; a
        # consumer can still join on ``deliverable_cusip = im.cusip``.
        unique_cusips = sorted({r["deliverable_cusip"] for r in ingestable if r.get("deliverable_cusip")})
        cusip_to_instrument_id: Dict[str, int] = {}
        if unique_cusips:
            with engine.begin() as conn:
                cusip_rows = conn.execute(
                    select(master_table.c.cusip, master_table.c.instrument_id)
                    .where(master_table.c.cusip.in_(unique_cusips))
                    .where(master_table.c.cusip.isnot(None))
                ).fetchall()
            cusip_to_instrument_id = {r.cusip: r.instrument_id for r in cusip_rows}
        if cusip_to_instrument_id:
            print(
                f"  [INFO] Resolved {len(cusip_to_instrument_id)}/{len(unique_cusips)} "
                "deliverable CUSIP(s) to instrument_master rows."
            )
        for r in ingestable:
            cusip = r.get("deliverable_cusip")
            if cusip and cusip in cusip_to_instrument_id:
                r["deliverable_instrument_id"] = int(cusip_to_instrument_id[cusip])

        print(f"  [INFO] {len(ingestable)} deliverables row(s), all well-formed.")

        # --- Critical destructive section ---------------------------------
        # The upsert + audit flip commit atomically or roll back together.
        # No close-open-window step here: deliverables are not SCD2 (the basket
        # is current-state per contract; old contracts' baskets are
        # historical-immutable; the natural-key UPSERT covers refinement of
        # a still-active basket).
        with engine.begin() as conn:
            upsert_futures_deliverables(conn, ingestable)
            update_load_audit_status(
                conn,
                load_id,
                "SUCCESS",
                (
                    f"Successfully loaded deliverables from GCS object "
                    f"{blob.name} | extraction_mode={extraction_mode} | "
                    f"generics={len(instrument_id_map)} | rows={len(ingestable)} | "
                    f"linked_deliverables={len(cusip_to_instrument_id)}/{len(unique_cusips)}"
                ),
            )

        # --- Archive (best-effort) -----------------------------------------
        try:
            new_blob_name = blob.name.replace("deliverables/", "archive/deliverables/", 1)
            bucket.rename_blob(blob, new_blob_name)
            print(f"  [SUCCESS] Archived file to gs://{BUCKET_NAME}/{new_blob_name}")
        except Exception as archive_err:
            print(
                f"  [WARNING] DB load succeeded but GCS archival failed: "
                f"{archive_err}. File remains in deliverables/; dedup hash will "
                "skip it on next run."
            )

        return True

    except Exception as exc:
        print(f"  [ERROR] Failed to process deliverables parquet {filename}: {exc}")
        if load_id is not None:
            try:
                update_load_audit_status(
                    engine, load_id, "FAILED",
                    f"Processing failed for GCS object {blob.name}: {exc}",
                )
            except Exception as audit_err:
                print(f"  [ERROR] Could not update load_audit status: {audit_err}")
        return False

    finally:
        if local_path.exists():
            local_path.unlink()


# ==============================================================================================
# MAIN PIPELINE
# ==============================================================================================
def run_ingestion_pipeline():
    print("Initializing Ingestion Engine...")

    # --- AUTHENTICATION & SETUP ---
    gcp_key_path = project_root / "secure_keys" / GCP_KEY_FILENAME

    if not gcp_key_path.exists():
        # Fallback for Docker environment where key might be mapped directly
        gcp_key_path = Path("/app/secure_keys") / GCP_KEY_FILENAME
        if not gcp_key_path.exists():
            print(f"[FATAL] Cannot find GCP Key at {gcp_key_path}")
            sys.exit(1)

    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(gcp_key_path)
    client = storage.Client()
    bucket = client.bucket(BUCKET_NAME)

    engine = get_db_engine()

    # --- PHASE 1: SCAN INBOX ---
    print("\n[PHASE 1] Scanning GCP Inbox for new data...")
    timeseries_blobs = [
        b for b in bucket.list_blobs(prefix="data/")
        if b.name.endswith(".parquet")
    ]
    metadata_history_blobs = [
        b for b in bucket.list_blobs(prefix="metadata_history/")
        if b.name.endswith(".parquet")
    ]
    otr_resolution_blobs = [
        b for b in bucket.list_blobs(prefix="otr_resolution/")
        if b.name.endswith(".parquet")
    ]
    event_blobs = [
        b for b in bucket.list_blobs(prefix="events/")
        if b.name.endswith(".parquet")
    ]
    deliverables_blobs = [
        b for b in bucket.list_blobs(prefix="deliverables/")
        if b.name.endswith(".parquet")
    ]
    parquet_blobs = timeseries_blobs  # Legacy alias for the time-series loop below.

    if (
        not timeseries_blobs
        and not metadata_history_blobs
        and not otr_resolution_blobs
        and not event_blobs
        and not deliverables_blobs
    ):
        print("[INFO] No new Parquet files found in the inbox. Exiting cleanly.")
        return

    print(
        f"[INFO] Found {len(timeseries_blobs)} time-series file(s), "
        f"{len(metadata_history_blobs)} metadata-history file(s), "
        f"{len(otr_resolution_blobs)} otr-resolution file(s), "
        f"{len(event_blobs)} event-calendar file(s) and "
        f"{len(deliverables_blobs)} deliverables file(s) to process."
    )

    temp_dir = current_dir / "temp_processing"
    temp_dir.mkdir(exist_ok=True)

    # --- PHASE 2: PROCESS & UPSERT ---
    any_failures = False

    for blob in parquet_blobs:
        filename = Path(blob.name).name
        local_path = temp_dir / filename
        load_id = None  # Track for error-path status updates

        try:
            print(f"\nProcessing: {filename}")

            # 1. Download and read parquet
            blob.download_to_filename(str(local_path))
            df = pd.read_parquet(local_path)
            normalized_data_hash = _compute_normalized_data_hash(df)

            if df.empty:
                print(f"  [WARNING] File {filename} is empty. Skipping.")
                any_failures = True
                continue

            # 2. Build audit metadata and skip ingestion if this parquet artifact is
            # identical to the latest successfully ingested artifact for the same playbook.
            playbook_name = _first_non_null(df, "playbook_name") or "unknown_playbook"
            playbook_version = _first_non_null(df, "playbook_version")
            dataset_name = _first_non_null(df, "dataset_name")
            requested_start_date = _first_non_null(df, "requested_start_date")
            requested_end_date = _first_non_null(df, "requested_end_date")
            extracted_at = _first_non_null(df, "extracted_at")
            extraction_mode = str(_first_non_null(df, "extraction_mode") or "historical").strip().lower()

            latest_success = get_latest_successful_load_for_playbook(engine, playbook_name)
            latest_success_hash = latest_success.get("source_file_hash") if latest_success else None

            if latest_success_hash and latest_success_hash == normalized_data_hash:
                print(
                    f"  [SKIP] Parquet hash matches latest successful load for playbook '{playbook_name}'. "
                    "Skipping ingestion."
                )
                mark_load_audit_skipped_duplicate(
                    engine=engine,
                    playbook_name=playbook_name,
                    playbook_version=playbook_version,
                    source_file_name=filename,
                    source_file_hash=normalized_data_hash,
                    dataset_name=dataset_name,
                    requested_start_date=requested_start_date,
                    requested_end_date=requested_end_date,
                    extracted_at=extracted_at,
                    notes=(
                        f"Skipped duplicate artifact from GCS object {blob.name}; "
                        "source_file_hash matches the latest successful load for this playbook. "
                        f"(extraction_mode={extraction_mode})"
                    ),
                )
                try:
                    new_blob_name = blob.name.replace("data/", "archive/data/", 1)
                    bucket.rename_blob(blob, new_blob_name)
                    print(f"  [SUCCESS] Archived duplicate file to gs://{BUCKET_NAME}/{new_blob_name}")
                except Exception as archive_err:
                    print(
                        f"  [WARNING] Duplicate correctly skipped but GCS archival failed: "
                        f"{archive_err}. File remains in data/; dedup hash will skip it on next run."
                    )
                continue

            audit_record = {
                "playbook_name": playbook_name,
                "playbook_version": playbook_version,
                "playbook_hash": _first_non_null(df, "playbook_hash") or normalized_data_hash,
                "git_commit_hash": _first_non_null(df, "git_commit_hash"),
                "extractor_version": _first_non_null(df, "extractor_version"),
                "source_file_name": filename,
                "source_file_hash": normalized_data_hash,
                "dataset_name": dataset_name,
                "requested_start_date": requested_start_date,
                "requested_end_date": requested_end_date,
                "extracted_at": extracted_at,
                "status": "RUNNING",
                "notes": f"Processing GCS object {blob.name} | extraction_mode={extraction_mode}",
            }

            print("  [DB] Inserting load_audit record...")
            load_id = insert_load_audit(engine, audit_record)

            # 3. Build richer instrument master records
            # Build one full metadata row per ticker instead of whitelisting a narrow set of columns.
            # This makes ingestion robust to new reference metadata fields like `security_name`,
            # `contract_multiplier`, `quote_units`, etc. without requiring code changes each time.
            unique_assets = (
                df.groupby("ticker", dropna=False, as_index=False)
                .agg(lambda series: _first_non_null_value(series))
            )

            master_records: List[Dict[str, Any]] = []
            for _, row in unique_assets.iterrows():
                vendor_ticker = row.get("ticker")
                if pd.isna(vendor_ticker):
                    continue

                record = {
                    "vendor": row.get("vendor") if pd.notna(row.get("vendor")) else DEFAULT_VENDOR,
                    "vendor_ticker": vendor_ticker,
                    "asset_class": row.get("asset_class") if pd.notna(row.get("asset_class")) else dataset_name,
                    "instrument_type": _infer_instrument_type(row, dataset_name),
                    "curve_family": row.get("curve_family") if pd.notna(row.get("curve_family")) else None,
                    "country": row.get("country") if pd.notna(row.get("country")) else None,
                    "currency": row.get("currency") if pd.notna(row.get("currency")) else None,
                    "tenor": row.get("tenor") if pd.notna(row.get("tenor")) else None,
                    "underlying_index": row.get("underlying_index") if pd.notna(row.get("underlying_index")) else None,
                    "contract_code": row.get("contract_code") if pd.notna(row.get("contract_code")) else None,
                    # Cash-bond identity (ADR 0003) — typed columns on instrument_master.
                    # NULL for every non-cash-bond playbook (none declare these fields today).
                    "cusip": row.get("cusip") if pd.notna(row.get("cusip")) else None,
                    "isin": row.get("isin") if pd.notna(row.get("isin")) else None,
                    "expiry_date": row.get("expiry_date") if pd.notna(row.get("expiry_date")) else None,
                    "maturity_date": row.get("maturity_date") if pd.notna(row.get("maturity_date")) else None,
                    "is_rolling_contract": _safe_bool(row.get("is_rolling_contract"), False),
                    "is_active": _safe_bool(row.get("is_active"), True),
                    # Any metadata not mapped to structured columns is preserved in JSONB attributes.
                    "attributes": _build_instrument_attributes(row, filename),
                }
                master_records.append(record)

            print(f"  [DB] Verifying/Upserting {len(master_records)} instruments in instrument_master...")

            # 4. Sanity gate: refuse to proceed if incoming data has significantly
            #    fewer instruments than the previous successful load.  This prevents
            #    a partial Bloomberg extraction from wiping good history.
            #    Runs BEFORE instrument_master upsert so a failed gate causes zero
            #    DB mutations.
            incoming_instrument_count = len(master_records)
            if latest_success and latest_success.get("load_id"):
                existing_instrument_count = count_instruments_in_load(
                    engine, latest_success["load_id"]
                )
                if existing_instrument_count > 0:
                    coverage = incoming_instrument_count / existing_instrument_count
                    if coverage < 0.8:
                        msg = (
                            f"Sanity gate FAILED: incoming parquet has "
                            f"{incoming_instrument_count} instruments vs "
                            f"{existing_instrument_count} in the previous successful "
                            f"load (coverage {coverage:.0%}, threshold 80%). "
                            f"Aborting to prevent data loss."
                        )
                        print(f"  [ABORT] {msg}")
                        update_load_audit_status(engine, load_id, "FAILED", msg)
                        any_failures = True
                        # Do NOT archive — leave in data/ for investigation
                        continue
                    print(
                        f"  [OK] Sanity gate passed: {incoming_instrument_count}/"
                        f"{existing_instrument_count} instruments ({coverage:.0%})."
                    )

            # 5. Upsert instrument master (only reached if sanity gate passes).
            #    Lives OUTSIDE the critical transaction below because
            #    instrument_master is idempotent on (vendor, vendor_ticker)
            #    and a retry simply re-upserts the same rows.  Including it
            #    in the critical txn would extend the lock window without
            #    correctness benefit.
            instrument_id_map = upsert_instrument_master(engine, master_records)

            # 6. CRITICAL SECTION — single transaction wrapping
            #    delete + upsert + audit-flip so a failure between any two
            #    of them rolls back cleanly.
            #
            #    Before Phase 0 PR 3 this was three separate transactions:
            #    if DELETE committed and UPSERT failed, prior data was
            #    destroyed without the audit row recording the failure
            #    (it would still be in RUNNING).  See
            #    ``docs/technical_debt.md`` item #1 for the historical
            #    failure mode.
            #
            #    The ``with engine.begin() as conn:`` block commits on
            #    clean exit and rolls back on ANY exception, which propagates
            #    to the outer ``except`` handler that flips the audit row
            #    to FAILED on a SEPARATE transaction.
            with engine.begin() as conn:
                if extraction_mode == "historical":
                    deleted_rows = _delete_existing_playbook_scope(
                        connectable=conn,
                        instrument_id_map=instrument_id_map,
                        playbook_name=playbook_name,
                        current_load_id=load_id,
                    )
                    print(
                        f"  [DB] Deleted {deleted_rows} existing rows for playbook '{playbook_name}' "
                        "before historical reload..."
                    )
                elif extraction_mode == "incremental":
                    deleted_rows = _delete_existing_playbook_window(
                        connectable=conn,
                        instrument_id_map=instrument_id_map,
                        playbook_name=playbook_name,
                        current_load_id=load_id,
                        requested_start_date=requested_start_date,
                        requested_end_date=requested_end_date,
                    )
                    print(
                        f"  [DB] Deleted {deleted_rows} existing rows for playbook '{playbook_name}' "
                        f"inside requested incremental window {requested_start_date} -> {requested_end_date}..."
                    )
                else:
                    raise ValueError(
                        f"Unsupported extraction_mode '{extraction_mode}' in parquet {filename}. "
                        "Expected 'historical' or 'incremental'."
                    )

                # 7. Upsert daily time-series data using instrument_id + load_id.
                #    Upserted in bounded batches (MARKET_DATA_UPSERT_BATCH_SIZE)
                #    but ALL batches run on this same ``conn`` inside the
                #    critical transaction — delete + every upsert batch +
                #    audit-flip still commit atomically or roll back together.
                print(f"  [DB] Upserting {len(df)} daily market data rows...")
                upsert_market_data_daily(
                    connectable=conn,
                    df=df,
                    instrument_id_map=instrument_id_map,
                    load_id=load_id,
                    batch_size=MARKET_DATA_UPSERT_BATCH_SIZE,
                )

                # 8. All DB mutations succeeded — flip audit row to SUCCESS
                #    INSIDE the critical transaction so the audit state and
                #    the data state commit atomically.
                update_load_audit_status(
                    conn, load_id, "SUCCESS",
                    f"Successfully loaded from GCS object {blob.name} | "
                    f"extraction_mode={extraction_mode} | "
                    f"instruments={len(instrument_id_map)} | rows={len(df)}",
                )
            # Critical transaction committed at this point.  Any subsequent
            # failure (archival below) cannot affect the DB state.

            # 9. Archive processed file in GCP (best-effort — does not affect
            #    DB status).  If archival fails, the file stays in data/ and
            #    the next run's dedup hash check will skip it.
            try:
                new_blob_name = blob.name.replace("data/", "archive/data/", 1)
                bucket.rename_blob(blob, new_blob_name)
                print(f"  [SUCCESS] Archived file to gs://{BUCKET_NAME}/{new_blob_name}")
            except Exception as archive_err:
                print(
                    f"  [WARNING] DB load succeeded but GCS archival failed: {archive_err}. "
                    f"File remains in data/; dedup hash will skip it on next run."
                )

        except Exception as e:
            any_failures = True
            print(f"  [ERROR] Failed to process {filename}: {e}")
            # Mark the audit row as FAILED if it was created
            if load_id is not None:
                try:
                    update_load_audit_status(
                        engine, load_id, "FAILED",
                        f"Processing failed for GCS object {blob.name}: {e}",
                    )
                except Exception as audit_err:
                    print(f"  [ERROR] Could not update load_audit status: {audit_err}")
            # File remains in data/ for retry on the next run

        finally:
            if local_path.exists():
                local_path.unlink()

    # --- PHASE 3: METADATA-HISTORY PROCESSING (ADR 0002) ---
    # Independent of the time-series loop above. Each parquet is processed
    # via _process_metadata_history_blob, which manages its own dedup
    # check, audit row, atomic destructive section, and archive — same
    # contract as the time-series flow.
    if metadata_history_blobs:
        print(
            f"\n[PHASE 3] Processing {len(metadata_history_blobs)} metadata-history parquet(s)..."
        )
        for blob in metadata_history_blobs:
            ok = _process_metadata_history_blob(
                bucket=bucket,
                blob=blob,
                engine=engine,
                temp_dir=temp_dir,
            )
            if not ok:
                any_failures = True

    # --- PHASE 4: OTR-RESOLUTION PROCESSING (ADR 0007) ---
    # Independent of the loops above. Each parquet is processed via
    # _process_otr_resolution_blob, which manages its own dedup check, audit
    # row, atomic destructive section, and archive — same contract as the
    # other flows. Destination is macro_data.otr_history (SCD2).
    if otr_resolution_blobs:
        print(
            f"\n[PHASE 4] Processing {len(otr_resolution_blobs)} otr-resolution parquet(s)..."
        )
        for blob in otr_resolution_blobs:
            ok = _process_otr_resolution_blob(
                bucket=bucket,
                blob=blob,
                engine=engine,
                temp_dir=temp_dir,
            )
            if not ok:
                any_failures = True

    # --- PHASE 5: EVENT-CALENDAR PROCESSING (ADR 0004) ---
    # Independent of the loops above. Each parquet is processed via
    # _process_event_blob, which manages its own dedup check, audit row,
    # atomic destructive section, and archive — same contract as the other
    # flows. Destination is macro_data.event_calendar.
    if event_blobs:
        print(
            f"\n[PHASE 5] Processing {len(event_blobs)} event-calendar parquet(s)..."
        )
        for blob in event_blobs:
            ok = _process_event_blob(
                bucket=bucket,
                blob=blob,
                engine=engine,
                temp_dir=temp_dir,
            )
            if not ok:
                any_failures = True

    # --- PHASE 6: DELIVERABLES (ADR 0011) -------------------------------------
    # Per-bond-future-contract deliverable basket + conversion factor +
    # delivery / notice dates. Each blob is processed end-to-end via
    # _process_deliverables_blob, which manages its own dedup check, audit
    # row, atomic destructive section, optional CUSIP->FK lookup, and archive.
    # Destination is macro_data.futures_deliverables.
    if deliverables_blobs:
        print(
            f"\n[PHASE 6] Processing {len(deliverables_blobs)} deliverables parquet(s)..."
        )
        for blob in deliverables_blobs:
            ok = _process_deliverables_blob(
                bucket=bucket,
                blob=blob,
                engine=engine,
                temp_dir=temp_dir,
            )
            if not ok:
                any_failures = True

    try:
        temp_dir.rmdir()
    except OSError:
        pass

    if any_failures:
        print("\n*** INGESTION PIPELINE COMPLETE (WITH FAILURES) ***")
        sys.exit(1)

    print("\n*** INGESTION PIPELINE COMPLETE ***")


if __name__ == "__main__":
    run_ingestion_pipeline()
