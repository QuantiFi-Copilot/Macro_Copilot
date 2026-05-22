"""
a4_ofr_resolver_probe.py — A4-4 follow-up probe: the OTR / off-the-run chain.

THE GOAL
--------
A4-4 needs, per (country, tenor) slot, the current ON-THE-RUN bond AND the
current 1st OFF-THE-RUN bond, as real CUSIP/ISIN bonds, so otr_ofr_spread
works on current data. We already have the 14 current OTR bonds. This probe
finds the off-the-run bonds — WITHOUT guessing Bloomberg ticker names.

THE METHOD
----------
A GT-generic (GT10 Govt, …) points to whichever bond is the benchmark TODAY.
The question is: does the generic expose its UNDERLYING bond identifier as a
time series? If `bdh(<generic>, ID_ISIN, …)` returns the underlying ISIN per
day, then the distinct values over time ARE the on-the-run chain:

    … ISIN_A (OTR until d1) | ISIN_B (OTR d1..d2) | ISIN_C (OTR d2..today) …

— newest = current OTR, the one before = current 1st off-the-run, etc. One
call per slot then yields current OTR + 1st OFR + 2nd OFR + every earlier
benchmark, each with the date it became on-the-run. That single mechanism
seeds the off-the-runs A4-4 needs AND (date-verified) would make the deferred
historical OTR backfill clean later.

The probe tests this on ID_ISIN and SECURITY_DES across all 14 slots and
reports the chain it recovers. If `bdh` of a reference field does not
historise the underlying, the report says so plainly and we choose another
mechanism — nothing is guessed.

Machine-robust: handles classic-pandas and newer Narwhals xbbg output.

DEPLOYMENT
----------
Self-contained — stdlib + pandas + xbbg. Run on the Bloomberg PC:

    python a4_ofr_resolver_probe.py

Outputs land in ./a4_ofr_resolver_probe_<TIMESTAMP>/ — a text report + one CSV.
Return both; the FINAL SUMMARY block is the pasteable digest.
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
LOOKBACK_YEARS = 3                 # long enough to capture several OTR rolls
CONNECTIVITY_TICKER = "GT10 Govt"

# The 14 slots. `known_otr_isin` is the current OTR bond already in
# sovereign_cash_bonds.yml — the newest entry of a recovered chain must match
# it (a built-in correctness check on the chain mechanism).
SLOTS: List[Dict[str, str]] = [
    {"generic": "GT2 Govt",      "country": "US",        "tenor": "2Y",  "known_otr_isin": "US91282CQL80"},
    {"generic": "GT10 Govt",     "country": "US",        "tenor": "10Y", "known_otr_isin": "US91282CQQ77"},
    {"generic": "GT30 Govt",     "country": "US",        "tenor": "30Y", "known_otr_isin": "US912810UU06"},
    {"generic": "GTDEM2Y Govt",  "country": "Germany",   "tenor": "2Y",  "known_otr_isin": "DE000BU22130"},
    {"generic": "GTDEM10Y Govt", "country": "Germany",   "tenor": "10Y", "known_otr_isin": "DE000BU2Z064"},
    {"generic": "GTGBP2Y Govt",  "country": "UK",        "tenor": "2Y",  "known_otr_isin": "GB00BSQNRC93"},
    {"generic": "GTGBP10Y Govt", "country": "UK",        "tenor": "10Y", "known_otr_isin": "GB00BTXS1K06"},
    {"generic": "GTJPY2Y Govt",  "country": "Japan",     "tenor": "2Y",  "known_otr_isin": "JP1024841S56"},
    {"generic": "GTJPY10Y Govt", "country": "Japan",     "tenor": "10Y", "known_otr_isin": "JP1103821S45"},
    {"generic": "GTFRF10Y Govt", "country": "France",    "tenor": "10Y", "known_otr_isin": "FR0014012II5"},
    {"generic": "GTITL10Y Govt", "country": "Italy",     "tenor": "10Y", "known_otr_isin": "IT0005676504"},
    {"generic": "GTCAD10Y Govt", "country": "Canada",    "tenor": "10Y", "known_otr_isin": "CA135087T537"},
    {"generic": "GTAUD10Y Govt", "country": "Australia", "tenor": "10Y", "known_otr_isin": "AU0000381832"},
    {"generic": "GTESP10Y Govt", "country": "Spain",     "tenor": "10Y", "known_otr_isin": "ES0000012Q08"},
]

# Reference fields tested for "does the generic historise its underlying?"
CHAIN_FIELDS = ["ID_ISIN", "SECURITY_DES"]


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
# Cleaning + normalisation — handles classic-pandas AND newer Narwhals output.
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
    if isinstance(value, (datetime, date)):
        return value.strftime("%Y-%m-%d")
    if hasattr(value, "item"):
        try:
            return value.item()
        except (ValueError, TypeError):
            pass
    return value


def _coerce_to_pandas(obj: Any) -> Any:
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
    if "field" in cols and "value" in cols:
        return {str(f).upper(): clean_scalar(v)
                for f, v in zip(df[cols["field"]], df[cols["value"]])}
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
    """xbbg bdh output -> 2-column ['date','value'], for either dataframe shape."""
    empty = pd.DataFrame(columns=["date", "value"])
    df = _coerce_to_pandas(df)
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return empty
    cols = {str(c).lower(): c for c in df.columns}
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


def safe_bdh_single(ticker: str, field: str, start: str, end: str
                    ) -> Tuple[pd.DataFrame, Optional[str]]:
    try:
        raw = blp.bdh(tickers=ticker, flds=[field], start_date=start, end_date=end)
        return normalize_bdh_output(raw, requested_field=field), None
    except Exception as exc:
        return pd.DataFrame(columns=["date", "value"]), f"{type(exc).__name__}: {exc}"


def safe_bdp_batch(ticker: str, fields: List[str]
                   ) -> Tuple[Dict[str, Any], Optional[str]]:
    try:
        raw = blp.bdp(tickers=ticker, flds=fields)
        norm = normalize_bdp_output(raw, fallback_ticker=ticker)
        return {f.upper(): norm.get(f.upper()) for f in fields}, None
    except Exception as exc:
        return {f.upper(): None for f in fields}, f"{type(exc).__name__}: {exc}"


# ---------------------------------------------------------------------------
# Chain extraction — collapse a [date, value] series into consecutive runs.
# Each run = one underlying bond's on-the-run window.
# ---------------------------------------------------------------------------
def underlying_runs(df: pd.DataFrame) -> List[Dict[str, Any]]:
    if df is None or df.empty or "value" not in df.columns:
        return []
    work = df.copy()
    work["d"] = pd.to_datetime(work["date"], errors="coerce")
    work = work.dropna(subset=["d", "value"])
    work["v"] = work["value"].astype(str).str.strip()
    work = work[(work["v"] != "") & (work["v"].str.lower() != "nan")]
    work = work.sort_values("d")
    runs: List[Dict[str, Any]] = []
    for _, r in work.iterrows():
        v, d = r["v"], r["d"]
        if runs and runs[-1]["value"] == v:
            runs[-1]["last_date"] = d
            runs[-1]["points"] += 1
        else:
            runs.append({"value": v, "first_date": d, "last_date": d, "points": 1})
    return runs


# ---------------------------------------------------------------------------
# Preflight.
# ---------------------------------------------------------------------------
def preflight(logger: Logger) -> None:
    logger.log("")
    logger.log("PREFLIGHT — Bloomberg connectivity:")
    vals, err = safe_bdp_batch(CONNECTIVITY_TICKER, ["SECURITY_DES"])
    if not vals.get("SECURITY_DES"):
        logger.log(f"  *** FAILED — bdp({CONNECTIVITY_TICKER}) returned nothing"
                   + (f" [{err}]" if err else "") + ". Terminal not live.")
        logger.save()
        raise RuntimeError("Preflight failed — start/log into the Bloomberg terminal.")
    logger.log(f"  OK — {CONNECTIVITY_TICKER} -> {vals.get('SECURITY_DES')}")


# ---------------------------------------------------------------------------
# Main.
# ---------------------------------------------------------------------------
def main() -> None:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path.cwd() / f"a4_ofr_resolver_probe_{timestamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    txt_path = out_dir / "a4_ofr_resolver_probe_report.txt"
    csv_path = out_dir / "a4_ofr_resolver_probe_chain.csv"

    logger = Logger(txt_path)
    rows: List[Dict[str, Any]] = []

    end_date = date.today() - timedelta(days=1)
    start_date = end_date - timedelta(days=int(LOOKBACK_YEARS * 365.25))
    start_str = start_date.strftime("%Y-%m-%d")
    end_str = end_date.strftime("%Y-%m-%d")

    logger.log("=" * 100)
    logger.log("A4-4 — OTR / OFF-THE-RUN CHAIN PROBE")
    logger.log("=" * 100)
    logger.log(f"Timestamp      : {timestamp}")
    logger.log(f"History window : {start_str} to {end_str}  ({LOOKBACK_YEARS}y)")
    logger.log(f"Slots          : {len(SLOTS)}")
    logger.log(f"Chain fields   : {CHAIN_FIELDS}")
    logger.log(f"Output dir     : {out_dir}")
    logger.log("")
    logger.log("For each slot: does bdh of a reference field on the GT generic")
    logger.log("historise the underlying bond? If so, the distinct values over")
    logger.log("time are the on-the-run chain (newest = current OTR, next =")
    logger.log("current 1st off-the-run, …).")

    preflight(logger)

    for slot in SLOTS:
        generic = slot["generic"]
        logger.log("")
        logger.log("#" * 100)
        logger.log(f"{generic}  ({slot['country']} {slot['tenor']})  "
                   f"known current OTR ISIN = {slot['known_otr_isin']}")
        logger.log("#" * 100)

        # control — current underlying via bdp
        cur, cur_err = safe_bdp_batch(generic, ["ID_ISIN", "SECURITY_DES"])
        logger.log(f"  bdp now -> ID_ISIN={cur.get('ID_ISIN')} "
                   f"SECURITY_DES={cur.get('SECURITY_DES')}"
                   + (f"  [bdp err: {cur_err}]" if cur_err else ""))

        for field in CHAIN_FIELDS:
            hist, err = safe_bdh_single(generic, field, start_str, end_str)
            runs = underlying_runs(hist)
            logger.log(f"  bdh({field}) -> {len(runs)} distinct underlying(s) "
                       f"over the window"
                       + (f"  [bdh err: {err}]" if err else ""))
            # newest-first
            for i, run in enumerate(reversed(runs)):
                role = ("current OTR" if i == 0
                        else ("1st off-the-run" if i == 1
                              else f"{i}th off-the-run"))
                logger.log(
                    f"      [{role:<16}] {run['value']:<22} "
                    f"{clean_scalar(run['first_date'])} .. "
                    f"{clean_scalar(run['last_date'])}  ({run['points']} days)"
                )
                rows.append({
                    "generic": generic,
                    "country": slot["country"],
                    "tenor": slot["tenor"],
                    "chain_field": field,
                    "role": role,
                    "chain_index_newest_first": i,
                    "underlying": run["value"],
                    "effective_from": clean_scalar(run["first_date"]),
                    "effective_to": clean_scalar(run["last_date"]),
                    "days": run["points"],
                    "bdh_error": err,
                })

    # ----------------------------------------------------------------------
    # FINAL SUMMARY
    # ----------------------------------------------------------------------
    logger.log("")
    logger.log("=" * 100)
    logger.log("FINAL SUMMARY  (paste this block back to the agent)")
    logger.log("=" * 100)
    logger.log("")
    # Prefer ID_ISIN as the chain field; fall back to SECURITY_DES.
    chain_field_works = {}
    for field in CHAIN_FIELDS:
        slots_with_chain = len({
            r["generic"] for r in rows
            if r["chain_field"] == field and r["chain_index_newest_first"] >= 1
        })
        chain_field_works[field] = slots_with_chain
        logger.log(f"  bdh({field}): recovered a multi-bond chain on "
                   f"{slots_with_chain}/{len(SLOTS)} slots")
    logger.log("")

    best_field = max(CHAIN_FIELDS, key=lambda f: chain_field_works[f])
    if chain_field_works[best_field] == 0:
        logger.log("RESULT: bdh of a reference field does NOT historise the")
        logger.log("underlying on any slot — the generics do not expose their")
        logger.log("OTR chain this way. A different mechanism is needed to find")
        logger.log("the off-the-run bonds; do NOT guess ticker names — report back.")
    else:
        logger.log(f"RESULT: bdh({best_field}) recovers the on-the-run chain on "
                   f"{chain_field_works[best_field]}/{len(SLOTS)} slots.")
        logger.log("Per slot above: 'current OTR' must match the known OTR ISIN")
        logger.log("(correctness check); '1st off-the-run' is the bond A4-4 adds")
        logger.log("to sovereign_cash_bonds.yml so otr_ofr_spread works now. The")
        logger.log("effective_from dates are Bloomberg-verified — usable later for")
        logger.log("the otr_history backfill without guessing any date.")

    try:
        pd.DataFrame(rows).to_csv(csv_path, index=False)
    except Exception as exc:
        logger.log(f"[WARNING] failed to write CSV ({csv_path}): {exc}")

    logger.log("")
    logger.log(f"Text report : {txt_path}")
    logger.log(f"Chain CSV   : {csv_path}")
    logger.save()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        crash_dir = Path.cwd() / (
            f"a4_ofr_resolver_probe_FATAL_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        )
        crash_dir.mkdir(parents=True, exist_ok=True)
        (crash_dir / "fatal_traceback.txt").write_text(
            "Fatal error while running the A4-4 OTR/OFR chain probe.\n\n"
            + traceback.format_exc(),
            encoding="utf-8",
        )
        raise
