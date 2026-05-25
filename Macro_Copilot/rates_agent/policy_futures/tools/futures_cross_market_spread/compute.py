"""
compute.py — Config-driven policy-futures cross-market-spread
              monitor (V1)
========================================================================

Matched-strip cross-market implied-rate differential between TWO
different ``curve_family`` values at ONE strip position. Reads both
legs in a single ``fetch_cross_market_strip`` round-trip, converts
each to implied-rate space per the per-strip ``inverse_priced`` flag
read INDEPENDENTLY per leg, aligns on the intersection of trading
days (the two markets' trading calendars differ), and emits a snapshot
of:

  - current ``spread_value_pct`` (desk-recognised RAW differential in
    PERCENT POINTS = ``rate_A - rate_B`` where A is the requested
    ``curve_family_a``)
  - per-leg current implied rates (``implied_rate_pct_a`` /
    ``implied_rate_pct_b``)
  - 1-day raw subtraction on the spread axis
  - rolling 252-day z-score on the SPREAD series
  - trailing 252-day high / low / mid on the spread axis
  - percentile rank of the current spread in the trailing 252-day
    range
  - observation_count over the LLM-supplied lookback_days window
  - per-leg as_of-bounded SCD2 disclosure block — including BOTH
    per-leg ``short_rate_regime`` labels (RFR vs IBOR) so mixed-
    regime pairs are surfaced explicitly (catalog guardrail)
  - the methodology disclosure (P5 / ADR 0013 — wire-frozen A − B
    sign convention with specific labels echoed, per-leg regime
    labels, inverse-pricing rule, z window, trailing window, RAW-
    differential label, pack-average refusal)

Sign convention (wire-frozen)
-----------------------------
``spread_value_pct = implied_rate_pct(curve_family_a) -
implied_rate_pct(curve_family_b)``

The orientation is wire-frozen as an invariant — swapping the
inputs flips the sign by construction. The methodology card echoes
the specific A and B curve_family labels back so a desk reader
cannot misread the direction. The input schema's validator enforces
``curve_family_a != curve_family_b``.

Raw cross-market differential (catalog guardrail)
--------------------------------------------------
The catalog's methodology guardrail REQUIRES the output be labelled
as a RAW cross-market implied-rate differential — NOT basis-
adjusted, NOT beta-adjusted. The methodology card carries this
label verbatim. There are NO input knobs for basis / beta selection
— the absence of those knobs is load-bearing on the guardrail.
Basis-adjusted and beta-adjusted variants are PR11 planned-extension
territory and ship as separate primitives.

Mixed RFR/IBOR pair handling (catalog guardrail)
------------------------------------------------
The catalog's methodology guardrail REQUIRES the primitive to:
  - surface benchmark-family caveats explicitly (RFR vs IBOR per
    ADR 0013); AND
  - NOT auto-collapse mixed RFR/IBOR curves into a pack-average —
    refuse and surface the choice.

Implementation: the snapshot carries per-leg
``short_rate_regime_a`` / ``short_rate_regime_b`` labels (NOT a
single collapsed label). The methodology card calls out mixed-
regime pairs in plain English. The implied-rate conversion itself
is the same one-line transform per leg regardless of the regime
(driven off ``inverse_priced``); the regime LABEL is disclosure-
only.

Why ``fetch_cross_market_strip`` (not two ``fetch_strip_position``
calls)
------------------------------------------------------------------
The catalog names ``fetch_cross_market_strip`` as the fetcher
surface for this primitive — one DB round-trip for both legs. The
function returns the long-format ``(trade_date, curve_family,
field_value)`` DataFrame the cross-market spread needs; we pivot
on ``curve_family`` via
``pivot_and_align_tenors(key_col='curve_family')`` to align the two
legs on a wide ``(date × curve_family)`` frame and forward-fill
holiday gaps up to the YAML's ``ffill_limit_days``. Same idiom the
sovereign cross_market_spread tool uses with
``pivot_and_align_tenors(key_col='curve_family')``.

``fetch_cross_market_strip`` does NOT carry an ``end_date``
parameter today (see ``shared/analytics/rates_fetch.py:967``); to
keep the deterministic-anchoring contract — explicit ``as_of_date``
⇒ snapshot anchored at that date — the post-fetch step filters
rows by ``trade_date <= as_of_date`` in pandas. Mirrors the sibling
``futures_calendar_spread`` exactly.

Per-leg → implied-rate conversion
---------------------------------
The implied-rate conversion per leg is the SAME rule the siblings
``futures_price_level`` / ``futures_calendar_spread`` /
``futures_butterfly_simple`` use: for ``inverse_priced=True``
strips, ``implied_rate_pct = 100 - raw_price``; for direct-priced
strips, ``implied_rate_pct = raw_price``. The per-leg flag is read
INDEPENDENTLY (the two markets need not share the flag — same-
strip-position cross-market pairs in the V1 universe happen to
agree, but a future direct-priced family integrates without code
changes because the conversion is metadata-driven).

Both legs must carry the flag — a missing flag on either leg
surfaces as the controlled-error envelope (defensive metadata-
hygiene; PR8 / P6 — no hidden methodology in code).

Methodology disclosure
----------------------
Every response carries a ``methodology_disclosure`` field with:
  - the wire-frozen A − B sign convention with the specific A and B
    labels echoed back,
  - the per-curve_family short-rate regime labels (RFR vs IBOR) for
    BOTH legs, explicitly calling out mixed-regime pairs,
  - the per-leg inverse-pricing rule,
  - the z-score lookback window,
  - the trailing-range window,
  - the matched-strip-position keying,
  - the catalog guardrail labels: RAW differential — NOT basis-
    adjusted, NOT beta-adjusted; basis-adjusted / beta-adjusted
    variants are planned_extension territory per PR11,
  - the explicit refusal of pack-average collapse on mixed-regime
    pairs.
The schema makes the field REQUIRED so a future caller cannot drop
the disclosure when relaying the snapshot.

Honest placeholder
------------------
``trailing_range_window_days`` is locked at 252 in V1. The output
schema's field names (``high_252d_spread_value_pct``,
``low_252d_spread_value_pct``, ``mid_252d_spread_value_pct``,
``percentile_252d``) embed the number; the compute path raises
``NotImplementedError`` if this is set to anything else.

Deterministic anchoring (PR8 + PR16)
------------------------------------
- ``None`` ⇒ anchor at the universe's last observed ``trade_date``
  where BOTH legs have a value after intersection.
- explicit date BEYOND any leg's last ``trade_date`` ⇒ controlled-
  error envelope (``no scoreable strip: ...``). Both legs are
  probed independently via ``fetch_strip_position_max_date`` so
  each leg's actual data range gates the guard (per-leg, not just
  first-failing).
- explicit date WITHIN the universe range ⇒ post-fetch filtered to
  ``trade_date <= as_of_date`` for determinism.

DB access
---------
Reaches the DB through the shared ``fetch_cross_market_strip``
(both legs in one call), ``fetch_strip_position_reference`` (per-
leg metadata bounded by ``as_of_date``), and
``fetch_strip_position_max_date`` (per-leg future-anchor probe)
helpers. NO raw SQL in this file.

Test seam
---------
``fetch_cross_market_strip``, ``fetch_strip_position_reference``,
``fetch_strip_position_max_date``, and ``date`` are imported here at
module level; tests patch them via
``patch("rates_agent.policy_futures.tools.futures_cross_market_spread"
".compute.X")``.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

import pandas as pd
from sqlalchemy.engine import Engine

from rates_agent.policy_futures.tools.futures_cross_market_spread.schemas import (
    FuturesCrossMarketSpreadCurrentMetrics,
    FuturesCrossMarketSpreadInput,
    FuturesCrossMarketSpreadOutput,
    FuturesCrossMarketSpreadTimeSeriesRow,
)
from shared.analytics.levels import trailing_high_low_percentile
from shared.analytics.rates_fetch import (
    fetch_cross_market_strip,
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


# Trailing-range window is wire-frozen at 252 in V1; see schemas.py
# and config.yaml's ``planned_extensions``. This guard fires at the
# convention layer so an editor of config.yaml gets a clear error
# rather than producing percentiles labelled "252d" against a
# different window. Mirrors the same guard in
# futures_price_level / futures_calendar_spread /
# futures_butterfly_simple / volume_open_interest_snapshot.
_FROZEN_TRAILING_WINDOW: int = 252


def _build_methodology_disclosure(
    *,
    curve_family_a: str,
    curve_family_b: str,
    strip_position: int,
    contract_code_a: str,
    contract_code_b: str,
    inverse_priced_a: bool,
    inverse_priced_b: bool,
    short_rate_regime_a: str,
    short_rate_regime_b: str,
    z_window_days: int,
    trailing_window_days: int,
) -> str:
    """Compose the P5 / ADR 0013 disclosure string with runtime
    context so consumers see the exact regime + conversion rule +
    sign convention + RAW-differential label that produced the
    snapshot."""
    # Per-leg inverse-pricing rule disclosures.
    def _rule(label: str, inverse: bool) -> str:
        if inverse:
            return (
                f"{label}: inverse-priced — implied_rate_pct = "
                "100 - raw_price"
            )
        return (
            f"{label}: direct-priced — implied_rate_pct = raw_price"
        )

    rule_a = _rule(f"Leg A ({curve_family_a})", inverse_priced_a)
    rule_b = _rule(f"Leg B ({curve_family_b})", inverse_priced_b)

    mixed_regime = short_rate_regime_a != short_rate_regime_b
    if mixed_regime:
        regime_block = (
            f"Underlying short-rate regimes: leg A ({curve_family_a}) "
            f"= {short_rate_regime_a}; leg B ({curve_family_b}) = "
            f"{short_rate_regime_b}. MIXED-REGIME PAIR (RFR vs IBOR) "
            f"— surfaced explicitly per the catalog guardrail (ADR "
            f"0011); this primitive does NOT collapse mixed-regime "
            f"pairs into a pack-average. The implied rate on the RFR "
            f"leg references a compounded daily risk-free rate "
            f"(SOFR / SONIA); the implied rate on the IBOR leg "
            f"references an unsecured 3M term IBOR (Euribor). The "
            f"differential is honest as a measure of cross-CB "
            f"divergence in futures pricing but is NOT a like-for-"
            f"like funding-adjusted spread (the basis-adjusted "
            f"variant is planned_extension territory per PR11)."
        )
    else:
        regime_block = (
            f"Underlying short-rate regimes: both legs = "
            f"{short_rate_regime_a} ({curve_family_a} and "
            f"{curve_family_b} share the same regime). RFR = "
            f"compounded daily risk-free rate; IBOR = unsecured 3M "
            f"term IBOR. Per-leg labels are surfaced on the snapshot "
            f"even when they agree (the catalog guardrail requires "
            f"the disclosure regardless of regime match)."
        )

    return (
        f"Rolling-generic matched-strip cross-market implied-rate "
        f"differential at strip_position={strip_position}: leg A = "
        f"{curve_family_a} (master stem={contract_code_a}); leg B = "
        f"{curve_family_b} (master stem={contract_code_b}). "
        f"Sign convention (WIRE-FROZEN): spread_value_pct = "
        f"implied_rate_pct({curve_family_a}) - "
        f"implied_rate_pct({curve_family_b}) — A minus B; swapping "
        f"the inputs flips the sign by construction. The desk-"
        f"recognised quantity is the cross-market spread in PERCENT "
        f"POINTS. {regime_block} {rule_a}; {rule_b}. Z-score "
        f"lookback = {z_window_days} trading days on the SPREAD "
        f"series; trailing range window = {trailing_window_days} "
        f"trading days. Output is a RAW cross-market implied-rate "
        f"differential — NOT a basis-adjusted spread (cross-currency "
        f"basis NOT netted) and NOT a beta-adjusted spread "
        f"(regression residual NOT computed); basis-adjusted and "
        f"beta-adjusted variants are planned_extension territory per "
        f"PR11 and ship as separate primitives in a future build. "
        f"This primitive does NOT collapse mixed RFR/IBOR pairs into "
        f"a pack-average — both per-leg regime labels are preserved "
        f"on the snapshot (catalog guardrail). This is NOT a CTD-of-"
        f"futures-of-OIS cross-market spread; the CTD-implied-OIS "
        f"curve is not yet a primitive in this build. This is also "
        f"NOT a meeting-by-meeting policy-path cross-CB "
        f"decomposition (ADR 0013 V1 scope — policy_futures ships "
        f"strip-position-keyed monitors only)."
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
            f"(high_252d_spread_value_pct, "
            f"low_252d_spread_value_pct, "
            f"mid_252d_spread_value_pct, percentile_252d) embed that "
            f"number on the wire. Either restore the value to "
            f"{_FROZEN_TRAILING_WINDOW} or implement the schema "
            f"rename + frontend update documented in "
            f"planned_extensions."
        )
    return trailing


def _format_expiry(value: Any) -> Optional[str]:
    """Render the per-leg ``expiry_date`` value as a YYYY-MM-DD
    string if present; None pass-through."""
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

def calculate_futures_cross_market_spread(
    engine: Engine,
    params: FuturesCrossMarketSpreadInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    """Return current matched-strip cross-market implied-rate
    differential on the policy-futures strip plus 1-day delta, 252-day
    z-score on the spread series, trailing 252-day high/low/mid/
    percentile on the spread axis, observation_count, per-leg current
    implied rates, the per-leg as_of-bounded SCD2 disclosure block
    (including BOTH per-leg short_rate_regime labels), the canonical
    TimeSeries exports, and the P5 methodology disclosure.

    Parameters
    ----------
    engine : Engine
        Live SQLAlchemy engine.
    params : FuturesCrossMarketSpreadInput
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
        Serialised ``FuturesCrossMarketSpreadOutput``, or
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
    spread_round_decimals = config.convention_value(
        "spread_value_round_decimals"
    )
    implied_rate_round_decimals = config.convention_value(
        "implied_rate_round_decimals"
    )
    z_score_round_decimals = config.convention_value("z_score_round_decimals")
    spread_high_low_round_decimals = config.convention_value(
        "spread_value_high_low_round_decimals"
    )
    percentile_round_decimals = config.convention_value(
        "percentile_round_decimals"
    )
    regime_map = _parse_regime_map(
        config.convention_value("short_rate_regime_map")
    )

    short_rate_regime_a = regime_map.get(params.curve_family_a)
    if short_rate_regime_a is None:
        return {
            "error": (
                f"Policy-futures curve_family_a={params.curve_family_a!r} "
                f"has no regime label in the YAML's "
                "``short_rate_regime_map`` convention. Add the entry "
                "(e.g. 'NEW_FAMILY=RFR') alongside the universe "
                "expansion in policy_futures.yml so the methodology "
                "disclosure remains honest (P5)."
            )
        }
    short_rate_regime_b = regime_map.get(params.curve_family_b)
    if short_rate_regime_b is None:
        return {
            "error": (
                f"Policy-futures curve_family_b={params.curve_family_b!r} "
                f"has no regime label in the YAML's "
                "``short_rate_regime_map`` convention. Add the entry "
                "(e.g. 'NEW_FAMILY=RFR') alongside the universe "
                "expansion in policy_futures.yml so the methodology "
                "disclosure remains honest (P5)."
            )
        }

    # Resolve field_name: caller's explicit value wins; None falls
    # through to the YAML default. Mirrors yield_levels /
    # futures_price_level / futures_calendar_spread.
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
        for leg_label, leg_curve_family in (
            ("a", params.curve_family_a),
            ("b", params.curve_family_b),
        ):
            leg_max = fetch_strip_position_max_date(
                engine=engine,
                curve_family=leg_curve_family,
                strip_position=params.strip_position,
                field_name=field_name_resolved,
            )
            if leg_max is not None and requested_as_of > leg_max:
                return {
                    "error": (
                        f"no scoreable strip: as_of_date="
                        f"{requested_as_of.isoformat()} is beyond the "
                        f"policy-futures universe's last observed "
                        f"trade_date={leg_max.isoformat()} for "
                        f"{leg_curve_family} (leg {leg_label.upper()}) "
                        f"strip_position={params.strip_position}. The "
                        "snapshot refuses to silently re-label an "
                        "unbounded read as a future-anchored read."
                    )
                }

    # ------------------------------------------------------------------
    # 2. Date window. Same buffer-from-z-window calendar-day math the
    #    sibling tools use.
    # ------------------------------------------------------------------
    buffer_calendar_days = int(z_window * buffer_mult)
    fetch_anchor: Optional[date] = requested_as_of
    if fetch_anchor is None:
        fetch_anchor = date.today()
    start_date = fetch_anchor - timedelta(
        days=params.lookback_days + buffer_calendar_days
    )

    # ------------------------------------------------------------------
    # 3. Per-leg reference metadata (SCD2 as-of lookup). Read both
    #    legs INDEPENDENTLY (the per-leg `inverse_pricing` flag is
    #    metadata-driven and may differ across markets in a future
    #    universe expansion; in V1 all three families happen to be
    #    inverse-priced but the conversion remains metadata-driven).
    # ------------------------------------------------------------------
    reference_a = fetch_strip_position_reference(
        engine=engine,
        curve_family=params.curve_family_a,
        strip_position=params.strip_position,
        as_of_date=fetch_anchor,
    )
    if reference_a is None:
        return {
            "error": (
                f"No policy-futures strip slot found for "
                f"curve_family_a='{params.curve_family_a}', "
                f"strip_position={params.strip_position}. Verify the "
                "(curve_family, strip_position) pair exists on "
                "instrument_master as a strip-position-keyed rolling "
                "contract (policy_futures.yml universe)."
            )
        }
    reference_b = fetch_strip_position_reference(
        engine=engine,
        curve_family=params.curve_family_b,
        strip_position=params.strip_position,
        as_of_date=fetch_anchor,
    )
    if reference_b is None:
        return {
            "error": (
                f"No policy-futures strip slot found for "
                f"curve_family_b='{params.curve_family_b}', "
                f"strip_position={params.strip_position}. Verify the "
                "(curve_family, strip_position) pair exists on "
                "instrument_master as a strip-position-keyed rolling "
                "contract (policy_futures.yml universe)."
            )
        }

    inverse_a_raw = reference_a.get("inverse_pricing")
    inverse_b_raw = reference_b.get("inverse_pricing")
    if inverse_a_raw is None:
        return {
            "error": (
                f"Policy-futures strip {params.curve_family_a} "
                f"strip_position={params.strip_position} (leg A) is "
                "missing the 'inverse_pricing' flag on "
                "instrument_master.attributes. The implied-rate "
                "conversion rule is metadata-driven (PR8 / P6 — no "
                "hidden methodology in code); a strip without this "
                "flag cannot be priced honestly. Surface this as a "
                "metadata gap to the playbook owner."
            )
        }
    if inverse_b_raw is None:
        return {
            "error": (
                f"Policy-futures strip {params.curve_family_b} "
                f"strip_position={params.strip_position} (leg B) is "
                "missing the 'inverse_pricing' flag on "
                "instrument_master.attributes. The implied-rate "
                "conversion rule is metadata-driven (PR8 / P6 — no "
                "hidden methodology in code); a strip without this "
                "flag cannot be priced honestly. Surface this as a "
                "metadata gap to the playbook owner."
            )
        }
    inverse_priced_a: bool = bool(inverse_a_raw)
    inverse_priced_b: bool = bool(inverse_b_raw)

    # ------------------------------------------------------------------
    # 4. Fetch the two legs in one DB round-trip via the cross-market-
    #    strip fetcher. The fetcher does not currently expose an
    #    end_date filter; we post-filter the resulting frame so the
    #    as_of_date anchor remains deterministic. See module
    #    docstring for the rationale.
    # ------------------------------------------------------------------
    raw_df = fetch_cross_market_strip(
        engine=engine,
        curve_family_1=params.curve_family_a,
        curve_family_2=params.curve_family_b,
        strip_position=params.strip_position,
        field_name=field_name_resolved,
        start_date=start_date,
    )
    if raw_df.empty:
        return {
            "error": (
                f"No policy-futures price data found for "
                f"curve_family_a='{params.curve_family_a}', "
                f"curve_family_b='{params.curve_family_b}', "
                f"strip_position={params.strip_position}, "
                f"field='{field_name_resolved}' since "
                f"{start_date.isoformat()}. Please verify both strip "
                "slots have ingested observations for the field."
            )
        }

    # Clip rows past the explicit as_of_date — the cross-market-strip
    # fetcher does not yet carry end_date.
    if requested_as_of is not None:
        raw_df = raw_df[
            pd.to_datetime(raw_df["trade_date"])
            <= pd.Timestamp(requested_as_of)
        ]
        if raw_df.empty:
            return {
                "error": (
                    f"No policy-futures price data found for "
                    f"curve_family_a='{params.curve_family_a}', "
                    f"curve_family_b='{params.curve_family_b}', "
                    f"strip_position={params.strip_position}, "
                    f"field='{field_name_resolved}' on or before "
                    f"as_of_date={requested_as_of.isoformat()}."
                )
            }

    # ------------------------------------------------------------------
    # 5. Verify both legs are present in the fetched window.
    # ------------------------------------------------------------------
    available_curves = set(raw_df["curve_family"].unique())
    required_curves = {params.curve_family_a, params.curve_family_b}
    missing_curves = required_curves - available_curves
    if missing_curves:
        return {
            "error": (
                f"Missing curve_family data for {sorted(missing_curves)} "
                f"at strip_position={params.strip_position}. Available "
                f"curve_families in the query window: "
                f"{sorted(available_curves)}."
            )
        }

    # ------------------------------------------------------------------
    # 6. Pivot → wide format (date × curve_family) and ffill across
    #    holiday gaps per leg. Then DROP rows missing either leg
    #    (i.e. take the intersection of trading days). Mirrors the
    #    sovereign cross_market_spread tool's pivot idiom.
    # ------------------------------------------------------------------
    wide = pivot_and_align_tenors(
        raw_df,
        required_tenors=(params.curve_family_a, params.curve_family_b),
        key_col="curve_family",
        ffill_limit=ffill_limit,
    )
    if wide.empty:
        return {
            "error": (
                f"After aligning dates for "
                f"'{params.curve_family_a}' and "
                f"'{params.curve_family_b}' at "
                f"strip_position={params.strip_position}, no overlapping "
                "observations remain."
            )
        }

    # ------------------------------------------------------------------
    # 7. Build per-leg implied-rate series from the per-leg raw-price
    #    series. The conversion rule is the SAME one the siblings
    #    use; each leg's flag is read INDEPENDENTLY.
    # ------------------------------------------------------------------
    raw_a = wide[params.curve_family_a]
    raw_b = wide[params.curve_family_b]
    if inverse_priced_a:
        implied_a = 100.0 - raw_a
    else:
        implied_a = raw_a.copy()
    if inverse_priced_b:
        implied_b = 100.0 - raw_b
    else:
        implied_b = raw_b.copy()

    # ------------------------------------------------------------------
    # 8. Compute the spread series. Wire-frozen orientation: A − B.
    # ------------------------------------------------------------------
    spread_series = implied_a - implied_b

    # ------------------------------------------------------------------
    # 9. Z-score on the SPREAD series.
    # ------------------------------------------------------------------
    z_series = rolling_zscore(
        spread_series,
        window=z_window,
        min_periods=z_min_periods,
        ddof=z_ddof,
        round_decimals=z_score_round_decimals,
    )
    z_score = safe_float(z_series.iloc[-1], decimals=z_score_round_decimals)

    # ------------------------------------------------------------------
    # 10. 1-day raw subtraction on the spread axis.
    # ------------------------------------------------------------------
    daily_change_spread_value_pct: Optional[float]
    if len(spread_series) >= daily_offset_rows:
        cur_val = spread_series.iloc[-1]
        prev_val = spread_series.iloc[-daily_offset_rows]
        if pd.isna(cur_val) or pd.isna(prev_val):
            daily_change_spread_value_pct = None
        else:
            daily_change_spread_value_pct = round(
                float(cur_val) - float(prev_val),
                spread_round_decimals,
            )
    else:
        daily_change_spread_value_pct = None

    # ------------------------------------------------------------------
    # 11. Trailing high/low/mid + percentile on the spread axis.
    # ------------------------------------------------------------------
    spread_high, spread_low, percentile = trailing_high_low_percentile(
        spread_series,
        window=trailing_window,
        decimals=spread_high_low_round_decimals,
    )
    percentile_rounded: Optional[float]
    if percentile is None:
        percentile_rounded = None
    else:
        percentile_rounded = round(
            float(percentile), percentile_round_decimals
        )
    spread_mid: Optional[float]
    if spread_high is None or spread_low is None:
        spread_mid = None
    else:
        spread_mid = round(
            (float(spread_high) + float(spread_low)) / 2.0,
            spread_high_low_round_decimals,
        )

    # ------------------------------------------------------------------
    # 12. Current values + observation_count.
    # ------------------------------------------------------------------
    current_spread = safe_float(
        spread_series.iloc[-1],
        decimals=spread_round_decimals,
    )
    current_implied_a = safe_float(
        implied_a.iloc[-1],
        decimals=implied_rate_round_decimals,
    )
    current_implied_b = safe_float(
        implied_b.iloc[-1],
        decimals=implied_rate_round_decimals,
    )

    as_of_date_resolved = spread_series.index[-1].date()
    cutoff = pd.Timestamp(
        as_of_date_resolved - timedelta(days=params.lookback_days)
    )
    display_spread = spread_series.loc[spread_series.index >= cutoff]
    display_zscore = z_series.loc[z_series.index >= cutoff]
    obs_count = len(display_spread.dropna())
    if obs_count == 0:
        return {
            "error": (
                f"No observations within the last {params.lookback_days} "
                f"days for {params.curve_family_a} vs "
                f"{params.curve_family_b} at "
                f"strip_position={params.strip_position}."
            )
        }

    # ------------------------------------------------------------------
    # 13. Build the snapshot.
    # ------------------------------------------------------------------
    contract_code_a = reference_a["contract_code"]
    contract_code_b = reference_b["contract_code"]
    spread_label = f"{contract_code_a}-{contract_code_b}"

    methodology_disclosure = _build_methodology_disclosure(
        curve_family_a=params.curve_family_a,
        curve_family_b=params.curve_family_b,
        strip_position=params.strip_position,
        contract_code_a=contract_code_a,
        contract_code_b=contract_code_b,
        inverse_priced_a=inverse_priced_a,
        inverse_priced_b=inverse_priced_b,
        short_rate_regime_a=short_rate_regime_a,
        short_rate_regime_b=short_rate_regime_b,
        z_window_days=z_window,
        trailing_window_days=trailing_window,
    )

    metrics = FuturesCrossMarketSpreadCurrentMetrics(
        as_of_date=as_of_date_resolved.strftime("%Y-%m-%d"),
        curve_family_a=params.curve_family_a,
        curve_family_b=params.curve_family_b,
        strip_position=params.strip_position,
        spread_label=spread_label,
        contract_code_a=contract_code_a,
        contract_code_b=contract_code_b,
        underlying_contract_code_a=reference_a.get("underlying_contract_code"),
        underlying_contract_code_b=reference_b.get("underlying_contract_code"),
        security_name_a=reference_a.get("security_name"),
        security_name_b=reference_b.get("security_name"),
        expiry_date_a=_format_expiry(reference_a.get("expiry_date")),
        expiry_date_b=_format_expiry(reference_b.get("expiry_date")),
        inverse_priced_a=inverse_priced_a,
        inverse_priced_b=inverse_priced_b,
        short_rate_regime_a=short_rate_regime_a,
        short_rate_regime_b=short_rate_regime_b,
        implied_rate_pct_a=current_implied_a,
        implied_rate_pct_b=current_implied_b,
        spread_value_pct=current_spread,
        daily_change_spread_value_pct=daily_change_spread_value_pct,
        z_score_spread=z_score,
        high_252d_spread_value_pct=spread_high,
        low_252d_spread_value_pct=spread_low,
        mid_252d_spread_value_pct=spread_mid,
        percentile_252d=percentile_rounded,
        rolling_window_days=z_window,
        observation_count=obs_count,
    )

    # ------------------------------------------------------------------
    # 14. Bespoke + canonical TimeSeries — built from the SAME
    #     display slices so they cannot drift.
    # ------------------------------------------------------------------
    bespoke_rows = _build_bespoke_time_series(
        display_spread=display_spread,
        display_zscore=display_zscore,
        spread_round_decimals=spread_round_decimals,
        z_score_round_decimals=z_score_round_decimals,
    )
    canonical_spread = _build_canonical_spread(
        display_spread=display_spread,
        curve_family_a=params.curve_family_a,
        curve_family_b=params.curve_family_b,
        strip_position=params.strip_position,
        spread_round_decimals=spread_round_decimals,
    )
    canonical_zscore = _build_canonical_zscore(
        display_zscore=display_zscore,
        curve_family_a=params.curve_family_a,
        curve_family_b=params.curve_family_b,
        strip_position=params.strip_position,
        z_score_round_decimals=z_score_round_decimals,
    )

    output = FuturesCrossMarketSpreadOutput(
        current_metrics=metrics,
        time_series=bespoke_rows,
        time_series_spread=canonical_spread,
        time_series_zscore=canonical_zscore,
        methodology_disclosure=methodology_disclosure,
    )
    return output.model_dump()


# ============================================================================
# TIME-SERIES BUILDERS
# ============================================================================

def _build_bespoke_time_series(
    *,
    display_spread: pd.Series,
    display_zscore: pd.Series,
    spread_round_decimals: int,
    z_score_round_decimals: int,
) -> list[FuturesCrossMarketSpreadTimeSeriesRow]:
    """Build the bespoke ``{date, spread_value_pct, z_score}`` row
    list. Skips rows where the spread value is NaN; z_score can
    legitimately be None during the rolling-window warmup."""
    rows: list[FuturesCrossMarketSpreadTimeSeriesRow] = []
    for ts, val in display_spread.items():
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
            FuturesCrossMarketSpreadTimeSeriesRow(
                date=ts.strftime("%Y-%m-%d"),
                spread_value_pct=round(
                    float(val), spread_round_decimals,
                ),
                z_score=z_rounded,
            )
        )
    return rows


def _build_canonical_spread(
    *,
    display_spread: pd.Series,
    curve_family_a: str,
    curve_family_b: str,
    strip_position: int,
    spread_round_decimals: int,
) -> TimeSeries:
    """Build the canonical ``TimeSeries`` for the spread series
    (closed-enum ``TimeSeriesUnits.PERCENT``)."""
    series_name = (
        f"{curve_family_a.lower()}_"
        f"{curve_family_b.lower()}_"
        f"{strip_position}_spread"
    )
    rows = []
    for ts, val in display_spread.items():
        if pd.isna(val):
            continue
        rows.append(
            TimeSeriesRow(
                date=ts.strftime("%Y-%m-%d"),
                value=round(float(val), spread_round_decimals),
            )
        )
    return TimeSeries(
        series_name=series_name,
        units=TimeSeriesUnits.PERCENT,
        description=(
            f"Cross-market implied-rate differential "
            f"({curve_family_a} − {curve_family_b}) at strip "
            f"position {strip_position} over the displayed window, in "
            f"PERCENT POINTS. RAW differential — not basis-adjusted, "
            f"not beta-adjusted."
        ),
        rows=rows,
    )


def _build_canonical_zscore(
    *,
    display_zscore: pd.Series,
    curve_family_a: str,
    curve_family_b: str,
    strip_position: int,
    z_score_round_decimals: int,
) -> TimeSeries:
    """Build the canonical ``TimeSeries`` for the rolling z-score
    series (closed-enum ``TimeSeriesUnits.Z_SCORE``). None values
    during the warmup window are preserved."""
    series_name = (
        f"{curve_family_a.lower()}_"
        f"{curve_family_b.lower()}_"
        f"{strip_position}_zscore"
    )
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
            f"Rolling z-score of the {curve_family_a}−"
            f"{curve_family_b} cross-market spread at strip "
            f"position {strip_position} vs its own trailing window."
        ),
        rows=rows,
    )
