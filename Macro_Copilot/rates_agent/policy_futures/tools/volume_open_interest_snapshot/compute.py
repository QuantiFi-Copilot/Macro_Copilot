"""
compute.py — Config-driven policy-futures volume + open-interest snapshot (V1)
================================================================================

Daily volume + open-interest + ΔOI + 252-day OI z-score + trailing 252-day
OI range / percentile + 22-day rolling volume mean / max for one
``(curve_family, strip_position)`` pair. Second primitive under the
``policy_futures`` domain (ADR 0011 — strip-position-keyed monitors;
mirrors the bond_futures ``futures_volume_oi`` shape for the OI + volume
math but reads through the strip-position fetchers).

Why not ``compute_level_metrics``
---------------------------------
``shared.analytics.levels.compute_level_metrics`` is the canonical
snapshot composite used by ``yield_levels`` / ``ois_rate_level`` /
``real_yield_level``. It assumes the underlying series is in PERCENT
(yield space) — internally it calls ``period_changes`` WITHOUT
``already_bps=True``, so deltas get multiplied by 100 to produce bps.
For policy-futures volume + OI the series live in CONTRACT-COUNT
space; a ``*100`` multiplication on a contract delta would silently
lie about the unit. We therefore compose the lower-level helpers
(``rolling_zscore``, ``trailing_high_low_percentile``) directly here
so every delta stays in the right unit space — same choice the
sibling ``bond_futures/futures_volume_oi`` made for identical
reasons.

Methodology disclosure
----------------------
Every response carries a ``methodology_disclosure`` field with:
  - the per-``curve_family`` short-rate regime label (RFR vs IBOR) so
    a desk reader sees the underlying short-rate object behind the
    OI / volume snapshot (same wording the sibling
    ``futures_price_level`` emits — policy_futures spans both SOFR /
    SONIA RFR strips and Euribor IBOR strips),
  - the P5 / ADR 0011 caveat verbatim — "this is the rolling-generic
    strip slot's open-interest series; the per-contract underlying
    rolls quarterly so the OI series mixes contracts across rolls
    (ADR 0011 — policy_futures V1 ships strip-position-keyed monitors
    only)",
  - the EXPLICIT OI z-score lookback window per the catalog's
    standardness guardrail (catalog entry
    ``policy_futures__volume_open_interest_snapshot``).
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

Deterministic anchoring (PR8 + PR16)
------------------------------------
The input schema's ``as_of_date`` (default ``None``) anchors the
snapshot:

  - ``None`` → anchor at the universe's last observed ``trade_date``
    for the requested ``(curve_family, strip_position)`` (post-fetch
    data-max anchor — same pattern as the sibling
    futures_price_level).

  - explicit date BEYOND the universe's last ``trade_date`` for this
    strip's OPEN_INT series → return the controlled-error envelope
    (``{"error": "no scoreable strip: as_of_date=... is beyond ..."}``).
    The future-anchor probe uses ``fetch_strip_position_max_date``
    against the OI field (NOT the volume field) because the snapshot's
    headline read is the positioning extreme and the OI series is
    the primary anchor; no silent re-labelling of an unbounded read.

  - explicit date WITHIN the universe range → the SQL fetch is
    anchored at this date via the fetcher's ``end_date`` parameter,
    so the snapshot is DETERMINISTIC across runs (same as_of_date +
    same DB state ⇒ same numbers).

DB access
---------
Reaches the DB through the shared ``fetch_strip_position`` (called
twice — once for volume, once for open interest, with the YAML-owned
field mnemonics), ``fetch_strip_position_reference`` (per-strip
metadata bounded by ``as_of_date``), and ``fetch_strip_position_max_date``
(future-anchor probe) helpers — single-source-of-truth (P10) for
policy-futures strip-position reads. NO raw SQL in this file.

Test seam
---------
``fetch_strip_position``, ``fetch_strip_position_reference``,
``fetch_strip_position_max_date``, and ``date`` are imported here
at module level; tests patch them via
``patch("rates_agent.policy_futures.tools.volume_open_interest_snapshot.compute.X")``.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

import pandas as pd
from sqlalchemy.engine import Engine

from rates_agent.policy_futures.tools.volume_open_interest_snapshot.schemas import (
    VolumeOpenInterestSnapshotCurrentMetrics,
    VolumeOpenInterestSnapshotInput,
    VolumeOpenInterestSnapshotOutput,
    VolumeOpenInterestSnapshotTimeSeriesRow,
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


# P5 / ADR 0011 disclosure template — emitted on every response so the
# consumer cannot relay the snapshot without the rolling-generic-strip
# OI caveat. The per-curve_family short-rate regime label (RFR / IBOR)
# and the OI z-score lookback are injected at runtime (catalog
# standardness guardrail: "Methodology card must state the OI z-score
# lookback window explicitly"; P5 honest disclosure: a desk reader
# relaying SFR1 OI vs ER1 OI must see that a Euribor position is
# structurally different from a SOFR position).
_METHODOLOGY_DISCLOSURE_TEMPLATE: str = (
    "Underlying short-rate regime for {curve_family}: "
    "{short_rate_regime} (RFR = compounded daily risk-free rate; "
    "IBOR = unsecured 3M term IBOR). OI z-score lookback = "
    "{oi_window} trading days on the strip-slot open-interest "
    "series. This is the rolling-generic strip slot's open interest; "
    "the per-contract underlying rolls quarterly so the OI series "
    "mixes contracts across rolls (ADR 0011 — policy_futures V1 "
    "ships strip-position-keyed monitors only). Front-back OI "
    "migration as a positioning signal cannot be expressed as a "
    "single-strip-slot primitive — that is future cross-strip work."
)


def _build_methodology_disclosure(
    *,
    curve_family: str,
    short_rate_regime: str,
    oi_window: int,
) -> str:
    """Compose the disclosure string with the runtime OI z-score window
    and the per-curve_family short-rate regime label (P5)."""
    return _METHODOLOGY_DISCLOSURE_TEMPLATE.format(
        curve_family=curve_family,
        short_rate_regime=short_rate_regime,
        oi_window=oi_window,
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

def calculate_volume_open_interest_snapshot(
    engine: Engine,
    params: VolumeOpenInterestSnapshotInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    """Return current strip-position volume + open-interest snapshot +
    1-day ΔOI + 252-day OI z-score + trailing high/low/percentile +
    22-day volume rolling mean/max + observation_count for one
    ``(curve_family, strip_position)`` pair, plus the as_of-bounded
    SCD2 disclosure block (underlying_contract_code, security_name,
    expiry_date, contract_size) and the P5 / ADR 0011 methodology
    disclosure (which includes the explicit OI z-score lookback per
    the catalog's standardness guardrail).

    Parameters
    ----------
    engine : Engine
        Live SQLAlchemy engine.
    params : VolumeOpenInterestSnapshotInput
        Validated input. ``as_of_date=None`` anchors at the universe's
        last observed trade_date for the requested strip.
    config : ToolConfig, optional
        Bundled config.yaml is auto-loaded when None. Tests pass a
        custom ToolConfig to exercise convention overrides.

    Returns
    -------
    dict
        Serialised ``VolumeOpenInterestSnapshotOutput``, or
        ``{"error": "..."}`` on recoverable failure.
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

    # ------------------------------------------------------------------
    # 1. Future-anchor guard (PR8 + PR16) — when an explicit
    #    as_of_date is supplied AND lies beyond the universe's last
    #    observed trade_date for this strip's OI series, return the
    #    controlled-error envelope rather than silently re-labelling
    #    an unbounded read. The OI series is the anchor (headline
    #    positioning read) — its data-max is the right gate. Skipped
    #    when as_of_date is None — the post-fetch data-max anchor is
    #    honest by construction.
    # ------------------------------------------------------------------
    requested_as_of: Optional[date] = params.as_of_date
    if requested_as_of is not None:
        universe_max_trade_date = fetch_strip_position_max_date(
            engine=engine,
            curve_family=params.curve_family,
            strip_position=params.strip_position,
            field_name=oi_field,
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
    #    oi_z_score_window_days * oi_z_score_buffer_multiplier (calendar-
    #    day buffer for weekends/holidays) PLUS the LLM's lookback_days.
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
    # 3. Fetch reference metadata + the two series. Reference lookup
    #    uses the as_of-bounded SCD2 window so the disclosed
    #    underlying_contract_code is the actual current-front
    #    contract on the as-of date — NOT a "latest effective_from"
    #    row that could be a far-future SCD2 entry.
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

    raw_volume_df = fetch_strip_position(
        engine=engine,
        curve_family=params.curve_family,
        strip_position=params.strip_position,
        field_name=volume_field,
        start_date=start_date,
        end_date=end_date_for_fetch,
    )
    raw_oi_df = fetch_strip_position(
        engine=engine,
        curve_family=params.curve_family,
        strip_position=params.strip_position,
        field_name=oi_field,
        start_date=start_date,
        end_date=end_date_for_fetch,
    )

    if raw_volume_df.empty:
        return {
            "error": (
                f"No policy-futures volume data found for "
                f"curve_family='{params.curve_family}', "
                f"strip_position={params.strip_position}, "
                f"field='{volume_field}' since "
                f"{start_date.isoformat()}. Please verify the strip "
                "slot has ingested PX_VOLUME observations."
            )
        }
    if raw_oi_df.empty:
        return {
            "error": (
                f"No policy-futures open-interest data found for "
                f"curve_family='{params.curve_family}', "
                f"strip_position={params.strip_position}, "
                f"field='{oi_field}' since "
                f"{start_date.isoformat()}. Please verify the strip "
                "slot has ingested OPEN_INT observations."
            )
        }

    # ------------------------------------------------------------------
    # 4. Clean (ffill, dedupe, coerce numeric)
    # ------------------------------------------------------------------
    clean_volume_df = clean_single_series(
        raw_volume_df, ffill_limit=ffill_limit
    )
    clean_oi_df = clean_single_series(raw_oi_df, ffill_limit=ffill_limit)
    if clean_volume_df.empty or clean_oi_df.empty:
        return {
            "error": (
                f"All values were null after cleaning for "
                f"{params.curve_family} strip_position="
                f"{params.strip_position} "
                f"(volume_empty={clean_volume_df.empty}, "
                f"oi_empty={clean_oi_df.empty})."
            )
        }

    volume = clean_volume_df["field_value"]
    open_interest = clean_oi_df["field_value"]

    # ------------------------------------------------------------------
    # 5. Align on the INTERSECTION of trading dates.
    #
    # Rationale: volume and open_interest are both fetched from the
    # same strip slot, so they should agree on trading days. But the
    # snapshot must not show a current_volume paired with a stale
    # open_interest reading (or vice versa); the honest as_of_date
    # is the most recent day where BOTH series have a value after
    # cleaning. Use index.intersection (set semantics) then re-sort
    # to restore chronological order. Same pattern as bond_futures
    # futures_volume_oi.
    # ------------------------------------------------------------------
    common_idx = volume.index.intersection(open_interest.index).sort_values()
    if len(common_idx) == 0:
        return {
            "error": (
                f"No overlapping trading days between volume and OI "
                f"series for {params.curve_family} strip_position="
                f"{params.strip_position}."
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
    oi_z_score = safe_float(
        z_series.iloc[-1], decimals=oi_z_score_round_decimals,
    )

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
    #    trading days, NOT the full series length. Anchor the cutoff
    #    to the data's latest aligned observation date so the count is
    #    deterministic given a fixed DB state.
    # ------------------------------------------------------------------
    as_of_date_resolved = volume.index[-1].date()
    cutoff = pd.Timestamp(
        as_of_date_resolved - timedelta(days=params.lookback_days)
    )
    display_volume = volume.loc[volume.index >= cutoff]
    display_oi = open_interest.loc[open_interest.index >= cutoff]
    obs_count = len(display_volume)

    if obs_count == 0:
        return {
            "error": (
                f"No observations within the last {params.lookback_days} "
                f"days for {params.curve_family} strip_position="
                f"{params.strip_position}."
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

    contract_size_raw = reference.get("contract_size")

    metrics = VolumeOpenInterestSnapshotCurrentMetrics(
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

    output = VolumeOpenInterestSnapshotOutput(
        current_metrics=metrics,
        time_series=time_series,
        methodology_disclosure=_build_methodology_disclosure(
            curve_family=params.curve_family,
            short_rate_regime=short_rate_regime,
            oi_window=z_window,
        ),
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
) -> list[VolumeOpenInterestSnapshotTimeSeriesRow]:
    """Convert the aligned, in-window volume + OI series into the
    bespoke per-row ``{date, volume, open_interest}`` list the schema
    expects.

    Each row is rounded with the same conventions the snapshot uses so
    ``current_metrics.current_volume`` / ``current_open_interest``
    equal ``time_series[-1].volume`` / ``open_interest`` byte-for-byte
    at the latest row. Skips rows where EITHER value is NaN.
    """
    rows: list[VolumeOpenInterestSnapshotTimeSeriesRow] = []
    for ts, vol_v in display_volume.items():
        oi_v = display_oi.loc[ts]
        if pd.isna(vol_v) or pd.isna(oi_v):
            continue
        rows.append(
            VolumeOpenInterestSnapshotTimeSeriesRow(
                date=ts.strftime("%Y-%m-%d"),
                volume=round(float(vol_v), volume_round_decimals),
                open_interest=round(float(oi_v), oi_round_decimals),
            )
        )
    return rows
