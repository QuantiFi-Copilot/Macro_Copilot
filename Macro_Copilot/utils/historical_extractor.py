import os
import sys
import yaml
import argparse
import hashlib
import subprocess
import pandas as pd
from datetime import datetime, timedelta, date
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
from google.cloud import storage
from xbbg import blp

# ============================================================================
# SELF-CONTAINED EXTRACTOR
#
# This script is designed to run as a single file on the Bloomberg terminal
# host. To preserve that operational property, this module deliberately
# does NOT import anything from sibling project packages (no `from ingestion
# import ...`, no `from database import ...`, no `from shared import ...`).
# The only project-internal dependency is the playbook YAML files pulled at
# runtime from GCS.
#
# The pure-Python helpers ``_HISTORY_TYPED_COLUMNS`` and
# ``_validate_no_overlaps`` defined further down in this file are inlined
# copies of the canonical implementation in
# ``Macro_Copilot/ingestion/metadata_history.py`` (which the database-side
# ingester uses). Both copies MUST stay byte-identical in behaviour
# because they enforce the same contract — the ``EXCLUDE USING GIST``
# constraint on ``macro_data.instrument_metadata_history`` (ADR 0001).
# The DB constraint is the ultimate enforcement; this in-extractor copy
# is for human-readable pre-write failure reporting on the Bloomberg
# host before any parquet is uploaded.
#
# If you ever modify these helpers here, modify them identically in
# ``ingestion/metadata_history.py`` AND in
# ``utils/incremental_extractor.py`` (which carries the same inlined
# copies). The contract is documented in ADR 0002.
#
# NO OTR RESOLVER HERE — BY DESIGN.
# The on-the-run resolver (ADR 0007) is carried ONLY by
# ``utils/incremental_extractor.py``, never this historical extractor.
# Resolution is ``bdp(<generic>, ID_ISIN)`` — it returns *today's* OTR bond.
# Running it during a historical backfill would stamp today's mapping onto
# backfilled dates and corrupt ``macro_data.otr_history``. Resolution is
# intrinsically a "what is true now" operation and belongs only on the
# incremental (forward) path.
# ============================================================================

# --- CONFIGURATION ---
BUCKET_NAME = os.getenv("GCP_BUCKET_NAME", "macro-storage-bucket")
GCP_KEY_FILENAME = os.getenv("GCP_KEY_FILENAME", "library-extractor-key.json")
DEFAULT_VENDOR = os.getenv("DATA_VENDOR", "BLOOMBERG")
DEFAULT_LOOKBACK_DAYS = int(os.getenv("DEFAULT_LOOKBACK_DAYS", "30"))
MAX_HISTORICAL_FIELDS_PER_REQUEST = int(os.getenv("MAX_HISTORICAL_FIELDS_PER_REQUEST", "25"))
MAX_REFERENCE_FIELDS_PER_REQUEST = int(os.getenv("MAX_REFERENCE_FIELDS_PER_REQUEST", "50"))
# Maximum number of underlying-contract tickers per batched ``bdp()`` call in
# the metadata-history flow. Bloomberg/xbbg comfortably handles 50-100 tickers
# per call; we default to 50 as a safe value. Higher values reduce call count
# further but risk per-call timeout on slow Bloomberg sessions. Tunable via
# the ``MAX_REFERENCE_TICKERS_PER_REQUEST`` env var.
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

    start_date = extraction_cfg.get("start_date") or playbook.get("start_date")
    if start_date:
        start_date = _safe_iso_date(start_date)
    else:
        lookback_days = extraction_cfg.get("lookback_days")
        if lookback_days is None:
            lookback_days = playbook.get("lookback_days", DEFAULT_LOOKBACK_DAYS)
        start_date = (pd.to_datetime(end_date) - timedelta(days=int(lookback_days))).strftime("%Y-%m-%d")

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
# isolated so the unit tests in tests/state/test_metadata_history_extraction.py
# can exercise them without a live BBG / GCS session.
# ============================================================================


def _extract_underlying_tickers_from_chain(
    chain_df: Any,
    fallback_column: Optional[str] = None,
) -> List[str]:
    """
    Convert ``xbbg.blp.bds(...)`` output for a chain-enumeration field
    (typically ``FUT_CHAIN``) into a flat list of underlying-contract ticker
    strings.

    xbbg's column naming differs across Bloomberg vintages (``Security
    Description`` / ``security_description`` / ``Security_Description``).
    We probe canonical names in order, then fall back to the first
    string-valued column. If no column qualifies we return ``[]`` and let
    the caller refuse to write that playbook (coverage gate).

    ``fallback_column`` is the playbook's escape hatch — when an operator
    learns in A2 that a specific vintage uses a different column name,
    they pass it via ``metadata_history.chain_overrides.column_name``
    rather than waiting for a code patch.
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
            tickers = [str(v).strip() for v in chain_df[col].dropna().tolist() if str(v).strip()]
            if tickers:
                return tickers

    # Last-resort fall-back: first string-valued column.
    for col in chain_df.columns:
        if chain_df[col].dtype == object:
            tickers = [str(v).strip() for v in chain_df[col].dropna().tolist() if str(v).strip()]
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
    returned list contains one window-record per contract, augmented with
    ``effective_from`` and ``effective_to`` keys.

    Inputs are sorted in-place by ``roll_field_column`` ascending. Contracts
    missing either the roll or first-trade field are dropped — the caller
    sees a smaller output list and can decide whether to count this against
    the coverage gate. NULL ``effective_to`` is signalled with Python ``None``
    so the downstream parquet writes NULL rather than a sentinel date.

    Returns ``[]`` if fewer than one valid contract remains.
    """
    if not contracts:
        return []

    # Filter to contracts that carry both date anchors.
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


# Typed-column set on ``macro_data.instrument_metadata_history`` (per
# database/schema.sql section 4). Inlined here for the self-contained
# extractor constraint. MUST stay byte-identical to
# ``HISTORY_TYPED_COLUMNS`` in ``ingestion/metadata_history.py`` and
# ``utils/incremental_extractor.py``.
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


def _validate_no_overlaps(
    rows_by_vendor_ticker: Dict[str, List[Dict[str, Any]]],
) -> List[str]:
    """
    Defence-in-depth overlap detector for the metadata-history flow.

    For each generic ticker, walks rows sorted by ``effective_from`` and
    reports conflicts where consecutive windows overlap or share a
    boundary day. Returns a list of human-readable conflict descriptions;
    an empty list means the input is clean and may be written.

    Rules (matching the ``EXCLUDE USING GIST`` constraint on
    ``instrument_metadata_history`` shipped in ADR 0001):

      * Every row must carry ``effective_from``.
      * If a row has ``effective_to``, it must be ``>= effective_from``.
      * ``effective_to=None`` is only valid on the chronologically-last
        row per ticker (the currently-effective window).
      * Consecutive rows must not overlap or share a boundary day —
        matching the DB constraint's '[]' inclusive bounds.

    Sync invariant: this function MUST stay byte-identical in behaviour
    to ``validate_no_overlaps`` in
    ``Macro_Copilot/ingestion/metadata_history.py`` and the inlined
    copy in ``Macro_Copilot/utils/incremental_extractor.py``. The DB
    ``EXCLUDE`` constraint is the ultimate enforcement; this in-extractor
    copy is for human-readable failure reporting BEFORE parquet upload.
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
                # Already reported on the next iteration.
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
    Return the playbook's ``metadata_history`` section if it is present and
    declares ``enabled: true``; otherwise return None (the playbook opts
    out of metadata-history mode entirely).

    Centralised here so the dispatcher in ``run_metadata_history_extraction``
    and the unit tests use the same parse/validation logic.
    """
    section = playbook.get("metadata_history")
    if not isinstance(section, dict):
        return None
    if not section.get("enabled"):
        return None

    # Minimal shape validation. Stricter validation (every static_field has
    # both column_name + bloomberg_field, etc.) lands when the playbook
    # validator (open question Q26) is built — out of scope for A1.
    if not isinstance(section.get("static_fields"), list) or not section["static_fields"]:
        return None
    if not section.get("chain_field"):
        return None
    roll = section.get("roll_convention") or {}
    if not isinstance(roll, dict) or not roll.get("roll_field") or not roll.get("first_trade_field"):
        return None
    if roll.get("type", "expiry_roll") != "expiry_roll":
        # Future roll-convention types will be added via ADR amendment;
        # for A1 we ship one. Refuse loudly per P11/P6.
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
    Thin wrapper around ``xbbg.blp.bds`` that returns the list of underlying
    contracts in a chain for a given generic ticker.

    Isolated from the rest of the orchestration so the bds dependency is
    mockable in unit tests via ``patch('utils.historical_extractor.blp.bds')``.
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
    applies (xbbg → flat dict, dates ISO-stringified, NaN → None).

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

    xbbg returns a DataFrame indexed by ticker with columns = Bloomberg
    fields. Each row is one ticker's static-field values. Tickers that
    Bloomberg could not resolve at all are missing from the index, not
    represented as a row of NaNs; callers detect "no data for ticker X"
    by the absence of X in the returned mapping.

    Field names in the output are upper-cased to match the convention
    that :func:`_normalize_bdp_output` uses for the single-ticker path,
    so downstream code (the field_to_column lookup table) works
    identically regardless of which fetch path produced the values.
    """
    out: Dict[str, Dict[str, Any]] = {}
    if df is None:
        return out
    df = _coerce_to_pandas(df)
    if not isinstance(df, pd.DataFrame):
        # Single-row Series — rare but possible when xbbg flattens a
        # one-ticker batch. Defer to the single-ticker normaliser using
        # the first requested ticker as the fallback key.
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

    # The canonical multi-ticker WIDE shape: DataFrame indexed by ticker.
    # UNCHANGED from the pre-narwhals implementation.
    for ticker, row in df.iterrows():
        if isinstance(row, pd.DataFrame):
            # Multi-row index (rare) — take the first row defensively.
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
    are simply absent from the output — same contract as the single-call
    fetcher (caller's downstream code already handles "no data for this
    contract" by skipping it).

    Performance properties (versus calling the single-ticker helper N times):
      * Dedups the input list internally — passing the same ticker
        multiple times across overlapping chains (SFR1/SFR2/.../SFR8
        share underlyings) costs ONE bdp call, not N.
      * Batches up to ``ticker_chunk_size`` tickers per bdp call. For
        ~1,500 distinct underlyings on a first full backfill, this
        reduces the bdp call count from ~1,500 to ~30 (50× fewer).
      * Chunks fields too via ``MAX_REFERENCE_FIELDS_PER_REQUEST`` — in
        practice the typical metadata_history static-field list is well
        under that limit so this is a single-pass per ticker chunk.

    Fault tolerance:
      * If a batched chunk raises (terminal hiccup, network blip, rate
        limit on a big chunk), the entire chunk falls back to the
        single-ticker helper one ticker at a time. A one-bad-contract
        problem does not lose data for the other 49 tickers in the chunk.

    Semantics:
      * Same Bloomberg fields are requested with the same overrides; the
        VALUES returned are identical to what the single-ticker path
        would return for the same ticker. Only the request packaging
        differs.
    """
    if not underlying_tickers or not bloomberg_fields:
        return {}

    # Dedup while preserving the order of first appearance — purely for
    # deterministic logging, no semantic effect.
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
        # Per-ticker-chunk accumulator so each field-chunk's results add
        # to the same per-ticker dict.
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
            # Fallback: serial single-ticker calls preserve the
            # no-data-loss contract — same call shape that worked in
            # pre-batch code paths.
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
    table (``macro_data.instrument_metadata_history``).

    Distinct from :func:`run_autonomous_extraction` (time-series) in three
    fundamental ways:

      * **Query pattern:** ``bds(generic, FUT_CHAIN, …)`` to enumerate the
        underlying-contract chain per rolling generic, then ``bdp(contract,
        static_fields)`` per chain member. Time-series extraction uses
        ``bdh()`` instead.
      * **Output shape:** wide effective-dated rows — one per
        ``(generic_ticker, effective_window)`` — not long-format time
        series.
      * **Destination:** parquet uploads to
        ``gs://<bucket>/metadata_history/<dataset>/`` which the ingester
        routes to ``instrument_metadata_history`` via the Step-0 helpers.

    Mirrors :func:`run_autonomous_extraction`'s GCP auth, playbook-pull,
    lineage-stamp, and parquet-upload structure so the operational story
    stays the same; only the per-ticker work differs. Skips any playbook
    that does NOT declare an enabled ``metadata_history`` section, so it
    is safe to run with a wide ``--playbook`` filter that includes
    playbooks like ``sovereign_bonds`` that have no rolling-contract
    universe.

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

            # ============================================================
            # PASS 1 — enumerate the underlying-contract chain for each
            # rolling generic in the playbook universe via bds(). One
            # bds() call per generic. The chains are cached locally so
            # the per-generic loop in PASS 3 reads from memory.
            # ============================================================
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

            # ============================================================
            # PASS 2 — collect distinct underlying tickers across every
            # generic, then issue one batched bdp() per chunk of 50
            # tickers. For overlapping chains (typical: policy_futures
            # SFR1-8 share the same underlying SOFR contracts), this
            # collapses 8x duplicate bdp() calls into 1x; for non-
            # overlapping chains (bond_futures, where each generic is
            # its own family) it still collapses ~50 single-ticker
            # calls into 1 batched call. Same Bloomberg fields, same
            # values, far fewer round-trips.
            # ============================================================
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

            # ============================================================
            # PASS 3 — per generic, look up each chain member's static
            # data from the PASS 2 cache and run the expiry_roll window
            # math. No Bloomberg calls happen here; all the data is in
            # memory already.
            # ============================================================
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
                        # Always preserve raw BBG-keyed values so the roll/first-trade
                        # lookups below can find them whether or not they were declared
                        # as static_fields (they often are not, since they're
                        # window-computation inputs, not output columns).
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

                # Strip the BBG-keyed scratch columns from the parquet payload;
                # downstream consumers see only the playbook-declared column_names
                # plus the window dates plus lineage stamps.
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

            # Coverage gate: refuse to upload if fewer than 90% of rolling tickers
            # produced any windows. Mirrors the time-series 90% gate per ADR 0002.
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

            # Pre-write overlap gate (defence-in-depth alongside the DB's
            # EXCLUDE constraint and the ingester's identical gate).
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
                print(
                    f"  [WARNING] No rows assembled for {dataset_name}. Skipping upload."
                )
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
            parquet_filename = (
                f"{dataset_name}_metadata_history_{timestamp}.parquet"
            )
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


def run_autonomous_extraction(selected_playbooks: Optional[Set[str]] = None):
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
                file_name = pb_path.name
                file_stem = pb_path.stem
                if file_name in selected_playbooks or file_stem in selected_playbooks:
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
            # and carry no time-series ``universe``. The event extractor is
            # incremental-only — the historical extractor never processes them;
            # skip cleanly so they are not mis-reported as failures.
            if playbook.get("event_calendar") is not None:
                print(
                    f"\n[SKIP] {pb_path.name}: event_calendar playbook — handled "
                    "by the incremental extractor's --mode event-calendar."
                )
                continue

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
                    long_df["extraction_mode"] = "historical"

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

            latest_blob_name = "scripts/terminal_extractor.py"
            archive_blob_name = f"scripts/archive/terminal_extractor_{timestamp}.py"

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
    parser = argparse.ArgumentParser(
        description="Run the historical Bloomberg extractor for selected playbooks."
    )
    parser.add_argument(
        "--playbook",
        action="append",
        help="Playbook filename or stem to run. Repeat the flag or pass comma-separated values.",
    )
    parser.add_argument(
        "--mode",
        choices=["time-series", "metadata-history"],
        default="time-series",
        help=(
            "Extraction mode. ``time-series`` (default) is the canonical "
            "bdh/bdp flow that has always shipped — produces long-format "
            "parquet under gs://<bucket>/data/<dataset>/, consumed by the "
            "ingester's market_data_daily path. ``metadata-history`` runs "
            "the bds(FUT_CHAIN) + bdp(per-underlying) flow for playbooks "
            "declaring an enabled ``metadata_history:`` section — produces "
            "wide effective-dated parquet under "
            "gs://<bucket>/metadata_history/<dataset>/, consumed by the "
            "ingester's instrument_metadata_history path. See "
            "docs_revamped/05_decisions/0002-playbook-metadata-history-section.md."
        ),
    )
    args = parser.parse_args()
    selected_playbooks = _parse_selected_playbooks(args.playbook)

    if args.mode == "metadata-history":
        run_metadata_history_extraction(selected_playbooks=selected_playbooks)
    else:
        run_autonomous_extraction(selected_playbooks=selected_playbooks)
