"""tests/test_financing_rate_sql_validation.py

Phase 1 PR 21.

SQL-parity validator for the ``compute_financing_rate_tool``
primitive (PR 19).  Verifies both methods against independent
baselines:

  * ``constant_rate`` — DB-independent; baseline is the analytical
    pd.Series construction.
  * ``overnight_index_proxy`` — requires the test DB to have the
    proxy curve's shortest-tenor OIS ingested (per
    ``rates_agent/playbooks/ois.yml``).

Pattern mirrors the established ``swap_spread_sql_validation`` shape
(two-leg fetch with field-name disambiguation) adapted for the
financing-rate primitive's single-leg shape.
"""

from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict

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


# ============================================================================
# constant_rate path — DB-independent baseline
# ============================================================================


def test_financing_rate_constant_rate_matches_analytical_baseline():
    """For method=constant_rate, the primitive should produce a
    daily Series whose value is exactly the caller-supplied
    constant rate over the business-day calendar.  No DB required."""
    from rates_agent.ois.tools.financing_rate import (
        CONFIG_PATH,
        FinancingRateInput,
        compute_financing_rate,
    )
    from rates_agent.ois.tools.financing_rate.schemas import FinancingRateOutput
    from shared.config import load_tool_config

    cfg = load_tool_config(CONFIG_PATH)
    start = date(2024, 1, 2)
    end = date(2024, 2, 28)
    rate_pct = 5.30

    params = FinancingRateInput(
        method="constant_rate",
        start_date=start,
        end_date=end,
        constant_rate_pct=rate_pct,
        calendar="business_days",
    )
    result = compute_financing_rate(engine=None, params=params, config=cfg)
    assert "error" not in result, f"Unexpected error: {result.get('error')}"

    # Independent baseline.
    expected_idx = pd.bdate_range(start=start, end=end)
    expected_values = np.full(len(expected_idx), rate_pct, dtype=float)
    expected = pd.DataFrame(
        {"financing_constant_5.3pct": expected_values},
        index=pd.DatetimeIndex(expected_idx),
    )

    validated = FinancingRateOutput.model_validate(result)
    actual = validated.panel.payload

    assert list(actual.columns) == list(expected.columns)
    assert len(actual) == len(expected)
    pd.testing.assert_index_equal(actual.index, expected.index)
    np.testing.assert_array_almost_equal(
        actual.iloc[:, 0].values,
        expected.iloc[:, 0].values,
        decimal=9,
    )


# ============================================================================
# overnight_index_proxy path — DB-anchored baseline
# ============================================================================


@pytest.mark.skipif(
    not _DB_AVAILABLE,
    reason=f"Postgres not reachable at {_DB_URL!r}",
)
def test_financing_rate_overnight_index_proxy_matches_sql_baseline():
    """For method=overnight_index_proxy, the primitive reads the
    OIS curve's shortest-available tenor (1W per playbook) as the
    O/N rate proxy.  Independent SQL baseline asserts byte equality.
    """
    from sqlalchemy import create_engine, text

    from rates_agent.ois.tools.financing_rate import (
        CONFIG_PATH,
        FinancingRateInput,
        compute_financing_rate,
    )
    from rates_agent.ois.tools.financing_rate.schemas import FinancingRateOutput
    from shared.config import load_tool_config

    engine = create_engine(_DB_URL)
    try:
        # Canonical case: SOFR overnight proxy over Jan-Feb 2024.
        proxy_curve = "USD_SOFR_OIS"
        proxy_tenor = "1W"  # the shortest-available OIS tenor per playbook
        start = date(2024, 1, 2)
        end = date(2024, 2, 28)

        # SQL baseline
        sql = text(
            """
            SELECT trade_date, field_value
            FROM macro_data.v_market_data_daily_enriched
            WHERE curve_family = :cf
              AND tenor = :tn
              AND field_name = 'PX_LAST'
              AND trade_date >= :start
              AND trade_date <= :end
            ORDER BY trade_date
            """
        )
        with engine.connect() as conn:
            rows = conn.execute(sql, {
                "cf": proxy_curve, "tn": proxy_tenor,
                "start": start.isoformat(), "end": end.isoformat(),
            }).fetchall()

        if not rows:
            pytest.skip(
                f"No {proxy_curve} {proxy_tenor} rows in the canonical "
                "window — the test DB's OIS playbook may not be fully "
                "ingested.  CI's state-layer job runs the full ingestion."
            )

        sql_df = pd.DataFrame(rows, columns=["trade_date", "field_value"])
        # Cast Decimal → float at raw layer so float comparisons work.
        sql_df["field_value"] = sql_df["field_value"].astype(float)
        # Build index WITHOUT a name to match the primitive's output
        # (the primitive's Series.index has name=None per pandas
        # DatetimeIndex construction from a numpy array of timestamps).
        expected_idx = pd.DatetimeIndex(
            pd.to_datetime(sql_df["trade_date"].values),
        )
        expected_values = sql_df["field_value"].values

        # Primitive output
        cfg = load_tool_config(CONFIG_PATH)
        params = FinancingRateInput(
            method="overnight_index_proxy",
            start_date=start,
            end_date=end,
            proxy_curve=proxy_curve,
        )
        result = compute_financing_rate(engine=engine, params=params, config=cfg)
        assert "error" not in result, f"Unexpected error: {result.get('error')}"

        validated = FinancingRateOutput.model_validate(result)
        actual = validated.panel.payload

        # Shape match
        assert len(actual) == len(expected_idx)
        pd.testing.assert_index_equal(actual.index, expected_idx)

        # Value match (1e-9 tolerance for float representation)
        np.testing.assert_array_almost_equal(
            actual.iloc[:, 0].values,
            expected_values,
            decimal=9,
        )

        # Column name encodes the proxy curve identity
        expected_col = f"financing_proxy_{proxy_curve.lower()}"
        assert list(actual.columns) == [expected_col], (
            f"Column name drift; expected {expected_col!r}, got "
            f"{list(actual.columns)}"
        )
    finally:
        engine.dispose()


# ============================================================================
# Method-validator invariants (DB-independent)
# ============================================================================


def test_financing_rate_term_repo_curve_raises():
    """V1 declared-but-not-implemented method must raise at compute
    time with a pointer to the planned_extensions doc."""
    from rates_agent.ois.tools.financing_rate import (
        CONFIG_PATH,
        FinancingRateInput,
        compute_financing_rate,
    )
    from shared.config import load_tool_config

    cfg = load_tool_config(CONFIG_PATH)
    params = FinancingRateInput(
        method="term_repo_curve",
        start_date=date(2024, 1, 1),
        end_date=date(2024, 2, 1),
    )
    result = compute_financing_rate(engine=None, params=params, config=cfg)
    assert "error" in result
    assert "term_repo_curve" in result["error"]
    assert "planned_extensions" in result["error"]
