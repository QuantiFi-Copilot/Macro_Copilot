"""
sovereign_bonds_realbond_bloomberg_check.py
===========================================

Operator-side Bloomberg verification — work order A4, FOLLOW-UP probe.

WHY THIS SCRIPT EXISTS
----------------------
The first A4 verification (scripts/sovereign_bonds_risk_bloomberg_check.py)
found that risk analytics (modified duration, DV01, accrued interest, YAS
yield) and the asset-swap spread return NOTHING via bdh on the GT-generic
benchmark tickers (`GT10 Govt`, `GTDEM10Y Govt`, …) — 0 points, no error —
while plain yields (`YLD_YTM_MID/BID/ASK`) come back cleanly. The inference:
the GT generics are pure YIELD-CURVE tickers and do not carry per-bond
analytics. `PX_DIRTY_MID` "passed" the count check but returned yield-
magnitude values (~4 for a US 2Y, where a real dirty price is ~100) — a
suspected false pass.

That inference must be PROVEN, not assumed. This script does three things:

  STEP A — resolve each GT generic to its CURRENT underlying cash bond.
    bdp the generic for a set of candidate identity fields (ID_ISIN,
    ID_CUSIP, SECURITY_DES, …) and report which resolve the real bond.

  STEP B — snapshot-test the analytics on the GENERIC via bdp.
    bdp (point-in-time), not bdh, for the risk/ASW fields on the generic.
    If bdp returns them but bdh did not, the fields are valid and the
    generic exposes a snapshot — it simply does not historise them.

  STEP C — re-test the analytics on the RESOLVED REAL BOND via bdh.
    Rebuild a security query from the resolved ISIN / CUSIP and bdh the
    risk/ASW fields over a multi-year window on the actual bond. If they
    return clean daily series there, the data genuinely lives on individual
    CUSIP bonds — which is the A4-4 (`sovereign_cash_bonds.yml`) universe —
    and the correct mnemonics are pinned for that playbook.

PX_DIRTY QUESTION: STEP C bdh's PX_DIRTY_MID / PX_CLEAN_MID on the real bond.
A real bond's dirty price is ~40-250; a yield is ~ -5-25. The report flags
each value as PRICE-like or YIELD-like, settling whether PX_DIRTY_MID on the
GT generic is a true dirty price or just another yield-like generic value.

FWCV is deliberately OUT OF SCOPE here. FWCV forward/carry failed bdp as well
as bdh in the first run — a screen-access problem, categorically different
from the wrong-ticker-type problem this script investigates. FWCV stays a
separate Bloomberg access-pattern investigation.

DEPLOYMENT
----------
Self-contained — stdlib + pandas + xbbg. Copy to the Bloomberg terminal host:

    python sovereign_bonds_realbond_bloomberg_check.py

Outputs land in ./sovereign_bonds_realbond_verification_<TIMESTAMP>/ — a text
report + two CSVs. Return all of them; the FINAL SUMMARY block is the
pasteable digest.
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
LOOKBACK_YEARS = 3               # a current OTR bond only has history since issue
MIN_REALBOND_POINTS = 1          # STEP C confirms the FIELD works — not coverage

# The GT-generic benchmark universe (one+ ticker per curve family) — the same
# generics the first verification probed. STEP A resolves each to its bond.
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

# STEP A — candidate reference fields that may resolve the generic's current
# underlying cash bond. Several candidates; the run reveals which Bloomberg
# populates on a generic.
UNDERLYING_ID_FIELDS: List[str] = [
    "ID_ISIN", "ID_CUSIP", "PARSEKYABLE_DES", "SECURITY_DES", "NAME",
    "CPN", "MATURITY", "ISSUE_DT",
]

# STEPS B & C — the risk / ASW mnemonics under test. These are the fields that
# returned nothing via bdh on the generics. Candidate alternates are included
# so the real-bond run also PINS the correct mnemonic for the A4-4 playbook.
# YLD_YTM_MID is a control — it MUST work on a real bond.
RISK_ASW_FIELDS: List[str] = [
    "YLD_YTM_MID",          # control
    "YAS_BOND_YLD",
    "DUR_ADJ_MID", "MOD_DUR_MID",
    "RISK_MID", "YAS_RISK",
    "INT_ACC",
    "PX_DIRTY_MID", "PX_CLEAN_MID", "PX_LAST",
    "ASSET_SWAP_SPD_MID", "ASW_SPREAD",
]

# Plausible value bands — used to classify a returned value as PRICE-like,
# YIELD-like, or out-of-band. The decisive use: a real bond's PX_DIRTY_MID
# must be PRICE-like (~40-250); if it is YIELD-like (~ -5-25) the field is
# not actually a dirty price.
PLAUSIBLE_BANDS: Dict[str, Tuple[float, float]] = {
    "YLD_YTM_MID": (-5, 25), "YAS_BOND_YLD": (-5, 25),
    "DUR_ADJ_MID": (0, 60), "MOD_DUR_MID": (0, 60),
    "RISK_MID": (0, 100), "YAS_RISK": (0, 100),
    "INT_ACC": (0, 20),
    "PX_DIRTY_MID": (40, 250), "PX_CLEAN_MID": (40, 250), "PX_LAST": (40, 250),
    "ASSET_SWAP_SPD_MID": (-300, 600), "ASW_SPREAD": (-300, 600),
}
PRICE_BAND = (40.0, 250.0)
YIELD_BAND = (-5.0, 25.0)


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
# Cleaning + normalisation helpers (verbatim from the extractor / first script).
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


def summarize_history(df: pd.DataFrame) -> Dict[str, Any]:
    empty = {
        "non_null_points": 0, "first_date": None, "last_date": None,
        "last_value": None, "min_value": None, "max_value": None,
    }
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


def classify_value(field: str, value: Any) -> str:
    """Classify a numeric value as price-like / yield-like / in-band / odd."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "non-numeric"
    band = PLAUSIBLE_BANDS.get(field.upper())
    in_band = band is not None and band[0] <= v <= band[1]
    price_like = PRICE_BAND[0] <= v <= PRICE_BAND[1]
    yield_like = YIELD_BAND[0] <= v <= YIELD_BAND[1]
    tag = "in-band" if in_band else "OUT-OF-BAND"
    shape = "price-like" if price_like else ("yield-like" if yield_like else "other-magnitude")
    return f"{tag}/{shape}"


# ---------------------------------------------------------------------------
# Resolve a security-query string for the underlying bond from its identifiers.
# blpapi accepts /isin/ and /cusip/ security identifiers; the run reveals which
# form bdp/bdh actually resolves on this terminal.
# ---------------------------------------------------------------------------
def candidate_bond_queries(isin: Optional[str], cusip: Optional[str]) -> List[str]:
    forms: List[str] = []
    if cusip:
        forms += [f"/cusip/{cusip}", f"/cusip/{cusip} Govt", f"{cusip} Govt"]
    if isin:
        forms += [f"/isin/{isin}", f"/isin/{isin} Govt", f"{isin} Govt"]
    return forms


# ---------------------------------------------------------------------------
# Main.
# ---------------------------------------------------------------------------
def main() -> None:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path.cwd() / f"sovereign_bonds_realbond_verification_{timestamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    txt_path = out_dir / "sovereign_bonds_realbond_validation_report.txt"
    resolution_csv = out_dir / "sovereign_bonds_realbond_validation_resolution.csv"
    fieldtest_csv = out_dir / "sovereign_bonds_realbond_validation_fieldtest.csv"

    logger = Logger(txt_path)
    resolution_rows: List[Dict[str, Any]] = []
    fieldtest_rows: List[Dict[str, Any]] = []

    end_date = date.today() - timedelta(days=1)
    start_date = end_date - timedelta(days=int(LOOKBACK_YEARS * 365.25))
    start_str = start_date.strftime("%Y-%m-%d")
    end_str = end_date.strftime("%Y-%m-%d")

    logger.log("=" * 100)
    logger.log("SOVEREIGN BONDS — REAL-BOND CONFIRMATION (work order A4, follow-up)")
    logger.log("=" * 100)
    logger.log(f"Timestamp        : {timestamp}")
    logger.log(f"History window   : {start_str} to {end_str}  ({LOOKBACK_YEARS}y)")
    logger.log(f"Generic tickers  : {len(GENERIC_UNIVERSE)}")
    logger.log(f"Risk/ASW fields  : {len(RISK_ASW_FIELDS)}")
    logger.log(f"Output directory : {out_dir}")
    logger.log("")
    logger.log("Each GT generic is resolved to its current underlying cash bond, then")
    logger.log("the risk/ASW fields are tested THREE ways: bdh on the generic (known")
    logger.log("to fail), bdp on the generic (snapshot), bdh on the resolved real")
    logger.log("bond. If the real-bond bdh works, the data lives on individual CUSIP")
    logger.log("bonds — the A4-4 universe — and the correct mnemonics are pinned.")

    for cfg in GENERIC_UNIVERSE:
        generic = cfg["ticker"]
        logger.log("")
        logger.log("#" * 100)
        logger.log(f"{generic}  |  {cfg['curve_family']} {cfg['country']} {cfg['tenor']}")
        logger.log("#" * 100)

        # ---- STEP A — resolve the underlying bond ------------------------
        ids, ids_err = safe_bdp_batch(generic, UNDERLYING_ID_FIELDS)
        isin = ids.get("ID_ISIN")
        cusip = ids.get("ID_CUSIP")
        logger.log("STEP A — resolve underlying bond (bdp on the generic):")
        for f in UNDERLYING_ID_FIELDS:
            logger.log(f"    {f:<18} = {ids.get(f.upper())}")
        if ids_err:
            logger.log(f"    [bdp error] {ids_err}")
        resolution_rows.append({
            "generic_ticker": generic,
            "curve_family": cfg["curve_family"],
            "country": cfg["country"],
            "tenor": cfg["tenor"],
            "id_isin": isin,
            "id_cusip": cusip,
            "security_des": ids.get("SECURITY_DES"),
            "name": ids.get("NAME"),
            "coupon": ids.get("CPN"),
            "maturity": ids.get("MATURITY"),
            "issue_date": ids.get("ISSUE_DT"),
            "bdp_error": ids_err,
        })

        # ---- STEP B — snapshot test on the generic (bdp) -----------------
        gen_snap, gen_snap_err = safe_bdp_batch(generic, RISK_ASW_FIELDS)
        logger.log("STEP B — snapshot of risk/ASW fields on the GENERIC (bdp):")
        for f in RISK_ASW_FIELDS:
            v = gen_snap.get(f.upper())
            cls = classify_value(f, v) if v is not None else "(none)"
            logger.log(f"    {f:<20} = {str(v):<16} {cls}")

        # ---- STEP C — resolve a bond query, then bdh on the real bond ----
        logger.log("STEP C — risk/ASW fields on the RESOLVED REAL BOND (bdh):")
        resolved_query: Optional[str] = None
        for form in candidate_bond_queries(isin, cusip):
            chk, _ = safe_bdp_batch(form, ["SECURITY_DES"])
            if chk.get("SECURITY_DES"):
                resolved_query = form
                break
        if resolved_query is None:
            logger.log("    [could not resolve a bond query form from ISIN/CUSIP — "
                       "real-bond bdh skipped; operator resolves manually]")
        else:
            logger.log(f"    resolved bond query: {resolved_query}")

        for f in RISK_ASW_FIELDS:
            gen_bdp_val = gen_snap.get(f.upper())
            gen_bdp_has = gen_bdp_val is not None and not (
                isinstance(gen_bdp_val, str) and gen_bdp_val.strip() == ""
            )
            rb_points = 0
            rb_last = None
            rb_class = None
            rb_err: Optional[str] = None
            if resolved_query is not None:
                hist, rb_err = safe_bdh_single(resolved_query, f, start_str, end_str)
                summary = summarize_history(hist)
                rb_points = summary["non_null_points"]
                rb_last = summary["last_value"]
                if rb_last is not None:
                    rb_class = classify_value(f, rb_last)
            rb_ok = rb_points >= MIN_REALBOND_POINTS
            if resolved_query is not None:
                logger.log(
                    f"    {f:<20} real-bond bdh: "
                    f"{'DATA' if rb_ok else 'no  '} points={rb_points:<5} "
                    f"last={str(rb_last):<14} {rb_class or ''}"
                    + (f"  [err {rb_err}]" if rb_err else "")
                )
            fieldtest_rows.append({
                "generic_ticker": generic,
                "curve_family": cfg["curve_family"],
                "country": cfg["country"],
                "field": f,
                "generic_bdp_returned": gen_bdp_has,
                "generic_bdp_value": clean_scalar(gen_bdp_val),
                "generic_bdp_class": classify_value(f, gen_bdp_val) if gen_bdp_has else None,
                "resolved_bond_query": resolved_query,
                "realbond_bdh_points": rb_points,
                "realbond_bdh_last_value": rb_last,
                "realbond_bdh_class": rb_class,
                "realbond_bdh_ok": rb_ok,
                "realbond_bdh_error": rb_err,
            })

    # ----------------------------------------------------------------------
    # FINAL SUMMARY
    # ----------------------------------------------------------------------
    logger.log("")
    logger.log("=" * 100)
    logger.log("FINAL SUMMARY  (paste this block back to the agent)")
    logger.log("=" * 100)

    resolved_n = sum(1 for r in resolution_rows if r["id_isin"] or r["id_cusip"])
    logger.log(f"Underlying-bond resolution : {resolved_n}/{len(resolution_rows)} "
               "generics resolved to an ISIN/CUSIP")
    logger.log("")
    logger.log("Per risk/ASW field — does it return data on the REAL bond (bdh) and")
    logger.log("on the GENERIC (bdp)?  [generic bdh already known to return nothing]")
    logger.log("")
    for f in RISK_ASW_FIELDS:
        rows = [r for r in fieldtest_rows if r["field"] == f]
        rb_hits = sum(1 for r in rows if r["realbond_bdh_ok"])
        bdp_hits = sum(1 for r in rows if r["generic_bdp_returned"])
        tested = len(rows)
        rb_classes = sorted({r["realbond_bdh_class"] for r in rows
                             if r["realbond_bdh_class"]})
        logger.log(
            f"  {f:<20} real-bond bdh {rb_hits}/{tested} | "
            f"generic bdp {bdp_hits}/{tested} | "
            f"real-bond value class: {rb_classes or '(none)'}"
        )

    logger.log("")
    logger.log("PX_DIRTY verdict — compare PX_DIRTY_MID on the real bond vs the generic:")
    for r in [x for x in fieldtest_rows if x["field"] == "PX_DIRTY_MID"]:
        logger.log(
            f"  {r['generic_ticker']:<16} generic bdp={r['generic_bdp_value']} "
            f"({r['generic_bdp_class']}) | real-bond bdh last="
            f"{r['realbond_bdh_last_value']} ({r['realbond_bdh_class']})"
        )
    logger.log("")
    logger.log("If real-bond PX_DIRTY_MID is price-like (~100) while the generic's is")
    logger.log("yield-like (~4), PX_DIRTY_MID on a GT generic is NOT a dirty price —")
    logger.log("confirming it must be dropped from any generic-ticker playbook.")
    logger.log("")
    logger.log("If the risk/ASW fields return clean bdh series on the resolved real")
    logger.log("bonds, A4-1 risk + A4-2 ASW move onto the A4-4 CUSIP-level playbook")
    logger.log("(sovereign_cash_bonds.yml) with the mnemonics pinned above. FWCV is")
    logger.log("NOT addressed here — it remains a separate access-pattern probe.")

    # ----------------------------------------------------------------------
    # Write outputs.
    # ----------------------------------------------------------------------
    for label, rows, path in (
        ("resolution", resolution_rows, resolution_csv),
        ("fieldtest", fieldtest_rows, fieldtest_csv),
    ):
        try:
            pd.DataFrame(rows).to_csv(path, index=False)
        except Exception as exc:
            logger.log(f"[WARNING] failed to write {label} CSV ({path}): {exc}")

    logger.log("")
    logger.log(f"Text report     : {txt_path}")
    logger.log(f"Resolution CSV  : {resolution_csv}")
    logger.log(f"Field-test CSV  : {fieldtest_csv}")
    logger.save()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        crash_dir = Path.cwd() / (
            f"sovereign_bonds_realbond_verification_FATAL_"
            f"{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        )
        crash_dir.mkdir(parents=True, exist_ok=True)
        (crash_dir / "fatal_traceback.txt").write_text(
            "Fatal error while running sovereign-bonds real-bond confirmation.\n\n"
            + traceback.format_exc(),
            encoding="utf-8",
        )
        raise
