import os
import sys
import yaml
import hashlib
import subprocess
import pandas as pd
from datetime import datetime, timedelta, date
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from google.cloud import storage
from xbbg import blp

# --- CONFIGURATION ---
BUCKET_NAME = os.getenv("GCP_BUCKET_NAME", "macro-storage-bucket")
GCP_KEY_FILENAME = os.getenv("GCP_KEY_FILENAME", "library-extractor-key.json")
DEFAULT_VENDOR = os.getenv("DATA_VENDOR", "BLOOMBERG")
DEFAULT_LOOKBACK_DAYS = int(os.getenv("DEFAULT_LOOKBACK_DAYS", "30"))
MAX_HISTORICAL_FIELDS_PER_REQUEST = int(os.getenv("MAX_HISTORICAL_FIELDS_PER_REQUEST", "25"))
MAX_REFERENCE_FIELDS_PER_REQUEST = int(os.getenv("MAX_REFERENCE_FIELDS_PER_REQUEST", "50"))


def _resolve_gcp_key_path(base_dir: Path) -> Optional[Path]:
    candidates = [
        base_dir / GCP_KEY_FILENAME,
        base_dir / "secure_keys" / GCP_KEY_FILENAME,
        Path("/app/secure_keys") / GCP_KEY_FILENAME,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _get_git_commit_hash(work_dir: Path) -> Optional[str]:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(work_dir),
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip() or None
    except Exception:
        return None


def _get_extractor_version(script_path: Path) -> str:
    explicit = os.getenv("EXTRACTOR_VERSION")
    if explicit:
        return explicit
    return f"terminal_extractor:{_sha256_file(script_path)[:12]}"


def _chunked(items: List[str], size: int) -> Iterable[List[str]]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _safe_iso_date(value: Any) -> Optional[str]:
    if value is None or value == "":
        return None
    return pd.to_datetime(value).strftime("%Y-%m-%d")


def _clean_scalar(value: Any) -> Any:
    """
    Normalize scalar outputs from reference data for parquet safety.
    Dates become ISO strings, pandas timestamps become ISO strings,
    numpy scalars become Python scalars, NaN/NaT become None.
    """
    if value is None:
        return None

    try:
        if pd.isna(value):
            return None
    except Exception:
        pass

    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")

    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass

    return value


def _resolve_date_window(playbook: Dict[str, Any]) -> Tuple[str, str]:
    extraction_cfg = playbook.get("extraction", {}) or {}

    today = datetime.today()
    end_date = (
        extraction_cfg.get("end_date")
        or playbook.get("end_date")
        or today.strftime("%Y-%m-%d")
    )
    end_date = _safe_iso_date(end_date)

    incremental_window_days = extraction_cfg.get("incremental_window_days")
    if incremental_window_days is None:
        incremental_window_days = playbook.get("incremental_window_days")

    if incremental_window_days is None:
        lookback_days = extraction_cfg.get("lookback_days")
        if lookback_days is None:
            lookback_days = playbook.get("lookback_days", DEFAULT_LOOKBACK_DAYS)
        incremental_window_days = lookback_days

    start_date = (pd.to_datetime(end_date) - timedelta(days=int(incremental_window_days))).strftime("%Y-%m-%d")

    if pd.to_datetime(start_date) > pd.to_datetime(end_date):
        raise ValueError(f"Invalid date window: start_date={start_date} is after end_date={end_date}.")

    return start_date, end_date

def _normalize_bdh_output(df: pd.DataFrame, fallback_ticker: str) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["trade_date", "ticker", "field_name", "field_value"])

    if isinstance(df.columns, pd.MultiIndex):
        long_df = (
            df.stack(level=list(range(df.columns.nlevels)), future_stack=True)
            .rename("field_value")
            .reset_index()
        )
        if df.columns.nlevels == 2:
            long_df.columns = ["trade_date", "ticker", "field_name", "field_value"]
        else:
            rename_cols = ["trade_date"] + [f"column_level_{i}" for i in range(1, df.columns.nlevels + 1)] + ["field_value"]
            long_df.columns = rename_cols
            if "column_level_1" in long_df.columns:
                long_df = long_df.rename(columns={"column_level_1": "ticker"})
            if "column_level_2" in long_df.columns:
                long_df = long_df.rename(columns={"column_level_2": "field_name"})
            if "ticker" not in long_df.columns:
                long_df["ticker"] = fallback_ticker
            if "field_name" not in long_df.columns:
                raise ValueError("Unable to normalize Bloomberg output: missing field level in MultiIndex columns.")
            long_df = long_df[["trade_date", "ticker", "field_name", "field_value"]]
    else:
        long_df = df.stack(future_stack=True).rename("field_value").reset_index()
        if len(long_df.columns) == 3:
            long_df.columns = ["trade_date", "field_name", "field_value"]
            long_df["ticker"] = fallback_ticker
            long_df = long_df[["trade_date", "ticker", "field_name", "field_value"]]
        else:
            raise ValueError("Unable to normalize Bloomberg output for non-MultiIndex columns.")

    long_df["trade_date"] = pd.to_datetime(long_df["trade_date"]).dt.strftime("%Y-%m-%d")
    long_df["field_name"] = long_df["field_name"].astype(str).str.upper()
    long_df = long_df.dropna(subset=["field_value"])
    return long_df


def _normalize_bdp_output(df: Any, fallback_ticker: str) -> Dict[str, Any]:
    """
    Convert xbbg bdp output to a simple field -> scalar mapping.

    Expected common shape:
    - DataFrame indexed by ticker with columns = Bloomberg fields
    """
    if df is None:
        return {}

    if isinstance(df, pd.Series):
        return {str(k): _clean_scalar(v) for k, v in df.items()}

    if isinstance(df, pd.DataFrame):
        if df.empty:
            return {}

        if fallback_ticker in df.index:
            row = df.loc[fallback_ticker]
            if isinstance(row, pd.DataFrame):
                row = row.iloc[0]
            return {str(k): _clean_scalar(v) for k, v in row.items()}

        if len(df) == 1:
            row = df.iloc[0]
            return {str(k): _clean_scalar(v) for k, v in row.items()}

    return {}


def _build_historical_request_kwargs(playbook: Dict[str, Any]) -> Dict[str, Any]:
    extraction_cfg = playbook.get("extraction", {}) or {}
    bdh_kwargs = extraction_cfg.get("bdh_kwargs") or playbook.get("bdh_kwargs") or {}
    if not isinstance(bdh_kwargs, dict):
        raise ValueError("`bdh_kwargs` must be a dictionary when provided.")
    return bdh_kwargs.copy()


def _build_reference_request_kwargs(playbook: Dict[str, Any]) -> Dict[str, Any]:
    extraction_cfg = playbook.get("extraction", {}) or {}
    bdp_kwargs = extraction_cfg.get("bdp_kwargs") or playbook.get("bdp_kwargs") or {}
    if not isinstance(bdp_kwargs, dict):
        raise ValueError("`bdp_kwargs` must be a dictionary when provided.")
    return bdp_kwargs.copy()


def _get_playbook_metadata(playbook: Dict[str, Any], pb_path: Path, script_path: Path) -> Dict[str, Any]:
    playbook_name = playbook.get("playbook_name") or pb_path.stem
    playbook_version = str(playbook.get("playbook_version", "1.0"))
    asset_class = playbook.get("asset_class", "unknown_asset")
    dataset_name = playbook.get("dataset_name") or asset_class
    playbook_hash = _sha256_file(pb_path)
    git_commit_hash = _get_git_commit_hash(pb_path.parent)
    extractor_version = _get_extractor_version(script_path)

    return {
        "playbook_name": playbook_name,
        "playbook_version": playbook_version,
        "asset_class": asset_class,
        "dataset_name": dataset_name,
        "playbook_hash": playbook_hash,
        "git_commit_hash": git_commit_hash,
        "extractor_version": extractor_version,
    }


def _fetch_reference_metadata(
    ticker: str,
    reference_metrics: List[Dict[str, Any]],
    request_kwargs: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Fetch static/reference metadata once per ticker using bdp().
    Returns a dict keyed by playbook-defined column names.
    """
    if not reference_metrics:
        return {}

    valid_metrics = [
        metric for metric in reference_metrics
        if isinstance(metric, dict)
        and metric.get("column_name")
        and metric.get("bloomberg_field")
    ]
    if not valid_metrics:
        return {}

    field_to_column = {
        str(metric["bloomberg_field"]).upper(): metric["column_name"]
        for metric in valid_metrics
    }
    requested_fields = list(field_to_column.keys())

    raw_values: Dict[str, Any] = {}
    for field_chunk in _chunked(requested_fields, MAX_REFERENCE_FIELDS_PER_REQUEST):
        try:
            df = blp.bdp(
                tickers=ticker,
                flds=field_chunk,
                **request_kwargs,
            )
            normalized = _normalize_bdp_output(df, fallback_ticker=ticker)
            for field_name, value in normalized.items():
                raw_values[str(field_name).upper()] = value
        except Exception as exc:
            print(f"    [WARNING] Reference data fetch failed for {ticker} fields {field_chunk}: {exc}")

    mapped: Dict[str, Any] = {}
    for bloomberg_field, column_name in field_to_column.items():
        mapped[column_name] = raw_values.get(bloomberg_field)

    return mapped


def run_incremental_extraction():
    """
    Pull playbooks from GCP, extract Bloomberg historical data, enrich with optional
    reference/static metadata, save long-format parquet, and upload results to GCP.
    """
    print("Initializing Library Extraction Agent...")

    base_dir = Path(__file__).resolve().parent
    script_path = Path(__file__).resolve()
    gcp_key_path = _resolve_gcp_key_path(base_dir)

    temp_playbooks_dir = base_dir / "temp_playbooks"
    temp_data_dir = base_dir / "temp_data"
    temp_playbooks_dir.mkdir(exist_ok=True)
    temp_data_dir.mkdir(exist_ok=True)

    any_failures = False

    try:
        if not gcp_key_path:
            print(f"[FATAL] Cannot find GCP Key '{GCP_KEY_FILENAME}' in expected locations.")
            any_failures = True
            return

        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(gcp_key_path)
        client = storage.Client()
        bucket = client.bucket(BUCKET_NAME)

        print("\n[PHASE 1] Pulling playbooks from GCP...")
        blobs = bucket.list_blobs(prefix="playbooks/")
        playbook_files: List[Path] = []

        for blob in blobs:
            if blob.name.endswith(".yml") or blob.name.endswith(".yaml"):
                local_path = temp_playbooks_dir / Path(blob.name).name
                blob.download_to_filename(str(local_path))
                playbook_files.append(local_path)
                print(f"  -> Downloaded: {Path(blob.name).name}")

        if not playbook_files:
            print("[ABORT] No playbooks found in the GCP bucket. Exiting.")
            any_failures = True
            return

        print("\n[PHASE 2] Executing Bloomberg Extraction...")

        for pb_path in playbook_files:
            with open(pb_path, "r", encoding="utf-8") as f:
                playbook = yaml.safe_load(f) or {}

            lineage_meta = _get_playbook_metadata(playbook, pb_path, script_path)
            asset_class = lineage_meta["asset_class"]
            dataset_name = lineage_meta["dataset_name"]
            universe_items = playbook.get("universe", [])
            target_metrics = playbook.get("target_metrics", [])
            reference_metrics = playbook.get("reference_metrics", [])
            historical_request_kwargs = _build_historical_request_kwargs(playbook)
            reference_request_kwargs = _build_reference_request_kwargs(playbook)
            start_date, end_date = _resolve_date_window(playbook)

            universe_items = [item for item in universe_items if isinstance(item, dict) and "ticker" in item]
            historical_fields = [
                item["bloomberg_field"]
                for item in target_metrics
                if isinstance(item, dict) and "bloomberg_field" in item
            ]

            if not universe_items:
                print(f"\n[WARNING] No valid tickers found in {pb_path.name}. Skipping.")
                any_failures = True
                continue

            if not historical_fields:
                print(f"\n[WARNING] No Bloomberg fields found in {pb_path.name}. Skipping.")
                any_failures = True
                continue

            print(f"\nProcessing Playbook: {pb_path.name}")
            print(f"  Playbook name: {lineage_meta['playbook_name']}")
            print(f"  Playbook version: {lineage_meta['playbook_version']}")
            print(f"  Asset class: {asset_class}")
            print(f"  Dataset name: {dataset_name}")
            print(f"  Tickers: {len(universe_items)}")
            print(f"  Historical fields: {len(historical_fields)}")
            print(f"  Reference fields: {len(reference_metrics)}")
            print(f"  Date range: {start_date} -> {end_date}")

            all_data_frames: List[pd.DataFrame] = []
            extracted_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            default_item_meta = {
                "vendor": playbook.get("vendor", DEFAULT_VENDOR),
                "asset_class": asset_class,
                "instrument_type": playbook.get("instrument_type") or playbook.get("default_instrument_type"),
                "curve_family": playbook.get("curve_family"),
                "underlying_index": playbook.get("underlying_index"),
                "is_rolling_contract": playbook.get("is_rolling_contract", False),
                "is_active": playbook.get("is_active", True),
            }

            for item in universe_items:
                ticker = item["ticker"]
                asset_metadata = {k: v for k, v in {**default_item_meta, **item}.items() if k != "ticker"}
                asset_metadata.setdefault("vendor", DEFAULT_VENDOR)
                asset_metadata.setdefault("asset_class", asset_class)

                try:
                    print(f"  Fetching historical data for {ticker}...")
                    ticker_frames: List[pd.DataFrame] = []

                    for field_chunk in _chunked(historical_fields, MAX_HISTORICAL_FIELDS_PER_REQUEST):
                        df = blp.bdh(
                            tickers=ticker,
                            flds=field_chunk,
                            start_date=start_date,
                            end_date=end_date,
                            **historical_request_kwargs,
                        )
                        normalized = _normalize_bdh_output(df, fallback_ticker=ticker)
                        if not normalized.empty:
                            ticker_frames.append(normalized)

                    if not ticker_frames:
                        print(f"    [!] No historical data returned for {ticker}")
                        continue

                    long_df = pd.concat(ticker_frames, ignore_index=True).drop_duplicates(
                        subset=["trade_date", "ticker", "field_name"], keep="last"
                    )

                    if long_df.empty:
                        print(f"    [!] Historical data returned for {ticker}, but all values were null after cleaning.")
                        continue

                    if reference_metrics:
                        print(f"    [INFO] Fetching reference metadata for {ticker}...")
                        ref_metadata = _fetch_reference_metadata(
                            ticker=ticker,
                            reference_metrics=reference_metrics,
                            request_kwargs=reference_request_kwargs,
                        )
                        asset_metadata.update(ref_metadata)

                    for meta_key, meta_val in asset_metadata.items():
                        long_df[meta_key] = _clean_scalar(meta_val)

                    for meta_key, meta_val in lineage_meta.items():
                        long_df[meta_key] = meta_val

                    long_df["requested_start_date"] = start_date
                    long_df["requested_end_date"] = end_date
                    long_df["extracted_at"] = extracted_at
                    long_df["extraction_mode"] = "incremental"

                    all_data_frames.append(long_df)
                    print(f"    [OK] Extracted {len(long_df)} rows for {ticker}")

                except Exception as exc:
                    print(f"    [ERROR] Failed on {ticker}: {exc}")

            if all_data_frames:
                # Coverage gate: refuse to upload if too many tickers failed.
                # This prevents a partial Bloomberg extraction from becoming
                # authoritative truth after ingestion deletes existing data.
                extracted_count = len(all_data_frames)
                expected_count = len(universe_items)
                if expected_count > 0:
                    coverage = extracted_count / expected_count
                    if coverage < 0.9:
                        print(
                            f"\n  [ABORT] Coverage gate: only {extracted_count}/"
                            f"{expected_count} tickers extracted ({coverage:.0%}). "
                            f"Refusing to upload partial data for {dataset_name}. "
                            f"Threshold is 90%."
                        )
                        any_failures = True
                        continue

                print(f"\n[PHASE 3] Compiling and Pushing Data for {dataset_name}...")

                final_df = pd.concat(all_data_frames, ignore_index=True)
                final_df = final_df.dropna(subset=["field_value"])

                if final_df.empty:
                    print(f"  [WARNING] Final dataframe for {dataset_name} is empty after cleaning. Skipping upload.")
                    any_failures = True
                    continue

                preferred_order = [
                    "trade_date",
                    "ticker",
                    "field_name",
                    "field_value",
                    "vendor",
                    "asset_class",
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
                    "dataset_name",
                    "playbook_name",
                    "playbook_version",
                    "playbook_hash",
                    "git_commit_hash",
                    "extractor_version",
                    "extraction_mode",
                    "requested_start_date",
                    "requested_end_date",
                    "extracted_at",
                ]
                ordered_cols = [c for c in preferred_order if c in final_df.columns]
                remaining_cols = [c for c in final_df.columns if c not in ordered_cols]
                final_df = final_df[ordered_cols + remaining_cols]

                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                parquet_filename = f"{dataset_name}_timeseries_{timestamp}.parquet"
                local_parquet_path = temp_data_dir / parquet_filename

                final_df.to_parquet(local_parquet_path, engine="pyarrow", index=False)

                blob_name = f"data/{dataset_name}/{parquet_filename}"
                out_blob = bucket.blob(blob_name)
                out_blob.upload_from_filename(str(local_parquet_path))

                print(f"  [SUCCESS] Parquet uploaded to gs://{BUCKET_NAME}/{blob_name}")
                print(f"  [INFO] Total rows uploaded: {len(final_df)}")

                local_parquet_path.unlink(missing_ok=True)
            else:
                print(f"  [WARNING] No valid data extracted for {dataset_name}. Skipping upload.")
                any_failures = True

        try:
            print("\n[PHASE 4] Uploading latest terminal extractor script to GCP...")
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

            latest_blob_name = f"scripts/{script_path.name}"
            archive_blob_name = f"scripts/archive/{script_path.stem}_{timestamp}{script_path.suffix}"

            latest_blob = bucket.blob(latest_blob_name)
            latest_blob.upload_from_filename(str(script_path))

            archive_blob = bucket.blob(archive_blob_name)
            archive_blob.upload_from_filename(str(script_path))

            print(f"  [SUCCESS] Latest script uploaded to gs://{BUCKET_NAME}/{latest_blob_name}")
            print(f"  [SUCCESS] Archive script uploaded to gs://{BUCKET_NAME}/{archive_blob_name}")

        except Exception as exc:
            print(f"  [WARNING] Failed to upload terminal extractor script: {exc}")

    except Exception as exc:
        any_failures = True
        print(f"[FATAL] Pipeline failed: {exc}")

    finally:
        print("\nCleaning up temporary files...")

        if temp_playbooks_dir.exists():
            for f in temp_playbooks_dir.glob("*"):
                if f.is_file():
                    f.unlink()
            try:
                temp_playbooks_dir.rmdir()
            except OSError:
                pass

        if temp_data_dir.exists():
            for f in temp_data_dir.glob("*"):
                if f.is_file():
                    f.unlink()
            try:
                temp_data_dir.rmdir()
            except OSError:
                pass

        if any_failures:
            print("\n*** EXTRACTION PIPELINE COMPLETE (WITH FAILURES) ***")
            sys.exit(1)

        print("\n*** EXTRACTION PIPELINE COMPLETE ***")


if __name__ == "__main__":
    run_incremental_extraction()