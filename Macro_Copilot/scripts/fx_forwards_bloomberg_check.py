from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Tuple
import traceback

import pandas as pd
from xbbg import blp


OUTPUT_TXT = "fx_forwards_validation_report.txt"
OUTPUT_TICKER_CSV = "fx_forwards_validation_ticker_summary.csv"
OUTPUT_FIELD_CSV = "fx_forwards_validation_field_level.csv"
OUTPUT_FAMILY_CSV = "fx_forwards_validation_family_summary.csv"

TARGET_FIELDS = ["PX_LAST"]
REFERENCE_FIELDS = ["SECURITY_DES"]

LOOKBACK_DAYS = 120
MIN_HISTORY_POINTS = 5

FX_FORWARD_UNIVERSE = {
    "EURUSD": {"ticker": "EURUSD1M Curncy", "base_ccy": "EUR", "quote_ccy": "USD"},
    "GBPUSD": {"ticker": "GBPUSD1M Curncy", "base_ccy": "GBP", "quote_ccy": "USD"},
    "USDJPY": {"ticker": "USDJPY1M Curncy", "base_ccy": "USD", "quote_ccy": "JPY"},
    "AUDUSD": {"ticker": "AUDUSD1M Curncy", "base_ccy": "AUD", "quote_ccy": "USD"},
    "USDCAD": {"ticker": "USDCAD1M Curncy", "base_ccy": "USD", "quote_ccy": "CAD"},
    "USDCHF": {"ticker": "USDCHF1M Curncy", "base_ccy": "USD", "quote_ccy": "CHF"},
}


class Logger:
    def __init__(self, path: str):
        self.path = Path(path)
        self.lines: List[str] = []

    def log(self, msg: str = "") -> None:
        self.lines.append(msg)

    def save(self) -> None:
        self.path.write_text("\n".join(self.lines), encoding="utf-8")


def clean_scalar(value: Any) -> Any:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except Exception:
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
        except Exception:
            pass
    return value


def normalize_bdp_output(df: Any, fallback_ticker: str) -> Dict[str, Any]:
    if df is None:
        return {}

    # Convert Narwhals / pyarrow-like xbbg output to pandas
    if not isinstance(df, (pd.DataFrame, pd.Series)):
        try:
            if hasattr(df, "to_native"):
                df = df.to_native()

            if hasattr(df, "to_pandas"):
                df = df.to_pandas()

        except Exception:
            return {}

    if isinstance(df, pd.Series):
        return {str(k).upper(): clean_scalar(v) for k, v in df.items()}

    if isinstance(df, pd.DataFrame):
        if df.empty:
            return {}

        # xbbg can return long format: ticker, field, value
        if {"ticker", "field", "value"}.issubset(set(df.columns)):
            out = {}
            work = df[df["ticker"].astype(str) == fallback_ticker]
            if work.empty:
                work = df
            for _, row in work.iterrows():
                out[str(row["field"]).upper()] = clean_scalar(row["value"])
            return out

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

    # Convert Narwhals / pyarrow-like xbbg output to pandas
    if not isinstance(df, (pd.DataFrame, pd.Series)):
        try:
            
            if hasattr(df, "to_native"):
                df = df.to_native()

            if hasattr(df, "to_pandas"):
                df = df.to_pandas()

        except Exception:
            return pd.DataFrame(columns=["date", "value"])

    if isinstance(df, pd.Series):
        out = df.to_frame(name="value").reset_index()
        out.columns = ["date", "value"]
        return out

    if not isinstance(df, pd.DataFrame) or df.empty:
        return pd.DataFrame(columns=["date", "value"])

    # xbbg can return already-long format: ticker, date, field, value
    if {"date", "field", "value"}.issubset(set(df.columns)):
        work = df.copy()
        work = work[work["field"].astype(str).str.upper() == requested_field.upper()]
        if work.empty:
            return pd.DataFrame(columns=["date", "value"])
        return work[["date", "value"]].rename(columns={"date": "date", "value": "value"})

    out = df.copy().reset_index()

    if len(out.columns) == 2:
        out.columns = ["date", "value"]
        return out

    value_col = None
    for col in out.columns:
        col_str = str(col).upper()
        if col_str == requested_field.upper():
            value_col = col
            break
        if isinstance(col, tuple) and len(col) >= 2 and str(col[-1]).upper() == requested_field.upper():
            value_col = col
            break

    if value_col is None:
        value_col = out.columns[1]

    return out[[out.columns[0], value_col]].rename(
        columns={out.columns[0]: "date", value_col: "value"}
    )


def safe_bdp_single(ticker: str, field: str) -> Tuple[Any, str | None]:
    try:
        raw = blp.bdp(tickers=ticker, flds=[field])
        norm = normalize_bdp_output(raw, fallback_ticker=ticker)
        return norm.get(field.upper()), None
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


def safe_bdp_batch(ticker: str, fields: List[str]) -> Tuple[Dict[str, Any], str | None]:
    try:
        raw = blp.bdp(tickers=ticker, flds=fields)
        norm = normalize_bdp_output(raw, fallback_ticker=ticker)
        return {f: norm.get(f.upper()) for f in fields}, None
    except Exception as exc:
        return {}, f"{type(exc).__name__}: {exc}"


def safe_bdh_single(ticker: str, field: str, start: str, end: str) -> Tuple[pd.DataFrame, str | None]:
    try:
        raw = blp.bdh(tickers=ticker, flds=[field], start_date=start, end_date=end)
        return normalize_bdh_output(raw, requested_field=field), None
    except Exception as exc:
        return pd.DataFrame(columns=["date", "value"]), f"{type(exc).__name__}: {exc}"


def safe_bdh_batch(ticker: str, fields: List[str], start: str, end: str) -> Tuple[Dict[str, pd.DataFrame], str | None]:
    try:
        raw = blp.bdh(tickers=ticker, flds=fields, start_date=start, end_date=end)
        if raw is None or not isinstance(raw, pd.DataFrame) or raw.empty:
            return {f: pd.DataFrame(columns=["date", "value"]) for f in fields}, None
        return {f: normalize_bdh_output(raw, requested_field=f) for f in fields}, None
    except Exception as exc:
        return {f: pd.DataFrame(columns=["date", "value"]) for f in fields}, f"{type(exc).__name__}: {exc}"


def is_non_null(v: Any) -> bool:
    if v is None:
        return False
    if isinstance(v, str) and v.strip().upper() in {"", "N.A.", "NA", "NONE"}:
        return False
    return True


def summarize_history(df: pd.DataFrame) -> Dict[str, Any]:
    if df is None or df.empty or "value" not in df.columns:
        return {"non_null_points": 0, "first_date": None, "last_date": None, "first_value": None, "last_value": None, "min_value": None, "max_value": None}

    work = df.copy()
    work["value_clean"] = pd.to_numeric(work["value"], errors="coerce")
    work = work.dropna(subset=["value_clean"])

    if work.empty:
        return {"non_null_points": 0, "first_date": None, "last_date": None, "first_value": None, "last_value": None, "min_value": None, "max_value": None}

    work = work.sort_values("date")
    return {
        "non_null_points": int(len(work)),
        "first_date": clean_scalar(work.iloc[0]["date"]),
        "last_date": clean_scalar(work.iloc[-1]["date"]),
        "first_value": clean_scalar(work.iloc[0]["value_clean"]),
        "last_value": clean_scalar(work.iloc[-1]["value_clean"]),
        "min_value": clean_scalar(work["value_clean"].min()),
        "max_value": clean_scalar(work["value_clean"].max()),
    }


def main() -> None:
    logger = Logger(OUTPUT_TXT)
    field_rows: List[Dict[str, Any]] = []
    ticker_rows: List[Dict[str, Any]] = []

    end_date = date.today() - timedelta(days=1)
    start_date = end_date - timedelta(days=LOOKBACK_DAYS)
    start_str = start_date.strftime("%Y-%m-%d")
    end_str = end_date.strftime("%Y-%m-%d")

    logger.log("=" * 140)
    logger.log("FX 1M FORWARDS BLOOMBERG TICKER AND FIELD VALIDATION")
    logger.log("=" * 140)
    logger.log(f"Total pairs tested     : {len(FX_FORWARD_UNIVERSE)}")
    logger.log(f"Historical fields      : {TARGET_FIELDS}")
    logger.log(f"Reference fields       : {REFERENCE_FIELDS}")
    logger.log(f"History window         : {start_str} to {end_str}")
    logger.log(f"Min history points     : {MIN_HISTORY_POINTS}")
    logger.log()

    passed_pairs: List[str] = []
    failed_pairs: List[str] = []

    for pair, cfg in FX_FORWARD_UNIVERSE.items():
        ticker = cfg["ticker"]

        logger.log("-" * 140)
        logger.log(f"{pair} | ticker={ticker} | tenor=1M | base={cfg['base_ccy']} | quote={cfg['quote_ccy']}")

        batch_ref_values, batch_ref_err = safe_bdp_batch(ticker, REFERENCE_FIELDS)
        reference_pass_map: Dict[str, bool] = {}
        ref_results_for_ticker: Dict[str, Any] = {}

        logger.log("REFERENCE FIELD CHECKS")
        for field in REFERENCE_FIELDS:
            single_value, single_err = safe_bdp_single(ticker, field)
            batch_value = batch_ref_values.get(field)
            value_for_eval = single_value if single_err is None else batch_value

            passed = is_non_null(value_for_eval)
            reference_pass_map[field] = passed
            ref_results_for_ticker[field] = value_for_eval

            field_rows.append({
                "pair": pair,
                "ticker": ticker,
                "tenor": "1M",
                "base_ccy": cfg["base_ccy"],
                "quote_ccy": cfg["quote_ccy"],
                "field_type": "reference",
                "field_name": field,
                "single_value": single_value,
                "single_error": single_err,
                "batch_value": batch_value,
                "batch_error": batch_ref_err,
                "pass": passed,
                "note": "non-null" if passed else "null value",
            })

            logger.log(f"{field:<16} | pass={'YES' if passed else 'NO '} | value={repr(value_for_eval)}")
            if single_err:
                logger.log(f"  single_error: {single_err}")

        batch_hist_values, batch_hist_err = safe_bdh_batch(ticker, TARGET_FIELDS, start_str, end_str)
        history_pass_map: Dict[str, bool] = {}

        logger.log("HISTORICAL FIELD CHECKS")
        for field in TARGET_FIELDS:
            single_hist_df, single_hist_err = safe_bdh_single(ticker, field, start_str, end_str)
            batch_hist_df = batch_hist_values.get(field, pd.DataFrame(columns=["date", "value"]))
            summary = summarize_history(single_hist_df if single_hist_err is None else batch_hist_df)

            passed = summary["non_null_points"] > 0
            history_pass_map[field] = passed

            field_rows.append({
                "pair": pair,
                "ticker": ticker,
                "tenor": "1M",
                "base_ccy": cfg["base_ccy"],
                "quote_ccy": cfg["quote_ccy"],
                "field_type": "historical",
                "field_name": field,
                "single_value": None,
                "single_error": single_hist_err,
                "batch_value": None,
                "batch_error": batch_hist_err,
                "pass": passed,
                "note": f"{summary['non_null_points']} non-null points",
                **summary,
            })

            logger.log(
                f"{field:<16} | pass={'YES' if passed else 'NO '} | "
                f"points={summary['non_null_points']} | first_date={summary['first_date']} | "
                f"last_date={summary['last_date']} | last_value={summary['last_value']}"
            )
            if single_hist_err:
                logger.log(f"  single_error: {single_hist_err}")

        reference_complete = all(reference_pass_map.values())
        history_complete = all(history_pass_map.values())
        ticker_ready = reference_complete and history_complete

        ticker_rows.append({
            "pair": pair,
            "ticker": ticker,
            "tenor": "1M",
            "base_ccy": cfg["base_ccy"],
            "quote_ccy": cfg["quote_ccy"],
            "reference_complete": reference_complete,
            "history_complete": history_complete,
            "ticker_ready_for_playbook": ticker_ready,
            "security_name": ref_results_for_ticker.get("SECURITY_DES"),
        })

        if ticker_ready:
            passed_pairs.append(pair)
        else:
            failed_pairs.append(pair)

        logger.log("PAIR SUMMARY")
        logger.log(f"reference_complete={'YES' if reference_complete else 'NO'} | history_complete={'YES' if history_complete else 'NO'} | ticker_ready_for_playbook={'YES' if ticker_ready else 'NO'}")
        logger.log()

    field_df = pd.DataFrame(field_rows)
    ticker_df = pd.DataFrame(ticker_rows)
    family_df = pd.DataFrame([{
        "fx_family": "G10_1M_FORWARDS",
        "pairs_tested": len(FX_FORWARD_UNIVERSE),
        "passed_pairs_count": len(passed_pairs),
        "failed_pairs_count": len(failed_pairs),
        "all_pairs_ready": len(failed_pairs) == 0,
        "passed_pairs": " | ".join(passed_pairs),
        "failed_pairs": " | ".join(failed_pairs),
    }])

    field_df.to_csv(OUTPUT_FIELD_CSV, index=False)
    ticker_df.to_csv(OUTPUT_TICKER_CSV, index=False)
    family_df.to_csv(OUTPUT_FAMILY_CSV, index=False)

    logger.log("=" * 140)
    logger.log("FINAL SUMMARY")
    logger.log("=" * 140)
    logger.log(f"Pair pass count         : {len(passed_pairs)} / {len(FX_FORWARD_UNIVERSE)}")
    logger.log(f"Failed pairs            : {' | '.join(failed_pairs) if failed_pairs else 'None'}")
    logger.log(f"Saved ticker summary to : {Path(OUTPUT_TICKER_CSV).resolve()}")
    logger.log(f"Saved field detail to   : {Path(OUTPUT_FIELD_CSV).resolve()}")
    logger.save()

    print("=" * 100)
    print("FX 1M FORWARDS VALIDATION COMPLETE")
    print("=" * 100)
    print(family_df.to_string(index=False))
    print()
    print(f"Text report   : {Path(OUTPUT_TXT).resolve()}")
    print(f"Ticker CSV    : {Path(OUTPUT_TICKER_CSV).resolve()}")
    print(f"Field CSV     : {Path(OUTPUT_FIELD_CSV).resolve()}")
    print(f"Family CSV    : {Path(OUTPUT_FAMILY_CSV).resolve()}")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        Path(OUTPUT_TXT).write_text(
            "Fatal error while running FX forwards validation.\n\n" + traceback.format_exc(),
            encoding="utf-8",
        )
        raise