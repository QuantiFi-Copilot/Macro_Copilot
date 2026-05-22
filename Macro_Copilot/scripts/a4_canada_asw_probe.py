"""
a4_canada_asw_probe.py — A4 focused follow-up probe.

THE QUESTION
------------
In the duration probe, ASSET_SWAP_SPD_MID historised via bdh for 13/14
markets; Canada returned 0 points (via /isin/CA135087T537). The earlier
realbond run had queried Canada via a different identifier form
(/cusip/135087T53) and reported 4 ASW points. This probe settles whether the
IDENTIFIER FORM (/cusip/ vs /isin/) affects ASW history — or whether Canadian
government bonds genuinely have no historised ASW series.

WHAT IT DOES
------------
Two bonds, each queried via several identifier forms, bdh over a long window:

  * Canada 10Y  — CUSIP 135087T53 / ISIN CA135087T537            <- the question
  * UK 2Y       — CUSIP YT0326355 / ISIN GB00BSQNRC93  (CONTROL)

The UK 2Y is the control: in the duration run it returned ~394 ASW points via
/isin/. If /cusip/ gives the same ~394, the two identifier forms are
equivalent for ASW bdh — which means Canada's zero is a property of the bond,
not the identifier.

For every (bond, form) it bdh's ASSET_SWAP_SPD_MID and YLD_YTM_MID. YLD is a
second control: a form that resolved the bond at all returns a full yield
series, so a form with YLD>0 but ASW=0 isolates ASW as the genuinely-absent
field.

VERDICT
-------
  * If Canada ASW is ~0 on EVERY form -> Canadian govvies have no historised
    ASW series; A4-4 ships ASW for all markets and Canadian bonds simply
    carry no ASW rows.
  * If some form returns a real Canada ASW series (hundreds of points) ->
    A4-4 uses that form for the Canadian universe rows.

Machine-robust: handles both classic-pandas and newer Narwhals xbbg output.

DEPLOYMENT
----------
Self-contained — stdlib + pandas + xbbg. Run on the Bloomberg PC:

    python a4_canada_asw_probe.py

Outputs land in ./a4_canada_asw_probe_<TIMESTAMP>/ — a text report + one CSV.
Return both.
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
CONNECTIVITY_TICKER = "GT10 Govt"

PROBE_BONDS: List[Dict[str, str]] = [
    {"label": "Canada 10Y",       "cusip": "135087T53", "isin": "CA135087T537"},
    {"label": "UK 2Y (control)",  "cusip": "YT0326355", "isin": "GB00BSQNRC93"},
]

PROBE_FIELDS = ["ASSET_SWAP_SPD_MID", "YLD_YTM_MID"]


def identifier_forms(cusip: str, isin: str) -> List[str]:
    return [
        f"/cusip/{cusip}",
        f"/isin/{isin}",
        f"{cusip} Govt",
        f"{isin} Govt",
    ]


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
    """Newer xbbg returns a Narwhals frame; unwrap to pandas. No-op for
    classic pandas (or None)."""
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


def history_points(df: pd.DataFrame) -> Tuple[int, Any]:
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
# Main.
# ---------------------------------------------------------------------------
def main() -> None:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path.cwd() / f"a4_canada_asw_probe_{timestamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    txt_path = out_dir / "a4_canada_asw_probe_report.txt"
    csv_path = out_dir / "a4_canada_asw_probe_detail.csv"

    logger = Logger(txt_path)
    rows: List[Dict[str, Any]] = []

    end_date = date.today() - timedelta(days=1)
    start_date = end_date - timedelta(days=int(LOOKBACK_YEARS * 365.25))
    start_str = start_date.strftime("%Y-%m-%d")
    end_str = end_date.strftime("%Y-%m-%d")

    logger.log("=" * 96)
    logger.log("A4 — CANADA ASW IDENTIFIER-FORM PROBE")
    logger.log("=" * 96)
    logger.log(f"Timestamp      : {timestamp}")
    logger.log(f"History window : {start_str} to {end_str}  ({LOOKBACK_YEARS}y)")
    logger.log(f"Output dir     : {out_dir}")

    # ---- preflight (plain ticker; confirms live terminal + normalisation) --
    logger.log("")
    logger.log("PREFLIGHT — Bloomberg connectivity:")
    pf_vals, pf_err = safe_bdp_batch(CONNECTIVITY_TICKER, ["SECURITY_DES"])
    if not pf_vals.get("SECURITY_DES"):
        logger.log(f"  *** FAILED — bdp({CONNECTIVITY_TICKER}) returned nothing"
                   + (f" [{pf_err}]" if pf_err else "") + ". Terminal not live.")
        logger.save()
        raise RuntimeError("Preflight failed — start/log into the Bloomberg terminal.")
    logger.log(f"  OK — {CONNECTIVITY_TICKER} -> {pf_vals.get('SECURITY_DES')}")

    # ---- probe -------------------------------------------------------------
    for bond in PROBE_BONDS:
        logger.log("")
        logger.log("#" * 96)
        logger.log(f"{bond['label']}   CUSIP={bond['cusip']}  ISIN={bond['isin']}")
        logger.log("#" * 96)
        for form in identifier_forms(bond["cusip"], bond["isin"]):
            line_parts: List[str] = []
            for field in PROBE_FIELDS:
                hist, err = safe_bdh_single(form, field, start_str, end_str)
                points, last = history_points(hist)
                rows.append({
                    "bond": bond["label"],
                    "identifier_form": form,
                    "field": field,
                    "bdh_points": points,
                    "bdh_last_value": last,
                    "bdh_error": err,
                })
                line_parts.append(
                    f"{field}={points}pts(last={last})"
                    + (f"[err {err}]" if err else "")
                )
            logger.log(f"  {form:<26} | " + " | ".join(line_parts))

    # ---- verdict -----------------------------------------------------------
    logger.log("")
    logger.log("=" * 96)
    logger.log("FINAL SUMMARY  (paste this block back to the agent)")
    logger.log("=" * 96)
    for bond in PROBE_BONDS:
        logger.log("")
        logger.log(f"{bond['label']}:")
        for field in PROBE_FIELDS:
            brows = [r for r in rows if r["bond"] == bond["label"]
                     and r["field"] == field]
            by_form = ", ".join(
                f"{r['identifier_form'].split('/')[1] if r['identifier_form'].startswith('/') else r['identifier_form']}"
                f"={r['bdh_points']}"
                for r in brows
            )
            best = max((r["bdh_points"] for r in brows), default=0)
            logger.log(f"  {field:<20} max={best:<5} | by form: {by_form}")

    logger.log("")
    can_asw = [r["bdh_points"] for r in rows
               if r["bond"] == "Canada 10Y" and r["field"] == "ASSET_SWAP_SPD_MID"]
    can_asw_max = max(can_asw, default=0)
    if can_asw_max >= 100:
        logger.log("VERDICT: Canada ASW DOES historise via at least one identifier")
        logger.log("form — A4-4 uses that form for the Canadian universe rows.")
    else:
        logger.log("VERDICT: Canada ASW does NOT historise on ANY identifier form")
        logger.log(f"(max {can_asw_max} points). Canadian govvies have no historised")
        logger.log("ASW series — A4-4 ships ASW for all markets and Canadian bonds")
        logger.log("simply carry no ASW rows. (The UK 2Y control above should show a")
        logger.log("full ASW series on the working forms, proving the probe + the")
        logger.log("forms are sound.)")

    try:
        pd.DataFrame(rows).to_csv(csv_path, index=False)
    except Exception as exc:
        logger.log(f"[WARNING] failed to write CSV ({csv_path}): {exc}")

    logger.log("")
    logger.log(f"Text report : {txt_path}")
    logger.log(f"Detail CSV  : {csv_path}")
    logger.save()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        crash_dir = Path.cwd() / (
            f"a4_canada_asw_probe_FATAL_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        )
        crash_dir.mkdir(parents=True, exist_ok=True)
        (crash_dir / "fatal_traceback.txt").write_text(
            "Fatal error while running the A4 Canada ASW probe.\n\n"
            + traceback.format_exc(),
            encoding="utf-8",
        )
        raise
