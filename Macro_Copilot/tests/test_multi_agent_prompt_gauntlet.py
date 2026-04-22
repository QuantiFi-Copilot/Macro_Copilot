#!/usr/bin/env python3
"""
test_multi_agent_prompt_gauntlet.py — Multi-agent prompt truth harness
========================================================================

Purpose
-------
Exercise the prompt gauntlet with a fixed deterministic truth surface
and produce a two-way comparison:

1. The direct Python tool outputs
2. Independent deterministic DB-sourced baselines

This is a prompt-level harness, not a substitute for the individual
per-tool validators in ``tests/test_*_sql_validation.py``.  It is
designed to answer a different question:

    "Given a real PM-style prompt, what should the underlying tool
    calls be, and do those tool outputs agree with an independent
    deterministic baseline?"

Usage
-----
    python3.10 -m tests.test_multi_agent_prompt_gauntlet
    python3.10 -m tests.test_multi_agent_prompt_gauntlet --group ois
    python3.10 -m tests.test_multi_agent_prompt_gauntlet --case ois_04
    python3.10 -m tests.test_multi_agent_prompt_gauntlet --report-json /tmp/gauntlet.json

Notes
-----
- For prompt cases whose wording is not numerically deterministic
  (e.g. month-only date-window forwards), this harness documents and
  enforces an explicit canonical parameter mapping.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence

import pandas as pd
from sqlalchemy import text


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from database.database import get_db_engine  # noqa: E402
from rates_agent.ois.tools.cross_market_spread import calculate_ois_cross_market_spread  # noqa: E402
from rates_agent.ois.tools.curve_spread import calculate_ois_curve_spread  # noqa: E402
from rates_agent.ois.tools.forward_rate import calculate_ois_forward_rate  # noqa: E402
from rates_agent.ois.tools.rate_level import get_ois_rate_level  # noqa: E402
from rates_agent.ois.tools.scanner import scan_ois_extremes  # noqa: E402
from rates_agent.ois.tools.schemas import (  # noqa: E402
    OISCrossMarketSpreadInput,
    OISCurveSpreadInput,
    OISForwardRateInput,
    OISRateLevelInput,
    OISScannerInput,
)
from tests import test_butterfly_sql_validation as sov_bfly_validator  # noqa: E402
from tests import test_cross_market_sql_validation as sov_cross_validator  # noqa: E402
from tests import test_curve_regime_sql_validation as sov_regime_validator  # noqa: E402
from tests import test_curve_spread_sql_validation as sov_spread_validator  # noqa: E402
from tests import test_scanner_sql_validation as sov_scanner_validator  # noqa: E402
from tests import test_yield_levels_sql_validation as sov_yield_validator  # noqa: E402
from tests.sql_validation_common import (  # noqa: E402
    add_exact_field_mismatches,
    add_numeric_field_mismatches,
    compare_time_series,
    floats_match,
    numeric_delta,
    print_case_header,
)


YLD_FIELD = "YLD_YTM_MID"
OIS_FIELD = "PX_LAST"
LOOKBACK_DAYS = 365

OIS_RATE_TOLERANCE = {
    "current_rate_pct": 0.00011,
    "daily_change_bps": 0.011,
    "weekly_change_bps": 0.011,
    "monthly_change_bps": 0.011,
    "z_score": 0.006,
    "high_252d_pct": 0.00011,
    "low_252d_pct": 0.00011,
    "percentile_252d": 0.11,
}

OIS_CROSS_TOLERANCE = {
    "current_spread_bps": 0.011,
    "daily_change_bps": 0.011,
    "weekly_change_bps": 0.011,
    "monthly_change_bps": 0.011,
    "current_z_score": 0.006,
    "high_252d_bps": 0.011,
    "low_252d_bps": 0.011,
    "percentile_252d": 0.11,
    "curve_family_1_rate": 0.00011,
    "curve_family_2_rate": 0.00011,
    "spread_bps": 0.011,
    "z_score": 0.006,
}

OIS_CURVE_SPREAD_TOLERANCE = {
    "current_spread_bps": 0.011,
    "daily_change_bps": 0.011,
    "current_z_score": 0.006,
    "short_tenor_rate": 0.00011,
    "long_tenor_rate": 0.00011,
    "spread_bps": 0.011,
    "z_score": 0.006,
}

OIS_FORWARD_TOLERANCE = {
    "start_years": 0.00011,
    "end_years": 0.00011,
    "forward_rate_pct": 0.00011,
    "daily_change_bps": 0.011,
    "current_z_score": 0.006,
    "high_252d_pct": 0.00011,
    "low_252d_pct": 0.00011,
    "percentile_252d": 0.11,
    "start_spot_rate_pct": 0.00011,
    "end_spot_rate_pct": 0.00011,
    "z_score": 0.006,
}

OIS_SCANNER_TOLERANCE = {
    "current_rate_pct": 0.00011,
    "daily_change_bps": 0.011,
    "z_score": 0.006,
    "high_252d_pct": 0.00011,
    "low_252d_pct": 0.00011,
    "percentile_252d": 0.11,
}

DAY_COUNT_360_CURVES = frozenset({"USD_SOFR_OIS", "EUR_ESTR_OIS"})
DEFAULT_PERIOD_OFFSETS = {"daily": 2, "weekly": 6, "monthly": 22}
Z_SCORE_WINDOW = 252
Z_SCORE_MIN_PERIODS = 60
BUFFER_DAYS = int(Z_SCORE_WINDOW * 1.5)

TENOR_UNIT_TO_YEARS = {
    "D": 1.0 / 365.0,
    "W": 7.0 / 365.0,
    "M": 1.0 / 12.0,
    "Y": 1.0,
}


@dataclass
class OperationSpec:
    name: str
    tool_name: str
    domain: str
    params: dict[str, Any]
    expected_param_subset: dict[str, Any]
    run_tool: Callable[[Any], dict[str, Any]]
    run_sql: Callable[[Any], dict[str, Any]]
    compare: Callable[[dict[str, Any], dict[str, Any]], list[str]]
    summarize: Callable[[dict[str, Any]], Any]


@dataclass
class OperationOutcome:
    spec: OperationSpec
    tool_result: dict[str, Any]
    sql_result: dict[str, Any]
    mismatches: list[str]
    tool_summary: Any
    sql_summary: Any


@dataclass
class DeterministicCaseResult:
    numeric_supported: bool
    outcomes: list[OperationOutcome] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class PromptCase:
    case_id: str
    group: str
    prompt: str
    expected_action: str
    expected_domains: tuple[str, ...]
    runner: Callable[[Any], DeterministicCaseResult]


def _local_safe_float(value: Any, decimals: int = 4) -> Optional[float]:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(f):
        return None
    return round(f, decimals)


def _local_bps_change(current: Any, previous: Any) -> Optional[float]:
    if current is None or previous is None:
        return None
    try:
        cur = float(current)
        prev = float(previous)
    except (TypeError, ValueError):
        return None
    if math.isnan(cur) or math.isnan(prev):
        return None
    return round((cur - prev) * 100, 2)


def _local_delta_bps(current: Any, previous: Any) -> Optional[float]:
    if current is None or previous is None:
        return None
    try:
        cur = float(current)
        prev = float(previous)
    except (TypeError, ValueError):
        return None
    if math.isnan(cur) or math.isnan(prev):
        return None
    return round(cur - prev, 2)


def _local_period_changes(
    series: pd.Series,
    *,
    already_bps: bool = False,
    offsets: Mapping[str, int] = DEFAULT_PERIOD_OFFSETS,
) -> dict[str, Optional[float]]:
    if len(series) == 0:
        return {label: None for label in offsets}
    current = series.iloc[-1]
    change_fn = _local_delta_bps if already_bps else _local_bps_change
    out: dict[str, Optional[float]] = {}
    for label, offset in offsets.items():
        out[label] = (
            change_fn(current, series.iloc[-offset]) if len(series) >= offset else None
        )
    return out


def _local_rolling_zscore(
    series: pd.Series,
    *,
    window: int = Z_SCORE_WINDOW,
    min_periods: int = Z_SCORE_MIN_PERIODS,
    round_decimals: int = 4,
) -> pd.Series:
    rolling_mean = series.rolling(window=window, min_periods=min_periods).mean()
    rolling_std = series.rolling(window=window, min_periods=min_periods).std()
    return ((series - rolling_mean) / rolling_std).round(round_decimals)


def _local_trailing_high_low_percentile(
    series: pd.Series,
    *,
    window: int = Z_SCORE_WINDOW,
    decimals: int = 4,
) -> tuple[Optional[float], Optional[float], Optional[float]]:
    if len(series) == 0:
        return None, None, None
    trailing = series.iloc[-window:] if len(series) >= window else series
    high = _local_safe_float(trailing.max(), decimals=decimals)
    low = _local_safe_float(trailing.min(), decimals=decimals)
    current = _local_safe_float(series.iloc[-1], decimals=8)
    if high is None or low is None or current is None or high == low:
        return high, low, None
    return high, low, round((current - low) / (high - low) * 100, 1)


def _local_clean_single_series(raw_df: pd.DataFrame) -> pd.DataFrame:
    df = raw_df.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["field_value"] = pd.to_numeric(df["field_value"], errors="coerce")
    df = df.dropna(subset=["field_value"])
    df = df.drop_duplicates(subset=["trade_date"], keep="last")
    df = df.set_index("trade_date").sort_index()
    df = df.ffill(limit=5)
    return df


def _local_pivot_and_align(
    raw_df: pd.DataFrame,
    *,
    key_col: str,
    required_keys: Sequence[str],
) -> pd.DataFrame:
    df = raw_df.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["field_value"] = pd.to_numeric(df["field_value"], errors="coerce")
    df = df.drop_duplicates(subset=["trade_date", key_col], keep="last")
    wide = df.pivot(index="trade_date", columns=key_col, values="field_value")
    wide = wide.sort_index().ffill(limit=5)
    wide = wide.dropna(subset=list(required_keys))
    return wide


def _local_tenor_to_years(tenor: str) -> float:
    unit = tenor[-1].upper()
    magnitude = int(tenor[:-1])
    if unit not in TENOR_UNIT_TO_YEARS:
        raise ValueError(f"Unsupported tenor {tenor!r}")
    return magnitude * TENOR_UNIT_TO_YEARS[unit]


def _local_sort_tenors(tenors: Iterable[str]) -> list[str]:
    parsed: list[tuple[float, str]] = []
    for tenor in tenors:
        try:
            parsed.append((_local_tenor_to_years(tenor), tenor))
        except ValueError:
            continue
    parsed.sort(key=lambda item: item[0])
    return [tenor for _, tenor in parsed]


def _local_day_count_basis(curve_family: str) -> int:
    return 360 if curve_family in DAY_COUNT_360_CURVES else 365


def _local_discount_factor_from_par(par_rate: float, years: float) -> float:
    if years < 0:
        raise ValueError(f"years must be non-negative, got {years}")
    if years <= 1.0:
        return 1.0 / (1.0 + par_rate * years)
    return 1.0 / ((1.0 + par_rate) ** years)


def _local_interpolate_rate(
    curve_years: Sequence[float],
    curve_rates: Sequence[float],
    target_years: float,
) -> float:
    if not curve_years:
        raise ValueError("Curve is empty.")
    if len(curve_years) != len(curve_rates):
        raise ValueError("Curve length mismatch.")
    if target_years <= curve_years[0]:
        return curve_rates[0]
    if target_years >= curve_years[-1]:
        return curve_rates[-1]
    for idx in range(1, len(curve_years)):
        left_years = curve_years[idx - 1]
        right_years = curve_years[idx]
        if left_years <= target_years <= right_years:
            left_rate = curve_rates[idx - 1]
            right_rate = curve_rates[idx]
            if right_years == left_years:
                return left_rate
            frac = (target_years - left_years) / (right_years - left_years)
            return left_rate + frac * (right_rate - left_rate)
    return curve_rates[-1]


def _local_forward_rate_between(
    curve_years: Sequence[float],
    curve_rates_decimal: Sequence[float],
    start_years: float,
    end_years: float,
) -> float:
    if end_years <= start_years:
        raise ValueError("Forward window collapsed.")
    if start_years < 0 or end_years < 0:
        raise ValueError("Forward window must be non-negative from as-of.")
    r_start = _local_interpolate_rate(curve_years, curve_rates_decimal, start_years)
    r_end = _local_interpolate_rate(curve_years, curve_rates_decimal, end_years)
    df_start = (
        _local_discount_factor_from_par(r_start, start_years)
        if start_years > 0
        else 1.0
    )
    df_end = _local_discount_factor_from_par(r_end, end_years)
    return (df_start / df_end - 1.0) / (end_years - start_years)


def _local_forward_label(
    curve_family: str,
    *,
    start_tenor: Optional[str] = None,
    end_tenor: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> str:
    curve_short = curve_family.replace("_OIS", "").replace("_", " ")
    if start_tenor and end_tenor:
        start_years = _local_tenor_to_years(start_tenor)
        end_years = _local_tenor_to_years(end_tenor)
        widened = end_years - start_years
        if (
            start_tenor.endswith("Y")
            and end_tenor.endswith("Y")
            and abs(widened - start_years) < 1e-9
        ):
            return f"{curve_short} {int(start_years)}Y{int(widened)}Y"
        return f"{curve_short} {start_tenor}/{end_tenor}"
    return f"{curve_short} {start_date} to {end_date}"


def _sql_to_frame(result) -> pd.DataFrame:
    rows = result.fetchall()
    columns = list(result.keys())
    return pd.DataFrame(rows, columns=columns)


def _fetch_ois_single_raw(
    engine,
    *,
    curve_family: str,
    tenor: str,
    field_name: str,
    start_date: date,
) -> pd.DataFrame:
    sql = text(
        """
        SELECT trade_date, field_value
        FROM macro_data.v_market_data_daily_enriched
        WHERE curve_family = :curve_family
          AND tenor = :tenor
          AND field_name = :field_name
          AND trade_date >= :start_date
        ORDER BY trade_date
        """
    )
    with engine.connect() as conn:
        result = conn.execute(
            sql,
            {
                "curve_family": curve_family,
                "tenor": tenor,
                "field_name": field_name,
                "start_date": start_date.isoformat(),
            },
        )
        return _sql_to_frame(result)


def _fetch_ois_cross_raw(
    engine,
    *,
    curve_family_1: str,
    curve_family_2: str,
    tenor: str,
    field_name: str,
    start_date: date,
) -> pd.DataFrame:
    sql = text(
        """
        SELECT trade_date, curve_family, field_value
        FROM macro_data.v_market_data_daily_enriched
        WHERE curve_family = ANY(:curve_families)
          AND tenor = :tenor
          AND field_name = :field_name
          AND trade_date >= :start_date
        ORDER BY trade_date, curve_family
        """
    )
    with engine.connect() as conn:
        result = conn.execute(
            sql,
            {
                "curve_families": [curve_family_1, curve_family_2],
                "tenor": tenor,
                "field_name": field_name,
                "start_date": start_date.isoformat(),
            },
        )
        return _sql_to_frame(result)


def _fetch_ois_curve_spread_raw(
    engine,
    *,
    curve_family: str,
    short_tenor: str,
    long_tenor: str,
    field_name: str,
    start_date: date,
) -> pd.DataFrame:
    sql = text(
        """
        SELECT trade_date, tenor, field_value
        FROM macro_data.v_market_data_daily_enriched
        WHERE curve_family = :curve_family
          AND tenor = ANY(:tenors)
          AND field_name = :field_name
          AND trade_date >= :start_date
        ORDER BY trade_date, tenor
        """
    )
    with engine.connect() as conn:
        result = conn.execute(
            sql,
            {
                "curve_family": curve_family,
                "tenors": [short_tenor, long_tenor],
                "field_name": field_name,
                "start_date": start_date.isoformat(),
            },
        )
        return _sql_to_frame(result)


def _fetch_ois_full_curve_raw(
    engine,
    *,
    curve_family: str,
    field_name: str,
    start_date: date,
) -> pd.DataFrame:
    sql = text(
        """
        SELECT trade_date, tenor, field_value
        FROM macro_data.v_market_data_daily_enriched
        WHERE curve_family = :curve_family
          AND field_name = :field_name
          AND trade_date >= :start_date
          AND tenor IS NOT NULL
        ORDER BY trade_date, tenor
        """
    )
    with engine.connect() as conn:
        result = conn.execute(
            sql,
            {
                "curve_family": curve_family,
                "field_name": field_name,
                "start_date": start_date.isoformat(),
            },
        )
        return _sql_to_frame(result)


def _fetch_ois_scan_universe_raw(
    engine,
    *,
    field_name: str,
    start_date: date,
    curve_families: Optional[Sequence[str]],
) -> pd.DataFrame:
    if curve_families:
        sql = text(
            """
            SELECT trade_date, curve_family, tenor, field_value
            FROM macro_data.v_market_data_daily_enriched
            WHERE instrument_type = 'ois_swap'
              AND field_name = :field_name
              AND trade_date >= :start_date
              AND tenor IS NOT NULL
              AND curve_family = ANY(:curve_families)
            ORDER BY curve_family, tenor, trade_date
            """
        )
        params = {
            "field_name": field_name,
            "start_date": start_date.isoformat(),
            "curve_families": list(curve_families),
        }
    else:
        sql = text(
            """
            SELECT trade_date, curve_family, tenor, field_value
            FROM macro_data.v_market_data_daily_enriched
            WHERE instrument_type = 'ois_swap'
              AND field_name = :field_name
              AND trade_date >= :start_date
              AND tenor IS NOT NULL
            ORDER BY curve_family, tenor, trade_date
            """
        )
        params = {
            "field_name": field_name,
            "start_date": start_date.isoformat(),
        }
    with engine.connect() as conn:
        result = conn.execute(sql, params)
        return _sql_to_frame(result)


def ois_rate_level_sql_baseline(
    engine,
    *,
    curve_family: str,
    tenor: str,
    lookback_days: int,
    field_name: str,
) -> dict[str, Any]:
    start_date = date.today() - timedelta(days=lookback_days + BUFFER_DAYS)
    raw_df = _fetch_ois_single_raw(
        engine,
        curve_family=curve_family,
        tenor=tenor,
        field_name=field_name,
        start_date=start_date,
    )
    if raw_df.empty:
        return {"error": "SQL baseline returned no rows."}
    clean_df = _local_clean_single_series(raw_df)
    if clean_df.empty:
        return {"error": "SQL baseline cleaned to empty series."}
    rates = clean_df["field_value"]
    as_of_date = rates.index[-1].date()
    cutoff = pd.Timestamp(as_of_date - timedelta(days=lookback_days))
    display_rates = rates.loc[rates.index >= cutoff]
    if display_rates.empty:
        return {"error": "No displayed observations after cutoff."}
    changes = _local_period_changes(rates)
    z_series = _local_rolling_zscore(rates)
    high_252, low_252, percentile = _local_trailing_high_low_percentile(
        rates, window=Z_SCORE_WINDOW, decimals=4
    )
    return {
        "current_metrics": {
            "as_of_date": rates.index[-1].strftime("%Y-%m-%d"),
            "curve_family": curve_family,
            "tenor": tenor,
            "current_rate_pct": _local_safe_float(rates.iloc[-1]),
            "daily_change_bps": changes["daily"],
            "weekly_change_bps": changes["weekly"],
            "monthly_change_bps": changes["monthly"],
            "z_score": _local_safe_float(z_series.iloc[-1]),
            "high_252d_pct": high_252,
            "low_252d_pct": low_252,
            "percentile_252d": percentile,
            "observation_count": len(display_rates),
        }
    }


def compare_ois_rate_level(tool_result: dict[str, Any], sql_result: dict[str, Any]) -> list[str]:
    mismatches: list[str] = []
    if "error" in tool_result:
        return [f"Tool returned error: {tool_result['error']}"]
    if "error" in sql_result:
        return [f"SQL baseline returned error: {sql_result['error']}"]
    tool_metrics = tool_result["current_metrics"]
    sql_metrics = sql_result["current_metrics"]
    add_exact_field_mismatches(
        mismatches=mismatches,
        tool_payload=tool_metrics,
        sql_payload=sql_metrics,
        fields=("as_of_date", "curve_family", "tenor", "observation_count"),
        prefix="current_metrics.",
    )
    add_numeric_field_mismatches(
        mismatches=mismatches,
        tool_payload=tool_metrics,
        sql_payload=sql_metrics,
        fields=(
            "current_rate_pct",
            "daily_change_bps",
            "weekly_change_bps",
            "monthly_change_bps",
            "z_score",
            "high_252d_pct",
            "low_252d_pct",
            "percentile_252d",
        ),
        tolerances=OIS_RATE_TOLERANCE,
        prefix="current_metrics.",
    )
    return mismatches


def ois_cross_market_sql_baseline(
    engine,
    *,
    curve_family_1: str,
    curve_family_2: str,
    tenor: str,
    lookback_days: int,
    field_name: str,
) -> dict[str, Any]:
    start_date = date.today() - timedelta(days=lookback_days + BUFFER_DAYS)
    raw_df = _fetch_ois_cross_raw(
        engine,
        curve_family_1=curve_family_1,
        curve_family_2=curve_family_2,
        tenor=tenor,
        field_name=field_name,
        start_date=start_date,
    )
    if raw_df.empty:
        return {"error": "SQL baseline returned no rows."}
    wide = _local_pivot_and_align(
        raw_df,
        key_col="curve_family",
        required_keys=(curve_family_1, curve_family_2),
    )
    if wide.empty:
        return {"error": "No overlapping aligned rows."}
    wide["spread_bps"] = ((wide[curve_family_1] - wide[curve_family_2]) * 100).round(2)
    wide["z_score"] = _local_rolling_zscore(wide["spread_bps"])
    as_of_date = wide.index[-1].date()
    cutoff = pd.Timestamp(as_of_date - timedelta(days=lookback_days))
    display_df = wide.loc[wide.index >= cutoff].copy()
    if display_df.empty:
        return {"error": "No displayed rows after cutoff."}
    latest = display_df.iloc[-1]
    spreads = display_df["spread_bps"]
    changes = _local_period_changes(spreads, already_bps=True)
    high_252, low_252, percentile = _local_trailing_high_low_percentile(
        spreads, window=Z_SCORE_WINDOW, decimals=2
    )
    return {
        "current_metrics": {
            "as_of_date": display_df.index[-1].strftime("%Y-%m-%d"),
            "curve_family_1": curve_family_1,
            "curve_family_2": curve_family_2,
            "tenor": tenor,
            "spread_label": f"{curve_family_1}-{curve_family_2} {tenor}",
            "current_spread_bps": _local_safe_float(latest["spread_bps"], decimals=2),
            "daily_change_bps": changes["daily"],
            "weekly_change_bps": changes["weekly"],
            "monthly_change_bps": changes["monthly"],
            "current_z_score": _local_safe_float(latest["z_score"]),
            "rolling_window_days": Z_SCORE_WINDOW,
            "high_252d_bps": high_252,
            "low_252d_bps": low_252,
            "percentile_252d": percentile,
            "curve_family_1_rate": _local_safe_float(latest[curve_family_1]),
            "curve_family_2_rate": _local_safe_float(latest[curve_family_2]),
        },
        "time_series": [
            {
                "date": idx.strftime("%Y-%m-%d"),
                "spread_bps": round(float(row["spread_bps"]), 2),
                "z_score": _local_safe_float(row["z_score"]),
            }
            for idx, row in display_df.iterrows()
        ],
    }


def compare_ois_cross_market(tool_result: dict[str, Any], sql_result: dict[str, Any]) -> list[str]:
    mismatches: list[str] = []
    if "error" in tool_result:
        return [f"Tool returned error: {tool_result['error']}"]
    if "error" in sql_result:
        return [f"SQL baseline returned error: {sql_result['error']}"]
    tool_metrics = tool_result["current_metrics"]
    sql_metrics = sql_result["current_metrics"]
    add_exact_field_mismatches(
        mismatches=mismatches,
        tool_payload=tool_metrics,
        sql_payload=sql_metrics,
        fields=(
            "as_of_date",
            "curve_family_1",
            "curve_family_2",
            "tenor",
            "spread_label",
            "rolling_window_days",
        ),
        prefix="current_metrics.",
    )
    add_numeric_field_mismatches(
        mismatches=mismatches,
        tool_payload=tool_metrics,
        sql_payload=sql_metrics,
        fields=(
            "current_spread_bps",
            "daily_change_bps",
            "weekly_change_bps",
            "monthly_change_bps",
            "current_z_score",
            "high_252d_bps",
            "low_252d_bps",
            "percentile_252d",
            "curve_family_1_rate",
            "curve_family_2_rate",
        ),
        tolerances=OIS_CROSS_TOLERANCE,
        prefix="current_metrics.",
    )
    mismatches.extend(
        compare_time_series(
            tool_rows=tool_result["time_series"],
            sql_rows=sql_result["time_series"],
            exact_fields=(),
            numeric_fields=("spread_bps", "z_score"),
            tolerances=OIS_CROSS_TOLERANCE,
        )
    )
    return mismatches


def ois_curve_spread_sql_baseline(
    engine,
    *,
    curve_family: str,
    short_tenor: str,
    long_tenor: str,
    lookback_days: int,
    field_name: str,
) -> dict[str, Any]:
    start_date = date.today() - timedelta(days=lookback_days + BUFFER_DAYS)
    raw_df = _fetch_ois_curve_spread_raw(
        engine,
        curve_family=curve_family,
        short_tenor=short_tenor,
        long_tenor=long_tenor,
        field_name=field_name,
        start_date=start_date,
    )
    if raw_df.empty:
        return {"error": "SQL baseline returned no rows."}
    wide = _local_pivot_and_align(
        raw_df,
        key_col="tenor",
        required_keys=(short_tenor, long_tenor),
    )
    if wide.empty:
        return {"error": "No overlapping aligned rows."}
    wide["spread_bps"] = ((wide[long_tenor] - wide[short_tenor]) * 100).round(2)
    wide["z_score"] = _local_rolling_zscore(wide["spread_bps"])
    as_of_date = wide.index[-1].date()
    cutoff = pd.Timestamp(as_of_date - timedelta(days=lookback_days))
    display_df = wide.loc[wide.index >= cutoff].copy()
    if display_df.empty:
        return {"error": "No displayed rows after cutoff."}
    latest = display_df.iloc[-1]
    previous = display_df.iloc[-2] if len(display_df) >= 2 else None
    return {
        "current_metrics": {
            "as_of_date": display_df.index[-1].strftime("%Y-%m-%d"),
            "curve_family": curve_family,
            "spread_label": (
                f"{short_tenor.replace('Y', '')}s{long_tenor.replace('Y', '')}s"
                if short_tenor.endswith("Y") and long_tenor.endswith("Y")
                else f"{short_tenor}/{long_tenor}"
            ),
            "current_spread_bps": _local_safe_float(latest["spread_bps"], decimals=2),
            "daily_change_bps": (
                _local_safe_float(latest["spread_bps"] - previous["spread_bps"], decimals=2)
                if previous is not None
                else None
            ),
            "current_z_score": _local_safe_float(latest["z_score"]),
            "rolling_window_days": Z_SCORE_WINDOW,
            "short_tenor_rate": _local_safe_float(latest[short_tenor]),
            "long_tenor_rate": _local_safe_float(latest[long_tenor]),
        },
        "time_series": [
            {
                "date": idx.strftime("%Y-%m-%d"),
                "spread_bps": round(float(row["spread_bps"]), 2),
                "z_score": _local_safe_float(row["z_score"]),
            }
            for idx, row in display_df.iterrows()
        ],
    }


def compare_ois_curve_spread(tool_result: dict[str, Any], sql_result: dict[str, Any]) -> list[str]:
    mismatches: list[str] = []
    if "error" in tool_result:
        return [f"Tool returned error: {tool_result['error']}"]
    if "error" in sql_result:
        return [f"SQL baseline returned error: {sql_result['error']}"]
    tool_metrics = tool_result["current_metrics"]
    sql_metrics = sql_result["current_metrics"]
    add_exact_field_mismatches(
        mismatches=mismatches,
        tool_payload=tool_metrics,
        sql_payload=sql_metrics,
        fields=("as_of_date", "curve_family", "spread_label", "rolling_window_days"),
        prefix="current_metrics.",
    )
    add_numeric_field_mismatches(
        mismatches=mismatches,
        tool_payload=tool_metrics,
        sql_payload=sql_metrics,
        fields=(
            "current_spread_bps",
            "daily_change_bps",
            "current_z_score",
            "short_tenor_rate",
            "long_tenor_rate",
        ),
        tolerances=OIS_CURVE_SPREAD_TOLERANCE,
        prefix="current_metrics.",
    )
    mismatches.extend(
        compare_time_series(
            tool_rows=tool_result["time_series"],
            sql_rows=sql_result["time_series"],
            exact_fields=(),
            numeric_fields=("spread_bps", "z_score"),
            tolerances=OIS_CURVE_SPREAD_TOLERANCE,
        )
    )
    return mismatches


def _local_forward_window_from_trade_date(
    *,
    trade_date_obj: date,
    curve_family: str,
    start_tenor: Optional[str],
    end_tenor: Optional[str],
    start_date_value: Optional[str],
    end_date_value: Optional[str],
) -> Optional[tuple[float, float]]:
    if start_tenor and end_tenor:
        start_years = _local_tenor_to_years(start_tenor)
        end_years = _local_tenor_to_years(end_tenor)
        if end_years <= start_years:
            raise ValueError("Tenor-mode forward window collapsed.")
        return start_years, end_years

    if not start_date_value or not end_date_value:
        raise ValueError("Date-mode forward window missing date(s).")

    start_date_obj = datetime.strptime(start_date_value, "%Y-%m-%d").date()
    end_date_obj = datetime.strptime(end_date_value, "%Y-%m-%d").date()
    if end_date_obj <= start_date_obj:
        raise ValueError("Date-mode end_date must be strictly after start_date.")
    day_count = _local_day_count_basis(curve_family)
    start_years = (start_date_obj - trade_date_obj).days / day_count
    end_years = (end_date_obj - trade_date_obj).days / day_count
    if start_years < 0:
        return None
    if end_years <= start_years:
        return None
    return start_years, end_years


def _compute_local_forward_series(
    raw_df: pd.DataFrame,
    *,
    curve_family: str,
    start_tenor: Optional[str],
    end_tenor: Optional[str],
    start_date_value: Optional[str],
    end_date_value: Optional[str],
) -> pd.Series:
    df = raw_df.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["field_value"] = pd.to_numeric(df["field_value"], errors="coerce")
    df = df.dropna(subset=["field_value"])
    df = df.drop_duplicates(subset=["trade_date", "tenor"], keep="last")

    forwards: dict[pd.Timestamp, float] = {}

    for trade_date_ts, day_df in df.groupby("trade_date"):
        trade_date_obj = trade_date_ts.date()
        try:
            window = _local_forward_window_from_trade_date(
                trade_date_obj=trade_date_obj,
                curve_family=curve_family,
                start_tenor=start_tenor,
                end_tenor=end_tenor,
                start_date_value=start_date_value,
                end_date_value=end_date_value,
            )
        except ValueError:
            continue

        if window is None:
            continue
        start_years, end_years = window
        ordered_tenors = _local_sort_tenors(day_df["tenor"].tolist())
        if len(ordered_tenors) < 2:
            continue

        rate_by_tenor = dict(zip(day_df["tenor"], day_df["field_value"]))
        years_grid = [_local_tenor_to_years(tenor) for tenor in ordered_tenors]
        if start_years > years_grid[-1] or end_years < years_grid[0]:
            continue
        rates_decimal = [float(rate_by_tenor[tenor]) / 100.0 for tenor in ordered_tenors]
        try:
            forward_decimal = _local_forward_rate_between(
                years_grid,
                rates_decimal,
                start_years,
                end_years,
            )
        except (ValueError, ZeroDivisionError):
            continue
        forwards[trade_date_ts] = forward_decimal * 100.0

    if not forwards:
        return pd.Series(dtype=float)
    series = pd.Series(forwards).sort_index()
    return series.ffill(limit=5)


def ois_forward_sql_baseline(
    engine,
    *,
    curve_family: str,
    start_tenor: Optional[str],
    end_tenor: Optional[str],
    start_date_value: Optional[str],
    end_date_value: Optional[str],
    lookback_days: int,
    field_name: str,
) -> dict[str, Any]:
    fetch_start_date = date.today() - timedelta(days=lookback_days + BUFFER_DAYS)
    raw_df = _fetch_ois_full_curve_raw(
        engine,
        curve_family=curve_family,
        field_name=field_name,
        start_date=fetch_start_date,
    )
    if raw_df.empty:
        return {"error": "SQL baseline returned no rows."}

    as_of_ts = pd.to_datetime(raw_df["trade_date"]).max()
    as_of_date = as_of_ts.date()

    if start_date_value:
        resolved_start = datetime.strptime(start_date_value, "%Y-%m-%d").date()
        if resolved_start < as_of_date:
            return {
                "error": (
                    f"start_date ({resolved_start.isoformat()}) is before the "
                    f"curve's as-of date ({as_of_date.isoformat()})."
                )
            }

    forward_series = _compute_local_forward_series(
        raw_df,
        curve_family=curve_family,
        start_tenor=start_tenor,
        end_tenor=end_tenor,
        start_date_value=start_date_value,
        end_date_value=end_date_value,
    )
    if forward_series.empty:
        return {"error": "Could not compute a forward series from baseline."}

    cutoff = forward_series.index[-1] - pd.Timedelta(days=lookback_days)
    display_series = forward_series.loc[forward_series.index >= cutoff]
    if display_series.empty:
        return {"error": "No displayed forward observations after cutoff."}

    z_series = _local_rolling_zscore(forward_series)
    display_z = z_series.loc[z_series.index >= cutoff]

    current_forward = float(display_series.iloc[-1])
    daily_change = (
        _local_bps_change(current_forward, display_series.iloc[-2])
        if len(display_series) >= 2
        else None
    )
    high_252, low_252, percentile = _local_trailing_high_low_percentile(
        forward_series,
        window=Z_SCORE_WINDOW,
        decimals=4,
    )

    latest_date_ts = display_series.index[-1]
    latest_date = latest_date_ts.date()
    latest_window = _local_forward_window_from_trade_date(
        trade_date_obj=latest_date,
        curve_family=curve_family,
        start_tenor=start_tenor,
        end_tenor=end_tenor,
        start_date_value=start_date_value,
        end_date_value=end_date_value,
    )

    start_spot_rate_pct = None
    end_spot_rate_pct = None
    if latest_window is not None:
        latest_day = raw_df[pd.to_datetime(raw_df["trade_date"]) == latest_date_ts].copy()
        latest_day["field_value"] = pd.to_numeric(latest_day["field_value"], errors="coerce")
        latest_day = latest_day.dropna(subset=["field_value"])
        ordered_tenors = _local_sort_tenors(latest_day["tenor"].tolist())
        if ordered_tenors:
            years_grid = [_local_tenor_to_years(tenor) for tenor in ordered_tenors]
            rate_by_tenor = dict(zip(latest_day["tenor"], latest_day["field_value"]))
            rates_pct = [float(rate_by_tenor[tenor]) for tenor in ordered_tenors]
            start_spot_rate_pct = _local_safe_float(
                _local_interpolate_rate(years_grid, rates_pct, latest_window[0])
            )
            end_spot_rate_pct = _local_safe_float(
                _local_interpolate_rate(years_grid, rates_pct, latest_window[1])
            )

    return {
        "current_metrics": {
            "as_of_date": latest_date_ts.strftime("%Y-%m-%d"),
            "curve_family": curve_family,
            "forward_label": _local_forward_label(
                curve_family,
                start_tenor=start_tenor,
                end_tenor=end_tenor,
                start_date=start_date_value,
                end_date=end_date_value,
            ),
            "start_years": round(latest_window[0], 4) if latest_window else 0.0,
            "end_years": round(latest_window[1], 4) if latest_window else 0.0,
            "forward_rate_pct": _local_safe_float(current_forward),
            "daily_change_bps": daily_change,
            "current_z_score": _local_safe_float(display_z.iloc[-1]) if len(display_z) else None,
            "rolling_window_days": Z_SCORE_WINDOW,
            "high_252d_pct": high_252,
            "low_252d_pct": low_252,
            "percentile_252d": percentile,
            "start_spot_rate_pct": start_spot_rate_pct,
            "end_spot_rate_pct": end_spot_rate_pct,
        },
        "time_series": [
            {
                "date": idx.strftime("%Y-%m-%d"),
                "forward_rate_pct": round(float(display_series.loc[idx]), 4),
                "z_score": _local_safe_float(display_z.loc[idx]) if idx in display_z.index else None,
            }
            for idx in display_series.index
        ],
    }


def compare_ois_forward(tool_result: dict[str, Any], sql_result: dict[str, Any]) -> list[str]:
    mismatches: list[str] = []
    if "error" in tool_result:
        return [f"Tool returned error: {tool_result['error']}"]
    if "error" in sql_result:
        return [f"SQL baseline returned error: {sql_result['error']}"]
    tool_metrics = tool_result["current_metrics"]
    sql_metrics = sql_result["current_metrics"]
    add_exact_field_mismatches(
        mismatches=mismatches,
        tool_payload=tool_metrics,
        sql_payload=sql_metrics,
        fields=("as_of_date", "curve_family", "forward_label", "rolling_window_days"),
        prefix="current_metrics.",
    )
    add_numeric_field_mismatches(
        mismatches=mismatches,
        tool_payload=tool_metrics,
        sql_payload=sql_metrics,
        fields=(
            "start_years",
            "end_years",
            "forward_rate_pct",
            "daily_change_bps",
            "current_z_score",
            "high_252d_pct",
            "low_252d_pct",
            "percentile_252d",
            "start_spot_rate_pct",
            "end_spot_rate_pct",
        ),
        tolerances=OIS_FORWARD_TOLERANCE,
        prefix="current_metrics.",
    )
    mismatches.extend(
        compare_time_series(
            tool_rows=tool_result["time_series"],
            sql_rows=sql_result["time_series"],
            exact_fields=(),
            numeric_fields=("forward_rate_pct", "z_score"),
            tolerances=OIS_FORWARD_TOLERANCE,
        )
    )
    return mismatches


def ois_scanner_sql_baseline(
    engine,
    *,
    curve_families: Optional[Sequence[str]],
    top_n: int,
    min_abs_z_score: float,
    field_name: str,
) -> dict[str, Any]:
    start_date = date.today() - timedelta(days=365 + BUFFER_DAYS)
    raw_df = _fetch_ois_scan_universe_raw(
        engine,
        field_name=field_name,
        start_date=start_date,
        curve_families=curve_families,
    )
    if raw_df.empty:
        return {"error": "SQL baseline returned no rows."}

    raw_df["trade_date"] = pd.to_datetime(raw_df["trade_date"])
    raw_df["field_value"] = pd.to_numeric(raw_df["field_value"], errors="coerce")
    raw_df = raw_df.dropna(subset=["field_value"])
    raw_df = raw_df.drop_duplicates(
        subset=["trade_date", "curve_family", "tenor"],
        keep="last",
    )

    all_results: list[dict[str, Any]] = []
    group_count = 0

    for (curve_family, tenor), group in raw_df.groupby(["curve_family", "tenor"]):
        group_count += 1
        series = group.set_index("trade_date").sort_index()["field_value"].ffill(limit=5)
        if len(series) < Z_SCORE_MIN_PERIODS:
            continue
        z_series = _local_rolling_zscore(series)
        current_z = _local_safe_float(z_series.iloc[-1])
        if current_z is None or abs(current_z) < min_abs_z_score:
            continue
        high_252, low_252, percentile = _local_trailing_high_low_percentile(
            series, window=Z_SCORE_WINDOW, decimals=4
        )
        all_results.append(
            {
                "curve_family": curve_family,
                "tenor": tenor,
                "as_of_date": series.index[-1].strftime("%Y-%m-%d"),
                "current_rate_pct": _local_safe_float(series.iloc[-1]),
                "daily_change_bps": (
                    _local_bps_change(series.iloc[-1], series.iloc[-2]) if len(series) >= 2 else None
                ),
                "z_score": current_z,
                "high_252d_pct": high_252,
                "low_252d_pct": low_252,
                "percentile_252d": percentile,
                "signal": "EXTREME_HIGH" if current_z > 0 else "EXTREME_LOW",
            }
        )

    if not all_results:
        filter_desc = f" for curves {list(curve_families)}" if curve_families else ""
        return {
            "error": (
                f"No OIS instruments found with |z-score| >= {min_abs_z_score}"
                f"{filter_desc}.  Try lowering min_abs_z_score."
            )
        }

    all_results.sort(
        key=lambda row: (-abs(float(row["z_score"])), row["curve_family"], row["tenor"])
    )
    top_results = all_results[:top_n]
    results = []
    for index, row in enumerate(top_results, start=1):
        results.append({"rank": index, **row})

    return {
        "scan_summary": (
            f"Scanned {group_count} OIS instruments.  "
            f"Found {len(all_results)} with |z-score| >= {min_abs_z_score}.  "
            f"Showing top {len(top_results)} by absolute z-score."
        ),
        "results": results,
    }


def compare_ois_scanner(tool_result: dict[str, Any], sql_result: dict[str, Any]) -> list[str]:
    mismatches: list[str] = []
    if "error" in tool_result:
        return [f"Tool returned error: {tool_result['error']}"]
    if "error" in sql_result:
        return [f"SQL baseline returned error: {sql_result['error']}"]

    if tool_result.get("scan_summary") != sql_result.get("scan_summary"):
        mismatches.append(
            f"scan_summary: tool={tool_result.get('scan_summary')!r} "
            f"sql={sql_result.get('scan_summary')!r}"
        )

    tool_rows = tool_result.get("results", [])
    sql_rows = sql_result.get("results", [])
    if len(tool_rows) != len(sql_rows):
        mismatches.append(f"results length: tool={len(tool_rows)} sql={len(sql_rows)}")
        return mismatches

    for index, (tool_row, sql_row) in enumerate(zip(tool_rows, sql_rows), start=1):
        for field in ("rank", "curve_family", "tenor", "as_of_date", "signal"):
            if tool_row.get(field) != sql_row.get(field):
                mismatches.append(
                    f"results[{index}].{field}: tool={tool_row.get(field)!r} "
                    f"sql={sql_row.get(field)!r}"
                )
                break
        else:
            for field in (
                "current_rate_pct",
                "daily_change_bps",
                "z_score",
                "high_252d_pct",
                "low_252d_pct",
                "percentile_252d",
            ):
                if floats_match(
                    tool_row.get(field),
                    sql_row.get(field),
                    field,
                    OIS_SCANNER_TOLERANCE,
                ):
                    continue
                message = (
                    f"results[{index}].{field}: tool={tool_row.get(field)!r} "
                    f"sql={sql_row.get(field)!r}"
                )
                delta = numeric_delta(tool_row.get(field), sql_row.get(field))
                if delta is not None:
                    message += f" (delta={delta:.6f})"
                mismatches.append(message)
                break

    return mismatches


def _summarize_current_metrics(result: dict[str, Any]) -> Any:
    if "error" in result:
        return {"error": result["error"]}
    return result.get("current_metrics")


def _summarize_scanner(result: dict[str, Any]) -> Any:
    if "error" in result:
        return {"error": result["error"]}
    return {
        "scan_summary": result.get("scan_summary"),
        "results": result.get("results", []),
    }


def _run_operation(engine, spec: OperationSpec) -> OperationOutcome:
    tool_result = spec.run_tool(engine)
    sql_result = spec.run_sql(engine)
    mismatches = spec.compare(tool_result, sql_result)
    return OperationOutcome(
        spec=spec,
        tool_result=tool_result,
        sql_result=sql_result,
        mismatches=mismatches,
        tool_summary=spec.summarize(tool_result),
        sql_summary=spec.summarize(sql_result),
    )


def _static_case_runner(
    operations: Sequence[OperationSpec],
    *,
    notes: Optional[Iterable[str]] = None,
    numeric_supported: bool = True,
) -> Callable[[Any], DeterministicCaseResult]:
    def _runner(engine) -> DeterministicCaseResult:
        outcomes = [_run_operation(engine, spec) for spec in operations]
        return DeterministicCaseResult(
            numeric_supported=numeric_supported,
            outcomes=outcomes,
            notes=list(notes or []),
        )

    return _runner


def _dynamic_ois_uk_scan_case(engine) -> DeterministicCaseResult:
    notes: list[str] = []
    outcomes: list[OperationOutcome] = []

    scan_op = op_ois_scan(min_abs_z_score=1.5)
    scan_outcome = _run_operation(engine, scan_op)
    outcomes.append(scan_outcome)

    if scan_outcome.mismatches:
        notes.append(
            "Dynamic follow-up decision uses the SQL baseline result; scanner itself mismatched."
        )

    sql_scan = scan_outcome.sql_result
    uk_flagged = False
    if "error" not in sql_scan:
        uk_flagged = any(
            row.get("curve_family") == "GBP_SONIA_OIS"
            for row in sql_scan.get("results", [])
        )

    if uk_flagged:
        notes.append("GBP_SONIA_OIS flagged by the scanner baseline; SONIA follow-ups required.")
        outcomes.append(_run_operation(engine, op_ois_curve_spread("GBP_SONIA_OIS", "2Y", "10Y")))
        outcomes.append(_run_operation(engine, op_ois_curve_spread("GBP_SONIA_OIS", "5Y", "30Y")))
    else:
        notes.append("GBP_SONIA_OIS not flagged by the scanner baseline; no SONIA follow-ups required.")

    return DeterministicCaseResult(numeric_supported=True, outcomes=outcomes, notes=notes)


def _unsupported_sovereign_scan_regime_case(engine) -> DeterministicCaseResult:
    notes = [
        "The prompt asks for a 3-month regime classification.",
        "The current sovereign regime tool only supports 1d / 5d / 22d, so there is no honest numeric baseline for the follow-up leg.",
        "This case therefore validates the supported scanner prefix only; the follow-up should be treated as current tool-surface gap, not a routing failure.",
    ]
    outcomes = [_run_operation(engine, op_sov_scan(min_abs_z_score=1.5))]
    return DeterministicCaseResult(
        numeric_supported=False,
        outcomes=outcomes,
        notes=notes,
    )


def op_sov_yield(curve_family: str, tenor: str) -> OperationSpec:
    params = {
        "curve_family": curve_family,
        "tenor": tenor,
        "lookback_days": LOOKBACK_DAYS,
        "field_name": YLD_FIELD,
    }
    return OperationSpec(
        name=f"{curve_family} {tenor} yield",
        tool_name="get_yield_levels_tool",
        domain="sovereign_bonds",
        params=params,
        expected_param_subset={"curve_family": curve_family, "tenor": tenor},
        run_tool=lambda engine: sov_yield_validator.get_yield_levels(
            engine, sov_yield_validator.YieldLevelInput(**params)
        ),
        run_sql=lambda engine: sov_yield_validator.sql_baseline(
            engine,
            curve_family=curve_family,
            tenor=tenor,
            lookback_days=LOOKBACK_DAYS,
            field_name=YLD_FIELD,
        ),
        compare=sov_yield_validator.compare_results,
        summarize=_summarize_current_metrics,
    )


def op_sov_curve_spread(curve_family: str, short_tenor: str, long_tenor: str) -> OperationSpec:
    params = {
        "curve_family": curve_family,
        "short_tenor": short_tenor,
        "long_tenor": long_tenor,
        "lookback_days": LOOKBACK_DAYS,
        "field_name": YLD_FIELD,
    }
    return OperationSpec(
        name=f"{curve_family} {short_tenor}/{long_tenor} spread",
        tool_name="calculate_curve_spread_tool",
        domain="sovereign_bonds",
        params=params,
        expected_param_subset={
            "curve_family": curve_family,
            "short_tenor": short_tenor,
            "long_tenor": long_tenor,
        },
        run_tool=lambda engine: sov_spread_validator.calculate_curve_spread(
            engine, sov_spread_validator.CurveSpreadInput(**params)
        ),
        run_sql=lambda engine: sov_spread_validator.sql_baseline(
            engine,
            curve_family=curve_family,
            short_tenor=short_tenor,
            long_tenor=long_tenor,
            lookback_days=LOOKBACK_DAYS,
            field_name=YLD_FIELD,
        ),
        compare=sov_spread_validator.compare_results,
        summarize=_summarize_current_metrics,
    )


def op_sov_cross_market(curve_family_1: str, curve_family_2: str, tenor: str) -> OperationSpec:
    params = {
        "curve_family_1": curve_family_1,
        "curve_family_2": curve_family_2,
        "tenor": tenor,
        "lookback_days": LOOKBACK_DAYS,
        "field_name": YLD_FIELD,
    }
    return OperationSpec(
        name=f"{curve_family_1}-{curve_family_2} {tenor} sovereign spread",
        tool_name="calculate_cross_market_spread_tool",
        domain="sovereign_bonds",
        params=params,
        expected_param_subset={
            "curve_family_1": curve_family_1,
            "curve_family_2": curve_family_2,
            "tenor": tenor,
        },
        run_tool=lambda engine: sov_cross_validator.calculate_cross_market_spread(
            engine, sov_cross_validator.CrossMarketSpreadInput(**params)
        ),
        run_sql=lambda engine: sov_cross_validator.sql_baseline(
            engine,
            curve_family_1=curve_family_1,
            curve_family_2=curve_family_2,
            tenor=tenor,
            lookback_days=LOOKBACK_DAYS,
            field_name=YLD_FIELD,
        ),
        compare=sov_cross_validator.compare_results,
        summarize=_summarize_current_metrics,
    )


def op_sov_butterfly(
    curve_family: str,
    short_tenor: str,
    belly_tenor: str,
    long_tenor: str,
) -> OperationSpec:
    params = {
        "curve_family": curve_family,
        "short_tenor": short_tenor,
        "belly_tenor": belly_tenor,
        "long_tenor": long_tenor,
        "lookback_days": LOOKBACK_DAYS,
        "field_name": YLD_FIELD,
    }
    return OperationSpec(
        name=f"{curve_family} {short_tenor}/{belly_tenor}/{long_tenor} fly",
        tool_name="calculate_butterfly_tool",
        domain="sovereign_bonds",
        params=params,
        expected_param_subset={
            "curve_family": curve_family,
            "short_tenor": short_tenor,
            "belly_tenor": belly_tenor,
            "long_tenor": long_tenor,
        },
        run_tool=lambda engine: sov_bfly_validator.calculate_butterfly(
            engine, sov_bfly_validator.ButterflyInput(**params)
        ),
        run_sql=lambda engine: sov_bfly_validator.sql_baseline(
            engine,
            curve_family=curve_family,
            short_tenor=short_tenor,
            belly_tenor=belly_tenor,
            long_tenor=long_tenor,
            lookback_days=LOOKBACK_DAYS,
            field_name=YLD_FIELD,
        ),
        compare=sov_bfly_validator.compare_results,
        summarize=_summarize_current_metrics,
    )


def op_sov_regime(
    curve_family: str,
    *,
    front_tenor: str = "2Y",
    back_tenor: str = "10Y",
    lookback_period: str = "1d",
) -> OperationSpec:
    params = {
        "curve_family": curve_family,
        "front_tenor": front_tenor,
        "back_tenor": back_tenor,
        "lookback_period": lookback_period,
        "field_name": YLD_FIELD,
    }
    return OperationSpec(
        name=f"{curve_family} regime {lookback_period}",
        tool_name="classify_curve_regime_tool",
        domain="sovereign_bonds",
        params=params,
        expected_param_subset={
            "curve_family": curve_family,
            "lookback_period": lookback_period,
        },
        run_tool=lambda engine: sov_regime_validator.classify_curve_regime(
            engine, sov_regime_validator.CurveRegimeInput(**params)
        ),
        run_sql=lambda engine: sov_regime_validator.sql_baseline(
            engine,
            curve_family=curve_family,
            front_tenor=front_tenor,
            back_tenor=back_tenor,
            lookback_period=lookback_period,
            field_name=YLD_FIELD,
        ),
        compare=sov_regime_validator.compare_results,
        summarize=_summarize_current_metrics,
    )


def op_sov_scan(
    *,
    curve_families: Optional[Sequence[str]] = None,
    top_n: int = 10,
    min_abs_z_score: float = 1.5,
) -> OperationSpec:
    params = {
        "curve_families": list(curve_families) if curve_families else None,
        "top_n": top_n,
        "min_abs_z_score": min_abs_z_score,
        "field_name": YLD_FIELD,
    }
    expected_subset = {}
    if curve_families:
        expected_subset["curve_families"] = list(curve_families)
    if min_abs_z_score != 1.5:
        expected_subset["min_abs_z_score"] = min_abs_z_score
    if top_n != 10:
        expected_subset["top_n"] = top_n

    return OperationSpec(
        name="sovereign scanner",
        tool_name="scan_extremes_tool",
        domain="sovereign_bonds",
        params=params,
        expected_param_subset=expected_subset,
        run_tool=lambda engine: sov_scanner_validator.scan_extremes(
            engine, sov_scanner_validator.ScannerInput(**params)
        ),
        run_sql=lambda engine: sov_scanner_validator.sql_baseline(
            engine,
            curve_families=tuple(curve_families) if curve_families else None,
            top_n=top_n,
            min_abs_z_score=min_abs_z_score,
            field_name=YLD_FIELD,
        ),
        compare=sov_scanner_validator.compare_results,
        summarize=_summarize_scanner,
    )


def op_ois_rate(curve_family: str, tenor: str) -> OperationSpec:
    params = {
        "curve_family": curve_family,
        "tenor": tenor,
        "lookback_days": LOOKBACK_DAYS,
        "field_name": OIS_FIELD,
    }
    return OperationSpec(
        name=f"{curve_family} {tenor} OIS rate",
        tool_name="calculate_ois_rate_level_tool",
        domain="ois",
        params=params,
        expected_param_subset={"curve_family": curve_family, "tenor": tenor},
        run_tool=lambda engine: get_ois_rate_level(engine, OISRateLevelInput(**params)),
        run_sql=lambda engine: ois_rate_level_sql_baseline(
            engine,
            curve_family=curve_family,
            tenor=tenor,
            lookback_days=LOOKBACK_DAYS,
            field_name=OIS_FIELD,
        ),
        compare=compare_ois_rate_level,
        summarize=_summarize_current_metrics,
    )


def op_ois_curve_spread(curve_family: str, short_tenor: str, long_tenor: str) -> OperationSpec:
    params = {
        "curve_family": curve_family,
        "short_tenor": short_tenor,
        "long_tenor": long_tenor,
        "lookback_days": LOOKBACK_DAYS,
        "field_name": OIS_FIELD,
    }
    return OperationSpec(
        name=f"{curve_family} {short_tenor}/{long_tenor} OIS spread",
        tool_name="calculate_ois_curve_spread_tool",
        domain="ois",
        params=params,
        expected_param_subset={
            "curve_family": curve_family,
            "short_tenor": short_tenor,
            "long_tenor": long_tenor,
        },
        run_tool=lambda engine: calculate_ois_curve_spread(
            engine, OISCurveSpreadInput(**params)
        ),
        run_sql=lambda engine: ois_curve_spread_sql_baseline(
            engine,
            curve_family=curve_family,
            short_tenor=short_tenor,
            long_tenor=long_tenor,
            lookback_days=LOOKBACK_DAYS,
            field_name=OIS_FIELD,
        ),
        compare=compare_ois_curve_spread,
        summarize=_summarize_current_metrics,
    )


def op_ois_forward_tenor(curve_family: str, start_tenor: str, end_tenor: str) -> OperationSpec:
    params = {
        "curve_family": curve_family,
        "start_tenor": start_tenor,
        "end_tenor": end_tenor,
        "start_date": None,
        "end_date": None,
        "lookback_days": LOOKBACK_DAYS,
        "field_name": OIS_FIELD,
    }
    return OperationSpec(
        name=f"{curve_family} forward {start_tenor}/{end_tenor}",
        tool_name="calculate_ois_forward_rate_tool",
        domain="ois",
        params=params,
        expected_param_subset={
            "curve_family": curve_family,
            "start_tenor": start_tenor,
            "end_tenor": end_tenor,
        },
        run_tool=lambda engine: calculate_ois_forward_rate(
            engine, OISForwardRateInput(**params)
        ),
        run_sql=lambda engine: ois_forward_sql_baseline(
            engine,
            curve_family=curve_family,
            start_tenor=start_tenor,
            end_tenor=end_tenor,
            start_date_value=None,
            end_date_value=None,
            lookback_days=LOOKBACK_DAYS,
            field_name=OIS_FIELD,
        ),
        compare=compare_ois_forward,
        summarize=_summarize_current_metrics,
    )


def op_ois_forward_date(curve_family: str, start_date_value: str, end_date_value: str) -> OperationSpec:
    params = {
        "curve_family": curve_family,
        "start_tenor": None,
        "end_tenor": None,
        "start_date": start_date_value,
        "end_date": end_date_value,
        "lookback_days": LOOKBACK_DAYS,
        "field_name": OIS_FIELD,
    }
    return OperationSpec(
        name=f"{curve_family} forward {start_date_value} to {end_date_value}",
        tool_name="calculate_ois_forward_rate_tool",
        domain="ois",
        params=params,
        expected_param_subset={
            "curve_family": curve_family,
            "start_date": start_date_value,
            "end_date": end_date_value,
        },
        run_tool=lambda engine: calculate_ois_forward_rate(
            engine, OISForwardRateInput(**params)
        ),
        run_sql=lambda engine: ois_forward_sql_baseline(
            engine,
            curve_family=curve_family,
            start_tenor=None,
            end_tenor=None,
            start_date_value=start_date_value,
            end_date_value=end_date_value,
            lookback_days=LOOKBACK_DAYS,
            field_name=OIS_FIELD,
        ),
        compare=compare_ois_forward,
        summarize=_summarize_current_metrics,
    )


def op_ois_cross_market(curve_family_1: str, curve_family_2: str, tenor: str) -> OperationSpec:
    params = {
        "curve_family_1": curve_family_1,
        "curve_family_2": curve_family_2,
        "tenor": tenor,
        "lookback_days": LOOKBACK_DAYS,
        "field_name": OIS_FIELD,
    }
    return OperationSpec(
        name=f"{curve_family_1}-{curve_family_2} {tenor} OIS spread",
        tool_name="calculate_ois_cross_market_spread_tool",
        domain="ois",
        params=params,
        expected_param_subset={
            "curve_family_1": curve_family_1,
            "curve_family_2": curve_family_2,
            "tenor": tenor,
        },
        run_tool=lambda engine: calculate_ois_cross_market_spread(
            engine, OISCrossMarketSpreadInput(**params)
        ),
        run_sql=lambda engine: ois_cross_market_sql_baseline(
            engine,
            curve_family_1=curve_family_1,
            curve_family_2=curve_family_2,
            tenor=tenor,
            lookback_days=LOOKBACK_DAYS,
            field_name=OIS_FIELD,
        ),
        compare=compare_ois_cross_market,
        summarize=_summarize_current_metrics,
    )


def op_ois_scan(
    *,
    curve_families: Optional[Sequence[str]] = None,
    top_n: int = 10,
    min_abs_z_score: float = 1.5,
) -> OperationSpec:
    params = {
        "curve_families": list(curve_families) if curve_families else None,
        "top_n": top_n,
        "min_abs_z_score": min_abs_z_score,
        "field_name": OIS_FIELD,
    }
    expected_subset = {}
    if curve_families:
        expected_subset["curve_families"] = list(curve_families)
    if min_abs_z_score != 1.5:
        expected_subset["min_abs_z_score"] = min_abs_z_score
    if top_n != 10:
        expected_subset["top_n"] = top_n

    return OperationSpec(
        name="OIS scanner",
        tool_name="scan_ois_extremes_tool",
        domain="ois",
        params=params,
        expected_param_subset=expected_subset,
        run_tool=lambda engine: scan_ois_extremes(engine, OISScannerInput(**params)),
        run_sql=lambda engine: ois_scanner_sql_baseline(
            engine,
            curve_families=curve_families,
            top_n=top_n,
            min_abs_z_score=min_abs_z_score,
            field_name=OIS_FIELD,
        ),
        compare=compare_ois_scanner,
        summarize=_summarize_scanner,
    )


PROMPT_CASES: list[PromptCase] = [
    # ------------------------------------------------------------------
    # OIS only
    # ------------------------------------------------------------------
    PromptCase(
        case_id="ois_01",
        group="ois",
        prompt="Where is the 2Y SOFR swap trading right now?",
        expected_action="single_domain",
        expected_domains=("ois",),
        runner=_static_case_runner([op_ois_rate("USD_SOFR_OIS", "2Y")]),
    ),
    PromptCase(
        case_id="ois_02",
        group="ois",
        prompt="What's the SOFR 2s10s spread doing today? Is it steepening?",
        expected_action="single_domain",
        expected_domains=("ois",),
        runner=_static_case_runner([op_ois_curve_spread("USD_SOFR_OIS", "2Y", "10Y")]),
    ),
    PromptCase(
        case_id="ois_03",
        group="ois",
        prompt="Where is 5Y5Y ESTR sitting? Give me the exact percent.",
        expected_action="single_domain",
        expected_domains=("ois",),
        runner=_static_case_runner([op_ois_forward_tenor("EUR_ESTR_OIS", "5Y", "10Y")]),
    ),
    PromptCase(
        case_id="ois_04",
        group="ois",
        prompt="What is the implied SOFR forward rate between June 2026 and December 2026?",
        expected_action="single_domain",
        expected_domains=("ois",),
        runner=_static_case_runner(
            [op_ois_forward_date("USD_SOFR_OIS", "2026-06-01", "2026-12-01")],
            notes=[
                "Canonical mapping for month-only wording: first calendar day of each named month.",
                "If the live agent chooses different dates, treat that as a parameter-mapping difference rather than a math failure.",
            ],
        ),
    ),
    PromptCase(
        case_id="ois_05",
        group="ois",
        prompt="How wide is the SOFR-ESTR 2Y differential right now?",
        expected_action="single_domain",
        expected_domains=("ois",),
        runner=_static_case_runner(
            [op_ois_cross_market("USD_SOFR_OIS", "EUR_ESTR_OIS", "2Y")]
        ),
    ),
    PromptCase(
        case_id="ois_06",
        group="ois",
        prompt="Run a sweep across all OIS curves. Is anything trading at a 2-sigma extreme?",
        expected_action="single_domain",
        expected_domains=("ois",),
        runner=_static_case_runner([op_ois_scan(min_abs_z_score=2.0)]),
    ),
    PromptCase(
        case_id="ois_07",
        group="ois",
        prompt="Give me the 2Y ESTR level and the ESTR 1s5s curve spread.",
        expected_action="single_domain",
        expected_domains=("ois",),
        runner=_static_case_runner(
            [
                op_ois_rate("EUR_ESTR_OIS", "2Y"),
                op_ois_curve_spread("EUR_ESTR_OIS", "1Y", "5Y"),
            ]
        ),
    ),
    PromptCase(
        case_id="ois_08",
        group="ois",
        prompt="Compare the 1Y1Y SOFR forward to the 1Y1Y SONIA forward. Then tell me the outright SOFR-SONIA 5Y cross-market spread.",
        expected_action="single_domain",
        expected_domains=("ois",),
        runner=_static_case_runner(
            [
                op_ois_forward_tenor("USD_SOFR_OIS", "1Y", "2Y"),
                op_ois_forward_tenor("GBP_SONIA_OIS", "1Y", "2Y"),
                op_ois_cross_market("USD_SOFR_OIS", "GBP_SONIA_OIS", "5Y"),
            ]
        ),
    ),
    PromptCase(
        case_id="ois_09",
        group="ois",
        prompt="Scan OIS for extremes. If anything in the UK is blowing up, give me the SONIA 2s10s and 5s30s spreads.",
        expected_action="single_domain",
        expected_domains=("ois",),
        runner=_dynamic_ois_uk_scan_case,
    ),
    PromptCase(
        case_id="ois_10",
        group="ois",
        prompt="Give me a full read on the front-end USD swap market: 1Y SOFR level, SOFR 1s3s spread, 1Y1Y forward, and how the 1Y SOFR compares to 1Y ESTR.",
        expected_action="single_domain",
        expected_domains=("ois",),
        runner=_static_case_runner(
            [
                op_ois_rate("USD_SOFR_OIS", "1Y"),
                op_ois_curve_spread("USD_SOFR_OIS", "1Y", "3Y"),
                op_ois_forward_tenor("USD_SOFR_OIS", "1Y", "2Y"),
                op_ois_cross_market("USD_SOFR_OIS", "EUR_ESTR_OIS", "1Y"),
            ]
        ),
    ),
    # ------------------------------------------------------------------
    # Sovereign only
    # ------------------------------------------------------------------
    PromptCase(
        case_id="sov_01",
        group="sovereign",
        prompt="What's the current yield on the 10-year US Treasury?",
        expected_action="single_domain",
        expected_domains=("sovereign_bonds",),
        runner=_static_case_runner([op_sov_yield("UST", "10Y")]),
    ),
    PromptCase(
        case_id="sov_02",
        group="sovereign",
        prompt="Where is the US 2s10s cash curve sitting?",
        expected_action="single_domain",
        expected_domains=("sovereign_bonds",),
        runner=_static_case_runner([op_sov_curve_spread("UST", "2Y", "10Y")]),
    ),
    PromptCase(
        case_id="sov_03",
        group="sovereign",
        prompt="What is the 10Y UST-Bund spread?",
        expected_action="single_domain",
        expected_domains=("sovereign_bonds",),
        runner=_static_case_runner([op_sov_cross_market("UST", "DE_BUND", "10Y")]),
    ),
    PromptCase(
        case_id="sov_04",
        group="sovereign",
        prompt="Run the US 2s5s10s fly.",
        expected_action="single_domain",
        expected_domains=("sovereign_bonds",),
        runner=_static_case_runner([op_sov_butterfly("UST", "2Y", "5Y", "10Y")]),
    ),
    PromptCase(
        case_id="sov_05",
        group="sovereign",
        prompt="What regime is the UK Gilt curve in over the last month?",
        expected_action="single_domain",
        expected_domains=("sovereign_bonds",),
        runner=_static_case_runner([op_sov_regime("UK_GILT", lookback_period="22d")]),
    ),
    PromptCase(
        case_id="sov_06",
        group="sovereign",
        prompt="Sweep global sovereign bonds for 2.5 sigma outliers.",
        expected_action="single_domain",
        expected_domains=("sovereign_bonds",),
        runner=_static_case_runner([op_sov_scan(min_abs_z_score=2.5)]),
    ),
    PromptCase(
        case_id="sov_07",
        group="sovereign",
        prompt="Is the US cash curve steepening? Give me the 2s10s and the 2s5s10s fly.",
        expected_action="single_domain",
        expected_domains=("sovereign_bonds",),
        runner=_static_case_runner(
            [
                op_sov_curve_spread("UST", "2Y", "10Y"),
                op_sov_butterfly("UST", "2Y", "5Y", "10Y"),
            ]
        ),
    ),
    PromptCase(
        case_id="sov_08",
        group="sovereign",
        prompt="Pull the 10Y JGB yield, compare it to the 10Y UST, and tell me the current regime of the JGB curve.",
        expected_action="single_domain",
        expected_domains=("sovereign_bonds",),
        runner=_static_case_runner(
            [
                op_sov_yield("JGB", "10Y"),
                op_sov_cross_market("JGB", "UST", "10Y"),
                op_sov_regime("JGB", lookback_period="1d"),
            ]
        ),
    ),
    PromptCase(
        case_id="sov_09",
        group="sovereign",
        prompt="Scan sovereign bonds for extremes. For any curve that shows up, run a 3-month regime classification.",
        expected_action="single_domain",
        expected_domains=("sovereign_bonds",),
        runner=_unsupported_sovereign_scan_regime_case,
    ),
    PromptCase(
        case_id="sov_10",
        group="sovereign",
        prompt="Give me the full US cash picture: 10Y yield, 2s10s spread, 2s5s10s fly, 10-year spread to Germany, and the 1-month US curve regime.",
        expected_action="single_domain",
        expected_domains=("sovereign_bonds",),
        runner=_static_case_runner(
            [
                op_sov_yield("UST", "10Y"),
                op_sov_curve_spread("UST", "2Y", "10Y"),
                op_sov_butterfly("UST", "2Y", "5Y", "10Y"),
                op_sov_cross_market("UST", "DE_BUND", "10Y"),
                op_sov_regime("UST", lookback_period="22d"),
            ]
        ),
    ),
    # ------------------------------------------------------------------
    # Cross-domain
    # ------------------------------------------------------------------
    PromptCase(
        case_id="cross_01",
        group="cross",
        prompt="Where is the 10Y US Treasury trading relative to the 10Y SOFR swap?",
        expected_action="multi_domain",
        expected_domains=("sovereign_bonds", "ois"),
        runner=_static_case_runner(
            [
                op_sov_yield("UST", "10Y"),
                op_ois_rate("USD_SOFR_OIS", "10Y"),
            ]
        ),
    ),
    PromptCase(
        case_id="cross_02",
        group="cross",
        prompt="Is the US 2s10s steeper in cash or in OIS?",
        expected_action="multi_domain",
        expected_domains=("sovereign_bonds", "ois"),
        runner=_static_case_runner(
            [
                op_sov_curve_spread("UST", "2Y", "10Y"),
                op_ois_curve_spread("USD_SOFR_OIS", "2Y", "10Y"),
            ]
        ),
    ),
    PromptCase(
        case_id="cross_03",
        group="cross",
        prompt="Run a full global morning sweep: scan both sovereign cash bonds and OIS swaps for any 2-sigma extremes.",
        expected_action="multi_domain",
        expected_domains=("sovereign_bonds", "ois"),
        runner=_static_case_runner(
            [
                op_sov_scan(min_abs_z_score=2.0),
                op_ois_scan(min_abs_z_score=2.0),
            ]
        ),
    ),
    PromptCase(
        case_id="cross_04",
        group="cross",
        prompt="Pull the 5Y5Y SOFR forward and compare it to the 10Y US Treasury yield.",
        expected_action="multi_domain",
        expected_domains=("sovereign_bonds", "ois"),
        runner=_static_case_runner(
            [
                op_ois_forward_tenor("USD_SOFR_OIS", "5Y", "10Y"),
                op_sov_yield("UST", "10Y"),
            ]
        ),
    ),
    PromptCase(
        case_id="cross_05",
        group="cross",
        prompt="What is the US-Europe 2Y differential in both cash bonds and OIS?",
        expected_action="multi_domain",
        expected_domains=("sovereign_bonds", "ois"),
        runner=_static_case_runner(
            [
                op_sov_cross_market("UST", "DE_BUND", "2Y"),
                op_ois_cross_market("USD_SOFR_OIS", "EUR_ESTR_OIS", "2Y"),
            ]
        ),
    ),
    PromptCase(
        case_id="cross_06",
        group="cross",
        prompt="Run the US 2s5s10s treasury fly, and see if the SOFR 2s10s swap spread agrees with the cash steepness.",
        expected_action="multi_domain",
        expected_domains=("sovereign_bonds", "ois"),
        runner=_static_case_runner(
            [
                op_sov_butterfly("UST", "2Y", "5Y", "10Y"),
                op_ois_curve_spread("USD_SOFR_OIS", "2Y", "10Y"),
            ]
        ),
    ),
    PromptCase(
        case_id="cross_07",
        group="cross",
        prompt="What regime is the Bund curve in right now? Also pull the 1Y1Y and 5Y5Y ESTR forwards to see what's driving it.",
        expected_action="multi_domain",
        expected_domains=("sovereign_bonds", "ois"),
        runner=_static_case_runner(
            [
                op_sov_regime("DE_BUND", lookback_period="1d"),
                op_ois_forward_tenor("EUR_ESTR_OIS", "1Y", "2Y"),
                op_ois_forward_tenor("EUR_ESTR_OIS", "5Y", "10Y"),
            ]
        ),
    ),
    PromptCase(
        case_id="cross_08",
        group="cross",
        prompt="Pull the 1-month and 2-year SOFR levels, then check where the US 2-year cash bond is trading.",
        expected_action="multi_domain",
        expected_domains=("sovereign_bonds", "ois"),
        runner=_static_case_runner(
            [
                op_ois_rate("USD_SOFR_OIS", "1M"),
                op_ois_rate("USD_SOFR_OIS", "2Y"),
                op_sov_yield("UST", "2Y"),
            ]
        ),
    ),
    PromptCase(
        case_id="cross_09",
        group="cross",
        prompt="Give me the absolute levels for the 30Y UK Gilt and the 30Y SONIA swap.",
        expected_action="multi_domain",
        expected_domains=("sovereign_bonds", "ois"),
        runner=_static_case_runner(
            [
                op_sov_yield("UK_GILT", "30Y"),
                op_ois_rate("GBP_SONIA_OIS", "30Y"),
            ]
        ),
    ),
    PromptCase(
        case_id="cross_10",
        group="cross",
        prompt="I need a complete check on the US vs Europe divergence. Get me the 10Y UST-Bund spread, the SOFR-ESTR 2Y differential, the US 2s10s cash spread, the 5Y5Y SOFR forward, and scan both universes for anything blowing out past 2 standard deviations.",
        expected_action="multi_domain",
        expected_domains=("sovereign_bonds", "ois"),
        runner=_static_case_runner(
            [
                op_sov_cross_market("UST", "DE_BUND", "10Y"),
                op_ois_cross_market("USD_SOFR_OIS", "EUR_ESTR_OIS", "2Y"),
                op_sov_curve_spread("UST", "2Y", "10Y"),
                op_ois_forward_tenor("USD_SOFR_OIS", "5Y", "10Y"),
                op_sov_scan(min_abs_z_score=2.0),
                op_ois_scan(min_abs_z_score=2.0),
            ]
        ),
    ),
]


def _select_cases(cases: Sequence[PromptCase], *, group: str, case_id: Optional[str]) -> list[PromptCase]:
    selected = list(cases)
    if group != "all":
        selected = [case for case in selected if case.group == group]
    if case_id:
        selected = [case for case in selected if case.case_id == case_id]
    return selected


def _case_passed(deterministic: DeterministicCaseResult) -> bool:
    return all(not outcome.mismatches for outcome in deterministic.outcomes)


def _serialize_deterministic(deterministic: DeterministicCaseResult) -> dict[str, Any]:
    return {
        "numeric_supported": deterministic.numeric_supported,
        "notes": list(deterministic.notes),
        "operations": [
            {
                "name": outcome.spec.name,
                "tool_name": outcome.spec.tool_name,
                "domain": outcome.spec.domain,
                "params": outcome.spec.params,
                "expected_param_subset": outcome.spec.expected_param_subset,
                "tool_summary": outcome.tool_summary,
                "sql_summary": outcome.sql_summary,
                "mismatches": list(outcome.mismatches),
            }
            for outcome in deterministic.outcomes
        ],
    }


def _print_deterministic_case(case: PromptCase, deterministic: DeterministicCaseResult) -> None:
    print(f"Prompt: {case.prompt}")
    print(
        f"Expected route: {case.expected_action} -> {list(case.expected_domains)}"
    )
    print(
        f"Deterministic baseline: {'PASS' if _case_passed(deterministic) else 'FAIL'}"
    )
    if deterministic.notes:
        for note in deterministic.notes:
            print(f"  note: {note}")

    for outcome in deterministic.outcomes:
        status = "PASS" if not outcome.mismatches else "FAIL"
        print(f"  [{status}] {outcome.spec.tool_name} :: {outcome.spec.name}")
        print(f"    expected params subset: {json.dumps(outcome.spec.expected_param_subset, default=str)}")
        if outcome.mismatches:
            for mismatch in outcome.mismatches[:12]:
                print(f"    mismatch: {mismatch}")
            if len(outcome.mismatches) > 12:
                print(f"    ... plus {len(outcome.mismatches) - 12} more mismatches")
        else:
            print(f"    tool summary: {json.dumps(outcome.tool_summary, default=str)}")
            print(f"    sql summary : {json.dumps(outcome.sql_summary, default=str)}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the prompt gauntlet against direct tool outputs and deterministic SQL baselines."
    )
    parser.add_argument(
        "--group",
        choices=("all", "ois", "sovereign", "cross"),
        default="all",
        help="Run only one prompt group (default: all).",
    )
    parser.add_argument(
        "--case",
        default=None,
        help="Run only one prompt case id, e.g. ois_04 or cross_10.",
    )
    parser.add_argument(
        "--report-json",
        default=None,
        help="Optional path to write the full JSON report.",
    )
    parser.add_argument(
        "--list-cases",
        action="store_true",
        help="List available case ids and exit.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    selected_cases = _select_cases(PROMPT_CASES, group=args.group, case_id=args.case)
    if args.list_cases:
        for case in selected_cases:
            print(f"{case.case_id:8s} [{case.group}] {case.prompt}")
        return 0

    if not selected_cases:
        print("No cases matched the selection.")
        return 1

    engine = get_db_engine()

    print("=" * 80)
    print("MULTI-AGENT PROMPT GAUNTLET")
    print("=" * 80)
    print(f"cases       : {len(selected_cases)}")
    print(f"group       : {args.group}")
    print("-" * 80)

    deterministic_results: dict[str, DeterministicCaseResult] = {}
    for index, case in enumerate(selected_cases, start=1):
        print_case_header(index, len(selected_cases), f"{case.case_id} [{case.group}]")
        deterministic = case.runner(engine)
        deterministic_results[case.case_id] = deterministic
        _print_deterministic_case(case, deterministic)

    total_cases = len(selected_cases)
    deterministic_failed = [
        case.case_id for case in selected_cases if not _case_passed(deterministic_results[case.case_id])
    ]

    print("-" * 80)
    print("SUMMARY")
    print("-" * 80)
    print(f"total_cases            : {total_cases}")
    print(f"deterministic_passed   : {total_cases - len(deterministic_failed)}")
    print(f"deterministic_failed   : {len(deterministic_failed)}")

    if deterministic_failed:
        print("deterministic failures:")
        for case_id in deterministic_failed:
            print(f"  - {case_id}")

    if args.report_json:
        report = {
            "cases": [
                {
                    "case_id": case.case_id,
                    "group": case.group,
                    "prompt": case.prompt,
                    "expected_action": case.expected_action,
                    "expected_domains": list(case.expected_domains),
                    "deterministic": _serialize_deterministic(deterministic_results[case.case_id]),
                }
                for case in selected_cases
            ]
        }
        report_path = Path(args.report_json)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2, default=str))
        print(f"report_json            : {report_path}")

    return 1 if deterministic_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
