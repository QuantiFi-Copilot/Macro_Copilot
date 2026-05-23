"""
compute.py — Config-driven policy-futures calendar-spread monitor (V1)
========================================================================

Same-curve calendar spread between TWO strip-position slots on ONE
``curve_family``. Reads both legs in a single ``fetch_strip_group``
round-trip, converts each to implied-rate space per the per-strip
``inverse_priced`` flag, aligns on the intersection of trading days,
and emits a snapshot of:

  - current ``spread_implied_rate_pct`` (desk-recognised in PERCENT
    POINTS = ``rate_short - rate_long``)
  - current ``raw_price_spread`` (in the contract's native price space
    = ``price_short - price_long``)
  - 1-day raw subtractions on each axis
  - rolling 252-day z-score on the IMPLIED-RATE-SPREAD series
  - trailing 252-day high / low / mid on the implied-rate-spread axis
  - percentile rank of the current implied-rate spread in the
    trailing 252-day range
  - observation_count over the LLM-supplied lookback_days window
  - per-leg as_of-bounded SCD2 disclosure block
  - the methodology disclosure (P5 / ADR 0011 — sign convention,
    regime label, inverse-pricing rule, z window, trailing window)

Sign convention (wire-frozen)
-----------------------------
``spread_implied_rate_pct = implied_rate_pct(short_leg) -
implied_rate_pct(long_leg)`` where the SHORT leg is the FRONTER strip
position (smaller ``strip_position`` integer) — the input schema's
validator enforces ``strip_position_short < strip_position_long`` so
the sign convention is deterministic.

Why ``fetch_strip_group`` (not two ``fetch_strip_position`` calls)
------------------------------------------------------------------
The catalog names ``fetch_strip_group`` as the fetcher surface for
this primitive — one DB round-trip for both legs. ``fetch_strip_group``
returns the long-format ``(trade_date, strip_position, field_value)``
DataFrame the calendar spread needs; we pivot on ``strip_position``
via ``pivot_and_align_tenors(key_col='strip_position')`` to align the
two legs on a wide ``(date × strip_position)`` frame and forward-fill
holiday gaps up to the YAML's ``ffill_limit_days``. The same idiom
sovereign ``curve_spread`` uses with ``fetch_tenor_pair`` /
``pivot_and_align_tenors(key_col='tenor')``.

``fetch_strip_group`` does NOT carry an ``end_date`` parameter today
(see ``shared/analytics/rates_fetch.py:913``), unlike
``fetch_strip_position``. To keep the deterministic-anchoring
contract — explicit ``as_of_date`` ⇒ snapshot anchored at that date —
the post-fetch step filters rows by ``trade_date <= as_of_date`` in
pandas. Adding an ``end_date`` to ``fetch_strip_group`` would be a
fetcher-surface change outside this primitive's scope; this in-process
filter is the honest equivalent given the rest of the pipeline runs
in-memory anyway.

Why not ``compute_level_metrics``
---------------------------------
Same reason the sibling ``futures_price_level`` doesn't use it:
``shared.analytics.levels.compute_level_metrics`` assumes the
underlying series is in PERCENT (yield space) and multiplies deltas
by 100 to produce bps. Calendar spreads on inverse-priced strips
live in PRICE-POINT space on the raw axis and PERCENT-POINT space
on the implied-rate axis; a ``*100`` multiplication on either delta
would silently lie about the unit. We compose the lower-level
helpers (``rolling_zscore``, ``trailing_high_low_percentile``,
``pivot_and_align_tenors``) directly here so every delta stays in
the right unit space.

Per-leg → implied-rate conversion (DRY with futures_price_level)
----------------------------------------------------------------
The implied-rate conversion per leg is the SAME rule the sibling
``futures_price_level/compute.py`` uses: for ``inverse_priced=True``
strips, ``implied_rate_pct = 100 - raw_price``; for direct-priced
strips, ``implied_rate_pct = raw_price``. We duplicate the small
inlined arithmetic here rather than importing a private helper from
the sibling tool to keep the two primitives' compute paths
independent (the build rules forbid composing this primitive on top
of the price-level tool's public output, and importing an underscore-
prefixed private helper would just relocate the coupling). The
conversion rule is one line per leg; the per-curve_family regime
LABEL is the YAML-owned disclosure-only metadata, identical to the
sibling tools.

Both legs must agree on the ``inverse_pricing`` flag — same-curve
calendar spreads inherently share the flag, but we verify and refuse
with the controlled-error envelope if metadata drifts (defensive
metadata hygiene; in the V1 universe both legs always agree).

Methodology disclosure
----------------------
Every response carries a ``methodology_disclosure`` field with:
  - the sign convention (``short_leg − long_leg`` = ``fronter −
    backer``),
  - the per-``curve_family`` short-rate regime label (RFR vs IBOR),
  - the inverse-pricing rule in plain English,
  - the z-score lookback window (read from the YAML at runtime so the
    disclosure matches whatever ``z_score_window_days`` is set to),
  - the trailing-range window,
  - the strip-position keying,
  - the rolling-generic-strip-spread scope-limit (NOT a meeting-by-
    meeting policy-path decomposition; NOT a CTD-of-futures-of-OIS
    spread — those are deferred per ADR 0011).
The schema makes the field REQUIRED so a future caller cannot drop
the disclosure when relaying the snapshot.

Honest placeholder
------------------
``trailing_range_window_days`` is locked at 252 in V1. The output
schema's field names (``high_252d_spread_implied_rate_pct``,
``low_252d_spread_implied_rate_pct``,
``mid_252d_spread_implied_rate_pct``, ``percentile_252d``) embed the
number; changing the convention without renaming the wire fields
would silently lie about what the percentile is computed against.
The compute path raises ``NotImplementedError`` if this is set to
anything else; see ``methodology.planned_extensions`` in the YAML
for the path to making it configurable.

Deterministic anchoring (PR8 + PR16)
------------------------------------
The input schema's ``as_of_date`` (default ``None``) anchors the
snapshot:

  - ``None`` → anchor at the universe's last observed ``trade_date``
    where BOTH legs have a value after intersection (post-fetch
    data-max anchor — same pattern as the sibling
    futures_price_level).

  - explicit date BEYOND the universe's last ``trade_date`` on
    EITHER leg → return the controlled-error envelope
    (``{"error": "no scoreable strip: as_of_date=... is beyond ..."}``).
    The future-anchor probes use ``fetch_strip_position_max_date``
    per leg (NOT a strip-group probe — the per-strip helper already
    mirrors the series-fetcher's filter shape, so each leg's "what is
    this strip's last trading day" is the right gate). The MORE
    RESTRICTIVE leg's last date is the binding anchor: a snapshot
    cannot be honest if either leg has no data on the requested
    as-of.

  - explicit date WITHIN the universe range → the fetch is filtered
    in pandas to ``trade_date <= as_of_date`` so the snapshot is
    DETERMINISTIC across runs (same as_of_date + same DB state ⇒
    same numbers).

DB access
---------
Reaches the DB through the shared ``fetch_strip_group`` (both legs'
price series in one call), ``fetch_strip_position_reference`` (per-
leg metadata bounded by ``as_of_date``), and
``fetch_strip_position_max_date`` (per-leg future-anchor probe)
helpers — single-source-of-truth (P10) for policy-futures strip-
position reads. NO raw SQL in this file.

Test seam
---------
``fetch_strip_group``, ``fetch_strip_position_reference``,
``fetch_strip_position_max_date``, and ``date`` are imported here
at module level; tests patch them via
``patch("rates_agent.policy_futures.tools.futures_calendar_spread.compute.X")``.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

import pandas as pd
from sqlalchemy.engine import Engine

from rates_agent.policy_futures.tools.futures_calendar_spread.schemas import (
    FuturesCalendarSpreadCurrentMetrics,
    FuturesCalendarSpreadInput,
    FuturesCalendarSpreadOutput,
    FuturesCalendarSpreadTimeSeriesRow,
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


# Bundled config — public symbol so external callers (mcp_server,
# tests, future REST routes) can build a ToolConfig from the same
# source the tool uses.
CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


# Trailing-range window is wire-frozen at 252 in V1; see schemas.py
# and config.yaml's ``planned_extensions``. This guard fires at the
# convention layer so an editor of config.yaml gets a clear error
# rather than producing percentiles labelled "252d" against a
# different window. Mirrors the same guard in yield_levels /
# futures_price_level / volume_open_interest_snapshot.
_FROZEN_TRAILING_WINDOW: int = 252


def _build_methodology_disclosure(
    *,
    curve_family: str,
    strip_position_short: int,
    strip_position_long: int,
    contract_code_short: str,
    contract_code_long: str,
    inverse_priced: bool,
    short_rate_regime: str,
    z_window_days: int,
    trailing_window_days: int,
) -> str:
    """Compose the P5 / ADR 0011 disclosure string with runtime context
    so consumers see the exact regime + conversion rule + sign
    convention that produced the snapshot."""
    if inverse_priced:
        rule = (
            "Inverse-priced strip — per leg, "
            "implied_rate_pct = 100 - raw_price; the calendar spread "
            "on the implied-rate axis equals -(raw-price spread)."
        )
    else:
        rule = (
            "Direct-priced strip — per leg, "
            "implied_rate_pct = raw_price; the calendar spread on the "
            "implied-rate axis equals the raw-price spread."
        )
    return (
        f"Rolling-generic strip calendar spread at {curve_family} "
        f"strip_position_short={strip_position_short} (master stem="
        f"{contract_code_short}) minus strip_position_long="
        f"{strip_position_long} (master stem={contract_code_long}). "
        f"Sign convention: short_leg minus long_leg (fronter minus "
        f"backer). The desk-recognised quantity is the implied-rate "
        f"spread in PERCENT POINTS. Underlying short-rate regime: "
        f"{short_rate_regime} (RFR = compounded daily risk-free "
        f"rate; IBOR = unsecured 3M term IBOR). {rule} Z-score "
        f"lookback = {z_window_days} trading days on the IMPLIED-"
        f"RATE-SPREAD series; trailing range window = "
        f"{trailing_window_days} trading days. This is NOT a CTD-of-"
        f"futures-of-OIS calendar spread; the CTD-implied-OIS curve "
        f"is not yet a primitive in this build. This is also NOT a "
        f"meeting-by-meeting policy-path decomposition — it is a "
        f"same-curve strip-slot calendar spread on rolling-generic "
        f"series (ADR 0011 V1 scope — policy_futures ships strip-"
        f"position-keyed monitors only)."
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
            f"(high_252d_spread_implied_rate_pct, "
            f"low_252d_spread_implied_rate_pct, "
            f"mid_252d_spread_implied_rate_pct, percentile_252d) "
            f"embed that number on the wire. Either restore the value "
            f"to {_FROZEN_TRAILING_WINDOW} or implement the schema "
            f"rename + frontend update documented in "
            f"planned_extensions."
        )
    return trailing


def _format_expiry(value: Any) -> Optional[str]:
    """Render the per-leg ``expiry_date`` value as a YYYY-MM-DD string
    if present; None pass-through."""
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

def calculate_futures_calendar_spread(
    engine: Engine,
    params: FuturesCalendarSpreadInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    """Return current same-curve calendar spread on the policy-futures
    strip plus 1-day deltas, 252-day z-score on the implied-rate-spread
    series, trailing 252-day high/low/mid/percentile on the implied-
    rate-spread series, observation_count, the per-leg as_of-bounded
    SCD2 disclosure block, and the P5 methodology disclosure.

    Parameters
    ----------
    engine : Engine
        Live SQLAlchemy engine.
    params : FuturesCalendarSpreadInput
        Validated input. ``field_name=None`` resolves against the
        YAML's ``default_price_field`` convention; ``as_of_date=None``
        anchors at the post-fetch data-max date where both legs are
        observed.
    config : ToolConfig, optional
        Bundled config.yaml is auto-loaded when None. Tests pass a
        custom ToolConfig to exercise convention overrides.

    Returns
    -------
    dict
        Serialised ``FuturesCalendarSpreadOutput``, or
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
    raw_price_round_decimals = config.convention_value("raw_price_round_decimals")
    implied_rate_round_decimals = config.convention_value(
        "implied_rate_round_decimals"
    )
    z_score_round_decimals = config.convention_value("z_score_round_decimals")
    implied_rate_high_low_round_decimals = config.convention_value(
        "implied_rate_high_low_round_decimals"
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

    # Resolve field_name: caller's explicit value wins; None falls
    # through to the YAML default. Mirrors yield_levels /
    # futures_price_level.
    field_name_resolved = (
        params.field_name if params.field_name is not None else default_field_name
    )

    # ------------------------------------------------------------------
    # 1. Future-anchor guard (PR8 + PR16) — per leg. When an explicit
    #    as_of_date is supplied AND lies beyond the universe's last
    #    observed trade_date on EITHER leg, return the controlled-
    #    error envelope. Skipped when as_of_date is None — the
    #    post-fetch data-max anchor is honest by construction.
    # ------------------------------------------------------------------
    requested_as_of: Optional[date] = params.as_of_date
    if requested_as_of is not None:
        for leg_label, leg_position in (
            ("short", params.strip_position_short),
            ("long", params.strip_position_long),
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
    # 2. Date window. Same buffer-from-z-window calendar-day math the
    #    sibling outright tool uses.
    # ------------------------------------------------------------------
    buffer_calendar_days = int(z_window * buffer_mult)
    fetch_anchor: Optional[date] = requested_as_of
    if fetch_anchor is None:
        fetch_anchor = date.today()
    start_date = fetch_anchor - timedelta(
        days=params.lookback_days + buffer_calendar_days
    )

    # ------------------------------------------------------------------
    # 3. Fetch per-leg reference metadata (as_of-bounded SCD2 lookup)
    #    + the two-leg price history in a single strip-group call.
    # ------------------------------------------------------------------
    reference_short = fetch_strip_position_reference(
        engine=engine,
        curve_family=params.curve_family,
        strip_position=params.strip_position_short,
        as_of_date=fetch_anchor,
    )
    if reference_short is None:
        return {
            "error": (
                f"No policy-futures strip slot found for "
                f"curve_family='{params.curve_family}', "
                f"strip_position_short="
                f"{params.strip_position_short}. Verify the "
                "(curve_family, strip_position) pair exists on "
                "instrument_master as a strip-position-keyed rolling "
                "contract (policy_futures.yml universe)."
            )
        }
    reference_long = fetch_strip_position_reference(
        engine=engine,
        curve_family=params.curve_family,
        strip_position=params.strip_position_long,
        as_of_date=fetch_anchor,
    )
    if reference_long is None:
        return {
            "error": (
                f"No policy-futures strip slot found for "
                f"curve_family='{params.curve_family}', "
                f"strip_position_long="
                f"{params.strip_position_long}. Verify the "
                "(curve_family, strip_position) pair exists on "
                "instrument_master as a strip-position-keyed rolling "
                "contract (policy_futures.yml universe)."
            )
        }

    inverse_short_raw = reference_short.get("inverse_pricing")
    inverse_long_raw = reference_long.get("inverse_pricing")
    if inverse_short_raw is None or inverse_long_raw is None:
        return {
            "error": (
                f"Policy-futures strip {params.curve_family} "
                f"({params.strip_position_short},"
                f"{params.strip_position_long}) is missing the "
                "'inverse_pricing' flag on instrument_master.attributes "
                f"(short_present={inverse_short_raw is not None}, "
                f"long_present={inverse_long_raw is not None}). The "
                "implied-rate conversion rule is metadata-driven "
                "(PR8 / P6 — no hidden methodology in code); a strip "
                "without this flag cannot be priced honestly. Surface "
                "this as a metadata gap to the playbook owner."
            )
        }
    inverse_short = bool(inverse_short_raw)
    inverse_long = bool(inverse_long_raw)
    if inverse_short != inverse_long:
        # Defensive metadata-hygiene check: same-curve calendar
        # spreads inherently share the inverse_pricing flag in the V1
        # universe; refusal here keeps the implied-rate spread
        # mathematically honest if a future ingestion drift makes one
        # leg direct-priced and one inverse-priced on the same curve.
        return {
            "error": (
                f"Policy-futures legs for {params.curve_family} differ "
                f"on the 'inverse_pricing' flag "
                f"(short_position={params.strip_position_short} → "
                f"{inverse_short!r}, long_position="
                f"{params.strip_position_long} → {inverse_long!r}). A "
                "same-curve calendar spread requires both legs to use "
                "the same price-to-rate convention; refusing to "
                "produce a mixed-convention snapshot (P5)."
            )
        }
    inverse_priced: bool = inverse_short  # == inverse_long

    # ------------------------------------------------------------------
    # 4. Fetch the two legs in one DB round-trip. fetch_strip_group
    #    does not currently expose an end_date filter; we post-filter
    #    the resulting frame so the as_of_date anchor remains
    #    deterministic. See module docstring for the rationale.
    # ------------------------------------------------------------------
    raw_df = fetch_strip_group(
        engine=engine,
        curve_family=params.curve_family,
        strip_positions=[
            params.strip_position_short,
            params.strip_position_long,
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
                f"{params.strip_position_short},"
                f"{params.strip_position_long}), "
                f"field='{field_name_resolved}' since "
                f"{start_date.isoformat()}. Please verify both strip "
                "slots have ingested PX_LAST observations."
            )
        }

    # If as_of_date is explicit, clip rows past it in pandas (the
    # strip-group fetcher does not yet carry end_date — see module
    # docstring).
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
                    f"{params.strip_position_short},"
                    f"{params.strip_position_long}), "
                    f"field='{field_name_resolved}' on or before "
                    f"as_of_date={requested_as_of.isoformat()}."
                )
            }

    # ------------------------------------------------------------------
    # 5. Verify both legs are present in the fetched window.
    # ------------------------------------------------------------------
    available_positions = set(int(p) for p in raw_df["strip_position"].unique())
    required_positions = {
        params.strip_position_short,
        params.strip_position_long,
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
    # 6. Pivot → wide format (date × strip_position) and ffill across
    #    holiday gaps per leg. Then DROP rows missing either leg
    #    (i.e. take the intersection of trading days). This is the
    #    same idiom sovereign curve_spread uses with
    #    ``pivot_and_align_tenors(key_col='tenor')``.
    # ------------------------------------------------------------------
    wide = pivot_and_align_tenors(
        raw_df,
        required_tenors=(
            params.strip_position_short,
            params.strip_position_long,
        ),
        key_col="strip_position",
        ffill_limit=ffill_limit,
    )
    if wide.empty:
        return {
            "error": (
                f"After aligning dates for strip_positions "
                f"({params.strip_position_short},"
                f"{params.strip_position_long}) on "
                f"'{params.curve_family}', no overlapping observations "
                "remain."
            )
        }

    # ------------------------------------------------------------------
    # 7. Build per-leg implied-rate series from the per-leg raw-price
    #    series. The conversion rule is the SAME one the sibling
    #    futures_price_level uses; both legs share the inverse_pricing
    #    flag for same-curve calendar spreads.
    # ------------------------------------------------------------------
    raw_short = wide[params.strip_position_short]
    raw_long = wide[params.strip_position_long]
    if inverse_priced:
        implied_short = 100.0 - raw_short
        implied_long = 100.0 - raw_long
    else:
        implied_short = raw_short.copy()
        implied_long = raw_long.copy()

    # ------------------------------------------------------------------
    # 8. Compute spread series on each axis.
    # ------------------------------------------------------------------
    raw_price_spread_series = raw_short - raw_long
    spread_implied_rate_series = implied_short - implied_long

    # ------------------------------------------------------------------
    # 9. Z-score on the IMPLIED-RATE-SPREAD axis.
    # ------------------------------------------------------------------
    z_series = rolling_zscore(
        spread_implied_rate_series,
        window=z_window,
        min_periods=z_min_periods,
        ddof=z_ddof,
        round_decimals=z_score_round_decimals,
    )
    z_score = safe_float(z_series.iloc[-1], decimals=z_score_round_decimals)

    # ------------------------------------------------------------------
    # 10. 1-day raw subtractions on each axis. Computed independently
    #     on each series so the direct-priced branch comes through
    #     unchanged (for inverse-priced strips the two deltas are
    #     exact negatives by construction).
    # ------------------------------------------------------------------
    daily_change_raw_price_spread: Optional[float]
    daily_change_spread_implied_rate_pct: Optional[float]
    if len(raw_price_spread_series) >= daily_offset_rows:
        cur_raw = raw_price_spread_series.iloc[-1]
        prev_raw = raw_price_spread_series.iloc[-daily_offset_rows]
        if pd.isna(cur_raw) or pd.isna(prev_raw):
            daily_change_raw_price_spread = None
        else:
            daily_change_raw_price_spread = round(
                float(cur_raw) - float(prev_raw),
                raw_price_round_decimals,
            )
        cur_rate = spread_implied_rate_series.iloc[-1]
        prev_rate = spread_implied_rate_series.iloc[-daily_offset_rows]
        if pd.isna(cur_rate) or pd.isna(prev_rate):
            daily_change_spread_implied_rate_pct = None
        else:
            daily_change_spread_implied_rate_pct = round(
                float(cur_rate) - float(prev_rate),
                implied_rate_round_decimals,
            )
    else:
        daily_change_raw_price_spread = None
        daily_change_spread_implied_rate_pct = None

    # ------------------------------------------------------------------
    # 11. Trailing high/low/mid + percentile on the implied-rate-spread
    #     axis. The raw-price-spread axis range is not surfaced on the
    #     snapshot in V1 (see config.yaml planned_extensions); for
    #     inverse-priced strips it is mechanically derivable.
    # ------------------------------------------------------------------
    rate_high, rate_low, percentile = trailing_high_low_percentile(
        spread_implied_rate_series,
        window=trailing_window,
        decimals=implied_rate_high_low_round_decimals,
    )
    percentile_rounded: Optional[float]
    if percentile is None:
        percentile_rounded = None
    else:
        percentile_rounded = round(
            float(percentile), percentile_round_decimals
        )
    rate_mid: Optional[float]
    if rate_high is None or rate_low is None:
        rate_mid = None
    else:
        rate_mid = round(
            (float(rate_high) + float(rate_low)) / 2.0,
            implied_rate_high_low_round_decimals,
        )

    # ------------------------------------------------------------------
    # 12. Current values + observation_count anchored to the data's
    #     latest aligned observation date.
    # ------------------------------------------------------------------
    current_raw_price_spread = safe_float(
        raw_price_spread_series.iloc[-1],
        decimals=raw_price_round_decimals,
    )
    current_spread_implied_rate_pct = safe_float(
        spread_implied_rate_series.iloc[-1],
        decimals=implied_rate_round_decimals,
    )

    as_of_date_resolved = raw_price_spread_series.index[-1].date()
    cutoff = pd.Timestamp(
        as_of_date_resolved - timedelta(days=params.lookback_days)
    )
    display_raw_spread = raw_price_spread_series.loc[
        raw_price_spread_series.index >= cutoff
    ]
    display_implied_spread = spread_implied_rate_series.loc[
        spread_implied_rate_series.index >= cutoff
    ]
    obs_count = len(display_raw_spread)
    if obs_count == 0:
        return {
            "error": (
                f"No observations within the last {params.lookback_days} "
                f"days for {params.curve_family} "
                f"strip_positions=("
                f"{params.strip_position_short},"
                f"{params.strip_position_long})."
            )
        }

    # ------------------------------------------------------------------
    # 13. Build the snapshot.
    # ------------------------------------------------------------------
    contract_code_short = reference_short["contract_code"]
    contract_code_long = reference_long["contract_code"]
    spread_label = f"{contract_code_short}-{contract_code_long}"

    methodology_disclosure = _build_methodology_disclosure(
        curve_family=params.curve_family,
        strip_position_short=params.strip_position_short,
        strip_position_long=params.strip_position_long,
        contract_code_short=contract_code_short,
        contract_code_long=contract_code_long,
        inverse_priced=inverse_priced,
        short_rate_regime=short_rate_regime,
        z_window_days=z_window,
        trailing_window_days=trailing_window,
    )

    metrics = FuturesCalendarSpreadCurrentMetrics(
        as_of_date=as_of_date_resolved.strftime("%Y-%m-%d"),
        curve_family=params.curve_family,
        strip_position_short=params.strip_position_short,
        strip_position_long=params.strip_position_long,
        spread_label=spread_label,
        contract_code_short=contract_code_short,
        contract_code_long=contract_code_long,
        underlying_contract_code_short=reference_short.get(
            "underlying_contract_code"
        ),
        underlying_contract_code_long=reference_long.get(
            "underlying_contract_code"
        ),
        security_name_short=reference_short.get("security_name"),
        security_name_long=reference_long.get("security_name"),
        expiry_date_short=_format_expiry(reference_short.get("expiry_date")),
        expiry_date_long=_format_expiry(reference_long.get("expiry_date")),
        inverse_priced=inverse_priced,
        short_rate_regime=short_rate_regime,
        raw_price_spread=current_raw_price_spread,
        spread_implied_rate_pct=current_spread_implied_rate_pct,
        daily_change_raw_price_spread=daily_change_raw_price_spread,
        daily_change_spread_implied_rate_pct=(
            daily_change_spread_implied_rate_pct
        ),
        z_score_spread_implied_rate=z_score,
        high_252d_spread_implied_rate_pct=rate_high,
        low_252d_spread_implied_rate_pct=rate_low,
        mid_252d_spread_implied_rate_pct=rate_mid,
        percentile_252d=percentile_rounded,
        rolling_window_days=z_window,
        observation_count=obs_count,
    )

    # ------------------------------------------------------------------
    # 14. Bespoke time_series (see schemas docstring for why two unit
    #     spaces side-by-side rather than canonical TimeSeries). Built
    #     from the SAME display slices the snapshot was computed
    #     against, rounded with the SAME conventions, so the snapshot
    #     equals ``time_series[-1]`` STRICTLY at the latest row.
    # ------------------------------------------------------------------
    time_series = _build_time_series(
        display_raw_spread=display_raw_spread,
        display_implied_spread=display_implied_spread,
        raw_price_round_decimals=raw_price_round_decimals,
        implied_rate_round_decimals=implied_rate_round_decimals,
    )

    output = FuturesCalendarSpreadOutput(
        current_metrics=metrics,
        time_series=time_series,
        methodology_disclosure=methodology_disclosure,
    )
    return output.model_dump()


# ============================================================================
# BESPOKE TIME-SERIES BUILDER
# ============================================================================

def _build_time_series(
    *,
    display_raw_spread: pd.Series,
    display_implied_spread: pd.Series,
    raw_price_round_decimals: int,
    implied_rate_round_decimals: int,
) -> list[FuturesCalendarSpreadTimeSeriesRow]:
    """Convert the cleaned, in-window per-axis spread series into the
    bespoke per-row ``{date, raw_price_spread, spread_implied_rate_pct}``
    list the schema expects.

    Each row is rounded with the same conventions the snapshot uses so
    the snapshot equals ``time_series[-1]`` byte-for-byte at the
    latest row. Skips rows where EITHER axis is NaN.
    """
    rows: list[FuturesCalendarSpreadTimeSeriesRow] = []
    for ts, raw_v in display_raw_spread.items():
        if pd.isna(raw_v):
            continue
        try:
            rate_v = display_implied_spread.loc[ts]
        except KeyError:
            continue
        if pd.isna(rate_v):
            continue
        rows.append(
            FuturesCalendarSpreadTimeSeriesRow(
                date=ts.strftime("%Y-%m-%d"),
                raw_price_spread=round(
                    float(raw_v), raw_price_round_decimals,
                ),
                spread_implied_rate_pct=round(
                    float(rate_v), implied_rate_round_decimals,
                ),
            )
        )
    return rows
