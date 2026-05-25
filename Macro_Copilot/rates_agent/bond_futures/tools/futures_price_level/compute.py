"""
compute.py — Config-driven bond-futures price-level monitor (V1)
=================================================================

Front-month rolling-generic bond-futures price level + 252d range +
z-score for one ``(curve_family, contract_code)`` pair. First primitive
under the new ``bond_futures`` domain (ADR 0013) — V1 monitors-only.

Why not ``compute_level_metrics``
---------------------------------
``shared.analytics.levels.compute_level_metrics`` is the canonical
snapshot composite used by ``yield_levels`` / ``ois_rate_level`` /
``real_yield_level``. It assumes the underlying series is in PERCENT
(yield space) — internally it calls ``period_changes`` WITHOUT
``already_bps=True``, so deltas get multiplied by 100 to produce bps.
For bond-futures rolling-generics the series is PRICE (e.g. TY1 in
``points``, RX1 in ``% of par value``), and a ``*100`` multiplication
on a price delta would silently lie about the unit. We therefore
compose the lower-level helpers (``rolling_zscore``, ``period_changes``
with ``already_bps=True``, ``trailing_high_low_percentile``) directly
here so every delta stays in price space.

Methodology disclosure
----------------------
Every response carries a ``methodology_disclosure`` field with the
P5 / ADR 0013 caveat verbatim — "this is the rolling-generic price;
the CTD-implied yield is not yet a primitive in this build". The
schema makes the field REQUIRED so a future caller cannot drop the
disclosure when relaying the snapshot.

Honest placeholder
------------------
``trailing_range_window_days`` is locked at 252 in V1. The output
schema's field names (``high_252d_price``, ``low_252d_price``,
``percentile_252d``) embed the number; changing the convention
without renaming the wire fields would silently lie about what the
percentile is computed against. The compute path raises
``NotImplementedError`` if this is set to anything else; see
``methodology.planned_extensions`` in the YAML for the path to
making it configurable.

DB access
---------
Reaches the DB through the shared
``fetch_rolling_generic_series`` + ``fetch_rolling_generic_reference``
helpers — bond-futures rolling-generics cannot use the standard
``fetch_single_tenor(..., contract_code=)`` path because the enriched
view's ``contract_code`` column COALESCEs the SCD2 history's per-
window underlying contract (TYH6 / TYM6 / ...) onto the master stem,
so filtering by ``contract_code = 'TY1'`` returns ZERO rows on the
view. The new helpers join ``market_data_daily`` to
``instrument_master`` directly and filter on the master stem. See
``shared.analytics.rates_fetch`` for the helpers.

Test seam
---------
``fetch_rolling_generic_series``, ``fetch_rolling_generic_reference``,
and ``date`` are imported here at module level; tests patch them via
``patch("rates_agent.bond_futures.tools.futures_price_level.compute.X")``.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
from sqlalchemy.engine import Engine

from rates_agent.bond_futures.tools.futures_price_level.schemas import (
    FuturesPriceLevelCurrentMetrics,
    FuturesPriceLevelInput,
    FuturesPriceLevelOutput,
    FuturesPriceLevelTimeSeriesRow,
)
from shared.analytics.levels import (
    clean_single_series,
    period_changes,
    trailing_high_low_percentile,
)
from shared.analytics.rates_fetch import (
    fetch_rolling_generic_reference,
    fetch_rolling_generic_series,
)
from shared.analytics.spreads import rolling_zscore, safe_float
from shared.config import ToolConfig, load_tool_config


# Bundled config — public symbol so external callers (mcp_server,
# tests, future REST routes) can build a ToolConfig from the same
# source the tool uses.
CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


# Trailing-range window is wire-frozen at 252 in V1; see schemas.py
# and config.yaml's ``planned_extensions``. This guard fires at the
# convention layer so an editor of config.yaml gets a clear error
# rather than producing percentiles labelled "252d" against a
# different window. Mirrors the same guard in yield_levels /
# ois rate_level.
_FROZEN_TRAILING_WINDOW: int = 252


# P5 / ADR 0013 disclosure — emitted on every response so the consumer
# cannot relay the snapshot without the rolling-generic-price caveat.
# Constant rather than YAML so a typo in config can't water it down.
METHODOLOGY_DISCLOSURE: str = (
    "This is the rolling-generic price; the CTD-implied yield is not "
    "yet a primitive in this build (ADR 0013 — bond_futures V1 ships "
    "monitors only). The CTD identification, gross/net basis, implied "
    "repo, and DV01-weighted RV stack are Phase-4 work gated on "
    "D-repo + D-deliverable data ingestion."
)


# ============================================================================
# CONFIG → KWARGS RESOLVER
# ============================================================================

def _check_trailing_window(config: ToolConfig) -> int:
    """Verify ``trailing_range_window_days`` is the wire-frozen value
    and return it. Raises ``NotImplementedError`` otherwise — see
    module docstring."""
    trailing = config.convention_value("trailing_range_window_days")
    if trailing != _FROZEN_TRAILING_WINDOW:
        raise NotImplementedError(
            f"trailing_range_window_days={trailing!r} is documented in this "
            f"tool's config.yaml as a future-supported value (see "
            f"methodology.planned_extensions) but is not yet implemented. "
            f"V1 supports only {_FROZEN_TRAILING_WINDOW} because the output "
            f"field names (high_252d_price, low_252d_price, percentile_252d) "
            f"embed that number on the wire. Either restore the value to "
            f"{_FROZEN_TRAILING_WINDOW} or implement the schema rename + "
            f"frontend update documented in planned_extensions."
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
    """Return current rolling-generic bond-futures price + period
    changes + z-score + trailing high/low/percentile +
    observation_count for one ``(curve_family, contract_code)`` pair,
    plus the per-contract disclosure block (quote_units, contract_size,
    expiry_date, security_name) and the P5 methodology disclosure.

    Parameters
    ----------
    engine : Engine
        Live SQLAlchemy engine.
    params : FuturesPriceLevelInput
        Validated input. ``field_name=None`` resolves against the
        YAML's ``default_price_field`` convention.
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
    ffill_limit = config.convention_value("ffill_limit_days")
    default_field_name = config.convention_value("default_price_field")
    period_offsets = {
        "daily": config.convention_value("daily_change_offset_rows"),
        "weekly": config.convention_value("weekly_change_offset_rows"),
        "monthly": config.convention_value("monthly_change_offset_rows"),
    }
    trailing_window = _check_trailing_window(config)
    price_round_decimals = config.convention_value("price_round_decimals")
    z_score_round_decimals = config.convention_value("z_score_round_decimals")
    price_high_low_round_decimals = config.convention_value(
        "price_high_low_round_decimals"
    )

    # Resolve field_name: caller's explicit value wins; None falls
    # through to the YAML default. Mirrors yield_levels.
    field_name_resolved = (
        params.field_name if params.field_name is not None else default_field_name
    )

    # ------------------------------------------------------------------
    # 1. Date window
    # ------------------------------------------------------------------
    buffer_calendar_days = int(z_window * buffer_mult)
    start_date = date.today() - timedelta(
        days=params.lookback_days + buffer_calendar_days
    )

    # ------------------------------------------------------------------
    # 2. Fetch reference metadata + price history
    # ------------------------------------------------------------------
    reference = fetch_rolling_generic_reference(
        engine=engine,
        curve_family=params.curve_family,
        contract_code=params.contract_code,
    )
    if reference is None:
        return {
            "error": (
                f"No rolling-generic master row found for "
                f"curve_family='{params.curve_family}', "
                f"contract_code='{params.contract_code}'. Verify the "
                "stem exists on instrument_master as a rolling contract "
                "(bond_futures.yml universe)."
            )
        }

    # Guard against routing a policy-futures stem (SOFR_FUT / SONIA_FUT /
    # EUR_SHORT_RATE_FUT) here — those instruments have NULL ``tenor``
    # on instrument_master (strip-position-keyed via attributes JSONB)
    # and belong to the policy_futures domain agent per ADR 0013.
    # Refuse with a controlled error envelope rather than crashing
    # downstream in Pydantic validation when ``tenor`` reaches the
    # snapshot.
    if reference.get("tenor") is None:
        return {
            "error": (
                f"{params.curve_family} {params.contract_code} has no "
                "tenor on instrument_master — this looks like a "
                "policy-futures (strip-position-keyed) contract, which "
                "belongs to the policy_futures domain agent per ADR "
                "0011. Route SFR / ER / SFI requests there. The "
                "bond_futures monitor is for sovereign-bond futures "
                "only (UST_FUT / DE_FUT / UK_FUT / JP_FUT / FR_FUT / "
                "IT_FUT / ES_FUT / CA_FUT / AU_FUT)."
            )
        }

    raw_df = fetch_rolling_generic_series(
        engine=engine,
        curve_family=params.curve_family,
        contract_code=params.contract_code,
        field_name=field_name_resolved,
        start_date=start_date,
    )

    if raw_df.empty:
        return {
            "error": (
                f"No bond-futures price data found for "
                f"curve_family='{params.curve_family}', "
                f"contract_code='{params.contract_code}', "
                f"field='{field_name_resolved}' since "
                f"{start_date.isoformat()}. Please verify the "
                "rolling-generic has ingested PX_LAST observations."
            )
        }

    # ------------------------------------------------------------------
    # 3. Clean
    # ------------------------------------------------------------------
    clean_df = clean_single_series(raw_df, ffill_limit=ffill_limit)
    if clean_df.empty:
        return {
            "error": (
                f"All values were null after cleaning for "
                f"{params.curve_family} {params.contract_code}."
            )
        }
    prices = clean_df["field_value"]

    # ------------------------------------------------------------------
    # 4. Metrics — compose lower-level helpers directly so price-unit
    #    deltas stay in price space (NOT multiplied by 100). See module
    #    docstring for the "why not compute_level_metrics" rationale.
    # ------------------------------------------------------------------
    z_series = rolling_zscore(
        prices,
        window=z_window,
        min_periods=z_min_periods,
        ddof=z_ddof,
        round_decimals=z_score_round_decimals,
    )
    z_score = safe_float(z_series.iloc[-1], decimals=z_score_round_decimals)

    # ``already_bps=True`` = pure subtraction (current - previous), no
    # *100 multiplication. The kwarg name is unfortunate but its
    # behaviour is exactly what we need for price-space deltas.
    changes = period_changes(
        prices,
        offsets=period_offsets,
        already_bps=True,
        decimals=price_round_decimals,
    )

    high, low, percentile = trailing_high_low_percentile(
        prices,
        window=trailing_window,
        decimals=price_high_low_round_decimals,
    )

    current_price = safe_float(prices.iloc[-1], decimals=price_round_decimals)

    # ------------------------------------------------------------------
    # 5. Observation count is the LOOKBACK_DAYS-window count, NOT the
    #    full series length. Anchor the cutoff to the data's latest
    #    observation date (NOT date.today()) — bond-futures price data
    #    can be 1-3 days stale over weekends / holidays; anchoring to
    #    wall-clock would include inconsistent history depending on
    #    when the tool runs. Matches the ois rate_level pattern.
    # ------------------------------------------------------------------
    as_of_date = prices.index[-1].date()
    cutoff = pd.Timestamp(as_of_date - timedelta(days=params.lookback_days))
    display_prices = prices.loc[prices.index >= cutoff]
    obs_count = len(display_prices)

    if obs_count == 0:
        return {
            "error": (
                f"No observations within the last {params.lookback_days} days "
                f"for {params.curve_family} {params.contract_code}."
            )
        }

    # ------------------------------------------------------------------
    # 6. Build output (snapshot)
    # ------------------------------------------------------------------
    expiry_raw = reference.get("expiry_date")
    expiry_str: Optional[str]
    if expiry_raw is None:
        expiry_str = None
    elif isinstance(expiry_raw, str):
        expiry_str = expiry_raw
    else:
        expiry_str = expiry_raw.strftime("%Y-%m-%d")

    metrics = FuturesPriceLevelCurrentMetrics(
        as_of_date=as_of_date.strftime("%Y-%m-%d"),
        curve_family=params.curve_family,
        contract_code=params.contract_code,
        tenor=reference["tenor"],
        quote_units=reference.get("quote_units"),
        contract_size=reference.get("contract_size"),
        expiry_date=expiry_str,
        security_name=reference.get("security_name"),
        current_price=current_price,
        daily_change_price=changes["daily"],
        weekly_change_price=changes["weekly"],
        monthly_change_price=changes["monthly"],
        z_score=z_score,
        high_252d_price=high,
        low_252d_price=low,
        percentile_252d=percentile,
        observation_count=obs_count,
    )

    # ------------------------------------------------------------------
    # 7. Bespoke time_series (NOT canonical TimeSeries — see schemas
    #    module docstring). Built from the SAME ``display_prices``
    #    slice the snapshot was computed against, rounded with the
    #    SAME ``price_round_decimals`` convention, so
    #    ``current_metrics.current_price`` equals
    #    ``time_series[-1].price`` STRICTLY at the latest row.
    # ------------------------------------------------------------------
    time_series = _build_price_time_series(
        display_prices,
        price_round_decimals=price_round_decimals,
    )

    output = FuturesPriceLevelOutput(
        current_metrics=metrics,
        time_series=time_series,
        methodology_disclosure=METHODOLOGY_DISCLOSURE,
    )
    return output.model_dump()


# ============================================================================
# BESPOKE TIME-SERIES BUILDER
# ============================================================================

def _build_price_time_series(
    display_prices: pd.Series,
    *,
    price_round_decimals: int,
) -> list[FuturesPriceLevelTimeSeriesRow]:
    """Convert the cleaned, in-window price series into the bespoke
    per-row ``{date, price}`` list the schema expects.

    Each row is rounded with ``price_round_decimals`` so the snapshot's
    ``current_price`` equals ``time_series[-1].price`` byte-for-byte at
    the latest row. Skips NaN values (a defensive measure — after
    ``clean_single_series + ffill`` the trailing rows are non-NaN by
    construction).
    """
    rows: list[FuturesPriceLevelTimeSeriesRow] = []
    for ts, v in display_prices.items():
        if pd.isna(v):
            continue
        rows.append(
            FuturesPriceLevelTimeSeriesRow(
                date=ts.strftime("%Y-%m-%d"),
                price=round(float(v), price_round_decimals),
            )
        )
    return rows
