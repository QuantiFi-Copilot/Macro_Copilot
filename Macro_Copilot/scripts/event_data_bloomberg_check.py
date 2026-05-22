"""
event_data_bloomberg_check.py  — v2 (post-discovery reverification)
===================================================================

Operator-side Bloomberg verification for work order B2 — the event-calendar
data layer (ADR 0004). This is the v2 iteration: the first run + the operator's
manual terminal inspection resolved the access patterns; this script now
RE-VERIFIES them empirically with real bdh/bds calls so the B2 playbooks /
extractor ship only confirmed mnemonics.

Narwhals-agnostic: the normalisers below carry the same ``_coerce_to_pandas`` +
long-format handling as the production extractors, so they read both classic-
pandas and the newer Narwhals xbbg output.

FOUR SECTIONS — each emits its own CSV:

  SECTION 1 — ECONOMIC RELEASES   -> event_econ_releases.csv
      Re-confirms the 10 working tickers + the corrected UK CPI ticker
      (UKRPCJYR Index). ALSO probes ``ECO_RELEASE_DT_LIST`` via ``bds`` and
      dumps the table shape — that bulk field carries the per-observation
      release dates (the bdh series is indexed by reference PERIOD, not the
      release date that event_calendar.release_date needs).

  SECTION 2 — CENTRAL-BANK MEETINGS -> event_cb_meetings.csv
      Re-confirms the meeting calendar via ``bds(<rate>, ECO_RELEASE_DT_LIST)``,
      DUMPS the bds table shape, and extracts the meeting dates — which feed
      the WIRP ticker construction in Section 3.

  SECTION 3 — WIRP                -> event_wirp.csv
      VERIFIED ticker format (operator screenshots, 2026-05-21):
          {REGION}0B{METRIC} {MMMYYYY} Index     e.g. US0BFR JUN2026 Index
      Regions: US0B / EZ0B / GB0B / JP0B. Metrics: FR (post-meeting implied
      effective rate), PR (hike/cut probability), NM (number of 25bp moves),
      CH (implied bp change). Fixed-meeting tickers — no resolver layer.
      The probe constructs tickers from the Section-2 meeting calendars and
      bdh's a NEXT and a PAST meeting per bank — the past-meeting probe
      confirms historical tickers retain their daily series.

  SECTION 4 — SOVEREIGN AUCTIONS  -> event_auctions.csv
      US Treasury v1. Probes the discovered MOST_RECENT_* auction-result
      fields across 2Y/10Y/30Y benchmarks so the unit behaviour of the
      high-yield vs. "stop-yield"/tail fields can be disambiguated from real
      values.

DEPLOYMENT
----------
Self-contained — stdlib + pandas + xbbg only. Copy this single file to the
Bloomberg terminal host and run:

    python event_data_bloomberg_check.py

It writes a timestamped output directory (text report + 4 CSVs) into the
current working directory. Paste the FINAL SUMMARY block back to the agent.
"""

from __future__ import annotations

import sys
import traceback
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

try:
    from xbbg import blp
except Exception as exc:  # pragma: no cover - only meaningful on the BBG host
    print(f"[FATAL] could not import xbbg.blp: {exc}")
    raise


# ===========================================================================
# CONFIG
# ===========================================================================
LOOKBACK_YEARS = 6
MIN_SERIES_POINTS = 12
WIRP_LOOKBACK_DAYS = 400          # ~13 months — spans a meeting's run-up
WIRP_PAST_MEETING_TARGET_DAYS = 180  # probe a meeting ~6 months back

# --- SECTION 1: economic releases ------------------------------------------
ECON_ACTUAL_FIELD = "PX_LAST"
ECON_SURVEY_FIELDS = [
    "BN_SURVEY_MEDIAN",
    "BN_SURVEY_AVERAGE",
    "BN_SURVEY_HIGH",
    "BN_SURVEY_LOW",
    "FORECAST_STANDARD_DEVIATION",
]
# LATEST_ANNOUNCEMENT_PERIOD was dropped — it returned None for every ticker.
ECON_REFERENCE_FIELDS = ["NAME", "ECO_RELEASE_DT", "LAST_UPDATE_DT"]
# The per-observation release dates live in this BULK field — bds, not bdh.
ECON_RELEASE_DATE_BULK_FIELD = "ECO_RELEASE_DT_LIST"

# logical_name -> {country, currency, candidate_tickers, note}
ECON_RELEASE_CANDIDATES: Dict[str, Dict[str, Any]] = {
    "US_CPI_YOY": {"country": "US", "currency": "USD",
                   "candidate_tickers": ["CPI YOY Index"],
                   "note": "US headline CPI YoY."},
    "US_NONFARM_PAYROLLS": {"country": "US", "currency": "USD",
                            "candidate_tickers": ["NFP TCH Index"],
                            "note": "US non-farm payrolls (US-specific)."},
    "US_RETAIL_SALES_MOM": {"country": "US", "currency": "USD",
                            "candidate_tickers": ["RSTAMOM Index"],
                            "note": "US advance retail sales MoM."},
    "US_ISM_MANUFACTURING": {"country": "US", "currency": "USD",
                             "candidate_tickers": ["NAPMPMI Index"],
                             "note": "US ISM manufacturing PMI."},
    "US_INITIAL_JOBLESS_CLAIMS": {"country": "US", "currency": "USD",
                                  "candidate_tickers": ["INJCJC Index"],
                                  "note": "US initial jobless claims, weekly (US-specific)."},
    "EU_HICP_YOY": {"country": "EU", "currency": "EUR",
                    "candidate_tickers": ["ECCPEMUY Index"],
                    "note": "Euro-area HICP YoY."},
    "EU_COMPOSITE_PMI": {"country": "EU", "currency": "EUR",
                         "candidate_tickers": ["MPMIEZCA Index"],
                         "note": "Euro-area composite PMI."},
    "UK_CPI_YOY": {"country": "UK", "currency": "GBP",
                   "candidate_tickers": ["UKRPCJYR Index"],
                   "note": "UK headline CPI YoY (EU-harmonised measure) — "
                           "corrected ticker; UKCPIYOY Index was invalid."},
    "UK_COMPOSITE_PMI": {"country": "UK", "currency": "GBP",
                         "candidate_tickers": ["MPMIGBCA Index"],
                         "note": "UK composite PMI."},
    "JP_CPI_YOY": {"country": "JP", "currency": "JPY",
                   "candidate_tickers": ["JNCPIYOY Index"],
                   "note": "Japan national CPI YoY."},
    "JP_COMPOSITE_PMI": {"country": "JP", "currency": "JPY",
                         "candidate_tickers": ["MPMIJPCA Index"],
                         "note": "Japan composite PMI."},
}
# Dump the ECO_RELEASE_DT_LIST table shape for these representative tickers
# (a monthly series + the weekly claims series) — the schema is the same
# across tickers, so two dumps reveal it without flooding the report.
ECON_RELEASE_DATE_DUMP_TICKERS = ["CPI YOY Index", "INJCJC Index"]

# --- SECTION 2: central-bank meetings --------------------------------------
# ECO_RELEASE_DT_LIST is the confirmed meeting-calendar bulk field (bds).
CB_MEETING_BDS_FIELD = "ECO_RELEASE_DT_LIST"
CB_MEETING_BDP_FIELDS = ["ECO_RELEASE_DT", "LAST_UPDATE_DT", "NAME"]
# central_bank -> {country, currency, rate_ticker, note}
CB_MEETING_CANDIDATES: Dict[str, Dict[str, Any]] = {
    "FOMC": {"country": "US", "currency": "USD", "rate_ticker": "FDTR Index",
             "note": "Fed funds target rate (upper bound)."},
    "ECB": {"country": "EU", "currency": "EUR", "rate_ticker": "EUORDEPO Index",
            "note": "ECB deposit facility rate (the policy rate)."},
    "BOE": {"country": "UK", "currency": "GBP", "rate_ticker": "UKBRBASE Index",
            "note": "Bank of England base rate."},
    "BOJ": {"country": "JP", "currency": "JPY", "rate_ticker": "BOJDTR Index",
            "note": "Bank of Japan policy-rate balance."},
}

# --- SECTION 3: WIRP -------------------------------------------------------
# VERIFIED format (operator terminal screenshots, 2026-05-21):
#   {REGION}0B{METRIC} {MMMYYYY} Index   — fixed-meeting virtual tickers.
WIRP_REGION_PREFIXES: Dict[str, str] = {
    "FOMC": "US0B",
    "ECB": "EZ0B",
    "BOE": "GB0B",
    "BOJ": "JP0B",
}
WIRP_METRICS: Dict[str, str] = {
    "FR": "post-meeting implied effective rate",
    "PR": "probability of a single hike(+)/cut(-)",
    "NM": "number of 25bp moves priced",
    "CH": "implied bp change from current effective rate",
}
WIRP_FIELD = "PX_LAST"

# --- SECTION 4: sovereign auctions (US Treasury v1) ------------------------
# Discovered MOST_RECENT_* auction-result fields (operator FLDS inspection).
AUCTION_FIELDS = [
    "ID_CUSIP",
    "ISSUE_DT",
    "PRE_ANNOUNCED_AUCTION_DATE",
    "MOST_RECENT_ANNOUNCEMENT_DATE",
    "YLD_CNV_FROM_HIGH",                 # high-yield candidate
    "MOST_REC_DEBT_AUCTION_STO_YIELD",   # named "stop yield" — value behaves like a tail; disambiguate
    "MOST_RECENT_BID_COVER_RATIO",
    "MOST_RECENT_IND_BIDDER_ACCPT_AMT",
    "MOST_REC_PRIM_DEALER_ACCPT_AMT",
    "AMT_ISSUED_ACTUAL_UNROUNDED",
]
AUCTION_CANDIDATE_BONDS: Dict[str, str] = {
    "UST_2Y": "/isin/US91282CQL80",
    "UST_10Y": "/isin/US91282CQQ77",
    "UST_30Y": "/isin/US912810UU06",
}


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
    the installed xbbg used. Newer xbbg returns a Narwhals frame (it exposes
    ``.to_pandas()``; failing that ``narwhals.to_native()`` unwraps it). A
    pandas object (or None) is returned UNCHANGED and narwhals is never
    imported."""
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
    """xbbg bdp output → {FIELD: value}. Agnostic to classic WIDE and the
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


def normalize_bdh_output(df: Any, requested_field: str) -> pd.DataFrame:
    """xbbg bdh output → a 2-column ['date', 'value'] frame. Agnostic to
    classic WIDE and the newer Narwhals LONG (``ticker|date|field|value``)
    shapes."""
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

    if not isinstance(df.columns, pd.MultiIndex):
        _cols = {str(c).lower(): c for c in df.columns}
        if "field" in _cols and "value" in _cols:
            date_key = (_cols.get("date") or _cols.get("trade_date")
                        or _cols.get("index"))
            if date_key is None:
                for c in df.columns:
                    if pd.api.types.is_datetime64_any_dtype(df[c]):
                        date_key = c
                        break
            if date_key is not None:
                work = df
                _fmatch = df[df[_cols["field"]].astype(str).str.upper()
                             == str(requested_field).upper()]
                if not _fmatch.empty:
                    work = _fmatch
                return pd.DataFrame({
                    "date": list(work[date_key]),
                    "value": list(work[_cols["value"]]),
                })

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


# ===========================================================================
# Safe Bloomberg call wrappers — capture exceptions, never raise out of a loop.
# ===========================================================================
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


def safe_bds(ticker: str, field: str) -> Tuple[pd.DataFrame, Optional[str]]:
    try:
        raw = blp.bds(ticker, field)
        coerced = _coerce_to_pandas(raw)
        if coerced is None:
            return pd.DataFrame(), None
        if isinstance(coerced, pd.DataFrame):
            return coerced, None
        if isinstance(coerced, pd.Series):
            return coerced.to_frame(), None
        try:
            return pd.DataFrame(coerced), None
        except Exception as conv_exc:
            return pd.DataFrame(), (
                f"bds returned an unconvertible {type(coerced).__name__}: {conv_exc}"
            )
    except Exception as exc:
        return pd.DataFrame(), f"{type(exc).__name__}: {exc}"


# ===========================================================================
# Small analysis helpers.
# ===========================================================================
def summarize_history(df: pd.DataFrame) -> Dict[str, Any]:
    empty = {"non_null_points": 0, "first_date": None, "last_date": None,
             "last_value": None}
    if df is None or df.empty or "value" not in df.columns:
        return empty
    work = df.copy()
    work["value_clean"] = pd.to_numeric(work["value"], errors="coerce")
    work["date_clean"] = pd.to_datetime(work["date"], errors="coerce")
    work = work.dropna(subset=["value_clean", "date_clean"]).sort_values("date_clean")
    if work.empty:
        return empty
    return {
        "non_null_points": int(len(work)),
        "first_date": clean_scalar(work.iloc[0]["date_clean"]),
        "last_date": clean_scalar(work.iloc[-1]["date_clean"]),
        "last_value": clean_scalar(work.iloc[-1]["value_clean"]),
    }


def describe_bds(df: pd.DataFrame, max_rows: int = 4) -> str:
    """Compact one-line description of a bds table's shape — columns + sample
    rows — so the report reveals the schema."""
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return "EMPTY / no rows"
    cols = [str(c) for c in df.columns]
    head = df.head(max_rows).to_dict("records")
    sample = "; ".join(str({k: clean_scalar(v) for k, v in r.items()}) for r in head)
    return f"rows={len(df)} | cols={cols} | sample=[{sample}]"


def extract_dates_from_bds(df: pd.DataFrame) -> List[date]:
    """Pull the date column out of a bds table — the column that yields the
    most parseable dates."""
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return []
    best: List[date] = []
    for col in df.columns:
        parsed = pd.to_datetime(df[col], errors="coerce")
        good = [d.date() for d in parsed if pd.notna(d)]
        if len(good) > len(best):
            best = good
    return sorted(set(best))


def pick_probe_meetings(
    dates: List[date], today: date
) -> Tuple[Optional[date], Optional[date]]:
    """From a meeting-date list pick (next upcoming, a ~6-month-past) meeting."""
    future = [d for d in dates if d >= today]
    past = [d for d in dates if d < today]
    next_m = min(future) if future else None
    past_m = None
    if past:
        target = today - timedelta(days=WIRP_PAST_MEETING_TARGET_DAYS)
        past_m = min(past, key=lambda d: abs((d - target).days))
    return next_m, past_m


def fmt_meeting(d: date) -> str:
    """A meeting date → the WIRP ticker month token, e.g. 2026-06-17 → JUN2026."""
    return d.strftime("%b%Y").upper()


# ===========================================================================
# SECTIONS
# ===========================================================================
def section_economic_releases(
    logger: Logger, start: str, end: str
) -> List[Dict[str, Any]]:
    logger.log("#" * 100)
    logger.log("SECTION 1 — ECONOMIC RELEASES")
    logger.log("#" * 100)
    rows: List[Dict[str, Any]] = []

    for logical_name, cfg in ECON_RELEASE_CANDIDATES.items():
        logger.log("")
        logger.log("-" * 100)
        logger.log(f"{logical_name} | country={cfg['country']} | {cfg['note']}")
        logger.log("-" * 100)

        for ticker in cfg["candidate_tickers"]:
            actual_df, actual_err = safe_bdh_single(ticker, ECON_ACTUAL_FIELD, start, end)
            actual = summarize_history(actual_df)

            survey_counts: Dict[str, int] = {}
            for fld in ECON_SURVEY_FIELDS:
                s_df, _ = safe_bdh_single(ticker, fld, start, end)
                survey_counts[fld] = summarize_history(s_df)["non_null_points"]

            ref_values, ref_err = safe_bdp_batch(ticker, ECON_REFERENCE_FIELDS)

            # ECO_RELEASE_DT_LIST — the bulk field carrying per-observation
            # release dates. Probe row count for all; dump the schema for the
            # representative tickers.
            rdl_df, rdl_err = safe_bds(ticker, ECON_RELEASE_DATE_BULK_FIELD)
            rdl_rows = 0 if rdl_df is None or rdl_df.empty else int(len(rdl_df))
            if ticker in ECON_RELEASE_DATE_DUMP_TICKERS:
                logger.log(f"  [{ECON_RELEASE_DATE_BULK_FIELD} on {ticker}] "
                           f"{describe_bds(rdl_df)}"
                           + (f"  [error: {rdl_err}]" if rdl_err else ""))

            rows.append({
                "logical_name": logical_name,
                "country": cfg["country"],
                "currency": cfg["currency"],
                "ticker": ticker,
                "actual_points": actual["non_null_points"],
                "actual_first_date": actual["first_date"],
                "actual_last_date": actual["last_date"],
                "actual_last_value": actual["last_value"],
                "actual_error": actual_err,
                **{f"survey_{f.lower()}_points": c for f, c in survey_counts.items()},
                **{f"ref_{f.lower()}": ref_values.get(f.upper()) for f in ECON_REFERENCE_FIELDS},
                "release_date_list_rows": rdl_rows,
                "release_date_list_error": rdl_err,
                "actual_ok": actual["non_null_points"] >= MIN_SERIES_POINTS,
                "any_survey_returned": any(c > 0 for c in survey_counts.values()),
            })
            logger.log(
                f"  {ticker:<22} | actual {actual['non_null_points']:>4} pts "
                f"[{actual['first_date']} -> {actual['last_date']}] last={actual['last_value']} "
                f"| survey "
                + ",".join(f"{f.split('_')[-1]}={c}" for f, c in survey_counts.items())
                + f" | {ECON_RELEASE_DATE_BULK_FIELD}={rdl_rows} rows"
                + (f"  [actual error: {actual_err}]" if actual_err else "")
            )
    return rows


def section_central_bank_meetings(
    logger: Logger,
) -> Tuple[List[Dict[str, Any]], Dict[str, List[date]]]:
    logger.log("")
    logger.log("#" * 100)
    logger.log("SECTION 2 — CENTRAL-BANK MEETINGS")
    logger.log("#" * 100)
    rows: List[Dict[str, Any]] = []
    meeting_dates: Dict[str, List[date]] = {}

    for bank, cfg in CB_MEETING_CANDIDATES.items():
        ticker = cfg["rate_ticker"]
        logger.log("")
        logger.log("-" * 100)
        logger.log(f"{bank} | {ticker} | {cfg['note']}")
        logger.log("-" * 100)

        bds_df, bds_err = safe_bds(ticker, CB_MEETING_BDS_FIELD)
        dates = extract_dates_from_bds(bds_df)
        meeting_dates[bank] = dates
        logger.log(f"  bds {CB_MEETING_BDS_FIELD}: {describe_bds(bds_df)}"
                   + (f"  [error: {bds_err}]" if bds_err else ""))
        if dates:
            logger.log(f"  -> {len(dates)} meeting date(s) extracted "
                       f"[{dates[0]} ... {dates[-1]}]")

        bdp_values, bdp_err = safe_bdp_batch(ticker, CB_MEETING_BDP_FIELDS)
        for fld in CB_MEETING_BDP_FIELDS:
            logger.log(f"  bdp {fld:<20} = {bdp_values.get(fld.upper())}")

        rows.append({
            "central_bank": bank,
            "country": cfg["country"],
            "currency": cfg["currency"],
            "rate_ticker": ticker,
            "bds_field": CB_MEETING_BDS_FIELD,
            "bds_rows": 0 if bds_df is None or bds_df.empty else int(len(bds_df)),
            "meeting_dates_extracted": len(dates),
            "first_meeting": dates[0].isoformat() if dates else None,
            "last_meeting": dates[-1].isoformat() if dates else None,
            "bds_error": bds_err,
            **{f"bdp_{f.lower()}": bdp_values.get(f.upper()) for f in CB_MEETING_BDP_FIELDS},
        })
    return rows, meeting_dates


def section_wirp(
    logger: Logger, meeting_dates: Dict[str, List[date]], today: date
) -> List[Dict[str, Any]]:
    logger.log("")
    logger.log("#" * 100)
    logger.log("SECTION 3 — WIRP (fixed-meeting virtual tickers)")
    logger.log("#" * 100)
    logger.log("Ticker format VERIFIED on the terminal: {REGION}0B{METRIC} {MMMYYYY} Index")
    logger.log("Probing a NEXT and a PAST meeting per bank — the past probe confirms")
    logger.log("historical meeting tickers retain their daily series.")
    rows: List[Dict[str, Any]] = []

    win_start = (today - timedelta(days=WIRP_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    win_end = today.strftime("%Y-%m-%d")

    for bank, prefix in WIRP_REGION_PREFIXES.items():
        dates = meeting_dates.get(bank) or []
        next_m, past_m = pick_probe_meetings(dates, today)
        logger.log("")
        logger.log("-" * 100)
        logger.log(f"{bank} | region prefix {prefix} | "
                   f"next meeting={next_m} | past meeting={past_m}")
        logger.log("-" * 100)

        for label, mdate in (("next", next_m), ("past", past_m)):
            if mdate is None:
                logger.log(f"  ({label} meeting) — no date available, skipped")
                continue
            token = fmt_meeting(mdate)
            for metric, metric_desc in WIRP_METRICS.items():
                ticker = f"{prefix}{metric} {token} Index"
                hist, err = safe_bdh_single(ticker, WIRP_FIELD, win_start, win_end)
                summary = summarize_history(hist)
                rows.append({
                    "central_bank": bank,
                    "meeting_label": label,
                    "meeting_date": mdate.isoformat(),
                    "metric": metric,
                    "metric_desc": metric_desc,
                    "ticker": ticker,
                    "field": WIRP_FIELD,
                    "points": summary["non_null_points"],
                    "first_date": summary["first_date"],
                    "last_date": summary["last_date"],
                    "last_value": summary["last_value"],
                    "returned_data": summary["non_null_points"] > 0,
                    "error": err,
                })
                logger.log(
                    f"  [{label:<4}] {ticker:<26} ({metric}) -> "
                    f"{summary['non_null_points']:>4} pts "
                    f"[{summary['first_date']} -> {summary['last_date']}] "
                    f"last={summary['last_value']}"
                    + (f"  [error: {err}]" if err else "")
                )
    return rows


def section_auctions(logger: Logger) -> List[Dict[str, Any]]:
    logger.log("")
    logger.log("#" * 100)
    logger.log("SECTION 4 — SOVEREIGN AUCTIONS (US Treasury v1)")
    logger.log("#" * 100)
    logger.log("Probing the discovered MOST_RECENT_* fields across 2Y/10Y/30Y so the")
    logger.log("unit behaviour of YLD_CNV_FROM_HIGH vs MOST_REC_DEBT_AUCTION_STO_YIELD")
    logger.log("can be disambiguated (high-yield ~4-5% vs tail ~sub-bp).")
    rows: List[Dict[str, Any]] = []

    for label, ticker in AUCTION_CANDIDATE_BONDS.items():
        logger.log("")
        logger.log("-" * 100)
        logger.log(f"{label} | {ticker}")
        logger.log("-" * 100)
        values, err = safe_bdp_batch(ticker, AUCTION_FIELDS)
        rows.append({
            "label": label,
            "ticker": ticker,
            **{f"bdp_{f.lower()}": values.get(f.upper()) for f in AUCTION_FIELDS},
            "bdp_error": err,
        })
        for fld in AUCTION_FIELDS:
            logger.log(f"  bdp {fld:<34} = {values.get(fld.upper())}")
        if err:
            logger.log(f"  bdp error: {err}")
    return rows


# ===========================================================================
# Main.
# ===========================================================================
def main() -> None:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path.cwd() / f"event_data_verification_v2_{timestamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    txt_path = out_dir / "event_data_validation_report.txt"
    econ_csv = out_dir / "event_econ_releases.csv"
    cb_csv = out_dir / "event_cb_meetings.csv"
    wirp_csv = out_dir / "event_wirp.csv"
    auction_csv = out_dir / "event_auctions.csv"

    logger = Logger(txt_path)
    today = date.today()
    end_date = today - timedelta(days=1)
    start_date = end_date - timedelta(days=int(LOOKBACK_YEARS * 365.25))
    start_str = start_date.strftime("%Y-%m-%d")
    end_str = end_date.strftime("%Y-%m-%d")

    logger.log("=" * 100)
    logger.log("EVENT DATA — BLOOMBERG VERIFICATION v2 (work order B2)")
    logger.log("=" * 100)
    logger.log(f"Timestamp        : {timestamp}")
    logger.log(f"History window   : {start_str} to {end_str}  ({LOOKBACK_YEARS}y)")
    logger.log(f"Economic series  : {len(ECON_RELEASE_CANDIDATES)}")
    logger.log(f"Central banks    : {len(CB_MEETING_CANDIDATES)}")
    logger.log(f"WIRP regions     : {len(WIRP_REGION_PREFIXES)} x {len(WIRP_METRICS)} metrics")
    logger.log(f"Auction bonds    : {len(AUCTION_CANDIDATE_BONDS)}")
    logger.log(f"Output directory : {out_dir}")
    logger.log("")

    logger.log("PREFLIGHT — Bloomberg connectivity:")
    pf_values, pf_err = safe_bdp_batch("CPI YOY Index", ["NAME"])
    if pf_err or not pf_values.get("NAME"):
        logger.log(f"  WARNING — preflight bdp returned nothing (error: {pf_err}).")
    else:
        logger.log(f"  OK — CPI YOY Index -> NAME={pf_values.get('NAME')}")
    logger.log("")

    econ_rows = section_economic_releases(logger, start_str, end_str)
    cb_rows, meeting_dates = section_central_bank_meetings(logger)
    wirp_rows = section_wirp(logger, meeting_dates, today)
    auction_rows = section_auctions(logger)

    # ----------------------------------------------------------------------
    # FINAL SUMMARY
    # ----------------------------------------------------------------------
    logger.log("")
    logger.log("=" * 100)
    logger.log("FINAL SUMMARY  (paste this block back to the agent)")
    logger.log("=" * 100)

    econ_ok = [r for r in econ_rows if r["actual_ok"]]
    rdl_ok = [r for r in econ_rows if r["release_date_list_rows"] > 0]
    logger.log(
        f"SECTION 1 — economic releases: {len(econ_ok)}/{len(econ_rows)} tickers "
        f"returned a usable actual series; {len(rdl_ok)}/{len(econ_rows)} returned "
        f"an {ECON_RELEASE_DATE_BULK_FIELD} table."
    )
    for r in econ_rows:
        logger.log(
            f"  {r['logical_name']:<26} {r['ticker']:<18} -> "
            f"actual {r['actual_points']:>4} pts | survey "
            f"{'YES' if r['any_survey_returned'] else 'no '} | "
            f"release-date rows {r['release_date_list_rows']}"
        )

    cb_ok = [r for r in cb_rows if r["meeting_dates_extracted"] > 0]
    logger.log("")
    logger.log(f"SECTION 2 — central-bank meetings: {len(cb_ok)}/{len(cb_rows)} "
               "exposed a meeting calendar.")
    for r in cb_rows:
        logger.log(f"  {r['central_bank']:<6} {r['rate_ticker']:<16} -> "
                   f"{r['meeting_dates_extracted']} meeting date(s)")

    wirp_ok = [r for r in wirp_rows if r["returned_data"]]
    wirp_past_ok = [r for r in wirp_rows if r["returned_data"] and r["meeting_label"] == "past"]
    logger.log("")
    logger.log(f"SECTION 3 — WIRP: {len(wirp_ok)}/{len(wirp_rows)} (ticker, metric) "
               f"probes returned data; {len(wirp_past_ok)} of those are PAST "
               "meetings (history-retention check).")
    for r in wirp_rows:
        logger.log(
            f"  [{r['meeting_label']:<4}] {r['ticker']:<26} -> "
            f"{r['points']:>4} pts  last={r['last_value']}"
            + ("" if r["returned_data"] else "  <-- NO DATA")
        )

    logger.log("")
    logger.log("SECTION 4 — auctions: probed 3 UST benchmarks. Disambiguation "
               "(agent reads the CSV):")
    for r in auction_rows:
        logger.log(
            f"  {r['label']:<8} {r['ticker']:<22} | "
            f"YLD_CNV_FROM_HIGH={r.get('bdp_yld_cnv_from_high')} | "
            f"STO_YIELD={r.get('bdp_most_rec_debt_auction_sto_yield')} | "
            f"bid_cover={r.get('bdp_most_recent_bid_cover_ratio')}"
        )

    logger.log("")
    logger.log("NEXT: the agent reads the per-family CSVs to finalise the event-")
    logger.log("playbook field lists and the --mode event-calendar extractor (Stage C).")

    for label, data, path in (
        ("econ-releases", econ_rows, econ_csv),
        ("cb-meetings", cb_rows, cb_csv),
        ("wirp", wirp_rows, wirp_csv),
        ("auctions", auction_rows, auction_csv),
    ):
        try:
            pd.DataFrame(data).to_csv(path, index=False)
        except Exception as exc:
            logger.log(f"[WARNING] failed to write {label} CSV ({path}): {exc}")

    logger.log("")
    logger.log(f"Text report        : {txt_path}")
    logger.log(f"Econ-releases CSV  : {econ_csv}")
    logger.log(f"CB-meetings CSV    : {cb_csv}")
    logger.log(f"WIRP CSV           : {wirp_csv}")
    logger.log(f"Auctions CSV       : {auction_csv}")
    logger.save()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        crash_dir = Path.cwd() / (
            f"event_data_verification_v2_FATAL_"
            f"{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        )
        crash_dir.mkdir(parents=True, exist_ok=True)
        (crash_dir / "fatal_traceback.txt").write_text(
            "Fatal error while running event-data verification v2.\n\n"
            + traceback.format_exc(),
            encoding="utf-8",
        )
        raise
