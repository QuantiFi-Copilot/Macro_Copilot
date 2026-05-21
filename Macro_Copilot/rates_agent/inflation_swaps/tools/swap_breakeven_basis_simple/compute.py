"""
compute.py — Config-driven swap-breakeven basis
================================================

Fifth tool in the ``inflation_swaps`` domain.  Owns the desk
concept of a *swap-breakeven basis*: the same-tenor, same-currency
difference between a zero-coupon inflation swap (ZCIS) rate and a
generic linker bond-implied breakeven inflation rate at the same
pillar.  Computed per-trade-date via the SPREAD formula:

    basis_pct = zcis_pct - breakeven_pct
    basis_bps = basis_pct * 100

where ``zcis_pct`` is the ZCIS rate level at ``tenor`` on
``zcis_curve_family`` (computed by
``calculate_inflation_swap_rate_level``) and ``breakeven_pct`` is
the generic bond-implied breakeven for the same-currency
``(nominal_curve_family, linker_curve_family)`` pair (computed by
``calculate_breakeven_inflation_simple`` which itself composes a
sovereign nominal yield_level with a linker real_yield_level under
their own four-conjunct discriminators, plus the same-country
invariant).

Concept honesty (load-bearing — surfaced on the wire)
-----------------------------------------------------
The basis is NOT a clean liquidity-premium read.  It also
reflects:

  - index-lag differences between ZCIS conventions (e.g. 3M USD,
    3M EUR, 2M GBP) and the linker bond's realised CPI accrual
    convention,
  - linker bond on-the-run / liquidity premium effects in the
    nominal-vs-real decomposition,
  - structural ZCIS vs linker-breakeven basis present even in
    benign markets.

The honesty disclosure lives in the YAML's
``methodology.what_it_does`` and is threaded onto the wire via
``current_metrics.methodology_label``.  That field is sourced
from the YAML at runtime — NOT hardcoded as a Python literal —
so a YAML edit flows through to runtime behaviour.

Composition path (deliberately simple)
--------------------------------------
This primitive composes two inner calls:

  - ``calculate_inflation_swap_rate_level(zcis_curve_family, tenor,
    config=this_tool_config)`` — the ZCIS leg.  Inherits the
    four-conjunct SELECT guard (``instrument_type='inflation_swap'``
    AND ``pricing_type='zero_coupon_breakeven'`` AND
    ``curve_family=?`` AND ``tenor=?``) transitively.

  - ``calculate_breakeven_inflation_simple(nominal_curve_family,
    linker_curve_family, tenor,
    config=breakeven_tool_config)`` — the breakeven leg.
    Inherits the breakeven primitive's instrument_type
    discriminators (``inflation_linker`` for the linker leg,
    ``sovereign_benchmark`` for the nominal leg), the
    same-country / same-currency invariant, and the composed
    nominal + linker yield reads transitively.  The basis
    primitive does NOT thread further into the sovereign /
    linker primitives — it consumes the breakeven primitive's
    public output only.

Each inner call's bundled ToolConfig is loaded explicitly here so
the dependency on each inner config is observable, mirroring the
``cross_country_breakeven_spread_simple`` cross-domain composition
pattern.  The ZCIS level call is passed THIS primitive's
ToolConfig so methodology stays consistent end-to-end on the ZCIS
leg (cross-config lint enforces value-agreement on shared
convention names).

NO raw market-data SELECTs in this primitive's compute path
-----------------------------------------------------------
The four-conjunct SELECT guard (ZCIS leg) and the
instrument_type discriminators (breakeven leg) live inside the
respective inner primitives.  This primitive inherits both sets
of guards transitively by composing the inner calls; it MUST NOT
issue any of its own SELECTs against
``macro_data.v_market_data_daily_enriched``,
``macro_data.market_data``, or ``macro_data.instrument_master``,
because doing so would let a future refactor silently bypass the
guards.  The SQL validator verifies this transitively (probe 4:
``four_conjunct_guard``).

Why an extended inner lookback
------------------------------
Each inner call trims its canonical TimeSeries to its own
``lookback_days`` worth of display history.  Our rolling z-score
/ trailing range / period changes need a longer history so
they're fully populated from the first row of OUR display
window.  We therefore pass ``lookback_days = params.lookback_days
+ buffer_calendar_days`` to each inner call, then align + trim
ourselves.  The buffer math matches the inner primitives' own
buffer math (``max(z_window, trailing_window) *
buffer_multiplier``) so this primitive does not double-buffer.

Per-trade-date alignment
------------------------
The two PERCENT-units endpoint series are inner-joined on date
so we only emit rows where BOTH legs had data — matches the
sibling spread primitives' alignment step.  No ffill across the
join: dates where one leg has no data after the inner ffill
window are dropped from the basis series, NOT carried forward
as synthetic basis points.  The inner primitives' clean steps
already apply ffill within ``ffill_limit_days``; the basis
layer never adds its own ffill across the inner-join.

Sign convention (canonical, wire-frozen)
----------------------------------------
``basis = zcis_pct - breakeven_pct``.  Per the catalog's
methodology_guardrails ("Must use the canonical sign convention
ZCIS minus breakeven", "Must not silently invert the sign"),
the convention is wire-locked in YAML at
``swap_breakeven_basis_sign_convention=zcis_minus_breakeven``.
``compute()`` raises ``NotImplementedError`` if it is ever set
to anything other than ``zcis_minus_breakeven``; see
``methodology.planned_extensions`` for the path to alternative
sign conventions.

Trailing-range wire freeze
--------------------------
``trailing_range_window_days`` is locked at 252 in V1.  The
output schema's field names (``high_252d_bps``, ``low_252d_bps``,
``percentile_252d``) embed the number; changing the convention
without renaming the wire fields would silently lie about what
the percentile is computed against.  ``compute()`` raises
``NotImplementedError`` if this is set to anything else; see
``methodology.planned_extensions`` for the path to making it
configurable.

Test seam
---------
``calculate_inflation_swap_rate_level`` and
``calculate_breakeven_inflation_simple`` are imported here at
module level; tests patch the inner primitives' own seams
(``...inflation_swap_rate_level.compute.fetch_zcis_single_pillar``,
``...inflation_swap_rate_level.compute.date``,
``...breakeven_inflation_simple.compute.fetch_single_tenor``,
``...breakeven_inflation_simple.compute._fetch_curve_family_country_currency``,
``...breakeven_inflation_simple.compute.date``) OR patch the
inner public callables themselves on this primitive's compute
module so the inner-call return values are controlled.  This
module does NOT import ``date`` directly — the fetch start-date
logic lives entirely inside each inner primitive.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
from sqlalchemy.engine import Engine

from rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple import (
    BreakevenInflationSimpleInput,
    CONFIG_PATH as BREAKEVEN_INFLATION_SIMPLE_CONFIG_PATH,
    calculate_breakeven_inflation_simple,
)
from rates_agent.inflation_swaps.tools.inflation_swap_rate_level import (
    InflationSwapRateLevelInput,
    calculate_inflation_swap_rate_level,
)
from rates_agent.inflation_swaps.tools.swap_breakeven_basis_simple.schemas import (
    SwapBreakevenBasisSimpleCurrentMetrics,
    SwapBreakevenBasisSimpleInput,
    SwapBreakevenBasisSimpleOutput,
    SwapBreakevenBasisSimpleTimeSeriesRow,
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


# Sign convention is wire-frozen at ``zcis_minus_breakeven`` in V1
# per the catalog's methodology_guardrails.  See config.yaml's
# ``methodology.planned_extensions`` for the path to alternative
# sign conventions.
_SUPPORTED_SIGN_CONVENTION: str = "zcis_minus_breakeven"


# ============================================================================
# CONFIG → KWARGS RESOLVER
# ============================================================================

def _conventions_from_config(config: ToolConfig) -> dict:
    """Pull the methodology kwargs the swap_breakeven_basis_simple
    compute() needs from a ToolConfig.

    Raises NotImplementedError if either ``trailing_range_window_days``
    or ``swap_breakeven_basis_sign_convention`` is set to a value
    the V1 wire surface cannot honestly represent.  See the module
    docstring for the wire-freeze rationale on each.
    """
    trailing = config.convention_value("trailing_range_window_days")
    if trailing != _FROZEN_TRAILING_WINDOW:
        raise NotImplementedError(
            f"trailing_range_window_days={trailing!r} is documented in "
            f"this tool's config.yaml as a future-supported value (see "
            f"methodology.planned_extensions) but is not yet "
            f"implemented.  V1 supports only {_FROZEN_TRAILING_WINDOW} "
            f"because the output field names (high_252d_bps, "
            f"low_252d_bps, percentile_252d) embed that number on the "
            f"wire.  Either restore the value to "
            f"{_FROZEN_TRAILING_WINDOW} or implement the schema rename "
            f"+ frontend update documented in planned_extensions."
        )

    sign = config.convention_value("swap_breakeven_basis_sign_convention")
    if sign != _SUPPORTED_SIGN_CONVENTION:
        raise NotImplementedError(
            f"swap_breakeven_basis_sign_convention={sign!r} is not "
            f"supported in V1.  The only supported value is "
            f"{_SUPPORTED_SIGN_CONVENTION!r}.  Per the catalog's "
            f"methodology_guardrails ('Must use the canonical sign "
            f"convention ZCIS minus breakeven', 'Must not silently "
            f"invert the sign'), V1 wire-locks the sign at "
            f"{_SUPPORTED_SIGN_CONVENTION!r}.  Alternative sign "
            f"conventions (e.g. breakeven_minus_zcis) are listed in "
            f"this tool's methodology.planned_extensions in "
            f"config.yaml; do NOT override the convention until that "
            f"V2 work lands.  Supporting a second value requires "
            f"updating the wire-disclosed methodology_label, the SQL "
            f"validator, and the snapshot field names in lockstep so "
            f"a desk user can never see a label that disagrees with "
            f"the math."
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

def calculate_swap_breakeven_basis_simple(
    engine: Engine,
    params: SwapBreakevenBasisSimpleInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    """Calculate the same-tenor, same-currency swap-breakeven basis.

        basis_pct = zcis_pct - breakeven_pct
        basis_bps = basis_pct * 100

    Sign convention is ZCIS minus breakeven — wire-locked per the
    catalog's methodology_guardrails.

    Parameters
    ----------
    engine : Engine
        Live SQLAlchemy engine connected to TimescaleDB.
    params : SwapBreakevenBasisSimpleInput
        Validated input.  ``nominal_curve_family !=
        linker_curve_family`` (structural invariant) and ``tenor``
        is parseable (validated at the schema layer).
    config : ToolConfig, optional
        Bundled config.yaml is auto-loaded when None.  Tests pass
        a custom ToolConfig to exercise convention overrides;
        production wrappers must pass ``config=`` explicitly.

    Returns
    -------
    dict
        Serialised ``SwapBreakevenBasisSimpleOutput``, or
        ``{"error": "..."}`` on recoverable failure (with a
        leg-attributed prefix for inner-call failures).
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

    # Wire-honesty disclosure threaded from YAML — NOT hardcoded.
    methodology_label = config.methodology.what_it_does.strip()

    # ------------------------------------------------------------------
    # Tenor year fraction for display.  The Pydantic validator
    # already enforced parsability; re-running the parser here is
    # cheap and keeps compute() self-contained.
    # ------------------------------------------------------------------
    tenor_years = tenor_to_years(params.tenor)

    # ------------------------------------------------------------------
    # Extended inner lookback.  Each inner call trims its canonical
    # TimeSeries to ``lookback_days`` worth of display history.  We
    # need a longer window so OUR rolling stats are fully populated
    # from the first row of OUR display window; match the buffer
    # math the inner primitives use internally so we don't double-
    # buffer.
    # ------------------------------------------------------------------
    buffer_calendar_days = int(
        max(z_window, trailing_window) * buffer_multiplier
    )
    extended_lookback_days = params.lookback_days + buffer_calendar_days

    # ------------------------------------------------------------------
    # Compose: ZCIS leg.  Pass ``config=`` (this primitive's
    # ToolConfig) so the inner conventions use the same values this
    # primitive uses; cross-config lint enforces value-agreement on
    # the bundled YAMLs, and any test override on this primitive's
    # ToolConfig flows through to the inner level call.
    # ``params.field_name`` (None or explicit) is threaded directly
    # — None falls through to the level primitive's own
    # ``default_zcis_rate_field`` default; an explicit override
    # propagates to both inner calls so the ZCIS and breakeven legs
    # are read off consistent fields by construction.
    # ------------------------------------------------------------------
    zcis_inner = calculate_inflation_swap_rate_level(
        engine=engine,
        params=InflationSwapRateLevelInput(
            curve_family=params.zcis_curve_family,
            tenor=params.tenor,
            lookback_days=extended_lookback_days,
            field_name=params.field_name,
        ),
        config=config,
    )
    if "error" in zcis_inner:
        return {
            "error": (
                f"Swap-breakeven basis "
                f"{params.zcis_curve_family} - "
                f"{params.nominal_curve_family}/"
                f"{params.linker_curve_family} {params.tenor} could "
                f"not be computed: the zcis "
                f"({params.zcis_curve_family}) leg failed.  "
                f"Inner error: {zcis_inner['error']}"
            )
        }

    # ------------------------------------------------------------------
    # Compose: breakeven leg.  The breakeven primitive uses its own
    # bundled ToolConfig (``default_field_name=YLD_YTM_MID`` for the
    # linker / nominal yield reads), so we load it explicitly here
    # rather than threading this primitive's ToolConfig through.
    # ``load_tool_config`` caches by path — repeated calls within a
    # process are a free lookup after the first.
    # ``params.field_name`` (None or explicit) is threaded directly
    # — None falls through to the breakeven primitive's own
    # ``default_field_name`` default; an explicit override
    # propagates to both inner calls.
    # ------------------------------------------------------------------
    breakeven_config = load_tool_config(BREAKEVEN_INFLATION_SIMPLE_CONFIG_PATH)
    breakeven_inner = calculate_breakeven_inflation_simple(
        engine=engine,
        params=BreakevenInflationSimpleInput(
            nominal_curve_family=params.nominal_curve_family,
            linker_curve_family=params.linker_curve_family,
            tenor=params.tenor,
            lookback_days=extended_lookback_days,
            field_name=params.field_name,
        ),
        config=breakeven_config,
    )
    if "error" in breakeven_inner:
        return {
            "error": (
                f"Swap-breakeven basis "
                f"{params.zcis_curve_family} - "
                f"{params.nominal_curve_family}/"
                f"{params.linker_curve_family} {params.tenor} could "
                f"not be computed: the breakeven "
                f"({params.nominal_curve_family}/"
                f"{params.linker_curve_family}) leg failed.  "
                f"Inner error: {breakeven_inner['error']}"
            )
        }

    # ------------------------------------------------------------------
    # Per-leg metadata extraction.  ZCIS leg surfaces full reference
    # metadata on its current_metrics (inflation_index_family /
    # index_lag / interpolation / underlying_index).  Breakeven leg's
    # current_metrics surfaces the per-leg yields and the breakeven
    # itself; if a future revision adds linker reference metadata,
    # this primitive picks it up via the same .get(...) seam without
    # code changes here.
    # ------------------------------------------------------------------
    zcis_metrics_inner = zcis_inner.get("current_metrics", {})
    breakeven_metrics_inner = breakeven_inner.get("current_metrics", {})

    # ------------------------------------------------------------------
    # Align endpoint series by date.  ZCIS leg's canonical
    # ``time_series`` is a single TimeSeries (rate level, in PERCENT)
    # rounded to ``yield_round_decimals``; breakeven leg's canonical
    # ``time_series_breakeven`` is in BPS, rounded to
    # ``bps_round_decimals``.  We use the breakeven series in BPS
    # directly to inherit the breakeven primitive's boundary
    # rounding, and convert to PCT here for the basis subtraction
    # (zcis is already in PCT).  Strict pandas inner-join on date
    # so we only emit rows where BOTH legs had data — no synthetic
    # basis points are produced on dates where one leg has no data.
    # ------------------------------------------------------------------
    zcis_rows = zcis_inner.get("time_series", {}).get("rows", [])
    breakeven_rows = breakeven_inner.get(
        "time_series_breakeven", {},
    ).get("rows", [])
    if not zcis_rows or not breakeven_rows:
        return {
            "error": (
                f"Swap-breakeven basis "
                f"{params.zcis_curve_family} - "
                f"{params.nominal_curve_family}/"
                f"{params.linker_curve_family} {params.tenor} could "
                f"not be computed: at least one leg returned no "
                "canonical TimeSeries rows.  This is a bug — the "
                "inner primitive returned a snapshot but no history.  "
                "Please report with the zcis / nominal / linker / "
                "tenor "
                f"{params.zcis_curve_family} / "
                f"{params.nominal_curve_family} / "
                f"{params.linker_curve_family} / {params.tenor}."
            )
        }

    zcis_df = pd.DataFrame(zcis_rows).rename(
        columns={"value": "zcis_pct"},
    )
    breakeven_df = pd.DataFrame(breakeven_rows).rename(
        columns={"value": "breakeven_bps"},
    )
    zcis_df["date"] = pd.to_datetime(zcis_df["date"])
    breakeven_df["date"] = pd.to_datetime(breakeven_df["date"])

    wide = (
        pd.merge(zcis_df, breakeven_df, on="date", how="inner")
        .sort_values("date")
        .set_index("date")
    )

    # Drop any rows where either leg's value is NaN — strict
    # inner-join discipline; no synthetic basis on partial data.
    wide = wide.dropna(
        subset=["zcis_pct", "breakeven_bps"],
    )

    if wide.empty:
        return {
            "error": (
                f"After aligning the ZCIS and breakeven series for "
                f"{params.zcis_curve_family} - "
                f"{params.nominal_curve_family}/"
                f"{params.linker_curve_family} at {params.tenor}, no "
                "overlapping dates remain.  Verify both legs have "
                "data on this pillar for the requested window."
            )
        }

    # ------------------------------------------------------------------
    # Basis formula:
    #   breakeven_pct = breakeven_bps / 100 (rounded to pct_round_decimals)
    #   basis_pct = zcis_pct - breakeven_pct
    #   basis_bps = basis_pct * 100
    #
    # The inner ZCIS series comes pre-rounded to
    # yield_round_decimals; the inner breakeven series comes
    # pre-rounded to bps_round_decimals.  We round the basis once
    # at the end of this arithmetic at the relevant display unit.
    # Same boundary-rounding discipline as
    # cross_market_inflation_swap_spread /
    # cross_country_breakeven_spread_simple /
    # breakeven_curve_spread.
    # ------------------------------------------------------------------
    wide["breakeven_pct"] = (wide["breakeven_bps"] / 100.0).round(
        pct_round_decimals,
    )
    wide["basis_pct"] = (
        wide["zcis_pct"] - wide["breakeven_pct"]
    ).round(pct_round_decimals)
    wide["basis_bps"] = (
        wide["basis_pct"] * 100
    ).round(bps_round_decimals)

    # ------------------------------------------------------------------
    # Rolling z-score on the basis bps series (BPS scale, mirroring
    # the sibling spread primitives).
    # ------------------------------------------------------------------
    wide["z_score"] = rolling_zscore(
        wide["basis_bps"],
        window=z_window,
        min_periods=z_min_periods,
        ddof=z_ddof,
        round_decimals=z_round_decimals,
    )

    # ------------------------------------------------------------------
    # Trim to requested display lookback — anchored to the data's
    # latest aligned trade_date.  Matches every sibling spread tool.
    # ------------------------------------------------------------------
    as_of_ts = wide.index[-1]
    cutoff = pd.Timestamp(
        as_of_ts.date() - timedelta(days=params.lookback_days),
    )
    display_df = wide.loc[wide.index >= cutoff].copy()

    if display_df.empty:
        return {
            "error": (
                f"No swap-breakeven basis observations within the "
                f"last {params.lookback_days} days for "
                f"{params.zcis_curve_family} - "
                f"{params.nominal_curve_family}/"
                f"{params.linker_curve_family} at {params.tenor}."
            )
        }

    # ------------------------------------------------------------------
    # Build current_metrics
    # ------------------------------------------------------------------
    latest = display_df.iloc[-1]
    bases = display_df["basis_bps"]

    current_basis_pct = safe_float(
        latest["basis_pct"], decimals=pct_round_decimals,
    )
    current_basis_bps = safe_float(
        latest["basis_bps"], decimals=bps_round_decimals,
    )
    current_breakeven_pct = safe_float(
        latest["breakeven_pct"], decimals=pct_round_decimals,
    )
    current_breakeven_bps = safe_float(
        latest["breakeven_bps"], decimals=bps_round_decimals,
    )

    # Daily / weekly / monthly bps changes — series is already in
    # bps so already_bps=True (plain subtraction).  decimals=
    # passes the YAML convention through so the changes respect
    # bps_round_decimals.
    changes = period_changes(
        bases,
        offsets=period_offsets,
        already_bps=True,
        decimals=bps_round_decimals,
    )

    # Trailing high/low/percentile of the swap-breakeven basis
    # (bps scale).
    high, low, percentile = trailing_high_low_percentile(
        bases, window=trailing_window, decimals=bps_round_decimals,
    )

    basis_label = (
        f"{params.zcis_curve_family} - "
        f"{params.nominal_curve_family}/"
        f"{params.linker_curve_family} "
        f"{params.tenor} swap-breakeven basis"
    )

    # Per-leg metadata pulled from the inner primitives' surfaces.
    zcis_idx_family = (
        zcis_metrics_inner.get("inflation_index_family") or ""
    )
    zcis_index_lag_val = zcis_metrics_inner.get("index_lag") or ""
    zcis_interpolation = (
        zcis_metrics_inner.get("interpolation") or ""
    )
    zcis_underlying_index = zcis_metrics_inner.get("underlying_index")

    # The breakeven primitive's current public surface does not yet
    # expose linker inflation_index_family / index_lag — see the
    # schema docstring for the rationale.  We pull what the surface
    # provides (empty / None today) so a future breakeven revision
    # automatically flows through here without code changes.
    linker_idx_family = (
        breakeven_metrics_inner.get("inflation_index_family") or None
    )
    linker_index_lag_val = (
        breakeven_metrics_inner.get("index_lag") or None
    )

    # Derived index-family summary surfaced top-level on
    # current_metrics so the desk reader can read the load-bearing
    # caveat from one field.  The match is True only when BOTH
    # values are non-empty AND equal; otherwise False (including
    # the common case where the breakeven primitive has not yet
    # surfaced linker inflation_index_family on its public wire).
    index_families_match = bool(
        zcis_idx_family
        and linker_idx_family
        and zcis_idx_family == linker_idx_family
    )
    if index_families_match:
        index_family_caveat: Optional[str] = None
    elif not linker_idx_family:
        index_family_caveat = (
            f"Linker leg's inflation_index_family is not surfaced "
            f"by the breakeven primitive's current public wire — the "
            f"basis primitive cannot directly verify whether the "
            f"linker bond references the same inflation index as "
            f"the ZCIS leg ({zcis_idx_family!r}).  This basis is "
            "NOT a clean liquidity-premium read regardless: it also "
            "reflects index-lag differences between the ZCIS "
            "convention and the linker's realised CPI accrual, "
            "linker on-the-run / liquidity premium effects, AND "
            "structural ZCIS basis present even in benign markets."
        )
    else:
        index_family_caveat = (
            f"Swap-breakeven basis mixes inflation-compensation "
            f"references: the ZCIS leg references "
            f"{zcis_idx_family!r} while the linker leg references "
            f"{linker_idx_family!r}, so this basis is NOT a clean "
            "basis read — it captures BOTH index-family "
            "differences AND the structural ZCIS-vs-linker basis "
            "(index-lag, linker on-the-run / liquidity premium, "
            "structural basis effects)."
        )

    metrics = SwapBreakevenBasisSimpleCurrentMetrics(
        as_of_date=as_of_ts.strftime("%Y-%m-%d"),
        zcis_curve_family=params.zcis_curve_family,
        nominal_curve_family=params.nominal_curve_family,
        linker_curve_family=params.linker_curve_family,
        tenor=params.tenor,
        tenor_years=round(tenor_years, window_years_round),
        basis_label=basis_label,
        basis_pct=current_basis_pct,
        basis_bps=current_basis_bps,
        zcis_pct=safe_float(
            latest.get("zcis_pct"),
            decimals=yield_round_decimals,
        ),
        breakeven_pct=current_breakeven_pct,
        breakeven_bps=current_breakeven_bps,
        nominal_yield_pct=safe_float(
            breakeven_metrics_inner.get("nominal_yield_pct"),
            decimals=yield_round_decimals,
        ),
        real_yield_pct=safe_float(
            breakeven_metrics_inner.get("real_yield_pct"),
            decimals=yield_round_decimals,
        ),
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
        observation_count=len(display_df),
        zcis_inflation_index_family=zcis_idx_family,
        zcis_index_lag=zcis_index_lag_val,
        zcis_interpolation=zcis_interpolation,
        zcis_underlying_index=zcis_underlying_index,
        linker_inflation_index_family=linker_idx_family,
        linker_index_lag=linker_index_lag_val,
        index_families_match=index_families_match,
        index_family_caveat=index_family_caveat,
        methodology_label=methodology_label,
    )

    # ------------------------------------------------------------------
    # Build time_series (bespoke shape) AND the two canonical
    # TimeSeries (basis in BPS, z-score in Z_SCORE).  All three
    # share the same display_df rows so they cannot drift.
    # ------------------------------------------------------------------
    ts_rows = [
        SwapBreakevenBasisSimpleTimeSeriesRow(
            date=row.Index.strftime("%Y-%m-%d"),
            basis_pct=round(row.basis_pct, pct_round_decimals),
            basis_bps=round(row.basis_bps, bps_round_decimals),
            zcis_pct=round(
                float(row.zcis_pct), yield_round_decimals,
            ),
            breakeven_pct=round(
                float(row.breakeven_pct), pct_round_decimals,
            ),
        )
        for row in display_df.itertuples()
    ]
    canonical_basis = _build_canonical_basis_series(
        display_df,
        zcis_curve_family=params.zcis_curve_family,
        nominal_curve_family=params.nominal_curve_family,
        linker_curve_family=params.linker_curve_family,
        tenor=params.tenor,
        bps_round_decimals=bps_round_decimals,
        zcis_inflation_index_family=zcis_idx_family,
        linker_inflation_index_family=linker_idx_family,
    )
    canonical_zscore = _build_canonical_zscore_series(
        display_df,
        zcis_curve_family=params.zcis_curve_family,
        nominal_curve_family=params.nominal_curve_family,
        linker_curve_family=params.linker_curve_family,
        tenor=params.tenor,
        z_round_decimals=z_round_decimals,
    )

    output = SwapBreakevenBasisSimpleOutput(
        current_metrics=metrics,
        time_series=ts_rows,
        time_series_basis=canonical_basis,
        time_series_zscore=canonical_zscore,
    )
    return output.model_dump()


# ============================================================================
# CANONICAL TIME-SERIES BUILDERS
# ============================================================================

def _series_name_slug(
    *,
    zcis_curve_family: str,
    nominal_curve_family: str,
    linker_curve_family: str,
    tenor: str,
) -> str:
    """Lower-snake-case slug used as the canonical TimeSeries
    series_name prefix.  Pattern:
    ``<zcis>_<nominal>_<linker>_<tenor>``.
    """
    return (
        f"{zcis_curve_family.lower()}_"
        f"{nominal_curve_family.lower()}_"
        f"{linker_curve_family.lower()}_"
        f"{tenor.lower()}"
    )


def _build_canonical_basis_series(
    display_df: pd.DataFrame,
    *,
    zcis_curve_family: str,
    nominal_curve_family: str,
    linker_curve_family: str,
    tenor: str,
    bps_round_decimals: int,
    zcis_inflation_index_family: str,
    linker_inflation_index_family: Optional[str],
) -> TimeSeries:
    """Convert the display DataFrame's basis_bps column into the
    canonical ``TimeSeries`` shape (closed-enum BPS units).

    The basis is a spread object, NOT a level — it ships in BPS to
    mirror sibling spread primitives.

    Naming convention:
    ``<zcis>_<nominal>_<linker>_<tenor>_swap_breakeven_basis``.
    """
    slug = _series_name_slug(
        zcis_curve_family=zcis_curve_family,
        nominal_curve_family=nominal_curve_family,
        linker_curve_family=linker_curve_family,
        tenor=tenor,
    )
    rows = [
        TimeSeriesRow(
            date=row.Index.strftime("%Y-%m-%d"),
            value=round(float(row.basis_bps), bps_round_decimals),
        )
        for row in display_df.itertuples()
    ]
    linker_repr = (
        repr(linker_inflation_index_family)
        if linker_inflation_index_family else "<not surfaced>"
    )
    return TimeSeries(
        series_name=f"{slug}_swap_breakeven_basis",
        units=TimeSeriesUnits.BPS,
        description=(
            f"Same-tenor, same-currency swap-breakeven basis "
            f"({zcis_curve_family} - "
            f"{nominal_curve_family}/{linker_curve_family}) at "
            f"{tenor} (sign convention `zcis_minus_breakeven`: "
            "zcis_pct - breakeven_pct, *100) over the "
            "displayed window.  NOT a clean liquidity-premium read "
            "— also reflects index-lag differences, linker "
            "on-the-run / liquidity effects, and structural ZCIS "
            "basis.  ZCIS leg references "
            f"{zcis_inflation_index_family!r}; linker leg index "
            f"family: {linker_repr}."
        ),
        rows=rows,
    )


def _build_canonical_zscore_series(
    display_df: pd.DataFrame,
    *,
    zcis_curve_family: str,
    nominal_curve_family: str,
    linker_curve_family: str,
    tenor: str,
    z_round_decimals: int,
) -> TimeSeries:
    """Convert the display DataFrame's z_score column into the
    canonical ``TimeSeries`` shape (closed-enum Z_SCORE units).

    Naming convention:
    ``<zcis>_<nominal>_<linker>_<tenor>_swap_breakeven_basis_zscore``.
    """
    slug = _series_name_slug(
        zcis_curve_family=zcis_curve_family,
        nominal_curve_family=nominal_curve_family,
        linker_curve_family=linker_curve_family,
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
        series_name=f"{slug}_swap_breakeven_basis_zscore",
        units=TimeSeriesUnits.Z_SCORE,
        description=(
            f"Rolling z-score of the {zcis_curve_family} - "
            f"{nominal_curve_family}/{linker_curve_family} "
            f"swap-breakeven basis (in bps) at {tenor} vs its own "
            "trailing window."
        ),
        rows=rows,
    )
