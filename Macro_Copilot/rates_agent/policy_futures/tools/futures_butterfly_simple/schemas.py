"""Pydantic schemas for the policy_futures futures_butterfly_simple tool.

V1 simple-butterfly (3-point curvature) primitive for the
``policy_futures`` domain (ADR 0011) — three strip-position slots on one
``curve_family`` (e.g. SOFR_FUT strip_position 1 / 2 / 3, ER strip 1 / 2
/ 4) combined into an implied-rate butterfly curvature using the
canonical FIXED 50-50 simple-butterfly weighting:

    butterfly_value_pct = implied_rate_body
                          - 0.5 * (implied_rate_wing_short
                                   + implied_rate_wing_long)

Why a new primitive rather than two futures_calendar_spread calls
---------------------------------------------------------------
A simple butterfly is the strip-curvature object for policy futures —
not a sum / difference of two calendar-spread reads. The desk-
recognised quantity is the BUTTERFLY SERIES on the implied-rate axis
(its current value, its 1-day change, its rolling z-score, its
trailing range / percentile). None of those derivatives are available
by post-processing two calendar-spread tool outputs: each calendar
spread is aligned on its own two-leg intersection, while the
butterfly is honest only on the intersection of ALL THREE legs'
trading days. The catalog's standardness disclosure ("futures strip
curvature is a distinct shape object, not reducible to one outright
or one calendar spread") makes this explicit; this primitive owns its
own three-leg read so the snapshot is constructed on aligned dates
and the display + canonical TimeSeries are consistent end-to-end.

Sign convention (wire-frozen)
-----------------------------
``butterfly_value_pct = rate_body − 0.5 * (rate_wing_short +
rate_wing_long)`` where ``rate_*`` is the per-leg implied rate in
PERCENT (derived from the leg's raw_price via the per-strip
``inverse_pricing`` flag). Positive ⇒ belly is CHEAP (body rate above
wing average) — the rate-space analogue of the sovereign tool's
"belly cheap" reading. The methodology-disclosure card on every
response states this convention verbatim so a desk reader cannot
misread the sign.

The input enforces ``strip_position_wing_short < strip_position_body <
strip_position_wing_long`` as a code invariant
(``model_validator(mode='after')``) — the user picks WHICH three strip
slots; the body is identified by ordering; and the sign convention is
then deterministic. The validator also enforces that the three
positions are PAIRWISE DISTINCT (a butterfly with repeated legs is
mathematically degenerate and operationally not a real desk object).

Fixed 50-50 simple-butterfly weighting
--------------------------------------
The catalog's methodology guardrail requires the FIXED simple-
butterfly weighting and the explicit disclosure thereof. The weights
(body = 1, wing_short = -0.5, wing_long = -0.5) are NOT exposed as
input knobs — they live on the methodology card and in the
``config.yaml`` ``conventions.butterfly_weighting`` block. DV01-
neutral / regression-fitted weighting variants are PR11 planned-
extension territory and are explicitly REFUSED by the methodology
disclosure (consumers cannot misread the simple-butterfly value as a
DV01-neutral butterfly).

Why a bespoke ``time_series`` row shape (plus canonical TimeSeries)
------------------------------------------------------------------
The bespoke ``{date, butterfly_value_pct, z_score}`` row shape mirrors
the sovereign butterfly tool's bespoke ``time_series`` (frontend-
friendly per-row pair). The butterfly value itself is single-unit
(PERCENT POINTS = PERCENT) so we ALSO emit canonical
``TimeSeriesUnits.PERCENT`` / ``TimeSeriesUnits.Z_SCORE`` series next
to the bespoke list — the closed-enum exemption used by the
calendar_spread sibling (two units per row) does NOT apply here.
``time_series_butterfly`` and ``time_series_zscore`` are the
operator-compatible canonical exports; ``time_series`` is the
wire-frozen bespoke shape PR15 expects. All three share the same
display rows so they cannot drift.

Field-name discipline on the snapshot (PR14 frozen)
---------------------------------------------------
- ``butterfly_value_pct`` — desk-recognised butterfly value in
  PERCENT POINTS (NOT bps — the policy-futures domain stays in
  PERCENT POINTS on implied-rate-derived objects, matching the
  ``implied_rate_pct`` / ``spread_implied_rate_pct`` siblings; the
  sovereign butterfly's ``butterfly_bps`` convention is a *100
  conversion to bps that does NOT apply here).
- ``daily_change_butterfly_value_pct`` — one-trading-day raw
  subtraction in PERCENT POINTS.
- ``z_score_butterfly`` — rolling 252-trading-day z-score of the
  IMPLIED-RATE BUTTERFLY series.
- ``high_252d_butterfly_value_pct`` /
  ``low_252d_butterfly_value_pct`` /
  ``mid_252d_butterfly_value_pct`` — trailing 252-day range on the
  butterfly series.
- ``percentile_252d`` — percentile rank of the current butterfly
  value within the trailing 252-day range (0-100).
- ``implied_rate_pct_wing_short`` / ``implied_rate_pct_body`` /
  ``implied_rate_pct_wing_long`` — per-leg current implied rates in
  PERCENT (PR14-frozen ``implied_rate_pct`` suffix per the sibling
  futures_price_level tool).
- ``contract_code_*`` (master stems, e.g. ``SFR1`` / ``SFR2`` /
  ``SFR3``) and ``underlying_contract_code_*`` (current-front per
  leg, e.g. ``SFRM26`` / ``SFRU26`` / ``SFRZ26``) disclosed
  side-by-side per leg so the reader knows BOTH the strip slot AND
  the underlying contract each slot resolves to today.
- ``inverse_priced`` + ``short_rate_regime`` carry the regime
  disclosure inline so the implied-rate conversion is transparent on
  the wire (P5 — no hidden methodology choice).

Why strip-position triple inputs (NOT contract codes)
-----------------------------------------------------
Per the catalog (build_order 17) and ADR 0011, policy futures are
keyed by ``(curve_family, strip_position)``. The user-facing knob is
"which THREE slots on the strip?" — SFR1 / SFR2 / SFR3 (front pack
curvature), SFR1 / SFR4 / SFR8 (whites / reds curvature), etc. — not
the master stems. ``contract_code`` is the DISAMBIGUATOR inside the
strip resolution; exposing it as an LLM input would be input-schema
overreach (PR8) and would silently duplicate the strip-position
knobs. The schema carries ``strip_position_wing_short`` /
``strip_position_body`` / ``strip_position_wing_long`` as positive
integers and surfaces the resolved ``contract_code_*`` /
``underlying_contract_code_*`` on the OUTPUT for disclosure. Same
input-keying-axis discipline the sibling ``futures_price_level`` /
``volume_open_interest_snapshot`` / ``futures_calendar_spread`` use.

Validation layering
-------------------
- ``as_of_date`` defaults to ``None`` — the sentinel that means
  "anchor at the universe's last observed trade_date where ALL THREE
  legs have a value". When supplied AND beyond the data max on ANY
  leg, the compute layer returns the documented controlled-error
  envelope per P6 / PR8 / PR16 honest disclosure (no silent
  re-labelling of an unbounded read as a future-anchored read).

- ``field_name`` defaults to ``None`` — the sentinel that means "use
  the YAML's ``default_price_field`` convention". MCP / HTTP
  wrappers translate their wire-level empty-string sentinel to None
  before constructing this input.

- ``frozen=True`` + ``extra="forbid"`` per typed-boundary discipline.
  None of the four models on this primitive carry pandas / numpy
  payloads, so ``arbitrary_types_allowed`` is intentionally omitted.
"""

from __future__ import annotations

from datetime import date
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from shared.schemas import TimeSeries


class FuturesButterflySimpleInput(BaseModel):
    """Parameters the LLM extracts to query a single policy-futures
    same-curve simple butterfly.

    Keyed by ``(curve_family, strip_position_wing_short,
    strip_position_body, strip_position_wing_long)`` per ADR 0011. The
    sign convention is "body minus wing average"; the validator
    enforces ``strip_position_wing_short < strip_position_body <
    strip_position_wing_long`` so the desk-recognised positive-
    butterfly direction (belly cheap) is unambiguous on the wire.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    curve_family: str = Field(
        ...,
        min_length=1,
        description=(
            "Policy-futures curve family. Valid V1 values: "
            "'SOFR_FUT' (US Fed SOFR strip, RFR regime), "
            "'EUR_SHORT_RATE_FUT' (ECB Euribor strip, IBOR regime), "
            "'SONIA_FUT' (BOE SONIA strip, RFR regime). Do NOT pass "
            "bond-futures curve families (UST_FUT / DE_FUT / UK_FUT / "
            "JP_FUT / ...) — those route to the bond_futures agent."
        ),
    )
    strip_position_wing_short: int = Field(
        ...,
        ge=1,
        le=12,
        description=(
            "1-based strip position of the SHORT wing of the butterfly "
            "(the fronter wing). 1 = front contract; higher numbers = "
            "quarterly forwards down the strip. Must be strictly less "
            "than ``strip_position_body`` (enforced by validator); the "
            "butterfly sign convention is "
            "``body − 0.5 * (wing_short + wing_long)``."
        ),
    )
    strip_position_body: int = Field(
        ...,
        ge=1,
        le=12,
        description=(
            "1-based strip position of the BODY (belly) of the "
            "butterfly. Must be strictly greater than "
            "``strip_position_wing_short`` AND strictly less than "
            "``strip_position_wing_long`` (enforced by validator)."
        ),
    )
    strip_position_wing_long: int = Field(
        ...,
        ge=1,
        le=12,
        description=(
            "1-based strip position of the LONG wing of the butterfly "
            "(the backer wing). Must be strictly greater than "
            "``strip_position_body`` (enforced by validator)."
        ),
    )
    lookback_days: int = Field(
        default=365,
        ge=30,
        le=7300,
        description=(
            "Calendar days of history fetched from the DB and used to "
            "scope the observation_count cutoff in the output. Does NOT "
            "control the rolling z-score window (fixed by config "
            "convention z_score_window_days, currently 252) or the "
            "trailing range window (fixed by trailing_range_window_days, "
            "locked at 252 in V1)."
        ),
    )
    as_of_date: Optional[date] = Field(
        default=None,
        description=(
            "As-of date for the snapshot. When omitted (default), the "
            "monitor anchors at the universe's last observed "
            "``trade_date`` for the requested legs (post-fetch data-max "
            "anchor — same pattern as the sibling futures_calendar_spread). "
            "When supplied AND BEYOND the universe's last observed "
            "``trade_date`` for ANY leg, the monitor returns the "
            "documented controlled-error envelope (``{\"error\": \"no "
            "scoreable strip: ...\"}``). When supplied AND within the "
            "universe range, the fetch is anchored at this date so the "
            "snapshot is DETERMINISTIC across runs."
        ),
    )
    field_name: Optional[str] = Field(
        default=None,
        description=(
            "Bloomberg observation field mnemonic. When None (default), "
            "the tool falls through to ``default_price_field`` from "
            "config.yaml (currently 'PX_LAST'). Pass an explicit field "
            "name to override per query. LLM/HTTP wrappers MUST translate "
            "their wire-level sentinel (empty string for MCP, missing "
            "param for FastAPI) to None before constructing this input — "
            "otherwise the YAML default is silently shadowed."
        ),
    )

    @model_validator(mode="after")
    def _strip_positions_must_be_ordered(self) -> "FuturesButterflySimpleInput":
        positions = (
            self.strip_position_wing_short,
            self.strip_position_body,
            self.strip_position_wing_long,
        )
        if len(set(positions)) != 3:
            raise ValueError(
                "strip_position_wing_short, strip_position_body, and "
                "strip_position_wing_long must all be distinct; a "
                "butterfly with repeated legs is mathematically "
                f"degenerate (got {positions})."
            )
        if not (
            self.strip_position_wing_short
            < self.strip_position_body
            < self.strip_position_wing_long
        ):
            raise ValueError(
                "strip_position_wing_short < strip_position_body < "
                "strip_position_wing_long must hold (the butterfly sign "
                "convention is `body − 0.5 * (wing_short + wing_long)`); "
                f"got strip_position_wing_short="
                f"{self.strip_position_wing_short}, "
                f"strip_position_body={self.strip_position_body}, "
                f"strip_position_wing_long={self.strip_position_wing_long}."
            )
        return self


class FuturesButterflySimpleTimeSeriesRow(BaseModel):
    """One observation in the bespoke butterfly-value history.

    Mirrors the sovereign butterfly tool's per-row ``{date,
    butterfly_*, z_score}`` shape; carries the butterfly value plus its
    rolling z-score side-by-side. Both quantities are rounded with the
    same conventions the snapshot uses so the snapshot equals
    ``time_series[-1]`` STRICTLY at the latest row.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    date: str = Field(..., description="Trade date in YYYY-MM-DD form.")
    butterfly_value_pct: float = Field(
        ...,
        description=(
            "Butterfly value in PERCENT POINTS at the trade date: "
            "``implied_rate_pct(body) - 0.5 * "
            "(implied_rate_pct(wing_short) + "
            "implied_rate_pct(wing_long))``. Rounded with "
            "``butterfly_value_round_decimals`` from config.yaml."
        ),
    )
    z_score: Optional[float] = Field(
        None,
        description=(
            "Rolling z-score of the butterfly value at this trade date "
            "(``None`` during the warmup window before "
            "``z_score_min_periods`` observations accumulate). Rounded "
            "with ``z_score_round_decimals`` from config.yaml."
        ),
    )


class FuturesButterflySimpleCurrentMetrics(BaseModel):
    """Snapshot of the latest aligned butterfly bar.

    Field naming is unit-honest and regime-aware:

    - ``butterfly_value_pct`` carries the desk-recognised butterfly
      value in PERCENT POINTS (NOT bps).
    - ``daily_change_butterfly_value_pct`` is a raw subtraction (NOT
      *100).
    - ``high_252d_butterfly_value_pct`` /
      ``low_252d_butterfly_value_pct`` /
      ``mid_252d_butterfly_value_pct`` embed the trailing-range
      window length (wire-frozen at 252 in V1).
    - ``inverse_priced`` / ``short_rate_regime`` carry the methodology
      disclosure inline so the implied-rate conversion is transparent
      on the wire.
    - Per-leg disclosure block carries both the strip-slot master stem
      (stable across rolls) and the current-front underlying contract
      per leg (rotates at roll), plus the per-leg expiry + security
      name from the SCD2 ``instrument_metadata_history`` row whose
      effective window contains ``as_of_date``.
    - Per-leg ``implied_rate_pct_*`` carries the latest implied rate on
      each of the three legs in PERCENT (PR14-frozen suffix).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    as_of_date: str = Field(
        ...,
        description=(
            "Most recent trade date on which ALL THREE legs have a "
            "value (YYYY-MM-DD). The snapshot is anchored to the "
            "intersection of the three legs' trading days so the row "
            "cannot pair fresh quotes on two legs with a stale quote "
            "on the third."
        ),
    )
    curve_family: str = Field(..., description="Policy-futures curve family.")
    strip_position_wing_short: int = Field(
        ...,
        ge=1,
        description="1-based strip position of the short (fronter) wing.",
    )
    strip_position_body: int = Field(
        ...,
        ge=1,
        description="1-based strip position of the body (belly).",
    )
    strip_position_wing_long: int = Field(
        ...,
        ge=1,
        description="1-based strip position of the long (backer) wing.",
    )
    butterfly_label: str = Field(
        ...,
        description=(
            "Human-readable label for the butterfly triple, e.g. "
            "``'SFR1-SFR2-SFR3'`` (SOFR_FUT strip_position 1 / 2 / 3) "
            "or ``'ER1-ER2-ER4'`` (EUR_SHORT_RATE_FUT 1 / 2 / 4). "
            "Derived from the three legs' resolved ``contract_code`` "
            "stems."
        ),
    )
    contract_code_wing_short: str = Field(
        ...,
        description=(
            "Strip-slot master stem of the short wing (e.g. "
            "``'SFR1'``). Stable across rolls — see "
            "``underlying_contract_code_wing_short`` for the current "
            "front."
        ),
    )
    contract_code_body: str = Field(
        ...,
        description=(
            "Strip-slot master stem of the body leg (e.g. ``'SFR2'``)."
        ),
    )
    contract_code_wing_long: str = Field(
        ...,
        description=(
            "Strip-slot master stem of the long wing (e.g. ``'SFR3'``)."
        ),
    )
    underlying_contract_code_wing_short: Optional[str] = Field(
        None,
        description=(
            "Current-front underlying contract the short wing resolves "
            "to AS OF ``as_of_date`` (e.g. ``'SFRM26'``)."
        ),
    )
    underlying_contract_code_body: Optional[str] = Field(
        None,
        description=(
            "Current-front underlying contract the body leg resolves "
            "to AS OF ``as_of_date`` (e.g. ``'SFRU26'``)."
        ),
    )
    underlying_contract_code_wing_long: Optional[str] = Field(
        None,
        description=(
            "Current-front underlying contract the long wing resolves "
            "to AS OF ``as_of_date`` (e.g. ``'SFRZ26'``)."
        ),
    )
    security_name_wing_short: Optional[str] = Field(
        None,
        description=(
            "Latest-effective ``SECURITY_DES`` for the short wing's "
            "current-front contract."
        ),
    )
    security_name_body: Optional[str] = Field(
        None,
        description=(
            "Latest-effective ``SECURITY_DES`` for the body leg's "
            "current-front contract."
        ),
    )
    security_name_wing_long: Optional[str] = Field(
        None,
        description=(
            "Latest-effective ``SECURITY_DES`` for the long wing's "
            "current-front contract."
        ),
    )
    expiry_date_wing_short: Optional[str] = Field(
        None,
        description=(
            "``LAST_TRADEABLE_DT`` for the short wing's current-front "
            "contract. YYYY-MM-DD."
        ),
    )
    expiry_date_body: Optional[str] = Field(
        None,
        description=(
            "``LAST_TRADEABLE_DT`` for the body leg's current-front "
            "contract. YYYY-MM-DD."
        ),
    )
    expiry_date_wing_long: Optional[str] = Field(
        None,
        description=(
            "``LAST_TRADEABLE_DT`` for the long wing's current-front "
            "contract. YYYY-MM-DD."
        ),
    )
    inverse_priced: bool = Field(
        ...,
        description=(
            "Inverse-pricing flag (read from "
            "``instrument_master.attributes->>'inverse_pricing'`` on "
            "ALL THREE legs; same-curve butterflies share the flag in "
            "V1). When ``True`` (SFR / ER / SFI), "
            "``implied_rate_pct = 100 - raw_price`` per leg; drives the "
            "conversion off METADATA, not a hardcoded list in "
            "compute.py (PR8 / P6)."
        ),
    )
    short_rate_regime: str = Field(
        ...,
        description=(
            "Methodology disclosure label for the underlying short-"
            "rate regime: ``'RFR'`` or ``'IBOR'``. Resolved from the "
            "YAML's ``short_rate_regime_map`` convention. Carried "
            "verbatim on the methodology disclosure string per P5 + "
            "ADR 0011."
        ),
    )
    implied_rate_pct_wing_short: float = Field(
        ...,
        description=(
            "Latest implied rate on the short wing in PERCENT. PR14-"
            "frozen unit suffix (``_pct``); do NOT rename to ``_bps`` "
            "or ``_rate``."
        ),
    )
    implied_rate_pct_body: float = Field(
        ...,
        description=(
            "Latest implied rate on the body leg in PERCENT."
        ),
    )
    implied_rate_pct_wing_long: float = Field(
        ...,
        description=(
            "Latest implied rate on the long wing in PERCENT."
        ),
    )
    butterfly_value_pct: float = Field(
        ...,
        description=(
            "Latest aligned butterfly value in PERCENT POINTS: "
            "``rate_body - 0.5 * (rate_wing_short + rate_wing_long)``. "
            "The desk-recognised simple-butterfly quantity. Positive "
            "⇒ body rate above wing average (belly CHEAP in rate "
            "space)."
        ),
    )
    daily_change_butterfly_value_pct: Optional[float] = Field(
        None,
        description=(
            "1-trading-day change in ``butterfly_value_pct`` (raw "
            "subtraction in PERCENT POINTS; NOT multiplied by 100)."
        ),
    )
    z_score_butterfly: Optional[float] = Field(
        None,
        description=(
            "Rolling 252-trading-day z-score of the butterfly series."
        ),
    )
    high_252d_butterfly_value_pct: Optional[float] = Field(
        None,
        description=(
            "Highest butterfly value over the trailing 252 trading "
            "days, in PERCENT POINTS."
        ),
    )
    low_252d_butterfly_value_pct: Optional[float] = Field(
        None,
        description=(
            "Lowest butterfly value over the trailing 252 trading "
            "days, in PERCENT POINTS."
        ),
    )
    mid_252d_butterfly_value_pct: Optional[float] = Field(
        None,
        description=(
            "Midpoint of the trailing 252-day butterfly range "
            "(``(high + low) / 2``), in PERCENT POINTS."
        ),
    )
    percentile_252d: Optional[float] = Field(
        None,
        description=(
            "Percentile rank of ``butterfly_value_pct`` within the "
            "trailing 252-day butterfly range (0-100)."
        ),
    )
    rolling_window_days: int = Field(
        ...,
        description=(
            "The trading-day window used for the z-score (mirrors the "
            "field name the sibling tools use for the same disclosure)."
        ),
    )
    observation_count: int = Field(
        ...,
        description=(
            "Number of aligned trading days within the "
            "``lookback_days`` window. Counts dates where ALL THREE "
            "legs have a value after cleaning + intersection."
        ),
    )


class FuturesButterflySimpleOutput(BaseModel):
    """Top-level response for the policy_futures
    futures_butterfly_simple tool.

    Wire shape:
      - ``current_metrics``: snapshot of the latest aligned butterfly
        bar + per-leg disclosure block + the three per-leg implied
        rates.
      - ``time_series``: bespoke per-row ``{date, butterfly_value_pct,
        z_score}`` list (mirrors the sovereign butterfly tool's
        frontend-friendly shape).
      - ``time_series_butterfly``: canonical TimeSeries of the
        butterfly series in PERCENT POINTS (closed-enum
        ``TimeSeriesUnits.PERCENT``). Values match
        ``time_series[i].butterfly_value_pct`` 1-to-1 by construction.
      - ``time_series_zscore``: canonical TimeSeries of the rolling
        z-score (closed-enum ``TimeSeriesUnits.Z_SCORE``).
      - ``methodology_disclosure``: the P5 / ADR 0011 caveat carried
        on every response. Includes the sign convention, the fixed
        50-50 simple-butterfly weighting, the inverse-pricing rule,
        the regime label, the z-score lookback window, the strip-
        position keying, AND the explicit refusal of meeting-by-
        meeting policy-path framing + DV01-neutral / regression-
        fitted butterfly variants (PR11 planned-extension territory).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    current_metrics: FuturesButterflySimpleCurrentMetrics
    time_series: List[FuturesButterflySimpleTimeSeriesRow] = Field(
        default_factory=list,
        description=(
            "Historical butterfly + z-score observations over the "
            "display window (cleaned, intersected, rounded). Bespoke "
            "row shape so the frontend chart pulls both the value and "
            "the z-score from one row; the canonical TimeSeries fields "
            "below carry the same series for operator-layer "
            "consumption."
        ),
    )
    time_series_butterfly: TimeSeries = Field(
        ...,
        description=(
            "Historical butterfly value series in PERCENT POINTS. "
            "Closed-enum ``TimeSeriesUnits.PERCENT``; series_name = "
            "``'<curve_family_lower>_<wing_short>_<body>_<wing_long>_"
            "butterfly'``. Values match "
            "``time_series[i].butterfly_value_pct`` 1-to-1 by "
            "construction."
        ),
    )
    time_series_zscore: TimeSeries = Field(
        ...,
        description=(
            "Historical rolling z-score of the butterfly value vs its "
            "own trailing window. Closed-enum "
            "``TimeSeriesUnits.Z_SCORE``; series_name = "
            "``'<curve_family_lower>_<wing_short>_<body>_<wing_long>_"
            "zscore'``. Values match ``time_series[i].z_score`` 1-to-1 "
            "(``None`` for rows in the rolling-window warmup)."
        ),
    )
    methodology_disclosure: str = Field(
        ...,
        min_length=1,
        description=(
            "P5 / ADR 0011 caveat carried on every response. Includes "
            "the sign convention (``body − 0.5 * (wing_short + "
            "wing_long)``), the fixed 50-50 simple-butterfly "
            "weighting, the inverse-pricing rule, the per-curve_family "
            "regime label (RFR vs IBOR), the z-score lookback window, "
            "the trailing-range window, the strip-position keying, "
            "and the explicit refusal of meeting-by-meeting policy-"
            "path framing + DV01-neutral / regression-fitted butterfly "
            "variants. Required so consumers cannot drop the "
            "disclosure when relaying the snapshot."
        ),
    )


__all__ = [
    "FuturesButterflySimpleInput",
    "FuturesButterflySimpleTimeSeriesRow",
    "FuturesButterflySimpleCurrentMetrics",
    "FuturesButterflySimpleOutput",
]
