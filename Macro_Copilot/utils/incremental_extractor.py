import os
import sys
import yaml
import argparse
import hashlib
import json
import subprocess
import warnings
import pandas as pd
from datetime import datetime, timedelta, date
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
from google.cloud import storage
from xbbg import blp

# ============================================================================
# SELF-CONTAINED EXTRACTOR
#
# Same operational property as utils/historical_extractor.py: this script
# runs as a single file on the Bloomberg terminal host with NO project-
# internal imports (no `from ingestion ...`, no `from database ...`).
# The pure-Python helpers ``_HISTORY_TYPED_COLUMNS`` and
# ``_validate_no_overlaps`` are inlined below, byte-identical copies of
# the canonical implementation in
# ``Macro_Copilot/ingestion/metadata_history.py``. The DB
# ``EXCLUDE USING GIST`` constraint on
# ``macro_data.instrument_metadata_history`` (ADR 0001) is the ultimate
# enforcement; these inlined helpers exist for human-readable pre-write
# failure reporting on the Bloomberg host.
#
# If you modify these helpers, you MUST update all three locations:
#   1. ``ingestion/metadata_history.py``        (canonical, DB-side)
#   2. ``utils/historical_extractor.py``        (inlined, BBG-side)
#   3. ``utils/incremental_extractor.py``       (inlined, BBG-side — this file)
# The contract is documented in ADR 0002.
# ============================================================================

# --- CONFIGURATION ---
BUCKET_NAME = os.getenv("GCP_BUCKET_NAME", "macro-storage-bucket")
GCP_KEY_FILENAME = os.getenv("GCP_KEY_FILENAME", "library-extractor-key.json")
DEFAULT_VENDOR = os.getenv("DATA_VENDOR", "BLOOMBERG")
DEFAULT_LOOKBACK_DAYS = int(os.getenv("DEFAULT_LOOKBACK_DAYS", "30"))
MAX_HISTORICAL_FIELDS_PER_REQUEST = int(os.getenv("MAX_HISTORICAL_FIELDS_PER_REQUEST", "25"))
MAX_REFERENCE_FIELDS_PER_REQUEST = int(os.getenv("MAX_REFERENCE_FIELDS_PER_REQUEST", "50"))
# Maximum number of underlying-contract tickers per batched ``bdp()`` call in
# the metadata-history flow. SYNC INVARIANT with utils/historical_extractor.py.
MAX_REFERENCE_TICKERS_PER_REQUEST = int(os.getenv("MAX_REFERENCE_TICKERS_PER_REQUEST", "50"))


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

def _coerce_to_pandas(obj: Any) -> Any:
    """
    Return a pandas DataFrame/Series regardless of which dataframe library
    the installed xbbg used for this call.

    Older xbbg returns classic pandas objects. Newer xbbg returns a Narwhals
    DataFrame wrapping pyarrow/polars. A Narwhals frame and a pyarrow Table
    both expose ``.to_pandas()``; failing that ``narwhals.to_native()``
    unwraps to the underlying frame. When the input is ALREADY a pandas
    object (or ``None``) this returns it UNCHANGED and never imports
    narwhals — so on the classic-xbbg setup behaviour is byte-identical to
    before this helper existed. It only ever does work for the newer-xbbg
    output shape.
    """
    if obj is None or isinstance(obj, (pd.DataFrame, pd.Series)):
        return obj
    to_pandas = getattr(obj, "to_pandas", None)
    if callable(to_pandas):
        try:
            converted = to_pandas()
            if isinstance(converted, (pd.DataFrame, pd.Series)):
                return converted
        except Exception:
            pass
    try:
        import narwhals as nw
        native = nw.to_native(obj)
        if isinstance(native, (pd.DataFrame, pd.Series)):
            return native
        native_to_pandas = getattr(native, "to_pandas", None)
        if callable(native_to_pandas):
            converted = native_to_pandas()
            if isinstance(converted, (pd.DataFrame, pd.Series)):
                return converted
    except Exception:
        pass
    return obj


def _normalize_bdh_output(df: Any, fallback_ticker: str) -> pd.DataFrame:
    df = _coerce_to_pandas(df)
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return pd.DataFrame(columns=["trade_date", "ticker", "field_name", "field_value"])

    # Newer xbbg returns a LONG / tidy frame — one row per (date, ticker,
    # field) carrying explicit 'field' and 'value' columns. Map it straight
    # onto the [trade_date, ticker, field_name, field_value] contract. The
    # classic WIDE handling below is byte-identical to the pre-narwhals
    # implementation; this branch only fires for the newer long shape (a
    # classic wide frame's columns are Bloomberg field names, never the
    # literal pair 'field' + 'value').
    _cols = {str(c).lower(): c for c in df.columns}
    if (not isinstance(df.columns, pd.MultiIndex)
            and "field" in _cols and "value" in _cols):
        _date_key = (_cols.get("date") or _cols.get("trade_date")
                     or _cols.get("index"))
        if _date_key is None:
            for _c in df.columns:
                if pd.api.types.is_datetime64_any_dtype(df[_c]):
                    _date_key = _c
                    break
        if _date_key is not None:
            long_df = pd.DataFrame({
                "trade_date": list(df[_date_key]),
                "ticker": (list(df[_cols["ticker"]]) if "ticker" in _cols
                           else fallback_ticker),
                "field_name": list(df[_cols["field"]]),
                "field_value": list(df[_cols["value"]]),
            })
            long_df["trade_date"] = pd.to_datetime(
                long_df["trade_date"]).dt.strftime("%Y-%m-%d")
            long_df["field_name"] = long_df["field_name"].astype(str).str.upper()
            long_df = long_df.dropna(subset=["field_value"])
            return long_df

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

    Handles both xbbg output shapes:
    - classic WIDE: a DataFrame indexed by ticker with columns = Bloomberg
      fields;
    - newer LONG/tidy: one row per (ticker, field) with explicit 'field' and
      'value' columns (and optionally a 'ticker' column).
    """
    df = _coerce_to_pandas(df)
    if df is None:
        return {}

    if isinstance(df, pd.Series):
        return {str(k): _clean_scalar(v) for k, v in df.items()}

    if isinstance(df, pd.DataFrame):
        if df.empty:
            return {}

        _cols = {str(c).lower(): c for c in df.columns}
        # Newer xbbg LONG/tidy shape — one row per (ticker, field).
        if "field" in _cols and "value" in _cols:
            work = df
            if "ticker" in _cols:
                _tcol = _cols["ticker"]
                _tmatch = df[df[_tcol].astype(str).str.upper()
                             == str(fallback_ticker).upper()]
                if not _tmatch.empty:
                    work = _tmatch
                elif df[_tcol].nunique() > 1:
                    # No exact match AND the response carries more than one
                    # distinct ticker — refuse rather than risk attributing
                    # another ticker's fields to the requested one. A
                    # single-ticker response with no exact match is just
                    # Bloomberg canonicalising the requested ticker (e.g.
                    # /isin/... shown under its displayed name); that case is
                    # safe and falls through with work = df.
                    return {}
            return {
                str(f).upper(): _clean_scalar(v)
                for f, v in zip(work[_cols["field"]], work[_cols["value"]])
            }

        # Classic WIDE shape — UNCHANGED from the pre-narwhals implementation.
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


def _parse_selected_playbooks(raw_values: Optional[List[str]]) -> Optional[Set[str]]:
    """
    Parse CLI ``--playbook`` arguments into a set of playbook names to
    filter against. Accepts repeated flags and comma-separated values.
    Returns ``None`` when no filter is specified (extractor runs every
    playbook in GCS).
    """
    if not raw_values:
        return None

    selected: Set[str] = set()
    for raw_value in raw_values:
        if not raw_value:
            continue
        for token in str(raw_value).split(","):
            cleaned = token.strip()
            if cleaned:
                selected.add(cleaned)

    return selected or None


# ============================================================================
# METADATA-HISTORY MODE — helpers shared by run_metadata_history_extraction.
#
# Substrate for the SCD2 sibling table macro_data.instrument_metadata_history
# shipped in ADR 0001. Driven by the optional ``metadata_history:`` playbook
# section formalised in ADR 0002. None of these helpers touch Bloomberg or
# the network — they are pure-Python transforms / validators, deliberately
# isolated so the unit tests in
# tests/state/test_metadata_history_extraction.py can exercise them
# without a live BBG / GCS session.
#
# These are byte-identical copies of the same helpers in
# ``utils/historical_extractor.py``. The "incremental" aspect of this
# extractor is operational (run frequency / staleness recovery), not
# semantic — under ``--mode metadata-history`` both files produce
# identical parquets for the same playbook + Bloomberg state. The
# duplication is intentional per the SELF-CONTAINED EXTRACTOR rule at
# the top of this file.
# ============================================================================


_HISTORY_TYPED_COLUMNS: tuple = (
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


def _extract_underlying_tickers_from_chain(
    chain_df: Any,
    fallback_column: Optional[str] = None,
) -> List[str]:
    """
    Convert ``xbbg.blp.bds(...)`` output for a chain-enumeration field
    (typically ``FUT_CHAIN``) into a flat list of underlying-contract
    ticker strings.

    xbbg's column naming differs across Bloomberg vintages (``Security
    Description`` / ``security_description`` / ``Security_Description``).
    We probe canonical names in order, then fall back to the first
    string-valued column. ``fallback_column`` is the playbook's escape
    hatch — when an operator learns in A2 that a specific vintage uses
    a different column name, they pass it via
    ``metadata_history.chain_column_name``.
    """
    if chain_df is None:
        return []
    chain_df = _coerce_to_pandas(chain_df)
    if not isinstance(chain_df, pd.DataFrame):
        return []
    if chain_df.empty:
        return []

    candidates = [
        "Security Description",
        "security_description",
        "Security_Description",
        "SECURITY_DES",
        "security_des",
        "value",  # xbbg sometimes flattens bulk-data into a "value" column
    ]
    if fallback_column:
        candidates.insert(0, fallback_column)

    for col in candidates:
        if col in chain_df.columns:
            tickers = [
                str(v).strip()
                for v in chain_df[col].dropna().tolist()
                if str(v).strip()
            ]
            if tickers:
                return tickers

    for col in chain_df.columns:
        if chain_df[col].dtype == object:
            tickers = [
                str(v).strip()
                for v in chain_df[col].dropna().tolist()
                if str(v).strip()
            ]
            if tickers:
                return tickers
    return []


def _compute_effective_windows_expiry_roll(
    contracts: List[Dict[str, Any]],
    roll_field_column: str,
    first_trade_field_column: str,
) -> List[Dict[str, Any]]:
    """
    Apply the ``expiry_roll`` convention from ADR 0002 to a chain of
    underlying contracts:

      * C_1 (oldest): effective_from = C_1.first_trade_field,
                      effective_to   = C_1.roll_field
      * C_i (middle): effective_from = C_{i-1}.roll_field + 1 day,
                      effective_to   = C_i.roll_field
      * C_n (latest): effective_from = C_{n-1}.roll_field + 1 day,
                      effective_to   = NULL  (currently in effect)

    Each input contract dict carries the typed-column mapping for ONE
    underlying contract (output of ``bdp(contract, [field, …])``). The
    returned list contains one window-record per contract, augmented
    with ``effective_from`` and ``effective_to`` keys.

    Returns ``[]`` if no valid contracts remain after filtering rows
    missing either anchor field.
    """
    if not contracts:
        return []

    valid: List[Dict[str, Any]] = []
    for c in contracts:
        roll_val = c.get(roll_field_column)
        first_val = c.get(first_trade_field_column)
        if roll_val is None or first_val is None:
            continue
        try:
            roll_dt = pd.to_datetime(roll_val).date()
            first_dt = pd.to_datetime(first_val).date()
        except Exception:
            continue
        valid.append({**c, "_roll_dt": roll_dt, "_first_trade_dt": first_dt})

    if not valid:
        return []

    valid.sort(key=lambda r: r["_roll_dt"])

    windows: List[Dict[str, Any]] = []
    prev_roll: Optional[date] = None
    for i, c in enumerate(valid):
        is_last = i == len(valid) - 1
        if i == 0:
            effective_from = c["_first_trade_dt"]
        else:
            effective_from = prev_roll + timedelta(days=1)
        effective_to = None if is_last else c["_roll_dt"]
        window = {k: v for k, v in c.items() if not k.startswith("_")}
        window["effective_from"] = effective_from.isoformat()
        window["effective_to"] = effective_to.isoformat() if effective_to else None
        windows.append(window)
        prev_roll = c["_roll_dt"]
    return windows


def _validate_no_overlaps(
    rows_by_vendor_ticker: Dict[str, List[Dict[str, Any]]],
) -> List[str]:
    """
    Defence-in-depth overlap detector for the metadata-history flow.

    For each generic ticker, walks rows sorted by ``effective_from`` and
    reports conflicts where consecutive windows overlap or share a
    boundary day. Returns a list of human-readable conflict descriptions;
    an empty list means the input is clean.

    Sync invariant: byte-identical in behaviour to ``validate_no_overlaps``
    in ``ingestion/metadata_history.py`` and the same-named copy in
    ``utils/historical_extractor.py``.
    """
    conflicts: List[str] = []
    for vendor_ticker, rows in rows_by_vendor_ticker.items():
        if not rows:
            continue

        bad_missing_from = [r for r in rows if r.get("effective_from") in (None, "")]
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
            if curr_to_raw not in (None, ""):
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
                continue
            if nxt_from <= curr_to:
                conflicts.append(
                    f"{vendor_ticker}: row {i} (effective_to={curr_to}) "
                    f"overlaps row {i + 1} (effective_from={nxt_from})"
                )
    return conflicts


def _resolve_metadata_history_section(
    playbook: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """
    Return the playbook's ``metadata_history`` section if it is present
    and declares ``enabled: true``; otherwise return None.

    Sync invariant with ``utils/historical_extractor.py``.
    """
    section = playbook.get("metadata_history")
    if not isinstance(section, dict):
        return None
    if not section.get("enabled"):
        return None

    if not isinstance(section.get("static_fields"), list) or not section["static_fields"]:
        return None
    if not section.get("chain_field"):
        return None
    roll = section.get("roll_convention") or {}
    if not isinstance(roll, dict) or not roll.get("roll_field") or not roll.get("first_trade_field"):
        return None
    if roll.get("type", "expiry_roll") != "expiry_roll":
        raise ValueError(
            f"metadata_history.roll_convention.type={roll.get('type')!r} is not "
            "supported. The only value supported in PR A1 is 'expiry_roll'. "
            "See ADR 0002."
        )
    return section


def _fetch_chain_underlyings(
    generic_ticker: str,
    chain_field: str,
    chain_overrides: Dict[str, Any],
    fallback_column: Optional[str] = None,
) -> List[str]:
    """
    Thin wrapper around ``xbbg.blp.bds`` that returns the list of
    underlying contracts in a chain for a given generic ticker.
    """
    try:
        df = blp.bds(
            tickers=generic_ticker,
            flds=chain_field,
            **(chain_overrides or {}),
        )
    except Exception as exc:
        print(f"    [WARNING] bds() failed for {generic_ticker} {chain_field}: {exc}")
        return []
    return _extract_underlying_tickers_from_chain(df, fallback_column=fallback_column)


def _fetch_underlying_contract_static(
    underlying_ticker: str,
    bloomberg_fields: List[str],
    request_kwargs: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Per-underlying-contract bdp() helper. Returns ``{field_upper: value}``
    mapping with the same normalisation pass _fetch_reference_metadata
    applies (xbbg -> flat dict, dates ISO-stringified, NaN -> None).

    Retained as a fallback path used by
    :func:`_fetch_underlying_contracts_static_batch` when an entire
    batch call raises — the batch fetcher then retries each ticker in
    the failed chunk individually through this single-ticker helper, so
    a one-bad-contract problem (delisted ticker, terminal hiccup) does
    not lose data for the other 49 tickers in the chunk.
    """
    if not bloomberg_fields:
        return {}
    raw: Dict[str, Any] = {}
    for field_chunk in _chunked(bloomberg_fields, MAX_REFERENCE_FIELDS_PER_REQUEST):
        try:
            df = blp.bdp(
                tickers=underlying_ticker,
                flds=field_chunk,
                **request_kwargs,
            )
            normalized = _normalize_bdp_output(df, fallback_ticker=underlying_ticker)
            for k, v in normalized.items():
                raw[str(k).upper()] = v
        except Exception as exc:
            print(
                f"      [WARNING] bdp() failed for {underlying_ticker} "
                f"fields {field_chunk}: {exc}"
            )
    return raw


def _normalize_bdp_batch_output(
    df: Any,
    requested_tickers: List[str],
) -> Dict[str, Dict[str, Any]]:
    """
    Convert ``xbbg.blp.bdp(tickers=[T1, T2, ...], flds=[F1, F2, ...])``
    multi-ticker output into a ``{ticker: {field_upper: value}}`` map.

    SYNC INVARIANT with utils/historical_extractor.py — see the
    SELF-CONTAINED EXTRACTOR header at the top of this file.
    """
    out: Dict[str, Dict[str, Any]] = {}
    if df is None:
        return out
    df = _coerce_to_pandas(df)
    if not isinstance(df, pd.DataFrame):
        if isinstance(df, pd.Series) and requested_tickers:
            single = _normalize_bdp_output(df, fallback_ticker=requested_tickers[0])
            if single:
                out[requested_tickers[0]] = {
                    str(k).upper(): v for k, v in single.items()
                }
        return out
    if df.empty:
        return out

    # Newer xbbg LONG/tidy shape — one row per (ticker, field). A classic
    # wide batch frame is indexed by ticker with Bloomberg-field columns and
    # never carries the literal 'ticker'/'field'/'value' column triple, so
    # this branch fires only for the newer long shape.
    _cols = {str(c).lower(): c for c in df.columns}
    if "ticker" in _cols and "field" in _cols and "value" in _cols:
        for _, _r in df.iterrows():
            _tk = str(_r[_cols["ticker"]])
            _fld = str(_r[_cols["field"]]).upper()
            out.setdefault(_tk, {})[_fld] = _clean_scalar(_r[_cols["value"]])
        return out

    for ticker, row in df.iterrows():
        if isinstance(row, pd.DataFrame):
            row = row.iloc[0]
        ticker_str = str(ticker)
        normalised: Dict[str, Any] = {}
        for k, v in row.items():
            normalised[str(k).upper()] = _clean_scalar(v)
        out[ticker_str] = normalised
    return out


def _fetch_underlying_contracts_static_batch(
    underlying_tickers: List[str],
    bloomberg_fields: List[str],
    request_kwargs: Dict[str, Any],
    ticker_chunk_size: int = MAX_REFERENCE_TICKERS_PER_REQUEST,
) -> Dict[str, Dict[str, Any]]:
    """
    Batched-and-deduped variant of :func:`_fetch_underlying_contract_static`.

    Returns ``{ticker: {field_upper: value}}`` for every requested ticker
    that Bloomberg returned data for. Tickers Bloomberg couldn't resolve
    are absent from the output (caller skips them, matching the
    single-call contract).

    Performance properties: see the docstring in
    utils/historical_extractor.py — this is the SYNC-INVARIANT mirror.
    On fallback (chunk-level batch failure), falls back to per-ticker
    single calls so a one-bad-contract failure does not lose data for
    the other tickers in the chunk.
    """
    if not underlying_tickers or not bloomberg_fields:
        return {}

    seen: set = set()
    distinct_tickers: List[str] = []
    for t in underlying_tickers:
        if t and t not in seen:
            seen.add(t)
            distinct_tickers.append(t)

    out: Dict[str, Dict[str, Any]] = {}

    for ticker_chunk in _chunked(distinct_tickers, ticker_chunk_size):
        if not ticker_chunk:
            continue
        chunk_out: Dict[str, Dict[str, Any]] = {}
        chunk_failed = False
        for field_chunk in _chunked(bloomberg_fields, MAX_REFERENCE_FIELDS_PER_REQUEST):
            try:
                df = blp.bdp(
                    tickers=list(ticker_chunk),
                    flds=list(field_chunk),
                    **request_kwargs,
                )
            except Exception as exc:
                print(
                    f"      [WARNING] batched bdp() failed for "
                    f"{len(ticker_chunk)} ticker(s) on fields "
                    f"{field_chunk}: {exc}. Falling back to single-ticker "
                    "calls for this chunk."
                )
                chunk_failed = True
                break
            partial = _normalize_bdp_batch_output(df, requested_tickers=list(ticker_chunk))
            for ticker, fields in partial.items():
                chunk_out.setdefault(ticker, {}).update(fields)

        if chunk_failed:
            for ticker in ticker_chunk:
                fallback_raw = _fetch_underlying_contract_static(
                    underlying_ticker=ticker,
                    bloomberg_fields=bloomberg_fields,
                    request_kwargs=request_kwargs,
                )
                if fallback_raw:
                    out[ticker] = fallback_raw
        else:
            out.update(chunk_out)

    return out


def run_metadata_history_extraction(selected_playbooks: Optional[Set[str]] = None) -> None:
    """
    Mode-specific extraction path for the SCD2 rolling-contract metadata
    table (``macro_data.instrument_metadata_history``), invoked via
    ``--mode metadata-history``.

    Semantically identical to ``run_metadata_history_extraction`` in
    ``utils/historical_extractor.py``. The "incremental" distinction
    between the two extractor scripts applies only to the time-series
    flow (incremental_window_days slicing); chain enumeration always
    pulls the full chain (with ``INCLUDE_EXPIRED_CONTRACTS=Y`` if the
    playbook declares it). The ingester's parquet-hash dedup gate makes
    re-runs cheap when the chain hasn't changed.

    ADR: ``docs_revamped/05_decisions/0002-playbook-metadata-history-section.md``.
    """
    print("Initializing Library Extraction Agent (mode: metadata-history)...")

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

        if selected_playbooks:
            filtered_files: List[Path] = []
            for pb_path in playbook_files:
                if pb_path.name in selected_playbooks or pb_path.stem in selected_playbooks:
                    filtered_files.append(pb_path)
            playbook_files = filtered_files
            print(f"\n[INFO] Playbook filter active: {sorted(selected_playbooks)}")
            print(f"[INFO] Matched {len(playbook_files)} playbook file(s) after filtering.")

        if not playbook_files:
            print("[ABORT] No playbooks found in the GCP bucket. Exiting.")
            any_failures = True
            return

        print("\n[PHASE 2] Executing metadata-history extraction...")

        for pb_path in playbook_files:
            with open(pb_path, "r", encoding="utf-8") as f:
                playbook = yaml.safe_load(f) or {}

            section = _resolve_metadata_history_section(playbook)
            if section is None:
                print(
                    f"\n[SKIP] {pb_path.name}: no enabled metadata_history section. "
                    "(This is expected for non-rolling playbooks such as sovereign_bonds.)"
                )
                continue

            lineage_meta = _get_playbook_metadata(playbook, pb_path, script_path)
            asset_class = lineage_meta["asset_class"]
            dataset_name = lineage_meta["dataset_name"]
            universe_items = playbook.get("universe", []) or []
            universe_items = [
                item
                for item in universe_items
                if isinstance(item, dict)
                and item.get("ticker")
                and item.get("is_rolling_contract")
            ]
            reference_request_kwargs = _build_reference_request_kwargs(playbook)
            start_date, end_date = _resolve_date_window(playbook)

            chain_field = section["chain_field"]
            chain_overrides = section.get("chain_overrides") or {}
            chain_fallback_column = section.get("chain_column_name")
            roll_convention = section["roll_convention"]
            roll_field = roll_convention["roll_field"]
            first_trade_field = roll_convention["first_trade_field"]
            static_fields = section["static_fields"]

            bloomberg_field_list: List[str] = []
            seen_fields: Set[str] = set()
            for entry in static_fields:
                if not isinstance(entry, dict):
                    continue
                f = entry.get("bloomberg_field")
                if f and f not in seen_fields:
                    bloomberg_field_list.append(f)
                    seen_fields.add(f)
            for f in (roll_field, first_trade_field):
                if f not in seen_fields:
                    bloomberg_field_list.append(f)
                    seen_fields.add(f)

            field_to_column: Dict[str, str] = {}
            for entry in static_fields:
                if not isinstance(entry, dict):
                    continue
                col = entry.get("column_name")
                fld = entry.get("bloomberg_field")
                if col and fld:
                    field_to_column[fld.upper()] = col

            if not universe_items:
                print(
                    f"\n[WARNING] {pb_path.name}: no rolling-contract tickers in "
                    "universe. Skipping."
                )
                continue

            print(f"\nProcessing Playbook: {pb_path.name}")
            print(f"  Playbook name: {lineage_meta['playbook_name']}")
            print(f"  Playbook version: {lineage_meta['playbook_version']}")
            print(f"  Rolling tickers: {len(universe_items)}")
            print(f"  Chain field: {chain_field}")
            print(f"  Static fields: {len(static_fields)} -> {len(bloomberg_field_list)} BBG mnemonics")

            extracted_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            rows_by_vendor_ticker: Dict[str, List[Dict[str, Any]]] = {}
            tickers_with_data = 0

            # PASS 1 — enumerate the chain for every rolling generic.
            chain_by_generic: Dict[str, List[str]] = {}
            for item in universe_items:
                generic_ticker = item["ticker"]
                print(f"  Enumerating chain for {generic_ticker}...")
                underlying_tickers = _fetch_chain_underlyings(
                    generic_ticker=generic_ticker,
                    chain_field=chain_field,
                    chain_overrides=chain_overrides,
                    fallback_column=chain_fallback_column,
                )
                if not underlying_tickers:
                    print(f"    [!] No underlying contracts returned for {generic_ticker}")
                    chain_by_generic[generic_ticker] = []
                    continue
                chain_by_generic[generic_ticker] = underlying_tickers
                print(f"    [OK] chain length = {len(underlying_tickers)}")

            # PASS 2 — collect distinct underlyings and batch-fetch.
            # See utils/historical_extractor.py for the full rationale;
            # this is the SYNC-INVARIANT mirror.
            distinct_underlyings: List[str] = []
            seen_underlyings: set = set()
            for chain in chain_by_generic.values():
                for u in chain:
                    if u and u not in seen_underlyings:
                        seen_underlyings.add(u)
                        distinct_underlyings.append(u)

            if distinct_underlyings:
                total_chain_membership = sum(len(c) for c in chain_by_generic.values())
                projected_calls = (
                    len(distinct_underlyings) + MAX_REFERENCE_TICKERS_PER_REQUEST - 1
                ) // MAX_REFERENCE_TICKERS_PER_REQUEST
                print(
                    f"\n  Batch bdp() over {len(distinct_underlyings)} distinct underlying(s) "
                    f"(saved {total_chain_membership - len(distinct_underlyings)} duplicate(s) "
                    f"via chain-overlap dedup); ~{projected_calls} batched call(s) "
                    f"vs ~{total_chain_membership} per-ticker calls under the prior shape."
                )
                static_by_underlying = _fetch_underlying_contracts_static_batch(
                    underlying_tickers=distinct_underlyings,
                    bloomberg_fields=bloomberg_field_list,
                    request_kwargs=reference_request_kwargs,
                )
                print(
                    f"  [OK] batch bdp() returned data for "
                    f"{len(static_by_underlying)}/{len(distinct_underlyings)} underlyings."
                )
            else:
                static_by_underlying = {}

            # PASS 3 — per generic, look up cached static data and run
            # the expiry_roll window math. No Bloomberg calls in this
            # pass; all data is in memory already.
            for item in universe_items:
                generic_ticker = item["ticker"]
                underlying_tickers = chain_by_generic.get(generic_ticker, [])
                if not underlying_tickers:
                    continue

                contract_records: List[Dict[str, Any]] = []
                for underlying in underlying_tickers:
                    raw = static_by_underlying.get(underlying)
                    if not raw:
                        continue
                    record: Dict[str, Any] = {"_underlying_ticker": underlying}
                    for fld_upper, value in raw.items():
                        col = field_to_column.get(fld_upper)
                        if col:
                            record[col] = _clean_scalar(value)
                        record[fld_upper] = _clean_scalar(value)
                    contract_records.append(record)

                if not contract_records:
                    print(f"    [!] No bdp() data returned for any underlying of {generic_ticker}")
                    continue

                windows = _compute_effective_windows_expiry_roll(
                    contracts=contract_records,
                    roll_field_column=roll_field.upper(),
                    first_trade_field_column=first_trade_field.upper(),
                )
                if not windows:
                    print(
                        f"    [!] No valid effective windows computed for {generic_ticker} "
                        f"(missing {roll_field}/{first_trade_field} on every chain member)"
                    )
                    continue

                payload_rows: List[Dict[str, Any]] = []
                allowed_cols = set(field_to_column.values()) | {"effective_from", "effective_to"}
                for w in windows:
                    row = {
                        "vendor": item.get("vendor", playbook.get("vendor", DEFAULT_VENDOR)),
                        "vendor_ticker": generic_ticker,
                        "effective_from": w["effective_from"],
                        "effective_to": w.get("effective_to"),
                    }
                    for k, v in w.items():
                        if k in allowed_cols or k in {"effective_from", "effective_to"}:
                            row[k] = v
                    payload_rows.append(row)

                rows_by_vendor_ticker[generic_ticker] = payload_rows
                tickers_with_data += 1
                print(f"    [OK] {len(payload_rows)} effective window(s) for {generic_ticker}")

            expected_count = len(universe_items)
            if expected_count > 0:
                coverage = tickers_with_data / expected_count
                if coverage < 0.9:
                    print(
                        f"\n  [ABORT] Coverage gate: only {tickers_with_data}/{expected_count} "
                        f"rolling tickers produced metadata-history rows ({coverage:.0%}). "
                        "Refusing to upload partial data. Threshold is 90%."
                    )
                    any_failures = True
                    continue

            conflicts = _validate_no_overlaps(rows_by_vendor_ticker)
            if conflicts:
                print(
                    f"\n  [ABORT] Overlap-validation gate found {len(conflicts)} "
                    f"conflict(s); refusing to upload:"
                )
                for c in conflicts[:10]:
                    print(f"    - {c}")
                if len(conflicts) > 10:
                    print(f"    ... and {len(conflicts) - 10} more")
                any_failures = True
                continue

            all_rows: List[Dict[str, Any]] = []
            for rows in rows_by_vendor_ticker.values():
                all_rows.extend(rows)

            if not all_rows:
                print(f"  [WARNING] No rows assembled for {dataset_name}. Skipping upload.")
                continue

            final_df = pd.DataFrame(all_rows)
            for meta_key, meta_val in lineage_meta.items():
                final_df[meta_key] = meta_val
            final_df["requested_start_date"] = start_date
            final_df["requested_end_date"] = end_date
            final_df["extracted_at"] = extracted_at
            final_df["extraction_mode"] = "metadata_history"

            print(f"\n[PHASE 3] Compiling and Pushing Data for {dataset_name}...")
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            parquet_filename = f"{dataset_name}_metadata_history_{timestamp}.parquet"
            local_parquet_path = temp_data_dir / parquet_filename
            final_df.to_parquet(local_parquet_path, engine="pyarrow", index=False)

            blob_name = f"metadata_history/{dataset_name}/{parquet_filename}"
            out_blob = bucket.blob(blob_name)
            out_blob.upload_from_filename(str(local_parquet_path))

            print(f"  [SUCCESS] Parquet uploaded to gs://{BUCKET_NAME}/{blob_name}")
            print(f"  [INFO] Total rows uploaded: {len(final_df)}")
            local_parquet_path.unlink(missing_ok=True)

    except Exception as exc:
        any_failures = True
        print(f"[FATAL] metadata-history pipeline failed: {exc}")

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
            print("\n*** METADATA-HISTORY EXTRACTION COMPLETE (WITH FAILURES) ***")
            sys.exit(1)
        print("\n*** METADATA-HISTORY EXTRACTION COMPLETE ***")


# ============================================================================
# OTR RESOLUTION  (work order A4-4 resolver — ADR 0007)
#
# A declarative, opt-in capability: a playbook that carries an enabled
# ``otr_resolution:`` block gets, on every INCREMENTAL run, an extra step that
# snapshots ``bdp(<generic_ticker>, ID_ISIN + reference_fields)`` for each slot
# and emits a SEPARATE resolution artifact (long parquet, one row per slot,
# under gs://<bucket>/otr_resolution/<dataset>/). Local ingestion folds that
# artifact into ``macro_data.otr_history``.
#
# This lives in the INCREMENTAL extractor ONLY — never historical_extractor.py.
# ``bdp`` on a generic returns *today's* OTR; running it during a historical
# backfill would stamp today's mapping onto backfilled dates and corrupt
# otr_history. Resolution is intrinsically a "what is true now" operation.
#
# resolve_otr() never mutates the playbook — it writes data, the resolver's
# only output. Two false-roll-defence layers exist: (1) the per-slot sanity
# checks in _build_resolution_row below (ISIN-prefix + maturity plausibility),
# and (2) the ingester's distinct-date two-run confirmation gate. See ADR 0007.
# ============================================================================
OTR_RESOLUTION_EXTRACTION_MODE = "otr_resolution"


def _opt_str(value: Any) -> Optional[str]:
    """Stripped string, or None for empty / missing values."""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return text or None


def _parse_tenor_years(tenor: Any) -> Optional[float]:
    """Parse a tenor label ('10Y' / '2Y' / '30Y') to a float number of years.

    Returns None for anything not of that simple form — the maturity-
    plausibility band is then skipped rather than guessed.
    """
    text = _opt_str(tenor)
    if not text:
        return None
    text = text.upper().rstrip("Y").strip()
    try:
        return float(text)
    except ValueError:
        return None


def _resolve_otr_block(playbook: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return a normalised ``otr_resolution`` config, or None when the playbook
    declares no resolver capability (or declares it disabled / slotless)."""
    block = playbook.get("otr_resolution")
    if not isinstance(block, dict) or not block.get("enabled", False):
        return None
    slots = [
        s for s in (block.get("slots") or [])
        if isinstance(s, dict) and s.get("generic_ticker")
    ]
    if not slots:
        return None
    resolution_field = str(block.get("resolution_field") or "ID_ISIN").upper()
    reference_fields = [
        str(f).upper() for f in (block.get("reference_fields") or []) if f
    ]
    # confirmation_runs is the INGESTER's roll-confirmation gate; it is carried
    # through to the resolution parquet so the ingester (which never reads the
    # playbook) sees it. Default 2 — see ADR 0007.
    try:
        confirmation_runs = int(block.get("confirmation_runs", 2))
    except (TypeError, ValueError):
        confirmation_runs = 2
    return {
        "slots": slots,
        "resolution_field": resolution_field,
        "reference_fields": reference_fields,
        "confirmation_runs": max(1, confirmation_runs),
    }


def _build_resolution_row(
    slot: Dict[str, Any],
    resolution_field: str,
    bdp_values: Dict[str, Any],
    resolution_date: str,
    resolution_timestamp: str,
) -> Dict[str, Any]:
    """Pure: turn one slot's ``bdp`` result into a resolution-parquet row.

    ``status`` is one of:
      * ``ok``       — a plausible OTR ISIN was resolved;
      * ``failed``   — ``bdp`` returned no value for ``resolution_field``;
      * ``rejected`` — a value was returned but failed a sanity check: an
        ISIN-prefix mismatch, an already-matured bond, or a remaining
        maturity implausible for the slot tenor (broad band). A rejected /
        failed row is carried in the artifact for the audit trail but the
        ingester applies no ``otr_history`` change for it.
    """
    row: Dict[str, Any] = {
        "slot_id": slot.get("slot_id"),
        "generic_ticker": slot.get("generic_ticker"),
        "country": slot.get("country"),
        "currency": slot.get("currency"),
        "curve_family": slot.get("curve_family"),
        "tenor": slot.get("tenor"),
        "resolved_isin": None,
        "resolved_cusip": None,
        "security_name": None,
        "coupon": None,
        "maturity_date": None,
        "issue_date": None,
        "instrument_ticker": None,
        "resolution_date": resolution_date,
        "resolution_timestamp": resolution_timestamp,
        "status": "ok",
        "error": None,
    }

    resolved_isin = _opt_str(_clean_scalar(bdp_values.get(str(resolution_field).upper())))
    if not resolved_isin:
        row["status"] = "failed"
        row["error"] = (
            f"bdp({slot.get('generic_ticker')}) returned no {resolution_field}"
        )
        return row

    row["resolved_isin"] = resolved_isin
    row["instrument_ticker"] = f"/isin/{resolved_isin}"
    row["resolved_cusip"] = _opt_str(_clean_scalar(bdp_values.get("ID_CUSIP")))
    row["security_name"] = _opt_str(_clean_scalar(bdp_values.get("SECURITY_DES")))
    row["coupon"] = _clean_scalar(bdp_values.get("CPN"))
    row["maturity_date"] = _opt_str(_clean_scalar(bdp_values.get("MATURITY")))
    row["issue_date"] = _opt_str(_clean_scalar(bdp_values.get("ISSUE_DT")))
    if not row["currency"]:
        row["currency"] = _opt_str(_clean_scalar(bdp_values.get("CRNCY")))

    # --- false-roll defence, layer 1: per-slot sanity checks ----------------
    prefix = _opt_str(slot.get("expected_isin_prefix"))
    if prefix and not resolved_isin.upper().startswith(prefix.upper()):
        row["status"] = "rejected"
        row["error"] = (
            f"resolved ISIN {resolved_isin} does not start with the slot's "
            f"expected prefix '{prefix}'"
        )
        return row

    if row["maturity_date"]:
        try:
            mat_iso = pd.to_datetime(row["maturity_date"]).strftime("%Y-%m-%d")
            row["maturity_date"] = mat_iso
        except Exception:
            # Unparseable maturity is not fatal — the bond is identified by
            # ISIN; leave the raw value and let the ingester normalise.
            mat_iso = None

        if mat_iso is not None:
            # The bond must still be alive.
            if mat_iso <= resolution_date:
                row["status"] = "rejected"
                row["error"] = (
                    f"resolved bond {resolved_isin} matures {mat_iso} on/before "
                    f"resolution date {resolution_date} — not a live OTR bond"
                )
                return row

            # maturity-plausibility band: the resolver only ever resolves the
            # CURRENT on-the-run bond, whose remaining life is ~the slot tenor.
            # A broad band [tenor*0.5, tenor+2.0] catches a gross mis-resolution
            # (e.g. a 2Y slot resolving to a 10Y bond) while leaving normal
            # auction-cycle variation comfortably inside.
            tenor_years = _parse_tenor_years(slot.get("tenor"))
            if tenor_years:
                try:
                    remaining = (
                        pd.to_datetime(mat_iso) - pd.to_datetime(resolution_date)
                    ).days / 365.25
                    lower, upper = tenor_years * 0.5, tenor_years + 2.0
                    if not (lower <= remaining <= upper):
                        row["status"] = "rejected"
                        row["error"] = (
                            f"resolved bond {resolved_isin} has ~{remaining:.1f}y "
                            f"to maturity — implausible for a {slot.get('tenor')} "
                            f"slot (expected {lower:.1f}-{upper:.1f}y)"
                        )
                        return row
                except Exception:
                    pass

    return row


def resolve_otr(
    playbook: Dict[str, Any],
    lineage_meta: Dict[str, Any],
    bucket: Any,
    temp_data_dir: Path,
    reference_request_kwargs: Dict[str, Any],
) -> bool:
    """Run the OTR resolver for a playbook and upload its resolution artifact.

    Returns True on success (resolved + uploaded) OR clean skip (no resolver
    block declared). Returns False if the resolver ran but produced nothing
    usable — the caller folds that into ``any_failures``.
    """
    block = _resolve_otr_block(playbook)
    if block is None:
        return True  # no resolver capability declared — nothing to do.

    slots = block["slots"]
    resolution_field = block["resolution_field"]
    # Request the resolution field plus every reference field, de-duplicated.
    requested_fields: List[str] = [resolution_field] + [
        f for f in block["reference_fields"] if f != resolution_field
    ]

    resolution_date = datetime.now().strftime("%Y-%m-%d")
    resolution_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    print(f"\n[OTR RESOLVER] Resolving {len(slots)} on-the-run slot(s)...")
    rows: List[Dict[str, Any]] = []
    for slot in slots:
        generic = slot.get("generic_ticker")
        try:
            bdp_values: Dict[str, Any] = {}
            for field_chunk in _chunked(requested_fields, MAX_REFERENCE_FIELDS_PER_REQUEST):
                df = blp.bdp(
                    tickers=generic,
                    flds=field_chunk,
                    **reference_request_kwargs,
                )
                normalized = _normalize_bdp_output(df, fallback_ticker=generic)
                for field_name, value in normalized.items():
                    bdp_values[str(field_name).upper()] = value
            row = _build_resolution_row(
                slot, resolution_field, bdp_values, resolution_date, resolution_timestamp
            )
        except Exception as exc:
            row = _build_resolution_row(
                slot, resolution_field, {}, resolution_date, resolution_timestamp
            )
            row["status"] = "failed"
            row["error"] = f"bdp() raised for {generic}: {exc}"

        if row["status"] == "ok":
            print(f"  [OK] {slot.get('slot_id')}: {generic} -> {row['resolved_isin']}")
        else:
            print(
                f"  [{row['status'].upper()}] {slot.get('slot_id')}: {generic} "
                f"-> {row['error']}"
            )
        rows.append(row)

    ok_count = sum(1 for r in rows if r["status"] == "ok")
    if ok_count == 0:
        print(
            "  [WARNING] OTR resolver resolved 0 slots — not uploading a "
            "resolution artifact."
        )
        return False

    df = pd.DataFrame(rows)
    base_dataset = lineage_meta["dataset_name"]
    base_playbook = lineage_meta["playbook_name"]
    resolution_dataset = f"{base_dataset}_otr_resolution"

    # Lineage stamps. playbook_name is SUFFIXED so the ingester's
    # playbook-keyed dedup / audit scope for resolution artifacts is isolated
    # from the market-data load history of the same playbook (ADR 0007).
    df["asset_class"] = lineage_meta["asset_class"]
    df["dataset_name"] = resolution_dataset
    df["playbook_name"] = f"{base_playbook}__otr_resolution"
    df["playbook_version"] = lineage_meta["playbook_version"]
    df["playbook_hash"] = lineage_meta["playbook_hash"]
    df["git_commit_hash"] = lineage_meta["git_commit_hash"]
    df["extractor_version"] = lineage_meta["extractor_version"]
    df["extraction_mode"] = OTR_RESOLUTION_EXTRACTION_MODE
    df["confirmation_runs"] = block["confirmation_runs"]
    df["requested_start_date"] = resolution_date
    df["requested_end_date"] = resolution_date
    df["extracted_at"] = resolution_timestamp

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    parquet_filename = f"{resolution_dataset}_{timestamp}.parquet"
    local_parquet_path = temp_data_dir / parquet_filename
    df.to_parquet(local_parquet_path, engine="pyarrow", index=False)

    blob_name = f"otr_resolution/{resolution_dataset}/{parquet_filename}"
    bucket.blob(blob_name).upload_from_filename(str(local_parquet_path))
    local_parquet_path.unlink(missing_ok=True)

    print(
        f"  [SUCCESS] OTR resolution artifact uploaded to gs://{BUCKET_NAME}/"
        f"{blob_name}  ({ok_count}/{len(rows)} slot(s) resolved)"
    )
    return True


# ============================================================================
# WIRP TIME-SERIES EXTRACTION  (work order B2, D-wirp, ADR 0009)
#
# A WIRP playbook declares a ``wirp:`` section: a time-series playbook whose
# four per-meeting metrics (FR/PR/NM/CH) are TICKER-borne — each metric is a
# different Bloomberg ticker, not a different field on one ticker — so it
# carries no ``target_metrics`` and is extracted by this dedicated branch into
# the standard ``data/`` → ``market_data_daily`` route (ADR 0009 §1, §4).
# Synthetic-instrument model: one instrument per central-bank meeting, the four
# metrics its four ``field`` values.
# ============================================================================


def _wirp_real_ticker(item: Dict[str, Any], code: str) -> Optional[str]:
    """The real Bloomberg ticker for one WIRP metric of one meeting — the
    rendered ``wirp_ticker_<code>`` key, else reconstructed from the region
    prefix + meeting token (the verified ``{prefix}{code} {token} Index``
    grammar)."""
    explicit = item.get(f"wirp_ticker_{code.lower()}")
    if explicit:
        return str(explicit)
    prefix = item.get("wirp_region_prefix")
    token = item.get("wirp_meeting_token")
    if prefix and token:
        return f"{prefix}{code} {token} Index"
    return None


def _extract_one_wirp_meeting(
    item: Dict[str, Any],
    metrics: List[Dict[str, Any]],
    bloomberg_field: str,
    start_date: str,
    end_date: str,
    bdh_kwargs: Dict[str, Any],
) -> Optional[pd.DataFrame]:
    """Pull every WIRP metric series for ONE meeting and fold them onto the
    synthetic per-meeting instrument.

    Returns a long-format frame (``trade_date, ticker, field_name,
    field_value``) where ``ticker`` is the meeting's synthetic vendor_ticker
    and ``field_name`` is the WIRP metric's ``field`` — or ``None`` if ANY
    required metric returns no data. STRICT 4/4 (ADR 0009 §4): a meeting
    missing any metric is dropped WHOLE, never partially emitted.
    """
    meeting_ticker = item["ticker"]
    frames: List[pd.DataFrame] = []
    for metric in metrics:
        code = str(metric["code"])
        field = str(metric["field"])
        real_ticker = _wirp_real_ticker(item, code)
        if not real_ticker:
            print(
                f"    [!] {meeting_ticker}: cannot build the {code} ticker "
                "(missing wirp_ticker_* / wirp_region_prefix / wirp_meeting_token)"
            )
            return None
        raw = blp.bdh(
            tickers=real_ticker,
            flds=[bloomberg_field],
            start_date=start_date,
            end_date=end_date,
            **bdh_kwargs,
        )
        normalized = _normalize_bdh_output(raw, fallback_ticker=real_ticker)
        if normalized.empty:
            print(
                f"    [!] {meeting_ticker}: metric {code} ({real_ticker}) "
                "returned no data — meeting dropped (strict 4/4 coverage)"
            )
            return None
        # Fold onto the synthetic per-meeting instrument: the row's ``ticker``
        # is the meeting, the ``field_name`` is the WIRP metric (ADR 0009 §1).
        normalized["ticker"] = meeting_ticker
        normalized["field_name"] = field
        frames.append(normalized)

    long_df = pd.concat(frames, ignore_index=True).drop_duplicates(
        subset=["trade_date", "ticker", "field_name"], keep="last"
    )
    return long_df if not long_df.empty else None


def _extract_wirp_playbook(
    playbook: Dict[str, Any],
    lineage_meta: Dict[str, Any],
    bucket: Any,
    temp_data_dir: Path,
) -> bool:
    """Extract a WIRP playbook (ADR 0009 §4) — the ``wirp:``-section-gated
    branch of the incremental time-series flow.

    Builds the STANDARD time-series parquet (``trade_date, ticker, field_name,
    field_value`` + lineage/identity columns) and uploads it to
    ``gs://<bucket>/data/<dataset>/`` — PHASE 2 ingestion is then byte-identical
    to any other time-series playbook.

    Coverage (ADR 0009 §4): each meeting is extracted only if ALL its required
    metrics return data (strict 4/4 — a partial meeting is dropped whole by
    :func:`_extract_one_wirp_meeting`); then the playbook gate requires ALL
    meetings — ``extracted_count == expected_count`` — because the WIRP
    universe is the horizon-bounded verified-coverage band, not a broad
    discovery set (a missing meeting is always an error, never a legitimate
    absence). Returns True on success, False on any failure — and uploads
    NOTHING on failure, so a partial WIRP artifact can never ingest as a clean
    SUCCESS.
    """
    dataset_name = lineage_meta["dataset_name"]
    asset_class = lineage_meta["asset_class"]
    wirp_cfg = playbook.get("wirp") or {}
    bloomberg_field = str(wirp_cfg.get("bloomberg_field") or "PX_LAST")
    metrics = [
        m
        for m in (wirp_cfg.get("metrics") or [])
        if isinstance(m, dict)
        and m.get("code")
        and m.get("field")
        and m.get("available", True)
    ]
    universe_items = [
        it
        for it in (playbook.get("universe") or [])
        if isinstance(it, dict) and "ticker" in it
    ]
    bdh_kwargs = _build_historical_request_kwargs(playbook)
    start_date, end_date = _resolve_date_window(playbook)

    print(f"\nProcessing WIRP Playbook: {lineage_meta['playbook_name']}")
    print(f"  Playbook version: {lineage_meta['playbook_version']}")
    print(f"  Dataset name: {dataset_name}")
    print(f"  Meetings (universe): {len(universe_items)}")
    print(f"  Metrics: {[m['code'] for m in metrics]} -> {[m['field'] for m in metrics]}")
    print(f"  Date range: {start_date} -> {end_date}")

    if not metrics:
        print(f"  [WARNING] WIRP playbook {dataset_name} declares no usable metrics. Skipping.")
        return False
    if not universe_items:
        print(
            f"  [WARNING] WIRP playbook {dataset_name} has an empty universe — "
            "render it with utils/render_wirp_universe.py first. Skipping."
        )
        return False

    default_item_meta = {
        "vendor": playbook.get("vendor", DEFAULT_VENDOR),
        "asset_class": asset_class,
        "instrument_type": playbook.get("instrument_type") or playbook.get("default_instrument_type"),
        "curve_family": playbook.get("curve_family"),
        "is_active": playbook.get("is_active", True),
    }
    extracted_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    all_data_frames: List[pd.DataFrame] = []

    for item in universe_items:
        meeting_ticker = item["ticker"]
        asset_metadata = {
            k: v for k, v in {**default_item_meta, **item}.items() if k != "ticker"
        }
        asset_metadata.setdefault("vendor", DEFAULT_VENDOR)
        asset_metadata.setdefault("asset_class", asset_class)
        try:
            long_df = _extract_one_wirp_meeting(
                item, metrics, bloomberg_field, start_date, end_date, bdh_kwargs
            )
            if long_df is None:
                continue  # strict 4/4 — meeting dropped, counts against the gate

            for meta_key, meta_val in asset_metadata.items():
                long_df[meta_key] = _clean_scalar(meta_val)
            for meta_key, meta_val in lineage_meta.items():
                long_df[meta_key] = meta_val
            long_df["requested_start_date"] = start_date
            long_df["requested_end_date"] = end_date
            long_df["extracted_at"] = extracted_at
            long_df["extraction_mode"] = "incremental"

            all_data_frames.append(long_df)
            print(f"    [OK] {meeting_ticker}: {len(long_df)} row(s), {len(metrics)} metric(s)")
        except Exception as exc:
            print(f"    [ERROR] WIRP extraction failed on {meeting_ticker}: {exc}")

    if not all_data_frames:
        print(f"  [WARNING] No WIRP data extracted for {dataset_name}. Nothing uploaded.")
        return False

    # Coverage gate — WIRP requires ALL meetings (ADR 0009 §4). The WIRP
    # universe is NOT a broad discovery set: the horizon (§3) bounds it to
    # EXACTLY the meetings the Stage-B probe verified have full 4/4 WIRP data.
    # Within that curated universe a meeting failing extraction is never a
    # legitimate absence — it is a transient Bloomberg error, or horizon drift
    # (an empty meeting that entered range and must trigger a re-probe). Either
    # way it must abort loudly and upload nothing, never ingest as a degraded
    # SUCCESS. This all-or-nothing rule matches the event extractor
    # (ADR 0008 §4), whose event list has the same verified-curated property;
    # the vanilla 90% gate is for broad-discovery time-series universes — which
    # WIRP, by construction, is not.
    extracted_count = len(all_data_frames)
    expected_count = len(universe_items)
    if extracted_count != expected_count:
        print(
            f"\n  [ABORT] WIRP coverage gate: only {extracted_count}/{expected_count} "
            f"meeting(s) extracted full 4/4. WIRP requires ALL meetings in the "
            f"verified-coverage universe — refusing to upload partial data for "
            f"{dataset_name}. Re-run; if a meeting is genuinely empty, re-run the "
            f"Stage-B probe (scripts/wirp_coverage_check.py) and re-set the "
            f"wirp.yml horizon."
        )
        return False

    print(
        f"\n[PHASE 3] Compiling and pushing WIRP data for {dataset_name} "
        f"({extracted_count}/{expected_count} meetings)..."
    )
    final_df = pd.concat(all_data_frames, ignore_index=True)
    final_df = final_df.dropna(subset=["field_value"])
    if final_df.empty:
        print(f"  [WARNING] WIRP final dataframe for {dataset_name} is empty after cleaning.")
        return False

    lead_cols = ["trade_date", "ticker", "field_name", "field_value"]
    ordered = [c for c in lead_cols if c in final_df.columns]
    final_df = final_df[ordered + [c for c in final_df.columns if c not in ordered]]

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    parquet_filename = f"{dataset_name}_timeseries_{timestamp}.parquet"
    local_parquet_path = temp_data_dir / parquet_filename
    final_df.to_parquet(local_parquet_path, engine="pyarrow", index=False)

    blob_name = f"data/{dataset_name}/{parquet_filename}"
    bucket.blob(blob_name).upload_from_filename(str(local_parquet_path))
    local_parquet_path.unlink(missing_ok=True)

    print(f"  [SUCCESS] WIRP parquet uploaded to gs://{BUCKET_NAME}/{blob_name}")
    print(f"  [INFO] Total rows uploaded: {len(final_df)}")
    return True


def run_incremental_extraction(selected_playbooks: Optional[Set[str]] = None):
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

        if selected_playbooks:
            filtered_files: List[Path] = []
            for pb_path in playbook_files:
                if pb_path.name in selected_playbooks or pb_path.stem in selected_playbooks:
                    filtered_files.append(pb_path)
            playbook_files = filtered_files
            print(f"\n[INFO] Playbook filter active: {sorted(selected_playbooks)}")
            print(f"[INFO] Matched {len(playbook_files)} playbook file(s) after filtering.")

        if not playbook_files:
            print("[ABORT] No playbooks found in the GCP bucket. Exiting.")
            any_failures = True
            return

        print("\n[PHASE 2] Executing Bloomberg Extraction...")

        for pb_path in playbook_files:
            with open(pb_path, "r", encoding="utf-8") as f:
                playbook = yaml.safe_load(f) or {}

            # Event playbooks (ADR 0008) declare an ``event_calendar:`` section
            # and carry no time-series ``universe`` — they are handled by
            # ``--mode event-calendar``, not this flow. Skip them cleanly so
            # they are not mis-reported as failures.
            if playbook.get("event_calendar") is not None:
                print(
                    f"\n[SKIP] {pb_path.name}: event_calendar playbook — handled "
                    "by --mode event-calendar, not the time-series flow."
                )
                continue

            # WIRP playbooks (ADR 0009) declare a ``wirp:`` section — a
            # time-series playbook whose four per-meeting metrics are
            # ticker-borne, so it carries no ``target_metrics`` and is
            # extracted by the dedicated WIRP branch into the standard data/ ->
            # market_data_daily route. WIRP declares no otr_resolution; dispatch
            # here, before the resolver step, and skip the vanilla flow.
            if playbook.get("wirp") is not None:
                wirp_lineage = _get_playbook_metadata(playbook, pb_path, script_path)
                if not _extract_wirp_playbook(
                    playbook=playbook,
                    lineage_meta=wirp_lineage,
                    bucket=bucket,
                    temp_data_dir=temp_data_dir,
                ):
                    any_failures = True
                continue

            lineage_meta = _get_playbook_metadata(playbook, pb_path, script_path)
            asset_class = lineage_meta["asset_class"]
            dataset_name = lineage_meta["dataset_name"]
            universe_items = playbook.get("universe", [])
            target_metrics = playbook.get("target_metrics", [])
            reference_metrics = playbook.get("reference_metrics", [])
            historical_request_kwargs = _build_historical_request_kwargs(playbook)
            reference_request_kwargs = _build_reference_request_kwargs(playbook)

            # --- OTR RESOLUTION (ADR 0007) — declarative, opt-in per playbook.
            # Runs BEFORE the market-data extraction so the market-data flow's
            # coverage-gate `continue` statements can never skip it; the two
            # steps are independent and write separate artifacts.
            try:
                if not resolve_otr(
                    playbook=playbook,
                    lineage_meta=lineage_meta,
                    bucket=bucket,
                    temp_data_dir=temp_data_dir,
                    reference_request_kwargs=reference_request_kwargs,
                ):
                    any_failures = True
            except Exception as exc:
                print(f"  [ERROR] OTR resolution step failed for {pb_path.name}: {exc}")
                any_failures = True

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


# ============================================================================
# EVENT-CALENDAR EXTRACTION  (work order B2, ADR 0008)
#
# ``--mode event-calendar`` extracts macro EVENTS — economic releases and
# central-bank meetings — into a wide parquet whose columns are
# macro_data.event_calendar's columns, uploaded to gs://<bucket>/events/. Local
# ingestion (``_process_event_blob``, PHASE 5) folds it into event_calendar.
#
# Incremental-only (ADR 0008 §4): the ECO_RELEASE_DT_LIST bds surface is a
# recent-plus-forward window — there is no deep history to backfill — so event
# extraction is intrinsically forward, like the OTR resolver. The historical
# extractor never runs this mode.
#
# WIRP is NOT handled here: per ADR 0004 it is daily time-series data and rides
# the ordinary ``--mode time-series`` path into market_data_daily.
# ============================================================================
EVENT_CALENDAR_EXTRACTION_MODE = "event_calendar"
EVENT_FAMILIES = ("economic_release", "central_bank_meeting")
DEFAULT_EVENT_LOOKBACK_DAYS = 800

# Every macro_data.event_calendar value column an event row may carry. load_id
# is assigned DB-side; lineage columns are stamped onto the parquet separately.
_EVENT_ROW_COLUMNS = (
    "event_type", "event_category", "country", "currency", "central_bank",
    "release_date", "release_time", "period",
    "actual", "consensus_median", "consensus_high", "consensus_low",
    "prior", "revised_prior", "surprise", "surprise_std_dev",
    "high_yield", "bid_to_cover", "tail_bps", "indirect_pct",
    "related_instrument_id", "attributes",
)


def _blank_event_row() -> Dict[str, Any]:
    """A fully-NULL event row — every event_calendar value column present, so a
    DataFrame built from a mix of economic-release and meeting rows has one
    coherent column set."""
    return {col: None for col in _EVENT_ROW_COLUMNS}


def _resolve_event_calendar_section(playbook: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return a normalised ``event_calendar`` config.

    Returns None ONLY when the playbook carries no ``event_calendar`` key at
    all — a genuine non-event playbook. A section that IS present but malformed
    (non-mapping, unknown ``family``, missing ``event_category``, no ``events``)
    raises ``ValueError``: a malformed event playbook is a config bug and must
    fail loudly, never be silently treated as a non-event playbook (P6,
    ADR 0008 §4).
    """
    block = playbook.get("event_calendar")
    if block is None:
        return None
    if not isinstance(block, dict):
        raise ValueError("event_calendar section must be a mapping.")
    family = str(block.get("family") or "").strip().lower()
    if family not in EVENT_FAMILIES:
        raise ValueError(
            f"event_calendar.family must be one of {EVENT_FAMILIES}; "
            f"got {block.get('family')!r}."
        )
    event_category = str(block.get("event_category") or "").strip().lower()
    if not event_category:
        raise ValueError("event_calendar.event_category is required.")
    events = [e for e in (block.get("events") or []) if isinstance(e, dict)]
    if not events:
        raise ValueError("event_calendar.events is empty or malformed.")
    cfg: Dict[str, Any] = {
        "family": family,
        "event_category": event_category,
        "events": events,
    }
    if family == "economic_release":
        cfg["actual_field"] = str(block.get("actual_field") or "PX_LAST").upper()
        cfg["release_date_field"] = str(
            block.get("release_date_field") or "ECO_RELEASE_DT_LIST"
        ).upper()
        survey = block.get("survey_fields") or {}
        cfg["survey_fields"] = {
            str(col): str(bbg).upper()
            for col, bbg in survey.items()
            if col and bbg
        }
    else:  # central_bank_meeting
        cfg["meeting_calendar_field"] = str(
            block.get("meeting_calendar_field") or "ECO_RELEASE_DT_LIST"
        ).upper()
        cfg["rate_field"] = str(block.get("rate_field") or "PX_LAST").upper()
    return cfg


def _parse_bds_dates(bds_df: Any) -> List[date]:
    """Pull the date column out of a bds result (e.g. ECO_RELEASE_DT_LIST) —
    the column that yields the most parseable dates. Narwhals-agnostic."""
    df = _coerce_to_pandas(bds_df)
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return []
    best: List[date] = []
    # Every column is probed as dates to discover which one holds them — the
    # non-date columns (ticker / field) coerce to NaT. pandas' format-inference
    # UserWarning on that broad probe is expected and silenced; the parse
    # itself (errors="coerce") is unaffected.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=UserWarning)
        for col in df.columns:
            parsed = pd.to_datetime(df[col], errors="coerce")
            good = [d.date() for d in parsed if pd.notna(d)]
            if len(good) > len(best):
                best = good
    return sorted(set(best))


def _bdh_to_period_records(
    bdh_long: pd.DataFrame, actual_field: str, survey_fields: Dict[str, str]
) -> List[Dict[str, Any]]:
    """Pivot a long ``_normalize_bdh_output`` frame into per-period records:
    one dict per reference period carrying ``period_date``, ``actual``, and
    each declared survey column (keyed by its event_calendar column name)."""
    if bdh_long is None or bdh_long.empty:
        return []
    by_date: Dict[str, Dict[str, Any]] = {}
    for _, r in bdh_long.iterrows():
        td = str(r["trade_date"])
        by_date.setdefault(td, {})[str(r["field_name"]).upper()] = r["field_value"]
    out: List[Dict[str, Any]] = []
    for td in sorted(by_date):
        fields = by_date[td]
        rec: Dict[str, Any] = {
            "period_date": td,
            "actual": fields.get(actual_field.upper()),
        }
        for col, bbg in survey_fields.items():
            rec[col] = fields.get(str(bbg).upper())
        out.append(rec)
    return out


def _join_econ_releases(
    period_records: List[Dict[str, Any]],
    release_dates: List[date],
    run_date: date,
) -> Tuple[List[Tuple[date, Dict[str, Any], Any]], List[date], List[str]]:
    """ADR 0008 §2 economic-release join — explicit and run-date-aware.

    Returns ``(realized_pairs, scheduled_dates, warnings)``. ``realized_pairs``
    is ``(release_date, period_record, prior_actual)``; ``scheduled_dates`` are
    future release dates plus any realized release whose actual is not yet
    posted (demoted to a placeholder).

    Each bdh observation is paired with the realized release that published it:
    iterating periods newest-first, a period claims the smallest still-unused
    realized release strictly after the period's date. This is lag-agnostic and
    naturally demotes a just-happened release whose actual has not landed in the
    bdh series yet (no period claims it → it falls through to ``scheduled``).
    """
    periods = sorted(period_records, key=lambda r: str(r["period_date"]))
    period_dates: List[Optional[date]] = []
    for r in periods:
        try:
            period_dates.append(date.fromisoformat(str(r["period_date"])[:10]))
        except (ValueError, TypeError):
            period_dates.append(None)

    realized = sorted(d for d in release_dates if d <= run_date)
    scheduled_future = [d for d in release_dates if d > run_date]
    warnings: List[str] = []

    used: Set[date] = set()
    pairs: List[Tuple[date, Dict[str, Any], Any]] = []
    for i in range(len(periods) - 1, -1, -1):  # newest period first
        pd_i = period_dates[i]
        if pd_i is None:
            continue
        candidates = sorted(R for R in realized if R > pd_i and R not in used)
        if not candidates:
            continue
        R = candidates[0]
        used.add(R)
        prior_actual = periods[i - 1]["actual"] if i > 0 else None
        pairs.append((R, periods[i], prior_actual))

    pairs.sort(key=lambda t: t[0])

    # Split the UNMATCHED realized release dates. A release NEWER than every
    # matched release is a just-happened event whose actual is not posted in
    # the bdh series yet — keep it as a scheduled placeholder (it fills on a
    # later run). A release that is OLDER (its period falls outside the bdh
    # lookback window, or a rare collision) is STALE — skip it with a warning,
    # NEVER fabricate it as a scheduled (future) row (ADR 0008 §2).
    newest_matched = max(used) if used else None
    pending: List[date] = []
    stale: List[date] = []
    for R in realized:
        if R in used:
            continue
        if newest_matched is not None and R > newest_matched:
            pending.append(R)
        else:
            stale.append(R)
    scheduled = sorted(set(scheduled_future) | set(pending))
    if stale:
        shown = ", ".join(d.isoformat() for d in sorted(stale)[:5])
        warnings.append(
            f"{len(stale)} realized release date(s) had no in-window bdh "
            f"observation and were SKIPPED (not scheduled): {shown}"
            + ("..." if len(stale) > 5 else "")
        )
    if realized and not pairs:
        warnings.append(
            f"none of {len(realized)} realized release date(s) matched a bdh "
            "observation — check the bdh lookback window vs the release list"
        )
    return pairs, scheduled, warnings


def _rate_asof(rate_pairs: List[Tuple[date, float]], cutoff: date) -> Optional[float]:
    """The value of the last (date, value) observation strictly before
    ``cutoff``; None if no observation precedes it. ``rate_pairs`` MUST be
    sorted ascending by date."""
    result: Optional[float] = None
    for d, v in rate_pairs:
        if d < cutoff:
            result = v
        else:
            break
    return result


def _cb_meeting_rows(
    meeting_dates: List[date],
    rate_pairs: List[Tuple[date, float]],
    run_date: date,
    event: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """ADR 0008 §3 — turn a bank's meeting calendar + daily policy-rate series
    into event_calendar rows. ``actual`` = the rate held over the inter-meeting
    period this meeting opened (last observation strictly before the next
    meeting); ``prior`` = the rate before this meeting (last observation
    strictly before its own date). Future meetings are scheduled placeholders."""
    rows: List[Dict[str, Any]] = []
    meetings = sorted(set(meeting_dates))
    for k, M in enumerate(meetings):
        row = _blank_event_row()
        row["event_type"] = event.get("event_type")
        row["event_category"] = "central_bank_meeting"
        row["country"] = event.get("country")
        row["currency"] = event.get("currency")
        row["central_bank"] = event.get("central_bank")
        row["release_date"] = M.isoformat()
        if M <= run_date:
            next_M = meetings[k + 1] if k + 1 < len(meetings) else None
            cutoff = next_M if next_M is not None else (run_date + timedelta(days=1))
            actual = _rate_asof(rate_pairs, cutoff)
            prior = _rate_asof(rate_pairs, M)
            row["actual"] = actual
            row["prior"] = prior
            if actual is not None and prior is not None:
                if actual > prior:
                    decision = "hike"
                elif actual < prior:
                    decision = "cut"
                else:
                    decision = "hold"
                row["attributes"] = json.dumps({"decision": decision})
        rows.append(row)
    return rows


def _extract_economic_releases(
    section: Dict[str, Any],
    start_date: str,
    end_date: str,
    run_date: date,
    bdh_kwargs: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Extract economic-release event rows for one event playbook.

    Returns ``(rows, failed)``. ``failed`` lists every configured event that
    did NOT yield realized rows — a bdh/bds error, a missing ticker, or a
    zero-realized-row join. A non-empty ``failed`` makes the caller refuse to
    upload (ADR 0008 §4 coverage gate — no partial event artifact).
    """
    actual_field = section["actual_field"]
    survey_fields = section["survey_fields"]  # {event_calendar_column: bbg_field}
    release_date_field = section["release_date_field"]
    rows: List[Dict[str, Any]] = []
    failed: List[str] = []

    for event in section["events"]:
        ticker = event.get("ticker")
        event_type = event.get("event_type")
        if not ticker or not event_type:
            failed.append(f"{event_type or '<no event_type>'}: missing ticker/event_type")
            print(f"    [ERROR] economic_release event missing ticker/event_type: {event}")
            continue

        # Per-event consensus opt-out (ADR 0008 §2): an indicator Bloomberg
        # exposes no survey for (e.g. Japan's composite PMI) sets
        # ``survey: false`` — its consensus columns are then deliberately NULL.
        use_survey = bool(event.get("survey", True))
        event_survey = survey_fields if use_survey else {}
        requested_fields = [actual_field] + [
            f for f in event_survey.values() if f != actual_field
        ]

        try:
            bdh_frames: List[pd.DataFrame] = []
            for chunk in _chunked(requested_fields, MAX_HISTORICAL_FIELDS_PER_REQUEST):
                raw = blp.bdh(
                    tickers=ticker, flds=chunk,
                    start_date=start_date, end_date=end_date, **bdh_kwargs,
                )
                norm = _normalize_bdh_output(raw, fallback_ticker=ticker)
                if not norm.empty:
                    bdh_frames.append(norm)
            bdh_long = (
                pd.concat(bdh_frames, ignore_index=True)
                if bdh_frames else pd.DataFrame()
            )
            period_records = _bdh_to_period_records(bdh_long, actual_field, event_survey)
            release_dates = _parse_bds_dates(blp.bds(ticker, release_date_field))
        except Exception as exc:
            failed.append(f"{event_type}: bdh/bds error — {exc}")
            print(f"    [ERROR] {event_type}: bdh/bds failed for {ticker}: {exc}")
            continue

        pairs, scheduled, warnings = _join_econ_releases(
            period_records, release_dates, run_date
        )
        for w in warnings:
            print(f"    [WARN] {event_type}: {w}")

        if not pairs:
            failed.append(f"{event_type}: 0 realized event rows")
            print(
                f"    [ERROR] {event_type}: produced 0 realized event rows "
                "(bdh lookback window vs release list mismatch?)"
            )
            continue

        for release_date, period, prior_actual in pairs:
            row = _blank_event_row()
            row["event_type"] = event_type
            row["event_category"] = "economic_release"
            row["country"] = event.get("country")
            row["currency"] = event.get("currency")
            row["release_date"] = release_date.isoformat()
            row["period"] = str(period["period_date"])[:10]
            row["actual"] = _clean_scalar(period.get("actual"))
            for col in event_survey:
                row[col] = _clean_scalar(period.get(col))
            row["prior"] = _clean_scalar(prior_actual)
            rows.append(row)

        for release_date in scheduled:
            row = _blank_event_row()
            row["event_type"] = event_type
            row["event_category"] = "economic_release"
            row["country"] = event.get("country")
            row["currency"] = event.get("currency")
            row["release_date"] = release_date.isoformat()
            rows.append(row)

        print(
            f"    [OK] {event_type} ({ticker}): {len(pairs)} realized + "
            f"{len(scheduled)} scheduled event row(s)"
        )
    return rows, failed


def _extract_cb_meetings(
    section: Dict[str, Any],
    start_date: str,
    end_date: str,
    run_date: date,
    bdh_kwargs: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Extract central-bank-meeting event rows for one event playbook.

    Returns ``(rows, failed)`` — ``failed`` lists every configured bank that
    did not yield meeting rows (bdh/bds error, missing rate_ticker, or an empty
    meeting calendar). A non-empty ``failed`` aborts the upload (ADR 0008 §4).
    """
    rate_field = section["rate_field"]
    calendar_field = section["meeting_calendar_field"]
    rows: List[Dict[str, Any]] = []
    failed: List[str] = []

    for event in section["events"]:
        rate_ticker = event.get("rate_ticker")
        event_type = event.get("event_type")
        if not rate_ticker or not event_type:
            failed.append(f"{event_type or '<no event_type>'}: missing rate_ticker/event_type")
            print(f"    [ERROR] central_bank_meeting event missing rate_ticker/event_type: {event}")
            continue
        try:
            meeting_dates = _parse_bds_dates(blp.bds(rate_ticker, calendar_field))
            raw = blp.bdh(
                tickers=rate_ticker, flds=[rate_field],
                start_date=start_date, end_date=end_date, **bdh_kwargs,
            )
            norm = _normalize_bdh_output(raw, fallback_ticker=rate_ticker)
        except Exception as exc:
            failed.append(f"{event_type}: bdh/bds error — {exc}")
            print(f"    [ERROR] {event_type}: bdh/bds failed for {rate_ticker}: {exc}")
            continue

        rate_pairs: List[Tuple[date, float]] = []
        for _, r in norm.iterrows():
            try:
                d = date.fromisoformat(str(r["trade_date"])[:10])
                v = float(r["field_value"])
            except (ValueError, TypeError):
                continue
            rate_pairs.append((d, v))
        rate_pairs.sort()

        bank_rows = _cb_meeting_rows(meeting_dates, rate_pairs, run_date, event)
        if not bank_rows:
            failed.append(f"{event_type}: 0 meeting rows (empty meeting calendar)")
            print(f"    [ERROR] {event_type}: produced 0 meeting rows for {rate_ticker}")
            continue
        rows.extend(bank_rows)
        print(
            f"    [OK] {event_type} ({rate_ticker}): {len(bank_rows)} meeting "
            f"event row(s) from {len(meeting_dates)} calendar date(s)"
        )
    return rows, failed


def run_event_calendar_extraction(selected_playbooks: Optional[Set[str]] = None) -> None:
    """``--mode event-calendar`` — extract macro events (economic releases,
    central-bank meetings) into wide event parquets under gs://<bucket>/events/.

    ADR: docs_revamped/05_decisions/0008-event-playbook-contract.md.
    """
    print("Initializing Library Extraction Agent (mode: event-calendar)...")

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

        if selected_playbooks:
            playbook_files = [
                p for p in playbook_files
                if p.name in selected_playbooks or p.stem in selected_playbooks
            ]
            print(f"\n[INFO] Playbook filter active: {sorted(selected_playbooks)}")
            print(f"[INFO] Matched {len(playbook_files)} playbook file(s) after filtering.")

        if not playbook_files:
            print("[ABORT] No playbooks found in the GCP bucket. Exiting.")
            any_failures = True
            return

        print("\n[PHASE 2] Executing event-calendar extraction...")
        run_date = date.today()

        for pb_path in playbook_files:
            with open(pb_path, "r", encoding="utf-8") as f:
                playbook = yaml.safe_load(f) or {}

            try:
                section = _resolve_event_calendar_section(playbook)
            except ValueError as exc:
                print(
                    f"\n[ERROR] {pb_path.name}: malformed event_calendar section "
                    f"— {exc} Aborting this playbook."
                )
                any_failures = True
                continue
            if section is None:
                print(
                    f"\n[SKIP] {pb_path.name}: no event_calendar section "
                    "(expected for non-event playbooks)."
                )
                continue

            lineage_meta = _get_playbook_metadata(playbook, pb_path, script_path)
            dataset_name = lineage_meta["dataset_name"]
            bdh_kwargs = _build_historical_request_kwargs(playbook)
            extraction_cfg = playbook.get("extraction", {}) or {}
            try:
                lookback_days = int(
                    extraction_cfg.get("lookback_days") or DEFAULT_EVENT_LOOKBACK_DAYS
                )
            except (TypeError, ValueError):
                lookback_days = DEFAULT_EVENT_LOOKBACK_DAYS
            start_date = (run_date - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
            end_date = run_date.strftime("%Y-%m-%d")
            extracted_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            print(f"\nProcessing event playbook: {pb_path.name}")
            print(f"  Family       : {section['family']}")
            print(f"  Events       : {len(section['events'])}")
            print(f"  Window       : {start_date} -> {end_date}")

            try:
                if section["family"] == "economic_release":
                    rows, failed = _extract_economic_releases(
                        section, start_date, end_date, run_date, bdh_kwargs
                    )
                else:
                    rows, failed = _extract_cb_meetings(
                        section, start_date, end_date, run_date, bdh_kwargs
                    )
            except Exception as exc:
                print(f"  [ERROR] Event extraction failed for {pb_path.name}: {exc}")
                any_failures = True
                continue

            # COVERAGE GATE (ADR 0008 §4) — every configured event must yield
            # rows. A single failed event aborts the whole playbook's upload:
            # a partial event artifact must never ingest as a clean SUCCESS.
            if failed:
                print(
                    f"  [ABORT] {pb_path.name}: {len(failed)}/"
                    f"{len(section['events'])} configured event(s) failed "
                    "extraction — refusing to upload a partial event artifact:"
                )
                for reason in failed:
                    print(f"    - {reason}")
                any_failures = True
                continue

            if not rows:
                print(f"  [WARNING] {pb_path.name}: 0 event rows extracted. Skipping upload.")
                any_failures = True
                continue

            df = pd.DataFrame(rows)
            df["event_category"] = df["event_category"].fillna(section["event_category"])
            df["dataset_name"] = dataset_name
            df["playbook_name"] = lineage_meta["playbook_name"]
            df["playbook_version"] = lineage_meta["playbook_version"]
            df["playbook_hash"] = lineage_meta["playbook_hash"]
            df["git_commit_hash"] = lineage_meta["git_commit_hash"]
            df["extractor_version"] = lineage_meta["extractor_version"]
            df["extraction_mode"] = EVENT_CALENDAR_EXTRACTION_MODE
            df["requested_start_date"] = start_date
            df["requested_end_date"] = end_date
            df["extracted_at"] = extracted_at

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            parquet_filename = f"{dataset_name}_events_{timestamp}.parquet"
            local_parquet_path = temp_data_dir / parquet_filename
            df.to_parquet(local_parquet_path, engine="pyarrow", index=False)

            blob_name = f"events/{dataset_name}/{parquet_filename}"
            bucket.blob(blob_name).upload_from_filename(str(local_parquet_path))
            local_parquet_path.unlink(missing_ok=True)
            print(
                f"  [SUCCESS] Event parquet uploaded to gs://{BUCKET_NAME}/"
                f"{blob_name}  ({len(df)} row(s))"
            )

        try:
            print("\n[PHASE 3] Uploading latest terminal extractor script to GCP...")
            latest_blob = bucket.blob(f"scripts/{script_path.name}")
            latest_blob.upload_from_filename(str(script_path))
            print(f"  [SUCCESS] Latest script uploaded to gs://{BUCKET_NAME}/scripts/{script_path.name}")
        except Exception as exc:
            print(f"  [WARNING] Failed to upload terminal extractor script: {exc}")

    except Exception as exc:
        any_failures = True
        print(f"[FATAL] Event-calendar pipeline failed: {exc}")

    finally:
        print("\nCleaning up temporary files...")
        for d in (temp_playbooks_dir, temp_data_dir):
            if d.exists():
                for f in d.glob("*"):
                    if f.is_file():
                        f.unlink()
                try:
                    d.rmdir()
                except OSError:
                    pass
        if any_failures:
            print("\n*** EVENT-CALENDAR EXTRACTION COMPLETE (WITH FAILURES) ***")
            sys.exit(1)
        print("\n*** EVENT-CALENDAR EXTRACTION COMPLETE ***")


# ============================================================================
# DELIVERABLES EXTRACTION  (work order C3, ADR 0011)
#
# ``--mode deliverables`` extracts per-bond-future-contract deliverable
# baskets + conversion factors + delivery / notice dates. Wide parquet (one
# row per ``(generic, contract_code, deliverable_cusip)``) uploaded to
# ``gs://<bucket>/deliverables/<dataset>/`` and consumed by the ingester's
# ``deliverables/`` route into ``macro_data.futures_deliverables``.
#
# Structurally close to ``--mode metadata-history`` (same ``bds(FUT_CHAIN)``
# chain enumeration). The per-contract step adds one ``bds`` call (the basket
# field, e.g. FUT_DLVRBL_BNDS_AND_CONV_FACTORS) and one ``bdp`` call (the
# delivery / notice dates). The exact Bloomberg mnemonics are declared per
# playbook (the ``deliverables:`` section on ``bond_futures.yml``, added in
# C4) and remain CANDIDATE until C4's operator verification confirms them.
#
# CANONICAL / SYNC INVARIANT — ``_DELIVERABLES_TYPED_COLUMNS`` below is the
# extractor-side mirror of ``ingestion.deliverables.DELIVERABLES_TYPED_COLUMNS``.
# If you change one, change the other AND the matching copy in
# ``utils/historical_extractor.py``. (The extractor stays self-contained — no
# project-internal imports — so each script carries its own copy.)
# ============================================================================
DELIVERABLES_EXTRACTION_MODE = "deliverables"

_DELIVERABLES_TYPED_COLUMNS: tuple = (
    "deliverable_isin",
    "conversion_factor",
    "first_delivery_date",
    "last_delivery_date",
    "first_notice_date",
    "last_notice_date",
)


# The per-contract static-date columns that ``futures_deliverables`` exposes
# as typed columns. Every ``static_fields[*].column_name`` must be one of
# these (ADR 0011 v4, Codex finding 2 — third round). SYNC INVARIANT with
# ``utils/historical_extractor.py``.
_ALLOWED_DELIVERABLES_STATIC_COLUMNS: frozenset = frozenset({
    "first_delivery_date",
    "last_delivery_date",
    "first_notice_date",
    "last_notice_date",
})


def _resolve_deliverables_section(playbook: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Validate and return the playbook's ``deliverables:`` section. SYNC
    INVARIANT with ``utils/historical_extractor.py``.

    Returns None when the section is **legitimately absent or disabled** — no
    ``deliverables:`` key, non-mapping value, or ``enabled`` not truthy. A
    playbook that simply doesn't participate in deliverables extraction is
    skipped without failing the run.

    **Raises ValueError when the section is enabled but malformed** (ADR 0011
    v3, Codex finding 3). Once the operator opts in via ``enabled: true``,
    any missing required field or malformed entry is a configuration error —
    *never* a silent no-op. Mirrors event_calendar's
    ``_resolve_event_calendar_section`` discipline (ADR 0008).

    Required fields when enabled (ADR 0011 v4 — conversion factor and the
    per-contract dates are core source data, not optional decoration):

      * ``chain_field`` — bds field that enumerates the contract chain.
      * ``basket_field`` — bds field that returns the deliverable basket.
      * ``basket_cusip_column`` — basket-frame column carrying the CUSIP.
      * ``basket_factor_column`` — basket-frame column carrying the
        conversion factor (Codex finding 1, third round).
      * ``static_fields`` — non-empty list of ``{column_name, bloomberg_field}``
        entries. Every ``column_name`` must be in
        :data:`_ALLOWED_DELIVERABLES_STATIC_COLUMNS` and every
        ``bloomberg_field`` must be a non-blank string (Codex finding 2,
        third round).

    Optional ``include_tickers`` whitelist (ADR 0011 v2, Codex finding 1):
    when present must be a non-empty list of non-empty strings. The
    **exact-match against the playbook universe** lives in
    :func:`_filter_universe_by_include_tickers` (ADR 0011 v3, Codex finding
    2 — second round).
    """
    section = playbook.get("deliverables")
    if not isinstance(section, dict):
        return None
    if not section.get("enabled"):
        return None
    # ENABLED — every required field MUST be present and well-formed.
    if not section.get("chain_field"):
        raise ValueError(
            "deliverables: section is enabled but `chain_field` is missing"
        )
    if not section.get("basket_field"):
        raise ValueError(
            "deliverables: section is enabled but `basket_field` is missing"
        )
    if not section.get("basket_cusip_column"):
        raise ValueError(
            "deliverables: section is enabled but `basket_cusip_column` is missing"
        )
    if not section.get("basket_factor_column"):
        raise ValueError(
            "deliverables: section is enabled but `basket_factor_column` is "
            "missing. Conversion factor is core source data; the playbook MUST "
            "name the basket-frame column carrying it (ADR 0011 v4)."
        )
    static_fields = section.get("static_fields")
    if not isinstance(static_fields, list) or not static_fields:
        raise ValueError(
            "deliverables: section is enabled but `static_fields` is missing or "
            "not a non-empty list"
        )
    seen_column_names: set = set()
    for i, entry in enumerate(static_fields):
        if not isinstance(entry, dict):
            raise ValueError(
                f"deliverables: static_fields[{i}] must be a mapping with "
                f"`column_name` and `bloomberg_field` keys"
            )
        col = entry.get("column_name")
        fld = entry.get("bloomberg_field")
        if not isinstance(col, str) or not col.strip():
            raise ValueError(
                f"deliverables: static_fields[{i}] is missing or has a blank "
                f"`column_name`"
            )
        if col not in _ALLOWED_DELIVERABLES_STATIC_COLUMNS:
            raise ValueError(
                f"deliverables: static_fields[{i}].column_name={col!r} is not "
                f"in the allowed set "
                f"{sorted(_ALLOWED_DELIVERABLES_STATIC_COLUMNS)}. These are "
                "the only typed per-contract date columns on "
                "macro_data.futures_deliverables."
            )
        if col in seen_column_names:
            raise ValueError(
                f"deliverables: static_fields[{i}].column_name={col!r} is "
                "duplicated; each per-contract date column may be configured "
                "only once."
            )
        seen_column_names.add(col)
        if not isinstance(fld, str) or not fld.strip():
            raise ValueError(
                f"deliverables: static_fields[{i}] (column_name={col!r}) is "
                "missing or has a blank `bloomberg_field`"
            )
    include_tickers = section.get("include_tickers")
    if include_tickers is not None:
        if not isinstance(include_tickers, list) or not include_tickers:
            raise ValueError(
                "deliverables: `include_tickers` must be a non-empty list when "
                "present (omit the key to scope to the full rolling universe)"
            )
        if not all(isinstance(t, str) and t.strip() for t in include_tickers):
            raise ValueError(
                "deliverables: `include_tickers` entries must be non-blank "
                "strings"
            )
    # ADR 0011 v6.1: optional per-ticker chain-depth cap (mapping of
    # vendor_ticker -> positive int). Workaround for Bloomberg's
    # historical-data cutoff on FUT_DLVRBLE_BNDS_CUSIPS. When present,
    # the extractor slices each capped chain to its newest N entries
    # before probing baskets, so the v3 strict contract-level coverage
    # gate applies only to the in-coverage portion of each chain.
    chain_max_length = section.get("chain_max_length")
    if chain_max_length is not None:
        if not isinstance(chain_max_length, dict) or not chain_max_length:
            raise ValueError(
                "deliverables: `chain_max_length` must be a non-empty "
                "mapping of vendor_ticker -> positive int when present "
                "(omit the key to disable per-ticker chain capping)"
            )
        for ticker_key, cap_val in chain_max_length.items():
            if not isinstance(ticker_key, str) or not ticker_key.strip():
                raise ValueError(
                    f"deliverables: `chain_max_length` keys must be non-blank "
                    f"strings; got {ticker_key!r}"
                )
            if (
                isinstance(cap_val, bool)
                or not isinstance(cap_val, int)
                or cap_val <= 0
            ):
                raise ValueError(
                    f"deliverables: `chain_max_length[{ticker_key!r}]` must be "
                    f"a positive int; got {cap_val!r}"
                )
    return section


# ============================================================================
# Pure helpers extracted from run_deliverables_extraction for direct unit
# testing (ADR 0011 v3, Codex finding 4). SYNC INVARIANT with
# utils/historical_extractor.py.
# ============================================================================
def _filter_universe_by_include_tickers(
    universe_items: List[Dict[str, Any]],
    include_tickers: Optional[List[str]],
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Apply the optional ``include_tickers`` whitelist to the rolling-contract
    universe. Returns ``(filtered_items, unmatched_whitelist_entries)``.

    ADR 0011 v3 (Codex finding 2) — a whitelist entry that does NOT match any
    rolling-contract universe ticker is a typo / out-of-scope reference and
    is surfaced to the caller. The caller MUST abort the load when
    ``unmatched`` is non-empty. v2 silently dropped unmatched entries, which
    could disguise a typo as a legitimate scope reduction.

    ``include_tickers`` of None / empty means "no whitelist active" — every
    rolling-contract universe row participates and ``unmatched`` is empty.
    """
    if not include_tickers:
        return list(universe_items), []
    allow = [str(t) for t in include_tickers]
    universe_tickers = {it["ticker"] for it in universe_items}
    unmatched = sorted(t for t in allow if t not in universe_tickers)
    allow_set = set(allow)
    filtered = [it for it in universe_items if it["ticker"] in allow_set]
    return filtered, unmatched


def _evaluate_deliverables_coverage(
    generics_with_data: int,
    expected_generics: int,
    total_contracts_probed: int,
    total_contracts_with_basket: int,
    missed_contracts: Optional[List[str]] = None,
) -> Optional[str]:
    """Strict all-or-nothing coverage check (ADR 0011 v3, §4).

    Two gates, evaluated in order; the first failure returns an error message
    describing the gap. Returns None on clean coverage.

      1. **Generic-level gate** (Codex finding 2 / v2) — every CONFIGURED
         generic (post-whitelist) must produce at least one basket.
      2. **Contract-level gate** (Codex finding 1 / v3) — every PROBED
         contract on every generic's chain must produce a basket. A v2 load
         could pass when 1/179 TY contracts emitted rows; v3 closes that.

    Deliverable baskets are *curated reference data*, not a broad discovery
    universe — partial loads are never legitimate. If a contract is genuinely
    out of scope (e.g. Bloomberg has dropped the basket for very old expired
    contracts), restrict the chain via the playbook's ``chain_overrides`` and
    document the deferral. Same rule as WIRP (ADR 0009 §4).
    """
    if expected_generics and generics_with_data < expected_generics:
        return (
            f"Strict generic coverage gate: only {generics_with_data}/"
            f"{expected_generics} configured generic(s) produced a deliverable "
            f"basket. All-or-nothing rule (ADR 0011 §4)."
        )
    if total_contracts_probed and total_contracts_with_basket < total_contracts_probed:
        shortfall = total_contracts_probed - total_contracts_with_basket
        sample = ""
        if missed_contracts:
            head = ", ".join(missed_contracts[:5])
            extra = (
                f" (+{len(missed_contracts) - 5} more)"
                if len(missed_contracts) > 5 else ""
            )
            sample = f" Missed: {head}{extra}."
        return (
            f"Strict contract coverage gate: only "
            f"{total_contracts_with_basket}/{total_contracts_probed} probed "
            f"contract(s) produced a basket ({shortfall} short).{sample} "
            "Curated reference data is all-or-nothing (ADR 0011 v3 §4). "
            "If a contract is genuinely out of scope, restrict the chain via "
            "the playbook's `chain_overrides` and document the deferral."
        )
    return None


def _stamp_deliverables_audit_suffix(
    lineage_meta: Dict[str, Any],
) -> Dict[str, Any]:
    """Audit-key isolation (ADR 0011 v2, Codex finding 3).

    Suffix ``playbook_name`` with ``__deliverables`` so the ingester's
    per-playbook dedup / load-audit lookup scopes deliverables artifacts
    away from the sibling ``--mode time-series`` and ``--mode
    metadata-history`` flows on the same playbook (e.g. bond_futures.yml).
    Mirrors the OTR-resolver pattern (``__otr_resolution``).

    Raises ``ValueError`` when ``playbook_name`` is missing — silently
    stamping ``None__deliverables`` would let a junk audit row land.
    """
    if not lineage_meta.get("playbook_name"):
        raise ValueError(
            "lineage_meta is missing `playbook_name`; cannot stamp the "
            "deliverables audit-key suffix"
        )
    return dict(
        lineage_meta,
        playbook_name=f"{lineage_meta['playbook_name']}__deliverables",
    )


def _apply_chain_max_length(
    contracts: List[str], chain_max_length: Optional[int],
) -> List[str]:
    """Cap a chain to the newest N entries (ADR 0011 v6.1).

    Bloomberg's ``FUT_CHAIN`` returns contracts in chronological
    (oldest-first) order. When ``chain_max_length`` is set, the extractor
    keeps only the most-recent N contracts — a workaround for Bloomberg's
    empirical historical-data cutoff on basket reference data
    (``FUT_DLVRBLE_BNDS_CUSIPS`` drops basket data for the oldest ~7-10
    years of each UST generic's chain; the v3 strict contract-level
    coverage gate would otherwise abort the load on those documented gaps).

    Returns the original list when ``chain_max_length`` is None or when the
    chain is already shorter than the cap. Returns the last
    ``chain_max_length`` entries (Python negative indexing) otherwise.

    Raises ``ValueError`` on non-positive or non-int cap (the validator
    should catch this earlier; this is defence-in-depth).

    SYNC INVARIANT with ``utils/historical_extractor.py``.
    """
    if chain_max_length is None:
        return contracts
    if isinstance(chain_max_length, bool) or not isinstance(chain_max_length, int):
        raise ValueError(
            f"chain_max_length must be a positive int or None; got "
            f"{type(chain_max_length).__name__}={chain_max_length!r}"
        )
    if chain_max_length <= 0:
        raise ValueError(
            f"chain_max_length must be a positive int; got {chain_max_length!r}"
        )
    if len(contracts) <= chain_max_length:
        return list(contracts)
    return list(contracts[-chain_max_length:])


# Bloomberg yellow-key suffixes that may appear on bds-returned identifier
# strings. " Govt" is the only one observed on UST FUT_DLVRBLE_BNDS_CUSIPS
# (operator probe 2026-05-23); the others are listed for future scope.
_BLOOMBERG_YELLOW_KEYS: tuple = (
    " Govt", " Corp", " Equity", " Comdty", " Mtge", " M-Mkt", " Index",
)


def _canonicalize_deliverable_cusip(raw_value: Any) -> str:
    """Canonicalise a Bloomberg deliverable-basket CUSIP value to its
    standard 9-character form (ADR 0011 v6 / C4 Phase A.3).

    Bloomberg's ``FUT_DLVRBLE_BNDS_CUSIPS`` bulk-data field returns
    deliverable bonds in the form ``"{8-char CUSIP stem} {yellow-key}"``
    (e.g. ``"9128273H Govt"`` for a UST). The 9th character (the check
    digit) is truncated for display because the stem + yellow-key tuple
    already resolves unambiguously to one Bloomberg security; the
    canonical 9-char CUSIP is reconstructed by computing the standard
    CUSIP Global Services Modulus-10-Double-Add-Double check digit.

    Behaviour:
      * If the value carries a recognised Bloomberg yellow-key suffix
        (``" Govt"``, etc.): strip the suffix, verify the remaining stem
        is exactly 8 alphanumeric characters, compute the check digit,
        and return ``stem + str(check_digit)``.
      * If the value carries NO yellow-key suffix: return it stripped
        of leading/trailing whitespace as-is (already-canonical input
        from a non-Bloomberg source; trust the caller). This preserves
        backward compatibility with the C3 tests that pass synthetic
        9-character CUSIPs.

    Raises ``ValueError`` (fail-closed) on:
      * non-string input,
      * blank / empty input,
      * yellow-key-present-but-stem-not-8-chars,
      * stem with invalid character (anything outside ``0-9 A-Z * @ #``).

    **P12 justification** — this is *identifier canonicalisation*, not
    market-data recomputation. Bloomberg stores the full 9-char CUSIP
    internally; the bulk-data viewer truncates the check digit purely as
    a display convention (the stem + yellow-key resolves identically).
    The check-digit algorithm is the public CGS standard, deterministic,
    and reconstructs the identifier Bloomberg itself uses. Analogous to
    normalising a returned date string to ISO YYYY-MM-DD form — not
    analogous to deriving a price or yield (which P12 forbids).

    SYNC INVARIANT with ``utils/historical_extractor.py``.
    """
    if raw_value is None:
        raise ValueError("deliverable CUSIP is None")
    if not isinstance(raw_value, str):
        raise ValueError(
            f"deliverable CUSIP must be a string, got "
            f"{type(raw_value).__name__}={raw_value!r}"
        )
    s = raw_value.strip()
    if not s:
        raise ValueError("deliverable CUSIP is empty after stripping")

    # Detect a Bloomberg yellow-key suffix (defines whether we're in the
    # canonicalisation branch or the passthrough branch).
    stem: Optional[str] = None
    for yk in _BLOOMBERG_YELLOW_KEYS:
        if s.endswith(yk):
            stem = s[: -len(yk)].rstrip()
            break
    if stem is None:
        # No yellow-key — passthrough (already-canonical caller input).
        return s

    if len(stem) != 8:
        raise ValueError(
            f"deliverable CUSIP stem {stem!r} (from {raw_value!r}) is "
            f"{len(stem)} chars after stripping yellow-key; expected exactly 8"
        )

    # Canonical CUSIPs are upper-case (CGS convention). Bloomberg returns
    # upper-case in practice; uppercasing here ensures the emitted natural
    # key is deterministic regardless of caller-side input casing.
    stem = stem.upper()

    # CUSIP Global Services Modulus-10-Double-Add-Double.
    #   For each character at position i (1-indexed, i = 1..8):
    #     numeric value: 0-9 -> 0-9; A-Z -> 10-35; * -> 36; @ -> 37; # -> 38;
    #     if position i is EVEN, multiply value by 2;
    #     sum the decimal digits of the (possibly doubled) value;
    #     add to running total.
    #   check_digit = (10 - (total mod 10)) mod 10.
    total = 0
    for i, ch in enumerate(stem, start=1):
        if ch.isdigit():
            v = int(ch)
        elif ch.isalpha():
            v = ord(ch.upper()) - ord("A") + 10
        elif ch == "*":
            v = 36
        elif ch == "@":
            v = 37
        elif ch == "#":
            v = 38
        else:
            raise ValueError(
                f"deliverable CUSIP stem {stem!r} (from {raw_value!r}) has "
                f"invalid character {ch!r} at position {i}"
            )
        if i % 2 == 0:
            v *= 2
        while v > 9:
            v = v // 10 + v % 10
        total += v
    check_digit = (10 - total % 10) % 10
    return stem + str(check_digit)


def _stage_contract_rows(
    basket_df: "pd.DataFrame",
    dates_raw: Dict[str, Any],
    generic_ticker: str,
    contract_code: str,
    basket_cusip_column: str,
    basket_factor_column: str,
    basket_isin_column: Optional[str],
    date_field_to_column: Dict[str, str],
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Stage one contract's deliverable basket rows for emission, OR return a
    miss reason. Pure function — no Bloomberg, no GCS, no project-internal
    imports. SYNC INVARIANT with ``utils/historical_extractor.py``.

    Returns ``(rows, None)`` when the contract is fully well-formed; every
    row carries the complete typed-column set ready for parquet emission.
    Returns ``([], miss_reason)`` when the contract MUST be counted as missed
    by the strict all-or-nothing coverage gate (ADR 0011 v4 §1, §2 — Codex
    findings 1 & 2, third round):

      * ``missing_basket_column:<col>`` — the configured CUSIP or factor
        column is absent from the basket frame.
      * ``missing_static_field:<col_name>`` — a configured per-contract
        static field has no value for this contract (the bdp returned None
        / NaN / blank).
      * ``missing_factor_for_cusip:<cusip>`` — a basket row has a CUSIP
        but no conversion factor (the factor column exists; the cell is
        empty). Conversion factor is core source data, never optional.
      * ``empty_basket`` — basket frame had rows but every row carried a
        blank CUSIP (data-shaped but semantically empty).

    All-or-nothing per row: even one structurally incomplete row fails the
    whole contract. Reference data must never silently load with NULLs.
    """
    lower_cols = {str(c).lower(): c for c in basket_df.columns}
    cusip_col_actual = lower_cols.get(basket_cusip_column.lower())
    if cusip_col_actual is None:
        return [], f"missing_basket_column:{basket_cusip_column}"
    factor_col_actual = lower_cols.get(basket_factor_column.lower())
    if factor_col_actual is None:
        return [], f"missing_basket_column:{basket_factor_column}"
    isin_col_actual = (
        lower_cols.get(basket_isin_column.lower())
        if basket_isin_column else None
    )

    # Per-configured static field: presence + date PARSEABILITY check (ADR
    # 0011 v5 / Codex finding, fourth round). v4 caught None / NaN / blank-
    # whitespace; v5 additionally rejects strings that are not real dates,
    # because the ingester's ``_to_iso_date`` coerces unparseable strings to
    # None and would otherwise silently NULL them on the way to
    # ``futures_deliverables``. Pre-compute canonical ISO strings once per
    # contract so every row of the basket emits the same denormalised date in
    # the same canonical form.
    parsed_static_values: Dict[str, str] = {}
    for fld_upper, col_name in date_field_to_column.items():
        cleaned = _clean_scalar(dates_raw.get(fld_upper))
        if cleaned is None or (isinstance(cleaned, str) and not cleaned.strip()):
            return [], f"missing_static_field:{col_name}"
        try:
            ts = pd.to_datetime(cleaned, errors="raise")
        except (ValueError, TypeError):
            return [], f"invalid_static_field:{col_name}"
        if pd.isna(ts):
            return [], f"invalid_static_field:{col_name}"
        parsed_static_values[col_name] = ts.date().isoformat()

    contract_rows: List[Dict[str, Any]] = []
    for _, basket_row in basket_df.iterrows():
        raw_cusip = _clean_scalar(basket_row[cusip_col_actual])
        if not raw_cusip:
            # Blank CUSIPs are basket-frame padding from Bloomberg — skip the
            # row rather than fail (the basket may still be valid overall).
            continue
        # ADR 0011 v6 / C4: canonicalise the deliverable CUSIP to its 9-char
        # standard form. Bloomberg's FUT_DLVRBLE_BNDS_CUSIPS bulk-data returns
        # "{8-char stem} Govt"; the natural key needs the canonical 9-char form
        # so the ingester's optional FK lookup `deliverable_cusip =
        # instrument_master.cusip` resolves. Fail-closed on any input the
        # canonicaliser cannot validate.
        try:
            canonical_cusip = _canonicalize_deliverable_cusip(raw_cusip)
        except ValueError:
            return [], f"invalid_cusip_for_basket:{raw_cusip!r}"
        factor_val = _clean_scalar(basket_row[factor_col_actual])
        # v4: None / blank → missing. v5: also parse-validate to a finite
        # float so "N/A" / "  " / "NaN"-as-string become explicit misses,
        # never silently NULLed by the ingester's ``_num_or_none``.
        if factor_val is None or (
            isinstance(factor_val, str) and not factor_val.strip()
        ):
            return [], f"missing_factor_for_cusip:{canonical_cusip}"
        try:
            factor_num = float(factor_val)
        except (TypeError, ValueError):
            return [], f"invalid_factor_for_cusip:{canonical_cusip}"
        # NaN is the only float not equal to itself; reject it.
        if factor_num != factor_num:
            return [], f"invalid_factor_for_cusip:{canonical_cusip}"
        isin_val = (
            _clean_scalar(basket_row[isin_col_actual])
            if isin_col_actual else None
        )
        row: Dict[str, Any] = {
            "vendor_ticker": generic_ticker,
            "contract_code": contract_code,
            "deliverable_cusip": canonical_cusip,
            "deliverable_isin": str(isin_val) if isin_val else None,
            "conversion_factor": factor_num,
            # ADR 0011 v6: preserve the raw Bloomberg identifier per row for
            # audit. The ingester's parser auto-routes any column not in the
            # typed-column set / not in _EXCLUDE_FOR_ATTRIBUTES into the
            # JSONB attributes blob, so this lands at
            # macro_data.futures_deliverables.attributes
            # ->> 'raw_deliverable_bond_cusip_and_yellow_key'.
            "raw_deliverable_bond_cusip_and_yellow_key": str(raw_cusip),
        }
        # Emit the pre-parsed canonical ISO strings — one value per
        # configured static field, identical across every row of the basket
        # (denormalisation, ADR 0011 §Alternatives).
        for col_name, iso in parsed_static_values.items():
            row[col_name] = iso
        contract_rows.append(row)

    if not contract_rows:
        return [], "empty_basket"
    return contract_rows, None


def _fetch_contract_deliverable_basket(
    contract_ticker: str,
    basket_field: str,
    request_kwargs: Dict[str, Any],
) -> Optional[pd.DataFrame]:
    """Thin wrapper around ``bds(contract, basket_field)`` returning the
    deliverable-basket frame (one row per deliverable bond, with the columns
    the playbook's ``basket_cusip_column`` / ``basket_factor_column`` /
    optional ``basket_isin_column`` map). Returns None on error / empty."""
    try:
        df = blp.bds(
            tickers=contract_ticker,
            flds=basket_field,
            **(request_kwargs or {}),
        )
    except Exception as exc:
        print(
            f"      [WARNING] bds() basket failed for {contract_ticker} "
            f"{basket_field}: {exc}"
        )
        return None
    df = _coerce_to_pandas(df)
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return None
    return df


def run_deliverables_extraction(selected_playbooks: Optional[Set[str]] = None) -> None:
    """``--mode deliverables`` — extract per-bond-future-contract deliverable
    baskets + conversion factors + delivery / notice dates (ADR 0011).

    Reads the ``deliverables:`` section on each playbook (added in C4); for
    every rolling-contract universe row, enumerates the chain via
    ``bds(generic, chain_field, chain_overrides)``, then for each contract
    calls ``bds(contract, basket_field)`` for the basket + per-deliverable
    factors and ``bdp(contract, date_fields)`` for the per-contract delivery /
    notice dates. Emits a wide parquet (one row per ``(generic, contract_code,
    deliverable_cusip)``) and uploads to ``gs://<bucket>/deliverables/<dataset>/``.

    SYNC INVARIANT with ``utils/historical_extractor.py``.
    """
    print("Initializing Library Extraction Agent (mode: deliverables)...")

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

        if selected_playbooks:
            playbook_files = [
                p for p in playbook_files
                if p.name in selected_playbooks or p.stem in selected_playbooks
            ]
            print(f"\n[INFO] Playbook filter active: {sorted(selected_playbooks)}")
            print(f"[INFO] Matched {len(playbook_files)} playbook file(s) after filtering.")

        if not playbook_files:
            print("[ABORT] No playbooks found in the GCP bucket. Exiting.")
            any_failures = True
            return

        print("\n[PHASE 2] Executing deliverables extraction...")

        for pb_path in playbook_files:
            with open(pb_path, "r", encoding="utf-8") as f:
                playbook = yaml.safe_load(f) or {}

            try:
                section = _resolve_deliverables_section(playbook)
            except ValueError as cfg_err:
                # ADR 0011 v3 (Codex finding 3): a `deliverables:` section that
                # is enabled but malformed is a configuration error, never a
                # silent skip. Mark the run failed and continue to the next
                # playbook so we surface every misconfiguration in one pass.
                print(
                    f"\n[ABORT] {pb_path.name}: malformed `deliverables:` "
                    f"section -- {cfg_err}"
                )
                any_failures = True
                continue
            if section is None:
                print(
                    f"\n[SKIP] {pb_path.name}: no enabled deliverables section. "
                    "(Expected for any playbook other than bond_futures.yml.)"
                )
                continue

            lineage_meta = _get_playbook_metadata(playbook, pb_path, script_path)
            # AUDIT-KEY ISOLATION (ADR 0011 v2, Codex finding 3): stamp the
            # `__deliverables` suffix on `playbook_name` so dedup / load-audit
            # scope is isolated from the sibling time-series / metadata-history
            # flows on the same playbook (extracted helper for direct testing
            # per ADR 0011 v3, Codex finding 4).
            lineage_meta = _stamp_deliverables_audit_suffix(lineage_meta)
            dataset_name = lineage_meta["dataset_name"]
            universe_items = [
                item for item in (playbook.get("universe") or [])
                if isinstance(item, dict)
                and item.get("ticker")
                and item.get("is_rolling_contract")
            ]
            # `include_tickers` whitelist (ADR 0011 v3, Codex finding 2):
            # EVERY entry must exactly match a rolling-contract universe ticker.
            # v2 silently dropped unmatched entries, which could disguise a typo
            # as a legitimate scope reduction.
            universe_items, unmatched_include = _filter_universe_by_include_tickers(
                universe_items, section.get("include_tickers"),
            )
            if unmatched_include:
                print(
                    f"\n[ABORT] {pb_path.name}: deliverables `include_tickers` "
                    f"references {len(unmatched_include)} ticker(s) not in the "
                    f"rolling-contract universe: {unmatched_include}. Fix the "
                    "typo or extend the playbook universe; never silently scope."
                )
                any_failures = True
                continue

            reference_request_kwargs = _build_reference_request_kwargs(playbook)
            start_date, end_date = _resolve_date_window(playbook)

            chain_field          = section["chain_field"]
            chain_overrides      = section.get("chain_overrides") or {}
            chain_fallback_col   = section.get("chain_column_name")
            basket_field         = section["basket_field"]
            basket_cusip_column  = section["basket_cusip_column"]
            basket_factor_column = section.get("basket_factor_column")
            basket_isin_column   = section.get("basket_isin_column")
            static_fields        = section["static_fields"]

            date_bbg_fields: List[str] = []
            seen_fields: Set[str] = set()
            for entry in static_fields:
                if isinstance(entry, dict):
                    f = entry.get("bloomberg_field")
                    if f and f not in seen_fields:
                        date_bbg_fields.append(f)
                        seen_fields.add(f)

            date_field_to_column: Dict[str, str] = {}
            for entry in static_fields:
                if isinstance(entry, dict):
                    col = entry.get("column_name")
                    fld = entry.get("bloomberg_field")
                    if col and fld:
                        date_field_to_column[fld.upper()] = col

            if not universe_items:
                print(
                    f"\n[WARNING] {pb_path.name}: no rolling-contract tickers in "
                    "universe. Skipping."
                )
                continue

            print(f"\nProcessing Playbook: {pb_path.name}")
            print(f"  Playbook name: {lineage_meta['playbook_name']}")
            print(f"  Playbook version: {lineage_meta['playbook_version']}")
            print(f"  Rolling tickers: {len(universe_items)}")
            print(f"  Chain field: {chain_field}")
            print(f"  Basket field: {basket_field}")
            print(f"  Per-contract date fields: {date_bbg_fields}")

            extracted_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            all_rows: List[Dict[str, Any]] = []
            generics_with_data = 0
            total_contracts_probed = 0
            total_contracts_with_basket = 0
            # Per-contract miss tracking (ADR 0011 v3, Codex finding 1): we
            # need the actual contract tickers that failed to surface a basket
            # so the coverage-gate error message can name them.
            missed_contracts: List[str] = []

            # ADR 0011 v6.1: per-ticker chain-depth cap. Bloomberg's basket
            # reference data has an empirical historical cutoff (FUT_DLVRBLE_
            # BNDS_CUSIPS drops baskets for the oldest ~7-10 years of each
            # UST generic's chain). The cap trims each chain to its newest
            # N entries so the v3 strict contract-level gate applies only
            # to the in-coverage portion.
            chain_caps: Dict[str, int] = section.get("chain_max_length") or {}

            for item in universe_items:
                generic_ticker = item["ticker"]
                print(f"  Enumerating chain for {generic_ticker}...")
                contracts = _fetch_chain_underlyings(
                    generic_ticker=generic_ticker,
                    chain_field=chain_field,
                    chain_overrides=chain_overrides,
                    fallback_column=chain_fallback_col,
                )
                if not contracts:
                    print(f"    [!] No underlying contracts returned for {generic_ticker}")
                    continue
                full_chain_len = len(contracts)
                cap = chain_caps.get(generic_ticker)
                contracts = _apply_chain_max_length(contracts, cap)
                if cap is not None and len(contracts) < full_chain_len:
                    print(
                        f"    [INFO] chain_max_length={cap} applied for "
                        f"{generic_ticker}: trimmed {full_chain_len} -> "
                        f"{len(contracts)} contracts (newest kept; older "
                        "contracts excluded per Bloomberg's basket-data "
                        "historical cutoff, ADR 0011 v6.1)"
                    )
                print(f"    [OK] chain length (after cap) = {len(contracts)}")

                generic_emitted_any = False
                for contract_ticker in contracts:
                    total_contracts_probed += 1
                    basket_df = _fetch_contract_deliverable_basket(
                        contract_ticker=contract_ticker,
                        basket_field=basket_field,
                        request_kwargs=reference_request_kwargs,
                    )
                    if basket_df is None:
                        missed_contracts.append(contract_ticker)
                        continue

                    dates_raw = _fetch_underlying_contract_static(
                        underlying_ticker=contract_ticker,
                        bloomberg_fields=date_bbg_fields,
                        request_kwargs=reference_request_kwargs,
                    )

                    # Bare contract code — strip the yellow-key suffix
                    # (e.g. "TYZ24 Comdty" -> "TYZ24") so the parquet key
                    # matches ``instrument_metadata_history.contract_code``.
                    contract_code = (
                        contract_ticker.split()[0] if contract_ticker else ""
                    )

                    # Stage rows for this contract (ADR 0011 v4): pure helper
                    # returns either a complete row list OR a miss reason
                    # naming the gap (missing column / static field / per-row
                    # factor). Reference data is all-or-nothing per contract.
                    rows, miss_reason = _stage_contract_rows(
                        basket_df=basket_df,
                        dates_raw=dates_raw or {},
                        generic_ticker=generic_ticker,
                        contract_code=contract_code,
                        basket_cusip_column=basket_cusip_column,
                        basket_factor_column=basket_factor_column,
                        basket_isin_column=basket_isin_column,
                        date_field_to_column=date_field_to_column,
                    )
                    if miss_reason is not None:
                        print(
                            f"      [WARNING] {contract_ticker}: {miss_reason}; "
                            "counting contract as missed."
                        )
                        missed_contracts.append(contract_ticker)
                        continue

                    all_rows.extend(rows)
                    total_contracts_with_basket += 1
                    generic_emitted_any = True

                if generic_emitted_any:
                    generics_with_data += 1

            if not all_rows:
                print(
                    f"  [WARNING] {pb_path.name}: no deliverable rows produced "
                    "across any generic / contract. Nothing uploaded."
                )
                any_failures = True
                continue

            # STRICT all-or-nothing coverage gate (ADR 0011 v3, §4) — closes
            # Codex findings 1 (contract-level) and 2 (generic-level). The
            # evaluator returns an error message on either gap; None on clean
            # coverage. Deliverable baskets are curated reference data, not a
            # broad-discovery universe — partial loads are never legitimate.
            expected_generics = len(universe_items)
            coverage_error = _evaluate_deliverables_coverage(
                generics_with_data=generics_with_data,
                expected_generics=expected_generics,
                total_contracts_probed=total_contracts_probed,
                total_contracts_with_basket=total_contracts_with_basket,
                missed_contracts=missed_contracts,
            )
            if coverage_error:
                print(f"  [ABORT] {coverage_error}")
                any_failures = True
                continue

            print(
                f"  [INFO] Emitted {len(all_rows)} deliverable row(s) across "
                f"{generics_with_data}/{expected_generics} generic(s); "
                f"{total_contracts_with_basket}/{total_contracts_probed} "
                "contract(s) had a basket."
            )

            df_out = pd.DataFrame(all_rows)
            for meta_key, meta_val in lineage_meta.items():
                df_out[meta_key] = meta_val
            df_out["requested_start_date"] = start_date
            df_out["requested_end_date"] = end_date
            df_out["extracted_at"] = extracted_at
            df_out["extraction_mode"] = DELIVERABLES_EXTRACTION_MODE

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            parquet_filename = f"{dataset_name}_deliverables_{timestamp}.parquet"
            local_parquet_path = temp_data_dir / parquet_filename
            df_out.to_parquet(local_parquet_path, engine="pyarrow", index=False)

            blob_name = f"deliverables/{dataset_name}/{parquet_filename}"
            bucket.blob(blob_name).upload_from_filename(str(local_parquet_path))
            print(
                f"  [SUCCESS] Deliverables parquet uploaded to "
                f"gs://{BUCKET_NAME}/{blob_name}"
            )
            local_parquet_path.unlink(missing_ok=True)

    except Exception as exc:
        any_failures = True
        print(f"[FATAL] Deliverables extraction pipeline failed: {exc}")

    finally:
        print("\nCleaning up temporary files...")
        for d in (temp_playbooks_dir, temp_data_dir):
            if d.exists():
                for f in d.glob("*"):
                    if f.is_file():
                        f.unlink()
                try:
                    d.rmdir()
                except OSError:
                    pass

        if any_failures:
            print("\n*** DELIVERABLES EXTRACTION COMPLETE (WITH FAILURES) ***")
            sys.exit(1)
        print("\n*** DELIVERABLES EXTRACTION COMPLETE ***")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Run the incremental Bloomberg extractor for selected playbooks."
    )
    parser.add_argument(
        "--playbook",
        action="append",
        help="Playbook filename or stem to run. Repeat the flag or pass comma-separated values.",
    )
    parser.add_argument(
        "--mode",
        choices=["time-series", "metadata-history", "event-calendar", "deliverables"],
        default="time-series",
        help=(
            "Extraction mode. ``time-series`` (default) is the canonical "
            "bdh/bdp incremental flow that has always shipped — produces "
            "long-format parquet under gs://<bucket>/data/<dataset>/, "
            "consumed by the ingester's market_data_daily path. "
            "``metadata-history`` runs the bds(FUT_CHAIN) + bdp(per-underlying) "
            "flow for playbooks declaring an enabled ``metadata_history:`` "
            "section — produces wide effective-dated parquet under "
            "gs://<bucket>/metadata_history/<dataset>/, consumed by the "
            "ingester's instrument_metadata_history path. "
            "``event-calendar`` runs the macro-event flow for playbooks "
            "declaring an ``event_calendar:`` section (economic releases, "
            "central-bank meetings) — produces wide event parquet under "
            "gs://<bucket>/events/<dataset>/, consumed by the ingester's "
            "event_calendar path. ``deliverables`` runs the per-contract "
            "deliverable-basket flow for playbooks declaring an enabled "
            "``deliverables:`` section (bond_futures.yml) — produces wide "
            "parquet under gs://<bucket>/deliverables/<dataset>/, consumed "
            "by the ingester's futures_deliverables path. See "
            "docs_revamped/05_decisions/0002-playbook-metadata-history-section.md, "
            "0008-event-playbook-contract.md, and 0011-futures-deliverables-substrate.md."
        ),
    )
    args = parser.parse_args()
    selected_playbooks = _parse_selected_playbooks(args.playbook)

    if args.mode == "metadata-history":
        run_metadata_history_extraction(selected_playbooks=selected_playbooks)
    elif args.mode == "event-calendar":
        run_event_calendar_extraction(selected_playbooks=selected_playbooks)
    elif args.mode == "deliverables":
        run_deliverables_extraction(selected_playbooks=selected_playbooks)
    else:
        run_incremental_extraction(selected_playbooks=selected_playbooks)
