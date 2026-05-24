"""
compute.py — Deterministic OTR/OFR yield-spread math (config-driven)
====================================================================

For one (country, tenor) sovereign cash-bond slot, reads:
  - macro_data.otr_history (ADR 0003) — the SCD2 window log;
  - macro_data.market_data_daily — per-CUSIP cash-bond yields, keyed
    by instrument_id (ADR 0005's sovereign_cash_bonds.yml universe).

Resolves the OTR bond per trade_date as the window covering that date,
and the OFR ("first off-the-run") bond as the SCD2 row immediately
prior (LAG over effective_from).  This is a Bucket 1A primitive in
the spread-arithmetic family — every methodology choice (z-score
window, fill limit, sample-vs-population std, OFR definition, output
rounding) flows from the bundled ``config.yaml``.

P12 (Bloomberg Accuracy Boundary)
---------------------------------
Pure-arithmetic primitive: the OTR/OFR pair RESOLUTION is the data
(ADR 0007's resolver writes otr_history); the yield LEVELS are
already-ingested per-CUSIP YLD_YTM_MID rows.  The primitive subtracts
the two and rolls a z-score; no Bloomberg-computed quantity is
recomputed.

P11 (honest refusal) — single-method primitive
----------------------------------------------
There is no ``method`` enum.  When no OTR window covers any date in
the display window, the response is a controlled error envelope
(``{"error": "..."}``) naming the missing data — matching curve_spread's
recoverable-failure shape.  When there IS an OTR window but no prior
window yet (the slot's first observed window — LAG is NULL), the
spread is honest-None on those dates (P5 + P6 — absence is
information, not failure).

Test seam
---------
Tests patch ``fetch_otr_ofr_yield_pair`` and ``date`` at this module's
namespace (``...otr_ofr_spread.compute.X``) — same pattern as
yield_levels / curve_spread.  The package ``__init__.py`` re-exports
only the public symbols; test seams must target this module
directly.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
from sqlalchemy.engine import Engine

from rates_agent.sovereign_bonds.tools.otr_ofr_spread.schemas import (
    OtrOfrSpreadCurrentMetrics,
    OtrOfrSpreadInput,
    OtrOfrSpreadOutput,
    OtrOfrSpreadTimeSeriesRow,
)
from shared.analytics.rates_fetch import fetch_otr_ofr_yield_pair
from shared.analytics.spreads import rolling_zscore, safe_float
from shared.config import ToolConfig, load_tool_config
from shared.schemas import TimeSeries, TimeSeriesRow, TimeSeriesUnits


# Bundled config — relative to this file.  Public symbol so external
# callers (mcp_server, REST routes, tests, parity-fixture capture)
# can build their own ToolConfig from the same source.
CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


# Per PR14: ``ofr_definition`` is a categorical convention whose
# accepted-value set is wire-frozen in V1.  The methodology.planned_extensions
# block documents the path to widening this.
_SUPPORTED_OFR_DEFINITION: str = "prior_otr_window"

# Per PR14: ``trailing_range_window_days`` is embedded into the
# output field names (``high_252d_bps``, ``low_252d_bps``,
# ``percentile_252d``).  Changing the YAML value without renaming the
# wire fields would silently lie about what window the percentile is
# computed against.  The guard fires at compute() entry so an editor
# of config.yaml gets a clear error rather than a silently mislabelled
# output.  Same pattern as yield_levels' ``_FROZEN_TRAILING_WINDOW``.
_FROZEN_TRAILING_WINDOW: int = 252


# Per PR10: methodology_note surfaces TD #27 at the user-facing layer.
# Constant string (string literal, unchanged across calls); keeping it
# here in code rather than in YAML matches PR7's "mathematical truths
# / structural constants in code" — the YAML disclosure lives in
# methodology.assumptions + methodology.citations + planned_extensions;
# this constant is the runtime user-facing echo of those.
_METHODOLOGY_NOTE: str = (
    "Source: macro_data.otr_history (ADR 0003) JOIN macro_data."
    "market_data_daily; OTR/OFR pair resolution per the on-the-run "
    "resolver (ADR 0007).  Forward-only ingest: pre-resolver OTR "
    "windows are NOT reconstructed — queries that pre-date the "
    "resolver's first run return an error envelope, never a "
    "fabricated spread.  Detection-date precision: a window's "
    "effective_from is the resolver's first confirmed observation, "
    "~1-2 days after the true auction date at daily incremental "
    "cadence (TD #27); roll-day spread datapoints may correspondingly "
    "lag the auction.  OFR is defined as the bond from the SCD2 "
    "window IMMEDIATELY PRIOR to the window covering the trade date "
    "(LAG over effective_from); the slot's first-ever observed "
    "window has no prior bond and emits ofr_yield=None / spread=None "
    "(honest absence, not zero).  Bloomberg has no historical OTR-"
    "window screen; this primitive does not recompute what the "
    "source of record provides (P12)."
)


# ============================================================================
# CONVENTION VALIDATION
# ============================================================================

def _validate_conventions(config: ToolConfig) -> None:
    """Refuse loudly when a convention is set to a value V1 does not
    yet support (per PR14 + PR11 — categorical conventions whose
    accepted-value set is wire-frozen until a follow-up PR widens
    them).

    Raises NotImplementedError naming the offending value and pointing
    at ``methodology.planned_extensions`` in ``config.yaml``.
    """
    ofr_def = config.convention_value("ofr_definition")
    if ofr_def != _SUPPORTED_OFR_DEFINITION:
        raise NotImplementedError(
            f"ofr_definition={ofr_def!r} is documented in this tool's "
            f"config.yaml as a future-supported value (see "
            f"methodology.planned_extensions) but is not yet "
            f"implemented.  V1 supports only "
            f"{_SUPPORTED_OFR_DEFINITION!r}.  Either restore the value "
            f"or implement the new branch in "
            f"shared.analytics.rates_fetch.fetch_otr_ofr_yield_pair."
        )

    trailing = config.convention_value("trailing_range_window_days")
    if trailing != _FROZEN_TRAILING_WINDOW:
        raise NotImplementedError(
            f"trailing_range_window_days={trailing!r} is documented in this "
            f"tool's config.yaml as a future-supported value (see "
            f"methodology.planned_extensions) but is not yet implemented.  "
            f"V1 supports only {_FROZEN_TRAILING_WINDOW} because the output "
            f"field names (high_252d_bps, low_252d_bps, percentile_252d) "
            f"embed the window length.  Changing this without renaming the "
            f"wire fields would silently lie about what window the "
            f"percentile is computed against (PR14)."
        )


# ============================================================================
# PUBLIC API
# ============================================================================

def calculate_otr_ofr_spread(
    engine: Engine,
    params: OtrOfrSpreadInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    """
    Calculate the OTR/OFR yield spread, daily change, rolling z-score
    and full time-series for one (country, tenor) sovereign cash-bond
    slot.

    Parameters
    ----------
    engine : sqlalchemy.engine.Engine
        Live SQLAlchemy engine.
    params : OtrOfrSpreadInput
        Validated input.  ``country`` and ``tenor`` are the instrument
        selectors (PR1); ``lookback_days`` is the single LLM-controlled
        central methodology knob (PR8).
    config : ToolConfig, optional
        Bundled config.yaml is auto-loaded when None.  Tests pass a
        custom ToolConfig to exercise convention overrides.

    Returns
    -------
    dict
        Serialised ``OtrOfrSpreadOutput`` with current_metrics,
        time_series (bespoke), time_series_spread / time_series_zscore
        (canonical), and methodology_note.

        On insufficient data (no OTR window for the slot in the
        lookback, or no overlapping observations after alignment),
        returns a controlled ``{"error": "..."}`` envelope.  Matches
        curve_spread's recoverable-failure shape.
    """

    if config is None:
        config = load_tool_config(CONFIG_PATH)

    _validate_conventions(config)

    # ------------------------------------------------------------------
    # Pull conventions from config.  Fail fast (KeyError) if the YAML
    # is missing a key we depend on — better than silently using a
    # different default than the bundled YAML expects.
    # ------------------------------------------------------------------
    z_window = config.convention_value("z_score_window_days")
    z_min_periods = config.convention_value("z_score_min_periods")
    z_ddof = config.convention_value("z_score_ddof")
    buffer_mult = config.convention_value("z_score_buffer_multiplier")
    ffill_limit = config.convention_value("ffill_limit_days")
    spread_round = config.convention_value("spread_bps_round_decimals")
    zscore_round = config.convention_value("z_score_round_decimals")
    default_field = config.convention_value("default_field_name")

    # Resolve the field_name sentinel (None → YAML default).  Same
    # pattern as yield_levels.compute().
    field_name = params.field_name if params.field_name else default_field

    # ------------------------------------------------------------------
    # 1. Determine the date window
    # ------------------------------------------------------------------
    # Two concepts:
    #   - lookback_days:   how much *displayed* history the user wants
    #   - z_window:        fixed (252) trading-day rolling window
    #
    # Fetch extra history (warm-up buffer) so the z-score is fully
    # populated from the first displayed row.
    today = date.today()
    buffer_calendar_days = int(z_window * buffer_mult)
    window_start = today - timedelta(days=params.lookback_days + buffer_calendar_days)
    window_end = today

    # ------------------------------------------------------------------
    # 2. Fetch via the shared analytics helper.  The helper resolves
    # the OTR bond per date from otr_history and the OFR bond via
    # window-function LAG, then LEFT-JOINs market_data_daily for both
    # bonds' yields on each date.  Single source of truth for the
    # OTR/OFR resolution math.
    # ------------------------------------------------------------------
    raw_df = fetch_otr_ofr_yield_pair(
        engine=engine,
        country=params.country,
        tenor=params.tenor,
        field_name=field_name,
        window_start=window_start,
        window_end=window_end,
    )

    if raw_df.empty:
        return {
            "error": (
                f"No OTR/OFR data found for country='{params.country}', "
                f"tenor='{params.tenor}', field='{field_name}' since "
                f"{window_start.isoformat()}.  Either the OTR resolver "
                f"has not yet observed this slot (TD #27a — forward-"
                f"only ingest) or the requested window pre-dates the "
                f"resolver's first run.  Verify the slot is one the "
                f"resolver covers and that macro_data.otr_history has "
                f"a window intersecting the lookback."
            )
        }

    # ------------------------------------------------------------------
    # 3. Build the display frame
    # ------------------------------------------------------------------
    # The raw frame has one row per trade_date with the OTR and OFR
    # bonds' identity + yield columns.  Coerce types and dedupe by date.
    df = raw_df.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["otr_yield"] = pd.to_numeric(df["otr_yield"], errors="coerce")
    df["ofr_yield"] = pd.to_numeric(df["ofr_yield"], errors="coerce")
    df = df.sort_values("trade_date")
    df = df.drop_duplicates(subset=["trade_date"], keep="last")
    df = df.set_index("trade_date")

    # ---- Per-instrument forward-fill (roll-boundary correctness) ----
    # Bridging up to ffill_limit days of holiday gaps WITHIN a single
    # bond's coverage is desk-honest; bridging ACROSS a roll boundary
    # would carry the prior bond's yield onto a row whose
    # ``ofr_instrument_id`` (or ``otr_instrument_id``) is a different
    # bond and that fabricates a spread.  Grouping the ffill by the
    # row's identifier blocks that leakage so each bond's series is
    # bridged in isolation.
    #
    # ``dropna=False`` keeps the LAG-is-NULL rows (the slot's first-
    # ever observed window where ``ofr_instrument_id`` is None)
    # grouped together, ffilling within the None-id group — which is
    # a no-op because those rows have ofr_yield=None anyway.
    df["otr_yield"] = df.groupby("otr_instrument_id", dropna=False)[
        "otr_yield"
    ].transform(lambda s: s.ffill(limit=ffill_limit))
    df["ofr_yield"] = df.groupby("ofr_instrument_id", dropna=False)[
        "ofr_yield"
    ].transform(lambda s: s.ffill(limit=ffill_limit))

    # Drop rows where the OTR leg is still missing after per-instrument
    # ffill (cannot compute a spread without the OTR leg).  Rows where
    # only the OFR leg is missing keep otr_yield and emit spread=None
    # — that's honest absence for the slot's first-ever window (no
    # prior bond exists in history) or for an OFR-bond holiday that
    # exceeded ffill_limit.
    df = df.dropna(subset=["otr_yield"])

    if df.empty:
        return {
            "error": (
                f"After forward-filling holidays, no OTR yield observations "
                f"remain for country='{params.country}', "
                f"tenor='{params.tenor}'.  Holiday gaps exceed "
                f"ffill_limit_days={ffill_limit}, or the OTR bond has "
                f"no ingested yield rows in the window."
            )
        }

    # ------------------------------------------------------------------
    # 4. Compute spread_bps, rolling z-score, trailing-range stats
    # ------------------------------------------------------------------
    # spread_bps = (otr - ofr) * 100, rounded per convention.  Rows
    # where ofr_yield is None emit spread=None (Series subtraction
    # propagates NaN naturally).
    spread = (df["otr_yield"] - df["ofr_yield"]) * 100
    df["spread_bps"] = spread.round(spread_round)

    # rolling_zscore handles NaN gaps internally — gaps reduce the
    # effective rolling window size but do not corrupt the stat.
    df["z_score"] = rolling_zscore(
        df["spread_bps"],
        window=z_window,
        min_periods=z_min_periods,
        ddof=z_ddof,
        round_decimals=zscore_round,
    )

    # Trailing-range stats: rolling max / min / percentile-rank over
    # the trailing _FROZEN_TRAILING_WINDOW (252) trading days.  Window
    # length is wire-frozen in the output field names per PR14; the
    # convention guard above ensures the YAML can't drift the window
    # without renaming the wire fields.  ``min_periods`` reuses
    # z_score_min_periods so all three trailing stats warm up together
    # — a row that has a z-score has high/low/percentile too, and a
    # row in z-score warmup has those as None.
    rolling = df["spread_bps"].rolling(
        window=_FROZEN_TRAILING_WINDOW, min_periods=z_min_periods,
    )
    df["high_252d_bps"] = rolling.max().round(spread_round)
    df["low_252d_bps"] = rolling.min().round(spread_round)
    # Percentile rank — position of the current spread within the
    # trailing range, in [0, 100].  Uses pandas' rolling-rank where
    # available; falls back to a manual computation for compatibility.
    df["percentile_252d"] = (
        rolling.apply(
            lambda s: _percentile_rank(s),
            raw=True,
        )
        .round(2)
    )

    # ------------------------------------------------------------------
    # 5. Trim to the requested display window (discard warm-up rows)
    # ------------------------------------------------------------------
    cutoff = pd.Timestamp(today - timedelta(days=params.lookback_days))
    display_df = df.loc[df.index >= cutoff].copy()

    if display_df.empty:
        return {
            "error": (
                f"No OTR observations within the last "
                f"{params.lookback_days} days for "
                f"country='{params.country}', tenor='{params.tenor}'."
            )
        }

    # ------------------------------------------------------------------
    # 6. Build current_metrics
    # ------------------------------------------------------------------
    latest = display_df.iloc[-1]
    previous = display_df.iloc[-2] if len(display_df) >= 2 else None

    current_spread = safe_float(latest["spread_bps"])

    # Daily change requires both the latest AND the previous spread to
    # be non-None.  None on either side → None change (honest absence).
    if previous is not None:
        latest_spread_raw = latest["spread_bps"]
        prev_spread_raw = previous["spread_bps"]
        if pd.notna(latest_spread_raw) and pd.notna(prev_spread_raw):
            daily_change = safe_float(
                round(float(latest_spread_raw) - float(prev_spread_raw), spread_round)
            )
        else:
            daily_change = None
    else:
        daily_change = None

    slot_label = f"{params.country} {params.tenor} OTR/OFR"

    metrics = OtrOfrSpreadCurrentMetrics(
        as_of_date=latest.name.strftime("%Y-%m-%d"),
        country=params.country,
        tenor=params.tenor,
        slot_label=slot_label,
        current_spread_bps=current_spread,
        daily_change_bps=daily_change,
        current_z_score=safe_float(latest.get("z_score")),
        rolling_window_days=z_window,
        high_252d_bps=safe_float(latest.get("high_252d_bps")),
        low_252d_bps=safe_float(latest.get("low_252d_bps")),
        percentile_252d=safe_float(latest.get("percentile_252d"), decimals=2),
        otr_yield_pct=safe_float(latest.get("otr_yield")),
        ofr_yield_pct=safe_float(latest.get("ofr_yield")),
        otr_instrument_id=_optional_int(latest.get("otr_instrument_id")),
        otr_cusip=_str_or_none(latest.get("otr_cusip")),
        otr_isin=_str_or_none(latest.get("otr_isin")),
        otr_vendor_ticker=_str_or_none(latest.get("otr_vendor_ticker")),
        ofr_instrument_id=_optional_int(latest.get("ofr_instrument_id")),
        ofr_cusip=_str_or_none(latest.get("ofr_cusip")),
        ofr_isin=_str_or_none(latest.get("ofr_isin")),
        ofr_vendor_ticker=_str_or_none(latest.get("ofr_vendor_ticker")),
        observation_count=len(display_df),
    )

    # ------------------------------------------------------------------
    # 7. Build time_series (bespoke wire shape) AND the two canonical
    # TimeSeries (time_series_spread units=BPS, time_series_zscore
    # units=Z_SCORE) for the upcoming primitive-to-operator bridge.
    # All three are built from the same display_df rows so they
    # cannot drift.
    # ------------------------------------------------------------------
    ts_rows = [
        OtrOfrSpreadTimeSeriesRow(
            date=row.Index.strftime("%Y-%m-%d"),
            spread_bps=safe_float(row.spread_bps),
            z_score=safe_float(row.z_score),
            otr_yield_pct=safe_float(row.otr_yield),
            ofr_yield_pct=safe_float(row.ofr_yield),
        )
        for row in display_df.itertuples()
    ]

    canonical_spread = _build_canonical_spread_series(
        display_df,
        country=params.country,
        tenor=params.tenor,
        spread_round=spread_round,
    )
    canonical_zscore = _build_canonical_zscore_series(
        display_df,
        country=params.country,
        tenor=params.tenor,
    )

    output = OtrOfrSpreadOutput(
        current_metrics=metrics,
        time_series=ts_rows,
        time_series_spread=canonical_spread,
        time_series_zscore=canonical_zscore,
        methodology_note=_METHODOLOGY_NOTE,
    )
    return output.model_dump()


# ============================================================================
# CANONICAL TIMESERIES BUILDERS — wire-format honesty (PR14)
# ============================================================================

def _build_canonical_spread_series(
    display_df: pd.DataFrame,
    *,
    country: str,
    tenor: str,
    spread_round: int,
) -> TimeSeries:
    """Convert the display DataFrame's spread column into the canonical
    ``TimeSeries`` shape (closed-enum BPS units).

    Naming convention: ``<country_lower>_<tenor_lower>_otr_ofr_spread``.
    """
    series_name = (
        f"{country.lower()}_{tenor.lower()}_otr_ofr_spread"
    )
    rows = [
        TimeSeriesRow(
            date=row.Index.strftime("%Y-%m-%d"),
            value=(
                None
                if pd.isna(row.spread_bps)
                else round(float(row.spread_bps), spread_round)
            ),
        )
        for row in display_df.itertuples()
    ]
    return TimeSeries(
        series_name=series_name,
        units=TimeSeriesUnits.BPS,
        description=(
            f"OTR/OFR yield spread for the {country} {tenor} sovereign "
            f"cash-bond slot (otr − ofr, in bps) over the displayed window."
        ),
        rows=rows,
    )


def _build_canonical_zscore_series(
    display_df: pd.DataFrame,
    *,
    country: str,
    tenor: str,
) -> TimeSeries:
    """Convert the display DataFrame's z_score column into the canonical
    ``TimeSeries`` shape (closed-enum Z_SCORE units).

    Naming convention: ``<country_lower>_<tenor_lower>_otr_ofr_zscore``.
    Z-score values are pre-rounded by ``rolling_zscore`` upstream; rows
    where the rolling window has not warmed up are emitted as ``None``
    so the canonical shape matches the bespoke time_series 1-to-1.
    """
    series_name = (
        f"{country.lower()}_{tenor.lower()}_otr_ofr_zscore"
    )
    rows = [
        TimeSeriesRow(
            date=row.Index.strftime("%Y-%m-%d"),
            value=safe_float(row.z_score),
        )
        for row in display_df.itertuples()
    ]
    return TimeSeries(
        series_name=series_name,
        units=TimeSeriesUnits.Z_SCORE,
        description=(
            f"Rolling z-score of the {country} {tenor} OTR/OFR yield "
            f"spread vs its own trailing 252-trading-day window."
        ),
        rows=rows,
    )


def _optional_int(value: Any) -> Optional[int]:
    """Coerce a nullable numeric to ``Optional[int]``.

    ``raw_df`` is sourced from a SQL fetch — integer FK columns come
    through as ``numpy.int64`` typically, but None / NaN can appear
    on the OFR leg when the SCD2 LAG was NULL (the slot's very first
    observed window).  Coerce None/NaN to None so the wire shape is
    honest.
    """
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        # pd.isna may not accept some types; fall through to try int()
        pass
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _str_or_none(value: Any) -> Optional[str]:
    """Coerce nullable identifier-string columns (CUSIP, ISIN,
    vendor_ticker) to ``Optional[str]``.  Empty strings and NaN are
    coerced to None — they reach the primitive only if instrument_master
    has a malformed row, and the right shape on the wire is honest
    absence.
    """
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    s = str(value).strip()
    return s if s else None


def _percentile_rank(window_values) -> float:
    """Return the percentile rank of the LAST value in ``window_values``
    within the trailing window, in ``[0.0, 100.0]``.

    Computed as ``rank(last) / count`` × 100 with ties counted as
    "at-or-below" — matching pandas' default ``method='average'``
    behaviour but anchored at the trailing-window's last element.
    NaN entries are excluded from both the rank and the count so a
    short warmup window does not artificially compress the rank.

    Returns ``nan`` when the window has fewer than 2 non-NaN values
    (rank is undefined).  ``rolling().apply`` honours min_periods so
    this branch fires only on degenerate windows where the spread is
    None for almost every row.
    """
    import numpy as np
    valid = window_values[~np.isnan(window_values)]
    if len(valid) < 2:
        return float("nan")
    current = window_values[-1]
    if np.isnan(current):
        return float("nan")
    # at-or-below count (inclusive of current) gives the canonical
    # percentile-rank in [0, 100].
    at_or_below = int((valid <= current).sum())
    return 100.0 * at_or_below / len(valid)
