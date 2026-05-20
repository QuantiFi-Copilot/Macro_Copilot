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

            print(f"  [DB] Upserting {len(records)} metadata-history rows...")
            upsert_instrument_metadata_history(conn, records)

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
    parquet_blobs = timeseries_blobs  # Legacy alias for the time-series loop below.

    if not timeseries_blobs and not metadata_history_blobs:
        print("[INFO] No new Parquet files found in the inbox. Exiting cleanly.")
        return

    print(
        f"[INFO] Found {len(timeseries_blobs)} time-series file(s) and "
        f"{len(metadata_history_blobs)} metadata-history file(s) to process."
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
