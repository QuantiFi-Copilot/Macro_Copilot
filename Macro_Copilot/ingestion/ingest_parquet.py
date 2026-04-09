import os
import sys
import hashlib
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
    get_db_engine,
    upsert_instrument_master,
    insert_load_audit,
    upsert_market_data_daily,
    get_latest_successful_load_for_playbook,
    mark_load_audit_skipped_duplicate,
)

# --- CONFIGURATION ---
BUCKET_NAME = "macro-storage-bucket"
GCP_KEY_FILENAME = "library-extractor-key.json"
DEFAULT_VENDOR = "BLOOMBERG"

NORMALIZED_HASH_EXCLUDED_COLUMNS = {
    "playbook_hash",
    "git_commit_hash",
    "extractor_version",
    "extraction_mode",
    "requested_start_date",
    "requested_end_date",
    "extracted_at",
    "source_file",
    "source_file_name",
    "source_file_hash",
    "normalized_data_hash",
    "load_id",
    "created_at",
    "updated_at",
    "ingested_at",
    "notes",
    "status",
}


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


def _normalize_hash_value(value: Any) -> str:
    """
    Convert values to a deterministic string representation for stable hashing.
    """
    if pd.isna(value):
        return ""

    if isinstance(value, pd.Timestamp):
        return value.isoformat()

    if hasattr(value, "isoformat") and not isinstance(value, str):
        try:
            return value.isoformat()
        except Exception:
            pass

    if isinstance(value, bool):
        return "true" if value else "false"

    if hasattr(value, "item"):
        try:
            value = value.item()
        except Exception:
            pass

    return str(value)



def _build_normalized_hash_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """
    Build a normalized dataframe for semantic dedup hashing.

    The goal is to hash the meaningful extracted dataset contents while excluding
    run-variant lineage fields such as extracted_at and playbook/git metadata.
    """
    normalized = df.copy()

    keep_columns = [c for c in normalized.columns if c not in NORMALIZED_HASH_EXCLUDED_COLUMNS]
    normalized = normalized[keep_columns]

    for col in normalized.columns:
        normalized[col] = normalized[col].map(_normalize_hash_value)

    normalized = normalized.reindex(sorted(normalized.columns), axis=1)
    normalized = normalized.sort_values(by=list(normalized.columns), kind="mergesort").reset_index(drop=True)
    return normalized



def _compute_normalized_data_hash(df: pd.DataFrame) -> str:
    """
    Compute a stable SHA256 hash of the economically meaningful dataframe contents.

    This intentionally excludes run-specific lineage columns so identical extracted
    data across runs will deduplicate even if extracted_at or parquet metadata changes.
    """
    normalized = _build_normalized_hash_dataframe(df)
    csv_payload = normalized.to_csv(index=False, lineterminator="\n")
    return hashlib.sha256(csv_payload.encode("utf-8")).hexdigest()



def _delete_existing_playbook_scope(
    engine,
    instrument_id_map: Dict[str, int],
    playbook_name: str,
    current_load_id: int,
) -> int:
    """
    Remove existing daily rows for the exact playbook-owned instrument scope being reloaded.

    This keeps `market_data_daily` as the latest clean truth for the current playbook
    scope and removes stale rows for fields that may have been dropped from the playbook
    in newer versions.
    """
    instrument_ids: List[int] = sorted({int(v) for v in instrument_id_map.values() if v is not None})

    if not instrument_ids or not playbook_name:
        return 0

    metadata = MetaData(schema="macro_data")
    market_data_table = Table("market_data_daily", metadata, autoload_with=engine)
    load_audit_table = Table("load_audit", metadata, autoload_with=engine)

    prior_load_ids_stmt = (
        select(load_audit_table.c.load_id)
        .where(load_audit_table.c.playbook_name == playbook_name)
        .where(load_audit_table.c.load_id != current_load_id)
    )

    stmt = delete(market_data_table).where(
        market_data_table.c.instrument_id.in_(instrument_ids),
        market_data_table.c.load_id.in_(prior_load_ids_stmt),
    )

    with engine.begin() as conn:
        result = conn.execute(stmt)

    return result.rowcount or 0


def _delete_existing_playbook_window(
    engine,
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
    market_data_table = Table("market_data_daily", metadata, autoload_with=engine)
    load_audit_table = Table("load_audit", metadata, autoload_with=engine)

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

    with engine.begin() as conn:
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
            return

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
    for blob in parquet_blobs:
        filename = Path(blob.name).name
        local_path = temp_dir / filename

        try:
            print(f"\nProcessing: {filename}")

            # 1. Download and read parquet
            blob.download_to_filename(str(local_path))
            df = pd.read_parquet(local_path)
            normalized_data_hash = _compute_normalized_data_hash(df)

            if df.empty:
                print(f"  [WARNING] File {filename} is empty. Skipping.")
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
                new_blob_name = blob.name.replace("data/", "archive/data/", 1)
                bucket.rename_blob(blob, new_blob_name)
                print(f"  [SUCCESS] Archived duplicate file to gs://{BUCKET_NAME}/{new_blob_name}")
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
                "status": "SUCCESS",
                "notes": f"Loaded from GCS object {blob.name} | extraction_mode={extraction_mode}",
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
            instrument_id_map = upsert_instrument_master(engine, master_records)

            # 4. Delete the appropriate overlap before reloading, depending on extraction mode.
            if extraction_mode == "historical":
                deleted_rows = _delete_existing_playbook_scope(
                    engine=engine,
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
                    engine=engine,
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

            # 5. Upsert daily time-series data using instrument_id + load_id
            print(f"  [DB] Upserting {len(df)} daily market data rows...")
            upsert_market_data_daily(
                engine=engine,
                df=df,
                instrument_id_map=instrument_id_map,
                load_id=load_id,
            )

            # 6. Archive processed file in GCP
            new_blob_name = blob.name.replace("data/", "archive/data/", 1)
            bucket.rename_blob(blob, new_blob_name)
            print(f"  [SUCCESS] Archived file to gs://{BUCKET_NAME}/{new_blob_name}")

        except Exception as e:
            print(f"  [ERROR] Failed to process {filename}: {e}")
            # File remains in data/ for retry on the next run

        finally:
            if local_path.exists():
                local_path.unlink()

    try:
        temp_dir.rmdir()
    except OSError:
        pass

    print("\n*** INGESTION PIPELINE COMPLETE ***")


if __name__ == "__main__":
    run_ingestion_pipeline()
