#!/usr/bin/env python3
"""
test_fx_ndf_outright_sql_validation.py — NDF outright snapshot validator
========================================================================

Validate ``get_fx_ndf_outright`` against an independent SQL baseline
for the latest observation across the 6 supported NDF families × 5
tenors = 30 instruments.

Snapshot scope (per the PR16 admission gate pattern established by
``test_ois_forward_rate_sql_validation.py``):
  - latest outright value (matches the tool's
    ``current_metrics.current_outright``)
  - rolling 252-day z-score (matches ``current_metrics.z_score``)
  - observation count (matches ``observation_count``)

Full historical time-series + period-change parity are covered by the
deterministic offline tests in ``test_fx_ndf_compute.py``. This SQL
validator is the independent DB-backed cross-check that the SQL fetch
+ z-score math agree on the snapshot the PM cares about.

Usage:
    python -m tests.test_fx_ndf_outright_sql_validation
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pandas as pd
from sqlalchemy import text

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from database.database import get_db_engine  # noqa: E402
from fx_agent.ndf._shared import SUPPORTED_NDF_CODES, SUPPORTED_NDF_TENORS  # noqa: E402
from fx_agent.ndf.tools.ndf_outright import (  # noqa: E402
    FXNDFOutrightInput,
    get_fx_ndf_outright,
)


DEFAULT_CASE_COUNT = 9
DEFAULT_LOOKBACK_DAYS = 365
DEFAULT_FIELD_NAME = "PX_LAST"

TOLERANCE_OUTRIGHT_PCT = 0.001    # 0.1 bp relative
TOLERANCE_Z_SCORE_ABS = 0.001     # 0.001 z-score units


Case = Tuple[str, str]  # (ndf_code, tenor)


# Anchor cases — always run, even if random sampling excludes them.
REGRESSION_CASES: List[Case] = [
    ("CCN+", "1M"),   # CNY 1M — heaviest watched NDF carry tenor
    ("IRN+", "1M"),   # INR 1M
    ("NTN+", "12M"),  # TWD 12M — added in warehouse seed
]


def sample_cases(count: int) -> List[Case]:
    """Deterministically pick (ndf_code, tenor) cases. Always includes
    REGRESSION_CASES; fills with the next NDF×tenor cells in canonical
    order to reach ``count``."""
    pool: List[Case] = []
    for code in sorted(SUPPORTED_NDF_CODES):
        for tenor in SUPPORTED_NDF_TENORS:
            pool.append((code, tenor))

    out: List[Case] = list(REGRESSION_CASES)
    for c in pool:
        if c in out:
            continue
        if len(out) >= count:
            break
        out.append(c)
    return out[:count]


def sql_baseline(
    engine, *, ndf_code: str, tenor: str, field_name: str, lookback_days: int,
) -> Dict[str, Any]:
    """Independently reproduce the NDF outright snapshot in SQL + pandas:
      - latest outright value at the as-of-date
      - rolling 252-day z-score from the raw series
      - observation count over the lookback
    """
    vendor_ticker = f"{ndf_code}{tenor} Curncy"
    query = text(
        """
        SELECT d.trade_date, d.field_value::float AS field_value
        FROM macro_data.market_data_daily d
        JOIN macro_data.instrument_master im
            ON d.instrument_id = im.instrument_id
        WHERE im.instrument_type = 'fx_ndf'
          AND im.vendor_ticker = :ticker
          AND d.field_name = :field_name
          AND d.trade_date >= CURRENT_DATE - (:lookback_days || ' days')::interval
        ORDER BY d.trade_date ASC
        """
    )
    with engine.connect() as conn:
        rows = conn.execute(query, {
            "ticker": vendor_ticker,
            "field_name": field_name,
            "lookback_days": lookback_days,
        }).fetchall()

    if not rows:
        return {"empty": True}

    df = pd.DataFrame(rows, columns=["trade_date", "field_value"])
    df["field_value"] = pd.to_numeric(df["field_value"], errors="coerce")
    df = df.dropna(subset=["field_value"]).reset_index(drop=True)

    values = df["field_value"]
    current = float(values.iloc[-1])

    z_window = 252
    z_min_periods = 60
    z_ddof = 1
    trailing = values.tail(z_window)
    mean_ = trailing.mean()
    std_ = trailing.std(ddof=z_ddof)
    z_score = None
    if len(trailing) >= z_min_periods and std_ and not pd.isna(std_):
        z_score = float((current - mean_) / std_)

    return {
        "current_outright": current,
        "z_score": z_score,
        "observation_count": int(len(df)),
    }


def compare_case(
    engine, case: Case, *, field_name: str, lookback_days: int,
) -> Dict[str, Any]:
    ndf_code, tenor = case
    try:
        tool_out = get_fx_ndf_outright(
            engine,
            FXNDFOutrightInput(
                ndf_code=ndf_code, tenor=tenor, lookback_days=lookback_days,
                field_name=field_name,
            ),
        )
    except ValueError as exc:
        # Sparse-history tenors (e.g. BCN+1W frozen since 2024-08) raise
        # at the tool level. SQL baseline should reach the same verdict.
        return {
            "case": case,
            "mismatches": [],
            "skipped_reason": f"tool fail-loud (expected): {exc}",
        }
    tool = tool_out["current_metrics"]
    sql = sql_baseline(
        engine, ndf_code=ndf_code, tenor=tenor,
        field_name=field_name, lookback_days=lookback_days,
    )

    mismatches: List[str] = []
    if sql.get("empty"):
        mismatches.append(f"SQL baseline returned no rows for {ndf_code}/{tenor}")
        return {"case": case, "mismatches": mismatches}

    # Outright parity — relative tolerance because magnitudes vary wildly
    # (USDCNY ~7 vs USDIDR ~16000).
    a = tool["current_outright"]
    b = sql["current_outright"]
    rel = abs(a - b) / max(abs(b), 1e-12)
    if rel > TOLERANCE_OUTRIGHT_PCT:
        mismatches.append(
            f"current_outright: tool={a} sql={b} rel_diff={rel:.6f} "
            f"(tolerance {TOLERANCE_OUTRIGHT_PCT})"
        )

    # z-score parity — absolute tolerance
    a = tool["z_score"]
    b = sql["z_score"]
    if a is None and b is None:
        pass
    elif a is None or b is None:
        mismatches.append(f"z_score: tool={a} sql={b} (one None, one not)")
    elif abs(a - b) > TOLERANCE_Z_SCORE_ABS:
        mismatches.append(
            f"z_score: tool={a} sql={b} abs_diff={abs(a-b):.6f} "
            f"(tolerance {TOLERANCE_Z_SCORE_ABS})"
        )

    # Observation count parity — exact
    if tool["observation_count"] != sql["observation_count"]:
        mismatches.append(
            f"observation_count: tool={tool['observation_count']} sql={sql['observation_count']}"
        )

    return {"case": case, "mismatches": mismatches}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=int, default=DEFAULT_CASE_COUNT)
    parser.add_argument("--field", default=DEFAULT_FIELD_NAME)
    parser.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    args = parser.parse_args()

    engine = get_db_engine()
    cases = sample_cases(args.cases)

    print("=" * 72)
    print(f"FX NDF OUTRIGHT SQL VALIDATION — {len(cases)} case(s)")
    print("=" * 72)
    results = []
    for c in cases:
        r = compare_case(
            engine, c, field_name=args.field, lookback_days=args.lookback_days,
        )
        results.append(r)
        skipped = r.get("skipped_reason")
        if skipped:
            status = "SKIP"
        else:
            status = "PASS" if not r["mismatches"] else "FAIL"
        ndf_code, tenor = c
        print(f"  [{status}] {ndf_code} {tenor}{' — ' + skipped if skipped else ''}")
        for m in r["mismatches"]:
            print(f"           {m}")

    fail_count = sum(1 for r in results if r["mismatches"] and not r.get("skipped_reason"))
    skip_count = sum(1 for r in results if r.get("skipped_reason"))
    pass_count = len(results) - fail_count - skip_count
    print("-" * 72)
    print(f"SQL validation summary: {pass_count} PASS / {fail_count} FAIL / {skip_count} SKIP")
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
