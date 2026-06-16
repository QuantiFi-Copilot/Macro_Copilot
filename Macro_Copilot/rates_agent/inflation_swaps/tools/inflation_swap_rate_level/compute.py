"""
compute.py — Config-driven zero-coupon inflation swap (ZCIS) rate level
========================================================================

First tool in the new ``inflation_swaps`` domain.  Owns the desk
concept of a single-curve-pillar ZCIS rate level — distinct from the
linker bond-implied breakeven (a two-bond differential carrying IRP /
liquidity premia) and from the linker real_yield_level primitive
(linker bond's real yield-to-maturity).

A ZCIS is its own instrument family with its own ``index_lag``,
``inflation_index_family``, and ``interpolation`` conventions
recorded in ``macro_data.instrument_master``.  This compute path
surfaces those fields on the wire so a desk reader can interpret the
rate level honestly: USD ZCIS references CPI-U with a 3M lag and
daily interpolation; EUR ZCIS references HICP (ex-tobacco) with a 3M
lag and monthly interpolation; GBP ZCIS references RPI with a 2M lag
and monthly interpolation — these are NOT comparable cross-curve
without harmonising the conventions.

No-proxy guard
--------------
The discriminator on the DB read is the load-bearing guarantee that
this primitive cannot return a proxy:

  - ``instrument_type='inflation_swap'`` AND
    ``pricing_type='zero_coupon_breakeven'`` AND
    ``curve_family=<user>`` AND ``tenor=<user>``.

Without these filters the SELECT could match a different ZCIS
pricing variant (e.g. year-on-year inflation swaps) or a non-swap
instrument that happens to share a curve_family / tenor label, and
the level-stat math would silently run on the wrong series.  Both
filters are enforced in code (NOT YAML).  Putting them in YAML would
let a config edit silently switch the tool to a proxy.  Per
DESIGN_PRINCIPLES.md §5, structural identity stays in code.

Single-ticker guarantee
-----------------------
The ZCIS playbook canonically yields exactly one ticker per
``(curve_family, tenor)``.  If the SELECT returns rows attributed to
more than one ``vendor_ticker``, the tool returns a controlled
``{"error": ...}`` envelope rather than silently averaging across
tickers.  Same shape every other rates tool uses for recoverable
ambiguity.

Trailing-range wire freeze
--------------------------
``trailing_range_window_days`` is locked at 252 in V1.  The output
schema's field names (``high_252d_pct``, ``low_252d_pct``,
``percentile_252d``) embed the number; changing the convention
without renaming the wire fields would silently lie about what the
percentile is computed against.  ``compute()`` raises
``NotImplementedError`` if this is set to anything else; see
``methodology.planned_extensions`` in the YAML for the path to
making it configurable.  Same wire-freeze as sibling level tools.

Observation-count anchoring
---------------------------
The cutoff is anchored to the data's latest observation date
(matching OIS rate_level and linker real_yield_level), NOT to
``date.today()``.  ZCIS daily series can lag wall-clock by several
business days; anchoring to ``date.today()`` would silently shrink
the displayed window when data is stale.

Test seam
---------
``fetch_zcis_single_pillar`` and ``date`` are imported here at
module level; tests patch them via
``patch("rates_agent.inflation_swaps.tools.inflation_swap_rate_level.compute.X")``.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rates_agent.inflation_swaps.tools.inflation_swap_rate_level.schemas import (
    InflationSwapRateLevelCurrentMetrics,
    InflationSwapRateLevelInput,
    InflationSwapRateLevelOutput,
)
from shared.analytics.levels import (
    clean_single_series,
    compute_level_metrics,
)
from shared.analytics.rates_fetch import _cap_to_end_date, latest_trade_date
from shared.config import ToolConfig, load_tool_config
from shared.schemas import TimeSeries, TimeSeriesRow, TimeSeriesUnits


# Bundled config — public symbol so external callers (mcp_server,
# tests, future REST routes, batch surfaces) can build a ToolConfig
# from the same source the tool uses.
CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


# Trailing-range window is wire-frozen at 252 in V1; see schemas.py
# and config.yaml's ``planned_extensions``.
_FROZEN_TRAILING_WINDOW: int = 252


# instrument_type + pricing_type discriminators.  Code-level
# invariants — NOT YAML knobs — per DESIGN_PRINCIPLES.md §5 and the
# no-proxy guard rationale in the module docstring.
_ZCIS_INSTRUMENT_TYPE: str = "inflation_swap"
_ZCIS_PRICING_TYPE: str = "zero_coupon_breakeven"


# ============================================================================
# CONFIG → KWARGS RESOLVER
# ============================================================================

def _conventions_from_config(config: ToolConfig) -> dict:
    """Pull the methodology kwargs ``compute_level_metrics`` needs from
    a ToolConfig.

    Raises NotImplementedError if ``trailing_range_window_days`` is set
    to anything other than 252 — see the wire-freeze rationale in the
    module docstring.
    """
    trailing = config.convention_value("trailing_range_window_days")
    if trailing != _FROZEN_TRAILING_WINDOW:
        raise NotImplementedError(
            f"trailing_range_window_days={trailing!r} is documented in this "
            f"tool's config.yaml as a future-supported value (see "
            f"methodology.planned_extensions) but is not yet implemented.  "
            f"V1 supports only {_FROZEN_TRAILING_WINDOW} because the output "
            f"field names (high_252d_pct, low_252d_pct, percentile_252d) "
            f"embed that number on the wire.  Either restore the value to "
            f"{_FROZEN_TRAILING_WINDOW} or implement the schema rename + "
            f"frontend update documented in planned_extensions."
        )

    return {
        "z_window": config.convention_value("z_score_window_days"),
        "z_min_periods": config.convention_value("z_score_min_periods"),
        "z_ddof": config.convention_value("z_score_ddof"),
        "period_offsets": {
            "daily": config.convention_value("daily_change_offset_rows"),
            "weekly": config.convention_value("weekly_change_offset_rows"),
            "monthly": config.convention_value("monthly_change_offset_rows"),
        },
        "trailing_window": trailing,
        "yield_round_decimals": config.convention_value("yield_round_decimals"),
        "z_score_round_decimals": config.convention_value(
            "z_score_round_decimals",
        ),
        "high_low_round_decimals": config.convention_value(
            "high_low_round_decimals",
        ),
    }


# ============================================================================
# DB FETCH — guarded by instrument_type + pricing_type + curve_family + tenor
# ============================================================================

# Joined SELECT against ``v_market_data_daily_enriched`` × the
# ``instrument_master.attributes`` JSONB so we can constrain on
# ``pricing_type`` (which lives inside ``attributes``, not on the view's
# top-level columns) AND surface ``inflation_index_family``,
# ``index_lag``, ``interpolation``, and ``underlying_index`` from the
# resolved ticker on the same query.  Single read trip.
_FETCH_ZCIS_SQL = text(
    """
    SELECT
        v.trade_date,
        v.field_value::double precision AS field_value,
        v.vendor_ticker,
        i.attributes ->> 'pricing_type'             AS pricing_type,
        i.attributes ->> 'inflation_index_family'    AS inflation_index_family,
        i.attributes ->> 'index_lag'                 AS index_lag,
        i.attributes ->> 'interpolation'             AS interpolation,
        i.underlying_index                            AS underlying_index
    FROM macro_data.v_market_data_daily_enriched AS v
    JOIN macro_data.instrument_master AS i
      ON v.instrument_id = i.instrument_id
    WHERE v.instrument_type            = :instrument_type
      AND v.curve_family                = :curve_family
      AND v.tenor                       = :tenor
      AND v.field_name                  = :field_name
      AND (i.attributes ->> 'pricing_type') = :pricing_type
      AND v.trade_date                  >= :start_date
    ORDER BY v.trade_date
    """
)


def fetch_zcis_single_pillar(
    engine: Engine,
    *,
    curve_family: str,
    tenor: str,
    field_name: str,
    start_date: date,
    end_date: Optional[date] = None,
) -> pd.DataFrame:
    """Fetch a single-pillar ZCIS series on one curve.

    Returns a long-format DataFrame with columns
    ``['trade_date', 'field_value', 'vendor_ticker', 'pricing_type',
    'inflation_index_family', 'index_lag', 'interpolation',
    'underlying_index']``.

    Filters on ``instrument_type='inflation_swap'``,
    ``pricing_type='zero_coupon_breakeven'``,
    ``curve_family=<user>``, and ``tenor=<user>`` — the no-proxy guard
    documented in the module docstring.  Empty DataFrame if no rows
    match.

    ``end_date`` (optional, default ``None``): historical as-of UPPER
    bound.  Applied via the canonical ``_cap_to_end_date`` helper —
    ``None`` returns the frame unchanged byte-for-byte (the default for
    every pre-as-of caller), so this is a strict additive extension.
    The compute path passes ``end_date=anchor``; when the caller does
    not supply ``as_of_date`` the anchor is the latest available
    ``trade_date``, so the cap drops zero rows.
    """
    with engine.connect() as conn:
        result = conn.execute(
            _FETCH_ZCIS_SQL,
            {
                "instrument_type": _ZCIS_INSTRUMENT_TYPE,
                "pricing_type": _ZCIS_PRICING_TYPE,
                "curve_family": curve_family,
                "tenor": tenor,
                "field_name": field_name,
                "start_date": start_date.isoformat(),
            },
        )
        rows = result.fetchall()
        columns = list(result.keys())
    return _cap_to_end_date(pd.DataFrame(rows, columns=columns), end_date)


def _resolve_reference_metadata(
    raw_df: pd.DataFrame,
    *,
    curve_family: str,
    tenor: str,
) -> Tuple[Dict[str, Optional[str]], Optional[str]]:
    """Read the reference-metadata fields from the fetched frame.

    Returns ``({fields...}, error_message)``.  ``error_message`` is
    None on a clean single-ticker resolution; non-None when the
    SELECT spanned multiple distinct vendor_tickers (ambiguous
    resolution) or returned rows with mixed reference metadata.
    """
    if raw_df.empty:
        return {}, "fetched frame is empty"

    distinct_tickers = sorted(set(raw_df["vendor_ticker"].dropna().tolist()))
    if len(distinct_tickers) > 1:
        return {}, (
            f"Ambiguous instrument_master resolution for "
            f"curve_family='{curve_family}', tenor='{tenor}': "
            f"observed {len(distinct_tickers)} distinct vendor_tickers "
            f"({distinct_tickers}).  This tool requires exactly one "
            "ticker per (curve_family, tenor) and refuses to silently "
            "average across them.  Verify the inflation_swaps playbook "
            "and instrument_master rows for this pillar."
        )

    fields = ["inflation_index_family", "index_lag", "interpolation",
              "underlying_index"]
    distincts: Dict[str, list] = {}
    for f in fields:
        if f in raw_df.columns:
            vals = sorted(set(v for v in raw_df[f].tolist() if v is not None))
            distincts[f] = vals
        else:
            distincts[f] = []

    for f in ("inflation_index_family", "index_lag", "interpolation"):
        if len(distincts[f]) > 1:
            return {}, (
                f"Ambiguous reference metadata for "
                f"curve_family='{curve_family}', tenor='{tenor}': "
                f"field '{f}' has multiple distinct values "
                f"({distincts[f]!r}) across rows for the same ticker.  "
                "This tool refuses to silently pick one — refer to "
                "the inflation_swaps playbook to confirm the "
                "intended convention."
            )

    return (
        {
            "inflation_index_family": (
                distincts["inflation_index_family"][0]
                if distincts["inflation_index_family"] else None
            ),
            "index_lag": (
                distincts["index_lag"][0]
                if distincts["index_lag"] else None
            ),
            "interpolation": (
                distincts["interpolation"][0]
                if distincts["interpolation"] else None
            ),
            "underlying_index": (
                distincts["underlying_index"][0]
                if distincts["underlying_index"] else None
            ),
        },
        None,
    )


# ============================================================================
# PUBLIC API
# ============================================================================

def calculate_inflation_swap_rate_level(
    engine: Engine,
    params: InflationSwapRateLevelInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    """Return the current ZCIS rate level + period changes + z-score +
    trailing high / low / percentile + observation_count for a single
    ZCIS curve pillar, plus the load-bearing reference metadata
    (``inflation_index_family``, ``index_lag``, ``interpolation``,
    ``underlying_index``) and the canonical ``time_series`` payload.

    Parameters
    ----------
    engine : Engine
        Live SQLAlchemy engine.
    params : InflationSwapRateLevelInput
        Validated input.  ``field_name=None`` resolves against the
        YAML's ``default_field_name`` convention.
    config : ToolConfig, optional
        Bundled config.yaml is auto-loaded when None.  Tests pass a
        custom ToolConfig to exercise convention overrides.

    Returns
    -------
    dict
        Serialised ``InflationSwapRateLevelOutput``, or
        ``{"error": "..."}`` on recoverable failure.
    """
    if config is None:
        config = load_tool_config(CONFIG_PATH)

    # ------------------------------------------------------------------
    # Pull conventions
    # ------------------------------------------------------------------
    z_window = config.convention_value("z_score_window_days")
    buffer_mult = config.convention_value("z_score_buffer_multiplier")
    ffill_limit = config.convention_value("ffill_limit_days")
    default_field_name = config.convention_value("default_zcis_rate_field")
    metrics_kwargs = _conventions_from_config(config)

    # Wire-honesty disclosure threaded from YAML — NOT hardcoded.
    methodology_label = config.methodology.what_it_does.strip()

    # Resolve field_name: caller's explicit value wins; None falls
    # through to the YAML default.
    field_name_resolved = (
        params.field_name if params.field_name is not None else default_field_name
    )

    # ------------------------------------------------------------------
    # 1. Date window
    # ------------------------------------------------------------------
    buffer_calendar_days = int(z_window * buffer_mult)
    # Anchor the fetch window to this pillar's latest available trade_date
    # (not date.today()) so a short lookback still resolves to real data
    # when the ZCIS series lags wall-clock by several business days; falls
    # back to today only when the pillar has no rows.  Filters mirror the
    # fetch_zcis_single_pillar read (curve_family + tenor + field_name);
    # instrument_type='inflation_swap' further tightens the probe to the
    # ZCIS universe.
    anchor = (
        params.as_of_date
        or latest_trade_date(
            engine,
            curve_family=params.curve_family,
            tenor=params.tenor,
            field_name=field_name_resolved,
            instrument_type=_ZCIS_INSTRUMENT_TYPE,
        )
        or date.today()
    )
    start_date = anchor - timedelta(
        days=params.lookback_days + buffer_calendar_days
    )

    # ------------------------------------------------------------------
    # 2. Fetch — instrument_type='inflation_swap' AND
    #    pricing_type='zero_coupon_breakeven' are mandatory.  Without
    #    these filters, calling the tool with a curve_family that
    #    happens to match a different ZCIS pricing variant (e.g. YoY
    #    inflation swaps when ingested) or a non-swap instrument
    #    sharing the curve label would silently flow through under a
    #    zcis_rate_pct label.  See module docstring.
    # ------------------------------------------------------------------
    raw_df = fetch_zcis_single_pillar(
        engine=engine,
        curve_family=params.curve_family,
        tenor=params.tenor,
        field_name=field_name_resolved,
        start_date=start_date,
        end_date=anchor,
    )

    if raw_df.empty:
        return {
            "error": (
                f"No ZCIS data found for "
                f"curve_family='{params.curve_family}', "
                f"tenor='{params.tenor}', "
                f"field='{field_name_resolved}' since "
                f"{start_date.isoformat()} "
                f"(instrument_type='{_ZCIS_INSTRUMENT_TYPE}', "
                f"pricing_type='{_ZCIS_PRICING_TYPE}').  Verify the "
                "inflation-swap curve family and tenor exist in the "
                "database (see "
                "rates_agent/playbooks/inflation_swaps.yml for the "
                "ingested universe — currently USD_ZCIS / EUR_ZCIS / "
                "GBP_ZCIS at 1Y / 2Y / 3Y / 5Y / 10Y / 20Y / 30Y).  "
                "If you intended a linker real-yield, breakeven, or "
                "nominal sovereign read, route to the corresponding "
                "domain agent instead — this tool only returns "
                "zero-coupon inflation-swap rows."
            )
        }

    # ------------------------------------------------------------------
    # 3. Resolve reference metadata BEFORE running the level math, so
    #    an ambiguous-ticker / mixed-metadata case fails fast with a
    #    controlled error envelope (no fabricated snapshot).
    # ------------------------------------------------------------------
    ref_meta, ref_err = _resolve_reference_metadata(
        raw_df,
        curve_family=params.curve_family,
        tenor=params.tenor,
    )
    if ref_err is not None:
        return {"error": ref_err}

    # ------------------------------------------------------------------
    # 4. Clean
    # ------------------------------------------------------------------
    clean_df = clean_single_series(
        raw_df[["trade_date", "field_value"]],
        ffill_limit=ffill_limit,
    )
    if clean_df.empty:
        return {
            "error": (
                f"All values were null after cleaning for ZCIS "
                f"'{params.curve_family}' {params.tenor}."
            )
        }
    rates = clean_df["field_value"]

    # ------------------------------------------------------------------
    # 5. Metrics — single canonical primitive shared with sibling level
    #    tools
    # ------------------------------------------------------------------
    metrics_dict = compute_level_metrics(rates, **metrics_kwargs)

    # ------------------------------------------------------------------
    # 6. Observation count is the LOOKBACK_DAYS-window count, NOT the
    #    full series length.  Anchor the cutoff to the data's latest
    #    observation date, NOT date.today() — same anchoring as OIS
    #    rate_level and linker real_yield_level.
    # ------------------------------------------------------------------
    as_of_date = rates.index[-1].date()
    cutoff = pd.Timestamp(as_of_date - timedelta(days=params.lookback_days))
    display_rates = rates.loc[rates.index >= cutoff]
    obs_count = len(display_rates)

    if obs_count == 0:
        return {
            "error": (
                f"No observations within the last {params.lookback_days} "
                f"days for ZCIS '{params.curve_family}' "
                f"{params.tenor}."
            )
        }

    # ------------------------------------------------------------------
    # 7. Build snapshot.  Reference metadata + methodology_label
    #    threaded onto current_metrics so the wire surface is
    #    self-describing (no second tool call needed to know which
    #    inflation index / lag / interpolation backs the level).
    # ------------------------------------------------------------------
    metrics = InflationSwapRateLevelCurrentMetrics(
        as_of_date=as_of_date.strftime("%Y-%m-%d"),
        curve_family=params.curve_family,
        tenor=params.tenor,
        zcis_rate_pct=metrics_dict["current_value"],
        daily_change_bps=metrics_dict["period_changes"]["daily"],
        weekly_change_bps=metrics_dict["period_changes"]["weekly"],
        monthly_change_bps=metrics_dict["period_changes"]["monthly"],
        z_score=metrics_dict["z_score"],
        high_252d_pct=metrics_dict["high"],
        low_252d_pct=metrics_dict["low"],
        percentile_252d=metrics_dict["percentile"],
        observation_count=obs_count,
        inflation_index_family=ref_meta.get("inflation_index_family") or "",
        index_lag=ref_meta.get("index_lag") or "",
        interpolation=ref_meta.get("interpolation") or "",
        underlying_index=ref_meta.get("underlying_index"),
        methodology_label=methodology_label,
    )

    # ------------------------------------------------------------------
    # 8. Canonical TimeSeries.  Same display slice the snapshot was
    #    computed against, rounded with the SAME ``yield_round_decimals``
    #    convention the snapshot used, so the snapshot's
    #    ``zcis_rate_pct`` equals ``time_series.rows[-1].value``
    #    STRICTLY (not just within tolerance).  Series description
    #    surfaces the index family / lag / interpolation detail so the
    #    caveat is visible on the wire even for callers that consume
    #    only the time_series payload.
    # ------------------------------------------------------------------
    yield_round_decimals = metrics_kwargs["yield_round_decimals"]
    canonical_series = _build_canonical_zcis_time_series(
        display_rates,
        curve_family=params.curve_family,
        tenor=params.tenor,
        yield_round_decimals=yield_round_decimals,
        inflation_index_family=ref_meta.get("inflation_index_family") or "",
        index_lag=ref_meta.get("index_lag") or "",
        interpolation=ref_meta.get("interpolation") or "",
    )

    output = InflationSwapRateLevelOutput(
        current_metrics=metrics,
        time_series=canonical_series,
    )
    return output.model_dump()


# ============================================================================
# CANONICAL TIME-SERIES BUILDER
# ============================================================================

def _build_canonical_zcis_time_series(
    display_rates: pd.Series,
    *,
    curve_family: str,
    tenor: str,
    yield_round_decimals: int,
    inflation_index_family: str,
    index_lag: str,
    interpolation: str,
) -> TimeSeries:
    """Convert the cleaned, in-window ZCIS rate series into the
    canonical ``TimeSeries`` shape with closed-enum units (PERCENT).

    Naming convention: ``<curve_family_lower>_<tenor_lower>_zcis_rate``.
    The ``_zcis_rate`` suffix distinguishes from sovereign nominal
    yield series (``_yield``), OIS rate series (``_ois_rate``), and
    linker real-yield series (``_real_yield``) when they end up in
    the same operator panel downstream.

    Description surfaces ``inflation_index_family`` / ``index_lag`` /
    ``interpolation`` so the cross-curve comparability caveat is
    visible on the wire.
    """
    series_name = f"{curve_family.lower()}_{tenor.lower()}_zcis_rate"
    rows = [
        TimeSeriesRow(
            date=ts.strftime("%Y-%m-%d"),
            value=(
                round(float(v), yield_round_decimals)
                if pd.notna(v) else None
            ),
        )
        for ts, v in display_rates.items()
    ]
    description = (
        f"Historical zero-coupon inflation swap (ZCIS) rates for "
        f"{curve_family} {tenor} over the display window (cleaned, "
        f"ffill'd; rounded to {yield_round_decimals} decimals to "
        "match current_metrics.zcis_rate_pct exactly at the latest "
        "row).  Reference: "
        f"inflation_index_family={inflation_index_family!r}, "
        f"index_lag={index_lag!r}, "
        f"interpolation={interpolation!r}.  These conventions differ "
        "across USD_ZCIS / EUR_ZCIS / GBP_ZCIS — cross-curve "
        "comparisons are NOT pure expected-inflation differentials "
        "without harmonising them."
    )
    return TimeSeries(
        series_name=series_name,
        units=TimeSeriesUnits.PERCENT,
        description=description,
        rows=rows,
    )
