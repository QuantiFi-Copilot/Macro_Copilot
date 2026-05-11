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
)

# The dedup hash logic lives in its own module so it can be imported
# (and tested for cross-version stability) without dragging in the
# google.cloud / database imports above.  Re-export the public name
# under its historical alias for any external callers.
from ingestion.hashing import (  # noqa: E402
    NORMALIZED_HASH_EXCLUDED_COLUMNS,
    compute_normalized_data_hash as _compute_normalized_data_hash,
)

# --- CONFIGURATION ---
BUCKET_NAME = "macro-storage-bucket"
GCP_KEY_FILENAME = "library-extractor-key.json"
DEFAULT_VENDOR = "BLOOMBERG"


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
    blobs = list(bucket.list_blobs(prefix="data/"))
    parquet_blobs = [b for b in blobs if b.name.endswith(".parquet")]

    if not parquet_blobs:
        print("[INFO] No new Parquet files found in the inbox. Exiting cleanly.")
        return

    print(f"[INFO] Found {len(parquet_blobs)} file(s) to process.")

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
                print(f"  [DB] Upserting {len(df)} daily market data rows...")
                upsert_market_data_daily(
                    connectable=conn,
                    df=df,
                    instrument_id_map=instrument_id_map,
                    load_id=load_id,
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
