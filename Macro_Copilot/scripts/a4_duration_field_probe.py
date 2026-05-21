"""
a4_duration_field_probe.py
==========================

Operator-side Bloomberg verification — work order A4.  MACHINE-ROBUST,
SELF-RESOLVING version.

WHY THIS VERSION EXISTS
-----------------------
An earlier version of this probe hardcoded `/cusip/<cusip>` query strings
copied from a previous run on a different Bloomberg PC. On another terminal
that form did not resolve, so every query returned nothing and the report
falsely read "no duration field". This version removes that brittleness: it
starts from the plain `GT.. Govt` generic tickers (which resolve on ANY live
Bloomberg terminal), resolves each to its underlying cash bond, and TRIES
SEVERAL security-identifier forms (`/isin/…`, `/cusip/…`, `<isin> Govt`, …)
until one resolves on THIS terminal. It then reports which form worked — that
is exactly the ticker form A4-4's `sovereign_cash_bonds.yml` must use.

WHAT THIS RUN ANSWERS (one run, complete picture)
-------------------------------------------------
  1. THE OPEN QUESTION — does ANY Bloomberg mnemonic historise modified /
     adjusted duration via `bdh` on a cash bond? Eight candidates are probed.
  2. RECONFIRMS, on this machine, the five A4-4 `target_metrics` an earlier
     run verified: YLD_YTM_MID, RISK_MID (DV01), PX_DIRTY_MID, PX_CLEAN_MID,
     ASSET_SWAP_SPD_MID.
  3. REPORTS the bond-identifier form that resolves on this terminal.

SAFETY
------
RISK_MID is the positive control — it is proven to historise via `bdh`. A
PREFLIGHT check (a plain-ticker query) aborts the run loudly if the terminal
is not live, and a control check invalidates the report if RISK_MID comes back
empty — so the script can never again emit a misleading all-zero verdict.

DEPLOYMENT
----------
Self-contained — stdlib + pandas + xbbg. Run on the Bloomberg PC:

    python a4_duration_field_probe.py

Outputs land in ./a4_duration_probe_<TIMESTAMP>/ — a text report + two CSVs.
Return all of them; the FINAL SUMMARY block is the pasteable digest.
"""

from __future__ import annotations

import traceback
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

try:
    from xbbg import blp
except ImportError as exc:  # pragma: no cover — operator-machine dependency
    raise ImportError(
        "xbbg is not installed in this environment. Run this script on a "
        "Bloomberg-equipped machine with xbbg + blpapi available. "
        f"Underlying: {exc}"
    ) from exc


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
LOOKBACK_YEARS = 3

# A plain ticker used only for the connectivity preflight — it resolves on any
# live, logged-in Bloomberg terminal.
CONNECTIVITY_TICKER = "GT10 Govt"

# The GT-generic benchmark universe — plain tickers, resolve on any terminal.
# Each is resolved to its current underlying cash bond at run time.
GENERIC_UNIVERSE: List[Dict[str, str]] = [
    {"ticker": "GT2 Govt",      "curve_family": "UST",         "country": "US",        "tenor": "2Y"},
    {"ticker": "GT10 Govt",     "curve_family": "UST",         "country": "US",        "tenor": "10Y"},
    {"ticker": "GT30 Govt",     "curve_family": "UST",         "country": "US",        "tenor": "30Y"},
    {"ticker": "GTDEM2Y Govt",  "curve_family": "DE_BUND",     "country": "Germany",   "tenor": "2Y"},
    {"ticker": "GTDEM10Y Govt", "curve_family": "DE_BUND",     "country": "Germany",   "tenor": "10Y"},
    {"ticker": "GTGBP2Y Govt",  "curve_family": "UK_GILT",     "country": "UK",        "tenor": "2Y"},
    {"ticker": "GTGBP10Y Govt", "curve_family": "UK_GILT",     "country": "UK",        "tenor": "10Y"},
    {"ticker": "GTJPY2Y Govt",  "curve_family": "JGB",         "country": "Japan",     "tenor": "2Y"},
    {"ticker": "GTJPY10Y Govt", "curve_family": "JGB",         "country": "Japan",     "tenor": "10Y"},
    {"ticker": "GTFRF10Y Govt", "curve_family": "FR_OAT",      "country": "France",    "tenor": "10Y"},
    {"ticker": "GTITL10Y Govt", "curve_family": "IT_BTP",      "country": "Italy",     "tenor": "10Y"},
    {"ticker": "GTCAD10Y Govt", "curve_family": "CANADA_GOVT", "country": "Canada",    "tenor": "10Y"},
    {"ticker": "GTAUD10Y Govt", "curve_family": "AU_GOVT",     "country": "Australia", "tenor": "10Y"},
    {"ticker": "GTESP10Y Govt", "curve_family": "ES_BONO",     "country": "Spain",     "tenor": "10Y"},
]

# Reference fields read off the generic to identify its underlying cash bond.
UNDERLYING_ID_FIELDS: List[str] = [
    "ID_ISIN", "ID_CUSIP", "SECURITY_DES", "NAME", "CPN", "MATURITY", "ISSUE_DT",
]

# Fields probed on each resolved real bond. Each row is {field, purpose}.
# RISK_MID is both an A4-4 field and the positive control.
PROBE_FIELDS: List[Dict[str, str]] = [
    {"field": "YLD_YTM_MID",        "purpose": "A4-4 confirm"},
    {"field": "RISK_MID",           "purpose": "A4-4 confirm + CONTROL"},
    {"field": "PX_DIRTY_MID",       "purpose": "A4-4 confirm"},
    {"field": "PX_CLEAN_MID",       "purpose": "A4-4 confirm"},
    {"field": "ASSET_SWAP_SPD_MID", "purpose": "A4-4 confirm"},
    {"field": "DUR_ADJ_MID",        "purpose": "duration candidate"},
    {"field": "DUR_ADJ_BID",        "purpose": "duration candidate"},
    {"field": "DUR_ADJ_ASK",        "purpose": "duration candidate"},
    {"field": "DUR_MID",            "purpose": "duration candidate"},
    {"field": "MOD_DUR",            "purpose": "duration candidate"},
    {"field": "MODIFIED_DURATION",  "purpose": "duration candidate"},
    {"field": "MAC_DUR_MID",        "purpose": "duration candidate"},
    {"field": "DUR_ADJ_MTY_MID",    "purpose": "duration candidate"},
]

CONTROL_FIELD = "RISK_MID"
DURATION_FIELDS = [p["field"] for p in PROBE_FIELDS if p["purpose"] == "duration candidate"]


# ---------------------------------------------------------------------------
# Logger.
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Cleaning + normalisation helpers (verbatim from the sibling A4 scripts).
# ---------------------------------------------------------------------------
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
    xbbg used.

    Older xbbg returns classic pandas. Newer xbbg returns a Narwhals
    DataFrame wrapping pyarrow/polars. A Narwhals frame and a pyarrow Table
    both expose ``.to_pandas()``; failing that, ``narwhals.to_native()``
    unwraps to the underlying frame. On an old-xbbg machine the input is
    already pandas and this returns immediately — narwhals is never imported.
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


def normalize_bdp_output(df: Any, fallback_ticker: str) -> Dict[str, Any]:
    df = _coerce_to_pandas(df)
    if df is None:
        return {}
    if isinstance(df, pd.Series):
        return {str(k).upper(): clean_scalar(v) for k, v in df.items()}
    if not isinstance(df, pd.DataFrame) or df.empty:
        return {}
    cols = {str(c).lower(): c for c in df.columns}
    # Long / tidy shape (newer xbbg): one row per (ticker, field); columns
    # include 'field' and 'value'.
    if "field" in cols and "value" in cols:
        work = df
        if "ticker" in cols:
            tmatch = df[df[cols["ticker"]].astype(str).str.upper()
                        == str(fallback_ticker).upper()]
            if not tmatch.empty:
                work = tmatch
        return {
            str(f).upper(): clean_scalar(v)
            for f, v in zip(work[cols["field"]], work[cols["value"]])
        }
    # Wide shape (older xbbg): ticker index, Bloomberg fields as columns.
    if fallback_ticker in df.index:
        row = df.loc[fallback_ticker]
        if isinstance(row, pd.DataFrame):
            row = row.iloc[0]
        return {str(k).upper(): clean_scalar(v) for k, v in row.items()}
    if len(df) == 1:
        row = df.iloc[0]
        return {str(k).upper(): clean_scalar(v) for k, v in row.items()}
    return {}


def normalize_bdh_output(df: Any, requested_field: str) -> pd.DataFrame:
    empty = pd.DataFrame(columns=["date", "value"])
    df = _coerce_to_pandas(df)
    if df is None:
        return empty
    if isinstance(df, pd.Series):
        out = df.to_frame(name="value").reset_index()
        out.columns = ["date", "value"]
        return out
    if not isinstance(df, pd.DataFrame) or df.empty:
        return empty
    cols = {str(c).lower(): c for c in df.columns}
    # Long / tidy shape (newer xbbg): a 'value' column + a date column,
    # optionally a 'field' column to filter the requested field on.
    if "value" in cols and ("date" in cols or "index" in cols):
        date_col = cols.get("date") or cols.get("index")
        work = df
        if "field" in cols:
            fmatch = df[df[cols["field"]].astype(str).str.upper()
                        == requested_field.upper()]
            if not fmatch.empty:
                work = fmatch
        return work[[date_col, cols["value"]]].rename(
            columns={date_col: "date", cols["value"]: "value"}
        ).reset_index(drop=True)
    # Wide shape (older xbbg): date index, field (or ticker/field) columns.
    out = df.copy().reset_index()
    if len(out.columns) == 2:
        out.columns = ["date", "value"]
        return out
    value_col = None
    for col in out.columns:
        if str(col).upper() == requested_field.upper():
            value_col = col
            break
        if isinstance(col, tuple) and len(col) >= 2 and str(col[-1]).upper() == requested_field.upper():
            value_col = col
            break
    if value_col is None:
        value_col = out.columns[-1]
    return out[[out.columns[0], value_col]].rename(
        columns={out.columns[0]: "date", value_col: "value"}
    )


def safe_bdh_single(
    ticker: str, field: str, start: str, end: str
) -> Tuple[pd.DataFrame, Optional[str]]:
    try:
        raw = blp.bdh(tickers=ticker, flds=[field], start_date=start, end_date=end)
        return normalize_bdh_output(raw, requested_field=field), None
    except Exception as exc:
        return pd.DataFrame(columns=["date", "value"]), f"{type(exc).__name__}: {exc}"


def safe_bdp_batch(
    ticker: str, fields: List[str]
) -> Tuple[Dict[str, Any], Optional[str]]:
    try:
        raw = blp.bdp(tickers=ticker, flds=fields)
        norm = normalize_bdp_output(raw, fallback_ticker=ticker)
        return {f.upper(): norm.get(f.upper()) for f in fields}, None
    except Exception as exc:
        return {f.upper(): None for f in fields}, f"{type(exc).__name__}: {exc}"


def history_points(df: pd.DataFrame) -> Tuple[int, Any]:
    """Non-null numeric point count + last value of a bdh frame."""
    if df is None or df.empty or "value" not in df.columns:
        return 0, None
    work = df.copy()
    work["v"] = pd.to_numeric(work["value"], errors="coerce")
    work["d"] = pd.to_datetime(work["date"], errors="coerce")
    work = work.dropna(subset=["v", "d"]).sort_values("d")
    if work.empty:
        return 0, None
    return int(len(work)), clean_scalar(work.iloc[-1]["v"])


# ---------------------------------------------------------------------------
# Bond-identifier resolution — try several forms, return the one that resolves.
# blpapi accepts /isin/ and /cusip/ security identifiers; which one a given
# terminal resolves can vary, so every form is tried and resolve-checked.
# ---------------------------------------------------------------------------
def candidate_bond_queries(isin: Optional[str], cusip: Optional[str]) -> List[str]:
    forms: List[str] = []
    if isin:
        forms += [f"/isin/{isin}", f"/isin/{isin} Govt", f"{isin} Govt", f"/bbgid/{isin}"]
    if cusip:
        forms += [f"/cusip/{cusip}", f"/cusip/{cusip} Govt", f"{cusip} Govt"]
    return forms


def resolve_underlying_bond(
    logger: Logger, generic: str
) -> Dict[str, Any]:
    """bdp the generic for its underlying-bond identifiers, then find a query
    form that resolves on THIS terminal (confirmed by SECURITY_DES coming
    back). Returns a dict with the identifiers + the working query form."""
    ids, ids_err = safe_bdp_batch(generic, UNDERLYING_ID_FIELDS)
    isin = ids.get("ID_ISIN")
    cusip = ids.get("ID_CUSIP")
    logger.log(f"  identifiers: ISIN={isin} CUSIP={cusip} "
               f"DES={ids.get('SECURITY_DES')}"
               + (f"  [bdp error: {ids_err}]" if ids_err else ""))

    resolved_query: Optional[str] = None
    tried: List[str] = []
    for form in candidate_bond_queries(isin, cusip):
        chk, _ = safe_bdp_batch(form, ["SECURITY_DES"])
        ok = bool(chk.get("SECURITY_DES"))
        tried.append(f"{form} -> {'OK' if ok else 'no'}")
        if ok:
            resolved_query = form
            break
    for t in tried:
        logger.log(f"    query form: {t}")
    if resolved_query:
        logger.log(f"  => resolved query form: {resolved_query}")
    else:
        logger.log("  => NO query form resolved — bond cannot be probed")
    return {
        "id_isin": isin,
        "id_cusip": cusip,
        "security_des": ids.get("SECURITY_DES"),
        "coupon": ids.get("CPN"),
        "maturity": ids.get("MATURITY"),
        "issue_date": ids.get("ISSUE_DT"),
        "resolved_query": resolved_query,
        "bdp_error": ids_err,
    }


# ---------------------------------------------------------------------------
# Preflight — refuse to run if not actually reaching live Bloomberg data.
# Uses a PLAIN ticker (no /cusip//isin/ identifier syntax) so it tests pure
# terminal connectivity, independent of which bond-query form this terminal
# happens to support.
# ---------------------------------------------------------------------------
def preflight_connectivity_check(logger: Logger, start_str: str, end_str: str) -> None:
    """Confirm, on a plain ticker, that (a) Bloomberg is live and (b) BOTH the
    bdp and bdh normalisation paths read this terminal's xbbg output. If a
    normalisation path fails, the raw dataframe type/columns are logged so the
    exact xbbg shape is diagnosable from the report alone."""
    logger.log("")
    logger.log("PREFLIGHT — Bloomberg connectivity + dataframe-format check:")

    # Raw-shape capture — makes any normalisation miss diagnosable.
    try:
        raw_bdp = blp.bdp(tickers=CONNECTIVITY_TICKER, flds=["SECURITY_DES"])
        logger.log(f"  raw bdp -> type={type(raw_bdp).__name__}  "
                   f"columns={list(getattr(raw_bdp, 'columns', []))[:8]}")
    except Exception as exc:
        logger.log(f"  raw bdp call raised: {type(exc).__name__}: {exc}")
    try:
        raw_bdh = blp.bdh(tickers=CONNECTIVITY_TICKER, flds=["PX_LAST"],
                          start_date=start_str, end_date=end_str)
        logger.log(f"  raw bdh -> type={type(raw_bdh).__name__}  "
                   f"columns={list(getattr(raw_bdh, 'columns', []))[:8]}")
    except Exception as exc:
        logger.log(f"  raw bdh call raised: {type(exc).__name__}: {exc}")

    vals, err = safe_bdp_batch(CONNECTIVITY_TICKER, ["SECURITY_DES", "NAME"])
    bdp_ok = bool(vals.get("SECURITY_DES") or vals.get("NAME"))
    logger.log(f"  bdp({CONNECTIVITY_TICKER}) normalised -> "
               f"SECURITY_DES={vals.get('SECURITY_DES')}"
               + (f"   [error: {err}]" if err else ""))

    hist, bdh_err = safe_bdh_single(CONNECTIVITY_TICKER, "PX_LAST", start_str, end_str)
    pts, _ = history_points(hist)
    logger.log(f"  bdh({CONNECTIVITY_TICKER}, PX_LAST) normalised -> {pts} points"
               + (f"   [error: {bdh_err}]" if bdh_err else ""))

    if not bdp_ok or pts == 0:
        logger.log("  *** PREFLIGHT FAILED ***")
        logger.log("  A plain ticker did not survive the normalisation layer.")
        logger.log("  Either the Bloomberg terminal is not live, OR this xbbg")
        logger.log("  returns a dataframe shape the script still cannot read — the")
        logger.log("  'raw bdp -> type=' / 'raw bdh -> type=' lines above tell the")
        logger.log("  agent exactly which. Send the report back. Aborting.")
        logger.save()
        raise RuntimeError(
            "Preflight failed: plain-ticker bdp/bdh did not normalise. "
            "See the report's 'raw bdp/bdh -> type=' lines."
        )
    logger.log("  PREFLIGHT OK — live Bloomberg data confirmed and normalised "
               "(both bdp and bdh).")


# ---------------------------------------------------------------------------
# Main.
# ---------------------------------------------------------------------------
def main() -> None:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path.cwd() / f"a4_duration_probe_{timestamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    txt_path = out_dir / "a4_duration_probe_report.txt"
    resolution_csv = out_dir / "a4_duration_probe_resolution.csv"
    detail_csv = out_dir / "a4_duration_probe_detail.csv"

    logger = Logger(txt_path)
    resolution_rows: List[Dict[str, Any]] = []
    detail_rows: List[Dict[str, Any]] = []

    end_date = date.today() - timedelta(days=1)
    start_date = end_date - timedelta(days=int(LOOKBACK_YEARS * 365.25))
    start_str = start_date.strftime("%Y-%m-%d")
    end_str = end_date.strftime("%Y-%m-%d")

    logger.log("=" * 100)
    logger.log("A4 — DURATION-FIELD PROBE + A4-4 FIELD RECONFIRM (machine-robust)")
    logger.log("=" * 100)
    logger.log(f"Timestamp        : {timestamp}")
    logger.log(f"History window   : {start_str} to {end_str}  ({LOOKBACK_YEARS}y)")
    logger.log(f"Generics         : {len(GENERIC_UNIVERSE)} (resolved to bonds at run time)")
    logger.log(f"Fields probed    : {len(PROBE_FIELDS)} ({len(DURATION_FIELDS)} duration candidates)")
    logger.log(f"Output directory : {out_dir}")

    # ----------------------------------------------------------------------
    # PREFLIGHT
    # ----------------------------------------------------------------------
    preflight_connectivity_check(logger, start_str, end_str)

    # ----------------------------------------------------------------------
    # STEP 1 — resolve every generic to its underlying cash bond.
    # ----------------------------------------------------------------------
    logger.log("")
    logger.log("#" * 100)
    logger.log("STEP 1 — RESOLVE GENERICS TO UNDERLYING BONDS")
    logger.log("#" * 100)
    resolved: List[Dict[str, Any]] = []
    for cfg in GENERIC_UNIVERSE:
        logger.log("")
        logger.log(f"{cfg['ticker']}  ({cfg['country']} {cfg['tenor']})")
        res = resolve_underlying_bond(logger, cfg["ticker"])
        row = {**cfg, **res}
        resolution_rows.append(row)
        if res["resolved_query"]:
            resolved.append(row)

    working_forms = sorted({
        r["resolved_query"].split("/")[1] if r["resolved_query"].startswith("/")
        else "bare"
        for r in resolved
    }) if resolved else []
    logger.log("")
    logger.log(f"STEP 1 result: {len(resolved)}/{len(GENERIC_UNIVERSE)} generics "
               f"resolved to a bond. Working identifier form(s): {working_forms or '(none)'}")

    if not resolved:
        logger.log("")
        logger.log("*** NO bond resolved on this terminal. Cannot probe fields. ***")
        logger.log("The terminal is live (preflight passed) but none of the tried")
        logger.log("identifier forms (/isin/, /cusip/, <isin> Govt, …) resolved a")
        logger.log("bond. Send this report back — the agent will add the form this")
        logger.log("terminal expects.")
        _write_csvs(logger, resolution_rows, detail_rows, resolution_csv, detail_csv)
        logger.log(f"Text report     : {txt_path}")
        logger.save()
        return

    # ----------------------------------------------------------------------
    # STEP 2 — probe every field on every resolved bond (bdh + bdp).
    # ----------------------------------------------------------------------
    logger.log("")
    logger.log("#" * 100)
    logger.log("STEP 2 — FIELD PROBE ON RESOLVED REAL BONDS")
    logger.log("#" * 100)
    for bond in resolved:
        q = bond["resolved_query"]
        logger.log("")
        logger.log(f"{bond['ticker']:<16} -> {q}   ({bond['security_des']})")
        for spec in PROBE_FIELDS:
            field = spec["field"]
            hist_df, bdh_err = safe_bdh_single(q, field, start_str, end_str)
            points, last_val = history_points(hist_df)
            bdp_vals, bdp_err = safe_bdp_batch(q, [field])
            bdp_val = bdp_vals.get(field.upper())
            bdp_has = bdp_val is not None and not (
                isinstance(bdp_val, str) and bdp_val.strip() == ""
            )
            bdh_has = bdh_err is None and points > 0
            detail_rows.append({
                "generic_ticker": bond["ticker"],
                "country": bond["country"],
                "tenor": bond["tenor"],
                "resolved_query": q,
                "field": field,
                "purpose": spec["purpose"],
                "bdh_points": points,
                "bdh_last_value": last_val,
                "bdh_returned_data": bdh_has,
                "bdp_value": clean_scalar(bdp_val),
                "bdp_returned_data": bdp_has,
                "bdh_error": bdh_err,
                "bdp_error": bdp_err,
            })
            logger.log(
                f"  {field:<20} {spec['purpose']:<22} | "
                f"bdh {'DATA' if bdh_has else 'no  '} points={points:<5} "
                f"last={str(last_val):<14} | bdp {'DATA' if bdp_has else 'no '}={bdp_val}"
                + (f" | bdh err: {bdh_err}" if bdh_err else "")
            )

    # ----------------------------------------------------------------------
    # FINAL SUMMARY
    # ----------------------------------------------------------------------
    logger.log("")
    logger.log("=" * 100)
    logger.log("FINAL SUMMARY  (paste this block back to the agent)")
    logger.log("=" * 100)
    logger.log("")
    logger.log(f"Identifier form that resolves on this terminal: {working_forms or '(none)'}")
    logger.log(f"Bonds resolved + probed: {len(resolved)}/{len(GENERIC_UNIVERSE)}")
    logger.log("")

    n_bonds = len(resolved)
    half = max(1, n_bonds // 2)

    def field_hits(field: str) -> Tuple[int, int]:
        rows = [r for r in detail_rows if r["field"] == field]
        bdh = sum(1 for r in rows if r["bdh_returned_data"])
        bdp = sum(1 for r in rows if r["bdp_returned_data"])
        return bdh, bdp

    control_bdh, _ = field_hits(CONTROL_FIELD)
    if control_bdh == 0:
        logger.log("RESULT: *** INVALID RUN — DISREGARD THE RESULTS ABOVE ***")
        logger.log(f"The {CONTROL_FIELD} positive control returned 0 bdh data on every")
        logger.log("bond. It is proven to historise. Zero means this run did not reach")
        logger.log("live Bloomberg data — re-run on the live terminal.")
        _write_csvs(logger, resolution_rows, detail_rows, resolution_csv, detail_csv)
        logger.log("")
        logger.log(f"Text report     : {txt_path}")
        logger.save()
        return

    logger.log(f"Positive control {CONTROL_FIELD}: bdh data on {control_bdh}/{n_bonds} "
               "bonds — run is VALID.")
    logger.log("")
    logger.log("A4-4 confirm fields (should all historise via bdh):")
    for spec in PROBE_FIELDS:
        if spec["purpose"].startswith("A4-4"):
            bdh, bdp = field_hits(spec["field"])
            logger.log(f"  {spec['field']:<20} bdh {bdh}/{n_bonds} | bdp {bdp}/{n_bonds}")
    logger.log("")
    logger.log("Duration candidates (does any historise via bdh?):")
    historising: List[str] = []
    for field in DURATION_FIELDS:
        bdh, bdp = field_hits(field)
        verdict = ("HISTORISES" if bdh >= half
                   else ("bdp-snapshot only" if bdp > 0 else "not a usable field"))
        if bdh >= half:
            historising.append(field)
        logger.log(f"  {field:<20} bdh {bdh}/{n_bonds} | bdp {bdp}/{n_bonds} | {verdict}")
    logger.log("")
    if historising:
        logger.log(f"RESULT: a historised duration field EXISTS -> {historising}")
        logger.log("A4-4 adds the verified field as sovereign_cash_bonds.yml's")
        logger.log("modified-duration target_metric.")
    else:
        logger.log("RESULT: NO candidate historises modified duration via bdh.")
        logger.log("A4-4 ships DV01 (RISK_MID) as the risk measure; modified duration")
        logger.log("is derived (ModDur = RISK_MID * 100 / PX_DIRTY_MID, an identity,")
        logger.log("with full P12 disclosure) or deferred — agent + operator decide.")

    _write_csvs(logger, resolution_rows, detail_rows, resolution_csv, detail_csv)
    logger.log("")
    logger.log(f"Text report     : {txt_path}")
    logger.log(f"Resolution CSV  : {resolution_csv}")
    logger.log(f"Detail CSV      : {detail_csv}")
    logger.save()


def _write_csvs(
    logger: Logger,
    resolution_rows: List[Dict[str, Any]],
    detail_rows: List[Dict[str, Any]],
    resolution_csv: Path,
    detail_csv: Path,
) -> None:
    for label, rows, path in (
        ("resolution", resolution_rows, resolution_csv),
        ("detail", detail_rows, detail_csv),
    ):
        try:
            pd.DataFrame(rows).to_csv(path, index=False)
        except Exception as exc:
            logger.log(f"[WARNING] failed to write {label} CSV ({path}): {exc}")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        crash_dir = Path.cwd() / (
            f"a4_duration_probe_FATAL_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        )
        crash_dir.mkdir(parents=True, exist_ok=True)
        (crash_dir / "fatal_traceback.txt").write_text(
            "Fatal error while running the A4 duration-field probe.\n\n"
            + traceback.format_exc(),
            encoding="utf-8",
        )
        raise
