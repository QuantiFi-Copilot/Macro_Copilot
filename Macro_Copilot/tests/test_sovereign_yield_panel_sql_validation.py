"""tests/test_sovereign_yield_panel_sql_validation.py

Phase 1 PR 21.

SQL-parity validator for the ``build_sovereign_yield_panel_tool``
primitive (PR 19).  Verifies that the assembled multi-leg Panel
matches an independent SQL baseline against the live test DB.

Pattern follows the established sovereign-side validators
(test_curve_spread_sql_validation.py / test_cross_market_spread_sql_validation.py)
but ships as a pytest test (skipif-no-DB) instead of a CLI script.
The CI state-layer job provides a Postgres service container with
the canonical universe ingested.

What this guards
----------------
* The primitive's SQL fetch (via ``fetch_instrument_panel``)
  returns exactly the rows the manual ``SELECT`` returns.
* The pivot logic + forward-fill convention is correct.
* The output Panel's columns + units_by_column match the leg
  specification exactly.
* The closed sovereign-family input validation prevents OIS curves
  leaking into a sovereign panel.
"""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd
import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ============================================================================
# DB availability — skip if Postgres unreachable
# ============================================================================


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
        engine = create_engine(url)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
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
# Canonical case — small, deterministic, ingestible
# ============================================================================

# UST 2Y + UST 10Y over a 30-day window starting from 2024-01-02.
# These instruments are part of the universe ingested by the
# sovereign_bonds playbook (GT2 Govt + GT10 Govt → instrument_master
# with curve_family='UST', tenor='2Y' / '10Y').  CI Postgres fixtures
# include this data with ingested YLD_YTM_MID values.
_CANONICAL_LEGS = [
    {"curve_family": "UST", "tenor": "2Y", "field_name": "YLD_YTM_MID"},
    {"curve_family": "UST", "tenor": "10Y", "field_name": "YLD_YTM_MID"},
]
_CANONICAL_START = date(2024, 1, 2)
_CANONICAL_END = date(2024, 2, 28)


# ============================================================================
# SQL baseline — independent reproduction
# ============================================================================


def _sql_baseline(engine, legs, start_dt, end_dt) -> pd.DataFrame:
    """Reproduce the primitive's expected output via hand-written SQL.

    Two-leg fetch with OR-clause filter (mirrors what
    ``fetch_instrument_panel`` does internally), pivots into wide
    format, applies the 5-day forward-fill convention.
    """
    from sqlalchemy import text

    where_clauses: List[str] = []
    params: Dict[str, Any] = {
        "start": start_dt.isoformat(),
        "end": end_dt.isoformat(),
    }
    for i, leg in enumerate(legs):
        params[f"cf_{i}"] = leg["curve_family"]
        params[f"tn_{i}"] = leg["tenor"]
        params[f"fn_{i}"] = leg["field_name"]
        where_clauses.append(
            f"(curve_family = :cf_{i} AND tenor = :tn_{i} "
            f"AND field_name = :fn_{i})"
        )
    sql = text(
        f"""
        SELECT trade_date, curve_family, tenor, field_value
        FROM macro_data.v_market_data_daily_enriched
        WHERE trade_date >= :start AND trade_date <= :end
          AND ({' OR '.join(where_clauses)})
        ORDER BY trade_date
        """
    )
    with engine.connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    if not rows:
        return pd.DataFrame()

    raw_df = pd.DataFrame(rows, columns=["trade_date", "curve_family", "tenor", "field_value"])
    # Cast Decimal → float at raw layer so all downstream math works.
    raw_df["field_value"] = raw_df["field_value"].astype(float)
    raw_df["leg_key"] = raw_df["curve_family"] + "_" + raw_df["tenor"]
    wide = (
        raw_df.pivot_table(
            index="trade_date",
            columns="leg_key",
            values="field_value",
            aggfunc="first",
        )
        .sort_index()
    )
    wide.index = pd.DatetimeIndex(pd.to_datetime(wide.index))
    # Forward-fill matching the primitive's config (5-day default).
    wide = wide.ffill(limit=5)
    # Business-days calendar policy (default).
    wide = wide[wide.index.dayofweek < 5]
    # Reorder columns to match the primitive's input-leg order (the
    # SQL pivot defaults to lex order — "UST_10Y" < "UST_2Y" — which
    # isn't what the primitive emits).
    desired_order = [f"{leg['curve_family']}_{leg['tenor']}" for leg in legs]
    wide = wide[[c for c in desired_order if c in wide.columns]]
    # Cast object/Decimal dtypes to float64 so the value-comparison
    # math doesn't break.
    wide = wide.astype(float)
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


def test_sovereign_yield_panel_matches_sql_baseline(engine):
    """Single canonical case: UST 2Y + 10Y over ~2 months.

    Primitive's output Panel must equal the hand-written SQL
    baseline column-for-column, row-for-row.
    """
    from rates_agent.sovereign_bonds.tools.sovereign_yield_panel import (
        CONFIG_PATH,
        SovereignYieldPanelInput,
        SovereignYieldPanelLegSpec,
        build_sovereign_yield_panel,
    )
    from shared.config import load_tool_config

    # SQL baseline
    expected_wide = _sql_baseline(
        engine, _CANONICAL_LEGS, _CANONICAL_START, _CANONICAL_END,
    )
    if expected_wide.empty:
        pytest.skip(
            "Canonical UST 2Y+10Y window has no ingested data — "
            "verify the test-DB sovereign_bonds playbook ran "
            "successfully."
        )

    # Primitive output
    cfg = load_tool_config(CONFIG_PATH)
    params = SovereignYieldPanelInput(
        legs=[SovereignYieldPanelLegSpec(**leg) for leg in _CANONICAL_LEGS],
        start_date=_CANONICAL_START,
        end_date=_CANONICAL_END,
    )
    result = build_sovereign_yield_panel(engine=engine, params=params, config=cfg)
    assert "error" not in result, (
        f"Primitive returned error envelope: {result.get('error')}"
    )
    actual_panel = result["panel"]
    # When model_dump'd, `panel` is a dict; revalidate to get the typed Panel.
    from rates_agent.sovereign_bonds.tools.sovereign_yield_panel.schemas import (
        SovereignYieldPanelOutput,
    )
    validated = SovereignYieldPanelOutput.model_validate(result)
    actual_df = validated.panel.payload

    # ----- Assertions -----
    # Column set + order match
    assert list(actual_df.columns) == list(expected_wide.columns), (
        f"Column order drift.\n"
        f"  Expected: {list(expected_wide.columns)}\n"
        f"  Got:      {list(actual_df.columns)}"
    )

    # Index matches (DatetimeIndex, sorted, no NaN dates)
    assert len(actual_df) == len(expected_wide), (
        f"Row count drift: expected {len(expected_wide)}, got {len(actual_df)}"
    )
    pd.testing.assert_index_equal(actual_df.index, expected_wide.index)

    # Values match within float tolerance (yield-percent precision)
    for col in expected_wide.columns:
        diff = (actual_df[col] - expected_wide[col]).abs()
        max_diff = float(diff.max())
        assert max_diff < 1e-9, (
            f"Column {col!r} drifted from SQL baseline; max abs diff = {max_diff}"
        )


def test_sovereign_yield_panel_rejects_ois_curve():
    """Closed-family invariant: OIS curve families are rejected at
    input validation, NOT at SQL fetch time.  This test does NOT
    require the DB."""
    from rates_agent.sovereign_bonds.tools.sovereign_yield_panel.schemas import (
        SovereignYieldPanelInput,
        SovereignYieldPanelLegSpec,
    )

    with pytest.raises(ValueError, match="not a recognised sovereign family"):
        SovereignYieldPanelInput(
            legs=[
                SovereignYieldPanelLegSpec(
                    curve_family="USD_SOFR_OIS",
                    tenor="2Y",
                    field_name="PX_LAST",
                ),
            ],
            start_date=date(2024, 1, 1),
        )
