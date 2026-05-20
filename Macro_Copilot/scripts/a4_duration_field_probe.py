"""
a4_duration_field_probe.py
==========================

Operator-side Bloomberg verification — work order A4, focused follow-up probe.

WHY THIS SCRIPT EXISTS
----------------------
The real-bond confirmation run (sovereign_bonds_realbond_bloomberg_check.py)
proved that on an individual cash bond, bdh historises YLD_YTM_MID, RISK_MID
(DV01), PX_DIRTY_MID, PX_CLEAN_MID and ASSET_SWAP_SPD_MID — but NOT
DUR_ADJ_MID (modified duration), which came back only as a bdp snapshot.

This probe answers one focused question: **is there ANY Bloomberg mnemonic
that historises modified / adjusted duration via bdh on a cash bond?**

It tests a set of candidate duration field names — via bdh AND bdp — on the
14 real bonds the realbond run already resolved (their /cusip/ query forms are
known to work, so no re-resolution is needed). Two of the candidates are
controls: DUR_ADJ_MID (known bdp-only) and RISK_MID (known to historise via
bdh) — they confirm the probe itself is behaving.

The decisive ticker is the UK 2Y's underlying bond (/cusip/YT0326355, issued
2024-11, ~393 business days of history): a candidate that historises will
show ~380+ points there; one that does not will show 0.

OUTCOME -> A4-4
---------------
  * If a candidate returns a clean bdh series, A4-4's sovereign_cash_bonds.yml
    adds it as the modified-duration target_metric.
  * If every candidate fails bdh (duration is snapshot-only), A4-4 ships DV01
    (RISK_MID) as the risk measure; modified duration is then either derived
    (ModDur = RISK_MID * 100 / PX_DIRTY_MID — an identity, with full P12
    disclosure) or deferred. This probe gathers the evidence; it does not
    decide.

DEPLOYMENT
----------
Self-contained — stdlib + pandas + xbbg. Copy to the Bloomberg terminal host:

    python a4_duration_field_probe.py

Outputs land in ./a4_duration_probe_<TIMESTAMP>/ — a text report + one CSV.
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
LOOKBACK_YEARS = 3

# The 14 real bonds resolved by sovereign_bonds_realbond_bloomberg_check.py.
# These /cusip/ query forms are already confirmed to resolve via bdp/bdh, so
# this probe needs no re-resolution step. `history_hint` flags the seasoned
# bond — the decisive test for "does this field historise".
RESOLVED_BONDS: List[Dict[str, str]] = [
    {"query": "/cusip/91282CQL8", "source_generic": "GT2 Govt",      "country": "US",        "tenor": "2Y",  "history_hint": "new (~2026-04)"},
    {"query": "/cusip/91282CQQ7", "source_generic": "GT10 Govt",     "country": "US",        "tenor": "10Y", "history_hint": "new (~2026-05)"},
    {"query": "/cusip/912810UU0", "source_generic": "GT30 Govt",     "country": "US",        "tenor": "30Y", "history_hint": "new (~2026-05)"},
    {"query": "/cusip/DI7485532", "source_generic": "GTDEM2Y Govt",  "country": "Germany",   "tenor": "2Y",  "history_hint": "new (~2026-04)"},
    {"query": "/cusip/DC6244172", "source_generic": "GTDEM10Y Govt", "country": "Germany",   "tenor": "10Y", "history_hint": "~2026-01 (~94d)"},
    {"query": "/cusip/YT0326355", "source_generic": "GTGBP2Y Govt",  "country": "UK",        "tenor": "2Y",  "history_hint": "SEASONED ~2024-11 (~393d) — decisive test"},
    {"query": "/cusip/YL1322681", "source_generic": "GTGBP10Y Govt", "country": "UK",        "tenor": "10Y", "history_hint": "~2025-09 (~183d)"},
    {"query": "/cusip/DJ9500716", "source_generic": "GTJPY2Y Govt",  "country": "Japan",     "tenor": "2Y",  "history_hint": "new (~2026-05)"},
    {"query": "/cusip/DH8120601", "source_generic": "GTJPY10Y Govt", "country": "Japan",     "tenor": "10Y", "history_hint": "~2026-04 (~33d)"},
    {"query": "/cusip/YL7489823", "source_generic": "GTFRF10Y Govt", "country": "France",    "tenor": "10Y", "history_hint": "~2025-09 (~185d)"},
    {"query": "/cusip/YJ6499398", "source_generic": "GTITL10Y Govt", "country": "Italy",     "tenor": "10Y", "history_hint": "~2025-11 (~144d)"},
    {"query": "/cusip/135087T53", "source_generic": "GTCAD10Y Govt", "country": "Canada",    "tenor": "10Y", "history_hint": "~2025-07 (~219d)"},
    {"query": "/cusip/YR1520430", "source_generic": "GTAUD10Y Govt", "country": "Australia", "tenor": "10Y", "history_hint": "~2025-02 (~331d)"},
    {"query": "/cusip/YI1701444", "source_generic": "GTESP10Y Govt", "country": "Spain",     "tenor": "10Y", "history_hint": "~2026-01 (~84d)"},
]

# The decisive bond — the longest available history.
REFERENCE_BOND_QUERY = "/cusip/YT0326355"

# Candidate modified/adjusted-duration mnemonics. DUR_ADJ_MID and RISK_MID are
# controls (known bdp-only / known bdh-OK respectively).
DURATION_CANDIDATES: List[str] = [
    "DUR_ADJ_MID",        # control — known bdp-only on a real bond
    "DUR_ADJ_BID",
    "DUR_ADJ_ASK",
    "DUR_MID",
    "MOD_DUR",
    "MODIFIED_DURATION",
    "MAC_DUR_MID",        # Macaulay duration (convertible to modified)
    "DUR_ADJ_MTY_MID",
    "RISK_MID",           # positive control — known to historise via bdh
]

# Plausible band for a modified/Macaulay duration value (years).
DURATION_BAND = (0.0, 60.0)


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


def normalize_bdp_output(df: Any, fallback_ticker: str) -> Dict[str, Any]:
    if df is None:
        return {}
    if isinstance(df, pd.Series):
        return {str(k).upper(): clean_scalar(v) for k, v in df.items()}
    if isinstance(df, pd.DataFrame):
        if df.empty:
            return {}
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
    if df is None:
        return pd.DataFrame(columns=["date", "value"])
    if isinstance(df, pd.Series):
        out = df.to_frame(name="value").reset_index()
        out.columns = ["date", "value"]
        return out
    if not isinstance(df, pd.DataFrame) or df.empty:
        return pd.DataFrame(columns=["date", "value"])
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


def plausible_duration(value: Any) -> bool:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return False
    return DURATION_BAND[0] <= v <= DURATION_BAND[1]


# ---------------------------------------------------------------------------
# Main.
# ---------------------------------------------------------------------------
def main() -> None:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path.cwd() / f"a4_duration_probe_{timestamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    txt_path = out_dir / "a4_duration_probe_report.txt"
    detail_csv = out_dir / "a4_duration_probe_detail.csv"

    logger = Logger(txt_path)
    detail_rows: List[Dict[str, Any]] = []

    end_date = date.today() - timedelta(days=1)
    start_date = end_date - timedelta(days=int(LOOKBACK_YEARS * 365.25))
    start_str = start_date.strftime("%Y-%m-%d")
    end_str = end_date.strftime("%Y-%m-%d")

    logger.log("=" * 100)
    logger.log("A4 — HISTORISED MODIFIED-DURATION FIELD PROBE")
    logger.log("=" * 100)
    logger.log(f"Timestamp        : {timestamp}")
    logger.log(f"History window   : {start_str} to {end_str}  ({LOOKBACK_YEARS}y)")
    logger.log(f"Bonds            : {len(RESOLVED_BONDS)} (resolved by the realbond run)")
    logger.log(f"Candidate fields : {len(DURATION_CANDIDATES)}")
    logger.log(f"Decisive bond    : {REFERENCE_BOND_QUERY} (UK 2Y — longest history)")
    logger.log(f"Output directory : {out_dir}")
    logger.log("")
    logger.log("Each candidate is tested via bdh (does it historise?) and bdp (is it")
    logger.log("a real field at all?). DUR_ADJ_MID and RISK_MID are controls.")

    for candidate in DURATION_CANDIDATES:
        logger.log("")
        logger.log("-" * 100)
        logger.log(f"CANDIDATE: {candidate}")
        logger.log("-" * 100)
        bdh_hits = 0
        bdp_hits = 0
        ref_points = 0
        for bond in RESOLVED_BONDS:
            q = bond["query"]
            hist_df, bdh_err = safe_bdh_single(q, candidate, start_str, end_str)
            points, last_val = history_points(hist_df)
            bdp_vals, bdp_err = safe_bdp_batch(q, [candidate])
            bdp_val = bdp_vals.get(candidate.upper())
            bdp_has = bdp_val is not None and not (
                isinstance(bdp_val, str) and bdp_val.strip() == ""
            )
            bdh_has = bdh_err is None and points > 0
            if bdh_has:
                bdh_hits += 1
            if bdp_has:
                bdp_hits += 1
            if q == REFERENCE_BOND_QUERY:
                ref_points = points

            detail_rows.append({
                "candidate_mnemonic": candidate,
                "bond_query": q,
                "source_generic": bond["source_generic"],
                "country": bond["country"],
                "tenor": bond["tenor"],
                "history_hint": bond["history_hint"],
                "bdh_points": points,
                "bdh_last_value": last_val,
                "bdh_value_plausible": plausible_duration(last_val) if last_val is not None else None,
                "bdh_returned_data": bdh_has,
                "bdp_value": clean_scalar(bdp_val),
                "bdp_returned_data": bdp_has,
                "bdh_error": bdh_err,
                "bdp_error": bdp_err,
            })
            logger.log(
                f"  {q:<22} {bond['country']:<10} {bond['tenor']:<4} | "
                f"bdh {'DATA' if bdh_has else 'no  '} points={points:<5} "
                f"last={str(last_val):<12} | bdp {'DATA' if bdp_has else 'no  '}={bdp_val}"
                + (f" | bdh err: {bdh_err}" if bdh_err else "")
            )
        logger.log(
            f"  => {candidate}: bdh data on {bdh_hits}/{len(RESOLVED_BONDS)} bonds | "
            f"bdp data on {bdp_hits}/{len(RESOLVED_BONDS)} | "
            f"decisive bond ({REFERENCE_BOND_QUERY}) bdh points = {ref_points}"
        )

    # ----------------------------------------------------------------------
    # FINAL SUMMARY
    # ----------------------------------------------------------------------
    logger.log("")
    logger.log("=" * 100)
    logger.log("FINAL SUMMARY  (paste this block back to the agent)")
    logger.log("=" * 100)
    logger.log("")
    historising: List[str] = []
    for candidate in DURATION_CANDIDATES:
        rows = [r for r in detail_rows if r["candidate_mnemonic"] == candidate]
        bdh_hits = sum(1 for r in rows if r["bdh_returned_data"])
        bdp_hits = sum(1 for r in rows if r["bdp_returned_data"])
        ref = next((r for r in rows if r["bond_query"] == REFERENCE_BOND_QUERY), None)
        ref_points = ref["bdh_points"] if ref else 0
        ref_plausible = ref["bdh_value_plausible"] if ref else None
        verdict = (
            "HISTORISES — usable as a target_metric"
            if ref_points > 0 and bdh_hits >= len(RESOLVED_BONDS) // 2
            else ("bdp-snapshot only" if bdp_hits > 0 else "not a usable field")
        )
        if verdict.startswith("HISTORISES"):
            historising.append(candidate)
        logger.log(
            f"  {candidate:<20} bdh {bdh_hits}/{len(RESOLVED_BONDS)} | "
            f"bdp {bdp_hits}/{len(RESOLVED_BONDS)} | "
            f"decisive-bond points={ref_points} plausible={ref_plausible} | {verdict}"
        )
    logger.log("")
    if historising:
        logger.log(f"RESULT: a historised duration field EXISTS -> {historising}")
        logger.log("A4-4 adds the verified field as sovereign_cash_bonds.yml's")
        logger.log("modified-duration target_metric.")
    else:
        logger.log("RESULT: NO candidate historises modified duration via bdh.")
        logger.log("A4-4 ships DV01 (RISK_MID) as the risk measure; modified duration")
        logger.log("is then derived (ModDur = RISK_MID * 100 / PX_DIRTY_MID, an")
        logger.log("identity, with full P12 disclosure) or deferred — agent + operator")
        logger.log("decide from this evidence.")

    try:
        pd.DataFrame(detail_rows).to_csv(detail_csv, index=False)
    except Exception as exc:
        logger.log(f"[WARNING] failed to write detail CSV ({detail_csv}): {exc}")

    logger.log("")
    logger.log(f"Text report : {txt_path}")
    logger.log(f"Detail CSV  : {detail_csv}")
    logger.save()


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
