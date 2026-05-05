"""
Deterministic ground-truth oracle for WORKFLOW_GAUNTLET.md questions 1–7.

Purpose
-------
Compute the answer the workflow_router + workflow_executor should return for
each of the first 7 prompts in the gauntlet, using ONLY raw SQL queries
against `macro_data.market_data_daily` plus pandas/numpy.  Deliberately does
NOT import any primitive (`rates_agent.*.tools.*`) or any operator
(`shared.operators.*`) — those are exactly what we want to validate.

Run the workflow router on the same prompts and manually compare the numbers
this script prints to the values shown in the chat's WorkflowResultCard.

Usage
-----
From the project root, with the env var pointing at TimescaleDB:

    DB_HOST=... DB_PORT=... DB_USER=... DB_PASSWORD=... DB_NAME=... \\
        python -m scripts.oracle_workflow_q1_q7

…or just rely on the project's `database.get_db_engine()` (which loads .env):

    python Macro_Copilot/scripts/oracle_workflow_q1_q7.py

The script is read-only; it issues SELECTs only.

Assumptions — aligned with the rates_agent event_study workflow
---------------------------------------------------------------
This oracle now mirrors the workflow template's *actual* config (read from
``rates_agent/workflows/event_study/template.yaml`` + each primitive's
``config.yaml``).  When numbers diverge after this, it is the WORKFLOW's
math that is suspect, not the oracle's.

1. Sovereign yield field   : ``YLD_YTM_MID``  (matches yield_levels config)
2. OIS rate field          : ``PX_LAST``      (matches rate_level config)
3. Swap spread             : ``(sov_pct - ois_pct) * 100``  → bps
4. Curve spread            : ``(long_pct - short_pct) * 100`` → bps
5. Two-leg alignment       : forward-fill up to 5 days (matches
   ``ffill_limit_days = 5`` in the swap_spread primitive's
   ``pivot_and_align_tenors``).  This filling happens BEFORE the spread
   is differenced for the change z-score, so a missing day produces
   ``diff = 0`` for that day and the real move on the next available day.
6. Rolling z-score window  : 252 trading days, ddof=1, **min_periods=60**
   (matches ``z_score_min_periods = 60`` in swap_spread / curve_spread
   config — was 126 in earlier oracle runs).
7. Rolling-window warm-up  : the rolling z-score is computed on a
   buffered series (``lookback_days + 1.5 * 252`` calendar days) BEFORE
   the display window is trimmed.  Matches
   ``z_score_buffer_multiplier = 1.5``.  Without this, the first ~6
   months of the displayed window have NaN z-scores and contribute zero
   events — the exact symptom you saw on earlier oracle runs.
8. Event extraction        : **TWO-SIDED ``|z| > threshold``** for every
   event_study question.  This matches ``rule = abs_above`` which is
   template-locked in ``event_study/template.yaml`` line 333.  The
   workflow does NOT honor the prompt's directional language ("widens"
   vs "stretched") — every event_study question fires events on
   absolute-value extremes, in either direction.
9. Forward move (target)   : ``(target[t + post_window] - target[t]) * 100``
   in bps, using positional (trading-day) shift on the target's index.
   Matches ``event_windows`` operator with
   ``units_basis = "level_change"`` + PERCENT→BPS multiplication.
10. Unconditional baseline : average forward move on EVERY signal-defined
    day (where the z-score is finite and a valid t+post target exists).
    Matches the workflow's ``unconditional_events`` sentinel
    (``rule = above, threshold = -1e18``).
11. ``diff (cond - uncond)`` is the canonical event-study deliverable.
    The workflow's terminal artifact is exactly this subtraction, per
    horizon — so ``card[Day +post_window]`` ↔ ``oracle.diff_bps``.
12. Lookback semantics     : ``lookback_days = 1825`` calendar days
    (matches the template's canonical Q1 binding).  Implemented as the
    last ``5 * 252 = 1260`` trading rows AFTER the rolling z-score is
    computed on the buffered series.

Outputs
-------
For each question the script prints:

    Q1 — event_study  (signal=USD_SOFR_OIS swap @2Y, target=UST 10Y, post=5d, σ>1.5 single-day widening)
      window:               2021-01-15 → 2026-01-14
      n_signal_obs (after rolling burn-in):  988
      n_events:             37
      conditional mean fwd 5d move (bps):    +4.21
      unconditional mean fwd 5d move (bps):  +0.18
      conditional std (bps):                 12.87
      unconditional std (bps):                9.34
      diff (cond - uncond) (bps):            +4.03

so you can read them off and tick against the chat card's terminal_artifact
horizon strip + summary stats.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

import numpy as np
import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

# Make sure the project root is importable so `database.get_db_engine` works.
_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from database.database import get_db_engine  # noqa: E402

# ---------------------------------------------------------------------------
# Constants — change here if your DB conventions differ
# ---------------------------------------------------------------------------

SOVEREIGN_FIELD = "YLD_YTM_MID"
OIS_FIELD = "PX_LAST"

# Rolling z-score config — matches the swap_spread / curve_spread
# config.yaml conventions.  ROLLING_MIN_PERIODS = 60 (NOT 126).  The
# rolling stats are computed on a BUFFERED series (lookback + warmup)
# before the display window is trimmed; see WARMUP_BUFFER_DAYS.
ROLLING_WINDOW_DAYS = 252
ROLLING_MIN_PERIODS = 60

# Display window (5 calendar years ≈ 1260 trading days).  Matches the
# canonical Q1 binding's ``lookback_days = 1825``.
LOOKBACK_DAYS_5Y = 5 * 252

# z_score_buffer_multiplier = 1.5 in config — the rolling z-score sees
# 1.5 * 252 ≈ 378 extra trading days BEFORE the display window so the
# z-score is fully warmed by the first displayed row.
WARMUP_BUFFER_DAYS = int(ROLLING_WINDOW_DAYS * 1.5)

# ffill_limit_days = 5 in pivot_and_align_tenors — both legs are
# forward-filled up to 5 days before the spread is computed.
FFILL_LIMIT_DAYS = 5

PCT_TO_BPS = 100.0


# ---------------------------------------------------------------------------
# Raw-data fetcher — one query, returns a date-indexed pandas Series
# ---------------------------------------------------------------------------


def fetch_series(
    engine: Engine,
    *,
    curve_family: str,
    tenor: str,
    field_name: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> pd.Series:
    """Pull ``field_value`` for one (curve_family, tenor, field_name) tuple.

    Returns a pandas Series indexed by trade_date, named after the lookup.
    No primitive / operator code is touched — pure SQL → DataFrame.
    """
    sql = text(
        """
        SELECT d.trade_date, d.field_value
        FROM macro_data.market_data_daily d
        JOIN macro_data.instrument_master i
          ON d.instrument_id = i.instrument_id
        WHERE i.curve_family = :curve_family
          AND i.tenor        = :tenor
          AND d.field_name   = :field_name
          AND (:start_date IS NULL OR d.trade_date >= :start_date)
          AND (:end_date   IS NULL OR d.trade_date <= :end_date)
        ORDER BY d.trade_date ASC
        """
    )
    with engine.connect() as conn:
        df = pd.read_sql_query(
            sql,
            conn,
            params={
                "curve_family": curve_family,
                "tenor": tenor,
                "field_name": field_name,
                "start_date": start_date,
                "end_date": end_date,
            },
        )
    if df.empty:
        return pd.Series(
            dtype=float,
            name=f"{curve_family}|{tenor}|{field_name}",
        )
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    s = df.set_index("trade_date")["field_value"].astype(float).sort_index()
    s = s[~s.index.duplicated(keep="last")]
    s.name = f"{curve_family}|{tenor}|{field_name}"
    return s


# ---------------------------------------------------------------------------
# Spread + z-score helpers (no primitive/operator imports)
# ---------------------------------------------------------------------------


def swap_spread_bps(
    sovereign_yield: pd.Series,
    ois_rate: pd.Series,
) -> pd.Series:
    """``(sov - ois) * 100`` → bps.  Two-leg alignment matches the
    swap_spread primitive's ``pivot_and_align_tenors`` with
    ``ffill_limit_days = 5``: outer-join the two legs, forward-fill up
    to 5 days, then drop any remaining rows where either leg is still
    missing."""
    aligned = pd.concat({"sov": sovereign_yield, "ois": ois_rate}, axis=1)
    aligned = aligned.ffill(limit=FFILL_LIMIT_DAYS).dropna()
    return ((aligned["sov"] - aligned["ois"]) * PCT_TO_BPS).rename("swap_spread_bps")


def curve_spread_bps(
    short_yield: pd.Series,
    long_yield: pd.Series,
) -> pd.Series:
    """``(long - short) * 100`` → bps.  Same two-leg alignment as
    ``swap_spread_bps`` (forward-fill up to 5 days, then drop)."""
    aligned = pd.concat({"short": short_yield, "long": long_yield}, axis=1)
    aligned = aligned.ffill(limit=FFILL_LIMIT_DAYS).dropna()
    return ((aligned["long"] - aligned["short"]) * PCT_TO_BPS).rename("curve_spread_bps")


def rolling_zscore(s: pd.Series) -> pd.Series:
    """252-day rolling z-score (mean + ddof=1 std), ``min_periods=60``.

    Matches the swap_spread / curve_spread primitive's config-driven
    rolling stats.  Rolls on whatever series the caller passes in —
    so callers should pass the FULL buffered series (lookback +
    warmup) and trim the display window AFTER calling this."""
    mu = s.rolling(ROLLING_WINDOW_DAYS, min_periods=ROLLING_MIN_PERIODS).mean()
    sd = s.rolling(ROLLING_WINDOW_DAYS, min_periods=ROLLING_MIN_PERIODS).std(ddof=1)
    return ((s - mu) / sd).rename(f"{s.name}_z")


def daily_change(s: pd.Series) -> pd.Series:
    """Single-day diff."""
    return s.diff().rename(f"{s.name}_d1")


# ---------------------------------------------------------------------------
# Event-study core — pure pandas
# ---------------------------------------------------------------------------


@dataclass
class EventStudyResult:
    label: str
    window_start: pd.Timestamp
    window_end: pd.Timestamp
    n_signal_obs: int
    n_events: int
    conditional_mean_bps: float
    unconditional_mean_bps: float
    conditional_std_bps: float
    unconditional_std_bps: float
    diff_bps: float

    def pretty(self) -> str:
        return (
            f"{self.label}\n"
            f"  window:                                {self.window_start.date()} → {self.window_end.date()}\n"
            f"  n_signal_obs (post burn-in):           {self.n_signal_obs}\n"
            f"  n_events:                              {self.n_events}\n"
            f"  conditional mean fwd move (bps):       {self.conditional_mean_bps:+.4f}\n"
            f"  unconditional mean fwd move (bps):     {self.unconditional_mean_bps:+.4f}\n"
            f"  conditional std (bps):                 {self.conditional_std_bps:.4f}\n"
            f"  unconditional std (bps):               {self.unconditional_std_bps:.4f}\n"
            f"  diff (cond - uncond) (bps):            {self.diff_bps:+.4f}\n"
        )


def event_study(
    *,
    label: str,
    signal_series_bps: pd.Series,
    target_yield_pct: pd.Series,
    threshold_sigma: float,
    post_window: int,
    signal_kind: Literal["level", "change"],
    lookback_days: Optional[int] = LOOKBACK_DAYS_5Y,
    warmup_buffer_days: int = WARMUP_BUFFER_DAYS,
) -> EventStudyResult:
    """Run one event study aligned to the workflow template.

    Pipeline (mirrors ``rates_agent/workflows/event_study/template.yaml``):

      1. Compute the signal z-score on the FULL buffered series
         (lookback + warmup ≈ 5y + 1.5y).  This lets the rolling stats
         warm fully BEFORE the displayed window starts — without it,
         the first ~6 months of the displayed window have NaN z-scores
         and contribute zero events, which is a huge artefact.
      2. Trim the displayed window to the last ``lookback_days`` rows
         AFTER the z-score is computed.
      3. Flag events using the workflow's template-locked
         ``rule = abs_above`` semantic: ``|z| > threshold``.  This is
         used for EVERY event_study question — the prompt's "widens"
         vs "stretched" language does NOT change the rule, only the
         z-score basis (level vs change).
      4. For every signal-defined day with a valid ``t + post_window``
         target, compute the forward move in bps.
      5. Report conditional mean (events only) vs unconditional mean
         (all signal-defined days with a valid forward window).  The
         workflow's terminal artifact at horizon ``post_window`` is
         exactly ``conditional_mean - unconditional_mean`` per offset,
         which is the ``diff_bps`` field of the result.
    """
    # 1. compute z-score on the FULL series (no early trim) ------------------
    sig_full = signal_series_bps.dropna()
    if signal_kind == "level":
        zscore_full = rolling_zscore(sig_full)
    elif signal_kind == "change":
        zscore_full = rolling_zscore(daily_change(sig_full))
    else:
        raise ValueError(f"signal_kind={signal_kind!r}")

    # 2. trim the displayed window to the last lookback_days rows ------------
    # If sig_full is shorter than lookback_days, we just use what we have.
    if lookback_days is not None and len(zscore_full) > lookback_days:
        zscore = zscore_full.iloc[-lookback_days:]
    else:
        zscore = zscore_full

    # 3. event flag — template-locked abs_above (|z| > N) --------------------
    events_mask = zscore.abs() > threshold_sigma

    # 4. forward move computation -------------------------------------------
    target = target_yield_pct.dropna()
    aligned = pd.concat(
        {"signal_z": zscore, "events": events_mask, "target_pct": target},
        axis=1,
    )
    aligned = aligned.dropna(subset=["target_pct"])

    # Forward move in bps: target[t + post] - target[t], times 100.
    # shift(-post_window) is positional (trading-day) shift, matching
    # event_windows operator's ``t_e + k`` panel-index arithmetic.
    aligned["target_t_plus"] = aligned["target_pct"].shift(-post_window)
    aligned["fwd_move_bps"] = (
        (aligned["target_t_plus"] - aligned["target_pct"]) * PCT_TO_BPS
    )

    # Restrict to rows with both a valid forward move AND a valid z-score.
    valid = aligned.dropna(subset=["fwd_move_bps", "signal_z"])

    # 5. Aggregate ------------------------------------------------------------
    cond_rows = valid[valid["events"].fillna(False)]
    uncond_rows = valid

    n_signal_obs = int(len(valid))
    n_events = int(len(cond_rows))

    cond_mean = float(cond_rows["fwd_move_bps"].mean()) if n_events > 0 else float("nan")
    uncond_mean = float(uncond_rows["fwd_move_bps"].mean())

    cond_std = (
        float(cond_rows["fwd_move_bps"].std(ddof=1))
        if n_events > 1
        else float("nan")
    )
    uncond_std = float(uncond_rows["fwd_move_bps"].std(ddof=1))

    diff = (
        float(cond_mean - uncond_mean)
        if not np.isnan(cond_mean)
        else float("nan")
    )

    return EventStudyResult(
        label=label,
        window_start=pd.Timestamp(valid.index.min()) if n_signal_obs > 0 else pd.NaT,
        window_end=pd.Timestamp(valid.index.max()) if n_signal_obs > 0 else pd.NaT,
        n_signal_obs=n_signal_obs,
        n_events=n_events,
        conditional_mean_bps=cond_mean,
        unconditional_mean_bps=uncond_mean,
        conditional_std_bps=cond_std,
        unconditional_std_bps=uncond_std,
        diff_bps=diff,
    )


# ---------------------------------------------------------------------------
# Q1 – Q7 — one function each, wired with the gauntlet's expected slot values
# ---------------------------------------------------------------------------


def q1(engine: Engine) -> EventStudyResult:
    """Q1: 2Y UST-SOFR swap spread |change z| > 1.5 (1d) → 5d 10Y UST move.

    Prompt says "widens" but the workflow's threshold_events rule is
    template-locked to ``abs_above`` — i.e. fires on both widening AND
    narrowing days.  This oracle mirrors that, so card[Day +5] should
    match this run's ``diff_bps``.
    """
    sov_2y = fetch_series(
        engine,
        curve_family="UST",
        tenor="2Y",
        field_name=SOVEREIGN_FIELD,
    )
    ois_2y = fetch_series(
        engine,
        curve_family="USD_SOFR_OIS",
        tenor="2Y",
        field_name=OIS_FIELD,
    )
    target_10y = fetch_series(
        engine,
        curve_family="UST",
        tenor="10Y",
        field_name=SOVEREIGN_FIELD,
    )
    signal_bps = swap_spread_bps(sov_2y, ois_2y)
    return event_study(
        label="Q1 — 2Y UST-SOFR swap spread |Δz|>1.5 (1d) → 5d 10Y UST",
        signal_series_bps=signal_bps,
        target_yield_pct=target_10y,
        threshold_sigma=1.5,
        post_window=5,
        signal_kind="change",
    )


def q2(engine: Engine) -> EventStudyResult:
    """Q2: 5Y UST-SOFR swap spread |Δz|>2 (1d) → 10d 5Y UST move."""
    sov_5y = fetch_series(engine, curve_family="UST", tenor="5Y", field_name=SOVEREIGN_FIELD)
    ois_5y = fetch_series(engine, curve_family="USD_SOFR_OIS", tenor="5Y", field_name=OIS_FIELD)
    target_5y = sov_5y  # target is the same 5Y UST
    signal_bps = swap_spread_bps(sov_5y, ois_5y)
    return event_study(
        label="Q2 — 5Y UST-SOFR swap spread |Δz|>2 (1d) → 10d 5Y UST",
        signal_series_bps=signal_bps,
        target_yield_pct=target_5y,
        threshold_sigma=2.0,
        post_window=10,
        signal_kind="change",
    )


def q3(engine: Engine) -> EventStudyResult:
    """Q3: 10Y BUND-ESTR swap spread |Δz|>1.5 (1d) → 3d 10Y BUND move."""
    sov_10y = fetch_series(engine, curve_family="DE_BUND", tenor="10Y", field_name=SOVEREIGN_FIELD)
    ois_10y = fetch_series(engine, curve_family="EUR_ESTR_OIS", tenor="10Y", field_name=OIS_FIELD)
    target_10y = sov_10y
    signal_bps = swap_spread_bps(sov_10y, ois_10y)
    return event_study(
        label="Q3 — 10Y BUND-ESTR swap spread |Δz|>1.5 (1d) → 3d 10Y BUND",
        signal_series_bps=signal_bps,
        target_yield_pct=target_10y,
        threshold_sigma=1.5,
        post_window=3,
        signal_kind="change",
    )


def q4(engine: Engine) -> EventStudyResult:
    """Q4: 30Y UST-SOFR swap spread |Δz|>1 (1d) → 5d 30Y UST move."""
    sov_30y = fetch_series(engine, curve_family="UST", tenor="30Y", field_name=SOVEREIGN_FIELD)
    ois_30y = fetch_series(engine, curve_family="USD_SOFR_OIS", tenor="30Y", field_name=OIS_FIELD)
    target_30y = sov_30y
    signal_bps = swap_spread_bps(sov_30y, ois_30y)
    return event_study(
        label="Q4 — 30Y UST-SOFR swap spread |Δz|>1 (1d) → 5d 30Y UST",
        signal_series_bps=signal_bps,
        target_yield_pct=target_30y,
        threshold_sigma=1.0,
        post_window=5,
        signal_kind="change",
    )


def q5(engine: Engine) -> EventStudyResult:
    """Q5: 10Y GILT-SONIA swap spread |Δz|>2 (1d) → 5d 10Y GILT move."""
    sov_10y = fetch_series(engine, curve_family="UK_GILT", tenor="10Y", field_name=SOVEREIGN_FIELD)
    ois_10y = fetch_series(engine, curve_family="GBP_SONIA_OIS", tenor="10Y", field_name=OIS_FIELD)
    target_10y = sov_10y
    signal_bps = swap_spread_bps(sov_10y, ois_10y)
    return event_study(
        label="Q5 — 10Y GILT-SONIA swap spread |Δz|>2 (1d) → 5d 10Y GILT",
        signal_series_bps=signal_bps,
        target_yield_pct=target_10y,
        threshold_sigma=2.0,
        post_window=5,
        signal_kind="change",
    )


def q6(engine: Engine) -> EventStudyResult:
    """Q6: 2Y UST-SOFR swap spread LEVEL > 1.5σ stretched → 5d 10Y UST move.

    Note the signal_kind change: this question is about the LEVEL z-score
    of the spread, not the daily-change z-score.
    """
    sov_2y = fetch_series(engine, curve_family="UST", tenor="2Y", field_name=SOVEREIGN_FIELD)
    ois_2y = fetch_series(engine, curve_family="USD_SOFR_OIS", tenor="2Y", field_name=OIS_FIELD)
    target_10y = fetch_series(engine, curve_family="UST", tenor="10Y", field_name=SOVEREIGN_FIELD)
    signal_bps = swap_spread_bps(sov_2y, ois_2y)
    return event_study(
        label="Q6 — 2Y UST-SOFR swap spread LEVEL >1.5σ stretched → 5d 10Y UST",
        signal_series_bps=signal_bps,
        target_yield_pct=target_10y,
        threshold_sigma=1.5,
        post_window=5,
        signal_kind="level",
    )


def q7(engine: Engine) -> EventStudyResult:
    """Q7: UST 2s10s curve spread LEVEL > 2σ stretched → 10d 30Y UST move."""
    sov_2y = fetch_series(engine, curve_family="UST", tenor="2Y", field_name=SOVEREIGN_FIELD)
    sov_10y = fetch_series(engine, curve_family="UST", tenor="10Y", field_name=SOVEREIGN_FIELD)
    target_30y = fetch_series(engine, curve_family="UST", tenor="30Y", field_name=SOVEREIGN_FIELD)
    signal_bps = curve_spread_bps(sov_2y, sov_10y)
    return event_study(
        label="Q7 — UST 2s10s curve spread LEVEL >2σ stretched → 10d 30Y UST",
        signal_series_bps=signal_bps,
        target_yield_pct=target_30y,
        threshold_sigma=2.0,
        post_window=10,
        signal_kind="level",
    )


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def main() -> int:
    engine = get_db_engine()
    results = []
    for fn in (q1, q2, q3, q4, q5, q6, q7):
        try:
            results.append(fn(engine))
        except Exception as exc:  # pragma: no cover  — diagnostic printer
            print(f"\n!! {fn.__name__} failed: {exc}\n")
    print("\n" + "=" * 78)
    print("ORACLE RESULTS — compare these to the WorkflowResultCard in chat")
    print("=" * 78 + "\n")
    for r in results:
        print(r.pretty())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
