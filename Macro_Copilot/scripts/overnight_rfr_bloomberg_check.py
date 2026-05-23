"""overnight_rfr_bloomberg_check.py — overnight RFR Bloomberg verification
=========================================================================

Operator-side Bloomberg verification for work order **C2a** (Track C, Step 1
sub-phase 4A of the data-first roadmap; ADR 0010 = the repo substrate).

ADR 0010 fixed the substrate: repo is time-series, lands in
``macro_data.market_data_daily`` on the existing ``--mode time-series`` rails,
sourced **Bloomberg-only** (a non-Bloomberg source would be a second L1
adapter, P7). C2a is the first repo data PR — the overnight RFR fixings for
the four core macro markets:

  USD  SOFR    (Secured Overnight Financing Rate, Federal Reserve)
  EUR  ESTR    (Euro Short-Term Rate, ECB)
  GBP  SONIA   (Sterling Overnight Index Average, Bank of England)
  JPY  TONA    (Tokyo Overnight Average Rate, Bank of Japan)

Nothing in this universe is trusted until this script's terminal run confirms
it returns clean data. Every candidate ticker in ``rates_agent/playbooks/
overnight_rfr.yml`` is marked ``# CANDIDATE — verify via …``; this probe
empirically validates each via real ``bdh`` / ``bdp`` calls, decides which
candidate (if any) wins per market, and emits a final summary the operator
pastes back to the agent for the playbook to be finalised (``# VERIFIED
<date>``) — or for a market with no working candidate to be deferred and
documented (the data-PR discipline, P2/P5).

Narwhals-agnostic: the normalisers carry the same ``_coerce_to_pandas`` +
long-format handling as the production extractors so the probe reads both
classic-pandas and the newer Narwhals xbbg output.

OUTPUT
------
A timestamped directory in the current working directory containing:

  * ``overnight_rfr_report.txt``     — sectioned text report with the
                                       FINAL SUMMARY block at the bottom.
  * ``overnight_rfr_per_field.csv``  — one row per (RFR, candidate ticker,
                                       field): pass/fail, points, last value,
                                       error, note.
  * ``overnight_rfr_per_instrument.csv`` — one row per (RFR, candidate ticker):
                                       did this candidate return a usable
                                       PX_LAST series + a sane SECURITY_DES?
  * ``overnight_rfr_per_market.csv`` — one row per RFR (USD/EUR/GBP/JPY):
                                       did ANY candidate win? recommended ticker.

DEPLOYMENT
----------
Self-contained — stdlib + pandas + xbbg only. Copy this single file to the
Bloomberg terminal host and run:

    python overnight_rfr_bloomberg_check.py

Paste the FINAL SUMMARY block back to the agent.
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
LOOKBACK_YEARS = 5
# A reasonable RFR series should return well above this for a 5y window. SOFR
# launched 2018-04-03 and ESTR 2019-10-02; even with launch-date truncation a
# verified RFR ticker should return many hundreds of daily points.
MIN_SERIES_POINTS = 200
# Bloomberg fields tested per candidate. PX_LAST is the daily rate; the bdp
# triple is descriptive sanity-check metadata.
PX_FIELD = "PX_LAST"
BDP_FIELDS = ["SECURITY_DES", "NAME", "CRNCY"]
# Sane-rate window. Negative-rate eras (ESTR/TONA pre-2022, SONIA in QE) had
# values down to ~-0.6%; the post-2022 high in SOFR was ~5.4%.
SANE_RATE_LO = -2.0
SANE_RATE_HI = 10.0


# market -> {currency, candidate_tickers, note}.
# Each ``candidate_tickers`` list is ordered by my prior — the most-likely
# Bloomberg ticker first, alternatives after. ALL are CANDIDATE; the operator
# report decides which wins.
CANDIDATE_RFRS: Dict[str, Dict[str, Any]] = {
    "SOFR": {
        "country": "US", "currency": "USD",
        "candidate_tickers": ["SOFRRATE Index", "SOFRINDX Index"],
        "note": ("USD Secured Overnight Financing Rate (Federal Reserve). "
                 "SOFRRATE is the daily fixing rate; SOFRINDX is the "
                 "compounded index — the playbook wants the DAILY RATE."),
    },
    "ESTR": {
        "country": "EU", "currency": "EUR",
        "candidate_tickers": ["ESTRON Index", "ESTR Index"],
        "note": ("EUR Euro Short-Term Rate (ECB). ESTR launched 2019-10-02 "
                 "— series before that is empty (acceptable)."),
    },
    "SONIA": {
        "country": "UK", "currency": "GBP",
        "candidate_tickers": ["SONIO/N Index", "SONIA Index"],
        "note": ("GBP Sterling Overnight Index Average (Bank of England). "
                 "Methodology reformed 2018-04-23 — pre-reform values are "
                 "the same ticker family per Bloomberg convention."),
    },
    "TONA": {
        "country": "JP", "currency": "JPY",
        "candidate_tickers": ["TONAR Index", "MUTSCALM Index"],
        "note": ("JPY Tokyo Overnight Average Rate (Bank of Japan). "
                 "MUTSCALM (Mutan overnight) is a historical alternative "
                 "if TONAR does not verify."),
    },
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
    """xbbg bdh output → 2-column ['date', 'value'] frame. Agnostic to classic
    WIDE and the newer Narwhals LONG (``ticker|date|field|value``) shapes."""
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


# ===========================================================================
# Series analysis.
# ===========================================================================
def summarize_history(df: pd.DataFrame) -> Dict[str, Any]:
    """Series summary: point count, first/last date, last value, min/max."""
    empty = {"non_null_points": 0, "first_date": None, "last_date": None,
             "last_value": None, "min_value": None, "max_value": None}
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
        "min_value": clean_scalar(work["value_clean"].min()),
        "max_value": clean_scalar(work["value_clean"].max()),
    }


def value_in_sane_range(value: Optional[float], lo: float, hi: float) -> bool:
    """A rate should sit in [-2%, +10%]. Outside that range is a strong signal
    the ticker is the wrong field (e.g. an index level, not a rate)."""
    if value is None:
        return False
    try:
        v = float(value)
    except (TypeError, ValueError):
        return False
    return lo <= v <= hi


# ===========================================================================
# Per-candidate probe.
# ===========================================================================
def probe_candidate(
    logger: Logger,
    market: str,
    cfg: Dict[str, Any],
    ticker: str,
    start: str,
    end: str,
) -> Dict[str, Any]:
    """Probe ONE candidate ticker for ONE market: bdh PX_LAST + bdp descriptive
    fields. Returns a row of results."""
    hist, hist_err = safe_bdh_single(ticker, PX_FIELD, start, end)
    px = summarize_history(hist)
    bdp, bdp_err = safe_bdp_batch(ticker, BDP_FIELDS)

    px_ok = px["non_null_points"] >= MIN_SERIES_POINTS and hist_err is None
    last_in_range = value_in_sane_range(px["last_value"], SANE_RATE_LO, SANE_RATE_HI)
    bdp_ok = bdp_err is None and any(bdp.get(f.upper()) for f in BDP_FIELDS)

    if px_ok and last_in_range and bdp_ok:
        note = "OK — all checks passed"
    elif hist_err is not None:
        note = f"FAIL — bdh error: {hist_err}"
    elif px["non_null_points"] == 0:
        note = "FAIL — bdh returned no data"
    elif px["non_null_points"] < MIN_SERIES_POINTS:
        note = (f"FAIL — bdh returned only {px['non_null_points']} points "
                f"(< {MIN_SERIES_POINTS} threshold)")
    elif not last_in_range:
        note = (f"FAIL — last_value={px['last_value']} outside the sane rate "
                f"range [{SANE_RATE_LO}, {SANE_RATE_HI}]; wrong ticker (probably "
                "an index level, not a rate)")
    elif not bdp_ok:
        note = f"WARN — bdp metadata empty or errored ({bdp_err})"
    else:
        note = "WARN — partial"

    row = {
        "market": market,
        "country": cfg["country"],
        "currency": cfg["currency"],
        "candidate_ticker": ticker,
        "px_field": PX_FIELD,
        "points": px["non_null_points"],
        "first_date": px["first_date"],
        "last_date": px["last_date"],
        "last_value": px["last_value"],
        "min_value": px["min_value"],
        "max_value": px["max_value"],
        "px_ok": px_ok,
        "last_in_sane_range": last_in_range,
        "security_des": bdp.get("SECURITY_DES"),
        "name": bdp.get("NAME"),
        "crncy": bdp.get("CRNCY"),
        "bdp_ok": bdp_ok,
        "verdict": ("PASS" if (px_ok and last_in_range and bdp_ok)
                    else "FAIL" if not (px_ok and last_in_range) else "WARN"),
        "note": note,
        "bdh_error": hist_err,
        "bdp_error": bdp_err,
    }
    return row


# ===========================================================================
# Main.
# ===========================================================================
def main() -> None:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path.cwd() / f"overnight_rfr_check_{timestamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    logger = Logger(out_dir / "overnight_rfr_report.txt")
    per_field_csv      = out_dir / "overnight_rfr_per_field.csv"
    per_instrument_csv = out_dir / "overnight_rfr_per_instrument.csv"
    per_market_csv     = out_dir / "overnight_rfr_per_market.csv"

    today = date.today()
    end_date = today - timedelta(days=1)
    start_date = end_date - timedelta(days=int(LOOKBACK_YEARS * 365.25))
    start_str = start_date.strftime("%Y-%m-%d")
    end_str = end_date.strftime("%Y-%m-%d")

    logger.log("=" * 100)
    logger.log("OVERNIGHT RFR — BLOOMBERG VERIFICATION (work order C2a; ADR 0010)")
    logger.log("=" * 100)
    logger.log(f"Timestamp        : {timestamp}")
    logger.log(f"History window   : {start_str} to {end_str}  ({LOOKBACK_YEARS}y)")
    logger.log(f"Markets probed   : {len(CANDIDATE_RFRS)}  ({', '.join(CANDIDATE_RFRS)})")
    logger.log(f"px_field         : {PX_FIELD}")
    logger.log(f"bdp fields       : {BDP_FIELDS}")
    logger.log(f"Min points / OK  : {MIN_SERIES_POINTS}")
    logger.log(f"Sane rate range  : [{SANE_RATE_LO}, {SANE_RATE_HI}]")
    logger.log(f"Output directory : {out_dir}")
    logger.log("")

    # Preflight — confirm xbbg is talking to Bloomberg at all.
    logger.log("PREFLIGHT — Bloomberg connectivity:")
    pf_values, pf_err = safe_bdp_batch("SOFRRATE Index", ["NAME"])
    if pf_err or not pf_values.get("NAME"):
        logger.log(f"  WARNING — preflight bdp returned nothing (error: {pf_err}).")
    else:
        logger.log(f"  OK — SOFRRATE Index -> NAME={pf_values.get('NAME')}")
    logger.log("")

    per_field_rows: List[Dict[str, Any]] = []
    per_market_rows: List[Dict[str, Any]] = []

    # ---------------------------------------------------------------- probe
    logger.log("#" * 100)
    logger.log("PER-MARKET PROBE")
    logger.log("#" * 100)
    for market, cfg in CANDIDATE_RFRS.items():
        logger.log("")
        logger.log("-" * 100)
        logger.log(f"{market}  ({cfg['country']} / {cfg['currency']})")
        logger.log(f"  {cfg['note']}")
        logger.log("-" * 100)

        market_results: List[Dict[str, Any]] = []
        for ticker in cfg["candidate_tickers"]:
            r = probe_candidate(logger, market, cfg, ticker, start_str, end_str)
            market_results.append(r)
            per_field_rows.append(r)

            logger.log(
                f"  {ticker:<22} -> {r['verdict']:<4} "
                f"points={r['points']:>5}  "
                f"[{r['first_date']} -> {r['last_date']}]  "
                f"last={r['last_value']}  range=[{r['min_value']}, {r['max_value']}]"
            )
            logger.log(
                f"      SECURITY_DES={r['security_des']!r}  "
                f"NAME={r['name']!r}  CRNCY={r['crncy']!r}"
            )
            logger.log(f"      note: {r['note']}")

        # ----- pick the winning candidate (first PASS wins; else first WARN; else nothing)
        passing = [r for r in market_results if r["verdict"] == "PASS"]
        warning = [r for r in market_results if r["verdict"] == "WARN"]
        if passing:
            winner = passing[0]
            market_verdict = "PASS"
        elif warning:
            winner = warning[0]
            market_verdict = "WARN"
        else:
            winner = None
            market_verdict = "FAIL"

        per_market_rows.append({
            "market": market,
            "country": cfg["country"],
            "currency": cfg["currency"],
            "candidates_tried": len(cfg["candidate_tickers"]),
            "verdict": market_verdict,
            "recommended_ticker": winner["candidate_ticker"] if winner else None,
            "recommended_security_des": winner["security_des"] if winner else None,
            "recommended_points": winner["points"] if winner else 0,
            "recommended_last_value": winner["last_value"] if winner else None,
            "recommended_first_date": winner["first_date"] if winner else None,
            "recommended_last_date": winner["last_date"] if winner else None,
            "note": (
                f"winner: {winner['candidate_ticker']}" if winner
                else "no candidate verified — DEFER per documented-deferral discipline (P2/P5)"
            ),
        })

    # ---------------------------------------------------------------- CSVs
    try:
        pd.DataFrame(per_field_rows).to_csv(per_field_csv, index=False)
    except Exception as exc:  # pragma: no cover
        logger.log(f"[WARNING] failed to write per-field CSV ({per_field_csv}): {exc}")
    try:
        pd.DataFrame(per_market_rows).to_csv(per_market_csv, index=False)
    except Exception as exc:  # pragma: no cover
        logger.log(f"[WARNING] failed to write per-market CSV ({per_market_csv}): {exc}")
    # The per-instrument CSV is the per-field CSV de-duplicated on (market, ticker)
    # — one row per candidate — for the operator's quick aggregate view.
    try:
        if per_field_rows:
            pd.DataFrame(per_field_rows)[[
                "market", "country", "currency", "candidate_ticker",
                "verdict", "points", "last_value", "security_des", "note",
            ]].to_csv(per_instrument_csv, index=False)
    except Exception as exc:  # pragma: no cover
        logger.log(f"[WARNING] failed to write per-instrument CSV ({per_instrument_csv}): {exc}")

    # ---------------------------------------------------------------- summary
    logger.log("")
    logger.log("=" * 100)
    logger.log("FINAL SUMMARY  (paste this block back to the agent)")
    logger.log("=" * 100)

    pass_n = sum(1 for r in per_market_rows if r["verdict"] == "PASS")
    warn_n = sum(1 for r in per_market_rows if r["verdict"] == "WARN")
    fail_n = sum(1 for r in per_market_rows if r["verdict"] == "FAIL")
    logger.log(f"Markets probed: {len(per_market_rows)} | PASS: {pass_n} | "
               f"WARN: {warn_n} | FAIL: {fail_n}")
    logger.log("")
    logger.log("Per-market verdict + recommended ticker:")
    for r in per_market_rows:
        logger.log(
            f"  {r['market']:<6} {r['country']}/{r['currency']:<3}  -> "
            f"{r['verdict']:<4}  "
            f"recommended_ticker={r['recommended_ticker']!r}  "
            f"({r['recommended_points']} pts, last={r['recommended_last_value']}, "
            f"span {r['recommended_first_date']}..{r['recommended_last_date']})"
        )
        logger.log(f"          note: {r['note']}")
    logger.log("")
    logger.log("NEXT: the agent reads the per-market CSV, updates the candidate")
    logger.log("tickers in rates_agent/playbooks/overnight_rfr.yml to the verified")
    logger.log("ones (marker `# VERIFIED <date>`), and DEFERS any market without")
    logger.log("a winning candidate per the documented-deferral discipline (P2/P5).")

    logger.log("")
    logger.log(f"Text report          : {logger.path}")
    logger.log(f"Per-field CSV        : {per_field_csv}")
    logger.log(f"Per-instrument CSV   : {per_instrument_csv}")
    logger.log(f"Per-market CSV       : {per_market_csv}")
    logger.save()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        crash_dir = Path.cwd() / (
            f"overnight_rfr_check_FATAL_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        )
        crash_dir.mkdir(parents=True, exist_ok=True)
        (crash_dir / "fatal_traceback.txt").write_text(
            "Fatal error while running the overnight-RFR verification.\n\n"
            + traceback.format_exc(),
            encoding="utf-8",
        )
        raise
