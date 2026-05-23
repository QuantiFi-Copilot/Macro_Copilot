"""Pydantic schemas for the policy_futures futures_pack_average_simple tool.

V1 pack-average primitive for the ``policy_futures`` domain (ADR
0011) — four same-curve strip slots (whites = strip positions 1-4,
reds = strip positions 5-8) combined into an implied-rate pack
average using the canonical SIMPLE ARITHMETIC MEAN weighting:

    pack_average_implied_rate_pct = (
        implied_rate_pct_position_1 + implied_rate_pct_position_2
        + implied_rate_pct_position_3 + implied_rate_pct_position_4
    ) / 4

(for whites; symmetric for reds on positions 5-8).

Why a new primitive rather than four ``futures_price_level`` calls
-----------------------------------------------------------------
A pack average is the canonical STIR-desk shorthand — "the SOFR
whites print", "where are the SONIA reds?" — and the desk-recognised
quantity is the PACK AVERAGE SERIES on the implied-rate axis (its
current value, its 1-day change, its rolling z-score, its trailing
range / percentile). None of those derivatives are available by
post-processing four outright reads: each ``futures_price_level``
call anchors at its own per-leg data-max ``trade_date``, while the
pack average is honest only on the intersection of ALL FOUR legs'
trading days. The catalog's standardness disclosure makes this
explicit; this primitive owns its own four-leg read so the snapshot
is constructed on aligned dates and the display + canonical
TimeSeries are consistent end-to-end.

Closed-family discipline (P8 / PR8)
-----------------------------------
``curve_family`` is a closed ``Literal[...]`` mirroring the V1
``policy_futures.yml`` universe (SOFR_FUT, EUR_SHORT_RATE_FUT,
SONIA_FUT). The EUR_SHORT_RATE_FUT branch is REFUSED AT COMPUTE TIME
with a controlled ``NotImplementedError`` (the schema deliberately
admits the value so the LLM can ask for it and receive a clean
error envelope with the unblock reason — see compute.py and the
methodology card). An unknown value raises
``pydantic.ValidationError`` at the schema layer; a future direct-
priced family lands via a new ADR + YAML/playbook entry + a Literal
extension, NEVER as a silent passthrough.

Pack input is a closed enum
---------------------------
``pack`` is ``Literal["whites", "reds"]`` — the central user-facing
choice that defines the tool. The strip-position ranges that each
pack covers (whites = 1-4, reds = 5-8) are canonical STIR-desk
conventions and live in YAML, NOT as user inputs. The LLM cannot
override "what whites mean"; if a desk needs a different pack
composition (6-pack, custom-window), that ships as a separate
primitive in a future build.

Why a bespoke ``time_series`` row shape (plus canonical TimeSeries)
------------------------------------------------------------------
The bespoke ``{date, pack_average_implied_rate_pct, z_score}`` row
shape mirrors the sibling butterfly tool's bespoke ``time_series``
(frontend-friendly per-row pair). The pack-average value itself is
single-unit (PERCENT) so we ALSO emit canonical
``TimeSeriesUnits.PERCENT`` / ``TimeSeriesUnits.Z_SCORE`` series
next to the bespoke list — the closed-enum exemption used by the
calendar_spread sibling (two units per row) does NOT apply here.
``time_series_pack_average`` and ``time_series_zscore`` are the
operator-compatible canonical exports; ``time_series`` is the
wire-frozen bespoke shape PR15 expects. All three share the same
display rows so they cannot drift.

Field-name discipline on the snapshot (PR14 frozen)
---------------------------------------------------
- ``pack_average_implied_rate_pct`` — desk-recognised pack-average
  value in PERCENT (NOT bps — the policy-futures domain stays in
  PERCENT on implied-rate-derived objects, matching the
  ``implied_rate_pct`` siblings; bps-multiplied variants are NOT
  used).
- ``daily_change_pack_average_implied_rate_pct`` — one-trading-day
  raw subtraction in PERCENT POINTS.
- ``z_score_pack_average`` — rolling 252-trading-day z-score of the
  PACK-AVERAGE series.
- ``high_252d_pack_average_implied_rate_pct`` /
  ``low_252d_pack_average_implied_rate_pct`` /
  ``mid_252d_pack_average_implied_rate_pct`` — trailing 252-day
  range on the pack-average series.
- ``percentile_252d`` — percentile rank of the current pack average
  within the trailing 252-day range (0-100).
- ``implied_rate_pct_position_N`` (one per pack member) — per-leg
  current implied rates in PERCENT (PR14-frozen ``implied_rate_pct``
  prefix per the sibling futures_price_level tool).
- ``contract_code_position_N`` (master stems, e.g. ``SFR1``) and
  ``underlying_contract_code_position_N`` (current-front per leg,
  e.g. ``SFRM26``) disclosed side-by-side per leg so the reader
  knows BOTH the strip slot AND the underlying contract each slot
  resolves to today.
- ``inverse_priced`` + ``short_rate_regime`` carry the regime
  disclosure inline so the implied-rate conversion is transparent
  on the wire (P5 — no hidden methodology choice).

Why the input set is minimal (PR8 input-schema overreach guard)
---------------------------------------------------------------
Per the catalog (build_order 28) and the build prompt's
non-negotiables: ONLY four inputs are legitimate per-query knobs:

  - ``curve_family`` — the policy-futures family (closed Literal).
  - ``pack`` — the central user-facing choice (whites or reds).
  - ``lookback_days`` — calendar-day fetch window for
    observation_count.
  - ``as_of_date`` — optional anchor (None = data-max).
  - ``field_name`` — optional Bloomberg field override.

Methodology defaults (z window, pack composition ranges, rounding,
inverse-pricing-by-family, regime labels) live in YAML (PR9 / PR10);
mathematical invariants (the ``implied_rate_pct = 100 - raw_price``
inversion, the intersection-of-trading-days alignment, the simple
arithmetic mean across pack members, the z-score recipe) live in
compute.py. Exposing any of those as inputs would be input-schema
overreach (PR8).

Validation layering
-------------------
- ``as_of_date`` defaults to ``None`` — the sentinel that means
  "anchor at the universe's last observed ``trade_date`` where ALL
  FOUR legs have a value". When supplied AND beyond the data max
  on ANY leg, the compute layer returns the documented controlled-
  error envelope per P6 / PR8 / PR16 honest disclosure (no silent
  re-labelling of an unbounded read as a future-anchored read).

- ``field_name`` defaults to ``None`` — the sentinel that means
  "use the YAML's ``default_price_field`` convention". MCP / HTTP
  wrappers translate their wire-level empty-string sentinel to
  None before constructing this input.

- ``frozen=True`` + ``extra="forbid"`` per typed-boundary
  discipline. None of the four models on this primitive carry
  pandas / numpy payloads, so ``arbitrary_types_allowed`` is
  intentionally omitted.
"""

from __future__ import annotations

from datetime import date
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from shared.schemas import TimeSeries


# Closed-family Literal mirroring rates_agent/playbooks/policy_futures.yml
# (V1 universe). Adding a new curve_family REQUIRES an ADR + playbook
# entry + this Literal extension + a regime-map YAML entry (P8 / PR8).
# Note: EUR_SHORT_RATE_FUT is REFUSED at compute time (per ADR 0011 V1
# scope); the schema admits it so the LLM can ask and receive a clean
# error envelope rather than a Pydantic ValidationError that hides the
# real unblock reason.
PolicyFuturesPackAverageCurveFamily = Literal[
    "SOFR_FUT",
    "EUR_SHORT_RATE_FUT",
    "SONIA_FUT",
]


# Closed-family Literal for the pack choice — this IS the central
# user-facing methodology knob defining the tool. Each pack maps to a
# YAML-locked range of strip positions (whites = 1-4, reds = 5-8).
PolicyFuturesPack = Literal["whites", "reds"]


class FuturesPackAverageSimpleInput(BaseModel):
    """Parameters the LLM extracts to query a single policy-futures
    same-curve pack-average implied rate.

    Keyed by ``(curve_family, pack)`` per ADR 0011 V1 scope. The
    strip-position ranges that each pack covers (whites = 1-4, reds
    = 5-8) are YAML-locked conventions; only ``curve_family`` +
    ``pack`` choose WHICH four legs are averaged.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    curve_family: PolicyFuturesPackAverageCurveFamily = Field(
        ...,
        description=(
            "Policy-futures curve family. Closed Literal mirroring "
            "the V1 playbook universe: 'SOFR_FUT' (US Fed SOFR strip, "
            "RFR regime), 'EUR_SHORT_RATE_FUT' (ECB Euribor strip, "
            "IBOR regime — REFUSED at compute time per ADR 0011 V1 "
            "scope; the schema admits the value so the LLM can ask "
            "and receive a clean error envelope naming the missing "
            "``delivery_month_type`` playbook metadata as the unblock "
            "dependency), 'SONIA_FUT' (BOE SONIA strip, RFR regime). "
            "An unknown value raises a Pydantic ValidationError at "
            "the schema layer — NOT silently passed through to the "
            "compute layer (P8 / PR8 closed-family discipline). Do "
            "NOT pass bond-futures curve families (UST_FUT / DE_FUT "
            "/ UK_FUT / JP_FUT / ...) — those route to the "
            "bond_futures agent."
        ),
    )
    pack: PolicyFuturesPack = Field(
        ...,
        description=(
            "Which pack to average. 'whites' = front year = strip "
            "positions 1-4 (e.g. SFR1..SFR4 for SOFR_FUT); 'reds' = "
            "second year = strip positions 5-8 (e.g. SFR5..SFR8). "
            "These are canonical STIR-desk conventions; the strip-"
            "position ranges live in this tool's config.yaml "
            "(``whites_strip_positions`` / ``reds_strip_positions``) "
            "and are NOT user-overridable. A future desk wanting a "
            "different pack composition (6-pack, bundles) would land "
            "as a separate primitive."
        ),
    )
    lookback_days: int = Field(
        default=365,
        ge=30,
        le=7300,
        description=(
            "Calendar days of history fetched from the DB and used "
            "to scope the observation_count cutoff in the output. "
            "Does NOT control the rolling z-score window (fixed by "
            "config convention z_score_window_days, currently 252) "
            "or the trailing range window (fixed by "
            "trailing_range_window_days, locked at 252 in V1)."
        ),
    )
    as_of_date: Optional[date] = Field(
        default=None,
        description=(
            "As-of date for the snapshot. When omitted (default), "
            "the monitor anchors at the universe's last observed "
            "``trade_date`` for the requested legs (post-fetch "
            "data-max anchor — same pattern as the sibling "
            "futures_butterfly_simple). When supplied AND BEYOND "
            "the universe's last observed ``trade_date`` for ANY "
            "leg, the monitor returns the documented controlled-"
            "error envelope (``{\"error\": \"no scoreable strip: "
            "...\"}``). When supplied AND within the universe "
            "range, the fetch is anchored at this date so the "
            "snapshot is DETERMINISTIC across runs."
        ),
    )
    field_name: Optional[str] = Field(
        default=None,
        description=(
            "Bloomberg observation field mnemonic. When None "
            "(default), the tool falls through to "
            "``default_price_field`` from config.yaml (currently "
            "'PX_LAST'). Pass an explicit field name to override "
            "per query. LLM/HTTP wrappers MUST translate their "
            "wire-level sentinel (empty string for MCP, missing "
            "param for FastAPI) to None before constructing this "
            "input — otherwise the YAML default is silently "
            "shadowed."
        ),
    )


class FuturesPackAverageSimpleTimeSeriesRow(BaseModel):
    """One observation in the bespoke pack-average history.

    Mirrors the sibling butterfly tool's per-row ``{date,
    <value>, z_score}`` shape; carries the pack-average value plus
    its rolling z-score side-by-side. Both quantities are rounded
    with the same conventions the snapshot uses so the snapshot
    equals ``time_series[-1]`` STRICTLY at the latest row.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    date: str = Field(..., description="Trade date in YYYY-MM-DD form.")
    pack_average_implied_rate_pct: float = Field(
        ...,
        description=(
            "Pack-average implied rate in PERCENT at the trade "
            "date: arithmetic mean of the four per-leg "
            "``implied_rate_pct`` values. Rounded with "
            "``pack_average_round_decimals`` from config.yaml."
        ),
    )
    z_score: Optional[float] = Field(
        None,
        description=(
            "Rolling z-score of the pack average at this trade "
            "date (``None`` during the warmup window before "
            "``z_score_min_periods`` observations accumulate). "
            "Rounded with ``z_score_round_decimals`` from "
            "config.yaml."
        ),
    )


class FuturesPackAverageSimpleCurrentMetrics(BaseModel):
    """Snapshot of the latest aligned pack-average bar.

    Field naming is unit-honest and regime-aware:

    - ``pack_average_implied_rate_pct`` carries the desk-recognised
      pack-average value in PERCENT.
    - ``daily_change_pack_average_implied_rate_pct`` is a raw
      subtraction in PERCENT POINTS (NOT *100 to bps).
    - ``high_252d_pack_average_implied_rate_pct`` /
      ``low_252d_pack_average_implied_rate_pct`` /
      ``mid_252d_pack_average_implied_rate_pct`` embed the
      trailing-range window length (wire-frozen at 252 in V1).
    - ``inverse_priced`` / ``short_rate_regime`` carry the
      methodology disclosure inline so the implied-rate conversion
      is transparent on the wire.
    - Per-leg disclosure block carries both the strip-slot master
      stem (stable across rolls) and the current-front underlying
      contract per leg (rotates at roll), plus the per-leg expiry
      + security name from the SCD2 ``instrument_metadata_history``
      row whose effective window contains ``as_of_date``. The list
      ordering mirrors the YAML's ``whites_strip_positions`` /
      ``reds_strip_positions`` (positions ascending).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    as_of_date: str = Field(
        ...,
        description=(
            "Most recent trade date on which ALL FOUR legs of the "
            "requested pack have a value (YYYY-MM-DD). The "
            "snapshot is anchored to the intersection of the four "
            "legs' trading days so the row cannot pair fresh "
            "quotes on three legs with a stale quote on the "
            "fourth."
        ),
    )
    curve_family: str = Field(..., description="Policy-futures curve family.")
    pack: str = Field(
        ...,
        description=(
            "Pack identifier: 'whites' (positions 1-4) or 'reds' "
            "(positions 5-8). Echoed for disclosure."
        ),
    )
    strip_positions: List[int] = Field(
        ...,
        description=(
            "Ordered list of strip positions covered by the "
            "requested pack (e.g. [1,2,3,4] for whites). Echoes "
            "the YAML's pack convention so the consumer can audit "
            "exactly which slots were averaged."
        ),
    )
    pack_label: str = Field(
        ...,
        description=(
            "Human-readable label for the pack, e.g. "
            "``'SOFR_FUT whites (SFR1..SFR4)'`` or ``'SONIA_FUT "
            "reds (SFI5..SFI8)'``. Derived from the four legs' "
            "resolved ``contract_code`` stems."
        ),
    )
    contract_codes: List[str] = Field(
        ...,
        description=(
            "Strip-slot master stems for the four pack members, "
            "in strip-position order (e.g. ['SFR1','SFR2','SFR3',"
            "'SFR4'] for SOFR_FUT whites). Stable across rolls — "
            "see ``underlying_contract_codes`` for the current "
            "front per leg."
        ),
    )
    underlying_contract_codes: List[Optional[str]] = Field(
        ...,
        description=(
            "Current-front underlying contract per pack member AS "
            "OF ``as_of_date`` (e.g. ['SFRM26','SFRU26','SFRZ26',"
            "'SFRH27'] for SOFR_FUT whites). Same length and "
            "order as ``contract_codes`` so consumers can zip the "
            "two."
        ),
    )
    security_names: List[Optional[str]] = Field(
        ...,
        description=(
            "Latest-effective ``SECURITY_DES`` per pack member. "
            "Same length and order as ``contract_codes``."
        ),
    )
    expiry_dates: List[Optional[str]] = Field(
        ...,
        description=(
            "``LAST_TRADEABLE_DT`` for each pack member's current-"
            "front contract (YYYY-MM-DD). Same length and order "
            "as ``contract_codes``."
        ),
    )
    inverse_priced: bool = Field(
        ...,
        description=(
            "Inverse-pricing flag (read from "
            "``instrument_master.attributes->>'inverse_pricing'`` "
            "on all four legs; same-curve packs share the flag "
            "in V1). When ``True`` (SFR / ER / SFI), "
            "``implied_rate_pct = 100 - raw_price`` per leg; "
            "drives the conversion off METADATA, not a hardcoded "
            "list in compute.py (PR8 / P6)."
        ),
    )
    short_rate_regime: str = Field(
        ...,
        description=(
            "Methodology disclosure label for the underlying "
            "short-rate regime: ``'RFR'`` or ``'IBOR'``. Resolved "
            "from the YAML's ``short_rate_regime_map`` convention. "
            "Carried verbatim on the methodology disclosure "
            "string per P5 + ADR 0011."
        ),
    )
    implied_rates_pct: List[float] = Field(
        ...,
        description=(
            "Latest implied rate on each pack member in PERCENT, "
            "in strip-position order. Same length and order as "
            "``contract_codes``. PR14-frozen unit suffix; do NOT "
            "rename to ``_bps`` or ``_rate``."
        ),
    )
    pack_average_implied_rate_pct: float = Field(
        ...,
        description=(
            "Latest aligned pack average in PERCENT: arithmetic "
            "mean of the four per-leg implied rates. The desk-"
            "recognised pack-average quantity."
        ),
    )
    daily_change_pack_average_implied_rate_pct: Optional[float] = Field(
        None,
        description=(
            "1-trading-day change in "
            "``pack_average_implied_rate_pct`` (raw subtraction "
            "in PERCENT POINTS; NOT multiplied by 100 to bps)."
        ),
    )
    z_score_pack_average: Optional[float] = Field(
        None,
        description=(
            "Rolling 252-trading-day z-score of the pack-average "
            "series."
        ),
    )
    high_252d_pack_average_implied_rate_pct: Optional[float] = Field(
        None,
        description=(
            "Highest pack-average value over the trailing 252 "
            "trading days, in PERCENT."
        ),
    )
    low_252d_pack_average_implied_rate_pct: Optional[float] = Field(
        None,
        description=(
            "Lowest pack-average value over the trailing 252 "
            "trading days, in PERCENT."
        ),
    )
    mid_252d_pack_average_implied_rate_pct: Optional[float] = Field(
        None,
        description=(
            "Midpoint of the trailing 252-day pack-average range "
            "(``(high + low) / 2``), in PERCENT."
        ),
    )
    percentile_252d: Optional[float] = Field(
        None,
        description=(
            "Percentile rank of "
            "``pack_average_implied_rate_pct`` within the "
            "trailing 252-day range (0-100)."
        ),
    )
    rolling_window_days: int = Field(
        ...,
        description=(
            "The trading-day window used for the z-score "
            "(mirrors the field name the sibling tools use for "
            "the same disclosure)."
        ),
    )
    observation_count: int = Field(
        ...,
        description=(
            "Number of aligned trading days within the "
            "``lookback_days`` window. Counts dates where ALL "
            "FOUR pack legs have a value after cleaning + "
            "intersection."
        ),
    )


class FuturesPackAverageSimpleOutput(BaseModel):
    """Top-level response for the policy_futures
    futures_pack_average_simple tool.

    Wire shape:
      - ``current_metrics``: snapshot of the latest aligned pack-
        average bar + per-leg disclosure block + the four per-leg
        implied rates.
      - ``time_series``: bespoke per-row ``{date,
        pack_average_implied_rate_pct, z_score}`` list (mirrors the
        sibling butterfly tool's frontend-friendly shape).
      - ``time_series_pack_average``: canonical TimeSeries of the
        pack-average series in PERCENT (closed-enum
        ``TimeSeriesUnits.PERCENT``). Values match
        ``time_series[i].pack_average_implied_rate_pct`` 1-to-1 by
        construction.
      - ``time_series_zscore``: canonical TimeSeries of the rolling
        z-score (closed-enum ``TimeSeriesUnits.Z_SCORE``).
      - ``methodology_disclosure``: the P5 / ADR 0011 caveat carried
        on every response. Includes the arithmetic-mean weighting,
        the inverse-pricing rule, the regime label, the z-score
        lookback window, the strip-position keying, AND the
        explicit refusal of duration-weighted / meeting-by-meeting
        / CTD-of-OIS variants (PR11 planned-extension territory).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    current_metrics: FuturesPackAverageSimpleCurrentMetrics
    time_series: List[FuturesPackAverageSimpleTimeSeriesRow] = Field(
        default_factory=list,
        description=(
            "Historical pack-average + z-score observations over "
            "the display window (cleaned, intersected, rounded). "
            "Bespoke row shape so the frontend chart pulls both "
            "the value and the z-score from one row; the "
            "canonical TimeSeries fields below carry the same "
            "series for operator-layer consumption."
        ),
    )
    time_series_pack_average: TimeSeries = Field(
        ...,
        description=(
            "Historical pack-average series in PERCENT. Closed-"
            "enum ``TimeSeriesUnits.PERCENT``; series_name = "
            "``'<curve_family_lower>_<pack>_pack_average'``. "
            "Values match "
            "``time_series[i].pack_average_implied_rate_pct`` "
            "1-to-1 by construction."
        ),
    )
    time_series_zscore: TimeSeries = Field(
        ...,
        description=(
            "Historical rolling z-score of the pack-average value "
            "vs its own trailing window. Closed-enum "
            "``TimeSeriesUnits.Z_SCORE``; series_name = "
            "``'<curve_family_lower>_<pack>_zscore'``. Values "
            "match ``time_series[i].z_score`` 1-to-1 (``None`` "
            "for rows in the rolling-window warmup)."
        ),
    )
    methodology_disclosure: str = Field(
        ...,
        min_length=1,
        description=(
            "P5 / ADR 0011 caveat carried on every response. "
            "Includes the arithmetic-mean weighting, the inverse-"
            "pricing rule, the per-curve_family regime label (RFR "
            "vs IBOR), the z-score lookback window, the trailing-"
            "range window, the strip-position keying, and the "
            "explicit refusal of duration-weighted / meeting-by-"
            "meeting / CTD-of-OIS pack variants. Required so "
            "consumers cannot drop the disclosure when relaying "
            "the snapshot."
        ),
    )


__all__ = [
    "PolicyFuturesPackAverageCurveFamily",
    "PolicyFuturesPack",
    "FuturesPackAverageSimpleInput",
    "FuturesPackAverageSimpleTimeSeriesRow",
    "FuturesPackAverageSimpleCurrentMetrics",
    "FuturesPackAverageSimpleOutput",
]
