"""
compute.py — Config-driven same-tenor cross-market ZCIS spread
================================================================

Fourth tool in the ``inflation_swaps`` domain.  Owns the desk
concept of a same-tenor cross-market zero-coupon inflation swap
(ZCIS) spread between two ZCIS curve families at the same pillar
(e.g. USD_ZCIS 5Y minus EUR_ZCIS 5Y, USD_ZCIS 10Y minus
GBP_ZCIS 10Y, EUR_ZCIS 5Y minus GBP_ZCIS 5Y).  Computed per-trade-
date via the SPREAD formula:

    spread_pct = leg_a_pct - leg_b_pct
    spread_bps = spread_pct * 100

where ``leg_a_pct`` and ``leg_b_pct`` are the ZCIS rate levels at
``tenor`` on ``leg_a_curve_family`` and ``leg_b_curve_family``
respectively (each computed by
``calculate_inflation_swap_rate_level``).

Concept honesty
---------------
USD_ZCIS, EUR_ZCIS, and GBP_ZCIS reference DIFFERENT inflation
indices (US CPI-U / Eurozone HICP-xT / UK RPI), so this spread
captures BOTH inflation-expectation differentials AND structural
index-family differences; it is NOT a clean expected-inflation
divergence.  The honesty disclosure lives in the YAML's
``methodology.what_it_does`` and is threaded onto the wire via
``current_metrics.methodology_label``.  That field is sourced from
the YAML at runtime — NOT hardcoded as a Python literal — so a
YAML edit flows through to runtime behaviour.

Per-leg reference metadata
--------------------------
The cross-market spread is a directional object across two
DIFFERENT curves.  Unlike ``inflation_swap_curve_spread``, there
is no same-curve invariant to share metadata across legs — the
two legs are EXPECTED to have different
``inflation_index_family`` / ``index_lag`` / ``interpolation`` /
``underlying_index`` values.  ``current_metrics`` therefore
surfaces the per-leg metadata on separate fields
(``leg_a_inflation_index_family`` / ``leg_b_inflation_index_family``
/ ``leg_a_index_lag`` / ``leg_b_index_lag`` / ...) so the desk
reader can decompose the spread.

Composition path (deliberately simple)
--------------------------------------
This primitive composes two calls to
``calculate_inflation_swap_rate_level`` (one per leg) into the
per-trade-date difference.  That gets the load-bearing
four-conjunct SELECT guard "for free" on each leg:

  - ``instrument_type='inflation_swap'`` AND
    ``pricing_type='zero_coupon_breakeven'`` AND
    ``curve_family=<user>`` AND ``tenor=<user>``.

The level primitive's compute() is invoked with ``config=`` set to
THIS primitive's ToolConfig.  Every shared convention name is
present in both YAMLs with the same numeric value (the cross-
config lint enforces that), so the inner compute reads exactly the
same window / min_periods / ddof / buffer / ffill / rounding values
it would read from its own bundled config.  Tests that override
conventions on the cross-market primitive's ToolConfig
automatically see those overrides flow through to the inner level
calls.

NO raw market-data SELECTs in this primitive's compute path
-----------------------------------------------------------
The four-conjunct SELECT guard
(instrument_type + pricing_type + curve_family + tenor) lives
inside ``calculate_inflation_swap_rate_level``.  This primitive
inherits the guard transitively by composing the level call twice;
it MUST NOT issue any of its own SELECTs against
``macro_data.v_market_data_daily_enriched`` or
``macro_data.instrument_master``, because doing so would let a
future refactor silently bypass the guard.  The SQL validator
verifies this transitively (probe 4: ``four_conjunct_guard``).

Why an extended inner lookback
------------------------------
Each inner level call trims its time_series to the requested
``lookback_days`` worth of display history (anchored to the
latest observation date).  Our rolling z-score / trailing range /
period changes need a longer history so they're fully populated
from the first row of OUR display window.  We therefore pass
``lookback_days = params.lookback_days + buffer_calendar_days`` to
each inner level call, then align + trim ourselves.  The buffer
math matches the level primitive's own buffer math
(``max(z_window, trailing_window) * buffer_multiplier``) so this
primitive does not double-buffer.

Per-trade-date alignment
------------------------
The two endpoint ZCIS series are inner-joined on date so we only
emit rows where BOTH legs had data.  No ffill across the join:
dates where one leg has no data after the level primitive's own
ffill window are dropped from the spread series, NOT carried
forward as synthetic spread points.  The level primitive's clean
step ffills within ``ffill_limit_days``; the cross-market spread
layer never adds its own ffill across the inner-join.

Sign convention
---------------
``spread = leg_a_pct - leg_b_pct`` (left minus right).  YAML's
``cross_market_sign_convention`` documents the choice; compute
reads it and refuses to run if it is ever set to anything other
than ``leg_a_minus_leg_b`` (V1 wire freeze) — the wire-disclosed
``methodology_label`` and the SQL validator must move in lockstep
with this convention so the desk user can never see a label that
disagrees with the math.

Trailing-range wire freeze
--------------------------
``trailing_range_window_days`` is locked at 252 in V1.  The output
schema's field names (``high_252d_bps``, ``low_252d_bps``,
``percentile_252d``) embed the number; changing the convention
without renaming the wire fields would silently lie about what the
percentile is computed against.  ``compute()`` raises
``NotImplementedError`` if this is set to anything else; see
``methodology.planned_extensions`` for the path to making it
configurable.

Test seam
---------
``calculate_inflation_swap_rate_level`` is imported here at module
level; tests patch the inner primitive's own seams
(``rates_agent.inflation_swaps.tools.inflation_swap_rate_level.compute.fetch_zcis_single_pillar``,
``...compute.date``) so the inner endpoint calls hit the test
fixtures.  This module does NOT import ``date`` directly — the
fetch start-date logic lives entirely inside the level primitive,
which already exposes its own date seam.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
from sqlalchemy.engine import Engine

from rates_agent.inflation_swaps.tools.cross_market_inflation_swap_spread.schemas import (
    CrossMarketInflationSwapSpreadCurrentMetrics,
    CrossMarketInflationSwapSpreadInput,
    CrossMarketInflationSwapSpreadOutput,
    CrossMarketInflationSwapSpreadTimeSeriesRow,
)
from rates_agent.inflation_swaps.tools.inflation_swap_rate_level import (
    InflationSwapRateLevelInput,
    calculate_inflation_swap_rate_level,
)
from shared.analytics.curve_bootstrap import tenor_to_years
from shared.analytics.levels import (
    period_changes,
    trailing_high_low_percentile,
)
from shared.analytics.spreads import (
    rolling_zscore,
    safe_float,
)
from shared.config import ToolConfig, load_tool_config
from shared.schemas import TimeSeries, TimeSeriesRow, TimeSeriesUnits


# Bundled config — public symbol so external callers (mcp_server,
# REST routes, tests, future batch surfaces) can build a ToolConfig
# from the same source the tool uses.
CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


# Trailing-range window is wire-frozen at 252 in V1; see schemas.py
# and config.yaml's ``planned_extensions``.
_FROZEN_TRAILING_WINDOW: int = 252


# Sign convention is wire-frozen at ``leg_a_minus_leg_b`` in V1.
# See config.yaml's ``methodology.planned_extensions`` for the path
# to a configurable sign convention (currently no plan to expose it
# as a knob).
_SUPPORTED_SIGN_CONVENTION: str = "leg_a_minus_leg_b"


# ============================================================================
# CONFIG → KWARGS RESOLVER
# ============================================================================

def _conventions_from_config(config: ToolConfig) -> dict:
    """Pull the methodology kwargs the
    cross_market_inflation_swap_spread compute() needs from a
    ToolConfig.

    Raises NotImplementedError if either ``trailing_range_window_days``
    or ``cross_market_sign_convention`` is set to a value the V1 wire
    surface cannot honestly represent.  See the module docstring for
    the wire-freeze rationale on each.
    """
    trailing = config.convention_value("trailing_range_window_days")
    if trailing != _FROZEN_TRAILING_WINDOW:
        raise NotImplementedError(
            f"trailing_range_window_days={trailing!r} is documented in this "
            f"tool's config.yaml as a future-supported value (see "
            f"methodology.planned_extensions) but is not yet implemented.  "
            f"V1 supports only {_FROZEN_TRAILING_WINDOW} because the output "
            f"field names (high_252d_bps, low_252d_bps, percentile_252d) "
            f"embed that number on the wire.  Either restore the value to "
            f"{_FROZEN_TRAILING_WINDOW} or implement the schema rename + "
            f"frontend update documented in planned_extensions."
        )

    sign = config.convention_value("cross_market_sign_convention")
    if sign != _SUPPORTED_SIGN_CONVENTION:
        raise NotImplementedError(
            f"cross_market_sign_convention={sign!r} is not supported in "
            f"V1.  The only supported value is "
            f"{_SUPPORTED_SIGN_CONVENTION!r}.  Alternative sign "
            f"conventions (e.g. dv01_weighted, duration_weighted) are "
            f"listed in this tool's methodology.planned_extensions in "
            f"config.yaml; do NOT override the convention until that V2 "
            f"work lands.  Supporting a second value requires updating "
            f"the wire-disclosed methodology_label and the SQL "
            f"validator at the same time so a desk user can never see "
            f"a label that disagrees with the math."
        )

    return {
        "z_window": config.convention_value("z_score_window_days"),
        "z_min_periods": config.convention_value("z_score_min_periods"),
        "z_ddof": config.convention_value("z_score_ddof"),
        "z_round_decimals": config.convention_value("z_score_round_decimals"),
        "buffer_multiplier": config.convention_value("z_score_buffer_multiplier"),
        "ffill_limit": config.convention_value("ffill_limit_days"),
        "period_offsets": {
            "daily": config.convention_value("daily_change_offset_rows"),
            "weekly": config.convention_value("weekly_change_offset_rows"),
            "monthly": config.convention_value("monthly_change_offset_rows"),
        },
        "trailing_window": trailing,
        "bps_round_decimals": config.convention_value("bps_round_decimals"),
        "pct_round_decimals": config.convention_value("pct_round_decimals"),
        "yield_round_decimals": config.convention_value("yield_round_decimals"),
        "window_years_round": config.convention_value(
            "window_years_round_decimals",
        ),
        "sign_convention": sign,
    }


# ============================================================================
# PUBLIC API
# ============================================================================

def calculate_cross_market_inflation_swap_spread(
    engine: Engine,
    params: CrossMarketInflationSwapSpreadInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    """Calculate the same-tenor cross-market ZCIS spread between
    two ZCIS curve families at the same pillar.

        spread_pct = leg_a_pct - leg_b_pct
        spread_bps = spread_pct * 100

    Parameters
    ----------
    engine : Engine
        Live SQLAlchemy engine connected to TimescaleDB.
    params : CrossMarketInflationSwapSpreadInput
        Validated input.  ``leg_a_curve_family`` !=
        ``leg_b_curve_family`` (cross-market invariant) and ``tenor``
        is parseable (validated at the schema layer).
    config : ToolConfig, optional
        Bundled config.yaml is auto-loaded when None.  Tests pass
        a custom ToolConfig to exercise convention overrides;
        production wrappers must pass ``config=`` explicitly.

    Returns
    -------
    dict
        Serialised ``CrossMarketInflationSwapSpreadOutput``, or
        ``{"error": "..."}`` on recoverable failure.
    """
    if config is None:
        config = load_tool_config(CONFIG_PATH)

    # ------------------------------------------------------------------
    # Pull conventions
    # ------------------------------------------------------------------
    conv = _conventions_from_config(config)
    z_window = conv["z_window"]
    z_min_periods = conv["z_min_periods"]
    z_ddof = conv["z_ddof"]
    z_round_decimals = conv["z_round_decimals"]
    buffer_multiplier = conv["buffer_multiplier"]
    period_offsets = conv["period_offsets"]
    trailing_window = conv["trailing_window"]
    bps_round_decimals = conv["bps_round_decimals"]
    pct_round_decimals = conv["pct_round_decimals"]
    yield_round_decimals = conv["yield_round_decimals"]
    window_years_round = conv["window_years_round"]

    # Resolve field_name: caller's explicit value wins; None falls
    # through to the YAML default (``default_zcis_rate_field``).  The
    # resolved value is threaded into BOTH inner level calls so the
    # two legs are read off the same Bloomberg field by construction.
    default_field_name = config.convention_value("default_zcis_rate_field")
    field_name_resolved = (
        params.field_name if params.field_name is not None else default_field_name
    )

    # Wire-honesty disclosure threaded from YAML — NOT hardcoded.
    methodology_label = config.methodology.what_it_does.strip()

    # ------------------------------------------------------------------
    # Tenor year fraction for display.  The Pydantic validator
    # already enforced parsability; re-running the parser here is
    # cheap and keeps compute() self-contained.
    # ------------------------------------------------------------------
    tenor_years = tenor_to_years(params.tenor)

    # ------------------------------------------------------------------
    # Extended inner lookback.  Each inner level call trims its
    # time_series to ``lookback_days`` worth of display history.  We
    # need a longer window so OUR rolling stats are fully populated
    # from the first row of OUR display window; match the buffer
    # math the level primitive uses internally so we don't double-
    # buffer.
    # ------------------------------------------------------------------
    buffer_calendar_days = int(
        max(z_window, trailing_window) * buffer_multiplier
    )
    extended_lookback_days = params.lookback_days + buffer_calendar_days

    # ------------------------------------------------------------------
    # Compose: call the ZCIS rate-level primitive once per leg.  This
    # inherits the four-conjunct SELECT guard
    # (instrument_type + pricing_type + curve_family + tenor) for
    # free.  We pass ``config=`` so the inner conventions use the
    # same values this primitive uses; cross-config lint enforces
    # value-agreement on the bundled YAMLs, and any test override
    # on this primitive's ToolConfig flows through to the inner
    # level calls.
    #
    # ``field_name_resolved`` is threaded into BOTH inner level
    # calls so the two legs are read off the same Bloomberg field.
    # The level primitive itself accepts ``field_name`` and falls
    # through to ``default_zcis_rate_field`` on None; we resolve the
    # sentinel ONCE here so a per-call override stays consistent
    # across legs.
    # ------------------------------------------------------------------
    leg_a_inner = calculate_inflation_swap_rate_level(
        engine=engine,
        params=InflationSwapRateLevelInput(
            curve_family=params.leg_a_curve_family,
            tenor=params.tenor,
            lookback_days=extended_lookback_days,
            field_name=field_name_resolved,
            as_of_date=params.as_of_date,
        ),
        config=config,
    )
    if "error" in leg_a_inner:
        return {
            "error": (
                f"Cross-market ZCIS spread "
                f"{params.leg_a_curve_family}-"
                f"{params.leg_b_curve_family} {params.tenor} could "
                f"not be computed: the leg_a "
                f"({params.leg_a_curve_family}) endpoint failed.  "
                f"Inner error: {leg_a_inner['error']}"
            )
        }

    leg_b_inner = calculate_inflation_swap_rate_level(
        engine=engine,
        params=InflationSwapRateLevelInput(
            curve_family=params.leg_b_curve_family,
            tenor=params.tenor,
            lookback_days=extended_lookback_days,
            field_name=field_name_resolved,
            as_of_date=params.as_of_date,
        ),
        config=config,
    )
    if "error" in leg_b_inner:
        return {
            "error": (
                f"Cross-market ZCIS spread "
                f"{params.leg_a_curve_family}-"
                f"{params.leg_b_curve_family} {params.tenor} could "
                f"not be computed: the leg_b "
                f"({params.leg_b_curve_family}) endpoint failed.  "
                f"Inner error: {leg_b_inner['error']}"
            )
        }

    # ------------------------------------------------------------------
    # Per-leg reference metadata.  Cross-market spreads are EXPECTED
    # to have different inflation_index_family / index_lag /
    # interpolation across legs — that is the whole point of the
    # index-family caveat.  We surface BOTH legs' metadata
    # separately on current_metrics so the desk reader can decompose
    # the spread.  No equality assertion here (unlike the same-
    # curve siblings).
    # ------------------------------------------------------------------
    leg_a_metrics_inner = leg_a_inner.get("current_metrics", {})
    leg_b_metrics_inner = leg_b_inner.get("current_metrics", {})

    # ------------------------------------------------------------------
    # Align endpoint ZCIS series by date.  The level primitive's
    # canonical TimeSeries already has each row pre-rounded to
    # ``yield_round_decimals`` from the inner compute; we use those
    # rows directly so the canonical-shape contract is end-to-end.
    # Strict pandas inner-join on date so we only emit rows where
    # BOTH legs had data — no synthetic spread points are produced
    # on dates where one leg has no data.  The level primitive's
    # clean step has already applied ffill within
    # ``ffill_limit_days`` per leg; the cross-market spread layer
    # never adds its own ffill across the inner-join.
    # ------------------------------------------------------------------
    leg_a_rows = leg_a_inner.get("time_series", {}).get("rows", [])
    leg_b_rows = leg_b_inner.get("time_series", {}).get("rows", [])
    if not leg_a_rows or not leg_b_rows:
        return {
            "error": (
                f"Cross-market ZCIS spread "
                f"{params.leg_a_curve_family}-"
                f"{params.leg_b_curve_family} {params.tenor} could "
                f"not be computed: at least one leg returned no "
                "canonical TimeSeries rows.  This is a bug — the "
                "level primitive returned a snapshot but no history.  "
                "Please report with the leg_a/leg_b/tenor "
                f"{params.leg_a_curve_family} / "
                f"{params.leg_b_curve_family} / {params.tenor}."
            )
        }

    leg_a_df = pd.DataFrame(leg_a_rows).rename(
        columns={"value": "leg_a_pct"},
    )
    leg_b_df = pd.DataFrame(leg_b_rows).rename(
        columns={"value": "leg_b_pct"},
    )
    leg_a_df["date"] = pd.to_datetime(leg_a_df["date"])
    leg_b_df["date"] = pd.to_datetime(leg_b_df["date"])

    wide = (
        pd.merge(leg_a_df, leg_b_df, on="date", how="inner")
        .sort_values("date")
        .set_index("date")
    )

    # Drop any rows where either leg's value is NaN — strict
    # inner-join discipline; no synthetic spread on partial data.
    wide = wide.dropna(
        subset=["leg_a_pct", "leg_b_pct"],
    )

    if wide.empty:
        return {
            "error": (
                f"After aligning the two endpoint ZCIS series for "
                f"{params.leg_a_curve_family} vs "
                f"{params.leg_b_curve_family} at {params.tenor}, no "
                "overlapping dates remain.  Verify both curves have "
                "data on this pillar."
            )
        }

    # ------------------------------------------------------------------
    # Spread formula:
    #   spread_pct = leg_a_pct - leg_b_pct
    #   spread_bps = spread_pct * 100
    #
    # The endpoint ZCIS series come in already rounded to
    # yield_round_decimals (the level primitive's canonical
    # TimeSeries applies the rounding upstream).  We round the
    # spread once at the end of this arithmetic at the relevant
    # display unit.  Same boundary-rounding discipline as
    # inflation_swap_curve_spread / breakeven_curve_spread.
    # ------------------------------------------------------------------
    wide["spread_pct"] = (
        wide["leg_a_pct"] - wide["leg_b_pct"]
    ).round(pct_round_decimals)
    wide["spread_bps"] = (
        wide["spread_pct"] * 100
    ).round(bps_round_decimals)

    # ------------------------------------------------------------------
    # Rolling z-score on the spread bps series (BPS scale, mirroring
    # inflation_swap_curve_spread / forward_breakeven_simple).
    # ------------------------------------------------------------------
    wide["z_score"] = rolling_zscore(
        wide["spread_bps"],
        window=z_window,
        min_periods=z_min_periods,
        ddof=z_ddof,
        round_decimals=z_round_decimals,
    )

    # ------------------------------------------------------------------
    # Trim to requested display lookback — anchored to the data's
    # latest aligned trade_date.  Matches inflation_swap_rate_level /
    # inflation_swap_curve_spread / inflation_swap_forward /
    # breakeven_curve_spread / OIS rate_level.
    # ------------------------------------------------------------------
    as_of_ts = wide.index[-1]
    cutoff = pd.Timestamp(
        as_of_ts.date() - timedelta(days=params.lookback_days),
    )
    display_df = wide.loc[wide.index >= cutoff].copy()

    if display_df.empty:
        return {
            "error": (
                f"No cross-market ZCIS spread observations within "
                f"the last {params.lookback_days} days for "
                f"'{params.leg_a_curve_family}' vs "
                f"'{params.leg_b_curve_family}' at {params.tenor}."
            )
        }

    # ------------------------------------------------------------------
    # Build current_metrics
    # ------------------------------------------------------------------
    latest = display_df.iloc[-1]
    spreads = display_df["spread_bps"]

    current_spread_pct = safe_float(
        latest["spread_pct"], decimals=pct_round_decimals,
    )
    current_spread_bps = safe_float(
        latest["spread_bps"], decimals=bps_round_decimals,
    )

    # Daily / weekly / monthly bps changes — series is already in
    # bps so already_bps=True (plain subtraction).  decimals=
    # passes the YAML convention through so the changes respect
    # bps_round_decimals.
    changes = period_changes(
        spreads,
        offsets=period_offsets,
        already_bps=True,
        decimals=bps_round_decimals,
    )

    # Trailing high/low/percentile of the cross-market ZCIS spread
    # (bps scale).
    high, low, percentile = trailing_high_low_percentile(
        spreads, window=trailing_window, decimals=bps_round_decimals,
    )

    spread_label = (
        f"{params.leg_a_curve_family}-"
        f"{params.leg_b_curve_family} "
        f"{params.tenor}"
    )

    # Derived index-family summary surfaced top-level on
    # current_metrics so the desk reader can read the load-bearing
    # caveat from one field rather than reconciling two per-leg
    # strings.  Reuse the per-leg metadata already extracted above —
    # do NOT recompute.
    leg_a_idx_family = (
        leg_a_metrics_inner.get("inflation_index_family") or ""
    )
    leg_b_idx_family = (
        leg_b_metrics_inner.get("inflation_index_family") or ""
    )
    index_families_match = bool(
        leg_a_idx_family
        and leg_b_idx_family
        and leg_a_idx_family == leg_b_idx_family
    )
    if index_families_match:
        index_family_caveat: Optional[str] = None
    else:
        index_family_caveat = (
            f"Cross-market spread mixes inflation-compensation "
            f"regimes: leg_a references {leg_a_idx_family!r} while "
            f"leg_b references {leg_b_idx_family!r}, so this spread "
            "captures BOTH inflation-expectation differentials AND "
            "structural index-family differences — it is NOT a clean "
            "expected-inflation divergence."
        )

    metrics = CrossMarketInflationSwapSpreadCurrentMetrics(
        as_of_date=as_of_ts.strftime("%Y-%m-%d"),
        leg_a_curve_family=params.leg_a_curve_family,
        leg_b_curve_family=params.leg_b_curve_family,
        tenor=params.tenor,
        tenor_years=round(tenor_years, window_years_round),
        spread_label=spread_label,
        spread_pct=current_spread_pct,
        spread_bps=current_spread_bps,
        change_1d_bps=changes["daily"],
        change_1w_bps=changes["weekly"],
        change_1m_bps=changes["monthly"],
        # Pass z_round_decimals so a YAML override above 4 isn't
        # silently truncated by safe_float's default of 4.
        z_score_252d=safe_float(
            latest.get("z_score"), decimals=z_round_decimals,
        ),
        high_252d_bps=high,
        low_252d_bps=low,
        percentile_252d=percentile,
        leg_a_pct=safe_float(
            latest.get("leg_a_pct"),
            decimals=yield_round_decimals,
        ),
        leg_b_pct=safe_float(
            latest.get("leg_b_pct"),
            decimals=yield_round_decimals,
        ),
        observation_count=len(display_df),
        leg_a_inflation_index_family=leg_a_idx_family,
        leg_b_inflation_index_family=leg_b_idx_family,
        index_families_match=index_families_match,
        index_family_caveat=index_family_caveat,
        leg_a_index_lag=leg_a_metrics_inner.get("index_lag") or "",
        leg_b_index_lag=leg_b_metrics_inner.get("index_lag") or "",
        leg_a_interpolation=(
            leg_a_metrics_inner.get("interpolation") or ""
        ),
        leg_b_interpolation=(
            leg_b_metrics_inner.get("interpolation") or ""
        ),
        leg_a_underlying_index=leg_a_metrics_inner.get(
            "underlying_index",
        ),
        leg_b_underlying_index=leg_b_metrics_inner.get(
            "underlying_index",
        ),
        methodology_label=methodology_label,
    )

    # ------------------------------------------------------------------
    # Build time_series (bespoke shape) AND the two canonical
    # TimeSeries (spread in BPS, z-score in Z_SCORE).  All three
    # share the same display_df rows so they cannot drift.
    # ------------------------------------------------------------------
    ts_rows = [
        CrossMarketInflationSwapSpreadTimeSeriesRow(
            date=row.Index.strftime("%Y-%m-%d"),
            spread_pct=round(row.spread_pct, pct_round_decimals),
            spread_bps=round(row.spread_bps, bps_round_decimals),
            leg_a_pct=round(
                float(row.leg_a_pct), yield_round_decimals,
            ),
            leg_b_pct=round(
                float(row.leg_b_pct), yield_round_decimals,
            ),
        )
        for row in display_df.itertuples()
    ]
    canonical_spread = _build_canonical_spread_series(
        display_df,
        leg_a_curve_family=params.leg_a_curve_family,
        leg_b_curve_family=params.leg_b_curve_family,
        tenor=params.tenor,
        bps_round_decimals=bps_round_decimals,
        leg_a_inflation_index_family=leg_a_idx_family,
        leg_b_inflation_index_family=leg_b_idx_family,
    )
    canonical_zscore = _build_canonical_zscore_series(
        display_df,
        leg_a_curve_family=params.leg_a_curve_family,
        leg_b_curve_family=params.leg_b_curve_family,
        tenor=params.tenor,
        z_round_decimals=z_round_decimals,
    )

    output = CrossMarketInflationSwapSpreadOutput(
        current_metrics=metrics,
        time_series=ts_rows,
        time_series_spread=canonical_spread,
        time_series_zscore=canonical_zscore,
    )
    return output.model_dump()


# ============================================================================
# CANONICAL TIME-SERIES BUILDERS
# ============================================================================

def _series_name_slug(
    *,
    leg_a_curve_family: str,
    leg_b_curve_family: str,
    tenor: str,
) -> str:
    """Lower-snake-case slug used as the canonical TimeSeries
    series_name prefix.  Pattern:
    ``<leg_a_curve_family>_<leg_b_curve_family>_<tenor>``.
    """
    return (
        f"{leg_a_curve_family.lower()}_"
        f"{leg_b_curve_family.lower()}_"
        f"{tenor.lower()}"
    )


def _build_canonical_spread_series(
    display_df: pd.DataFrame,
    *,
    leg_a_curve_family: str,
    leg_b_curve_family: str,
    tenor: str,
    bps_round_decimals: int,
    leg_a_inflation_index_family: str,
    leg_b_inflation_index_family: str,
) -> TimeSeries:
    """Convert the display DataFrame's spread_bps column into the
    canonical ``TimeSeries`` shape (closed-enum BPS units).

    Cross-market ZCIS spreads ship in BPS (mirrors sovereign
    cross_market_spread; do NOT confuse with the level convention
    where forward / level primitives ship in PERCENT).

    Naming convention:
    ``<leg_a>_<leg_b>_<tenor>_zcis_cross_market_spread``.
    """
    slug = _series_name_slug(
        leg_a_curve_family=leg_a_curve_family,
        leg_b_curve_family=leg_b_curve_family,
        tenor=tenor,
    )
    rows = [
        TimeSeriesRow(
            date=row.Index.strftime("%Y-%m-%d"),
            value=round(float(row.spread_bps), bps_round_decimals),
        )
        for row in display_df.itertuples()
    ]
    return TimeSeries(
        series_name=f"{slug}_zcis_cross_market_spread",
        units=TimeSeriesUnits.BPS,
        description=(
            f"Same-tenor cross-market ZCIS spread for "
            f"{leg_a_curve_family} vs {leg_b_curve_family} at "
            f"{tenor} ((leg_a_pct - leg_b_pct) * 100) over the "
            "displayed window.  INDEX-FAMILY CAVEAT: leg_a "
            f"references {leg_a_inflation_index_family!r} while "
            f"leg_b references {leg_b_inflation_index_family!r} — "
            "this spread captures BOTH inflation-expectation "
            "differentials AND structural index-family "
            "differences; it is NOT a clean expected-inflation "
            "divergence."
        ),
        rows=rows,
    )


def _build_canonical_zscore_series(
    display_df: pd.DataFrame,
    *,
    leg_a_curve_family: str,
    leg_b_curve_family: str,
    tenor: str,
    z_round_decimals: int,
) -> TimeSeries:
    """Convert the display DataFrame's z_score column into the
    canonical ``TimeSeries`` shape (closed-enum Z_SCORE units).

    Naming convention:
    ``<leg_a>_<leg_b>_<tenor>_zcis_cross_market_spread_zscore``.
    """
    slug = _series_name_slug(
        leg_a_curve_family=leg_a_curve_family,
        leg_b_curve_family=leg_b_curve_family,
        tenor=tenor,
    )
    rows = [
        TimeSeriesRow(
            date=row.Index.strftime("%Y-%m-%d"),
            value=safe_float(row.z_score, decimals=z_round_decimals),
        )
        for row in display_df.itertuples()
    ]
    return TimeSeries(
        series_name=f"{slug}_zcis_cross_market_spread_zscore",
        units=TimeSeriesUnits.Z_SCORE,
        description=(
            f"Rolling z-score of the {leg_a_curve_family}-"
            f"{leg_b_curve_family} cross-market ZCIS spread "
            f"(in bps) at {tenor} vs its own trailing window."
        ),
        rows=rows,
    )
