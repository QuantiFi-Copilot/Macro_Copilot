"""
compute.py — Config-driven policy-futures simple-butterfly monitor (V1)
=======================================================================

Same-curve 3-point simple butterfly between THREE strip-position slots
on ONE ``curve_family``. Reads all three legs in a single
``fetch_strip_group`` round-trip, converts each to implied-rate space
per the per-strip ``inverse_priced`` flag, aligns on the intersection
of trading days, and emits a snapshot of:

  - current ``butterfly_value_pct`` in PERCENT POINTS
    (``rate_body − 0.5 * (rate_wing_short + rate_wing_long)``)
  - per-leg current implied rates (``implied_rate_pct_*``)
  - 1-day raw subtraction on the butterfly axis
  - rolling 252-day z-score on the BUTTERFLY series
  - trailing 252-day high / low / mid on the butterfly axis
  - percentile rank of the current butterfly in the trailing 252-day
    range
  - observation_count over the LLM-supplied lookback_days window
  - per-leg as_of-bounded SCD2 disclosure block
  - the methodology disclosure (P5 / ADR 0013 — sign convention, fixed
    50-50 simple-butterfly weighting, regime label, inverse-pricing
    rule, z window, trailing window, scope-limit refusals)

Sign convention (wire-frozen)
-----------------------------
``butterfly_value_pct = implied_rate_pct(body)
    − 0.5 * (implied_rate_pct(wing_short)
             + implied_rate_pct(wing_long))``

The input schema's validator enforces ``strip_position_wing_short <
strip_position_body < strip_position_wing_long`` so the sign
convention is deterministic.

Fixed 50-50 simple-butterfly weighting
--------------------------------------
The catalog's methodology guardrail requires the fixed simple-
butterfly weighting (body=1, wing_short=-0.5, wing_long=-0.5). The
weights are NOT exposed as inputs — they live in the YAML's
``butterfly_weighting`` convention as a DISCLOSURE block and as the
wire-frozen formula in this module. DV01-neutral / regression-fitted
variants are PR11 planned-extension territory and ship as separate
primitives in a future build.

Why ``fetch_strip_group`` (not three ``fetch_strip_position`` calls)
--------------------------------------------------------------------
The catalog names ``fetch_strip_group`` as the fetcher surface for
this primitive — one DB round-trip for all three legs. The function
returns the long-format ``(trade_date, strip_position, field_value)``
DataFrame the butterfly needs; we pivot on ``strip_position`` via
``pivot_and_align_tenors(key_col='strip_position')`` to align the
three legs on a wide ``(date × strip_position)`` frame and forward-
fill holiday gaps up to the YAML's ``ffill_limit_days``.

``fetch_strip_group`` does NOT carry an ``end_date`` parameter today;
to keep the deterministic-anchoring contract — explicit ``as_of_date``
⇒ snapshot anchored at that date — the post-fetch step filters rows
by ``trade_date <= as_of_date`` in pandas. Mirrors the sibling
``futures_calendar_spread`` exactly.

Per-leg → implied-rate conversion
---------------------------------
The implied-rate conversion per leg is the SAME rule the siblings
``futures_price_level`` / ``futures_calendar_spread`` use: for
``inverse_priced=True`` strips,
``implied_rate_pct = 100 - raw_price``; for direct-priced strips,
``implied_rate_pct = raw_price``. We duplicate the small inlined
arithmetic here rather than importing a private helper from the
siblings to keep the three primitives' compute paths independent
(the build rules forbid composing this primitive on top of the
price-level / calendar-spread tools' public output).

All three legs must agree on the ``inverse_pricing`` flag — same-
curve butterflies inherently share the flag, but we verify and refuse
with the controlled-error envelope if metadata drifts.

Methodology disclosure
----------------------
Every response carries a ``methodology_disclosure`` field with:
  - the sign convention (``body − 0.5 * (wing_short + wing_long)``),
  - the fixed 50-50 simple-butterfly weighting,
  - the per-``curve_family`` short-rate regime label (RFR vs IBOR),
  - the inverse-pricing rule in plain English,
  - the z-score lookback window,
  - the trailing-range window,
  - the strip-position keying,
  - the rolling-generic-strip-butterfly scope-limit (NOT a meeting-
    by-meeting policy-path decomposition; NOT a DV01-neutral /
    regression-fitted variant; NOT a CTD-of-futures-of-OIS curvature).
The schema makes the field REQUIRED.

Honest placeholder
------------------
``trailing_range_window_days`` is locked at 252 in V1. The output
schema's field names embed the number; the compute path raises
``NotImplementedError`` if this is set to anything else.

Deterministic anchoring (PR8 + PR16)
------------------------------------
- ``None`` ⇒ anchor at the universe's last observed ``trade_date``
  where ALL THREE legs have a value after intersection.
- explicit date BEYOND any leg's last ``trade_date`` ⇒ controlled-
  error envelope (``no scoreable strip: ...``).
- explicit date WITHIN the universe range ⇒ post-fetch filtered to
  ``trade_date <= as_of_date`` for determinism.

DB access
---------
Reaches the DB through the shared ``fetch_strip_group`` (all three
legs in one call), ``fetch_strip_position_reference`` (per-leg
metadata bounded by ``as_of_date``), and
``fetch_strip_position_max_date`` (per-leg future-anchor probe)
helpers. NO raw SQL in this file.

Test seam
---------
``fetch_strip_group``, ``fetch_strip_position_reference``,
``fetch_strip_position_max_date``, and ``date`` are imported here at
module level; tests patch them via
``patch("rates_agent.policy_futures.tools.futures_butterfly_simple"
".compute.X")``.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

import pandas as pd
from sqlalchemy.engine import Engine

from rates_agent.policy_futures.tools.futures_butterfly_simple.schemas import (
    FuturesButterflySimpleCurrentMetrics,
    FuturesButterflySimpleInput,
    FuturesButterflySimpleOutput,
    FuturesButterflySimpleTimeSeriesRow,
)
from shared.analytics.levels import trailing_high_low_percentile
from shared.analytics.rates_fetch import (
    fetch_strip_group,
    fetch_strip_position_max_date,
    fetch_strip_position_reference,
)
from shared.analytics.spreads import (
    pivot_and_align_tenors,
    rolling_zscore,
    safe_float,
)
from shared.config import ToolConfig, load_tool_config
from shared.schemas import TimeSeries, TimeSeriesRow, TimeSeriesUnits


# Bundled config — public symbol so external callers (mcp_server,
# tests, future REST routes) can build a ToolConfig from the same
# source the tool uses.
CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


# Trailing-range window is wire-frozen at 252 in V1.
_FROZEN_TRAILING_WINDOW: int = 252


# Fixed 50-50 simple-butterfly weights — wire-frozen in code as
# mathematical invariants per PR9 / PR10. The YAML's
# ``butterfly_weighting`` convention is a DISCLOSURE block (audit
# trail), NOT a knob. Changing those YAML values does NOT alter
# compute(); the formula here is the source of truth.
_BODY_WEIGHT: float = 1.0
_WING_WEIGHT: float = -0.5


def _build_methodology_disclosure(
    *,
    curve_family: str,
    strip_position_wing_short: int,
    strip_position_body: int,
    strip_position_wing_long: int,
    contract_code_wing_short: str,
    contract_code_body: str,
    contract_code_wing_long: str,
    inverse_priced: bool,
    short_rate_regime: str,
    z_window_days: int,
    trailing_window_days: int,
) -> str:
    """Compose the P5 / ADR 0013 disclosure string with runtime
    context so consumers see the exact regime + conversion rule +
    sign convention + weighting that produced the snapshot."""
    if inverse_priced:
        rule = (
            "Inverse-priced strip — per leg, "
            "implied_rate_pct = 100 - raw_price; the butterfly value "
            "on the implied-rate axis equals the body's implied rate "
            "minus the wing average."
        )
    else:
        rule = (
            "Direct-priced strip — per leg, "
            "implied_rate_pct = raw_price; the butterfly value equals "
            "the body's price minus the wing-price average."
        )
    return (
        f"Rolling-generic strip simple butterfly at {curve_family} "
        f"strip_position_wing_short={strip_position_wing_short} "
        f"(master stem={contract_code_wing_short}), "
        f"strip_position_body={strip_position_body} (master stem="
        f"{contract_code_body}), strip_position_wing_long="
        f"{strip_position_wing_long} (master stem="
        f"{contract_code_wing_long}). Sign convention: body minus "
        f"wing average (formula: body - 0.5 * (wing_short + "
        f"wing_long)). Weighting: FIXED 50-50 simple-butterfly "
        f"weights (body=1.0, wing_short=-0.5, wing_long=-0.5). The "
        f"desk-recognised quantity is the butterfly value in PERCENT "
        f"POINTS. Underlying short-rate regime: {short_rate_regime} "
        f"(RFR = compounded daily risk-free rate; IBOR = unsecured "
        f"3M term IBOR). {rule} Z-score lookback = {z_window_days} "
        f"trading days on the BUTTERFLY series; trailing range "
        f"window = {trailing_window_days} trading days. This is NOT "
        f"a CTD-of-futures-of-OIS butterfly; the CTD-implied-OIS "
        f"curve is not yet a primitive in this build. This is also "
        f"NOT a meeting-by-meeting policy-path decomposition. This "
        f"is also NOT a DV01-neutral or regression-fitted butterfly "
        f"— those weighting variants are planned-extension territory "
        f"and ship as separate primitives (ADR 0013 V1 scope — "
        f"policy_futures ships strip-position-keyed monitors only)."
    )


def _parse_regime_map(csv_value: str) -> Mapping[str, str]:
    """Parse the YAML's ``short_rate_regime_map`` CSV into a dict."""
    out: dict[str, str] = {}
    for chunk in csv_value.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "=" not in chunk:
            raise ValueError(
                f"short_rate_regime_map entry {chunk!r} is malformed; "
                f"expected 'CURVE_FAMILY=REGIME' (CSV-joined)."
            )
        key, value = chunk.split("=", 1)
        out[key.strip()] = value.strip()
    return out


def _check_trailing_window(config: ToolConfig) -> int:
    """Verify ``trailing_range_window_days`` is the wire-frozen value
    and return it. Raises ``NotImplementedError`` otherwise."""
    trailing = config.convention_value("trailing_range_window_days")
    if trailing != _FROZEN_TRAILING_WINDOW:
        raise NotImplementedError(
            f"trailing_range_window_days={trailing!r} is documented in "
            f"this tool's config.yaml as a future-supported value (see "
            f"methodology.planned_extensions) but is not yet "
            f"implemented. V1 supports only {_FROZEN_TRAILING_WINDOW} "
            f"because the output field names "
            f"(high_252d_butterfly_value_pct, "
            f"low_252d_butterfly_value_pct, "
            f"mid_252d_butterfly_value_pct, percentile_252d) embed "
            f"that number on the wire. Either restore the value to "
            f"{_FROZEN_TRAILING_WINDOW} or implement the schema "
            f"rename + frontend update documented in "
            f"planned_extensions."
        )
    return trailing


def _format_expiry(value: Any) -> Optional[str]:
    """Render the per-leg ``expiry_date`` value as a YYYY-MM-DD
    string if present; None pass-through."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    return str(value)


# ============================================================================
# PUBLIC API
# ============================================================================

def calculate_futures_butterfly_simple(
    engine: Engine,
    params: FuturesButterflySimpleInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    """Return current same-curve simple butterfly on the policy-
    futures strip plus 1-day delta, 252-day z-score on the butterfly
    series, trailing 252-day high/low/mid/percentile, observation_count,
    per-leg current implied rates, the per-leg as_of-bounded SCD2
    disclosure block, the canonical TimeSeries exports, and the P5
    methodology disclosure.

    Parameters
    ----------
    engine : Engine
        Live SQLAlchemy engine.
    params : FuturesButterflySimpleInput
        Validated input. ``field_name=None`` resolves against the
        YAML's ``default_price_field`` convention; ``as_of_date=None``
        anchors at the post-fetch data-max date where all three legs
        are observed.
    config : ToolConfig, optional
        Bundled config.yaml is auto-loaded when None.

    Returns
    -------
    dict
        Serialised ``FuturesButterflySimpleOutput``, or
        ``{"error": "..."}`` on recoverable failure.
    """
    if config is None:
        config = load_tool_config(CONFIG_PATH)

    # ------------------------------------------------------------------
    # Pull conventions
    # ------------------------------------------------------------------
    z_window = config.convention_value("z_score_window_days")
    z_min_periods = config.convention_value("z_score_min_periods")
    z_ddof = config.convention_value("z_score_ddof")
    buffer_mult = config.convention_value("z_score_buffer_multiplier")
    daily_offset_rows = config.convention_value("daily_change_offset_rows")
    ffill_limit = config.convention_value("ffill_limit_days")
    default_field_name = config.convention_value("default_price_field")
    trailing_window = _check_trailing_window(config)
    butterfly_round_decimals = config.convention_value(
        "butterfly_value_round_decimals"
    )
    implied_rate_round_decimals = config.convention_value(
        "implied_rate_round_decimals"
    )
    z_score_round_decimals = config.convention_value("z_score_round_decimals")
    butterfly_high_low_round_decimals = config.convention_value(
        "butterfly_value_high_low_round_decimals"
    )
    percentile_round_decimals = config.convention_value(
        "percentile_round_decimals"
    )
    regime_map = _parse_regime_map(
        config.convention_value("short_rate_regime_map")
    )

    short_rate_regime = regime_map.get(params.curve_family)
    if short_rate_regime is None:
        return {
            "error": (
                f"Policy-futures curve_family={params.curve_family!r} "
                f"has no regime label in the YAML's "
                "``short_rate_regime_map`` convention. Add the entry "
                "(e.g. 'NEW_FAMILY=RFR') alongside the universe "
                "expansion in policy_futures.yml so the methodology "
                "disclosure remains honest (P5)."
            )
        }

    field_name_resolved = (
        params.field_name if params.field_name is not None else default_field_name
    )

    # ------------------------------------------------------------------
    # 1. Future-anchor guard (PR8 + PR16) — per leg
    # ------------------------------------------------------------------
    requested_as_of: Optional[date] = params.as_of_date
    if requested_as_of is not None:
        for leg_label, leg_position in (
            ("wing_short", params.strip_position_wing_short),
            ("body", params.strip_position_body),
            ("wing_long", params.strip_position_wing_long),
        ):
            leg_max = fetch_strip_position_max_date(
                engine=engine,
                curve_family=params.curve_family,
                strip_position=leg_position,
                field_name=field_name_resolved,
            )
            if leg_max is not None and requested_as_of > leg_max:
                return {
                    "error": (
                        f"no scoreable strip: as_of_date="
                        f"{requested_as_of.isoformat()} is beyond the "
                        f"policy-futures universe's last observed "
                        f"trade_date={leg_max.isoformat()} for "
                        f"{params.curve_family} {leg_label}_leg "
                        f"strip_position={leg_position}. The snapshot "
                        "refuses to silently re-label an unbounded "
                        "read as a future-anchored read."
                    )
                }

    # ------------------------------------------------------------------
    # 2. Date window
    # ------------------------------------------------------------------
    buffer_calendar_days = int(z_window * buffer_mult)
    fetch_anchor: Optional[date] = requested_as_of
    if fetch_anchor is None:
        fetch_anchor = date.today()
    start_date = fetch_anchor - timedelta(
        days=params.lookback_days + buffer_calendar_days
    )

    # ------------------------------------------------------------------
    # 3. Per-leg reference metadata (SCD2 as-of lookup)
    # ------------------------------------------------------------------
    references: dict[str, Optional[dict]] = {}
    for leg_label, leg_position in (
        ("wing_short", params.strip_position_wing_short),
        ("body", params.strip_position_body),
        ("wing_long", params.strip_position_wing_long),
    ):
        reference = fetch_strip_position_reference(
            engine=engine,
            curve_family=params.curve_family,
            strip_position=leg_position,
            as_of_date=fetch_anchor,
        )
        if reference is None:
            return {
                "error": (
                    f"No policy-futures strip slot found for "
                    f"curve_family='{params.curve_family}', "
                    f"strip_position_{leg_label}={leg_position}. "
                    "Verify the (curve_family, strip_position) pair "
                    "exists on instrument_master as a strip-position-"
                    "keyed rolling contract (policy_futures.yml "
                    "universe)."
                )
            }
        references[leg_label] = reference

    inverse_flags: dict[str, Optional[bool]] = {}
    for leg_label in ("wing_short", "body", "wing_long"):
        raw_flag = references[leg_label].get("inverse_pricing")
        if raw_flag is None:
            return {
                "error": (
                    f"Policy-futures strip {params.curve_family} "
                    f"{leg_label}_leg (strip_position="
                    f"{references[leg_label].get('strip_position')}) "
                    "is missing the 'inverse_pricing' flag on "
                    "instrument_master.attributes. The implied-rate "
                    "conversion rule is metadata-driven (PR8 / P6 — "
                    "no hidden methodology in code); a strip without "
                    "this flag cannot be priced honestly. Surface "
                    "this as a metadata gap to the playbook owner."
                )
            }
        inverse_flags[leg_label] = bool(raw_flag)

    distinct_flags = set(inverse_flags.values())
    if len(distinct_flags) > 1:
        return {
            "error": (
                f"Policy-futures legs for {params.curve_family} "
                f"disagree on the 'inverse_pricing' flag "
                f"(wing_short={inverse_flags['wing_short']!r}, "
                f"body={inverse_flags['body']!r}, "
                f"wing_long={inverse_flags['wing_long']!r}). A same-"
                "curve simple butterfly requires all three legs to "
                "use the same price-to-rate convention; refusing to "
                "produce a mixed-convention snapshot (P5)."
            )
        }
    inverse_priced: bool = next(iter(distinct_flags))

    # ------------------------------------------------------------------
    # 4. Fetch all three legs in one DB round-trip
    # ------------------------------------------------------------------
    raw_df = fetch_strip_group(
        engine=engine,
        curve_family=params.curve_family,
        strip_positions=[
            params.strip_position_wing_short,
            params.strip_position_body,
            params.strip_position_wing_long,
        ],
        field_name=field_name_resolved,
        start_date=start_date,
    )
    if raw_df.empty:
        return {
            "error": (
                f"No policy-futures price data found for "
                f"curve_family='{params.curve_family}', "
                f"strip_positions=("
                f"{params.strip_position_wing_short},"
                f"{params.strip_position_body},"
                f"{params.strip_position_wing_long}), "
                f"field='{field_name_resolved}' since "
                f"{start_date.isoformat()}. Please verify all three "
                "strip slots have ingested PX_LAST observations."
            )
        }

    if requested_as_of is not None:
        raw_df = raw_df[
            pd.to_datetime(raw_df["trade_date"])
            <= pd.Timestamp(requested_as_of)
        ]
        if raw_df.empty:
            return {
                "error": (
                    f"No policy-futures price data found for "
                    f"curve_family='{params.curve_family}', "
                    f"strip_positions=("
                    f"{params.strip_position_wing_short},"
                    f"{params.strip_position_body},"
                    f"{params.strip_position_wing_long}), "
                    f"field='{field_name_resolved}' on or before "
                    f"as_of_date={requested_as_of.isoformat()}."
                )
            }

    available_positions = set(int(p) for p in raw_df["strip_position"].unique())
    required_positions = {
        params.strip_position_wing_short,
        params.strip_position_body,
        params.strip_position_wing_long,
    }
    missing_positions = required_positions - available_positions
    if missing_positions:
        return {
            "error": (
                f"Missing strip_position data for "
                f"{sorted(missing_positions)} in "
                f"curve_family='{params.curve_family}'. Available "
                f"strip_positions in the query window: "
                f"{sorted(available_positions)}."
            )
        }

    # ------------------------------------------------------------------
    # 5. Pivot → wide format (date × strip_position), align, drop rows
    #    missing any leg.
    # ------------------------------------------------------------------
    wide = pivot_and_align_tenors(
        raw_df,
        required_tenors=(
            params.strip_position_wing_short,
            params.strip_position_body,
            params.strip_position_wing_long,
        ),
        key_col="strip_position",
        ffill_limit=ffill_limit,
    )
    if wide.empty:
        return {
            "error": (
                f"After aligning dates for strip_positions "
                f"({params.strip_position_wing_short},"
                f"{params.strip_position_body},"
                f"{params.strip_position_wing_long}) on "
                f"'{params.curve_family}', no overlapping observations "
                "remain."
            )
        }

    # ------------------------------------------------------------------
    # 6. Per-leg implied-rate series — driven off metadata flag
    # ------------------------------------------------------------------
    raw_wing_short = wide[params.strip_position_wing_short]
    raw_body = wide[params.strip_position_body]
    raw_wing_long = wide[params.strip_position_wing_long]
    if inverse_priced:
        implied_wing_short = 100.0 - raw_wing_short
        implied_body = 100.0 - raw_body
        implied_wing_long = 100.0 - raw_wing_long
    else:
        implied_wing_short = raw_wing_short.copy()
        implied_body = raw_body.copy()
        implied_wing_long = raw_wing_long.copy()

    # ------------------------------------------------------------------
    # 7. Compute butterfly series — fixed 50-50 simple-butterfly
    #    weighting: body − 0.5 * (wing_short + wing_long).
    # ------------------------------------------------------------------
    butterfly_series = (
        _BODY_WEIGHT * implied_body
        + _WING_WEIGHT * implied_wing_short
        + _WING_WEIGHT * implied_wing_long
    )

    # ------------------------------------------------------------------
    # 8. Z-score on the butterfly series
    # ------------------------------------------------------------------
    z_series = rolling_zscore(
        butterfly_series,
        window=z_window,
        min_periods=z_min_periods,
        ddof=z_ddof,
        round_decimals=z_score_round_decimals,
    )
    z_score = safe_float(z_series.iloc[-1], decimals=z_score_round_decimals)

    # ------------------------------------------------------------------
    # 9. 1-day raw subtraction on the butterfly axis
    # ------------------------------------------------------------------
    daily_change_butterfly_value_pct: Optional[float]
    if len(butterfly_series) >= daily_offset_rows:
        cur_val = butterfly_series.iloc[-1]
        prev_val = butterfly_series.iloc[-daily_offset_rows]
        if pd.isna(cur_val) or pd.isna(prev_val):
            daily_change_butterfly_value_pct = None
        else:
            daily_change_butterfly_value_pct = round(
                float(cur_val) - float(prev_val),
                butterfly_round_decimals,
            )
    else:
        daily_change_butterfly_value_pct = None

    # ------------------------------------------------------------------
    # 10. Trailing high/low/mid + percentile on the butterfly axis
    # ------------------------------------------------------------------
    bfly_high, bfly_low, percentile = trailing_high_low_percentile(
        butterfly_series,
        window=trailing_window,
        decimals=butterfly_high_low_round_decimals,
    )
    percentile_rounded: Optional[float]
    if percentile is None:
        percentile_rounded = None
    else:
        percentile_rounded = round(
            float(percentile), percentile_round_decimals
        )
    bfly_mid: Optional[float]
    if bfly_high is None or bfly_low is None:
        bfly_mid = None
    else:
        bfly_mid = round(
            (float(bfly_high) + float(bfly_low)) / 2.0,
            butterfly_high_low_round_decimals,
        )

    # ------------------------------------------------------------------
    # 11. Current values + observation_count
    # ------------------------------------------------------------------
    current_butterfly = safe_float(
        butterfly_series.iloc[-1],
        decimals=butterfly_round_decimals,
    )
    current_wing_short = safe_float(
        implied_wing_short.iloc[-1],
        decimals=implied_rate_round_decimals,
    )
    current_body = safe_float(
        implied_body.iloc[-1], decimals=implied_rate_round_decimals,
    )
    current_wing_long = safe_float(
        implied_wing_long.iloc[-1],
        decimals=implied_rate_round_decimals,
    )

    as_of_date_resolved = butterfly_series.index[-1].date()
    cutoff = pd.Timestamp(
        as_of_date_resolved - timedelta(days=params.lookback_days)
    )
    display_butterfly = butterfly_series.loc[
        butterfly_series.index >= cutoff
    ]
    display_zscore = z_series.loc[z_series.index >= cutoff]
    obs_count = len(display_butterfly.dropna())
    if obs_count == 0:
        return {
            "error": (
                f"No observations within the last {params.lookback_days} "
                f"days for {params.curve_family} "
                f"strip_positions=("
                f"{params.strip_position_wing_short},"
                f"{params.strip_position_body},"
                f"{params.strip_position_wing_long})."
            )
        }

    # ------------------------------------------------------------------
    # 12. Build the snapshot
    # ------------------------------------------------------------------
    contract_code_wing_short = references["wing_short"]["contract_code"]
    contract_code_body = references["body"]["contract_code"]
    contract_code_wing_long = references["wing_long"]["contract_code"]
    butterfly_label = (
        f"{contract_code_wing_short}-{contract_code_body}-"
        f"{contract_code_wing_long}"
    )

    methodology_disclosure = _build_methodology_disclosure(
        curve_family=params.curve_family,
        strip_position_wing_short=params.strip_position_wing_short,
        strip_position_body=params.strip_position_body,
        strip_position_wing_long=params.strip_position_wing_long,
        contract_code_wing_short=contract_code_wing_short,
        contract_code_body=contract_code_body,
        contract_code_wing_long=contract_code_wing_long,
        inverse_priced=inverse_priced,
        short_rate_regime=short_rate_regime,
        z_window_days=z_window,
        trailing_window_days=trailing_window,
    )

    metrics = FuturesButterflySimpleCurrentMetrics(
        as_of_date=as_of_date_resolved.strftime("%Y-%m-%d"),
        curve_family=params.curve_family,
        strip_position_wing_short=params.strip_position_wing_short,
        strip_position_body=params.strip_position_body,
        strip_position_wing_long=params.strip_position_wing_long,
        butterfly_label=butterfly_label,
        contract_code_wing_short=contract_code_wing_short,
        contract_code_body=contract_code_body,
        contract_code_wing_long=contract_code_wing_long,
        underlying_contract_code_wing_short=references["wing_short"].get(
            "underlying_contract_code"
        ),
        underlying_contract_code_body=references["body"].get(
            "underlying_contract_code"
        ),
        underlying_contract_code_wing_long=references["wing_long"].get(
            "underlying_contract_code"
        ),
        security_name_wing_short=references["wing_short"].get("security_name"),
        security_name_body=references["body"].get("security_name"),
        security_name_wing_long=references["wing_long"].get("security_name"),
        expiry_date_wing_short=_format_expiry(
            references["wing_short"].get("expiry_date")
        ),
        expiry_date_body=_format_expiry(
            references["body"].get("expiry_date")
        ),
        expiry_date_wing_long=_format_expiry(
            references["wing_long"].get("expiry_date")
        ),
        inverse_priced=inverse_priced,
        short_rate_regime=short_rate_regime,
        implied_rate_pct_wing_short=current_wing_short,
        implied_rate_pct_body=current_body,
        implied_rate_pct_wing_long=current_wing_long,
        butterfly_value_pct=current_butterfly,
        daily_change_butterfly_value_pct=daily_change_butterfly_value_pct,
        z_score_butterfly=z_score,
        high_252d_butterfly_value_pct=bfly_high,
        low_252d_butterfly_value_pct=bfly_low,
        mid_252d_butterfly_value_pct=bfly_mid,
        percentile_252d=percentile_rounded,
        rolling_window_days=z_window,
        observation_count=obs_count,
    )

    # ------------------------------------------------------------------
    # 13. Bespoke + canonical TimeSeries — built from the SAME display
    #     slices so they cannot drift.
    # ------------------------------------------------------------------
    bespoke_rows = _build_bespoke_time_series(
        display_butterfly=display_butterfly,
        display_zscore=display_zscore,
        butterfly_round_decimals=butterfly_round_decimals,
        z_score_round_decimals=z_score_round_decimals,
    )
    canonical_butterfly = _build_canonical_butterfly(
        display_butterfly=display_butterfly,
        curve_family=params.curve_family,
        strip_position_wing_short=params.strip_position_wing_short,
        strip_position_body=params.strip_position_body,
        strip_position_wing_long=params.strip_position_wing_long,
        butterfly_round_decimals=butterfly_round_decimals,
    )
    canonical_zscore = _build_canonical_zscore(
        display_zscore=display_zscore,
        curve_family=params.curve_family,
        strip_position_wing_short=params.strip_position_wing_short,
        strip_position_body=params.strip_position_body,
        strip_position_wing_long=params.strip_position_wing_long,
        z_score_round_decimals=z_score_round_decimals,
    )

    output = FuturesButterflySimpleOutput(
        current_metrics=metrics,
        time_series=bespoke_rows,
        time_series_butterfly=canonical_butterfly,
        time_series_zscore=canonical_zscore,
        methodology_disclosure=methodology_disclosure,
    )
    return output.model_dump()


# ============================================================================
# TIME-SERIES BUILDERS
# ============================================================================

def _build_bespoke_time_series(
    *,
    display_butterfly: pd.Series,
    display_zscore: pd.Series,
    butterfly_round_decimals: int,
    z_score_round_decimals: int,
) -> list[FuturesButterflySimpleTimeSeriesRow]:
    """Build the bespoke ``{date, butterfly_value_pct, z_score}`` row
    list. Skips rows where the butterfly value is NaN; z_score can
    legitimately be None during the rolling-window warmup."""
    rows: list[FuturesButterflySimpleTimeSeriesRow] = []
    for ts, val in display_butterfly.items():
        if pd.isna(val):
            continue
        try:
            z_val = display_zscore.loc[ts]
        except KeyError:
            z_val = None
        z_rounded: Optional[float]
        if z_val is None or pd.isna(z_val):
            z_rounded = None
        else:
            z_rounded = round(float(z_val), z_score_round_decimals)
        rows.append(
            FuturesButterflySimpleTimeSeriesRow(
                date=ts.strftime("%Y-%m-%d"),
                butterfly_value_pct=round(
                    float(val), butterfly_round_decimals,
                ),
                z_score=z_rounded,
            )
        )
    return rows


def _build_canonical_butterfly(
    *,
    display_butterfly: pd.Series,
    curve_family: str,
    strip_position_wing_short: int,
    strip_position_body: int,
    strip_position_wing_long: int,
    butterfly_round_decimals: int,
) -> TimeSeries:
    """Build the canonical ``TimeSeries`` for the butterfly value
    series (closed-enum ``TimeSeriesUnits.PERCENT``)."""
    series_name = (
        f"{curve_family.lower()}_"
        f"{strip_position_wing_short}_{strip_position_body}_"
        f"{strip_position_wing_long}_butterfly"
    )
    rows = []
    for ts, val in display_butterfly.items():
        if pd.isna(val):
            continue
        rows.append(
            TimeSeriesRow(
                date=ts.strftime("%Y-%m-%d"),
                value=round(float(val), butterfly_round_decimals),
            )
        )
    return TimeSeries(
        series_name=series_name,
        units=TimeSeriesUnits.PERCENT,
        description=(
            f"Simple butterfly value (body − 0.5 * (wing_short + "
            f"wing_long)) on {curve_family} strip positions "
            f"({strip_position_wing_short}, {strip_position_body}, "
            f"{strip_position_wing_long}) over the displayed window, "
            f"in PERCENT POINTS."
        ),
        rows=rows,
    )


def _build_canonical_zscore(
    *,
    display_zscore: pd.Series,
    curve_family: str,
    strip_position_wing_short: int,
    strip_position_body: int,
    strip_position_wing_long: int,
    z_score_round_decimals: int,
) -> TimeSeries:
    """Build the canonical ``TimeSeries`` for the rolling z-score
    series (closed-enum ``TimeSeriesUnits.Z_SCORE``). None values
    during the warmup window are preserved."""
    series_name = (
        f"{curve_family.lower()}_"
        f"{strip_position_wing_short}_{strip_position_body}_"
        f"{strip_position_wing_long}_zscore"
    )
    rows = []
    for ts, z_val in display_zscore.items():
        rows.append(
            TimeSeriesRow(
                date=ts.strftime("%Y-%m-%d"),
                value=safe_float(z_val, decimals=z_score_round_decimals),
            )
        )
    return TimeSeries(
        series_name=series_name,
        units=TimeSeriesUnits.Z_SCORE,
        description=(
            f"Rolling z-score of the simple butterfly on "
            f"{curve_family} strip positions "
            f"({strip_position_wing_short}, {strip_position_body}, "
            f"{strip_position_wing_long}) vs its own trailing window."
        ),
        rows=rows,
    )
