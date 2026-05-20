"""
sovereign_bonds_risk_bloomberg_check.py
=======================================

Operator-side Bloomberg verification for work order A4 — threads A4-1, A4-2 and
A4-3 — the risk-field, asset-swap-spread, and FWCV forward/carry extensions to
the existing `sovereign_bonds.yml` (and `ois.yml`) playbooks. It confirms —
empirically, on a live Bloomberg terminal — which candidate mnemonics actually
return a usable daily series, so the playbooks ship only VERIFIED fields.

WHAT THIS SCRIPT VERIFIES
-------------------------
A4 extends the sovereign-benchmark and OIS playbooks with new `target_metrics`.
Every new mnemonic must be confirmed before it ships. Three sections:

  SECTION 1 — RISK FIELDS (work order A4-1).
    Bond-level risk analytics on the sovereign-benchmark generics
    (`GT10 Govt`, `GTDEM10Y Govt`, …): bid/ask yield, modified duration,
    DV01/risk, dirty price, accrued interest, convexity. For each logical
    field several CANDIDATE mnemonics are probed; the script runs
    bdh(<ticker>, <field>) over a multi-year window and reports whether a
    clean daily series comes back. Per P12 these are INGESTED Bloomberg
    analytics — never recomputed.

  SECTION 2 — ASSET-SWAP SPREAD (work order A4-2).
    The Bloomberg ASW spread on the same sovereign benchmarks. The exact
    mnemonic is vendor-specific and may differ by market, so several
    candidates are probed across all curve families. Per P12, ASW is
    INGESTED, never recomputed.

  SECTION 3 — FWCV FORWARD + CARRY (work order A4-3) — EXPLORATORY.
    A4-3 wants FWCV-derived forward rates and carry / roll-down per
    sovereign curve and per OIS curve. Unlike Sections 1-2, the field names
    here are NOT known — FWCV is a Bloomberg *screen* (Forward Curve
    Analysis), and forward/carry figures may not be exposed as plain
    bdh/bdp fields at all. This section probes a candidate set on both the
    sovereign and OIS universes and reports, honestly, what (if anything)
    returns data. It does NOT decide. If nothing returns data, A4-3 is
    resolved in Phase B by EITHER an operator-determined FWCV data-access
    pattern OR — per P12 (ingest-or-refuse, never recompute) — deferral:
    a two-pillar-compounded forward on sparse benchmarks would be a proxy
    shipped under a standard name, which P12 forbids.

DEPLOYMENT
----------
Self-contained — depends only on stdlib + pandas + xbbg. Copy this single file
to the Bloomberg terminal host and run it there:

    python sovereign_bonds_risk_bloomberg_check.py

Outputs land in ./sovereign_bonds_risk_verification_<TIMESTAMP>/ next to the
working directory: a text report + four CSVs. Return all of them to the agent;
the FINAL SUMMARY block at the end of the text report is the pasteable digest.

NOTHING in the candidate playbook edits is trusted until this script's report
shows a 100% pass. Every new mnemonic in `sovereign_bonds.yml` is marked
`# CANDIDATE` until then; the FWCV target_metrics are not added to either
playbook until this script's SECTION 3 evidence is in.
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
# Risk / ASW / FWCV fields are DAILY series. A 3-year window is ample to
# confirm a mnemonic returns a clean daily series — the playbook itself pulls
# full history from the playbook start_date; this script only proves the field.
LOOKBACK_YEARS = 3
MIN_DAILY_POINTS = 250           # >= ~1y of business days to call a field usable
DAILY_GAP_MAX_DAYS = 7           # consecutive business-daily points sit <= a week apart

# Representative sovereign-benchmark universe — one or more tickers per curve
# family from rates_agent/playbooks/sovereign_bonds.yml. All 9 curve families
# are covered (the brief requires per-benchmark verification for ASW/FWCV); the
# big-four markets carry extra tenors so a short/long contrast is visible.
SOVEREIGN_UNIVERSE: List[Dict[str, str]] = [
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

# Representative OIS universe — one 10Y ticker per curve family from
# rates_agent/playbooks/ois.yml. Used ONLY by SECTION 3 (FWCV forward/carry).
OIS_UNIVERSE: List[Dict[str, str]] = [
    {"ticker": "USOSFR10 Curncy", "curve_family": "USD_SOFR_OIS",  "country": "US",        "tenor": "10Y"},
    {"ticker": "EESWE10 Curncy",  "curve_family": "EUR_ESTR_OIS",  "country": "Euro area", "tenor": "10Y"},
    {"ticker": "BPSWS10 Curncy",  "curve_family": "GBP_SONIA_OIS", "country": "UK",        "tenor": "10Y"},
    {"ticker": "JYSO10 Curncy",   "curve_family": "JPY_OIS",       "country": "Japan",     "tenor": "10Y"},
    {"ticker": "ADSO10 Curncy",   "curve_family": "AUD_OIS",       "country": "Australia", "tenor": "10Y"},
    {"ticker": "CDSO10 Curncy",   "curve_family": "CAD_OIS",       "country": "Canada",    "tenor": "10Y"},
]

# SECTION 1 — risk fields. Each logical field lists CANDIDATE mnemonics; the
# script probes them all and the report names the one that passes across the
# universe. The first candidate is the brief's primary; the rest are
# documented alternates a verification run might prove correct instead.
RISK_FIELD_PROBES: List[Dict[str, Any]] = [
    {"logical": "yield_bid",        "candidates": ["YLD_YTM_BID"]},
    {"logical": "yield_ask",        "candidates": ["YLD_YTM_ASK"]},
    {"logical": "yas_bond_yield",   "candidates": ["YAS_BOND_YLD"]},
    {"logical": "mod_duration",     "candidates": ["MOD_DUR_MID", "DUR_ADJ_MID", "DUR_ADJ_BID", "DUR_ADJ_ASK"]},
    {"logical": "dv01_risk",        "candidates": ["RISK_MID", "YAS_RISK", "DV01"]},
    {"logical": "dirty_price",      "candidates": ["PX_DIRTY_MID", "PX_DIRTY"]},
    {"logical": "accrued_interest", "candidates": ["INT_ACC", "ACCRUED_INTEREST"]},
]

# SECTION 2 — asset-swap spread. The exact mnemonic is vendor-specific and may
# differ by market, hence several candidates probed across all curve families.
ASW_PROBES: List[Dict[str, Any]] = [
    {"logical": "asw_spread", "candidates": [
        "ASSET_SWAP_SPD_MID", "ASW_SPREAD", "BLOOMBERG_ASW", "YAS_ASW_SPREAD",
    ]},
]

# SECTION 3 — FWCV forward + carry. EXPLORATORY: these field names are NOT
# known to be correct. FWCV is a Bloomberg screen; forward/carry figures may
# not be plain data-API fields. The candidates below are probes, not claims —
# the report states honestly what returns data and what does not.
FWCV_PROBES: List[Dict[str, Any]] = [
    {"logical": "forward_rate", "candidates": [
        "FWD_RATE", "FORWARD_RATE", "FWD_CURVE", "FWD_YLD",
    ]},
    {"logical": "carry",        "candidates": ["CARRY", "YAS_CARRY", "CARRY_MID"]},
    {"logical": "roll_down",    "candidates": ["ROLL_DOWN", "ROLLDOWN", "ROLL"]},
    {"logical": "carry_roll",   "candidates": ["CARRY_ROLL", "CARRY_ROLL_DOWN", "TOTAL_CARRY"]},
]


# ---------------------------------------------------------------------------
# Logger — sectioned text report.
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
# Cleaning helpers — copied verbatim from the extractor's normalisation so the
# verification matches what production will see.
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
    """Convert xbbg bdh output to a 2-column ['date', 'value'] frame."""
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


# ---------------------------------------------------------------------------
# Safe Bloomberg call wrappers — capture exceptions, never raise out of a loop.
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# History summary + cadence check.
# ---------------------------------------------------------------------------
def summarize_history(df: pd.DataFrame) -> Dict[str, Any]:
    empty = {
        "non_null_points": 0, "first_date": None, "last_date": None,
        "first_value": None, "last_value": None, "min_value": None,
        "max_value": None, "median_gap_days": None, "daily_cadence": False,
    }
    if df is None or df.empty or "value" not in df.columns:
        return empty
    work = df.copy()
    work["value_clean"] = pd.to_numeric(work["value"], errors="coerce")
    work["date_clean"] = pd.to_datetime(work["date"], errors="coerce")
    work = work.dropna(subset=["value_clean", "date_clean"]).sort_values("date_clean")
    if work.empty:
        return empty
    gaps = work["date_clean"].diff().dropna().dt.days
    median_gap = float(gaps.median()) if not gaps.empty else None
    daily = median_gap is not None and median_gap <= DAILY_GAP_MAX_DAYS
    return {
        "non_null_points": int(len(work)),
        "first_date": clean_scalar(work.iloc[0]["date_clean"]),
        "last_date": clean_scalar(work.iloc[-1]["date_clean"]),
        "first_value": clean_scalar(work.iloc[0]["value_clean"]),
        "last_value": clean_scalar(work.iloc[-1]["value_clean"]),
        "min_value": clean_scalar(work["value_clean"].min()),
        "max_value": clean_scalar(work["value_clean"].max()),
        "median_gap_days": median_gap,
        "daily_cadence": daily,
    }


# ---------------------------------------------------------------------------
# One probe section — bdh every (ticker x candidate field) and tabulate.
# ---------------------------------------------------------------------------
def run_field_section(
    logger: Logger,
    section_title: str,
    universe: List[Dict[str, str]],
    probes: List[Dict[str, Any]],
    start_str: str,
    end_str: str,
    detail_rows: List[Dict[str, Any]],
    rollup_rows: List[Dict[str, Any]],
    section_tag: str,
) -> None:
    """Probe every candidate of every logical field across the universe."""
    logger.log("")
    logger.log("#" * 100)
    logger.log(section_title)
    logger.log("#" * 100)

    for probe in probes:
        logical = probe["logical"]
        logger.log("")
        logger.log("-" * 100)
        logger.log(f"LOGICAL FIELD: {logical}   (candidates: {probe['candidates']})")
        logger.log("-" * 100)

        for candidate in probe["candidates"]:
            passes = 0
            tested = 0
            for row in universe:
                ticker = row["ticker"]
                hist_df, err = safe_bdh_single(ticker, candidate, start_str, end_str)
                summary = summarize_history(hist_df)
                points = summary["non_null_points"]
                ok = (
                    err is None
                    and points >= MIN_DAILY_POINTS
                    and summary["daily_cadence"]
                )
                tested += 1
                if ok:
                    passes += 1

                note = (
                    "OK daily series"
                    if ok
                    else (f"bdh error: {err}" if err
                          else (f"too few points ({points} < {MIN_DAILY_POINTS})"
                                if points < MIN_DAILY_POINTS
                                else f"non-daily cadence (median gap "
                                     f"{summary['median_gap_days']}d)"))
                )

                detail_rows.append({
                    "section": section_tag,
                    "logical_field": logical,
                    "candidate_mnemonic": candidate,
                    "ticker": ticker,
                    "curve_family": row["curve_family"],
                    "country": row["country"],
                    "tenor": row.get("tenor"),
                    "pass": ok,
                    "non_null_points": points,
                    "median_gap_days": summary["median_gap_days"],
                    "first_date": summary["first_date"],
                    "last_date": summary["last_date"],
                    "min_value": summary["min_value"],
                    "max_value": summary["max_value"],
                    "last_value": summary["last_value"],
                    "bdh_error": err,
                    "note": note,
                })
                logger.log(
                    f"  {candidate:<22} {ticker:<16} | pass={'YES' if ok else 'NO ':<3} | "
                    f"points={points:<5} | gap={summary['median_gap_days']} | "
                    f"last={summary['last_value']} | {note}"
                )

            pct = (100.0 * passes / tested) if tested else 0.0
            verdict = (
                "VERIFIED — 100% pass" if passes == tested and tested > 0
                else ("PARTIAL" if passes > 0 else "FAILED — 0% pass")
            )
            rollup_rows.append({
                "section": section_tag,
                "logical_field": logical,
                "candidate_mnemonic": candidate,
                "tickers_tested": tested,
                "tickers_passed": passes,
                "pass_pct": round(pct, 1),
                "verdict": verdict,
            })
            logger.log(
                f"  => {candidate:<22} {passes}/{tested} tickers passed "
                f"({pct:.0f}%) — {verdict}"
            )


# ---------------------------------------------------------------------------
# Main.
# ---------------------------------------------------------------------------
def main() -> None:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path.cwd() / f"sovereign_bonds_risk_verification_{timestamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    txt_path = out_dir / "sovereign_bonds_risk_validation_report.txt"
    risk_csv = out_dir / "sovereign_bonds_risk_validation_risk.csv"
    asw_csv = out_dir / "sovereign_bonds_risk_validation_asw.csv"
    fwcv_csv = out_dir / "sovereign_bonds_risk_validation_fwcv.csv"
    rollup_csv = out_dir / "sovereign_bonds_risk_validation_rollup.csv"

    logger = Logger(txt_path)
    risk_rows: List[Dict[str, Any]] = []
    asw_rows: List[Dict[str, Any]] = []
    fwcv_rows: List[Dict[str, Any]] = []
    rollup_rows: List[Dict[str, Any]] = []

    end_date = date.today() - timedelta(days=1)
    start_date = end_date - timedelta(days=int(LOOKBACK_YEARS * 365.25))
    start_str = start_date.strftime("%Y-%m-%d")
    end_str = end_date.strftime("%Y-%m-%d")

    logger.log("=" * 100)
    logger.log("SOVEREIGN BONDS — RISK / ASW / FWCV MNEMONIC VERIFICATION (work order A4)")
    logger.log("=" * 100)
    logger.log(f"Timestamp        : {timestamp}")
    logger.log(f"History window   : {start_str} to {end_str}  ({LOOKBACK_YEARS}y)")
    logger.log(f"Min daily points : {MIN_DAILY_POINTS}")
    logger.log(f"Sovereign tickers: {len(SOVEREIGN_UNIVERSE)} ({len({r['curve_family'] for r in SOVEREIGN_UNIVERSE})} curve families)")
    logger.log(f"OIS tickers      : {len(OIS_UNIVERSE)} ({len({r['curve_family'] for r in OIS_UNIVERSE})} curve families)")
    logger.log(f"Output directory : {out_dir}")

    # ----------------------------------------------------------------------
    # SECTION 1 — risk fields (A4-1) on the sovereign benchmark universe.
    # ----------------------------------------------------------------------
    run_field_section(
        logger,
        "SECTION 1 — RISK FIELDS (A4-1)  —  sovereign benchmark universe",
        SOVEREIGN_UNIVERSE, RISK_FIELD_PROBES, start_str, end_str,
        risk_rows, rollup_rows, section_tag="risk",
    )

    # ----------------------------------------------------------------------
    # SECTION 2 — asset-swap spread (A4-2) on the sovereign benchmark universe.
    # ----------------------------------------------------------------------
    run_field_section(
        logger,
        "SECTION 2 — ASSET-SWAP SPREAD (A4-2)  —  sovereign benchmark universe",
        SOVEREIGN_UNIVERSE, ASW_PROBES, start_str, end_str,
        asw_rows, rollup_rows, section_tag="asw",
    )

    # ----------------------------------------------------------------------
    # SECTION 3 — FWCV forward + carry (A4-3) — EXPLORATORY.
    # Probed on BOTH the sovereign and OIS universes, via bdh AND bdp (the
    # shape is unknown — a forward/carry figure may be a point-in-time bdp
    # field rather than a daily series). The report does not decide; it
    # gathers evidence for the Phase-B A4-3 decision.
    # ----------------------------------------------------------------------
    logger.log("")
    logger.log("#" * 100)
    logger.log("SECTION 3 — FWCV FORWARD + CARRY (A4-3)  —  EXPLORATORY")
    logger.log("#" * 100)
    logger.log("FWCV is a Bloomberg screen; forward/carry figures may not be exposed")
    logger.log("as bdh/bdp fields at all. The mnemonics below are PROBES, not claims.")
    logger.log("If nothing returns data, A4-3 is resolved in Phase B by an")
    logger.log("operator-determined FWCV access pattern, or deferred per P12 — a")
    logger.log("recomputed forward/carry proxy under a standard name is forbidden.")

    fwcv_universe = (
        [dict(r, universe="sovereign") for r in SOVEREIGN_UNIVERSE]
        + [dict(r, universe="ois") for r in OIS_UNIVERSE]
    )
    for probe in FWCV_PROBES:
        logical = probe["logical"]
        logger.log("")
        logger.log("-" * 100)
        logger.log(f"LOGICAL FIELD: {logical}   (candidates: {probe['candidates']})")
        logger.log("-" * 100)
        for candidate in probe["candidates"]:
            bdh_hits = 0
            bdp_hits = 0
            tested = 0
            for row in fwcv_universe:
                ticker = row["ticker"]
                hist_df, bdh_err = safe_bdh_single(ticker, candidate, start_str, end_str)
                summary = summarize_history(hist_df)
                bdh_points = summary["non_null_points"]
                bdp_values, bdp_err = safe_bdp_batch(ticker, [candidate])
                bdp_value = bdp_values.get(candidate.upper())
                bdp_has = bdp_value is not None and not (
                    isinstance(bdp_value, str) and bdp_value.strip() == ""
                )
                bdh_has = bdh_err is None and bdh_points > 0
                tested += 1
                if bdh_has:
                    bdh_hits += 1
                if bdp_has:
                    bdp_hits += 1

                fwcv_rows.append({
                    "section": "fwcv",
                    "logical_field": logical,
                    "candidate_mnemonic": candidate,
                    "universe": row["universe"],
                    "ticker": ticker,
                    "curve_family": row["curve_family"],
                    "country": row["country"],
                    "bdh_returned_data": bdh_has,
                    "bdh_non_null_points": bdh_points,
                    "bdp_returned_data": bdp_has,
                    "bdp_value": clean_scalar(bdp_value),
                    "bdh_error": bdh_err,
                    "bdp_error": bdp_err,
                })
                logger.log(
                    f"  {candidate:<18} {ticker:<16} | "
                    f"bdh={'DATA' if bdh_has else 'no  '}({bdh_points}) | "
                    f"bdp={'DATA' if bdp_has else 'no  '}({bdp_value}) "
                    + (f"| bdh err: {bdh_err}" if bdh_err else "")
                )
            rollup_rows.append({
                "section": "fwcv",
                "logical_field": logical,
                "candidate_mnemonic": candidate,
                "tickers_tested": tested,
                "tickers_passed": bdh_hits,
                "pass_pct": round(100.0 * bdh_hits / tested, 1) if tested else 0.0,
                "verdict": (f"bdh DATA on {bdh_hits}/{tested}, "
                            f"bdp DATA on {bdp_hits}/{tested}"),
            })
            logger.log(
                f"  => {candidate:<18} bdh data on {bdh_hits}/{tested}, "
                f"bdp data on {bdp_hits}/{tested}"
            )

    # ----------------------------------------------------------------------
    # FINAL SUMMARY
    # ----------------------------------------------------------------------
    logger.log("")
    logger.log("=" * 100)
    logger.log("FINAL SUMMARY  (paste this block back to the agent)")
    logger.log("=" * 100)

    for section_tag, section_label in (
        ("risk", "SECTION 1 — RISK FIELDS (A4-1)"),
        ("asw", "SECTION 2 — ASSET-SWAP SPREAD (A4-2)"),
    ):
        logger.log("")
        logger.log(section_label)
        for r in [x for x in rollup_rows if x["section"] == section_tag]:
            logger.log(
                f"  {r['logical_field']:<18} {r['candidate_mnemonic']:<22} -> "
                f"{r['tickers_passed']}/{r['tickers_tested']} ({r['pass_pct']:.0f}%) "
                f"{r['verdict']}"
            )
        verified = sorted({
            r["candidate_mnemonic"] for r in rollup_rows
            if r["section"] == section_tag and r["verdict"].startswith("VERIFIED")
        })
        logger.log(f"  => mnemonics with 100% pass: {verified or '(none)'}")

    logger.log("")
    logger.log("SECTION 3 — FWCV FORWARD + CARRY (A4-3) — exploratory")
    fwcv_any = [r for r in rollup_rows if r["section"] == "fwcv" and r["tickers_passed"] > 0]
    for r in [x for x in rollup_rows if x["section"] == "fwcv"]:
        logger.log(f"  {r['logical_field']:<14} {r['candidate_mnemonic']:<20} -> {r['verdict']}")
    logger.log("")
    if fwcv_any:
        logger.log("FWCV: at least one candidate returned data — Phase B encodes the")
        logger.log("verified field(s) as target_metrics on sovereign_bonds.yml / ois.yml.")
    else:
        logger.log("FWCV: NO candidate returned data on either universe. Per P12 the")
        logger.log("agent + operator decide in Phase B: either find the FWCV data-API")
        logger.log("access pattern on the terminal, or DEFER A4-3 — never ship a")
        logger.log("recomputed forward/carry proxy under a standard field name.")

    # ----------------------------------------------------------------------
    # Write outputs. out_dir was created up-front; each write is guarded so a
    # single failure cannot lose the others or the text report.
    # ----------------------------------------------------------------------
    for label, rows, path in (
        ("risk", risk_rows, risk_csv),
        ("asw", asw_rows, asw_csv),
        ("fwcv", fwcv_rows, fwcv_csv),
        ("rollup", rollup_rows, rollup_csv),
    ):
        try:
            pd.DataFrame(rows).to_csv(path, index=False)
        except Exception as exc:
            logger.log(f"[WARNING] failed to write {label} CSV ({path}): {exc}")

    logger.log("")
    logger.log(f"Text report : {txt_path}")
    logger.log(f"Risk CSV    : {risk_csv}")
    logger.log(f"ASW CSV     : {asw_csv}")
    logger.log(f"FWCV CSV    : {fwcv_csv}")
    logger.log(f"Rollup CSV  : {rollup_csv}")
    logger.save()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        crash_dir = Path.cwd() / (
            f"sovereign_bonds_risk_verification_FATAL_"
            f"{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        )
        crash_dir.mkdir(parents=True, exist_ok=True)
        (crash_dir / "fatal_traceback.txt").write_text(
            "Fatal error while running sovereign-bonds risk/ASW/FWCV verification.\n\n"
            + traceback.format_exc(),
            encoding="utf-8",
        )
        raise
