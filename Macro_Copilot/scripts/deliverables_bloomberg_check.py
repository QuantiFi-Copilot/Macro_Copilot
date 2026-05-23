"""deliverables_bloomberg_check.py — deliverable-basket Bloomberg verification
==============================================================================

Operator-side Bloomberg verification for work order **C4** (Track C, deliverables
data PR; ADR 0011 = the futures-deliverables substrate).

ADR 0011 fixed the substrate (C3): ``macro_data.futures_deliverables`` with the
natural key ``(instrument_id, contract_code, deliverable_cusip)``, an extractor
``--mode deliverables`` on both ``utils/incremental_extractor.py`` and
``utils/historical_extractor.py``, and an ingester route at
``ingestion/ingest_parquet.py`` (PHASE 6). C4 is the first data PR: write the
``deliverables:`` section on ``rates_agent/playbooks/bond_futures.yml``, scope
to UST in v1, defer non-US per P2/P5.

Nothing in this section is trusted until this script's terminal run confirms
it returns clean data. The candidate playbook section marks every CANDIDATE
mnemonic; this probe empirically validates each via real ``bds`` / ``bdp``
calls and emits a final summary the operator pastes back to the agent for the
playbook to be finalised to ``# VERIFIED <date>``.

What is **already VERIFIED** (this script DOES NOT re-verify, only smoke-
checks they are still present):

  * ``FUT_CHAIN`` + ``INCLUDE_EXPIRED_CONTRACTS="Y"`` — chain enumeration.
    VERIFIED 2026-05-19 via the metadata_history flow on this playbook.
  * ``FUT_DLV_DT_FIRST``  — first delivery date per contract.  Same source.
  * ``FUT_DLV_DT_LAST``   — last delivery date per contract.   Same source.
  * ``FUT_NOTICE_FIRST``  — first notice date per contract.    Same source.
  * ``FUT_DLVRBLE_BNDS_CUSIPS`` — basket field. VERIFIED via FLDS
    (operator manual probe 2026-05-23): BBG field ID FO066,
    "Deliverable Bonds CUSIP", Bulk Data field. Empty on the generic
    (TY1), populated on the explicit contract (TYM6 etc.) — the
    extractor enumerates the chain and probes explicit contracts.

What this script empirically VERIFIES (or fails for) per UST generic:

  1. ``bds(contract, FUT_DLVRBLE_BNDS_CUSIPS)`` returns a non-empty frame
     across deeply historical contracts (does Bloomberg still serve
     baskets for TY contracts from the 1980s? — drives the historical
     extractor's INCLUDE_EXPIRED_CONTRACTS sweep).
  2. **EXACT pandas-DataFrame column names** the xbbg call produces for
     the basket frame. The internal Bloomberg mnemonics behind the two
     columns (per BH406, the "Bulk Header" companion) are
     ``BC_FUT_DLVRBLE_BNDS_CUSIP_YK`` and ``BC_FUT_DELIVERABLE_CF`` — but
     xbbg may rename to display labels. The agent uses the script's
     schema-sample output to finalise ``basket_cusip_column`` /
     ``basket_factor_column`` in the playbook.
  3. **CUSIP string format** as xbbg returns it. The FLDS bulk-data viewer
     shows "91282CGM Govt"-shaped strings — visible 8 chars + " Govt"
     yellow-key suffix, but the FLDS view may truncate the 9th
     check digit; xbbg may return the full 9-char form. The extractor's
     ``_clean_scalar`` strips trailing whitespace; the script's per-
     contract sample-rows block reports the literal string so the agent
     can decide whether the ingester needs additional yellow-key stripping.
  4. Per-contract date coverage: across the three required static fields,
     how often does bdp return all three valid dates for sampled
     contracts? (``FUT_NOTICE_LAST`` was DROPPED from v1 — operator
     manual FLDS probe 2026-05-23 confirmed the mnemonic does not exist
     for UST bond futures; substrate column
     ``futures_deliverables.last_notice_date`` stays nullable, v1 every
     row lands NULL.)

UST scope (v1):

  TU1 Comdty  UST 2Y T-Note futures
  FV1 Comdty  UST 5Y T-Note futures
  TY1 Comdty  UST 10Y T-Note futures
  UXY1 Comdty UST Ultra 10Y T-Note futures
  US1 Comdty  UST Classic Long Bond (30Y) futures
  WN1 Comdty  UST Ultra Bond futures

Sampling strategy per generic: enumerate the full chain via FUT_CHAIN; sample
SAMPLE_PER_GENERIC contracts spread across the chain (most-recent + a few
mid-history + the oldest) so the operator can confirm the basket flow works
both for active contracts and for deep historical backfill.

OUTPUT
------
A timestamped directory in the current working directory containing:

  * ``deliverables_report.txt``       — sectioned text report with the
                                        FINAL SUMMARY block at the bottom.
  * ``deliverables_per_contract.csv`` — one row per sampled contract:
                                        basket-row-count, columns found,
                                        all-four-dates-present y/n.
  * ``deliverables_per_generic.csv``  — one row per generic: chain length,
                                        sampled count, basket-success
                                        rate, date-coverage rate, the
                                        recommended ``basket_cusip_column``
                                        / ``basket_factor_column`` /
                                        ``basket_isin_column`` (the most-
                                        common columns from sampled
                                        contracts), verdict.
  * ``deliverables_basket_schema_samples.txt`` — for the first contract per
                                        generic that returned a non-empty
                                        basket, the full column list +
                                        first 5 rows. This is the artefact
                                        the agent uses to finalise the
                                        column-name fields in the playbook.

DEPLOYMENT
----------
Self-contained — stdlib + pandas + xbbg only. Copy this single file to the
Bloomberg terminal host and run::

    python deliverables_bloomberg_check.py

Paste the FINAL SUMMARY block + ``deliverables_basket_schema_samples.txt``
back to the agent.
"""

from __future__ import annotations

import sys
import traceback
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

try:
    from xbbg import blp
except Exception as exc:  # pragma: no cover - only meaningful on the BBG host
    print(f"[FATAL] could not import xbbg.blp: {exc}")
    raise


# ===========================================================================
# CONFIG — UST v1 scope
# ===========================================================================
GENERICS: List[Dict[str, str]] = [
    {"ticker": "TU1 Comdty",  "label": "UST 2Y T-Note"},
    {"ticker": "FV1 Comdty",  "label": "UST 5Y T-Note"},
    {"ticker": "TY1 Comdty",  "label": "UST 10Y T-Note"},
    {"ticker": "UXY1 Comdty", "label": "UST Ultra 10Y T-Note"},
    {"ticker": "US1 Comdty",  "label": "UST Long Bond"},
    {"ticker": "WN1 Comdty",  "label": "UST Ultra Bond"},
]

CHAIN_FIELD = "FUT_CHAIN"
CHAIN_OVERRIDES = {"INCLUDE_EXPIRED_CONTRACTS": "Y"}

# Basket field. VERIFIED via FLDS (operator manual probe, 2026-05-23): the
# correct Bloomberg mnemonic is FUT_DLVRBLE_BNDS_CUSIPS (with the 'E' in
# DLVRBLE; ID FO066, "Deliverable Bonds CUSIP", Bulk Data field).
# Populated only on the explicit cycle contract (e.g. TYM6 Comdty), empty
# on the front-month-rolling generic (TY1) — by Bloomberg's design.
CANDIDATE_BASKET_FIELDS: List[str] = [
    "FUT_DLVRBLE_BNDS_CUSIPS",
]

# Per-contract static date fields. v1 ships THREE — every one VERIFIED via
# the metadata_history flow on bond_futures.yml (2026-05-19). The script
# re-checks per-contract presence as smoke. FUT_NOTICE_LAST was DROPPED
# from v1 (FLDS confirmed the mnemonic does not exist for UST bond
# futures; only FUT_NOTICE_FIRST has a "first" form — there is no
# symmetric "last").
STATIC_FIELDS: List[Dict[str, str]] = [
    {"column_name": "first_delivery_date", "bloomberg_field": "FUT_DLV_DT_FIRST",
     "status": "VERIFIED via metadata_history (2026-05-19)"},
    {"column_name": "last_delivery_date",  "bloomberg_field": "FUT_DLV_DT_LAST",
     "status": "VERIFIED via metadata_history (2026-05-19)"},
    {"column_name": "first_notice_date",   "bloomberg_field": "FUT_NOTICE_FIRST",
     "status": "VERIFIED via metadata_history (2026-05-19)"},
]

# Sampling: ~8 contracts per generic = bounded Bloomberg load.
SAMPLE_PER_GENERIC = 8

# Verdict thresholds (per-generic).
# basket success rate: a generic PASSES when >=80% of sampled contracts
# returned a non-empty basket. Some very old contracts may legitimately
# have no basket on Bloomberg — that is acceptable IF most do.
BASKET_SUCCESS_PASS_RATIO = 0.80
# All-four-dates rate: a generic PASSES when 100% of sampled contracts
# return non-null values for ALL FOUR configured date fields. The historical
# extractor's strict gate (ADR 0011 v3/v4) requires every probed contract
# to produce a complete basket; a 100% pass rate here is the prerequisite.
DATE_COVERAGE_PASS_RATIO = 1.00


# ===========================================================================
# Logger
# ===========================================================================
class Logger:
    def __init__(self, path: Path):
        self.path = path
        self.lines: List[str] = []

    def log(self, msg: str = "") -> None:
        self.lines.append(msg)
        print(msg)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("\n".join(self.lines), encoding="utf-8")


# ===========================================================================
# Cleaning helpers — narwhals-agnostic (ported from the production extractors).
# ===========================================================================
def clean_scalar(value: Any) -> Any:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
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
        except (ValueError, TypeError):
            pass
    return value


def _coerce_to_pandas(obj: Any) -> Any:
    """Return a pandas DataFrame/Series regardless of which dataframe library
    the installed xbbg used. A pandas object (or None) is returned UNCHANGED
    and narwhals is never imported unless needed."""
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


def normalize_bdp_output(df: Any, fallback_ticker: str) -> Dict[str, Any]:
    """xbbg bdp output → {FIELD: value}. Handles both classic WIDE and the
    newer Narwhals LONG (``ticker | field | value``) shapes."""
    df = _coerce_to_pandas(df)
    if df is None:
        return {}
    if isinstance(df, pd.Series):
        return {str(k).upper(): clean_scalar(v) for k, v in df.items()}
    if isinstance(df, pd.DataFrame):
        if df.empty:
            return {}
        _cols = {str(c).lower(): c for c in df.columns}
        if "field" in _cols and "value" in _cols:
            work = df
            if "ticker" in _cols:
                _tcol = _cols["ticker"]
                _tmatch = df[df[_tcol].astype(str).str.upper()
                             == str(fallback_ticker).upper()]
                if not _tmatch.empty:
                    work = _tmatch
                elif df[_tcol].nunique() > 1:
                    return {}
            return {
                str(f).upper(): clean_scalar(v)
                for f, v in zip(work[_cols["field"]], work[_cols["value"]])
            }
        if fallback_ticker in df.index:
            row = df.loc[fallback_ticker]
            if isinstance(row, pd.DataFrame):
                row = row.iloc[0]
            return {str(k).upper(): clean_scalar(v) for k, v in row.items()}
        if len(df) == 1:
            row = df.iloc[0]
            return {str(k).upper(): clean_scalar(v) for k, v in row.items()}
    return {}


def extract_underlying_tickers_from_chain(
    df: Any, fallback_column: Optional[str] = None,
) -> List[str]:
    """Mirror of the production extractor's chain-frame interpreter — returns
    the list of underlying contract tickers from a ``bds(generic, FUT_CHAIN)``
    response."""
    df = _coerce_to_pandas(df)
    if df is None or not isinstance(df, (pd.DataFrame, pd.Series)) or len(df) == 0:
        return []
    if isinstance(df, pd.Series):
        return [str(x) for x in df.dropna().tolist() if str(x).strip()]
    # DataFrame: find the ticker column.
    if fallback_column and fallback_column in df.columns:
        col = fallback_column
    else:
        # Standard Bloomberg FUT_CHAIN bds returns a frame whose first column
        # is the ticker. Many older xbbg shapes use 'Security Description'.
        candidate_cols = [
            c for c in df.columns
            if str(c).lower() in (
                "security description", "security_description",
                "ticker", "underlying ticker",
            )
        ]
        col = candidate_cols[0] if candidate_cols else df.columns[0]
    out: List[str] = []
    for v in df[col].tolist():
        if v is None:
            continue
        s = str(v).strip()
        if s and not pd.isna(v):
            out.append(s)
    return out


# ===========================================================================
# Safe Bloomberg call wrappers — capture exceptions, never raise out of a loop.
# ===========================================================================
def safe_bds(
    ticker: str, field: str, overrides: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[pd.DataFrame], Optional[str]]:
    try:
        kwargs = dict(overrides or {})
        raw = blp.bds(tickers=ticker, flds=field, **kwargs)
        df = _coerce_to_pandas(raw)
        return df, None
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


def safe_bdp(
    ticker: str, fields: List[str],
) -> Tuple[Dict[str, Any], Optional[str]]:
    try:
        raw = blp.bdp(tickers=ticker, flds=fields)
        norm = normalize_bdp_output(raw, fallback_ticker=ticker)
        return {f.upper(): norm.get(f.upper()) for f in fields}, None
    except Exception as exc:
        return {f.upper(): None for f in fields}, f"{type(exc).__name__}: {exc}"


# ===========================================================================
# Sampling — spread N contracts across the (newest-first) chain.
# ===========================================================================
def sample_contract_indices(chain_len: int, n: int) -> List[int]:
    """Return up to ``n`` indices into a chain (assumed newest-first), spread
    across the chain so the sample includes recent, mid-history, and the
    oldest contract. Indices are deduplicated and sorted."""
    if chain_len <= 0 or n <= 0:
        return []
    if chain_len <= n:
        return list(range(chain_len))
    # 3 most-recent + evenly-spaced middles + the oldest.
    out = {0, 1, 2, chain_len - 1}
    middles_needed = n - len(out)
    if middles_needed > 0:
        for i in range(1, middles_needed + 1):
            out.add(int(chain_len * i / (middles_needed + 1)))
    return sorted(out)


# ===========================================================================
# Per-contract probe.
# ===========================================================================
def probe_contract(
    contract_ticker: str,
    basket_field: str,
    static_bbg_fields: List[str],
) -> Dict[str, Any]:
    """Probe one contract: bds(basket_field), bdp(static_fields). Returns a
    structured row of results."""
    basket_df, basket_err = safe_bds(
        contract_ticker, basket_field, overrides=None,
    )
    basket_present = (
        isinstance(basket_df, pd.DataFrame) and not basket_df.empty
    )
    basket_columns: List[str] = []
    basket_row_count = 0
    sample_rows: List[Dict[str, Any]] = []
    if basket_present:
        basket_columns = [str(c) for c in basket_df.columns]
        basket_row_count = int(len(basket_df))
        for _, r in basket_df.head(3).iterrows():
            sample_rows.append({str(k): clean_scalar(v) for k, v in r.items()})

    bdp_vals, bdp_err = safe_bdp(contract_ticker, static_bbg_fields)
    # Map BBG field → present? (non-null after clean_scalar normalisation).
    static_presence: Dict[str, bool] = {}
    for f in static_bbg_fields:
        v = bdp_vals.get(f.upper())
        static_presence[f] = v is not None and not (
            isinstance(v, str) and not v.strip()
        )

    all_dates_present = all(static_presence.values()) and bdp_err is None

    return {
        "contract_ticker": contract_ticker,
        "basket_field": basket_field,
        "basket_present": basket_present,
        "basket_row_count": basket_row_count,
        "basket_columns": basket_columns,
        "basket_sample_rows": sample_rows,
        "basket_error": basket_err,
        "static_presence": static_presence,
        "all_dates_present": all_dates_present,
        "bdp_values": {f: bdp_vals.get(f.upper()) for f in static_bbg_fields},
        "bdp_error": bdp_err,
    }


# ===========================================================================
# Column-name discovery: pick the most-common basket-frame columns across
# sampled contracts. The operator's playbook uses these (case-insensitive
# match in the extractor, but exact reporting helps the operator confirm).
# ===========================================================================
_LIKELY_CUSIP_COL_NAMES = {"cusip", "cusip number", "deliverable cusip"}
_LIKELY_ISIN_COL_NAMES  = {"isin", "isin number", "deliverable isin"}
_LIKELY_FACTOR_COL_NAMES = {
    "conversion factor", "conv factor", "conversion_factor", "factor",
}


def recommend_column(
    candidate_cols: List[str], likely_names: set,
) -> Optional[str]:
    """Return the first column in ``candidate_cols`` whose lowercased name
    matches one of ``likely_names``; None if no match."""
    for c in candidate_cols:
        if str(c).strip().lower() in likely_names:
            return c
    return None


# ===========================================================================
# Per-generic probe.
# ===========================================================================
def probe_generic(
    logger: Logger,
    cfg: Dict[str, str],
    basket_field: str,
    static_bbg_fields: List[str],
    static_column_names: Dict[str, str],
) -> Dict[str, Any]:
    """Probe one generic: chain → sample → per-contract probe → aggregate."""
    generic_ticker = cfg["ticker"]
    label = cfg["label"]
    logger.log("")
    logger.log("-" * 100)
    logger.log(f"{generic_ticker}  ({label})")
    logger.log("-" * 100)

    # ----- Chain enumeration (re-uses the metadata_history-VERIFIED config).
    chain_df, chain_err = safe_bds(generic_ticker, CHAIN_FIELD, CHAIN_OVERRIDES)
    if chain_err is not None:
        logger.log(f"  [FAIL] chain bds error: {chain_err}")
        return {
            "generic_ticker": generic_ticker, "label": label,
            "chain_length": 0, "sampled_count": 0,
            "basket_success_rate": 0.0, "all_dates_rate": 0.0,
            "verdict": "FAIL", "note": f"chain bds error: {chain_err}",
            "recommended_basket_cusip_column": None,
            "recommended_basket_factor_column": None,
            "recommended_basket_isin_column": None,
            "per_contract": [],
            "schema_sample": None,
        }

    contracts = extract_underlying_tickers_from_chain(chain_df)
    chain_len = len(contracts)
    logger.log(f"  chain length         = {chain_len}")
    if chain_len == 0:
        return {
            "generic_ticker": generic_ticker, "label": label,
            "chain_length": 0, "sampled_count": 0,
            "basket_success_rate": 0.0, "all_dates_rate": 0.0,
            "verdict": "FAIL", "note": "chain returned 0 contracts",
            "recommended_basket_cusip_column": None,
            "recommended_basket_factor_column": None,
            "recommended_basket_isin_column": None,
            "per_contract": [],
            "schema_sample": None,
        }

    # ----- Sample contracts and probe each.
    indices = sample_contract_indices(chain_len, SAMPLE_PER_GENERIC)
    sampled = [contracts[i] for i in indices]
    logger.log(f"  sample size          = {len(sampled)} / {chain_len}")
    logger.log(f"  sampled contracts    = {sampled}")
    logger.log(f"  probing basket_field = {basket_field!r}")
    logger.log(f"  probing static fields= {static_bbg_fields}")

    per_contract: List[Dict[str, Any]] = []
    schema_sample: Optional[Dict[str, Any]] = None
    for c in sampled:
        r = probe_contract(c, basket_field, static_bbg_fields)
        per_contract.append(r)
        logger.log(
            f"    {c:<22} basket={'Y' if r['basket_present'] else 'N'}  "
            f"rows={r['basket_row_count']:>3}  "
            f"all_dates={'Y' if r['all_dates_present'] else 'N'}"
        )
        if r["basket_present"]:
            logger.log(f"        cols={r['basket_columns']}")
        if r["basket_error"]:
            logger.log(f"        basket_err={r['basket_error']}")
        for fld, present in r["static_presence"].items():
            if not present:
                v = r["bdp_values"].get(fld)
                logger.log(
                    f"        [MISSING] {fld}={v!r}"
                )
        if schema_sample is None and r["basket_present"]:
            schema_sample = {
                "contract_ticker": c,
                "columns": r["basket_columns"],
                "sample_rows": r["basket_sample_rows"],
            }

    # ----- Aggregate per-generic.
    basket_hits = sum(1 for r in per_contract if r["basket_present"])
    all_dates_hits = sum(1 for r in per_contract if r["all_dates_present"])
    basket_rate = basket_hits / len(per_contract) if per_contract else 0.0
    dates_rate = all_dates_hits / len(per_contract) if per_contract else 0.0

    # Column recommendation: pull from the first non-empty basket; fall back
    # to the most-common basket frame columns across all successful contracts.
    all_basket_columns: List[str] = []
    for r in per_contract:
        if r["basket_present"]:
            all_basket_columns.extend(r["basket_columns"])
    col_counts: Dict[str, int] = {}
    for c in all_basket_columns:
        col_counts[c] = col_counts.get(c, 0) + 1
    columns_by_freq = sorted(col_counts.keys(), key=lambda k: -col_counts[k])

    recommended_cusip  = recommend_column(columns_by_freq, _LIKELY_CUSIP_COL_NAMES)
    recommended_factor = recommend_column(columns_by_freq, _LIKELY_FACTOR_COL_NAMES)
    recommended_isin   = recommend_column(columns_by_freq, _LIKELY_ISIN_COL_NAMES)

    if basket_rate >= BASKET_SUCCESS_PASS_RATIO and dates_rate >= DATE_COVERAGE_PASS_RATIO:
        verdict = "PASS"
        note = (
            f"basket {basket_hits}/{len(per_contract)}; "
            f"all-4-dates {all_dates_hits}/{len(per_contract)}; "
            "ready to scope into VERIFIED."
        )
    elif basket_hits == 0:
        verdict = "FAIL"
        note = (
            "no contract returned a basket — basket_field may be wrong "
            "for this generic, or this market does not expose a "
            "CUSIP-shaped deliverable list."
        )
    else:
        verdict = "WARN"
        note = (
            f"basket {basket_hits}/{len(per_contract)}; "
            f"all-4-dates {all_dates_hits}/{len(per_contract)}; "
            "partial coverage — review per-contract rows before scoping in."
        )

    logger.log(
        f"  ==> {verdict}  basket_rate={basket_rate:.0%}  "
        f"all_dates_rate={dates_rate:.0%}"
    )
    logger.log(f"      recommended basket_cusip_column = {recommended_cusip!r}")
    logger.log(f"      recommended basket_factor_column= {recommended_factor!r}")
    logger.log(f"      recommended basket_isin_column  = {recommended_isin!r}")
    logger.log(f"      note: {note}")

    return {
        "generic_ticker": generic_ticker,
        "label": label,
        "chain_length": chain_len,
        "sampled_count": len(per_contract),
        "basket_success_rate": round(basket_rate, 4),
        "all_dates_rate": round(dates_rate, 4),
        "verdict": verdict,
        "note": note,
        "recommended_basket_cusip_column": recommended_cusip,
        "recommended_basket_factor_column": recommended_factor,
        "recommended_basket_isin_column": recommended_isin,
        "all_basket_columns_seen": columns_by_freq,
        "per_contract": per_contract,
        "schema_sample": schema_sample,
    }


# ===========================================================================
# Main.
# ===========================================================================
def main() -> None:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path.cwd() / f"deliverables_check_{timestamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    logger = Logger(out_dir / "deliverables_report.txt")
    per_contract_csv = out_dir / "deliverables_per_contract.csv"
    per_generic_csv  = out_dir / "deliverables_per_generic.csv"
    schema_txt       = out_dir / "deliverables_basket_schema_samples.txt"

    logger.log("=" * 100)
    logger.log("DELIVERABLES — BLOOMBERG VERIFICATION (work order C4; ADR 0011)")
    logger.log("=" * 100)
    logger.log(f"Timestamp        : {timestamp}")
    logger.log(f"UST generics     : {len(GENERICS)}  "
               f"({', '.join(g['ticker'] for g in GENERICS)})")
    logger.log(f"Chain field      : {CHAIN_FIELD}  (overrides={CHAIN_OVERRIDES})  "
               "[VERIFIED via metadata_history]")
    logger.log(f"Basket candidates: {CANDIDATE_BASKET_FIELDS}")
    logger.log(f"Static fields    :")
    for sf in STATIC_FIELDS:
        logger.log(
            f"    {sf['column_name']:<22} -> {sf['bloomberg_field']:<24}  "
            f"[{sf['status']}]"
        )
    logger.log(f"Sample/generic   : {SAMPLE_PER_GENERIC}")
    logger.log(f"Pass thresholds  : basket>={BASKET_SUCCESS_PASS_RATIO:.0%}  "
               f"all-4-dates>={DATE_COVERAGE_PASS_RATIO:.0%}")
    logger.log(f"Output directory : {out_dir}")
    logger.log("")

    # Preflight — confirm xbbg is talking to Bloomberg at all.
    logger.log("PREFLIGHT — Bloomberg connectivity:")
    pf_vals, pf_err = safe_bdp("TY1 Comdty", ["NAME"])
    if pf_err or not pf_vals.get("NAME"):
        logger.log(f"  WARNING — preflight bdp returned nothing (error: {pf_err}).")
    else:
        logger.log(f"  OK — TY1 Comdty -> NAME={pf_vals.get('NAME')}")
    logger.log("")

    static_bbg_fields = [sf["bloomberg_field"] for sf in STATIC_FIELDS]
    static_column_names = {sf["bloomberg_field"]: sf["column_name"] for sf in STATIC_FIELDS}

    # Use the primary candidate basket field. If the operator later wants to
    # try the alternates, edit CANDIDATE_BASKET_FIELDS at the top of this
    # file and re-run.
    basket_field = CANDIDATE_BASKET_FIELDS[0]
    logger.log("#" * 100)
    logger.log(f"PER-GENERIC PROBE  (basket_field={basket_field!r})")
    logger.log("#" * 100)

    per_generic_rows: List[Dict[str, Any]] = []
    per_contract_rows: List[Dict[str, Any]] = []
    schema_chunks: List[str] = []

    for cfg in GENERICS:
        gen_result = probe_generic(
            logger, cfg, basket_field, static_bbg_fields, static_column_names,
        )
        per_generic_rows.append({
            k: v for k, v in gen_result.items()
            if k not in ("per_contract", "schema_sample", "all_basket_columns_seen")
        })
        # Per-contract: flatten the static-presence dict into one CSV row.
        for r in gen_result["per_contract"]:
            row = {
                "generic_ticker": cfg["ticker"],
                "label": cfg["label"],
                "contract_ticker": r["contract_ticker"],
                "basket_field": r["basket_field"],
                "basket_present": r["basket_present"],
                "basket_row_count": r["basket_row_count"],
                "basket_columns": ", ".join(r["basket_columns"]),
                "basket_error": r["basket_error"],
                "all_dates_present": r["all_dates_present"],
                "bdp_error": r["bdp_error"],
            }
            for f in static_bbg_fields:
                row[f"present_{f}"] = bool(r["static_presence"].get(f))
                row[f"value_{f}"] = r["bdp_values"].get(f)
            per_contract_rows.append(row)

        # Schema-sample chunk for the agent.
        if gen_result["schema_sample"]:
            ss = gen_result["schema_sample"]
            schema_chunks.append("=" * 100)
            schema_chunks.append(
                f"{cfg['ticker']}  ({cfg['label']})  via "
                f"{ss['contract_ticker']} (basket_field={basket_field!r})"
            )
            schema_chunks.append("=" * 100)
            schema_chunks.append(f"columns ({len(ss['columns'])}):")
            for c in ss["columns"]:
                schema_chunks.append(f"  - {c!r}")
            schema_chunks.append("")
            schema_chunks.append("first 3 rows (clean_scalar-normalised):")
            for i, row in enumerate(ss["sample_rows"]):
                schema_chunks.append(f"  row[{i}]:")
                for k, v in row.items():
                    schema_chunks.append(f"    {k!r:<32} = {v!r}")
            schema_chunks.append("")
        else:
            schema_chunks.append("=" * 100)
            schema_chunks.append(
                f"{cfg['ticker']}  ({cfg['label']}): NO BASKET RETURNED "
                "on any sampled contract — schema sample unavailable."
            )
            schema_chunks.append("=" * 100)
            schema_chunks.append("")

    # ------------------------------------------------------------------ CSVs
    try:
        pd.DataFrame(per_contract_rows).to_csv(per_contract_csv, index=False)
    except Exception as exc:  # pragma: no cover
        logger.log(f"[WARNING] failed to write per-contract CSV ({per_contract_csv}): {exc}")
    try:
        pd.DataFrame(per_generic_rows).to_csv(per_generic_csv, index=False)
    except Exception as exc:  # pragma: no cover
        logger.log(f"[WARNING] failed to write per-generic CSV ({per_generic_csv}): {exc}")
    try:
        schema_txt.write_text("\n".join(schema_chunks), encoding="utf-8")
    except Exception as exc:  # pragma: no cover
        logger.log(f"[WARNING] failed to write schema-sample text ({schema_txt}): {exc}")

    # --------------------------------------------------------------- summary
    logger.log("")
    logger.log("=" * 100)
    logger.log("FINAL SUMMARY  (paste this block back to the agent)")
    logger.log("=" * 100)

    pass_n = sum(1 for r in per_generic_rows if r["verdict"] == "PASS")
    warn_n = sum(1 for r in per_generic_rows if r["verdict"] == "WARN")
    fail_n = sum(1 for r in per_generic_rows if r["verdict"] == "FAIL")
    logger.log(f"Generics probed: {len(per_generic_rows)} | PASS: {pass_n} | "
               f"WARN: {warn_n} | FAIL: {fail_n}")
    logger.log("")
    logger.log("Per-generic verdict + recommended column names:")
    for r in per_generic_rows:
        logger.log(
            f"  {r['generic_ticker']:<12} ({r['label']:<26})  -> "
            f"{r['verdict']:<4}  "
            f"basket_rate={r['basket_success_rate']:.0%} | "
            f"all_4_dates_rate={r['all_dates_rate']:.0%} | "
            f"chain_len={r['chain_length']}"
        )
        logger.log(
            f"        basket_cusip_column  = {r['recommended_basket_cusip_column']!r}"
        )
        logger.log(
            f"        basket_factor_column = {r['recommended_basket_factor_column']!r}"
        )
        logger.log(
            f"        basket_isin_column   = {r['recommended_basket_isin_column']!r}"
        )
        logger.log(f"        note: {r['note']}")
    logger.log("")
    logger.log("NEXT: the agent reads the per-generic CSV + the basket schema")
    logger.log("samples and finalises the deliverables: section on")
    logger.log("rates_agent/playbooks/bond_futures.yml — VERIFIED for every")
    logger.log("PASS generic, DEFERRED for any WARN/FAIL generic per the")
    logger.log("documented-deferral discipline (P2/P5).")

    logger.log("")
    logger.log(f"Text report                : {logger.path}")
    logger.log(f"Per-contract CSV           : {per_contract_csv}")
    logger.log(f"Per-generic CSV            : {per_generic_csv}")
    logger.log(f"Basket schema samples (TXT): {schema_txt}")
    logger.save()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        crash_dir = Path.cwd() / (
            f"deliverables_check_FATAL_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        )
        crash_dir.mkdir(parents=True, exist_ok=True)
        (crash_dir / "fatal_traceback.txt").write_text(
            "Fatal error while running the deliverables verification.\n\n"
            + traceback.format_exc(),
            encoding="utf-8",
        )
        raise
