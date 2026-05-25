"""
compute.py — Config-driven policy-futures universe-wide extremes scan (V1)
==========================================================================

Universe-wide policy-futures strip sweep — ranks every
``(curve_family, strip_position)`` stem in the policy_futures
universe (SOFR_FUT / EUR_SHORT_RATE_FUT / SONIA_FUT × strip positions
1..8) by absolute 252-day z-score across four metrics
(implied_rate_level, 1-day implied_rate_change in BPS, volume_level,
open_interest_level). V1 monitor under ``policy_futures`` (ADR 0013 —
strip-position-keyed monitors only).

Methodology disclosure
----------------------
Every response carries a ``methodology_disclosure`` field AND every
result row carries the SAME disclosure (per the catalog's
methodology guardrail wording: "Output rows must carry the
per-contract methodology disclosure (RFR vs IBOR regime, rolling-
generic price caveat)"). The disclosure includes:

  - the universe-wide strip-scan label,
  - the explicit z-score lookback window (read from YAML at
    runtime; not hardcoded — same pattern as the bond_futures /
    inflation_swaps scanners),
  - the per-row RFR-vs-IBOR regime caveat per curve_family per ADR
    0011 (SOFR_FUT / SONIA_FUT = RFR; EUR_SHORT_RATE_FUT = IBOR),
  - the inverse-pricing rule (metadata-driven),
  - the rolling-generic strip-snapshot caveat (per-contract
    underlying rolls quarterly),
  - the explicit non-CTD / non-OIS / non-pack-average /
    non-curve-shape / non-policy-path / morning-screen-NOT-
    tactical-signal scope statement.

DB access
---------
Reaches the DB through the shared
``fetch_scan_universe_strip_position`` (market data) +
``fetch_scan_universe_policy_future_reference`` (per-stem
inverse_pricing + SCD2 metadata) +
``fetch_scan_universe_strip_position_max_date`` (future-anchor
probe) helpers from ``shared/analytics/rates_fetch.py``. All three
are NEW helpers added alongside this primitive — same fetcher-
extension precedent the inflation_swaps scanner used (commit
91a5714) when it extended ``fetch_scan_universe_reference`` with
``underlying_index``. NO raw SQL in this file.

The reference helper was chosen as a NEW per-family helper
(rather than widening ``fetch_scan_universe_reference``) because
the policy-futures reference columns (``strip_position``,
``inverse_pricing``, ``security_name``, ``expiry_date``,
``contract_size``, ``tick_size``, ``tick_value``,
``underlying_contract_code``) require a join to
``instrument_master`` + LATERAL SCD2 history — a different SQL
shape from the existing helper's pure-enriched-view DISTINCT
projection. Widening the existing helper would mean either
adding 8 columns of None to every linker / ZCIS / bond_futures
row, or branching the helper's SQL on instrument_type (a typed-
boundary smell). The new helper preserves the existing helper's
contract unchanged for the three current callers and exposes the
policy-futures shape cleanly.

Per-stem alignment + scoring
-----------------------------
1. Resolve curve_families against the YAML whitelist (full
   universe by default).
2. Fetch the three field series across the universe via three
   ``fetch_scan_universe_strip_position`` round-trips (PX_LAST /
   PX_VOLUME / OPEN_INT). Each row is keyed by
   ``(curve_family, strip_position)``.
3. Fetch the per-stem reference frame (one row per stem,
   inverse_pricing + SCD2 SCD2 metadata) via
   ``fetch_scan_universe_policy_future_reference`` (1 round-trip)
   anchored at the resolved as_of_date.
4. Refuse mixed inverse_pricing flags within a single scan call
   with the controlled-error envelope (the scan's per-row
   ``quote_units`` disclosure cannot be honest under mixed
   convention — same pattern futures_strip_snapshot uses).
5. Group market data by (curve_family, strip_position).
6. For each stem:
   a. Clean each of price / volume / OI series (ffill 5 days;
      drop duplicates; sort).
   b. Convert raw_price to implied_rate_pct via the per-stem
      inverse_pricing flag.
   c. Intersect the three series on the shared trading days.
   d. Cap at the resolved as_of_date (defensive — the SQL upper
      bound already truncated).
   e. Compute, on the intersection:
      - rolling 252d z-score on the implied-rate series →
        ``implied_rate_level``
      - rolling 252d z-score on diff(1) of the implied-rate
        series, multiplied by 100 to land in BPS →
        ``implied_rate_change``
      - rolling 252d z-score on the volume series →
        ``volume_level``
      - rolling 252d z-score on the OI series →
        ``open_interest_level``
   f. Snapshot values: current_raw_price, implied_rate_pct,
      daily_change_implied_rate_bps (latest 1-day Δ × 100),
      current_volume, current_open_interest,
      delta_open_interest_1d.
   g. Stems with fewer than ``z_score_min_periods`` aligned
      observations are SKIPPED — never silently promoted to a
      z=None row (the rank ordering would be undefined).
7. For each requested metric independently:
   a. Filter by |z| >= ``min_abs_z_score`` (input).
   b. Sort by |z| desc, ties broken by ``curve_family`` asc then
      ``strip_position`` asc (deterministic).
   c. Take the top ``top_n`` rows.
8. Concatenate the per-metric top-N lists into one ordered
   ``results`` list in YAML-default order
   (implied_rate_level, implied_rate_change, volume_level,
   open_interest_level — or the LLM's requested subset in the
   same canonical order).

Frozen-window guard
-------------------
PR14: the z-score lookback window is wire-frozen at 252 days for
V1 (the methodology disclosure mentions "252 trading days"
verbatim). A future window rename is caught at import time via
the ``_validate_window_252d`` guard — same pattern the
scan_inflation_swaps_extremes scanner uses for its 252d
z-score window.

Determinism + as-of anchor
--------------------------
``params.as_of_date`` anchors the scan to a specific trading day.
When supplied AND beyond the universe's last observed trade_date,
the future-anchor guard
(``fetch_scan_universe_strip_position_max_date``) returns the
controlled-error envelope rather than silently re-labelling an
unbounded ranking. When omitted, the scan resolves to the
most-recent shared trading day across the fetched universe
(max trade_date observed). Per-stem series are CAPPED at the
resolved as-of date before z-scoring so a stray future-dated tick
cannot contaminate the trailing rolling stats.

Test seam
---------
``fetch_scan_universe_strip_position``,
``fetch_scan_universe_policy_future_reference``,
``fetch_scan_universe_strip_position_max_date``, and ``date`` are
imported here at module level; tests patch them via
``patch("rates_agent.policy_futures.tools.scan_policy_futures_extremes.compute.X")``.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import pandas as pd
from sqlalchemy.engine import Engine

from rates_agent.policy_futures.tools.scan_policy_futures_extremes.schemas import (
    PolicyFuturesScanCurveFamily,
    ScanMetric,
    ScanPolicyFuturesExtremesInput,
    ScanPolicyFuturesExtremesOutput,
    ScanPolicyFuturesExtremesResultRow,
)
from shared.analytics.levels import clean_single_series
from shared.analytics.rates_fetch import (
    fetch_scan_universe_policy_future_reference,
    fetch_scan_universe_strip_position,
    fetch_scan_universe_strip_position_max_date,
)
from shared.analytics.spreads import rolling_zscore, safe_float
from shared.config import ToolConfig, load_tool_config


# Bundled config — public symbol so external callers (mcp_server,
# tests, future REST routes) can build a ToolConfig from the same
# source the tool uses.
CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


# instrument_type filter passed to the universe fetcher. Code-level
# invariant — this primitive owns the policy-futures-universe
# concept and must NEVER silently fall through to bond-futures /
# sovereign / OIS / inflation rows under a policy-futures label.
# Putting this in YAML would let a YAML edit silently switch the
# scan over to a different instrument type; per
# DESIGN_PRINCIPLES.md §5, structural identity stays in code.
# Same pattern scan_inflation_swaps_extremes uses for its own
# ``_INFLATION_SWAP_INSTRUMENT_TYPE``.
_POLICY_FUTURE_INSTRUMENT_TYPE: str = "policy_future"


# Wire-frozen z-score lookback window for V1. The methodology
# disclosure template mentions "252 trading days" verbatim; a
# future rename of the YAML's ``z_score_window_days`` to a
# different value would mean the disclosure lies about the actual
# window. The guard fires at compute time so a YAML edit gets a
# loud ``NotImplementedError`` rather than producing a methodology
# disclosure labelled "252 trading days" against a different
# window. Mirrors the ``_validate_window_252d`` guard in the
# inflation_swaps scanner.
_FROZEN_Z_WINDOW: int = 252


def _validate_window_252d(z_window: int) -> None:
    """Wire-freeze guard for the 252d z-score lookback window."""
    if z_window != _FROZEN_Z_WINDOW:
        raise NotImplementedError(
            f"z_score_window_days={z_window!r} is documented in "
            f"this tool's config.yaml as a future-supported value "
            f"(see methodology.planned_extensions) but is not yet "
            f"implemented. V1 supports only {_FROZEN_Z_WINDOW} "
            f"because the methodology disclosure mentions '252 "
            f"trading days' verbatim per the catalog's "
            f"methodology guardrail. Either restore the value to "
            f"{_FROZEN_Z_WINDOW} or implement the disclosure-"
            f"template rename to read the window from YAML so a "
            f"different window can be disclosed honestly."
        )


# Canonical ordering for the per-metric top-N concatenation. Stable
# so downstream consumers can rely on the implied_rate_level rows
# always coming first, etc. The order matches the catalog's
# concept_summary wording ("implied rate, Δ, volume, and OI") and
# the YAML's ``default_metrics`` CSV.
_METRIC_ORDER: Tuple[ScanMetric, ...] = (
    "implied_rate_level",
    "implied_rate_change",
    "volume_level",
    "open_interest_level",
)


# ============================================================================
# CONFIG HELPERS
# ============================================================================

def _parse_regime_map(csv_value: str) -> Mapping[str, str]:
    """Parse the YAML's ``short_rate_regime_map`` CSV into a dict.

    Stored as a CSV of ``KEY=VALUE`` pairs because
    ``Convention.value`` is scalar (str). Defensive against
    whitespace / trailing commas / mis-cased separators. Same
    parser shape every policy_futures sibling uses.
    """
    out: dict[str, str] = {}
    for chunk in csv_value.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "=" not in chunk:
            raise ValueError(
                f"short_rate_regime_map entry {chunk!r} is "
                f"malformed; expected 'CURVE_FAMILY=REGIME' "
                f"(CSV-joined)."
            )
        key, value = chunk.split("=", 1)
        out[key.strip()] = value.strip()
    return out


def _parse_default_metrics(csv_value: str) -> Tuple[ScanMetric, ...]:
    """Parse the YAML's ``default_metrics`` CSV into an ordered
    tuple of ScanMetric values, validated against the closed
    Literal set.

    The YAML stores the canonical default order as a CSV (because
    ``Convention.value`` is scalar). Compute parses and validates
    at load time so a malformed YAML edit gets caught loudly
    rather than producing a quiet skip.
    """
    out: list[ScanMetric] = []
    for chunk in csv_value.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if chunk not in _METRIC_ORDER:
            raise ValueError(
                f"default_metrics entry {chunk!r} is not a member "
                f"of the closed ScanMetric Literal. Allowed: "
                f"{list(_METRIC_ORDER)!r}."
            )
        out.append(chunk)  # type: ignore[arg-type]
    if not out:
        raise ValueError(
            "default_metrics is empty; configure at least one "
            "metric in the YAML (V1 default is "
            "'implied_rate_level,implied_rate_change,"
            "volume_level,open_interest_level')."
        )
    return tuple(out)


def _resolve_curve_families(
    requested: Optional[Sequence[str]],
    whitelist_csv: str,
) -> List[str]:
    """Resolve the LLM's optional ``curve_families`` input against
    the YAML's ``policy_futures_curve_families`` whitelist.

    When ``requested`` is None, returns the full whitelist (full
    universe scan). When ``requested`` is a list, the input schema
    already validated it against the whitelist, so this is a no-op
    pass-through that returns the request as-is.
    """
    whitelist = [s.strip() for s in whitelist_csv.split(",") if s.strip()]
    if requested is None:
        return whitelist
    return list(requested)


# ============================================================================
# METHODOLOGY DISCLOSURE
# ============================================================================

def _build_methodology_disclosure(
    *,
    z_window: int,
    curve_families: Sequence[str],
    regime_summary: str,
) -> str:
    """Compose the P5 / ADR 0013 disclosure string with the runtime
    z-score window AND the per-curve_family regime summary so a
    desk consumer sees the regime heterogeneity captured by the
    scan."""
    families_csv = ",".join(curve_families)
    return (
        f"Universe-wide policy-futures strip scan across "
        f"curve_families=[{families_csv}], strip_positions 1..8. "
        f"Ranks each (curve_family, strip_position) stem by "
        f"absolute z-score across four metrics: implied_rate_level "
        f"(z on the implied-rate PERCENT series), "
        f"implied_rate_change (z on the 1-day Δ implied-rate BPS "
        f"series, Δ × 100), volume_level (z on the raw volume "
        f"CONTRACTS series), open_interest_level (z on the raw OI "
        f"CONTRACTS series). Z-score lookback = {z_window} trading "
        f"days. Underlying short-rate regime varies across rows: "
        f"{regime_summary} (RFR = compounded daily risk-free rate; "
        f"IBOR = unsecured 3M term IBOR). The implied-rate "
        f"conversion is metadata-driven from "
        f"instrument_master.attributes->>'inverse_pricing': "
        f"implied_rate_pct = 100 - raw_price for inverse-priced "
        f"strips (all V1 entries), implied_rate_pct = raw_price "
        f"otherwise. Rolling-generic strip — the per-contract "
        f"underlying rolls quarterly so each strip slot mixes "
        f"contracts across rolls; this is the canonical desk read "
        f"but does NOT equal the price of a single underlying "
        f"contract over time. This is NOT a tenor-anchored OIS "
        f"scan, NOT a pack-average summary, NOT a curve-shape read "
        f"(use futures_calendar_spread / futures_butterfly_simple "
        f"/ futures_cross_market_spread for those), NOT a "
        f"meeting-by-meeting policy-path decomposition, NOT a "
        f"CTD-of-futures-of-OIS read (Phase-4 work gated on the "
        f"CTD identification + OIS-curve interpolation stack per "
        f"ADR 0013). This is a morning screen, NOT a tactical "
        f"trade signal."
    )


def _summarise_regimes(
    curve_families: Sequence[str],
    regime_map: Mapping[str, str],
) -> str:
    """Produce a short ``'SOFR_FUT=RFR, EUR_SHORT_RATE_FUT=IBOR'``
    summary for inclusion in the response-level disclosure. Ordered
    by the input curve_families list so the response-level string
    is deterministic given the same scope."""
    return ", ".join(
        f"{cf}={regime_map.get(cf, '?')}" for cf in curve_families
    )


# ============================================================================
# PER-STEM SCORING
# ============================================================================

def _build_stem_aligned_series(
    *,
    price_df: pd.DataFrame,
    volume_df: pd.DataFrame,
    oi_df: pd.DataFrame,
    ffill_limit: int,
    inverse_priced: bool,
    cap_at: Optional[date] = None,
) -> Optional[pd.DataFrame]:
    """Clean each of the three per-stem long-format slices, convert
    raw_price to implied_rate_pct via the per-stem inverse_pricing
    flag, and align all three series on the intersection of
    trading days.

    Returns a wide DataFrame with columns
    ``['raw_price', 'implied_rate_pct', 'volume', 'open_interest']``
    indexed by date, or None if any of the three series is empty
    after cleaning or the intersection is empty.

    ``cap_at`` truncates the per-stem aligned series to rows on or
    before the supplied date — defensive against the case where
    the SQL upper bound did not include this stem's tail. The SQL
    fetcher's ``end_date`` already enforces the upper bound on the
    raw rows; this in-Python cap is belt-and-braces.
    """
    if price_df.empty or volume_df.empty or oi_df.empty:
        return None
    clean_price = clean_single_series(price_df, ffill_limit=ffill_limit)
    clean_volume = clean_single_series(volume_df, ffill_limit=ffill_limit)
    clean_oi = clean_single_series(oi_df, ffill_limit=ffill_limit)
    if clean_price.empty or clean_volume.empty or clean_oi.empty:
        return None

    raw_price_s = clean_price["field_value"]
    if inverse_priced:
        implied_rate_s = 100.0 - raw_price_s
    else:
        implied_rate_s = raw_price_s.copy()

    volume_s = clean_volume["field_value"]
    oi_s = clean_oi["field_value"]

    common_idx = (
        raw_price_s.index.intersection(volume_s.index)
        .intersection(oi_s.index)
        .sort_values()
    )
    if cap_at is not None:
        cap_ts = pd.Timestamp(cap_at)
        common_idx = common_idx[common_idx <= cap_ts]
    if len(common_idx) == 0:
        return None
    return pd.DataFrame(
        {
            "raw_price": raw_price_s.loc[common_idx],
            "implied_rate_pct": implied_rate_s.loc[common_idx],
            "volume": volume_s.loc[common_idx],
            "open_interest": oi_s.loc[common_idx],
        }
    )


def _compute_stem_metrics(
    *,
    curve_family: str,
    strip_position: int,
    aligned: pd.DataFrame,
    z_window: int,
    z_min_periods: int,
    z_ddof: int,
    daily_offset_rows: int,
    raw_price_round_decimals: int,
    implied_rate_round_decimals: int,
    bps_change_round_decimals: int,
    volume_round_decimals: int,
    oi_round_decimals: int,
    z_score_round_decimals: int,
) -> Optional[Dict[str, Any]]:
    """Compute the four-metric scoring for one stem.

    Returns a dict carrying the snapshot values + four per-metric
    z-scores, or None if the stem has fewer than ``z_min_periods``
    aligned observations.

    Z-scores are computed via the shared ``rolling_zscore`` helper
    — same primitive every other policy_futures / bond_futures /
    sovereign / OIS / inflation rates monitor uses. The
    implied_rate_change metric's z-score is the rolling z of the
    diff(1) of the implied-rate series, multiplied by 100 to land
    in BPS space (matches the bps wire convention).
    """
    if len(aligned) < z_min_periods:
        return None

    raw_price = aligned["raw_price"]
    implied_rate = aligned["implied_rate_pct"]
    volume = aligned["volume"]
    open_interest = aligned["open_interest"]

    # ------------------------------------------------------------------
    # Snapshot values (latest aligned row)
    # ------------------------------------------------------------------
    as_of_ts = aligned.index[-1]
    as_of_date_str = as_of_ts.date().strftime("%Y-%m-%d")
    current_raw_price = safe_float(
        raw_price.iloc[-1], decimals=raw_price_round_decimals,
    )
    current_implied_rate = safe_float(
        implied_rate.iloc[-1], decimals=implied_rate_round_decimals,
    )
    current_volume = safe_float(
        volume.iloc[-1], decimals=volume_round_decimals,
    )
    current_oi = safe_float(
        open_interest.iloc[-1], decimals=oi_round_decimals,
    )

    # 1-day raw subtraction on the IMPLIED-RATE axis, multiplied by
    # 100 to land in BPS (matches scan_inflation_swaps_extremes'
    # daily_change_zcis_rate_bps convention and the bps wire shape
    # sibling rate-level primitives use).
    daily_change_implied_rate_bps: Optional[float]
    if len(implied_rate) >= daily_offset_rows:
        cur_rate = implied_rate.iloc[-1]
        prev_rate = implied_rate.iloc[-daily_offset_rows]
        if pd.isna(cur_rate) or pd.isna(prev_rate):
            daily_change_implied_rate_bps = None
        else:
            daily_change_implied_rate_bps = round(
                (float(cur_rate) - float(prev_rate)) * 100.0,
                bps_change_round_decimals,
            )
    else:
        daily_change_implied_rate_bps = None

    # 1-day raw OI change (NOT *100; matches the sibling
    # volume_open_interest_snapshot / scan_bond_futures_extremes
    # delta_open_interest_1d convention — whole-contract delta).
    delta_oi_1d: Optional[float]
    if len(open_interest) >= daily_offset_rows:
        cur_oi_raw = open_interest.iloc[-1]
        prev_oi_raw = open_interest.iloc[-daily_offset_rows]
        if pd.isna(cur_oi_raw) or pd.isna(prev_oi_raw):
            delta_oi_1d = None
        else:
            delta_oi_1d = round(
                float(cur_oi_raw) - float(prev_oi_raw),
                oi_round_decimals,
            )
    else:
        delta_oi_1d = None

    # ------------------------------------------------------------------
    # Per-metric z-scores. All four use the SAME window /
    # min_periods / ddof; the metric distinction is in the SERIES
    # the z is computed on, not the method.
    # ------------------------------------------------------------------
    def _latest_z(series: pd.Series) -> Optional[float]:
        z = rolling_zscore(
            series,
            window=z_window,
            min_periods=z_min_periods,
            ddof=z_ddof,
            round_decimals=z_score_round_decimals,
        )
        return safe_float(z.iloc[-1], decimals=z_score_round_decimals)

    implied_rate_level_z = _latest_z(implied_rate)

    # The implied_rate_change metric's z is the rolling z of the
    # diff(1) of the implied-rate series, in BPS. We materialise
    # the change series as (diff(1) * 100) — the bps wire
    # convention sibling tools use — and z-score the result. Drop
    # the first NaN from diff(1) so rolling_zscore sees a clean
    # leading edge (it handles NaN internally but starting clean
    # is honest).
    implied_rate_change_series_bps = (
        implied_rate.diff(1).dropna() * 100.0
    )
    implied_rate_change_z = (
        _latest_z(implied_rate_change_series_bps)
        if len(implied_rate_change_series_bps) >= z_min_periods
        else None
    )

    volume_level_z = _latest_z(volume)
    open_interest_level_z = _latest_z(open_interest)

    return {
        "curve_family": curve_family,
        "strip_position": strip_position,
        "as_of_date": as_of_date_str,
        "current_raw_price": current_raw_price,
        "current_implied_rate_pct": current_implied_rate,
        "daily_change_implied_rate_bps": daily_change_implied_rate_bps,
        "current_volume": current_volume,
        "current_open_interest": current_oi,
        "delta_open_interest_1d": delta_oi_1d,
        "z_by_metric": {
            "implied_rate_level": implied_rate_level_z,
            "implied_rate_change": implied_rate_change_z,
            "volume_level": volume_level_z,
            "open_interest_level": open_interest_level_z,
        },
    }


# ============================================================================
# PUBLIC API
# ============================================================================

def calculate_scan_policy_futures_extremes(
    engine: Engine,
    params: ScanPolicyFuturesExtremesInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    """Scan the policy-futures strip universe and return the top-N
    extremes per metric (implied_rate_level / implied_rate_change
    / volume_level / open_interest_level), each tagged with the
    per-row methodology disclosure.

    Parameters
    ----------
    engine : Engine
        Live SQLAlchemy engine.
    params : ScanPolicyFuturesExtremesInput
        Validated input. ``curve_families=None`` means scan the
        full universe per the YAML's
        ``policy_futures_curve_families`` whitelist.
        ``metrics=None`` means rank all four metrics. ``top_n`` /
        ``min_abs_z_score`` ``=None`` fall through to YAML
        defaults.
    config : ToolConfig, optional
        Bundled ``config.yaml`` auto-loaded when None. Tests pass a
        custom ToolConfig to exercise convention overrides.

    Returns
    -------
    dict
        Serialised ``ScanPolicyFuturesExtremesOutput``, or
        ``{"error": "..."}`` on recoverable failure (empty
        universe, mixed inverse_pricing flags, all stems filtered
        out, future as_of anchor, etc.).
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
    daily_offset_rows = config.convention_value("daily_change_offset_rows")
    price_field = config.convention_value("default_price_field")
    volume_field = config.convention_value("default_volume_field")
    oi_field = config.convention_value("default_open_interest_field")
    raw_price_round_decimals = config.convention_value(
        "raw_price_round_decimals",
    )
    implied_rate_round_decimals = config.convention_value(
        "implied_rate_round_decimals",
    )
    bps_change_round_decimals = config.convention_value(
        "bps_change_round_decimals",
    )
    volume_round_decimals = config.convention_value("volume_round_decimals")
    oi_round_decimals = config.convention_value("oi_round_decimals")
    z_score_round_decimals = config.convention_value(
        "z_score_round_decimals",
    )
    whitelist_csv = config.convention_value(
        "policy_futures_curve_families",
    )
    regime_map = _parse_regime_map(
        config.convention_value("short_rate_regime_map"),
    )
    default_metrics = _parse_default_metrics(
        config.convention_value("default_metrics"),
    )

    # PR14 wire-freeze guard.
    _validate_window_252d(z_window)

    # Resolve None-sentinel display thresholds against the YAML
    # defaults (PR9 / PR10 — the default lives in config.yaml,
    # not in the schema's Field default).
    effective_top_n = (
        int(params.top_n)
        if params.top_n is not None
        else int(config.convention_value("default_top_n"))
    )
    effective_min_abs_z = (
        float(params.min_abs_z_score)
        if params.min_abs_z_score is not None
        else float(config.convention_value("default_min_abs_z_score"))
    )
    effective_metrics: Tuple[ScanMetric, ...] = (
        tuple(params.metrics)  # type: ignore[arg-type]
        if params.metrics is not None
        else default_metrics
    )

    # Resolve the universe scope (LLM's input, validated against
    # YAML whitelist at schema time).
    curve_families = _resolve_curve_families(
        params.curve_families, whitelist_csv,
    )

    # ------------------------------------------------------------------
    # 1. Date window — fetch start is derived from YAML, NOT from
    #    an LLM input (PR8 / OPR8 — no ``lookback_days`` overreach).
    # ------------------------------------------------------------------
    fetch_window_days = int(z_window * buffer_mult)
    requested_as_of: Optional[date] = params.as_of_date
    fetch_anchor: date = (
        requested_as_of if requested_as_of is not None else date.today()
    )
    start_date = fetch_anchor - timedelta(
        days=fetch_window_days + int(ffill_limit)
    )

    # ------------------------------------------------------------------
    # 1a. Future-anchor guard — when the LLM supplies an explicit
    #     ``as_of_date`` that lies beyond the policy-futures
    #     universe's last ingested ``trade_date``, return the
    #     controlled-error envelope (P5 / PR8 honest disclosure).
    #     Implemented as a cheap MAX(trade_date) probe over the
    #     same source the series fetcher uses (single source of
    #     truth). Skipped when ``requested_as_of`` is None.
    # ------------------------------------------------------------------
    if requested_as_of is not None:
        universe_max_trade_date = (
            fetch_scan_universe_strip_position_max_date(
                engine=engine,
                instrument_type=_POLICY_FUTURE_INSTRUMENT_TYPE,
                curve_families=curve_families,
            )
        )
        if (
            universe_max_trade_date is not None
            and requested_as_of > universe_max_trade_date
        ):
            return {
                "error": (
                    f"no scoreable stems: as_of_date="
                    f"{requested_as_of.isoformat()} is beyond the "
                    f"policy-futures universe's last observed "
                    f"trade_date="
                    f"{universe_max_trade_date.isoformat()}. The "
                    "scan refuses to silently re-label an "
                    "unbounded ranking as a future-anchored read."
                )
            }

    # ------------------------------------------------------------------
    # 2. Fetch the three field series across the universe. Each
    #    helper call is one round-trip; three calls total. The
    #    helper's strip-position-keyed projection guarantees that
    #    rows from a non-policy-future instrument_type cannot leak
    #    in. The SQL upper bound (end_date) is the deterministic-
    #    anchor scope for Layer-B validation.
    # ------------------------------------------------------------------
    raw_price = fetch_scan_universe_strip_position(
        engine=engine,
        instrument_type=_POLICY_FUTURE_INSTRUMENT_TYPE,
        field_name=price_field,
        start_date=start_date,
        curve_families=curve_families,
        end_date=requested_as_of,
    )
    raw_volume = fetch_scan_universe_strip_position(
        engine=engine,
        instrument_type=_POLICY_FUTURE_INSTRUMENT_TYPE,
        field_name=volume_field,
        start_date=start_date,
        curve_families=curve_families,
        end_date=requested_as_of,
    )
    raw_oi = fetch_scan_universe_strip_position(
        engine=engine,
        instrument_type=_POLICY_FUTURE_INSTRUMENT_TYPE,
        field_name=oi_field,
        start_date=start_date,
        curve_families=curve_families,
        end_date=requested_as_of,
    )

    if raw_price.empty or raw_volume.empty or raw_oi.empty:
        return {
            "error": (
                "No policy-futures universe data found for at "
                f"least one of the required fields ({price_field}, "
                f"{volume_field}, {oi_field}) since "
                f"{start_date.isoformat()} across "
                f"curve_families={curve_families}. Verify the "
                "strip-position universe is ingested with all "
                "three fields."
            )
        }

    # ------------------------------------------------------------------
    # 3. Resolve as_of_date for the reference fetch. When supplied,
    #    use it directly; otherwise, peek the price frame's max
    #    trade_date so the SCD2 lookup is anchored at the actual
    #    data-max (not wall-clock today, which could fall on a
    #    weekend / outside the ingested range).
    # ------------------------------------------------------------------
    for df in (raw_price, raw_volume, raw_oi):
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        df["field_value"] = pd.to_numeric(
            df["field_value"], errors="coerce",
        )

    if requested_as_of is not None:
        scd2_anchor: date = requested_as_of
    else:
        scd2_anchor = raw_price["trade_date"].max().date()

    # ------------------------------------------------------------------
    # 4. Fetch per-stem reference (inverse_pricing + SCD2 SCD2
    #    metadata) — ONE round-trip. The helper returns one row per
    #    (curve_family, strip_position).
    # ------------------------------------------------------------------
    ref_df = fetch_scan_universe_policy_future_reference(
        engine=engine,
        as_of_date=scd2_anchor,
        curve_families=curve_families,
    )
    if ref_df.empty:
        return {
            "error": (
                "No policy-futures strip slots found on "
                "instrument_master for "
                f"curve_families={curve_families}. Verify the "
                "(curve_family, strip_position) universe is "
                "populated as strip-position-keyed rolling "
                "contracts."
            )
        }

    # Build a lookup keyed by (curve_family, strip_position) for
    # O(1) per-stem reference attachment. Refuse stems missing the
    # inverse_pricing flag (PR8 / P6 — no hidden methodology
    # in code; surface the metadata gap honestly).
    ref_lookup: Dict[Tuple[str, int], Dict[str, Any]] = {}
    for _, r in ref_df.iterrows():
        cf = str(r["curve_family"])
        sp = int(r["strip_position"])
        ip_raw = r.get("inverse_pricing")
        if ip_raw is None or (isinstance(ip_raw, float) and pd.isna(ip_raw)):
            return {
                "error": (
                    f"Policy-futures strip {cf} strip_position="
                    f"{sp} is missing the 'inverse_pricing' flag "
                    "on instrument_master.attributes. The "
                    "implied-rate conversion rule is metadata-"
                    "driven (PR8 / P6 — no hidden methodology in "
                    "code); a strip without this flag cannot be "
                    "priced honestly. Surface this as a metadata "
                    "gap to the playbook owner."
                )
            }
        ref_lookup[(cf, sp)] = {
            "contract_code": (
                str(r["contract_code"])
                if pd.notna(r["contract_code"]) else None
            ),
            "underlying_contract_code": (
                str(r["underlying_contract_code"])
                if pd.notna(r["underlying_contract_code"]) else None
            ),
            "security_name": (
                str(r["security_name"])
                if pd.notna(r["security_name"]) else None
            ),
            "expiry_date": (
                r["expiry_date"].strftime("%Y-%m-%d")
                if pd.notna(r["expiry_date"])
                and hasattr(r["expiry_date"], "strftime")
                else (
                    str(r["expiry_date"])
                    if pd.notna(r["expiry_date"]) else None
                )
            ),
            "contract_size": (
                float(r["contract_size"])
                if pd.notna(r["contract_size"]) else None
            ),
            "inverse_pricing": bool(ip_raw),
        }

    # ------------------------------------------------------------------
    # 5. Mixed-flag universe guard. The scan's response-level
    #    quote_units disclosure cannot be honest if some stems are
    #    inverse-priced and others are direct-priced under a
    #    single call. Refuse with the controlled-error envelope.
    #    (Per-row quote_units is set honestly per stem, but the
    #    SCAN-LEVEL disclosure cannot describe two conventions in
    #    one string — same shape futures_strip_snapshot uses.)
    # ------------------------------------------------------------------
    distinct_flags = {ref["inverse_pricing"] for ref in ref_lookup.values()}
    if len(distinct_flags) > 1:
        mixed_summary = ", ".join(
            f"({cf}, sp={sp})={ref['inverse_pricing']}"
            for (cf, sp), ref in sorted(ref_lookup.items())
        )
        return {
            "error": (
                "Policy-futures universe scan refuses a mixed "
                "inverse_pricing universe: the response-level "
                "quote_units disclosure cannot describe two "
                "conventions in one string. Per-stem flags: "
                f"{mixed_summary}. Narrow the curve_families "
                "scope to a single-convention subset, or surface "
                "the mixed-convention question as a metadata-gap "
                "ticket to the playbook owner (P5)."
            )
        }

    # ------------------------------------------------------------------
    # 6. Per-stem materialisation + scoring.
    # ------------------------------------------------------------------
    # Universe of stems present in the market-data frames. We use
    # the price frame's stems as the scoring universe — a stem
    # missing from price cannot be scored, and price availability
    # is the headline desk signal. ref_lookup names every
    # universe-wide stem on instrument_master; the price frame may
    # be a subset (e.g., a stem ingested for OI but not yet for
    # PX_LAST — defensive).
    price_groups: Dict[Tuple[str, int], pd.DataFrame] = {}
    for (cf_raw, sp_raw), g in raw_price.groupby(
        ["curve_family", "strip_position"], dropna=False,
    ):
        price_groups[(str(cf_raw), int(sp_raw))] = g
    volume_groups: Dict[Tuple[str, int], pd.DataFrame] = {}
    for (cf_raw, sp_raw), g in raw_volume.groupby(
        ["curve_family", "strip_position"], dropna=False,
    ):
        volume_groups[(str(cf_raw), int(sp_raw))] = g
    oi_groups: Dict[Tuple[str, int], pd.DataFrame] = {}
    for (cf_raw, sp_raw), g in raw_oi.groupby(
        ["curve_family", "strip_position"], dropna=False,
    ):
        oi_groups[(str(cf_raw), int(sp_raw))] = g

    universe_stems = sorted(set(price_groups.keys()))
    universe_count = len(universe_stems)
    if universe_count == 0:
        return {
            "error": (
                "Policy-futures universe scan found 0 "
                "(curve_family, strip_position) stems with price "
                f"data — check {price_field} ingestion for "
                f"curve_families={curve_families}."
            )
        }

    per_stem_metrics: List[Dict[str, Any]] = []
    for (cf, sp) in universe_stems:
        price_slice = price_groups.get((cf, sp))
        volume_slice = volume_groups.get((cf, sp))
        oi_slice = oi_groups.get((cf, sp))
        ref = ref_lookup.get((cf, sp))
        if (
            price_slice is None or volume_slice is None
            or oi_slice is None or ref is None
        ):
            continue
        aligned = _build_stem_aligned_series(
            price_df=price_slice[["trade_date", "field_value"]],
            volume_df=volume_slice[["trade_date", "field_value"]],
            oi_df=oi_slice[["trade_date", "field_value"]],
            ffill_limit=ffill_limit,
            inverse_priced=ref["inverse_pricing"],
            cap_at=requested_as_of,
        )
        if aligned is None:
            continue
        scored = _compute_stem_metrics(
            curve_family=cf,
            strip_position=sp,
            aligned=aligned,
            z_window=z_window,
            z_min_periods=z_min_periods,
            z_ddof=z_ddof,
            daily_offset_rows=daily_offset_rows,
            raw_price_round_decimals=raw_price_round_decimals,
            implied_rate_round_decimals=implied_rate_round_decimals,
            bps_change_round_decimals=bps_change_round_decimals,
            volume_round_decimals=volume_round_decimals,
            oi_round_decimals=oi_round_decimals,
            z_score_round_decimals=z_score_round_decimals,
        )
        if scored is None:
            continue
        # Attach reference fields the per-row output needs.
        scored["reference"] = ref
        per_stem_metrics.append(scored)

    if not per_stem_metrics:
        as_of_clause = (
            f" with as_of_date={requested_as_of.isoformat()}"
            if requested_as_of is not None else ""
        )
        return {
            "error": (
                f"Scanned {universe_count} policy-futures stems "
                f"but none had >= {z_min_periods} aligned "
                "observations to compute a meaningful z-score"
                f"{as_of_clause}. Verify the universe's data "
                "coverage or pass an as_of_date within the "
                "ingested range."
            )
        }

    # ------------------------------------------------------------------
    # 7. Per-metric ranking + top-N + threshold filter.
    # ------------------------------------------------------------------
    regime_summary = _summarise_regimes(curve_families, regime_map)
    methodology_disclosure = _build_methodology_disclosure(
        z_window=z_window,
        curve_families=curve_families,
        regime_summary=regime_summary,
    )

    per_metric_pass_counts: Dict[ScanMetric, int] = {
        m: 0 for m in effective_metrics
    }
    output_rows: List[ScanPolicyFuturesExtremesResultRow] = []
    # Iterate metrics in canonical order so the output rows are
    # ordered deterministically (implied_rate_level first, then
    # implied_rate_change, then volume_level, then
    # open_interest_level — or the LLM's subset in the same
    # canonical order). Filter against the LLM's requested
    # ``effective_metrics`` subset.
    requested_metric_set = set(effective_metrics)
    for metric in _METRIC_ORDER:
        if metric not in requested_metric_set:
            continue
        scored_for_metric: List[Tuple[float, Dict[str, Any]]] = []
        for stem_metrics in per_stem_metrics:
            z_value = stem_metrics["z_by_metric"][metric]
            if z_value is None:
                continue
            if abs(z_value) < effective_min_abs_z:
                continue
            scored_for_metric.append((z_value, stem_metrics))
        # Sort by |z| desc, then curve_family asc, then
        # strip_position asc for deterministic ordering when two
        # stems share the same |z|.
        scored_for_metric.sort(
            key=lambda zm: (
                -abs(zm[0]),
                zm[1]["curve_family"],
                zm[1]["strip_position"],
            )
        )
        per_metric_pass_counts[metric] = len(scored_for_metric)
        top_for_metric = scored_for_metric[:effective_top_n]
        for rank_idx, (z_value, stem_metrics) in enumerate(
            top_for_metric, start=1,
        ):
            ref = stem_metrics["reference"]
            cf = stem_metrics["curve_family"]
            inverse_priced = ref["inverse_pricing"]
            quote_units = "100 - rate" if inverse_priced else "rate (%)"
            short_rate_regime = regime_map.get(cf)
            if short_rate_regime is None:
                return {
                    "error": (
                        f"Policy-futures curve_family={cf!r} has "
                        "no regime label in the YAML's "
                        "``short_rate_regime_map`` convention. "
                        "Add the entry (e.g. 'NEW_FAMILY=RFR') "
                        "alongside the universe expansion in "
                        "policy_futures.yml so the methodology "
                        "disclosure remains honest (P5)."
                    )
                }
            output_rows.append(
                ScanPolicyFuturesExtremesResultRow(
                    rank=rank_idx,
                    metric=metric,
                    curve_family=cf,  # type: ignore[arg-type]
                    strip_position=stem_metrics["strip_position"],
                    contract_code=ref["contract_code"],
                    underlying_contract_code=ref[
                        "underlying_contract_code"
                    ],
                    security_name=ref["security_name"],
                    expiry_date=ref["expiry_date"],
                    contract_size=ref["contract_size"],
                    inverse_priced=inverse_priced,
                    short_rate_regime=short_rate_regime,  # type: ignore[arg-type]
                    quote_units=quote_units,
                    as_of_date=stem_metrics["as_of_date"],
                    current_raw_price=stem_metrics["current_raw_price"],
                    implied_rate_pct=stem_metrics[
                        "current_implied_rate_pct"
                    ],
                    daily_change_implied_rate_bps=stem_metrics[
                        "daily_change_implied_rate_bps"
                    ],
                    current_volume=stem_metrics["current_volume"],
                    current_open_interest=stem_metrics[
                        "current_open_interest"
                    ],
                    delta_open_interest_1d=stem_metrics[
                        "delta_open_interest_1d"
                    ],
                    z_score=round(
                        float(z_value), z_score_round_decimals,
                    ),
                    signal=(
                        "EXTREME_HIGH" if z_value > 0
                        else "EXTREME_LOW"
                    ),
                    methodology_disclosure=methodology_disclosure,
                )
            )

    if not output_rows:
        return {
            "error": (
                f"Scanned {universe_count} policy-futures stems "
                f"({len(per_stem_metrics)} scoreable) but none "
                f"passed |z| >= {effective_min_abs_z} on any of "
                f"the requested metrics {list(effective_metrics)}. "
                "Try lowering min_abs_z_score (or omit it to fall "
                "through to the YAML default)."
            )
        }

    # ------------------------------------------------------------------
    # 8. scan_summary — one line summarising the scan state.
    # ------------------------------------------------------------------
    as_of_dates = sorted({m["as_of_date"] for m in per_stem_metrics})
    if len(as_of_dates) == 1:
        as_of_clause = f"as_of {as_of_dates[0]}"
    else:
        as_of_clause = (
            f"as_of dates span {as_of_dates[0]} to {as_of_dates[-1]}"
        )
    per_metric_clause = ", ".join(
        f"{m}={per_metric_pass_counts.get(m, 0)}"
        for m in effective_metrics
    )
    scan_summary = (
        f"Scanned {universe_count} policy-futures stems "
        f"({len(per_stem_metrics)} scoreable). "
        f"Stems with |z| >= {effective_min_abs_z} per metric: "
        f"{per_metric_clause}. "
        f"Showing top {effective_top_n} per metric "
        f"({len(output_rows)} rows). {as_of_clause}."
    )

    output = ScanPolicyFuturesExtremesOutput(
        scan_summary=scan_summary,
        results=output_rows,
        methodology_disclosure=methodology_disclosure,
    )
    return output.model_dump()
