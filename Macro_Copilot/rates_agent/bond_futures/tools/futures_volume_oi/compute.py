"""
compute.py — Config-driven bond-futures volume + open-interest monitor (V1)
============================================================================

Daily volume + open-interest + ΔOI + 252-day OI z-score for one
``(curve_family, contract_code)`` rolling-generic pair. Second
primitive under the ``bond_futures`` domain (ADR 0013 — V1 monitors-
only; mirrors the ``futures_price_level`` shape).

Methodology disclosure
----------------------
Every response carries a ``methodology_disclosure`` field with:
  - the P5 / ADR 0013 caveat verbatim — "this is rolling-generic
    open interest; the per-contract underlying rolls quarterly so
    the OI series mixes contracts across rolls",
  - the EXPLICIT OI z-score lookback window per the catalog's
    methodology guardrail (catalog entry
    ``bond_futures__futures_volume_oi``).
The window value is read from the YAML at runtime so the disclosure
matches whatever ``oi_z_score_window_days`` is set to (the schema
makes the field required so a future caller cannot drop the
disclosure when relaying the snapshot).

Honest placeholders
-------------------
``oi_trailing_range_window_days`` is locked at 252 in V1. The output
schema's field names (``oi_high_252d``, ``oi_low_252d``,
``oi_percentile_252d``) embed the number; changing the convention
without renaming the wire fields would silently lie about what the
percentile is computed against. The compute path raises
``NotImplementedError`` if this is set to anything else.

``volume_avg_window_days`` is locked at 22 in V1. The output schema's
field names (``volume_rolling_mean_22d``, ``volume_rolling_max_22d``)
embed the number; same wire-freeze guard. See
``methodology.planned_extensions`` in the YAML for the path to making
either configurable.

DB access
---------
Reaches the DB through the shared
``fetch_rolling_generic_series`` + ``fetch_rolling_generic_reference``
helpers — bond-futures rolling-generics cannot use the standard
``fetch_single_tenor(..., contract_code=)`` path because the enriched
view's ``contract_code`` column COALESCEs the SCD2 history's per-
window underlying contract (TYH6 / TYM6 / ...) onto the master stem,
so filtering by ``contract_code = 'TY1'`` returns ZERO rows on the
view. The helpers join ``market_data_daily`` to ``instrument_master``
directly and filter on the master stem.

The existing ``fetch_rolling_generic_series`` already accepts a
``field_name`` parameter and is documented to support
``PX_LAST`` / ``OPEN_INT`` / ``PX_VOLUME`` (see the helper's
docstring L873-875). No new fetcher shape is required for this
primitive — the volume + OI series are fetched via two separate
helper calls with the YAML-owned mnemonics.

Test seam
---------
``fetch_rolling_generic_series``, ``fetch_rolling_generic_reference``,
and ``date`` are imported here at module level; tests patch them via
``patch("rates_agent.bond_futures.tools.futures_volume_oi.compute.X")``.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
from sqlalchemy.engine import Engine

from rates_agent.bond_futures.tools.futures_volume_oi.schemas import (
    FuturesVolumeOICurrentMetrics,
    FuturesVolumeOIInput,
    FuturesVolumeOIOutput,
    FuturesVolumeOITimeSeriesRow,
)
from shared.analytics.levels import (
    clean_single_series,
    trailing_high_low_percentile,
)
from shared.analytics.rates_fetch import (
    fetch_rolling_generic_reference,
    fetch_rolling_generic_series,
)
from shared.analytics.spreads import rolling_zscore, safe_float
from shared.config import ToolConfig, load_tool_config
from shared.schemas import TimeSeries, TimeSeriesRow, TimeSeriesUnits


# Bundled config — public symbol so external callers (mcp_server,
# tests, future REST routes) can build a ToolConfig from the same
# source the tool uses.
CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


# Wire-frozen window numbers in V1; see schemas.py and config.yaml's
# ``planned_extensions``. These guards fire at the convention layer so
# an editor of config.yaml gets a clear error rather than producing
# stats labelled "252d" / "22d" against a different window.
_FROZEN_OI_TRAILING_WINDOW: int = 252
_FROZEN_VOLUME_AVG_WINDOW: int = 22


# P5 / ADR 0013 disclosure template — emitted on every response so the
# consumer cannot relay the snapshot without the rolling-generic OI
# caveat. The OI z-score lookback is injected at runtime (catalog
# methodology guardrail: "Methodology card must state the OI z-score
# lookback window explicitly").
_METHODOLOGY_DISCLOSURE_TEMPLATE: str = (
    "OI z-score lookback = {oi_window} trading days. This is rolling-"
    "generic open interest; the front-back OI migration is the "
    "positioning signal but the per-contract underlying rolls "
    "quarterly so the OI series mixes contracts across rolls (ADR "
    "0013 — bond_futures V1 ships monitors only). The CTD "
    "identification, gross/net basis, implied repo, and DV01-weighted "
    "RV stack are Phase-4 work gated on D-repo + D-deliverable data "
    "ingestion."
)


def _build_methodology_disclosure(oi_window: int) -> str:
    """Compose the disclosure string with the runtime OI z-score window
    so consumers see the exact lookback that produced the snapshot
    (catalog methodology guardrail)."""
    return _METHODOLOGY_DISCLOSURE_TEMPLATE.format(oi_window=oi_window)


# ============================================================================
# WIRE-FROZEN WINDOW GUARDS
# ============================================================================

def _check_wire_frozen_windows(config: ToolConfig) -> tuple[int, int]:
    """Verify ``oi_trailing_range_window_days`` and
    ``volume_avg_window_days`` are wire-frozen values. Raises
    ``NotImplementedError`` otherwise — see module docstring."""
    oi_trailing = config.convention_value("oi_trailing_range_window_days")
    if oi_trailing != _FROZEN_OI_TRAILING_WINDOW:
        raise NotImplementedError(
            f"oi_trailing_range_window_days={oi_trailing!r} is documented in "
            f"this tool's config.yaml as a future-supported value (see "
            f"methodology.planned_extensions) but is not yet implemented. "
            f"V1 supports only {_FROZEN_OI_TRAILING_WINDOW} because the "
            f"output field names (oi_high_252d, oi_low_252d, "
            f"oi_percentile_252d) embed that number on the wire. Either "
            f"restore the value to {_FROZEN_OI_TRAILING_WINDOW} or "
            f"implement the schema rename + frontend update documented "
            f"in planned_extensions."
        )
    vol_window = config.convention_value("volume_avg_window_days")
    if vol_window != _FROZEN_VOLUME_AVG_WINDOW:
        raise NotImplementedError(
            f"volume_avg_window_days={vol_window!r} is documented in this "
            f"tool's config.yaml as a future-supported value (see "
            f"methodology.planned_extensions) but is not yet implemented. "
            f"V1 supports only {_FROZEN_VOLUME_AVG_WINDOW} because the "
            f"output field names (volume_rolling_mean_22d, "
            f"volume_rolling_max_22d) embed that number on the wire. "
            f"Either restore the value to {_FROZEN_VOLUME_AVG_WINDOW} or "
            f"implement the schema rename + frontend update documented in "
            f"planned_extensions."
        )
    return oi_trailing, vol_window


# ============================================================================
# PUBLIC API
# ============================================================================

def calculate_futures_volume_oi(
    engine: Engine,
    params: FuturesVolumeOIInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    """Return current rolling-generic bond-futures volume + open-
    interest snapshot + 1-day ΔOI + 252-day OI z-score + trailing
    high/low/percentile + 22-day volume rolling mean/max +
    observation_count for one ``(curve_family, contract_code)`` pair,
    plus the per-contract disclosure block (contract_size,
    expiry_date, security_name) and the P5 / ADR 0013 methodology
    disclosure (which includes the explicit OI z-score lookback per
    the catalog's methodology guardrail).

    Parameters
    ----------
    engine : Engine
        Live SQLAlchemy engine.
    params : FuturesVolumeOIInput
        Validated input.
    config : ToolConfig, optional
        Bundled config.yaml is auto-loaded when None. Tests pass a
        custom ToolConfig to exercise convention overrides.

    Returns
    -------
    dict
        Serialised ``FuturesVolumeOIOutput``, or ``{"error": "..."}``
        on recoverable failure.
    """
    if config is None:
        config = load_tool_config(CONFIG_PATH)

    # ------------------------------------------------------------------
    # Pull conventions
    # ------------------------------------------------------------------
    z_window = config.convention_value("oi_z_score_window_days")
    z_min_periods = config.convention_value("oi_z_score_min_periods")
    z_ddof = config.convention_value("oi_z_score_ddof")
    buffer_mult = config.convention_value("oi_z_score_buffer_multiplier")
    delta_oi_offset = config.convention_value("delta_oi_offset_rows")
    ffill_limit = config.convention_value("ffill_limit_days")
    volume_field = config.convention_value("default_volume_field")
    oi_field = config.convention_value("default_open_interest_field")
    oi_round_decimals = config.convention_value("oi_round_decimals")
    volume_round_decimals = config.convention_value("volume_round_decimals")
    oi_z_score_round_decimals = config.convention_value(
        "oi_z_score_round_decimals"
    )
    oi_high_low_round_decimals = config.convention_value(
        "oi_high_low_round_decimals"
    )
    volume_avg_round_decimals = config.convention_value(
        "volume_avg_round_decimals"
    )
    oi_trailing_window, volume_avg_window = _check_wire_frozen_windows(config)

    # ------------------------------------------------------------------
    # 1. Date window + as-of anchor
    #
    # ``as_of_date`` upper-bounds the read so the snapshot is a
    # historical, replayable view.  For rolling-generic stems the anchor
    # is resolved directly from the LLM input (or wall-clock when None),
    # NOT via ``latest_trade_date`` — that probe filters the enriched
    # view by (curve_family, tenor) and cannot scope a single rolling
    # stem (the view's ``contract_code`` COALESCEs the per-window
    # underlying onto the master stem; see the module docstring + the
    # sibling ``scan_bond_futures_extremes`` anchor pattern).  When
    # ``as_of_date`` is None the anchor is ``date.today()`` and
    # ``end_date=anchor`` drops zero rows (futures data is never in the
    # future), so behaviour is byte-identical to the pre-as-of build.
    # The output's ``as_of_date`` is still resolved post-fetch from the
    # data's latest aligned observation (``volume.index[-1]``), so a
    # stale feed is reported honestly regardless of the anchor.
    # ------------------------------------------------------------------
    anchor = params.as_of_date or date.today()
    buffer_calendar_days = int(z_window * buffer_mult)
    start_date = anchor - timedelta(
        days=params.lookback_days + buffer_calendar_days
    )

    # ------------------------------------------------------------------
    # 2. Fetch reference metadata
    # ------------------------------------------------------------------
    reference = fetch_rolling_generic_reference(
        engine=engine,
        curve_family=params.curve_family,
        contract_code=params.contract_code,
        end_date=anchor,
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
    # EUR_SHORT_RATE_FUT) here — those have NULL ``tenor`` on
    # instrument_master and belong to the policy_futures agent.
    if reference.get("tenor") is None:
        return {
            "error": (
                f"{params.curve_family} {params.contract_code} has no "
                "tenor on instrument_master — this looks like a "
                "policy-futures (strip-position-keyed) contract, which "
                "belongs to the policy_futures domain agent per ADR "
                "0013. Route SFR / ER / SFI requests there. The "
                "bond_futures monitor is for sovereign-bond futures "
                "only (UST_FUT / DE_FUT / UK_FUT / JP_FUT / FR_FUT / "
                "IT_FUT / ES_FUT / CA_FUT / AU_FUT)."
            )
        }

    # ------------------------------------------------------------------
    # 3. Fetch volume + open_interest series
    # ------------------------------------------------------------------
    raw_volume_df = fetch_rolling_generic_series(
        engine=engine,
        curve_family=params.curve_family,
        contract_code=params.contract_code,
        field_name=volume_field,
        start_date=start_date,
        end_date=anchor,
    )
    raw_oi_df = fetch_rolling_generic_series(
        engine=engine,
        curve_family=params.curve_family,
        contract_code=params.contract_code,
        field_name=oi_field,
        start_date=start_date,
        end_date=anchor,
    )

    if raw_volume_df.empty:
        return {
            "error": (
                f"No bond-futures volume data found for "
                f"curve_family='{params.curve_family}', "
                f"contract_code='{params.contract_code}', "
                f"field='{volume_field}' since "
                f"{start_date.isoformat()}. Please verify the "
                "rolling-generic has ingested PX_VOLUME observations."
            )
        }
    if raw_oi_df.empty:
        return {
            "error": (
                f"No bond-futures open-interest data found for "
                f"curve_family='{params.curve_family}', "
                f"contract_code='{params.contract_code}', "
                f"field='{oi_field}' since "
                f"{start_date.isoformat()}. Please verify the "
                "rolling-generic has ingested OPEN_INT observations."
            )
        }

    # ------------------------------------------------------------------
    # 4. Clean (ffill, dedupe, coerce numeric)
    # ------------------------------------------------------------------
    clean_volume_df = clean_single_series(raw_volume_df, ffill_limit=ffill_limit)
    clean_oi_df = clean_single_series(raw_oi_df, ffill_limit=ffill_limit)
    if clean_volume_df.empty or clean_oi_df.empty:
        return {
            "error": (
                f"All values were null after cleaning for "
                f"{params.curve_family} {params.contract_code} "
                f"(volume_empty={clean_volume_df.empty}, "
                f"oi_empty={clean_oi_df.empty})."
            )
        }

    volume = clean_volume_df["field_value"]
    open_interest = clean_oi_df["field_value"]

    # ------------------------------------------------------------------
    # 5. Align on the INTERSECTION of trading dates.
    #
    # Rationale: volume and open_interest are both fetched from
    # market_data_daily for the same instrument, so they should agree
    # on trading days. But the snapshot must not show a current_volume
    # paired with a stale open_interest reading (or vice versa); the
    # honest as_of_date is the most recent day where BOTH series have
    # a value after cleaning. Use index.intersection (set semantics)
    # then re-sort to restore chronological order.
    # ------------------------------------------------------------------
    common_idx = volume.index.intersection(open_interest.index).sort_values()
    if len(common_idx) == 0:
        return {
            "error": (
                f"No overlapping trading days between volume and OI "
                f"series for {params.curve_family} {params.contract_code}."
            )
        }
    volume = volume.loc[common_idx]
    open_interest = open_interest.loc[common_idx]

    # ------------------------------------------------------------------
    # 6. OI metrics
    # ------------------------------------------------------------------
    z_series = rolling_zscore(
        open_interest,
        window=z_window,
        min_periods=z_min_periods,
        ddof=z_ddof,
        round_decimals=oi_z_score_round_decimals,
    )
    oi_z_score = safe_float(z_series.iloc[-1], decimals=oi_z_score_round_decimals)

    # ΔOI = current OI - OI N rows back, in CONTRACTS (raw subtraction;
    # NOT a *100 multiplication that would lie about the unit).
    delta_oi_1d: Optional[float]
    if len(open_interest) >= delta_oi_offset:
        current_oi_raw = open_interest.iloc[-1]
        prior_oi_raw = open_interest.iloc[-delta_oi_offset]
        if pd.isna(current_oi_raw) or pd.isna(prior_oi_raw):
            delta_oi_1d = None
        else:
            delta_oi_1d = round(
                float(current_oi_raw) - float(prior_oi_raw),
                oi_round_decimals,
            )
    else:
        delta_oi_1d = None

    oi_high, oi_low, oi_percentile = trailing_high_low_percentile(
        open_interest,
        window=oi_trailing_window,
        decimals=oi_high_low_round_decimals,
    )

    current_oi = safe_float(
        open_interest.iloc[-1], decimals=oi_round_decimals,
    )

    # ------------------------------------------------------------------
    # 7. Volume metrics — short-window rolling mean / max.
    # ------------------------------------------------------------------
    current_volume = safe_float(
        volume.iloc[-1], decimals=volume_round_decimals,
    )
    if len(volume) >= 1:
        vol_rolling = volume.rolling(window=volume_avg_window, min_periods=1)
        volume_rolling_mean_value = vol_rolling.mean().iloc[-1]
        volume_rolling_max_value = vol_rolling.max().iloc[-1]
        volume_rolling_mean = safe_float(
            volume_rolling_mean_value, decimals=volume_avg_round_decimals,
        )
        volume_rolling_max = safe_float(
            volume_rolling_max_value, decimals=volume_avg_round_decimals,
        )
    else:
        volume_rolling_mean = None
        volume_rolling_max = None

    # ------------------------------------------------------------------
    # 8. Observation count is the LOOKBACK_DAYS-window count of ALIGNED
    #    trading days, NOT the full series length. Anchor the cutoff to
    #    the data's latest aligned observation date (NOT date.today())
    #    — futures data can be 1-3 days stale over weekends / holidays;
    #    anchoring to wall-clock would include inconsistent history
    #    depending on when the tool runs. Matches the ois rate_level /
    #    futures_price_level pattern.
    # ------------------------------------------------------------------
    as_of_date = volume.index[-1].date()
    cutoff = pd.Timestamp(as_of_date - timedelta(days=params.lookback_days))
    display_volume = volume.loc[volume.index >= cutoff]
    display_oi = open_interest.loc[open_interest.index >= cutoff]
    obs_count = len(display_volume)

    if obs_count == 0:
        return {
            "error": (
                f"No observations within the last {params.lookback_days} days "
                f"for {params.curve_family} {params.contract_code}."
            )
        }

    # ------------------------------------------------------------------
    # 9. Build output (snapshot)
    # ------------------------------------------------------------------
    expiry_raw = reference.get("expiry_date")
    expiry_str: Optional[str]
    if expiry_raw is None:
        expiry_str = None
    elif isinstance(expiry_raw, str):
        expiry_str = expiry_raw
    else:
        expiry_str = expiry_raw.strftime("%Y-%m-%d")

    metrics = FuturesVolumeOICurrentMetrics(
        as_of_date=as_of_date.strftime("%Y-%m-%d"),
        curve_family=params.curve_family,
        contract_code=params.contract_code,
        tenor=reference["tenor"],
        contract_size=reference.get("contract_size"),
        expiry_date=expiry_str,
        security_name=reference.get("security_name"),
        current_volume=current_volume,
        current_open_interest=current_oi,
        delta_open_interest_1d=delta_oi_1d,
        oi_z_score=oi_z_score,
        oi_high_252d=oi_high,
        oi_low_252d=oi_low,
        oi_percentile_252d=oi_percentile,
        volume_rolling_mean_22d=volume_rolling_mean,
        volume_rolling_max_22d=volume_rolling_max,
        observation_count=obs_count,
    )

    # ------------------------------------------------------------------
    # 10. Bespoke time_series (NOT canonical TimeSeries — see schemas
    #     module docstring). Built from the SAME ``display_volume`` /
    #     ``display_oi`` slices the snapshot was computed against,
    #     rounded with the SAME ``volume_round_decimals`` /
    #     ``oi_round_decimals`` conventions, so
    #     ``current_metrics.current_volume`` equals
    #     ``time_series[-1].volume`` and
    #     ``current_metrics.current_open_interest`` equals
    #     ``time_series[-1].open_interest`` STRICTLY at the latest row.
    # ------------------------------------------------------------------
    time_series = _build_volume_oi_time_series(
        display_volume=display_volume,
        display_oi=display_oi,
        volume_round_decimals=volume_round_decimals,
        oi_round_decimals=oi_round_decimals,
    )
    canonical_volume = _build_canonical_contract_count_series(
        display_values=display_volume,
        facet="volume",
        facet_label="daily traded volume",
        curve_family=params.curve_family,
        contract_code=params.contract_code,
        round_decimals=volume_round_decimals,
    )
    canonical_oi = _build_canonical_contract_count_series(
        display_values=display_oi,
        facet="open_interest",
        facet_label="end-of-day open interest",
        curve_family=params.curve_family,
        contract_code=params.contract_code,
        round_decimals=oi_round_decimals,
    )

    output = FuturesVolumeOIOutput(
        current_metrics=metrics,
        time_series=time_series,
        time_series_volume=canonical_volume,
        time_series_open_interest=canonical_oi,
        methodology_disclosure=_build_methodology_disclosure(z_window),
    )
    return output.model_dump()


# ============================================================================
# BESPOKE TIME-SERIES BUILDER
# ============================================================================

def _build_volume_oi_time_series(
    *,
    display_volume: pd.Series,
    display_oi: pd.Series,
    volume_round_decimals: int,
    oi_round_decimals: int,
) -> list[FuturesVolumeOITimeSeriesRow]:
    """Convert the aligned, in-window volume + OI series into the
    bespoke per-row ``{date, volume, open_interest}`` list the schema
    expects.

    Each row is rounded with the same conventions the snapshot uses so
    ``current_metrics.current_volume`` / ``current_open_interest``
    equal ``time_series[-1].volume`` / ``open_interest`` byte-for-byte
    at the latest row. Skips rows where EITHER value is NaN (a
    defensive measure — after ``clean_single_series + ffill + intersect``
    the trailing rows are non-NaN by construction, but the helper is
    called with the aligned-but-not-revalidated slices).
    """
    rows: list[FuturesVolumeOITimeSeriesRow] = []
    for ts, vol_v in display_volume.items():
        oi_v = display_oi.loc[ts]
        if pd.isna(vol_v) or pd.isna(oi_v):
            continue
        rows.append(
            FuturesVolumeOITimeSeriesRow(
                date=ts.strftime("%Y-%m-%d"),
                volume=round(float(vol_v), volume_round_decimals),
                open_interest=round(float(oi_v), oi_round_decimals),
            )
        )
    return rows


def _build_canonical_contract_count_series(
    *,
    display_values: pd.Series,
    facet: str,
    facet_label: str,
    curve_family: str,
    contract_code: str,
    round_decimals: int,
) -> TimeSeries:
    """Build one canonical ``TimeSeries`` companion of the bespoke
    rows (closed-enum ``TimeSeriesUnits.CONTRACTS`` per ADR 0017) for
    a single count facet (``volume`` or ``open_interest``).

    Built from the SAME display slice with the SAME rounding as
    ``_build_volume_oi_time_series`` so the canonical values match the
    bespoke rows 1-to-1 ON THE VOLUME/OI-BOTH-PRESENT ROWS.  The
    intersection-align step aligns the two facets' INDICES, but a
    leading-edge row can still carry a value on one facet and a residual
    NaN on the other (cleaning/ffill leaves it NaN within the common
    index).  This single-facet series skips only its OWN facet's NaNs,
    whereas the bespoke ``time_series`` skips a row if EITHER facet is
    NaN — so on such a row this canonical series can carry one extra
    leading row the bespoke view drops.  The OVERLAPPING values are
    identical by construction; only the leading row-SET can differ.
    """
    series_name = (
        f"{curve_family.lower()}_{contract_code.lower()}_{facet}"
    )
    rows = []
    for ts, v in display_values.items():
        if pd.isna(v):
            continue
        rows.append(
            TimeSeriesRow(
                date=ts.strftime("%Y-%m-%d"),
                value=round(float(v), round_decimals),
            )
        )
    return TimeSeries(
        series_name=series_name,
        units=TimeSeriesUnits.CONTRACTS,
        description=(
            f"Rolling-generic {contract_code} {facet_label} on "
            f"{curve_family} in CONTRACTS over the displayed window "
            f"(this facet's present rows; the volume/OI indices are "
            f"intersection-aligned, so this matches the other facet "
            f"1-to-1 except on a leading row where this facet is "
            f"present and the other is still NaN)."
        ),
        rows=rows,
    )
