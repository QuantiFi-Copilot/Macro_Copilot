"""tests/test_breakeven_inflation_sql_validation.py

Phase 1 PR 21.

SQL-parity validator for the ``calculate_breakeven_inflation_tool``
primitive (PR 19).  V1 implements the ``nominal_breakeven`` convention
(``(nominal_yield − real_yield) × 100`` in bps) at matched tenor.

Independent SQL baseline reproduces:
  * The two-leg fetch (UST nominal + USD_TIPS real at same tenor).
  * The pivot + alignment + ffill convention.
  * The breakeven computation in bps.
  * The rolling z-score window.

Pattern mirrors test_curve_spread_sql_validation.py adapted for
two curves at one matched tenor (not two tenors on one curve).
"""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pandas as pd
import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def _build_url() -> str:
    user = os.getenv("DB_USER", "quantuser")
    password = os.getenv("DB_PASSWORD", "myStrongPass")
    host = os.getenv("DB_HOST", "localhost")
    port = os.getenv("DB_PORT", "5433")
    db_name = os.getenv("DB_NAME", "macrodata")
    return f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{db_name}"


def _db_reachable(url: str) -> bool:
    try:
        from sqlalchemy import create_engine, text
        e = create_engine(url)
        with e.connect() as conn:
            conn.execute(text("SELECT 1"))
        e.dispose()
        return True
    except Exception:
        return False


_DB_URL = _build_url()
_DB_AVAILABLE = _db_reachable(_DB_URL)

pytestmark = pytest.mark.skipif(
    not _DB_AVAILABLE,
    reason=f"Postgres not reachable at {_DB_URL!r}",
)


# ============================================================================
# Canonical case
# ============================================================================
#
# UST nominal vs USD_TIPS real at 10Y matched tenor.  The TIPS
# playbook (rates_agent/playbooks/inflation_indexed_bonds.yml)
# ingests GTII10 Govt as curve_family='USD_TIPS', tenor='10Y'.
# The sovereign_bonds playbook ingests GT10 Govt as 'UST', '10Y'.
# Both fields are YLD_YTM_MID.

_NOMINAL_CURVE = "UST"
_REAL_CURVE = "USD_TIPS"
_TENOR = "10Y"
_LOOKBACK = 365


# ============================================================================
# SQL baseline
# ============================================================================


def _sql_baseline(engine, start_dt):
    """Independent two-leg fetch + alignment + breakeven math."""
    from sqlalchemy import text

    sql = text(
        """
        SELECT trade_date, curve_family, tenor, field_value
        FROM macro_data.v_market_data_daily_enriched
        WHERE curve_family IN (:nom, :real)
          AND tenor = :tn
          AND field_name = 'YLD_YTM_MID'
          AND trade_date >= :start
        ORDER BY trade_date
        """
    )
    with engine.connect() as conn:
        rows = conn.execute(sql, {
            "nom": _NOMINAL_CURVE,
            "real": _REAL_CURVE,
            "tn": _TENOR,
            "start": start_dt.isoformat(),
        }).fetchall()
    if not rows:
        return None

    raw = pd.DataFrame(rows, columns=["trade_date", "curve_family", "tenor", "field_value"])
    # Cast Decimal → float at raw layer so downstream arithmetic works.
    raw["field_value"] = raw["field_value"].astype(float)
    # Tag legs with stable names so the pivot is unambiguous.
    raw["leg"] = raw["curve_family"].apply(
        lambda cf: "nominal" if cf == _NOMINAL_CURVE else "real"
    )
    wide = (
        raw.pivot_table(
            index="trade_date",
            columns="leg",
            values="field_value",
            aggfunc="first",
        )
        .sort_index()
    )
    wide.index = pd.DatetimeIndex(pd.to_datetime(wide.index))
    # Defensive: pivot_table can re-introduce object dtypes when the
    # source column had a non-native numeric type.
    wide = wide.astype(float)
    # 5-day ffill (matches the primitive's ffill_limit_days default).
    wide = wide.ffill(limit=5)
    # Drop any remaining row where either leg is NaN (matches compute's
    # dropna after ffill).
    wide = wide.dropna(subset=["nominal", "real"], how="any")
    if wide.empty:
        return None

    # Breakeven in bps, rounded to 2dp (matches breakeven_bps_round_decimals).
    wide["breakeven_bps"] = ((wide["nominal"] - wide["real"]) * 100.0).round(2)
    return wide


# ============================================================================
# Test
# ============================================================================


@pytest.fixture
def engine():
    from sqlalchemy import create_engine
    e = create_engine(_DB_URL)
    yield e
    e.dispose()


def test_breakeven_inflation_matches_sql_baseline(engine):
    """The primitive's current_metrics.current_breakeven_bps must
    match the SQL baseline's last row's breakeven_bps within
    rounding tolerance."""
    from rates_agent.sovereign_bonds.tools.breakeven_inflation import (
        CONFIG_PATH,
        BreakevenInflationInput,
        calculate_breakeven_inflation,
    )
    from shared.config import load_tool_config

    # Fetch baseline first so we can compute lookback-days bound
    # consistently with the primitive's date.today() anchor.
    cfg = load_tool_config(CONFIG_PATH)
    z_buffer = float(cfg.convention_value("z_score_buffer_multiplier"))
    z_window = int(cfg.convention_value("z_score_window_days"))
    # Anchor the independent fetch window to the latest available
    # trade_date — the same way the primitive now does — so the parity
    # check compares identical windows even when the DB lags "today".
    from shared.analytics.rates_fetch import latest_trade_date

    anchor = latest_trade_date(engine, tenor=_TENOR) or date.today()
    fetch_start = anchor - timedelta(
        days=_LOOKBACK + int(z_window * z_buffer),
    )

    sql_wide = _sql_baseline(engine, fetch_start)
    if sql_wide is None:
        pytest.skip(
            f"No {_NOMINAL_CURVE}/{_REAL_CURVE} {_TENOR} rows in the "
            f"canonical lookback window since {fetch_start} — verify "
            "the TIPS + sovereign playbooks both ingested."
        )

    expected_last = sql_wide.iloc[-1]

    # Primitive output
    params = BreakevenInflationInput(
        nominal_curve_family=_NOMINAL_CURVE,
        real_curve_family=_REAL_CURVE,
        tenor=_TENOR,
        lookback_days=_LOOKBACK,
    )
    result = calculate_breakeven_inflation(engine=engine, params=params, config=cfg)
    assert "error" not in result, f"Unexpected error: {result.get('error')}"

    metrics = result["current_metrics"]

    # current_breakeven_bps matches SQL baseline at the latest date
    assert abs(
        metrics["current_breakeven_bps"] - float(expected_last["breakeven_bps"])
    ) < 0.011, (
        f"current_breakeven_bps drift: tool={metrics['current_breakeven_bps']}, "
        f"sql={float(expected_last['breakeven_bps'])}"
    )

    # Per-leg yields match (PERCENT precision)
    assert abs(
        metrics["nominal_yield_pct"] - float(expected_last["nominal"])
    ) < 1e-9, (
        f"nominal_yield_pct drift: tool={metrics['nominal_yield_pct']}, "
        f"sql={float(expected_last['nominal'])}"
    )
    assert abs(
        metrics["real_yield_pct"] - float(expected_last["real"])
    ) < 1e-9, (
        f"real_yield_pct drift: tool={metrics['real_yield_pct']}, "
        f"sql={float(expected_last['real'])}"
    )

    # As-of date matches
    assert metrics["as_of_date"] == expected_last.name.strftime("%Y-%m-%d")


def test_breakeven_inflation_inflation_swap_convention_raises():
    """V1 declared-but-not-implemented convention must raise (no DB
    required)."""
    from rates_agent.sovereign_bonds.tools.breakeven_inflation import (
        CONFIG_PATH,
        BreakevenInflationInput,
        calculate_breakeven_inflation,
    )
    from shared.config import load_tool_config

    cfg = load_tool_config(CONFIG_PATH)
    params = BreakevenInflationInput(
        nominal_curve_family="UST",
        real_curve_family="USD_TIPS",
        tenor="10Y",
        convention="inflation_swap_breakeven",
    )
    result = calculate_breakeven_inflation(
        engine=None, params=params, config=cfg,
    )
    assert "error" in result
    assert "inflation_swap_breakeven" in result["error"]
