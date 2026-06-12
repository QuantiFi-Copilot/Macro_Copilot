"""
compute.py — Config-driven policy-futures price-level + implied-rate monitor (V1)
=================================================================================

Front-month strip-position price level + implied-rate level + 252d
range + z-score for one ``(curve_family, strip_position)`` pair. First
primitive under the new ``policy_futures`` domain (ADR 0013) — V1
monitors-only, strip-position-keyed.

Why not ``compute_level_metrics``
---------------------------------
``shared.analytics.levels.compute_level_metrics`` is the canonical
snapshot composite used by ``yield_levels`` / ``ois_rate_level`` /
``real_yield_level``. It assumes the underlying series is in PERCENT
(yield space) — internally it calls ``period_changes`` WITHOUT
``already_bps=True``, so deltas get multiplied by 100 to produce bps.
For policy-futures strip slots the series is in PRICE space
(``100 - rate``); a ``*100`` multiplication on a price delta would
silently lie about the unit. We therefore compose the lower-level
helpers (``rolling_zscore``, ``trailing_high_low_percentile``)
directly here so every delta stays in the right unit space — same
choice the sibling ``bond_futures/futures_price_level`` made for
identical reasons.

Two unit spaces, side by side
-----------------------------
The snapshot carries BOTH ``raw_price`` (futures-price space) and
``implied_rate_pct`` (PERCENT) per row, plus per-axis 1-day deltas
and trailing range. The z-score lives on the IMPLIED-RATE level
axis (not the raw-price axis) because the desk-recognised level IS
the rate; z-scoring inverse-priced raw prices would flip the sign
of every "extreme" reading relative to the rate.

The implied-rate conversion rule is driven OFF METADATA: the
per-strip ``inverse_priced`` flag from ``instrument_master.attributes``
(read via ``fetch_strip_position_reference``) decides whether
``implied_rate_pct = 100 - raw_price`` (inverse) or ``implied_rate_pct
= raw_price`` (direct). NOT a hardcoded list in compute.py (PR8 /
P6 — no hidden methodology choice in code).

Methodology disclosure
----------------------
Every response carries a ``methodology_disclosure`` field with the
P5 / ADR 0013 caveat verbatim — "rolling-generic strip read at
strip_position N; CTD-of-futures-of-OIS curves are NOT a primitive
in this build" — plus the per-strip regime label (RFR / IBOR), the
inverse-pricing rule, the implied-rate computation formula in plain
English, and the z-score lookback window. The schema makes the field
REQUIRED so a future caller cannot drop the disclosure when relaying
the snapshot.

Honest placeholder
------------------
``trailing_range_window_days`` is locked at 252 in V1. The output
schema's field names (``high_252d_implied_rate_pct``,
``low_252d_implied_rate_pct``, ``mid_252d_implied_rate_pct``,
``high_252d_raw_price``, ``low_252d_raw_price``,
``mid_252d_raw_price``, ``percentile_252d``) embed the number;
changing the convention without renaming the wire fields would
silently lie about what the percentile is computed against. The
compute path raises ``NotImplementedError`` if this is set to
anything else; see ``methodology.planned_extensions`` in the YAML
for the path to making it configurable.

Deterministic anchoring (PR8 + PR16)
------------------------------------
The input schema's ``as_of_date`` (default ``None``) anchors the
snapshot:

  - ``None`` → anchor at the universe's last observed ``trade_date``
    for the requested ``(curve_family, strip_position)`` (post-fetch
    data-max anchor — same pattern as the sibling bond_futures
    monitors).

  - explicit date BEYOND the universe's last ``trade_date`` for this
    strip → return the controlled-error envelope
    (``{"error": "no scoreable strip: as_of_date=... is beyond ..."}``).
    The future-anchor probe (``fetch_strip_position_max_date``)
    mirrors the series fetcher's filter shape so the two helpers
    agree about "what's this strip's last trading day". No silent
    re-labelling of an unbounded read as a future-anchored read.

  - explicit date WITHIN the universe range → the SQL fetch is
    anchored at this date via the fetcher's ``end_date`` parameter,
    so the snapshot is DETERMINISTIC across runs (same as_of_date +
    same DB state ⇒ same numbers).

DB access
---------
Reaches the DB through the shared ``fetch_strip_position`` (price
series), ``fetch_strip_position_reference`` (per-strip metadata
incl. ``inverse_pricing`` flag), and ``fetch_strip_position_max_date``
(future-anchor probe) helpers — single-source-of-truth (P10) for
policy-futures strip-position reads. NO raw SQL in this file.

Test seam
---------
``fetch_strip_position``, ``fetch_strip_position_reference``,
``fetch_strip_position_max_date``, and ``date`` are imported here
at module level; tests patch them via
``patch("rates_agent.policy_futures.tools.futures_price_level.compute.X")``.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

import pandas as pd
from sqlalchemy.engine import Engine

from rates_agent.policy_futures.tools.futures_price_level.schemas import (
    FuturesPriceLevelCurrentMetrics,
    FuturesPriceLevelInput,
    FuturesPriceLevelOutput,
    FuturesPriceLevelTimeSeriesRow,
)
from shared.analytics.levels import (
    clean_single_series,
    trailing_high_low_percentile,
)
from shared.analytics.rates_fetch import (
    fetch_strip_position,
    fetch_strip_position_max_date,
    fetch_strip_position_reference,
)
from shared.analytics.spreads import rolling_zscore, safe_float
from shared.config import ToolConfig, load_tool_config
from shared.schemas import TimeSeries, TimeSeriesRow, TimeSeriesUnits


# Bundled config — public symbol so external callers (mcp_server,
# tests, future REST routes) can build a ToolConfig from the same
# source the tool uses.
CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


# Trailing-range window is wire-frozen at 252 in V1; see schemas.py
# and config.yaml's ``planned_extensions``. This guard fires at the
# convention layer so an editor of config.yaml gets a clear error
# rather than producing percentiles labelled "252d" against a
# different window. Mirrors the same guard in yield_levels /
# ois rate_level / bond_futures futures_price_level.
_FROZEN_TRAILING_WINDOW: int = 252


def _build_methodology_disclosure(
    *,
    curve_family: str,
    strip_position: int,
    contract_code: str,
    inverse_priced: bool,
    short_rate_regime: str,
    z_window_days: int,
    trailing_window_days: int,
) -> str:
    """Compose the P5 / ADR 0013 disclosure string with runtime context
    so consumers see the exact regime + conversion rule that produced
    the snapshot."""
    if inverse_priced:
        rule = (
            "Inverse-priced strip — implied_rate_pct = 100 - raw_price."
        )
    else:
        rule = (
            "Direct-priced strip — implied_rate_pct = raw_price."
        )
    return (
        f"Rolling-generic strip read at {curve_family} strip_position="
        f"{strip_position} (master stem={contract_code}); the desk-"
        f"recognised level is the implied rate in PERCENT. Underlying "
        f"short-rate regime: {short_rate_regime} (RFR = compounded "
        f"daily risk-free rate; IBOR = unsecured 3M term IBOR). {rule} "
        f"Z-score lookback = {z_window_days} trading days on the "
        f"IMPLIED-RATE level series; trailing range window = "
        f"{trailing_window_days} trading days. This is NOT a CTD-of-"
        f"futures-of-OIS read; the CTD-implied-OIS curve is not yet a "
        f"primitive in this build (ADR 0013 V1 scope — policy_futures "
        f"ships strip-position-keyed monitors only)."
    )


def _parse_regime_map(csv_value: str) -> Mapping[str, str]:
    """Parse the YAML's ``short_rate_regime_map`` CSV into a dict.

    Stored as a CSV of ``KEY=VALUE`` pairs because
    ``Convention.value`` is scalar (str). Defensive against
    whitespace / trailing commas / mis-cased separators.
    """
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
    and return it. Raises ``NotImplementedError`` otherwise — see
    module docstring."""
    trailing = config.convention_value("trailing_range_window_days")
    if trailing != _FROZEN_TRAILING_WINDOW:
        raise NotImplementedError(
            f"trailing_range_window_days={trailing!r} is documented in "
            f"this tool's config.yaml as a future-supported value (see "
            f"methodology.planned_extensions) but is not yet "
            f"implemented. V1 supports only {_FROZEN_TRAILING_WINDOW} "
            f"because the output field names "
            f"(high_252d_implied_rate_pct, low_252d_implied_rate_pct, "
            f"mid_252d_implied_rate_pct, high_252d_raw_price, "
            f"low_252d_raw_price, mid_252d_raw_price, percentile_252d) "
            f"embed that number on the wire. Either restore the value "
            f"to {_FROZEN_TRAILING_WINDOW} or implement the schema "
            f"rename + frontend update documented in planned_extensions."
        )
    return trailing


# ============================================================================
# PUBLIC API
# ============================================================================

def calculate_futures_price_level(
    engine: Engine,
    params: FuturesPriceLevelInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    """Return current strip-position raw_price + implied_rate_pct +
    period changes + z-score + trailing high/low/mid/percentile +
    observation_count for one ``(curve_family, strip_position)`` pair,
    plus the per-strip disclosure block (underlying_contract_code,
    security_name, expiry_date, contract_size, tick_size, tick_value,
    inverse_priced, short_rate_regime, quote_units) and the P5
    methodology disclosure.

    Parameters
    ----------
    engine : Engine
        Live SQLAlchemy engine.
    params : FuturesPriceLevelInput
        Validated input. ``field_name=None`` resolves against the
        YAML's ``default_price_field`` convention; ``as_of_date=None``
        anchors at the universe's last observed trade_date for the
        requested strip.
    config : ToolConfig, optional
        Bundled config.yaml is auto-loaded when None. Tests pass a
        custom ToolConfig to exercise convention overrides.

    Returns
    -------
    dict
        Serialised ``FuturesPriceLevelOutput``, or ``{"error": "..."}``
        on recoverable failure.
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
    raw_price_round_decimals = config.convention_value("raw_price_round_decimals")
    implied_rate_round_decimals = config.convention_value(
        "implied_rate_round_decimals"
    )
    z_score_round_decimals = config.convention_value("z_score_round_decimals")
    raw_price_high_low_round_decimals = config.convention_value(
        "raw_price_high_low_round_decimals"
    )
    implied_rate_high_low_round_decimals = config.convention_value(
        "implied_rate_high_low_round_decimals"
    )
    percentile_round_decimals = config.convention_value(
        "percentile_round_decimals"
    )
    regime_map = _parse_regime_map(
        config.convention_value("short_rate_regime_map")
    )

    # Resolve field_name: caller's explicit value wins; None falls
    # through to the YAML default. Mirrors yield_levels /
    # bond_futures futures_price_level.
    field_name_resolved = (
        params.field_name if params.field_name is not None else default_field_name
    )

    # ------------------------------------------------------------------
    # 1. Future-anchor guard (PR8 + PR16) — when an explicit
    #    as_of_date is supplied AND lies beyond the universe's last
    #    observed trade_date for this strip, return the controlled-
    #    error envelope rather than silently re-labelling an
    #    unbounded read. Mirrors scan_bond_futures_extremes's
    #    future-anchor envelope shape. Skipped when as_of_date is
    #    None — the post-fetch data-max anchor is honest by
    #    construction.
    # ------------------------------------------------------------------
    requested_as_of: Optional[date] = params.as_of_date
    if requested_as_of is not None:
        universe_max_trade_date = fetch_strip_position_max_date(
            engine=engine,
            curve_family=params.curve_family,
            strip_position=params.strip_position,
            field_name=field_name_resolved,
        )
        if (
            universe_max_trade_date is not None
            and requested_as_of > universe_max_trade_date
        ):
            return {
                "error": (
                    f"no scoreable strip: as_of_date="
                    f"{requested_as_of.isoformat()} is beyond the "
                    f"policy-futures universe's last observed "
                    f"trade_date="
                    f"{universe_max_trade_date.isoformat()} for "
                    f"{params.curve_family} strip_position="
                    f"{params.strip_position}. The snapshot refuses "
                    "to silently re-label an unbounded read as a "
                    "future-anchored read."
                )
            }

    # ------------------------------------------------------------------
    # 2. Date window. The fetch buffer is derived from YAML:
    #    z_score_window_days * z_score_buffer_multiplier (calendar-day
    #    buffer for weekends/holidays) PLUS the LLM's lookback_days
    #    (which scopes the displayed observation_count). When
    #    as_of_date is supplied, the fetch is anchored at the
    #    requested anchor so a stray future-dated row cannot leak in;
    #    when omitted, the fetch_anchor is wall-clock today (the
    #    post-fetch data-max anchor resolves the as-of from the
    #    actual rows).
    # ------------------------------------------------------------------
    buffer_calendar_days = int(z_window * buffer_mult)
    fetch_anchor: Optional[date] = requested_as_of
    end_date_for_fetch: Optional[date] = requested_as_of
    if fetch_anchor is None:
        fetch_anchor = date.today()
    start_date = fetch_anchor - timedelta(
        days=params.lookback_days + buffer_calendar_days
    )

    # ------------------------------------------------------------------
    # 3. Fetch reference metadata + price history. Reference lookup
    #    uses the as_of-bounded SCD2 window (see
    #    fetch_strip_position_reference docstring) so the disclosed
    #    underlying_contract_code is the actual current-front
    #    contract on the as-of date — NOT a "latest effective_from"
    #    row that could be a far-future SCD2 entry on policy-futures
    #    chains where the history is pre-populated out to 2035.
    # ------------------------------------------------------------------
    reference = fetch_strip_position_reference(
        engine=engine,
        curve_family=params.curve_family,
        strip_position=params.strip_position,
        as_of_date=fetch_anchor,
    )
    if reference is None:
        return {
            "error": (
                f"No policy-futures strip slot found for "
                f"curve_family='{params.curve_family}', "
                f"strip_position={params.strip_position}. Verify the "
                "(curve_family, strip_position) pair exists on "
                "instrument_master as a strip-position-keyed rolling "
                "contract (policy_futures.yml universe)."
            )
        }

    inverse_priced_raw = reference.get("inverse_pricing")
    if inverse_priced_raw is None:
        return {
            "error": (
                f"Policy-futures strip {params.curve_family} "
                f"strip_position={params.strip_position} is missing "
                "the 'inverse_pricing' flag on "
                "instrument_master.attributes. The implied-rate "
                "conversion rule is metadata-driven (PR8 / P6 — no "
                "hidden methodology in code); a strip without this "
                "flag cannot be priced honestly. Surface this as a "
                "metadata gap to the playbook owner."
            )
        }
    inverse_priced: bool = bool(inverse_priced_raw)

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

    raw_df = fetch_strip_position(
        engine=engine,
        curve_family=params.curve_family,
        strip_position=params.strip_position,
        field_name=field_name_resolved,
        start_date=start_date,
        end_date=end_date_for_fetch,
    )

    if raw_df.empty:
        return {
            "error": (
                f"No policy-futures price data found for "
                f"curve_family='{params.curve_family}', "
                f"strip_position={params.strip_position}, "
                f"field='{field_name_resolved}' since "
                f"{start_date.isoformat()}. Please verify the strip "
                "slot has ingested PX_LAST observations."
            )
        }

    # ------------------------------------------------------------------
    # 4. Clean
    # ------------------------------------------------------------------
    clean_df = clean_single_series(raw_df, ffill_limit=ffill_limit)
    if clean_df.empty:
        return {
            "error": (
                f"All values were null after cleaning for "
                f"{params.curve_family} strip_position="
                f"{params.strip_position}."
            )
        }
    raw_prices = clean_df["field_value"]

    # ------------------------------------------------------------------
    # 5. Build the implied-rate series via the metadata-driven
    #    conversion. For inverse-priced strips: 100 - raw_price; for
    #    direct-priced: raw_price (identity). The conversion rule is
    #    set ONCE here from the metadata; per-row recomputation would
    #    just duplicate the work.
    # ------------------------------------------------------------------
    if inverse_priced:
        implied_rates = 100.0 - raw_prices
        quote_units = "100 - rate"
    else:
        implied_rates = raw_prices.copy()
        quote_units = "rate (%)"

    # ------------------------------------------------------------------
    # 6. Z-score (on the IMPLIED-RATE level — see module docstring for
    #    why the z lives on the rate axis).
    # ------------------------------------------------------------------
    z_series = rolling_zscore(
        implied_rates,
        window=z_window,
        min_periods=z_min_periods,
        ddof=z_ddof,
        round_decimals=z_score_round_decimals,
    )
    z_score = safe_float(z_series.iloc[-1], decimals=z_score_round_decimals)

    # ------------------------------------------------------------------
    # 7. 1-day raw subtractions on each axis. The implied-rate delta
    #    equals -(raw-price delta) by construction when
    #    inverse_priced, but we compute it on the implied-rate series
    #    so the direct-priced branch comes through unchanged.
    # ------------------------------------------------------------------
    daily_change_raw_price: Optional[float]
    daily_change_implied_rate_pct: Optional[float]
    if len(raw_prices) >= daily_offset_rows:
        cur_raw = raw_prices.iloc[-1]
        prev_raw = raw_prices.iloc[-daily_offset_rows]
        if pd.isna(cur_raw) or pd.isna(prev_raw):
            daily_change_raw_price = None
        else:
            daily_change_raw_price = round(
                float(cur_raw) - float(prev_raw),
                raw_price_round_decimals,
            )
        cur_rate = implied_rates.iloc[-1]
        prev_rate = implied_rates.iloc[-daily_offset_rows]
        if pd.isna(cur_rate) or pd.isna(prev_rate):
            daily_change_implied_rate_pct = None
        else:
            daily_change_implied_rate_pct = round(
                float(cur_rate) - float(prev_rate),
                implied_rate_round_decimals,
            )
    else:
        daily_change_raw_price = None
        daily_change_implied_rate_pct = None

    # ------------------------------------------------------------------
    # 8. Trailing high/low/mid + percentile. Percentile is computed on
    #    the IMPLIED-RATE axis (see schemas docstring); the raw-price
    #    high/low/mid is carried in parallel for consumers that want
    #    the price view.
    # ------------------------------------------------------------------
    rate_high, rate_low, percentile = trailing_high_low_percentile(
        implied_rates,
        window=trailing_window,
        decimals=implied_rate_high_low_round_decimals,
    )
    price_high, price_low, _ = trailing_high_low_percentile(
        raw_prices,
        window=trailing_window,
        decimals=raw_price_high_low_round_decimals,
    )
    # Percentile from trailing_high_low_percentile rounds to 1 dp by
    # default (its internal convention). Re-round to the YAML's
    # convention so an edit there propagates.
    percentile_rounded: Optional[float]
    if percentile is None:
        percentile_rounded = None
    else:
        percentile_rounded = round(float(percentile), percentile_round_decimals)

    rate_mid: Optional[float]
    if rate_high is None or rate_low is None:
        rate_mid = None
    else:
        rate_mid = round(
            (float(rate_high) + float(rate_low)) / 2.0,
            implied_rate_high_low_round_decimals,
        )
    price_mid: Optional[float]
    if price_high is None or price_low is None:
        price_mid = None
    else:
        price_mid = round(
            (float(price_high) + float(price_low)) / 2.0,
            raw_price_high_low_round_decimals,
        )

    # ------------------------------------------------------------------
    # 9. Current values (latest aligned row).
    # ------------------------------------------------------------------
    current_raw_price = safe_float(
        raw_prices.iloc[-1], decimals=raw_price_round_decimals
    )
    current_implied_rate = safe_float(
        implied_rates.iloc[-1], decimals=implied_rate_round_decimals
    )

    # ------------------------------------------------------------------
    # 10. Observation count is the LOOKBACK_DAYS-window count, NOT
    #     the full series length. Anchor the cutoff to the data's
    #     latest observation date (the post-fetch as-of resolution)
    #     so the count is deterministic given a fixed DB state.
    # ------------------------------------------------------------------
    as_of_date_resolved = raw_prices.index[-1].date()
    cutoff = pd.Timestamp(
        as_of_date_resolved - timedelta(days=params.lookback_days)
    )
    display_raw_prices = raw_prices.loc[raw_prices.index >= cutoff]
    display_implied_rates = implied_rates.loc[implied_rates.index >= cutoff]
    obs_count = len(display_raw_prices)

    if obs_count == 0:
        return {
            "error": (
                f"No observations within the last {params.lookback_days} "
                f"days for {params.curve_family} strip_position="
                f"{params.strip_position}."
            )
        }

    # ------------------------------------------------------------------
    # 11. Build output (snapshot)
    # ------------------------------------------------------------------
    expiry_raw = reference.get("expiry_date")
    expiry_str: Optional[str]
    if expiry_raw is None:
        expiry_str = None
    elif isinstance(expiry_raw, str):
        expiry_str = expiry_raw
    else:
        expiry_str = expiry_raw.strftime("%Y-%m-%d")

    contract_size_raw = reference.get("contract_size")
    tick_size_raw = reference.get("tick_size")
    tick_value_raw = reference.get("tick_value")

    methodology_disclosure = _build_methodology_disclosure(
        curve_family=params.curve_family,
        strip_position=params.strip_position,
        contract_code=reference["contract_code"],
        inverse_priced=inverse_priced,
        short_rate_regime=short_rate_regime,
        z_window_days=z_window,
        trailing_window_days=trailing_window,
    )

    metrics = FuturesPriceLevelCurrentMetrics(
        as_of_date=as_of_date_resolved.strftime("%Y-%m-%d"),
        curve_family=params.curve_family,
        strip_position=params.strip_position,
        contract_code=reference["contract_code"],
        underlying_contract_code=reference.get("underlying_contract_code"),
        security_name=reference.get("security_name"),
        expiry_date=expiry_str,
        contract_size=(
            float(contract_size_raw) if contract_size_raw is not None else None
        ),
        tick_size=(
            float(tick_size_raw) if tick_size_raw is not None else None
        ),
        tick_value=(
            float(tick_value_raw) if tick_value_raw is not None else None
        ),
        inverse_priced=inverse_priced,
        short_rate_regime=short_rate_regime,
        quote_units=quote_units,
        raw_price=current_raw_price,
        implied_rate_pct=current_implied_rate,
        daily_change_raw_price=daily_change_raw_price,
        daily_change_implied_rate_pct=daily_change_implied_rate_pct,
        z_score_implied_rate=z_score,
        high_252d_implied_rate_pct=rate_high,
        low_252d_implied_rate_pct=rate_low,
        mid_252d_implied_rate_pct=rate_mid,
        high_252d_raw_price=price_high,
        low_252d_raw_price=price_low,
        mid_252d_raw_price=price_mid,
        percentile_252d=percentile_rounded,
        observation_count=obs_count,
    )

    # ------------------------------------------------------------------
    # 12. Bespoke time_series (see schemas docstring for why two unit
    #     spaces side-by-side rather than canonical TimeSeries). Built
    #     from the SAME display slices the snapshot was computed
    #     against, rounded with the SAME conventions, so the snapshot
    #     equals ``time_series[-1]`` STRICTLY at the latest row.
    # ------------------------------------------------------------------
    time_series = _build_time_series(
        display_raw_prices=display_raw_prices,
        display_implied_rates=display_implied_rates,
        raw_price_round_decimals=raw_price_round_decimals,
        implied_rate_round_decimals=implied_rate_round_decimals,
    )
    canonical_implied_rate = _build_canonical_implied_rate(
        display_implied_rates=display_implied_rates,
        curve_family=params.curve_family,
        strip_position=params.strip_position,
        implied_rate_round_decimals=implied_rate_round_decimals,
    )

    output = FuturesPriceLevelOutput(
        current_metrics=metrics,
        time_series=time_series,
        time_series_implied_rate=canonical_implied_rate,
        methodology_disclosure=methodology_disclosure,
    )
    return output.model_dump()


# ============================================================================
# BESPOKE TIME-SERIES BUILDER
# ============================================================================

def _build_time_series(
    *,
    display_raw_prices: pd.Series,
    display_implied_rates: pd.Series,
    raw_price_round_decimals: int,
    implied_rate_round_decimals: int,
) -> list[FuturesPriceLevelTimeSeriesRow]:
    """Convert the cleaned, in-window raw-price + implied-rate series
    into the bespoke per-row ``{date, raw_price, implied_rate_pct}``
    list the schema expects.

    Each row is rounded with the same conventions the snapshot uses so
    the snapshot equals ``time_series[-1]`` byte-for-byte at the
    latest row. Skips NaN values on either axis (defensive — after
    ``clean_single_series + ffill`` the trailing rows are non-NaN by
    construction).
    """
    rows: list[FuturesPriceLevelTimeSeriesRow] = []
    # The two series share the index by construction (implied_rates is
    # derived from raw_prices). We iterate against display_raw_prices
    # and pull the implied rate by .loc to be defensive against any
    # downstream divergence.
    for ts, raw_v in display_raw_prices.items():
        if pd.isna(raw_v):
            continue
        try:
            rate_v = display_implied_rates.loc[ts]
        except KeyError:
            continue
        if pd.isna(rate_v):
            continue
        rows.append(
            FuturesPriceLevelTimeSeriesRow(
                date=ts.strftime("%Y-%m-%d"),
                raw_price=round(float(raw_v), raw_price_round_decimals),
                implied_rate_pct=round(
                    float(rate_v), implied_rate_round_decimals,
                ),
            )
        )
    return rows


def _build_canonical_implied_rate(
    *,
    display_implied_rates: pd.Series,
    curve_family: str,
    strip_position: int,
    implied_rate_round_decimals: int,
) -> TimeSeries:
    """Build the canonical ``TimeSeries`` companion of the bespoke
    rows' implied-rate facet (closed-enum ``TimeSeriesUnits.PERCENT``
    per ADR 0017).

    Built from the SAME ``display_implied_rates`` slice with the SAME
    rounding as ``_build_time_series`` so the canonical values match
    ``time_series[i].implied_rate_pct`` 1-to-1 by construction.
    """
    series_name = (
        f"{curve_family.lower()}_{strip_position}_implied_rate"
    )
    rows = []
    for ts, rate_v in display_implied_rates.items():
        if pd.isna(rate_v):
            continue
        rows.append(
            TimeSeriesRow(
                date=ts.strftime("%Y-%m-%d"),
                value=round(float(rate_v), implied_rate_round_decimals),
            )
        )
    return TimeSeries(
        series_name=series_name,
        units=TimeSeriesUnits.PERCENT,
        description=(
            f"Desk-recognised implied rate for {curve_family} strip "
            f"position {strip_position} in PERCENT (implied-rate "
            f"conversion per the strip's inverse-pricing rule), "
            f"cleaned + ffilled over the displayed window."
        ),
        rows=rows,
    )
