"""
inflation_references_bloomberg_check.py
=======================================

Operator-side Bloomberg verification for the CANDIDATE `inflation_references`
playbook (work order B3 of the data-first roadmap). It confirms — empirically,
on a live Bloomberg terminal — which candidate mnemonics actually return data,
so the playbook ships only VERIFIED fields.

WHAT THIS SCRIPT VERIFIES
-------------------------
B3's playbook covers the headline inflation REFERENCE INDICES that
inflation-linked bonds and inflation swaps settle against — US CPI-U, UK RPI,
Euro-area HICP, France CPI, Canada CPI, Japan CPI. Two things must be checked:

  1. INDEX LEVELS (the core of B3 — known to be clean Bloomberg data).
     For each logical index, several CANDIDATE tickers are probed. For each
     candidate the script runs bdh(<ticker>, PX_LAST) over a multi-year window
     and confirms a monthly time series comes back (non-null point count,
     monthly cadence, plausible level range). Multiple candidates per logical
     index are probed so the right ticker is discovered even if the first
     guess is wrong — the operator's terminal is the source of truth.

  2. SEASONAL-ADJUSTMENT FACTORS (the open question — B3 fork 1).
     full_plan.md names the source of CPI seasonal factors as the statistical
     agencies (FRB / BLS / Eurostat / ONS), not Bloomberg, and flags it as an
     open question. The decision (per the operator) is: probe Bloomberg
     thoroughly first, see what it actually exposes, and decide AFTER this run
     whether seasonal factors ship in B3 or are deferred to a non-Bloomberg
     follow-up. This script therefore probes several candidate representations
     of seasonal factors — dedicated seasonal-factor tickers, the SA index
     alongside the NSA index (a seasonal factor is derivable as SA/NSA), and
     candidate bdp fields — and reports what returns data. It does NOT decide;
     it gathers the evidence.

DEPLOYMENT
----------
Self-contained — depends only on stdlib + pandas + xbbg. Copy this single file
to the Bloomberg terminal host and run it there:

    python inflation_references_bloomberg_check.py

Outputs land in ./inflation_references_verification_<TIMESTAMP>/ next to the
working directory: a text report + three CSVs. Return all four to the agent;
the FINAL SUMMARY block at the end of the text report is the pasteable digest.

  3. INDEXATION-LAG EVIDENCE (B3 fork 2 — the indexation conventions).
     SECTION 3 pulls the indexation lag + reference-index linkage from
     representative inflation-linked BONDS. Bloomberg exposes these as the
     reference fields REFERENCE_INDEX and INFLATION_LAG on a linker bond
     (NOT on a CPI index ticker — a CPI index does not carry linker
     conventions). The lag is a per-BOND fact (old-style UK gilts use an
     8-month lag, new-style ones a 3-month lag, both settling the SAME RPI
     index), so it does NOT go onto inflation_references.yml: this section
     captures it purely as EVIDENCE for the future inflation_indexed_bonds.yml
     revision, where INFLATION_LAG is ingested per bond. The daily-index
     INTERPOLATION rule is NOT probed — Bloomberg does not expose it as a
     reference field, so it is deferred as technical debt
     (docs/technical_debt.md item #25), not shipped in B3.

OUTPUTS
-------
  inflation_references_validation_report.txt    — sectioned narrative + FINAL SUMMARY
  inflation_references_validation_index_level.csv   — one row per (logical index x candidate ticker)
  inflation_references_validation_index_summary.csv — one row per logical index (best candidate, ready flag)
  inflation_references_validation_seasonal.csv      — one row per seasonal-factor candidate probe
  inflation_references_validation_metadata.csv      — one row per representative linker bond (REFERENCE_INDEX + INFLATION_LAG)

NOTHING in the candidate playbook is trusted until this script's report shows
a 100% pass. Every mnemonic in tmp/tmp_playbooks/inflation_references.yml is
marked `# CANDIDATE` until then. inflation_references.yml ships index levels
only — the indexation lag (Section 3) is per-bond evidence for the future
inflation_indexed_bonds.yml revision, and the interpolation convention is
deferred as technical debt (docs/technical_debt.md item #25).
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
# Inflation reference indices are MONTHLY. A long window confirms history depth.
LOOKBACK_YEARS = 20
MIN_MONTHLY_POINTS = 24          # >= 2 years of monthly points to call a ticker usable
MONTHLY_GAP_MIN_DAYS = 25        # plausible spacing between consecutive monthly points
MONTHLY_GAP_MAX_DAYS = 35

PRICE_FIELD = "PX_LAST"          # an index ticker carries its level in PX_LAST
REFERENCE_FIELDS = ["SECURITY_DES", "NAME", "INDX_FREQ", "COUNTRY"]

# Candidate inflation REFERENCE INDICES. Each logical index lists several
# CANDIDATE tickers — the script probes them all and reports which return a
# clean monthly series. These tickers are CANDIDATES, not verified truth:
# the operator's terminal confirms or refutes each one.
INFLATION_REFERENCE_UNIVERSE: Dict[str, Dict[str, Any]] = {
    "US_CPI_URBAN_NSA": {
        "country": "US",
        "currency": "USD",
        "inflation_index_family": "US_CPI_URBAN",
        "description": "US CPI Urban Consumers, NOT seasonally adjusted "
                       "(the index US TIPS and USD ZCIS settle against)",
        "candidate_tickers": ["CPURNSA Index", "CPI INDX Index"],
    },
    "US_CPI_URBAN_SA": {
        "country": "US",
        "currency": "USD",
        "inflation_index_family": "US_CPI_URBAN",
        "description": "US CPI Urban Consumers, seasonally adjusted "
                       "(probed so the SA/NSA pair can yield seasonal factors)",
        "candidate_tickers": ["CPI INDX Index", "CPSEINDX Index"],
    },
    "UK_RPI": {
        "country": "UK",
        "currency": "GBP",
        "inflation_index_family": "UK_RPI",
        "description": "UK Retail Prices Index, all items "
                       "(the index UK linkers and GBP ZCIS settle against)",
        "candidate_tickers": ["UKRPI Index", "RPI Index"],
    },
    "EU_HICP_EX_TOBACCO": {
        "country": "Euro area",
        "currency": "EUR",
        "inflation_index_family": "EU_HICP",
        "description": "Euro-area HICP excluding tobacco "
                       "(the index EUR ZCIS settle against)",
        "candidate_tickers": ["CPTFEMU Index", "EUR HICP Index"],
    },
    "EU_HICP_ALL_ITEMS": {
        "country": "Euro area",
        "currency": "EUR",
        "inflation_index_family": "EU_HICP",
        "description": "Euro-area HICP, all items",
        "candidate_tickers": ["CPALEMU Index", "ECCPEMU Index"],
    },
    "FR_CPI_EX_TOBACCO": {
        "country": "France",
        "currency": "EUR",
        "inflation_index_family": "FR_CPI",
        "description": "France CPI excluding tobacco "
                       "(the index French OATi linkers settle against)",
        "candidate_tickers": ["FRCPXTOB Index", "FRCPIXT Index"],
    },
    "CA_CPI": {
        "country": "Canada",
        "currency": "CAD",
        "inflation_index_family": "CAN_CPI",
        "description": "Canada CPI, all items "
                       "(the index Canadian Real Return Bonds settle against)",
        "candidate_tickers": ["CACPI Index", "CANNCPI Index"],
    },
    "JP_CPI": {
        "country": "Japan",
        "currency": "JPY",
        "inflation_index_family": "JP_CPI",
        "description": "Japan CPI reference index for JGBi. NOTE: the first "
                       "verification run found NO working ticker among the "
                       "JNCPI* / JCPN* nationwide-CPI candidates (`JNCPI "
                       "Index` resolved via bdp but bdh PX_LAST returned 0 "
                       "points). The operator then read REFERENCE_INDEX off a "
                       "live JGBi linker (JGBI 0.6 03/10/36 Govt) and it "
                       "returned `JCPNJGBI Index` — the index JGBi actually "
                       "settles against. That verified ticker is the sole "
                       "candidate below; this run confirms its bdh history.",
        "candidate_tickers": ["JCPNJGBI Index"],
    },
}

# Candidate SEASONAL-FACTOR representations to probe. Bloomberg's exposure of
# CPI seasonal factors is uncertain — this section probes several shapes and
# reports what (if anything) returns data. Each entry: a label + a ticker +
# the field to pull + how to read the result.
#
#   * "sa_index"  — a seasonally-adjusted index ticker. If both the SA and the
#     NSA index verify, the seasonal factor is derivable as SA / NSA.
#   * "direct"    — a candidate dedicated seasonal-factor ticker.
SEASONAL_FACTOR_CANDIDATES: List[Dict[str, str]] = [
    {"label": "US CPI-U SA index (for SA/NSA ratio)", "ticker": "CPI INDX Index",
     "field": PRICE_FIELD, "kind": "sa_index"},
    {"label": "US CPI-U SA index alt", "ticker": "CPSEINDX Index",
     "field": PRICE_FIELD, "kind": "sa_index"},
    {"label": "US CPI seasonal factor (direct candidate)", "ticker": "CPI SEAS Index",
     "field": PRICE_FIELD, "kind": "direct"},
    {"label": "US CPI seasonal adjustment factor (direct candidate)",
     "ticker": "CPURSADJ Index", "field": PRICE_FIELD, "kind": "direct"},
    # NOTE: the first run probed "CPALEMU Index" as a candidate Euro HICP SA
    # index — but its SECURITY_DES came back "Euro Area MUICP All Items NSA"
    # (it is the NSA all-items index, already covered in Section 1, not an SA
    # index). It is removed here so the seasonal probe is not misleading.
]

# Representative inflation-linked BONDS whose indexation convention SECTION 3
# probes. The verification run confirmed the indexation lag is NOT exposed on
# a CPI index ticker (a CPI index does not carry linker conventions). The lag
# + the index linkage ARE exposed as the reference fields INFLATION_LAG and
# REFERENCE_INDEX on the LINKER BOND.
#
# SCOPE: the lag does NOT go onto inflation_references.yml — it is a per-BOND
# fact (old-style UK gilts use an 8-month lag, new-style ones a 3-month lag,
# both settling the SAME RPI index). SECTION 3 captures the lag only as
# EVIDENCE for the future inflation_indexed_bonds.yml revision, where
# INFLATION_LAG is ingested per bond. inflation_references.yml itself ships
# index levels only.
#
# Bond tickers use DECIMAL coupons (e.g. `TII 0.125 ...`, not `TII 0 1/8 ...`).
# The first verification run used the terminal's whole-fraction coupon
# notation and the three US/UK bonds failed to resolve via the API, while
# every decimal/whole-coupon bond resolved — so the fractional-coupon strings
# are corrected to decimal here. If a description ticker still does not
# resolve, the operator substitutes the bond's ISIN.
#
# `expected_reference_index` lets the script cross-check that each market's
# linker settles against exactly the CPI index ticker Section 1 verified.
#
# The daily-index INTERPOLATION rule is deliberately NOT probed here:
# Bloomberg exposes no reference field for it. It is deferred as technical
# debt (docs/technical_debt.md item #25).
LINKER_BOND_CONVENTION_PROBES: List[Dict[str, str]] = [
    {"label": "US TIPS",          "market": "US",
     "bond_ticker": "TII 0.125 01/15/32 Govt",  "expected_reference_index": "CPURNSA"},
    # Both UK gilts below are OLD-STYLE (high real coupons -> 8-month lag).
    # New-style 3-month-lag UK linkers are captured per bond when
    # inflation_indexed_bonds.yml is revised — not spot-checked here.
    {"label": "UK linker (2024)", "market": "UK",
     "bond_ticker": "UKTI 2.5 07/17/24 Govt",   "expected_reference_index": "UKRPI"},
    {"label": "UK linker (2030)", "market": "UK",
     "bond_ticker": "UKTI 4.125 07/22/30 Govt", "expected_reference_index": "UKRPI"},
    {"label": "Germany linker",   "market": "Germany",
     "bond_ticker": "DBRI 0.1 04/15/33 Govt",   "expected_reference_index": "CPTFEMU"},
    {"label": "France OAT€i",     "market": "France",
     "bond_ticker": "FRTR 0.1 07/25/36 Govt",   "expected_reference_index": "CPTFEMU"},
    {"label": "France OATi",      "market": "France",
     "bond_ticker": "FRTR 0.1 03/01/36 Govt",   "expected_reference_index": "FRCPXTOB"},
    {"label": "Canada RRB",       "market": "Canada",
     "bond_ticker": "CANRRB 3 12/01/36 Govt",   "expected_reference_index": "CACPI"},
    {"label": "Japan JGBi",       "market": "Japan",
     "bond_ticker": "JGBI 0.6 03/10/36 Govt",   "expected_reference_index": "JCPNJGBI"},
]

# The VERIFIED reference-field mnemonics pulled from each linker bond.
# REFERENCE_INDEX names the inflation index the linker settles against;
# INFLATION_LAG carries the indexation lag. SECURITY_DES is pulled alongside
# only to confirm the bond ticker resolved.
LINKER_CONVENTION_FIELDS: List[str] = [
    "SECURITY_DES",
    "REFERENCE_INDEX",
    "INFLATION_LAG",
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
        "max_value": None, "median_gap_days": None, "monthly_cadence": False,
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
    monthly = (
        median_gap is not None
        and MONTHLY_GAP_MIN_DAYS <= median_gap <= MONTHLY_GAP_MAX_DAYS
    )
    return {
        "non_null_points": int(len(work)),
        "first_date": clean_scalar(work.iloc[0]["date_clean"]),
        "last_date": clean_scalar(work.iloc[-1]["date_clean"]),
        "first_value": clean_scalar(work.iloc[0]["value_clean"]),
        "last_value": clean_scalar(work.iloc[-1]["value_clean"]),
        "min_value": clean_scalar(work["value_clean"].min()),
        "max_value": clean_scalar(work["value_clean"].max()),
        "median_gap_days": median_gap,
        "monthly_cadence": monthly,
    }


# ---------------------------------------------------------------------------
# Main.
# ---------------------------------------------------------------------------
def main() -> None:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path.cwd() / f"inflation_references_verification_{timestamp}"
    # Create the output directory UP FRONT — before any file is written. The
    # CSV writes at the end of main() land here; without this mkdir the first
    # to_csv() raises OSError("non-existent directory").
    out_dir.mkdir(parents=True, exist_ok=True)
    txt_path = out_dir / "inflation_references_validation_report.txt"
    index_csv = out_dir / "inflation_references_validation_index_level.csv"
    summary_csv = out_dir / "inflation_references_validation_index_summary.csv"
    seasonal_csv = out_dir / "inflation_references_validation_seasonal.csv"
    metadata_csv = out_dir / "inflation_references_validation_metadata.csv"

    logger = Logger(txt_path)
    index_rows: List[Dict[str, Any]] = []
    summary_rows: List[Dict[str, Any]] = []
    seasonal_rows: List[Dict[str, Any]] = []
    metadata_rows: List[Dict[str, Any]] = []

    end_date = date.today() - timedelta(days=1)
    start_date = end_date - timedelta(days=int(LOOKBACK_YEARS * 365.25))
    start_str = start_date.strftime("%Y-%m-%d")
    end_str = end_date.strftime("%Y-%m-%d")

    logger.log("=" * 100)
    logger.log("INFLATION REFERENCES — BLOOMBERG MNEMONIC VERIFICATION (work order B3)")
    logger.log("=" * 100)
    logger.log(f"Timestamp        : {timestamp}")
    logger.log(f"History window   : {start_str} to {end_str}  ({LOOKBACK_YEARS}y)")
    logger.log(f"Price field      : {PRICE_FIELD}")
    logger.log(f"Min monthly pts  : {MIN_MONTHLY_POINTS}")
    logger.log(f"Logical indices  : {len(INFLATION_REFERENCE_UNIVERSE)}")
    logger.log(f"Seasonal probes  : {len(SEASONAL_FACTOR_CANDIDATES)}")
    logger.log(f"Convention probe : {len(LINKER_BOND_CONVENTION_PROBES)} linker bond(s) "
               f"-> {LINKER_CONVENTION_FIELDS}")
    logger.log(f"Output directory : {out_dir}")
    logger.log()

    # ----------------------------------------------------------------------
    # SECTION 1 — index-level verification
    # ----------------------------------------------------------------------
    logger.log("#" * 100)
    logger.log("SECTION 1 — INFLATION REFERENCE INDEX LEVELS")
    logger.log("#" * 100)

    for logical_name, cfg in INFLATION_REFERENCE_UNIVERSE.items():
        logger.log("")
        logger.log("-" * 100)
        logger.log(f"{logical_name} | country={cfg['country']} | currency={cfg['currency']} "
                   f"| family={cfg['inflation_index_family']}")
        logger.log(f"  {cfg['description']}")
        logger.log("-" * 100)

        best_ticker: Optional[str] = None
        best_points = -1

        for ticker in cfg["candidate_tickers"]:
            hist_df, hist_err = safe_bdh_single(ticker, PRICE_FIELD, start_str, end_str)
            summary = summarize_history(hist_df)
            ref_values, ref_err = safe_bdp_batch(ticker, REFERENCE_FIELDS)

            points = summary["non_null_points"]
            passed = (
                hist_err is None
                and points >= MIN_MONTHLY_POINTS
                and summary["monthly_cadence"]
            )
            if passed and points > best_points:
                best_points = points
                best_ticker = ticker

            note = (
                "OK monthly index series"
                if passed
                else (f"bdh error: {hist_err}" if hist_err
                      else (f"too few points ({points} < {MIN_MONTHLY_POINTS})"
                            if points < MIN_MONTHLY_POINTS
                            else f"non-monthly cadence (median gap "
                                 f"{summary['median_gap_days']}d)"))
            )

            index_rows.append({
                "logical_index": logical_name,
                "country": cfg["country"],
                "candidate_ticker": ticker,
                "price_field": PRICE_FIELD,
                "pass": passed,
                "non_null_points": points,
                "median_gap_days": summary["median_gap_days"],
                "monthly_cadence": summary["monthly_cadence"],
                "first_date": summary["first_date"],
                "last_date": summary["last_date"],
                "first_value": summary["first_value"],
                "last_value": summary["last_value"],
                "min_value": summary["min_value"],
                "max_value": summary["max_value"],
                "security_des": ref_values.get("SECURITY_DES"),
                "name": ref_values.get("NAME"),
                "indx_freq": ref_values.get("INDX_FREQ"),
                "bdh_error": hist_err,
                "bdp_error": ref_err,
                "note": note,
            })

            logger.log(
                f"  {ticker:<22} | pass={'YES' if passed else 'NO ':<3} | "
                f"points={points:<4} | gap={summary['median_gap_days']} | "
                f"range=[{summary['first_date']} .. {summary['last_date']}] | "
                f"last={summary['last_value']} | {note}"
            )
            if ref_values.get("SECURITY_DES"):
                logger.log(f"      SECURITY_DES: {ref_values.get('SECURITY_DES')}")

        index_ready = best_ticker is not None
        summary_rows.append({
            "logical_index": logical_name,
            "country": cfg["country"],
            "currency": cfg["currency"],
            "inflation_index_family": cfg["inflation_index_family"],
            "candidates_tested": len(cfg["candidate_tickers"]),
            "best_ticker": best_ticker,
            "best_ticker_points": best_points if best_points >= 0 else None,
            "index_ready_for_playbook": index_ready,
        })
        logger.log(f"  => best candidate: {best_ticker or 'NONE — all candidates failed'}"
                   f"  (index_ready={index_ready})")

    # ----------------------------------------------------------------------
    # SECTION 2 — seasonal-factor probe
    # ----------------------------------------------------------------------
    logger.log("")
    logger.log("#" * 100)
    logger.log("SECTION 2 — SEASONAL-FACTOR CANDIDATE PROBE")
    logger.log("#" * 100)
    logger.log("Probing what Bloomberg exposes for CPI seasonal factors. The B3")
    logger.log("decision (ship in B3 vs defer to a non-Bloomberg follow-up) is made")
    logger.log("AFTER this run, from the evidence below — this script only gathers it.")
    logger.log("")

    for cand in SEASONAL_FACTOR_CANDIDATES:
        hist_df, hist_err = safe_bdh_single(
            cand["ticker"], cand["field"], start_str, end_str
        )
        summary = summarize_history(hist_df)
        points = summary["non_null_points"]
        returned_data = hist_err is None and points > 0

        seasonal_rows.append({
            "label": cand["label"],
            "kind": cand["kind"],
            "ticker": cand["ticker"],
            "field": cand["field"],
            "returned_data": returned_data,
            "non_null_points": points,
            "median_gap_days": summary["median_gap_days"],
            "monthly_cadence": summary["monthly_cadence"],
            "first_date": summary["first_date"],
            "last_date": summary["last_date"],
            "last_value": summary["last_value"],
            "min_value": summary["min_value"],
            "max_value": summary["max_value"],
            "bdh_error": hist_err,
        })
        logger.log(
            f"  [{cand['kind']:<9}] {cand['ticker']:<22} {cand['field']:<10} | "
            f"returned_data={'YES' if returned_data else 'NO '} | points={points:<4} | "
            f"last={summary['last_value']} | "
            f"{('err: ' + hist_err) if hist_err else 'ok'}"
        )

    # ----------------------------------------------------------------------
    # SECTION 3 — indexation-convention probe on representative linker bonds
    # ----------------------------------------------------------------------
    logger.log("")
    logger.log("#" * 100)
    logger.log("SECTION 3 — INDEXATION-CONVENTION PROBE (linker bonds)")
    logger.log("#" * 100)
    logger.log("Bloomberg exposes the indexation lag + reference-index linkage on the")
    logger.log("LINKER BOND, not on the CPI index ticker. This section pulls the two")
    logger.log("verified mnemonics — REFERENCE_INDEX and INFLATION_LAG — from one or")
    logger.log("two representative linker bonds per market. The lag is a per-bond")
    logger.log("fact, so it does NOT go onto inflation_references.yml; it is captured")
    logger.log("here as EVIDENCE for the future inflation_indexed_bonds.yml revision")
    logger.log("(per-bond INFLATION_LAG ingestion). The interpolation rule is NOT")
    logger.log("probed — Bloomberg exposes no reference field for it; it is deferred")
    logger.log("as technical debt (docs/technical_debt.md item #25).")
    logger.log("")

    for probe in LINKER_BOND_CONVENTION_PROBES:
        bond = probe["bond_ticker"]
        values, err = safe_bdp_batch(bond, LINKER_CONVENTION_FIELDS)
        security_des = values.get("SECURITY_DES")
        reference_index = values.get("REFERENCE_INDEX")
        inflation_lag = values.get("INFLATION_LAG")
        bond_resolved = security_des is not None

        expected = probe["expected_reference_index"].upper()
        got = str(reference_index).upper() if reference_index is not None else ""
        ref_index_match = bool(got) and (expected in got or got in expected)

        metadata_rows.append({
            "label": probe["label"],
            "market": probe["market"],
            "bond_ticker": bond,
            "bond_resolved": bond_resolved,
            "security_des": clean_scalar(security_des),
            "reference_index": clean_scalar(reference_index),
            "expected_reference_index": probe["expected_reference_index"],
            "reference_index_match": ref_index_match,
            "inflation_lag": clean_scalar(inflation_lag),
            "bdp_error": err,
        })
        logger.log(
            f"  {probe['label']:<18} | {bond:<28} | "
            f"resolved={'YES' if bond_resolved else 'NO '} | "
            f"REFERENCE_INDEX={reference_index} "
            f"(expect {probe['expected_reference_index']}, "
            f"{'match' if ref_index_match else 'MISMATCH'}) | "
            f"INFLATION_LAG={inflation_lag}"
            + (f" | bdp error: {err}" if err else "")
        )
        if security_des:
            logger.log(f"      SECURITY_DES: {security_des}")

    # ----------------------------------------------------------------------
    # FINAL SUMMARY
    # ----------------------------------------------------------------------
    logger.log("")
    logger.log("=" * 100)
    logger.log("FINAL SUMMARY  (paste this block back to the agent)")
    logger.log("=" * 100)
    ready = [r for r in summary_rows if r["index_ready_for_playbook"]]
    not_ready = [r for r in summary_rows if not r["index_ready_for_playbook"]]
    logger.log(f"Index levels — ready : {len(ready)} / {len(summary_rows)}")
    for r in summary_rows:
        logger.log(
            f"  {r['logical_index']:<22} -> "
            f"{r['best_ticker'] or 'NO WORKING CANDIDATE'}"
            + (f"  ({r['best_ticker_points']} pts)" if r['best_ticker'] else "")
        )
    if not_ready:
        logger.log("")
        logger.log("Indices with NO working candidate (need a corrected ticker):")
        for r in not_ready:
            logger.log(f"  - {r['logical_index']} ({r['country']})")
    logger.log("")
    seasonal_hits = [r for r in seasonal_rows if r["returned_data"]]
    logger.log(f"Seasonal-factor probe : {len(seasonal_hits)} / {len(seasonal_rows)} "
               "candidate(s) returned data")
    for r in seasonal_rows:
        logger.log(
            f"  [{r['kind']:<9}] {r['ticker']:<22} -> "
            f"{'DATA' if r['returned_data'] else 'no data'}"
            + (f"  ({r['non_null_points']} pts)" if r['returned_data'] else "")
        )
    logger.log("")
    logger.log("Seasonal-factor decision is made by the agent + operator AFTER this")
    logger.log("run: if a candidate returned a clean monthly series it can ship in")
    logger.log("B3; if none did, seasonal factors are deferred to a non-Bloomberg")
    logger.log("follow-up and B3 ships index levels + conventions only.")

    logger.log("")
    ref_hits = [r for r in metadata_rows if r["reference_index"] is not None]
    lag_hits = [r for r in metadata_rows if r["inflation_lag"] is not None]
    mismatches = [
        r for r in metadata_rows
        if r["reference_index"] is not None and not r["reference_index_match"]
    ]
    logger.log(f"Indexation-convention probe : {len(metadata_rows)} linker bond(s) probed")
    logger.log(f"  REFERENCE_INDEX returned : {len(ref_hits)} / {len(metadata_rows)}")
    logger.log(f"  INFLATION_LAG returned   : {len(lag_hits)} / {len(metadata_rows)}")
    for r in metadata_rows:
        logger.log(
            f"  {r['label']:<18} {r['bond_ticker']:<28} -> "
            f"REFERENCE_INDEX={r['reference_index']} "
            f"INFLATION_LAG={r['inflation_lag']}"
            + ("" if r["reference_index_match"] else "  [REF-INDEX MISMATCH]")
        )
    if mismatches:
        logger.log("")
        logger.log("REFERENCE_INDEX mismatches — a linker settled against an index")
        logger.log("other than the one Section 1 verified. Reconcile before Phase B:")
        for r in mismatches:
            logger.log(f"  - {r['label']}: got {r['reference_index']} "
                       f"(expected {r['expected_reference_index']})")
    logger.log("")
    logger.log("index_lag: the INFLATION_LAG values above are EVIDENCE for the future")
    logger.log("inflation_indexed_bonds.yml revision (per-bond INFLATION_LAG) — they")
    logger.log("do NOT go onto inflation_references.yml, which ships index levels only.")
    logger.log("interpolation: deferred as technical debt (docs/technical_debt.md")
    logger.log("item #25) — Bloomberg exposes no reference field for the daily-index")
    logger.log("interpolation rule; it is added in a later work order, not in B3.")

    # ----------------------------------------------------------------------
    # Write outputs. out_dir was created up-front (top of main()), so these
    # writes have a directory to land in. Each CSV write is guarded so a
    # single failure cannot lose the others or the text report.
    # ----------------------------------------------------------------------
    for label, rows, path in (
        ("index-level", index_rows, index_csv),
        ("index-summary", summary_rows, summary_csv),
        ("seasonal", seasonal_rows, seasonal_csv),
        ("metadata", metadata_rows, metadata_csv),
    ):
        try:
            pd.DataFrame(rows).to_csv(path, index=False)
        except Exception as exc:
            logger.log(f"[WARNING] failed to write {label} CSV ({path}): {exc}")

    logger.log("")
    logger.log(f"Text report      : {txt_path}")
    logger.log(f"Index-level CSV  : {index_csv}")
    logger.log(f"Index-summary CSV: {summary_csv}")
    logger.log(f"Seasonal CSV     : {seasonal_csv}")
    logger.log(f"Metadata CSV     : {metadata_csv}")
    logger.save()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        crash_dir = Path.cwd() / (
            f"inflation_references_verification_FATAL_"
            f"{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        )
        crash_dir.mkdir(parents=True, exist_ok=True)
        (crash_dir / "fatal_traceback.txt").write_text(
            "Fatal error while running inflation-references verification.\n\n"
            + traceback.format_exc(),
            encoding="utf-8",
        )
        raise
