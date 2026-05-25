#!/usr/bin/env python3
"""
test_build_policy_futures_strip_panel_sql_validation.py — Policy-
                                                          futures
                                                          strip Panel
                                                          SQL validator
=================================================================

Independently reproduces the ``build_policy_futures_strip_panel``
primitive's wide policy-futures implied-rate panel via a hand-
written SQL pivot against the live
``macro_data.v_market_data_daily_enriched`` view +
``macro_data.instrument_master`` (for the per-curve_family
``inverse_pricing`` flag used in the implied-rate conversion).

The validator does NOT call back into the Python primitive's
backend (``shared.analytics.panel_assembly.fetch_policy_futures_
strip_panel``) — it issues its own SQL, computes the implied-
rate conversion in raw SQL via the per-cell ``inverse_pricing``
flag (``CASE WHEN inverse_pricing THEN 100 - last_price ELSE
last_price END``), pivots in raw pandas, and applies the calendar
+ ffill policies independently so the cross-check is a real
independent baseline.

Test cases (all PINNED so the validator is deterministic across
runs):

  1. SOFR_FUT-only narrow universe over
     ``2024-01-02 .. 2024-02-28`` (verifies the inverse-pricing
     conversion for an RFR strip).
  2. SONIA_FUT-only narrow universe over the same anchor
     (verifies the inverse-pricing flag matches DB truth for the
     UK SONIA strip).
  3. All-three-curve-families broad universe over the same
     anchor — verifies the EUR_SHORT_RATE_FUT Buba-mix rows
     ARE INCLUDED RAW (no NotImplementedError, no row-drop, no
     silent averaging).

For every case the validator asserts:

  - row count match
  - column set + order match (Python and SQL both use the
    caller-supplied curve_family × ascending strip_position
    Cartesian product)
  - cell-by-cell PERCENT match within float-precision tolerance
    (both sides compute ``implied_rate_pct`` the SAME way: per-
    curve_family inverse_pricing flag → ``100 - raw_price`` or
    ``raw_price``)
  - the methodology_card field is populated on the Python output
  - the per-curve-family RFR / IBOR regime caveat is present
    (SOFR_FUT=RFR, SONIA_FUT=RFR, EUR_SHORT_RATE_FUT=IBOR)
  - the EUR_SHORT_RATE_FUT row carries the Buba-mix caveat
    citing futures_pack_average_simple

DB safety: read-only SELECTs only.  Excluded from pytest
collection via ``tests/conftest.py``'s ``collect_ignore`` list
(matches the established sibling pattern).
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd
from sqlalchemy import text

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from database.database import get_db_engine  # noqa: E402
from rates_agent.policy_futures.tools.build_policy_futures_strip_panel import (  # noqa: E402
    CONFIG_PATH,
    BuildPolicyFuturesStripPanelInput,
    BuildPolicyFuturesStripPanelOutput,
    build_policy_futures_strip_panel,
)
from shared.analytics.panel_assembly import _strip_panel_column_key  # noqa: E402
from shared.config import load_tool_config  # noqa: E402


INSTRUMENT_TYPE = "policy_future"
DEFAULT_FIELD = "PX_LAST"

POLICY_FUTURES_CURVE_FAMILIES_ALL: Tuple[str, ...] = (
    "SOFR_FUT", "SONIA_FUT", "EUR_SHORT_RATE_FUT",
)
POLICY_FUTURES_STRIP_POSITIONS_ALL: Tuple[int, ...] = (
    1, 2, 3, 4, 5, 6, 7, 8,
)

# Live DB-verified per-curve_family short-rate regime mapping.
# This is the desk-honest cross-CB mapping the methodology card
# surfaces.  Verified at build time (2026-05-25) against ADR 0013 V1
# (policy_futures domain scope) AND the live
# ``instrument_master.attributes->>'inverse_pricing'`` flag values.
EXPECTED_SHORT_RATE_REGIME: Dict[str, str] = {
    "SOFR_FUT": "RFR",
    "SONIA_FUT": "RFR",
    "EUR_SHORT_RATE_FUT": "IBOR",
}


# A pinned anchor inside the ingested policy_futures window so the
# validator is deterministic across runs.  2024-01..2024-02 covers
# ~40 business days across all three curve families with the V1
# playbook ingestion.
_PINNED_START = date(2024, 1, 2)
_PINNED_END = date(2024, 2, 28)


# ---------------------------------------------------------------------------
# Case shape: (label, curve_families, strip_positions, start, end,
#              calendar_policy, missing_data_policy)
# ---------------------------------------------------------------------------
Case = Tuple[
    str,
    Tuple[str, ...],
    Tuple[int, ...],
    date,
    Optional[date],
    Optional[str],
    Optional[str],
]


REGRESSION_CASES: List[Case] = [
    (
        "SOFR_FUT-only full strip over 2024-01-02..2024-02-28",
        ("SOFR_FUT",),
        POLICY_FUTURES_STRIP_POSITIONS_ALL,
        _PINNED_START,
        _PINNED_END,
        None,  # default calendar policy (business_days)
        None,  # default missingness policy (forward_fill_only)
    ),
    (
        "SONIA_FUT-only full strip over 2024-01-02..2024-02-28",
        ("SONIA_FUT",),
        POLICY_FUTURES_STRIP_POSITIONS_ALL,
        _PINNED_START,
        _PINNED_END,
        None,
        None,
    ),
    (
        "All-three-curve-families full strip over 2024-01-02..2024-02-28 "
        "(verifies EUR_SHORT_RATE_FUT raw Buba mix preserved)",
        POLICY_FUTURES_CURVE_FAMILIES_ALL,
        POLICY_FUTURES_STRIP_POSITIONS_ALL,
        _PINNED_START,
        _PINNED_END,
        None,
        None,
    ),
]


_BASELINE_SQL = text(
    """
    SELECT
        v.trade_date,
        v.curve_family,
        (v.attributes->>'strip_position')::int AS strip_position,
        v.field_value::double precision        AS raw_price,
        i.inverse_pricing                       AS inverse_pricing
    FROM macro_data.v_market_data_daily_enriched AS v
    JOIN (
        SELECT
            curve_family,
            (attributes->>'strip_position')::int     AS strip_position,
            (attributes->>'inverse_pricing')::bool   AS inverse_pricing
        FROM macro_data.instrument_master
        WHERE instrument_type = :instrument_type
          AND is_rolling_contract = TRUE
          AND is_active           = TRUE
          AND curve_family        = ANY(:curve_families)
          AND (attributes->>'strip_position')::int = ANY(:strip_positions)
    ) AS i
      ON i.curve_family    = v.curve_family
     AND i.strip_position  = (v.attributes->>'strip_position')::int
    WHERE v.instrument_type = :instrument_type
      AND v.field_name      = :field_name
      AND v.curve_family    = ANY(:curve_families)
      AND (v.attributes->>'strip_position')::int = ANY(:strip_positions)
      AND v.field_value    IS NOT NULL
      AND v.trade_date     >= :start_date
      AND v.trade_date     <= :end_date
    ORDER BY v.trade_date, v.curve_family,
             (v.attributes->>'strip_position')::int
    """
)


def sql_baseline(
    engine,
    *,
    curve_families: Sequence[str],
    strip_positions: Sequence[int],
    start_date: date,
    end_date: date,
    calendar_policy: str,
    field_name: str,
    ffill_limit_days: int,
) -> pd.DataFrame:
    """Independent SQL reproduction of the policy-futures strip
    panel.

    Issues its own pivot (NOT a call into the Python primitive's
    backend), pivots in raw pandas, applies the per-cell
    inverse-pricing conversion via the inverse_pricing flag joined
    from ``instrument_master``, applies the calendar filter the
    primitive applies, and forward-fills with the same
    ``ffill_limit_days``.
    """
    with engine.connect() as conn:
        rows = conn.execute(
            _BASELINE_SQL,
            {
                "instrument_type": INSTRUMENT_TYPE,
                "field_name": field_name,
                "curve_families": list(curve_families),
                "strip_positions": [int(p) for p in strip_positions],
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
            },
        ).mappings().all()

    if not rows:
        return pd.DataFrame()

    raw = pd.DataFrame([dict(r) for r in rows])

    # Apply the implied-rate conversion per row using the SCD2
    # inverse_pricing flag pulled from instrument_master via the
    # subquery join.
    raw["implied_rate_pct"] = raw.apply(
        lambda r: (
            100.0 - float(r["raw_price"])
            if bool(r["inverse_pricing"])
            else float(r["raw_price"])
        ),
        axis=1,
    )

    # Build the column key and pivot to wide.
    raw["_col_key"] = [
        _strip_panel_column_key(cf, int(pos))
        for cf, pos in zip(raw["curve_family"], raw["strip_position"])
    ]
    sorted_positions = sorted(int(p) for p in strip_positions)
    ordered_columns = [
        _strip_panel_column_key(cf, pos)
        for cf in curve_families
        for pos in sorted_positions
    ]

    wide = (
        raw.pivot_table(
            index="trade_date",
            columns="_col_key",
            values="implied_rate_pct",
            aggfunc="first",
        )
        .sort_index()
    )
    wide.index = pd.DatetimeIndex(pd.to_datetime(wide.index))

    # Reorder columns deterministically.
    for col in ordered_columns:
        if col not in wide.columns:
            wide[col] = float("nan")
    wide = wide[ordered_columns]

    if ffill_limit_days and ffill_limit_days > 0:
        wide = wide.ffill(limit=ffill_limit_days)

    if calendar_policy == "business_days":
        wide = wide[wide.index.dayofweek < 5]
    elif calendar_policy == "instrument_native":
        pass
    else:
        raise ValueError(
            f"sql_baseline: unknown calendar_policy "
            f"{calendar_policy!r}."
        )

    return wide.astype(float)


def compare_panels(
    actual: pd.DataFrame,
    expected: pd.DataFrame,
    *,
    tolerance: float = 1e-9,
) -> List[str]:
    mismatches: List[str] = []

    if list(actual.columns) != list(expected.columns):
        mismatches.append(
            "column order drift:\n"
            f"  expected: {list(expected.columns)}\n"
            f"  got:      {list(actual.columns)}"
        )
        return mismatches  # downstream comparisons are pointless

    if len(actual) != len(expected):
        mismatches.append(
            f"row count drift: expected {len(expected)}, "
            f"got {len(actual)}"
        )

    try:
        pd.testing.assert_index_equal(actual.index, expected.index)
    except AssertionError as exc:
        mismatches.append(f"index drift: {exc}")

    common_idx = actual.index.intersection(expected.index)
    for col in expected.columns:
        if col not in actual.columns:
            mismatches.append(f"column {col!r} missing on Python output")
            continue
        diff = (
            actual.loc[common_idx, col] - expected.loc[common_idx, col]
        ).abs()
        max_diff = float(diff.max(skipna=True))
        if pd.isna(max_diff):
            # All cells NaN on both sides — that's fine.
            continue
        if max_diff > tolerance:
            mismatches.append(
                f"column {col!r} drifted from SQL baseline; "
                f"max abs diff = {max_diff}"
            )
    return mismatches


def _assert_methodology_card(
    tool_result: Dict[str, Any],
    curve_families: Sequence[str],
) -> List[str]:
    """Verify the methodology card is populated AND the per-curve
    regime caveat matches the desk-honest mapping."""
    mismatches: List[str] = []
    card = tool_result.get("methodology_card", {})
    if not card:
        return ["methodology_card missing on Python output"]
    for key in (
        "field_name",
        "panel_value_field",
        "calendar_policy",
        "missing_data_policy",
        "ffill_limit_days",
        "ffill_source_tag",
        "curve_families",
        "strip_positions",
        "column_axis_encoding",
        "column_axis_encoding_separator",
        "inverse_pricing_handling",
        "cross_region_business_days_caveat",
        "rolling_generic_strip_caveat",
        "curve_family_reference",
        "methodology_label",
    ):
        if key not in card:
            mismatches.append(
                f"methodology_card missing key {key!r}"
            )
    if mismatches:
        return mismatches

    if card["panel_value_field"] != "implied_rate_pct":
        mismatches.append(
            f"panel_value_field drift: expected 'implied_rate_pct', "
            f"got {card['panel_value_field']!r}"
        )

    cf_ref = card["curve_family_reference"]
    for cf in curve_families:
        if cf not in cf_ref:
            mismatches.append(
                f"curve_family_reference missing entry for {cf!r}"
            )
            continue
        expected_regime = EXPECTED_SHORT_RATE_REGIME[cf]
        actual_regime = cf_ref[cf].get("short_rate_regime")
        if actual_regime != expected_regime:
            mismatches.append(
                f"curve_family={cf!r} short_rate_regime drift: "
                f"expected {expected_regime!r}, got "
                f"{actual_regime!r}"
            )
        if cf_ref[cf].get("inverse_pricing") is not True:
            mismatches.append(
                f"curve_family={cf!r} inverse_pricing should be "
                f"True for V1 universe; got "
                f"{cf_ref[cf].get('inverse_pricing')!r}"
            )
        # The regime caveat string must name the regime label
        # (RFR / IBOR) explicitly so the disclosure is honest.
        regime_caveat = cf_ref[cf].get("regime_caveat", "")
        if expected_regime not in regime_caveat:
            mismatches.append(
                f"curve_family={cf!r} regime_caveat does NOT "
                f"name the regime {expected_regime!r}; got: "
                f"{regime_caveat!r}"
            )

    if "EUR_SHORT_RATE_FUT" in curve_families:
        eur_block = cf_ref.get("EUR_SHORT_RATE_FUT", {})
        buba_caveat = eur_block.get("buba_mix_caveat", "")
        for token in (
            "delivery_month_type",
            "futures_pack_average_simple",
        ):
            if token not in buba_caveat:
                mismatches.append(
                    f"EUR_SHORT_RATE_FUT buba_mix_caveat missing "
                    f"token {token!r}; got: {buba_caveat!r}"
                )

    return mismatches


def run_case(engine, *, case: Case) -> List[str]:
    (
        label,
        curve_families,
        strip_positions,
        start_dt,
        end_dt,
        calendar_policy_in,
        missing_policy_in,
    ) = case
    cfg = load_tool_config(CONFIG_PATH)
    ffill_limit_days = int(cfg.convention_value("ffill_limit_days"))
    default_calendar = cfg.convention_value("calendar_policy")
    field_name = cfg.convention_value("default_price_field")
    calendar_policy = calendar_policy_in or default_calendar

    params = BuildPolicyFuturesStripPanelInput(
        start_date=start_dt,
        end_date=end_dt,
        curve_families=list(curve_families),
        strip_positions=list(strip_positions),
        calendar_policy=calendar_policy_in,
        missing_data_policy=missing_policy_in,
    )
    tool_result = build_policy_futures_strip_panel(
        engine=engine, params=params, config=cfg,
    )
    if "error" in tool_result:
        return [
            f"Python tool returned error envelope: {tool_result['error']}"
        ]

    card_mismatches = _assert_methodology_card(tool_result, curve_families)
    if card_mismatches:
        return card_mismatches

    validated = BuildPolicyFuturesStripPanelOutput.model_validate(tool_result)
    actual_panel = validated.panel.payload

    expected_wide = sql_baseline(
        engine,
        curve_families=curve_families,
        strip_positions=strip_positions,
        start_date=start_dt,
        end_date=end_dt,
        calendar_policy=calendar_policy,
        field_name=field_name,
        ffill_limit_days=ffill_limit_days,
    )
    if expected_wide.empty:
        return [
            "SQL baseline returned 0 rows — DB coverage gap?  "
            f"(curve_families={curve_families!r}, "
            f"strip_positions={strip_positions!r}, "
            f"start={start_dt.isoformat()}, end={end_dt.isoformat()})"
        ]

    return compare_panels(actual_panel, expected_wide, tolerance=1e-9)


def print_header(idx: int, total: int, label: str) -> None:
    print(f"\n[{idx}/{total}] {label}")
    print("-" * 80)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the build_policy_futures_strip_panel "
            "primitive against an independent SQL reproduction."
        ),
    )
    parser.parse_args()

    print("=" * 80)
    print("BUILD_POLICY_FUTURES_STRIP_PANEL — SQL VALIDATION")
    print("=" * 80)
    print(f"  field_name      : {DEFAULT_FIELD}")
    print(f"  instrument_type : {INSTRUMENT_TYPE}")
    print("-" * 80)

    print("[1/3] Creating DB engine...")
    engine = get_db_engine()

    cases = REGRESSION_CASES
    total = len(cases)

    print(f"[2/3] Running {total} tool-vs-SQL comparisons...")
    failed: List[Tuple[str, List[str]]] = []
    for idx, case in enumerate(cases, start=1):
        label = case[0]
        print_header(idx, total, label)
        mismatches = run_case(engine, case=case)
        if mismatches:
            print("FAIL")
            for m in mismatches[:10]:
                print(f"  - {m}")
            if len(mismatches) > 10:
                print(f"  - ... plus {len(mismatches) - 10} more mismatches")
            failed.append((label, mismatches))
        else:
            print("PASS")

    print("\n[3/3] Summary")
    print("-" * 80)
    print(f"  total_cases : {total}")
    print(f"  passed      : {total - len(failed)}")
    print(f"  failed      : {len(failed)}")

    if failed:
        print("\nFAILED CASES:")
        for label, mismatches in failed:
            print(f"  - {label} ({len(mismatches)} mismatches)")
        sys.exit(1)


if __name__ == "__main__":
    main()
