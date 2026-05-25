"""
compute.py — Config-driven policy-futures pack-average monitor (V1)
====================================================================

Same-curve pack-average implied rate (simple arithmetic mean of four
strip-position legs) for ONE policy-futures ``curve_family``: whites
= strip positions 1-4; reds = strip positions 5-8. Reads all four
legs in a single ``fetch_strip_group`` round-trip, converts each to
implied-rate space per the per-strip ``inverse_priced`` flag, aligns
on the intersection of trading days, and emits a snapshot of:

  - current ``pack_average_implied_rate_pct`` in PERCENT
    (arithmetic mean of the four per-leg implied rates)
  - per-leg current implied rates (``implied_rates_pct[i]``)
  - 1-day raw subtraction on the pack-average axis (PERCENT POINTS)
  - rolling 252-day z-score on the PACK-AVERAGE series
  - trailing 252-day high / low / mid on the pack-average axis
  - percentile rank of the current pack average in the trailing
    252-day range
  - observation_count over the LLM-supplied lookback_days window
  - per-leg as_of-bounded SCD2 disclosure block
  - the methodology disclosure (P5 / ADR 0013 — simple arithmetic-
    mean weighting, regime label, inverse-pricing rule, z window,
    trailing window, scope-limit refusals)

Arithmetic mean — the desk-standard pack weighting
--------------------------------------------------
``pack_average_implied_rate_pct = mean(implied_rate_pct_position_i for i in pack_positions)``

The catalog's methodology guardrail REQUIRES the simple arithmetic
mean (the STIR-desk shorthand IS the arithmetic mean — that is the
quantity quoted on the morning call). Duration-weighted / DV01-
weighted variants are PR11 planned-extension territory and ship as
separate primitives; the methodology card refuses to mix them under
the ``pack_average_simple`` name.

ADR 0013 V1 HARD scope — EUR_SHORT_RATE_FUT refusal
---------------------------------------------------
SOFR_FUT and SONIA_FUT pack averages build cleanly (uniform
quarterly serial strips with one delivery-month type). The
Euribor strip (EUR_SHORT_RATE_FUT) interleaves serial and
quarterly contracts at the front — a 1-4 pack average that does
NOT distinguish them silently mixes structurally different delivery
cadences (P5 dishonesty). ``policy_futures.yml`` does not yet carry
per-row ``delivery_month_type`` metadata to disambiguate, so this
primitive raises ``NotImplementedError`` for
``curve_family == EUR_SHORT_RATE_FUT`` with a message naming the
missing playbook metadata AND citing ADR 0013 as the source of the
refusal rule. The MCP wrapper catches this and surfaces a clean
``{"error": "..."}`` envelope so the LLM sees a stable wire shape.

Why ``fetch_strip_group`` (not four ``fetch_strip_position`` calls)
-------------------------------------------------------------------
The catalog names ``fetch_strip_group`` as the fetcher surface for
this primitive — one DB round-trip for all four legs. The function
returns the long-format ``(trade_date, strip_position, field_value)``
DataFrame the pack average needs; we pivot on ``strip_position`` via
``pivot_and_align_tenors(key_col='strip_position')`` to align the
four legs on a wide ``(date × strip_position)`` frame and forward-
fill holiday gaps up to the YAML's ``ffill_limit_days``.

``fetch_strip_group`` does NOT carry an ``end_date`` parameter
today; to keep the deterministic-anchoring contract — explicit
``as_of_date`` ⇒ snapshot anchored at that date — the post-fetch
step filters rows by ``trade_date <= as_of_date`` in pandas.
Mirrors the sibling ``futures_butterfly_simple`` exactly.

Per-leg → implied-rate conversion
---------------------------------
The implied-rate conversion per leg is the SAME rule the siblings
``futures_price_level`` / ``futures_calendar_spread`` /
``futures_butterfly_simple`` use: for ``inverse_priced=True``
strips, ``implied_rate_pct = 100 - raw_price``; for direct-priced
strips, ``implied_rate_pct = raw_price``. All four legs must agree
on the ``inverse_pricing`` flag — same-curve packs inherently share
the flag, but we verify and refuse with the controlled-error
envelope if metadata drifts.

Honest placeholder
------------------
``trailing_range_window_days`` is locked at 252 in V1. The output
schema's field names embed the number; the compute path raises
``NotImplementedError`` if this is set to anything else.

Deterministic anchoring (PR8 + PR16)
------------------------------------
- ``None`` ⇒ anchor at the universe's last observed ``trade_date``
  where ALL FOUR legs have a value after intersection.
- explicit date BEYOND any leg's last ``trade_date`` ⇒ controlled-
  error envelope (``no scoreable strip: ...``).
- explicit date WITHIN the universe range ⇒ post-fetch filtered to
  ``trade_date <= as_of_date`` for determinism.

DB access
---------
Reaches the DB through the shared ``fetch_strip_group`` (all four
legs in one call), ``fetch_strip_position_reference`` (per-leg
metadata bounded by ``as_of_date``), and
``fetch_strip_position_max_date`` (per-leg future-anchor probe)
helpers. NO raw SQL in this file.

Test seam
---------
``fetch_strip_group``, ``fetch_strip_position_reference``,
``fetch_strip_position_max_date``, and ``date`` are imported here
at module level; tests patch them via
``patch("rates_agent.policy_futures.tools.futures_pack_average_simple.compute.X")``.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

import pandas as pd
from sqlalchemy.engine import Engine

from rates_agent.policy_futures.tools.futures_pack_average_simple.schemas import (
    FuturesPackAverageSimpleCurrentMetrics,
    FuturesPackAverageSimpleInput,
    FuturesPackAverageSimpleOutput,
    FuturesPackAverageSimpleTimeSeriesRow,
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
from shared.schemas import TimeSeries, TimeSeriesRow, TimeSeriesUnits


# Bundled config — public symbol so external callers (mcp_server,
# tests, future REST routes) can build a ToolConfig from the same
# source the tool uses.
CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


# Trailing-range window is wire-frozen at 252 in V1.
_FROZEN_TRAILING_WINDOW: int = 252


# Curve families that require per-row delivery-month-type metadata
# before a pack-average can be computed honestly. ADR 0013 V1 names
# EUR_SHORT_RATE_FUT explicitly; the gate refuses any such family.
_BUBA_MIX_CURVE_FAMILIES: frozenset[str] = frozenset({"EUR_SHORT_RATE_FUT"})


# ============================================================================
# CONFIG HELPERS
# ============================================================================

def _parse_regime_map(csv_value: str) -> Mapping[str, str]:
    """Parse the YAML's ``short_rate_regime_map`` CSV into a dict."""
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


def _parse_pack_positions(csv_value: str) -> Tuple[int, ...]:
    """Parse one of the YAML pack-positions CSVs into an ordered
    tuple of positive integers. The tuple ordering IS the wire
    ordering — preserves the desk's left-to-right pack reading."""
    out: list[int] = []
    for chunk in csv_value.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            pos = int(chunk)
        except ValueError as exc:
            raise ValueError(
                f"pack strip-position entry {chunk!r} is not an "
                f"integer; expected 'N,N,N,N' (CSV-joined). "
                f"Detail: {exc}"
            ) from exc
        if pos < 1:
            raise ValueError(
                f"pack strip-position entry {pos} is not a positive "
                "integer (1-based strip positions only)."
            )
        out.append(pos)
    if len(out) != 4:
        raise ValueError(
            f"pack strip-positions must list exactly 4 positions; "
            f"got {out}. Whites = 1-4 / reds = 5-8 are canonical "
            "STIR conventions; reconfiguring requires an ADR."
        )
    return tuple(out)


def _check_trailing_window(config: ToolConfig) -> int:
    """Verify ``trailing_range_window_days`` is the wire-frozen value
    and return it. Raises ``NotImplementedError`` otherwise."""
    trailing = config.convention_value("trailing_range_window_days")
    if trailing != _FROZEN_TRAILING_WINDOW:
        raise NotImplementedError(
            f"trailing_range_window_days={trailing!r} is documented in "
            f"this tool's config.yaml as a future-supported value (see "
            f"methodology.planned_extensions) but is not yet "
            f"implemented. V1 supports only {_FROZEN_TRAILING_WINDOW} "
            f"because the output field names "
            f"(high_252d_pack_average_implied_rate_pct, "
            f"low_252d_pack_average_implied_rate_pct, "
            f"mid_252d_pack_average_implied_rate_pct, percentile_252d) "
            f"embed that number on the wire. Either restore the value "
            f"to {_FROZEN_TRAILING_WINDOW} or implement the schema "
            f"rename + frontend update documented in "
            f"planned_extensions."
        )
    return trailing


def _format_expiry(value: Any) -> Optional[str]:
    """Render a per-leg ``expiry_date`` value as a YYYY-MM-DD string
    if present; None pass-through."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    return str(value)


# ============================================================================
# DISCLOSURE BUILDER
# ============================================================================

def _build_methodology_disclosure(
    *,
    curve_family: str,
    pack: str,
    strip_positions: Tuple[int, ...],
    contract_codes: List[str],
    inverse_priced: bool,
    short_rate_regime: str,
    z_window_days: int,
    trailing_window_days: int,
) -> str:
    """Compose the P5 / ADR 0013 disclosure string with runtime
    context so consumers see the exact regime + conversion rule +
    weighting that produced the snapshot."""
    if inverse_priced:
        rule = (
            "Inverse-priced strip — per leg, implied_rate_pct = 100 "
            "- raw_price; the pack average on the implied-rate axis "
            "equals the simple arithmetic mean of the four legs' "
            "implied rates."
        )
    else:
        rule = (
            "Direct-priced strip — per leg, implied_rate_pct = "
            "raw_price; the pack average equals the simple "
            "arithmetic mean of the four legs' prices."
        )
    positions_csv = ",".join(str(p) for p in strip_positions)
    codes_csv = ",".join(contract_codes)
    return (
        f"Rolling-generic strip pack average at {curve_family} "
        f"pack={pack} (strip positions [{positions_csv}], master "
        f"stems [{codes_csv}]). Weighting: SIMPLE ARITHMETIC MEAN "
        f"(each pack member carries weight 1/4 = 0.25). The desk-"
        f"recognised quantity is the pack-average implied rate in "
        f"PERCENT. Underlying short-rate regime: {short_rate_regime} "
        f"(RFR = compounded daily risk-free rate; IBOR = unsecured "
        f"3M term IBOR). {rule} Z-score lookback = {z_window_days} "
        f"trading days on the PACK-AVERAGE series; trailing range "
        f"window = {trailing_window_days} trading days. This is NOT "
        f"a CTD-of-futures-of-OIS pack average; the CTD-implied-OIS "
        f"curve is not yet a primitive in this build. This is also "
        f"NOT a meeting-by-meeting policy-path decomposition. This "
        f"is also NOT a duration-weighted / DV01-weighted / "
        f"regression-fitted pack average — those weighting variants "
        f"are planned-extension territory and ship as separate "
        f"primitives (ADR 0013 V1 scope — policy_futures ships "
        f"strip-position-keyed monitors only)."
    )


# ============================================================================
# PUBLIC API
# ============================================================================

def calculate_futures_pack_average_simple(
    engine: Engine,
    params: FuturesPackAverageSimpleInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    """Return the same-curve pack-average implied rate (arithmetic
    mean of the four pack-member legs) on the policy-futures strip
    plus 1-day delta, 252-day z-score on the pack-average series,
    trailing 252-day high/low/mid/percentile, observation_count,
    per-leg current implied rates, the per-leg as_of-bounded SCD2
    disclosure block, the canonical TimeSeries exports, and the P5
    methodology disclosure.

    Parameters
    ----------
    engine : Engine
        Live SQLAlchemy engine.
    params : FuturesPackAverageSimpleInput
        Validated input. ``field_name=None`` resolves against the
        YAML's ``default_price_field`` convention; ``as_of_date=None``
        anchors at the post-fetch data-max date where all four pack
        legs are observed.
    config : ToolConfig, optional
        Bundled config.yaml is auto-loaded when None.

    Returns
    -------
    dict
        Serialised ``FuturesPackAverageSimpleOutput``, or
        ``{"error": "..."}`` on recoverable failure.

    Raises
    ------
    NotImplementedError
        When ``curve_family`` is in the ADR-0013-V1 refusal set
        (currently ``{EUR_SHORT_RATE_FUT}``) because the playbook
        does not yet carry per-row ``delivery_month_type`` metadata.
        The MCP wrapper catches this and serialises a clean
        ``{"error": "..."}`` envelope so the wire shape stays
        stable.
    """
    if config is None:
        config = load_tool_config(CONFIG_PATH)

    # ------------------------------------------------------------------
    # ADR 0013 V1 hard gate — refuse EUR_SHORT_RATE_FUT and any other
    # curve_family in the buba-mix refusal set BEFORE any DB work.
    # The schema admits the value so the LLM can ask for it; the gate
    # raises NotImplementedError naming the missing playbook metadata
    # AND citing ADR 0013 — the MCP wrapper turns this into the
    # controlled {"error": "..."} envelope.
    # ------------------------------------------------------------------
    if params.curve_family in _BUBA_MIX_CURVE_FAMILIES:
        raise NotImplementedError(
            f"Pack-average primitive refuses curve_family="
            f"{params.curve_family!r} in ADR 0013 V1 scope. The "
            f"Euribor strip interleaves serial and quarterly "
            f"contracts at the front; a simple arithmetic mean "
            f"across positions 1-4 (or 5-8) would silently mix "
            f"structurally different delivery cadences. "
            f"``rates_agent/playbooks/policy_futures.yml`` does not "
            f"yet carry per-row ``delivery_month_type`` "
            f"(``serial``|``quarterly``) metadata; once it does, "
            f"this primitive can either filter to a uniform cadence "
            f"per pack OR require a homogeneous-cadence pack and "
            f"refuse mixed packs with a clearer error. Until that "
            f"playbook metadata lands, the refusal here is the "
            f"honest behaviour (see ADR 0013 — "
            f"docs_revamped/05_decisions/0013-futures-domain-"
            f"agents.md — and the methodology.planned_extensions "
            f"block in this tool's config.yaml for the unblock "
            f"path). SOFR_FUT and SONIA_FUT pack averages build "
            f"cleanly."
        )

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
    pack_average_round_decimals = config.convention_value(
        "pack_average_round_decimals"
    )
    implied_rate_round_decimals = config.convention_value(
        "implied_rate_round_decimals"
    )
    z_score_round_decimals = config.convention_value("z_score_round_decimals")
    pack_average_high_low_round_decimals = config.convention_value(
        "pack_average_high_low_round_decimals"
    )
    percentile_round_decimals = config.convention_value(
        "percentile_round_decimals"
    )
    regime_map = _parse_regime_map(
        config.convention_value("short_rate_regime_map")
    )
    whites_positions = _parse_pack_positions(
        config.convention_value("whites_strip_positions")
    )
    reds_positions = _parse_pack_positions(
        config.convention_value("reds_strip_positions")
    )

    if params.pack == "whites":
        pack_positions: Tuple[int, ...] = whites_positions
    elif params.pack == "reds":
        pack_positions = reds_positions
    else:
        # Closed-Literal schema guarantees this branch is unreachable
        # under normal use; kept as a defensive guard for future enum
        # extensions that forget to wire compute().
        return {
            "error": (
                f"Unknown pack={params.pack!r}; expected 'whites' or "
                "'reds'."
            )
        }

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

    field_name_resolved = (
        params.field_name if params.field_name is not None else default_field_name
    )

    # ------------------------------------------------------------------
    # 1. Future-anchor guard (PR8 + PR16) — per leg
    # ------------------------------------------------------------------
    requested_as_of: Optional[date] = params.as_of_date
    if requested_as_of is not None:
        for leg_position in pack_positions:
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
                        f"{params.curve_family} pack={params.pack} "
                        f"strip_position={leg_position}. The snapshot "
                        "refuses to silently re-label an unbounded "
                        "read as a future-anchored read."
                    )
                }

    # ------------------------------------------------------------------
    # 2. Date window
    # ------------------------------------------------------------------
    buffer_calendar_days = int(z_window * buffer_mult)
    fetch_anchor: Optional[date] = requested_as_of
    if fetch_anchor is None:
        fetch_anchor = date.today()
    start_date = fetch_anchor - timedelta(
        days=params.lookback_days + buffer_calendar_days
    )

    # ------------------------------------------------------------------
    # 3. Per-leg reference metadata (SCD2 as-of lookup)
    # ------------------------------------------------------------------
    references: list[dict] = []
    for leg_position in pack_positions:
        reference = fetch_strip_position_reference(
            engine=engine,
            curve_family=params.curve_family,
            strip_position=leg_position,
            as_of_date=fetch_anchor,
        )
        if reference is None:
            return {
                "error": (
                    f"No policy-futures strip slot found for "
                    f"curve_family='{params.curve_family}', "
                    f"strip_position={leg_position} (pack="
                    f"{params.pack}). Verify the (curve_family, "
                    "strip_position) pair exists on instrument_master "
                    "as a strip-position-keyed rolling contract "
                    "(policy_futures.yml universe)."
                )
            }
        references.append(reference)

    inverse_flags: list[bool] = []
    for ref, leg_position in zip(references, pack_positions):
        raw_flag = ref.get("inverse_pricing")
        if raw_flag is None:
            return {
                "error": (
                    f"Policy-futures strip {params.curve_family} "
                    f"strip_position={leg_position} (pack="
                    f"{params.pack}) is missing the 'inverse_pricing' "
                    "flag on instrument_master.attributes. The "
                    "implied-rate conversion rule is metadata-driven "
                    "(PR8 / P6 — no hidden methodology in code); a "
                    "strip without this flag cannot be priced "
                    "honestly. Surface this as a metadata gap to the "
                    "playbook owner."
                )
            }
        inverse_flags.append(bool(raw_flag))

    distinct_flags = set(inverse_flags)
    if len(distinct_flags) > 1:
        per_position = ", ".join(
            f"strip_position={p}: inverse_pricing={f!r}"
            for p, f in zip(pack_positions, inverse_flags)
        )
        return {
            "error": (
                f"Policy-futures pack legs on {params.curve_family} "
                f"disagree on the 'inverse_pricing' flag "
                f"({per_position}). A same-curve pack average "
                "requires all four legs to use the same price-to-"
                "rate convention; refusing to produce a mixed-"
                "convention snapshot (P5)."
            )
        }
    inverse_priced: bool = next(iter(distinct_flags))

    # ------------------------------------------------------------------
    # 4. Fetch all four pack legs in one DB round-trip
    # ------------------------------------------------------------------
    raw_df = fetch_strip_group(
        engine=engine,
        curve_family=params.curve_family,
        strip_positions=list(pack_positions),
        field_name=field_name_resolved,
        start_date=start_date,
    )
    if raw_df.empty:
        return {
            "error": (
                f"No policy-futures price data found for "
                f"curve_family='{params.curve_family}', pack="
                f"{params.pack} strip_positions="
                f"{list(pack_positions)}, "
                f"field='{field_name_resolved}' since "
                f"{start_date.isoformat()}. Please verify all four "
                "pack legs have ingested PX_LAST observations."
            )
        }

    if requested_as_of is not None:
        raw_df = raw_df[
            pd.to_datetime(raw_df["trade_date"])
            <= pd.Timestamp(requested_as_of)
        ]
        if raw_df.empty:
            return {
                "error": (
                    f"No policy-futures price data found for "
                    f"curve_family='{params.curve_family}', pack="
                    f"{params.pack} strip_positions="
                    f"{list(pack_positions)}, "
                    f"field='{field_name_resolved}' on or before "
                    f"as_of_date={requested_as_of.isoformat()}."
                )
            }

    available_positions = set(int(p) for p in raw_df["strip_position"].unique())
    required_positions = set(pack_positions)
    missing_positions = required_positions - available_positions
    if missing_positions:
        return {
            "error": (
                f"Missing strip_position data for "
                f"{sorted(missing_positions)} in "
                f"curve_family='{params.curve_family}' pack="
                f"{params.pack}. Available strip_positions in the "
                f"query window: {sorted(available_positions)}."
            )
        }

    # ------------------------------------------------------------------
    # 5. Pivot → wide format (date × strip_position), align, drop rows
    #    missing any leg.
    # ------------------------------------------------------------------
    wide = pivot_and_align_tenors(
        raw_df,
        required_tenors=pack_positions,
        key_col="strip_position",
        ffill_limit=ffill_limit,
    )
    if wide.empty:
        return {
            "error": (
                f"After aligning dates for pack={params.pack} "
                f"strip_positions={list(pack_positions)} on "
                f"'{params.curve_family}', no overlapping "
                "observations remain."
            )
        }

    # ------------------------------------------------------------------
    # 6. Per-leg implied-rate series — metadata-driven inversion
    # ------------------------------------------------------------------
    implied_series_by_position: Dict[int, pd.Series] = {}
    for leg_position in pack_positions:
        raw_series = wide[leg_position]
        if inverse_priced:
            implied_series_by_position[leg_position] = 100.0 - raw_series
        else:
            implied_series_by_position[leg_position] = raw_series.copy()

    # ------------------------------------------------------------------
    # 7. Compute pack-average series — simple arithmetic mean
    # ------------------------------------------------------------------
    pack_average_series = sum(
        implied_series_by_position[p] for p in pack_positions
    ) / float(len(pack_positions))

    # ------------------------------------------------------------------
    # 8. Z-score on the pack-average series
    # ------------------------------------------------------------------
    z_series = rolling_zscore(
        pack_average_series,
        window=z_window,
        min_periods=z_min_periods,
        ddof=z_ddof,
        round_decimals=z_score_round_decimals,
    )
    z_score = safe_float(z_series.iloc[-1], decimals=z_score_round_decimals)

    # ------------------------------------------------------------------
    # 9. 1-day raw subtraction on the pack-average axis
    # ------------------------------------------------------------------
    daily_change_pack_average: Optional[float]
    if len(pack_average_series) >= daily_offset_rows:
        cur_val = pack_average_series.iloc[-1]
        prev_val = pack_average_series.iloc[-daily_offset_rows]
        if pd.isna(cur_val) or pd.isna(prev_val):
            daily_change_pack_average = None
        else:
            daily_change_pack_average = round(
                float(cur_val) - float(prev_val),
                pack_average_round_decimals,
            )
    else:
        daily_change_pack_average = None

    # ------------------------------------------------------------------
    # 10. Trailing high/low/mid + percentile on the pack-average axis
    # ------------------------------------------------------------------
    pack_high, pack_low, percentile = trailing_high_low_percentile(
        pack_average_series,
        window=trailing_window,
        decimals=pack_average_high_low_round_decimals,
    )
    percentile_rounded: Optional[float]
    if percentile is None:
        percentile_rounded = None
    else:
        percentile_rounded = round(
            float(percentile), percentile_round_decimals
        )
    pack_mid: Optional[float]
    if pack_high is None or pack_low is None:
        pack_mid = None
    else:
        pack_mid = round(
            (float(pack_high) + float(pack_low)) / 2.0,
            pack_average_high_low_round_decimals,
        )

    # ------------------------------------------------------------------
    # 11. Current values + observation_count
    # ------------------------------------------------------------------
    current_pack_average = safe_float(
        pack_average_series.iloc[-1],
        decimals=pack_average_round_decimals,
    )
    current_per_leg: List[float] = []
    for leg_position in pack_positions:
        leg_value = safe_float(
            implied_series_by_position[leg_position].iloc[-1],
            decimals=implied_rate_round_decimals,
        )
        # safe_float can return None; the snapshot schema requires a
        # float, and reaching here implies the aligned row exists
        # (else wide would be empty / row dropped). Defensive None
        # check → error envelope.
        if leg_value is None:
            return {
                "error": (
                    f"Per-leg implied rate for "
                    f"{params.curve_family} strip_position="
                    f"{leg_position} (pack={params.pack}) is NaN at "
                    "the aligned anchor date — refusing to emit a "
                    "snapshot that hides a missing leg."
                )
            }
        current_per_leg.append(leg_value)

    as_of_date_resolved = pack_average_series.index[-1].date()
    cutoff = pd.Timestamp(
        as_of_date_resolved - timedelta(days=params.lookback_days)
    )
    display_pack_average = pack_average_series.loc[
        pack_average_series.index >= cutoff
    ]
    display_zscore = z_series.loc[z_series.index >= cutoff]
    obs_count = len(display_pack_average.dropna())
    if obs_count == 0:
        return {
            "error": (
                f"No observations within the last "
                f"{params.lookback_days} days for "
                f"{params.curve_family} pack={params.pack} "
                f"strip_positions={list(pack_positions)}."
            )
        }

    # ------------------------------------------------------------------
    # 12. Build the snapshot
    # ------------------------------------------------------------------
    contract_codes: List[str] = [ref["contract_code"] for ref in references]
    pack_label = (
        f"{params.curve_family} {params.pack} "
        f"({contract_codes[0]}..{contract_codes[-1]})"
    )
    underlying_contract_codes: List[Optional[str]] = [
        ref.get("underlying_contract_code") for ref in references
    ]
    security_names: List[Optional[str]] = [
        ref.get("security_name") for ref in references
    ]
    expiry_dates: List[Optional[str]] = [
        _format_expiry(ref.get("expiry_date")) for ref in references
    ]

    methodology_disclosure = _build_methodology_disclosure(
        curve_family=params.curve_family,
        pack=params.pack,
        strip_positions=pack_positions,
        contract_codes=contract_codes,
        inverse_priced=inverse_priced,
        short_rate_regime=short_rate_regime,
        z_window_days=z_window,
        trailing_window_days=trailing_window,
    )

    metrics = FuturesPackAverageSimpleCurrentMetrics(
        as_of_date=as_of_date_resolved.strftime("%Y-%m-%d"),
        curve_family=params.curve_family,
        pack=params.pack,
        strip_positions=list(pack_positions),
        pack_label=pack_label,
        contract_codes=contract_codes,
        underlying_contract_codes=underlying_contract_codes,
        security_names=security_names,
        expiry_dates=expiry_dates,
        inverse_priced=inverse_priced,
        short_rate_regime=short_rate_regime,
        implied_rates_pct=current_per_leg,
        pack_average_implied_rate_pct=current_pack_average,
        daily_change_pack_average_implied_rate_pct=daily_change_pack_average,
        z_score_pack_average=z_score,
        high_252d_pack_average_implied_rate_pct=pack_high,
        low_252d_pack_average_implied_rate_pct=pack_low,
        mid_252d_pack_average_implied_rate_pct=pack_mid,
        percentile_252d=percentile_rounded,
        rolling_window_days=z_window,
        observation_count=obs_count,
    )

    # ------------------------------------------------------------------
    # 13. Bespoke + canonical TimeSeries — built from the SAME display
    #     slices so they cannot drift.
    # ------------------------------------------------------------------
    bespoke_rows = _build_bespoke_time_series(
        display_pack_average=display_pack_average,
        display_zscore=display_zscore,
        pack_average_round_decimals=pack_average_round_decimals,
        z_score_round_decimals=z_score_round_decimals,
    )
    canonical_pack_average = _build_canonical_pack_average(
        display_pack_average=display_pack_average,
        curve_family=params.curve_family,
        pack=params.pack,
        pack_average_round_decimals=pack_average_round_decimals,
    )
    canonical_zscore = _build_canonical_zscore(
        display_zscore=display_zscore,
        curve_family=params.curve_family,
        pack=params.pack,
        z_score_round_decimals=z_score_round_decimals,
    )

    output = FuturesPackAverageSimpleOutput(
        current_metrics=metrics,
        time_series=bespoke_rows,
        time_series_pack_average=canonical_pack_average,
        time_series_zscore=canonical_zscore,
        methodology_disclosure=methodology_disclosure,
    )
    return output.model_dump()


# ============================================================================
# TIME-SERIES BUILDERS
# ============================================================================

def _build_bespoke_time_series(
    *,
    display_pack_average: pd.Series,
    display_zscore: pd.Series,
    pack_average_round_decimals: int,
    z_score_round_decimals: int,
) -> list[FuturesPackAverageSimpleTimeSeriesRow]:
    """Build the bespoke ``{date, pack_average_implied_rate_pct,
    z_score}`` row list. Skips rows where the pack-average value is
    NaN; z_score can legitimately be None during the rolling-window
    warmup."""
    rows: list[FuturesPackAverageSimpleTimeSeriesRow] = []
    for ts, val in display_pack_average.items():
        if pd.isna(val):
            continue
        try:
            z_val = display_zscore.loc[ts]
        except KeyError:
            z_val = None
        z_rounded: Optional[float]
        if z_val is None or pd.isna(z_val):
            z_rounded = None
        else:
            z_rounded = round(float(z_val), z_score_round_decimals)
        rows.append(
            FuturesPackAverageSimpleTimeSeriesRow(
                date=ts.strftime("%Y-%m-%d"),
                pack_average_implied_rate_pct=round(
                    float(val), pack_average_round_decimals,
                ),
                z_score=z_rounded,
            )
        )
    return rows


def _build_canonical_pack_average(
    *,
    display_pack_average: pd.Series,
    curve_family: str,
    pack: str,
    pack_average_round_decimals: int,
) -> TimeSeries:
    """Build the canonical ``TimeSeries`` for the pack-average value
    series (closed-enum ``TimeSeriesUnits.PERCENT``)."""
    series_name = f"{curve_family.lower()}_{pack}_pack_average"
    rows = []
    for ts, val in display_pack_average.items():
        if pd.isna(val):
            continue
        rows.append(
            TimeSeriesRow(
                date=ts.strftime("%Y-%m-%d"),
                value=round(float(val), pack_average_round_decimals),
            )
        )
    return TimeSeries(
        series_name=series_name,
        units=TimeSeriesUnits.PERCENT,
        description=(
            f"Pack-average implied rate (simple arithmetic mean) on "
            f"{curve_family} pack={pack} over the displayed window, "
            f"in PERCENT."
        ),
        rows=rows,
    )


def _build_canonical_zscore(
    *,
    display_zscore: pd.Series,
    curve_family: str,
    pack: str,
    z_score_round_decimals: int,
) -> TimeSeries:
    """Build the canonical ``TimeSeries`` for the rolling z-score
    series (closed-enum ``TimeSeriesUnits.Z_SCORE``). None values
    during the warmup window are preserved."""
    series_name = f"{curve_family.lower()}_{pack}_zscore"
    rows = []
    for ts, z_val in display_zscore.items():
        rows.append(
            TimeSeriesRow(
                date=ts.strftime("%Y-%m-%d"),
                value=safe_float(z_val, decimals=z_score_round_decimals),
            )
        )
    return TimeSeries(
        series_name=series_name,
        units=TimeSeriesUnits.Z_SCORE,
        description=(
            f"Rolling z-score of the pack-average implied rate on "
            f"{curve_family} pack={pack} vs its own trailing window."
        ),
        rows=rows,
    )
