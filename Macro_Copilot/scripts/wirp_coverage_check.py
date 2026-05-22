"""wirp_coverage_check.py — WIRP full-calendar coverage probe
============================================================

Operator-side Bloomberg verification for work order B2, the D-wirp increment
(ADR 0009 §6, Stage B).

The WIRP ticker GRAMMAR is already verified — the B2 event-data probe confirmed
32/32 (ticker, metric) probes across one next + one past meeting per bank. What
is NOT yet verified is FULL-CALENDAR coverage: does every scheduled meeting in
the central-bank calendar expose a WIRP ticker, or only those inside some
pricing horizon? This probe answers that empirically so the ``wirp.yml``
``forward_horizon_days`` / ``past_horizon_days`` are SET from data, not guessed
(P2), and so the observed value range of each metric documents its units
(P12 — WIRP is ingested verbatim; the ``field`` name asserts no unit).

INPUT — the RENDERED wirp.yml
----------------------------
This probe runs on the Bloomberg PC, which has NO Postgres access, so it cannot
read ``event_calendar`` directly. Instead it consumes the RENDERED ``wirp.yml``
produced locally by ``utils/render_wirp_universe.py`` (run it UNBOUNDED — no
horizon in the seed — so every meeting is probed). The operator copies that
rendered ``wirp.yml`` to the Bloomberg PC next to this script. The rendered
universe already carries, per meeting, the four source Bloomberg tickers
(``wirp_ticker_fr`` …) — the probe verifies the EXACT universe the extractor
will pull.

    python wirp_coverage_check.py [path/to/wirp.yml]      (default: ./wirp.yml)

For every (meeting, metric) it bdh(PX_LAST)'s the ticker and reports whether
data returned, the series span, and the observed value range. It writes a
timestamped output directory (text report + CSV) into the current working
directory. Paste the FINAL SUMMARY block back to the agent.

Narwhals-agnostic: the normalisers carry the same ``_coerce_to_pandas`` +
long-format handling as the production extractors, so the probe reads both
classic-pandas and the newer Narwhals xbbg output.

DEPLOYMENT
----------
Self-contained — stdlib + pandas + pyyaml + xbbg only.
"""

from __future__ import annotations

import sys
import traceback
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import yaml

try:
    from xbbg import blp
except Exception as exc:  # pragma: no cover - only meaningful on the BBG host
    print(f"[FATAL] could not import xbbg.blp: {exc}")
    raise


# ===========================================================================
# CONFIG
# ===========================================================================
# Deep enough to span every meeting's full pre-meeting run-up AND reveal how
# far back a past-meeting ticker retains history (~3.5 years).
PROBE_LOOKBACK_DAYS = 1300
DEFAULT_WIRP_YML = "wirp.yml"


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
    the installed xbbg used. Newer xbbg returns a Narwhals frame; a pandas
    object (or None) is returned UNCHANGED and narwhals is never imported."""
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
    """xbbg bdh output -> a 2-column ['date', 'value'] frame. Agnostic to
    classic WIDE and the newer Narwhals LONG (ticker|date|field|value) shapes."""
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
            date_key = _cols.get("date") or _cols.get("trade_date") or _cols.get("index")
            if date_key is None:
                for c in df.columns:
                    if pd.api.types.is_datetime64_any_dtype(df[c]):
                        date_key = c
                        break
            if date_key is not None:
                work = df
                _fmatch = df[
                    df[_cols["field"]].astype(str).str.upper()
                    == str(requested_field).upper()
                ]
                if not _fmatch.empty:
                    work = _fmatch
                return pd.DataFrame(
                    {
                        "date": list(work[date_key]),
                        "value": list(work[_cols["value"]]),
                    }
                )

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


def summarize_history(df: pd.DataFrame) -> Dict[str, Any]:
    """Series summary, including the observed value range — the value range is
    what documents a metric's units (P12)."""
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


# ===========================================================================
# Read the rendered wirp.yml.
# ===========================================================================
def read_wirp_universe(
    path: Path,
) -> Tuple[List[Dict[str, str]], List[Dict[str, Any]], str]:
    """Parse the rendered wirp.yml. Returns (metrics, universe, bloomberg_field).

    ``metrics`` — the wirp.metrics list ({code, field}); ``universe`` — the
    rendered universe rows (each carries wirp_ticker_<code>); ``bloomberg_field``
    — the daily field (PX_LAST).
    """
    playbook = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    wirp = playbook.get("wirp")
    if not isinstance(wirp, dict):
        raise ValueError(f"{path} has no `wirp:` section — not a WIRP playbook.")
    metrics = [
        m for m in (wirp.get("metrics") or [])
        if isinstance(m, dict) and m.get("code")
    ]
    if not metrics:
        raise ValueError(f"{path} wirp.metrics is empty.")
    universe = [r for r in (playbook.get("universe") or []) if isinstance(r, dict)]
    if not universe:
        raise ValueError(
            f"{path} has an EMPTY universe — render it UNBOUNDED with "
            "utils/render_wirp_universe.py before probing."
        )
    bloomberg_field = str(wirp.get("bloomberg_field") or "PX_LAST")
    return metrics, universe, bloomberg_field


def ticker_for(row: Dict[str, Any], code: str) -> Optional[str]:
    """The Bloomberg ticker for one metric of one meeting — the rendered
    ``wirp_ticker_<code>`` key, or reconstructed from prefix + token."""
    explicit = row.get(f"wirp_ticker_{code.lower()}")
    if explicit:
        return str(explicit)
    prefix = row.get("wirp_region_prefix")
    token = row.get("wirp_meeting_token")
    if prefix and token:
        return f"{prefix}{code} {token} Index"
    return None


# ===========================================================================
# Main.
# ===========================================================================
def main() -> None:
    wirp_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd() / DEFAULT_WIRP_YML
    if not wirp_path.exists():
        print(f"[FATAL] rendered wirp.yml not found at {wirp_path}")
        print("        Render it locally (utils/render_wirp_universe.py) and copy it here.")
        sys.exit(1)

    metrics, universe, bloomberg_field = read_wirp_universe(wirp_path)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path.cwd() / f"wirp_coverage_check_{timestamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    logger = Logger(out_dir / "wirp_coverage_report.txt")
    csv_path = out_dir / "wirp_coverage.csv"

    today = date.today()
    end_date = today - timedelta(days=1)
    start_date = end_date - timedelta(days=PROBE_LOOKBACK_DAYS)
    start_str = start_date.strftime("%Y-%m-%d")
    end_str = end_date.strftime("%Y-%m-%d")

    logger.log("=" * 100)
    logger.log("WIRP FULL-CALENDAR COVERAGE PROBE  (work order B2, D-wirp, ADR 0009 Stage B)")
    logger.log("=" * 100)
    logger.log(f"Timestamp        : {timestamp}")
    logger.log(f"Rendered wirp.yml: {wirp_path}")
    logger.log(f"Meetings         : {len(universe)}")
    logger.log(f"Metrics          : {[m['code'] for m in metrics]}")
    logger.log(f"Field            : {bloomberg_field}")
    logger.log(f"History window   : {start_str} to {end_str}  ({PROBE_LOOKBACK_DAYS}d)")
    logger.log(f"Output directory : {out_dir}")
    logger.log("")

    logger.log("PREFLIGHT — Bloomberg connectivity:")
    first_ticker = None
    for row in universe:
        first_ticker = ticker_for(row, str(metrics[0]["code"]))
        if first_ticker:
            break
    if first_ticker is None:
        logger.log("  [FATAL] no probeable ticker in the rendered universe.")
        sys.exit(1)
    pf_df, pf_err = safe_bdh_single(first_ticker, bloomberg_field, start_str, end_str)
    if pf_err:
        logger.log(f"  WARNING — preflight bdh on {first_ticker} errored: {pf_err}")
    else:
        logger.log(f"  OK — {first_ticker} -> {len(pf_df)} row(s)")
    logger.log("")

    rows: List[Dict[str, Any]] = []
    # Per-meeting metric-coverage counts and per-metric aggregates. The
    # full/partial/empty tallies are accumulated PER MEETING inside the loop
    # below — never re-aggregated by meeting_date afterwards, since two banks
    # can share a meeting date and a date-keyed tally would merge them.
    full_cov_dates: List[date] = []  # meeting dates with all metrics returning
    full_meetings = 0
    partial_meetings = 0
    empty_meetings = 0
    metric_codes = [str(m["code"]) for m in metrics]
    per_metric_hits: Dict[str, int] = {c: 0 for c in metric_codes}
    per_metric_values: Dict[str, List[float]] = {c: [] for c in metric_codes}

    logger.log("#" * 100)
    logger.log("PER-MEETING PROBE")
    logger.log("#" * 100)

    for row in universe:
        cb = row.get("central_bank")
        mdate = row.get("meeting_date")
        logger.log("")
        logger.log("-" * 100)
        logger.log(f"{cb} | meeting {mdate} | token {row.get('wirp_meeting_token')}")
        logger.log("-" * 100)

        metrics_with_data = 0
        for metric in metrics:
            code = str(metric["code"])
            field_name = metric.get("field")
            ticker = ticker_for(row, code)
            if ticker is None:
                logger.log(f"  ({code}) — no ticker in the rendered row, skipped")
                continue
            hist, err = safe_bdh_single(ticker, bloomberg_field, start_str, end_str)
            summary = summarize_history(hist)
            returned = summary["non_null_points"] > 0
            if returned:
                metrics_with_data += 1
                per_metric_hits[code] += 1
                if summary["min_value"] is not None:
                    per_metric_values[code].append(float(summary["min_value"]))
                if summary["max_value"] is not None:
                    per_metric_values[code].append(float(summary["max_value"]))

            rows.append({
                "central_bank": cb,
                "meeting_date": mdate,
                "meeting_token": row.get("wirp_meeting_token"),
                "metric_code": code,
                "field": field_name,
                "ticker": ticker,
                "returned_data": returned,
                "points": summary["non_null_points"],
                "first_date": summary["first_date"],
                "last_date": summary["last_date"],
                "last_value": summary["last_value"],
                "min_value": summary["min_value"],
                "max_value": summary["max_value"],
                "error": err,
            })
            logger.log(
                f"  {code:<3} {ticker:<26} -> {summary['non_null_points']:>4} pts "
                f"[{summary['first_date']} -> {summary['last_date']}] "
                f"last={summary['last_value']} range=[{summary['min_value']}, "
                f"{summary['max_value']}]"
                + (f"  [error: {err}]" if err else "")
            )

        logger.log(f"  => {metrics_with_data}/{len(metrics)} metric(s) returned data")
        if metrics_with_data == len(metrics):
            full_meetings += 1
            if mdate:
                try:
                    full_cov_dates.append(date.fromisoformat(str(mdate)[:10]))
                except ValueError:
                    pass
        elif metrics_with_data > 0:
            partial_meetings += 1
        else:
            empty_meetings += 1

    # ----------------------------------------------------------------------
    # FINAL SUMMARY
    # ----------------------------------------------------------------------
    n_meetings = len(universe)
    full = full_meetings
    partial = partial_meetings
    none = empty_meetings

    logger.log("")
    logger.log("=" * 100)
    logger.log("FINAL SUMMARY  (paste this block back to the agent)")
    logger.log("=" * 100)
    logger.log(
        f"Meetings probed: {n_meetings} | full 4/4: {full} | partial: {partial} | "
        f"no data: {none}"
    )
    logger.log("")
    logger.log("Per-metric coverage + observed value range (the range documents the unit):")
    for code in metric_codes:
        vals = per_metric_values[code]
        rng = f"[{min(vals):.4g}, {max(vals):.4g}]" if vals else "[n/a]"
        logger.log(
            f"  {code:<3} -> {per_metric_hits[code]:>3}/{n_meetings} meetings "
            f"returned data | value range {rng}"
        )
    logger.log("")
    if full_cov_dates:
        lo, hi = min(full_cov_dates), max(full_cov_dates)
        logger.log(
            "Full-coverage meeting span (feeds wirp.yml forward/past_horizon_days "
            f"in Stage D): {lo.isoformat()} ... {hi.isoformat()}"
        )
        logger.log(
            f"  -> past_horizon_days >= {(today - lo).days} ; "
            f"forward_horizon_days >= {(hi - today).days}"
        )
    else:
        logger.log("Full-coverage meeting span: NONE — no meeting returned all metrics.")
    logger.log("")
    logger.log("NEXT: the agent reads the CSV, sets wirp.yml's horizons + unit comments "
               "(Stage D), and confirms the strict 4/4 coverage gate is satisfiable.")

    try:
        pd.DataFrame(rows).to_csv(csv_path, index=False)
    except Exception as exc:  # noqa: BLE001
        logger.log(f"[WARNING] failed to write CSV ({csv_path}): {exc}")

    logger.log("")
    logger.log(f"Text report : {logger.path}")
    logger.log(f"Coverage CSV: {csv_path}")
    logger.save()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        crash_dir = Path.cwd() / (
            f"wirp_coverage_check_FATAL_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        )
        crash_dir.mkdir(parents=True, exist_ok=True)
        (crash_dir / "fatal_traceback.txt").write_text(
            "Fatal error while running the WIRP coverage probe.\n\n"
            + traceback.format_exc(),
            encoding="utf-8",
        )
        raise
