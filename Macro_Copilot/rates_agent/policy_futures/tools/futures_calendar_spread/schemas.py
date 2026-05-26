"""Pydantic schemas for the policy_futures futures_calendar_spread tool.

V1 calendar-spread primitive for the ``policy_futures`` domain (ADR
0013) — same-curve implied-rate spread between two strip-position
slots on one ``curve_family`` (e.g. ``SOFR_FUT`` strip_position 1 vs
strip_position 2, ``EUR_SHORT_RATE_FUT`` strip 1 vs strip 4, etc.).

Why a new primitive rather than two ``futures_price_level`` calls
-----------------------------------------------------------------
A calendar spread is the strip-slope object for policy futures, not a
sum of two outright reads. The desk-recognised quantity is the SPREAD
SERIES on the implied-rate axis — its current value, its day-on-day
change, its rolling z-score, its trailing range / percentile. None of
those derivatives are available by post-processing two outright-tool
outputs: the two outright series are aligned on each leg's own trading
calendar, while the spread is only honest on the intersection of the
two legs' trading days. The catalog's standardness disclosure
("calendar spread is the strip-slope object, not just two standalone
price levels") makes this explicit; this primitive owns its own two-
leg read so the snapshot is constructed on aligned dates and the
display + canonical TimeSeries are consistent end-to-end.

Sign convention (wire-frozen)
-----------------------------
``spread_implied_rate_pct = implied_rate_pct(short_leg) -
implied_rate_pct(long_leg)`` where ``short_leg`` is the FRONTER strip
position (smaller ``strip_position`` integer) and ``long_leg`` is the
BACKER strip position (larger ``strip_position`` integer). For an
inverted strip (front rate above back rate), the spread is positive;
for a normal upward-sloping strip in implied-rate space, the spread
is negative. The methodology-disclosure card on every response states
this convention verbatim so a desk reader cannot misread the sign.

The input enforces ``strip_position_short < strip_position_long`` as
a code invariant (``model_validator(mode='after')``) — the user picks
WHICH two strip slots, the FRONTER one is identified by ordering, and
the sign convention is then deterministic. The validator also
enforces ``strip_position_short != strip_position_long`` (a calendar
spread between identical legs is mathematically zero and operationally
not a real desk object).

Why a bespoke ``time_series`` row shape (NOT canonical TimeSeries)
------------------------------------------------------------------
Each row in the historical series carries TWO unit spaces — the raw
futures-price spread (in the contract's native quote space ``100 -
rate``) AND the implied-rate spread in PERCENT POINTS. The reason is
the same as the sibling ``futures_price_level`` tool: the closed-enum
``shared.schemas.time_series.TimeSeriesUnits`` family has no PRICE
member; declaring ``percent`` would only cover the implied-rate axis
and silently mis-label the raw-price spread axis. Extending
``TimeSeriesUnits`` is ADR-gated per
``docs_revamped/03_standards/closed_family_discipline.md`` §6, and
ADR 0013 (which sanctions this primitive) does NOT authorise that
extension. The bespoke ``{date, raw_price_spread,
spread_implied_rate_pct}`` shape keeps both reads honest, mirrors the
sibling ``futures_price_level`` choice, and is the same exempt
pattern documented at ``shared/workflow/validate.py:368``
(``output_field_units={}``).

Field-name discipline on the snapshot (PR14 frozen)
---------------------------------------------------
- ``spread_implied_rate_pct`` — desk-recognised calendar spread in
  PERCENT POINTS (NOT bps; NOT raw price subtraction). PR14 freezes
  the ``implied_rate_pct`` name on the sibling outright tool; the
  calendar spread reuses the suffix to keep the unit space readable
  at a glance.
- ``raw_price_spread`` — the corresponding spread in the contract's
  native price space, carried in parallel for consumers that need
  the price view (for inverse-priced strips this equals
  ``-spread_implied_rate_pct``).
- ``daily_change_spread_implied_rate_pct`` / ``daily_change_raw_price_spread``
  — one-trading-day raw subtraction in each unit space (NOT *100;
  the implied-rate delta is in PERCENT POINTS, not bps).
- ``z_score_spread_implied_rate`` — rolling 252-trading-day z-score of
  the IMPLIED-RATE spread series. The z lives on the rate-spread axis
  because the desk-recognised level IS the rate spread; z-scoring the
  raw-price-spread series for an inverse-priced strip would flip the
  sign of every "extreme" reading relative to the rate spread.
- ``high_252d_spread_implied_rate_pct`` / ``low_252d_spread_implied_rate_pct``
  / ``mid_252d_spread_implied_rate_pct`` — trailing 252-day range on
  the implied-rate spread series.
- ``percentile_252d`` — percentile rank of the current implied-rate
  spread within the trailing 252-day implied-rate spread range
  (0-100). Reported on the rate-spread axis because that is the
  desk-recognised view.
- ``contract_code_short`` / ``contract_code_long`` (master stems, e.g.
  ``SFR1`` / ``SFR2``) and ``underlying_contract_code_short`` /
  ``underlying_contract_code_long`` (current-front per leg, e.g.
  ``SFRM26`` / ``SFRU26``) disclosed side-by-side per leg so the
  reader knows BOTH the strip slot AND the underlying contract each
  slot resolves to today.
- ``inverse_priced`` + ``short_rate_regime`` carry the regime
  disclosure inline so the implied-rate conversion is transparent on
  the wire (P5 — no hidden methodology choice).

Why ``strip_position_short`` + ``strip_position_long`` are the input
keying axes (NOT ``contract_code_short`` / ``contract_code_long``)
--------------------------------------------------------------------
Per the catalog (build_order 16) and ADR 0013, policy futures are
keyed by ``(curve_family, strip_position)``. The user-facing knob is
"which TWO slots on the strip?" — SFR1 (front) vs SFR2 (front+1), or
SFR1 vs SFR4 (front vs end-of-whites) — not the master stems
``contract_code_short`` / ``contract_code_long`` (which are just the
strip slots' playbook labels). ``contract_code`` is the DISAMBIGUATOR
inside the strip resolution; exposing it as an LLM input would be
input-schema overreach (PR8) and would silently duplicate the
strip-position knobs. The schema carries ``strip_position_short`` +
``strip_position_long`` as positive integers and surfaces the
resolved ``contract_code_short`` / ``contract_code_long`` on the
OUTPUT for disclosure. Same input-keying-axis discipline the sibling
``futures_price_level`` / ``volume_open_interest_snapshot`` use.

Validation layering
-------------------
- ``as_of_date`` defaults to ``None`` — the sentinel that means
  "anchor at the universe's last observed trade_date where BOTH legs
  have a value". When supplied AND beyond the data max on either
  leg, the compute layer returns the documented controlled-error
  envelope per P6 / PR8 / PR16 honest disclosure (no silent
  re-labelling of an unbounded read as a future-anchored read).

- ``field_name`` defaults to ``None`` — the sentinel that means "use
  the YAML's ``default_price_field`` convention". MCP / HTTP
  wrappers translate their wire-level empty-string sentinel to None
  before constructing this input (same shadowing-fix pattern fixed
  for sovereign curve_move_classifier in commit b2605ee).

- ``frozen=True`` + ``extra="forbid"`` per typed-boundary discipline
  (``docs_revamped/03_standards/typed_boundary_discipline.md`` §1).
  None of the four models on this primitive carry pandas / numpy
  payloads, so ``arbitrary_types_allowed`` is intentionally
  omitted.
"""

from __future__ import annotations

from datetime import date
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FuturesCalendarSpreadInput(BaseModel):
    """Parameters the LLM extracts to query a single policy-futures
    same-curve calendar spread.

    Keyed by ``(curve_family, strip_position_short, strip_position_long)``
    per ADR 0013. The sign convention is "short leg minus long leg" =
    "fronter minus backer"; the validator enforces ``strip_position_short
    < strip_position_long`` so the desk-recognised positive-spread
    direction is unambiguous on the wire.
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
    strip_position_short: int = Field(
        ...,
        ge=1,
        le=12,
        description=(
            "1-based strip position of the SHORT (fronter) leg of the "
            "calendar spread. 1 = front contract; higher numbers = "
            "quarterly forwards down the strip. Must be strictly less "
            "than ``strip_position_long`` (enforced by validator); "
            "the spread sign convention is `short_leg − long_leg`, so "
            "fronter-minus-backer is positive when the front rate is "
            "above the back rate (inverted strip)."
        ),
    )
    strip_position_long: int = Field(
        ...,
        ge=1,
        le=12,
        description=(
            "1-based strip position of the LONG (backer) leg of the "
            "calendar spread. Must be strictly greater than "
            "``strip_position_short`` (enforced by validator)."
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
            "anchor — same pattern as the sibling futures_price_level). "
            "When supplied AND BEYOND the universe's last observed "
            "``trade_date`` for EITHER leg, the monitor returns the "
            "documented controlled-error envelope (``{\"error\": \"no "
            "scoreable strip: ...\"}``) — the snapshot refuses to "
            "silently re-label an unbounded read as a future-anchored "
            "read (P5 / PR8 honest disclosure). When supplied AND "
            "within the universe range, the fetch is anchored at this "
            "date so the snapshot is DETERMINISTIC across runs (same "
            "as_of_date + same DB state ⇒ same numbers)."
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
            "otherwise the YAML default is silently shadowed (same "
            "wrapper-shadowing pattern fixed for sovereign "
            "curve_move_classifier in commit b2605ee)."
        ),
    )

    @model_validator(mode="after")
    def _strip_positions_must_be_ordered(self) -> "FuturesCalendarSpreadInput":
        # Code-level invariant (PR9 / PR10 — invariants in code, not YAML).
        if self.strip_position_short == self.strip_position_long:
            raise ValueError(
                "strip_position_short and strip_position_long must "
                "differ; a same-leg calendar spread is mathematically "
                f"zero (both = {self.strip_position_short})."
            )
        if self.strip_position_short > self.strip_position_long:
            raise ValueError(
                "strip_position_short must be strictly less than "
                "strip_position_long (the spread sign convention is "
                "`short_leg − long_leg` = `fronter − backer`); got "
                f"strip_position_short={self.strip_position_short}, "
                f"strip_position_long={self.strip_position_long}."
            )
        return self


class FuturesCalendarSpreadTimeSeriesRow(BaseModel):
    """One observation in the bespoke calendar-spread history.

    Carries BOTH unit spaces side-by-side so a downstream consumer
    reading a row can map either the raw-price spread or the implied-
    rate spread without re-deriving the conversion. The per-row
    ``raw_price_spread`` is in the contract's native quote space (for
    SFR / ER / SFI: ``(100 - rate_short) - (100 - rate_long) =
    rate_long - rate_short``); the per-row ``spread_implied_rate_pct``
    is in PERCENT POINTS (``rate_short - rate_long``). For inverse-
    priced strips these two quantities are exact negatives. Both are
    rounded with the same conventions the snapshot uses so
    ``current_metrics.spread_implied_rate_pct`` equals
    ``time_series[-1].spread_implied_rate_pct`` and
    ``current_metrics.raw_price_spread`` equals
    ``time_series[-1].raw_price_spread`` STRICTLY at the latest row."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    date: str = Field(..., description="Trade date in YYYY-MM-DD form.")
    raw_price_spread: float = Field(
        ...,
        description=(
            "Calendar spread in the contract's native price space: "
            "``raw_price(short_leg) - raw_price(long_leg)``. For "
            "inverse-priced strips this equals "
            "``-spread_implied_rate_pct`` by construction. Rounded "
            "with ``raw_price_round_decimals`` from config.yaml."
        ),
    )
    spread_implied_rate_pct: float = Field(
        ...,
        description=(
            "Calendar spread in implied-rate space, in PERCENT POINTS: "
            "``implied_rate_pct(short_leg) - "
            "implied_rate_pct(long_leg)``. The desk-recognised "
            "calendar-spread quantity. Rounded with "
            "``implied_rate_round_decimals`` from config.yaml."
        ),
    )


class FuturesCalendarSpreadCurrentMetrics(BaseModel):
    """Snapshot of the latest aligned calendar-spread bar.

    Field naming is unit-honest and regime-aware:

    - ``spread_implied_rate_pct`` carries the desk-recognised calendar
      spread in PERCENT POINTS (NOT bps; NOT raw price subtraction).
    - ``raw_price_spread`` carries the contract's native price-space
      spread.
    - ``daily_change_spread_implied_rate_pct`` /
      ``daily_change_raw_price_spread`` are raw subtractions in each
      unit space (NOT *100).
    - ``high_252d_spread_implied_rate_pct`` /
      ``low_252d_spread_implied_rate_pct`` /
      ``mid_252d_spread_implied_rate_pct`` embed the trailing-range
      window length (wire-frozen at 252 in V1).
    - ``inverse_priced`` / ``short_rate_regime`` carry the methodology
      disclosure inline so the implied-rate conversion is transparent
      on the wire.
    - Per-leg disclosure block carries both the strip-slot master stem
      (stable across rolls) and the current-front underlying contract
      per leg (rotates at roll), plus the per-leg expiry + contract
      size from the SCD2 ``instrument_metadata_history`` row whose
      effective window contains ``as_of_date``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    as_of_date: str = Field(
        ...,
        description=(
            "Most recent trade date on which BOTH legs have a value "
            "(YYYY-MM-DD). The snapshot is anchored to the "
            "intersection of the two legs' trading days so the row "
            "cannot pair a fresh short-leg quote with a stale long-leg "
            "quote (or vice versa)."
        ),
    )
    curve_family: str = Field(..., description="Policy-futures curve family.")
    strip_position_short: int = Field(
        ...,
        ge=1,
        description="1-based strip position of the short (fronter) leg.",
    )
    strip_position_long: int = Field(
        ...,
        ge=1,
        description="1-based strip position of the long (backer) leg.",
    )
    spread_label: str = Field(
        ...,
        description=(
            "Human-readable label for the calendar pair, e.g. "
            "``'SFR1-SFR2'`` (SOFR_FUT strip_position 1 minus "
            "strip_position 2) or ``'ER1-ER4'`` "
            "(EUR_SHORT_RATE_FUT 1 minus 4). Derived from the two "
            "legs' resolved ``contract_code`` stems."
        ),
    )
    contract_code_short: str = Field(
        ...,
        description=(
            "Strip-slot master stem of the short leg (e.g. "
            "``'SFR1'``). Stable across rolls — see "
            "``underlying_contract_code_short`` for the current front."
        ),
    )
    contract_code_long: str = Field(
        ...,
        description=(
            "Strip-slot master stem of the long leg (e.g. ``'SFR2'``)."
        ),
    )
    underlying_contract_code_short: Optional[str] = Field(
        None,
        description=(
            "Current-front underlying contract the short leg resolves "
            "to AS OF ``as_of_date`` (e.g. ``'SFRM26'``). Read off "
            "the SCD2 ``instrument_metadata_history`` row whose "
            "effective window contains ``as_of_date``."
        ),
    )
    underlying_contract_code_long: Optional[str] = Field(
        None,
        description=(
            "Current-front underlying contract the long leg resolves "
            "to AS OF ``as_of_date`` (e.g. ``'SFRU26'``)."
        ),
    )
    security_name_short: Optional[str] = Field(
        None,
        description=(
            "Latest-effective ``SECURITY_DES`` for the short leg's "
            "current-front contract."
        ),
    )
    security_name_long: Optional[str] = Field(
        None,
        description=(
            "Latest-effective ``SECURITY_DES`` for the long leg's "
            "current-front contract."
        ),
    )
    expiry_date_short: Optional[str] = Field(
        None,
        description=(
            "``LAST_TRADEABLE_DT`` for the short leg's current-front "
            "contract. YYYY-MM-DD."
        ),
    )
    expiry_date_long: Optional[str] = Field(
        None,
        description=(
            "``LAST_TRADEABLE_DT`` for the long leg's current-front "
            "contract. YYYY-MM-DD."
        ),
    )
    inverse_priced: bool = Field(
        ...,
        description=(
            "Inverse-pricing flag (read from "
            "``instrument_master.attributes->>'inverse_pricing'`` on "
            "BOTH legs; both legs of a same-curve calendar spread "
            "share the flag in V1). When ``True`` (SFR / ER / SFI), "
            "``implied_rate_pct = 100 - raw_price`` per leg; the "
            "spread on the implied-rate axis is therefore "
            "``rate_short - rate_long = -(price_short - price_long)``. "
            "Drives the conversion off METADATA, not a hardcoded list "
            "in compute.py (PR8 / P6 — no hidden methodology choice "
            "in code)."
        ),
    )
    short_rate_regime: str = Field(
        ...,
        description=(
            "Methodology disclosure label for the underlying short-"
            "rate regime: ``'RFR'`` (compounded risk-free rate — SOFR "
            "/ SONIA) or ``'IBOR'`` (unsecured term IBOR rate — "
            "Euribor). Resolved from the YAML's "
            "``short_rate_regime_map`` convention against the "
            "requested ``curve_family``. Carried verbatim on the "
            "methodology disclosure string per P5 + ADR 0013."
        ),
    )
    raw_price_spread: float = Field(
        ...,
        description=(
            "Latest aligned calendar spread in the contract's native "
            "price space: ``raw_price(short_leg) - "
            "raw_price(long_leg)``. NOT a rate. Rounded with "
            "``raw_price_round_decimals`` from config.yaml."
        ),
    )
    spread_implied_rate_pct: float = Field(
        ...,
        description=(
            "Latest aligned calendar spread in implied-rate space, in "
            "PERCENT POINTS: ``implied_rate_pct(short_leg) - "
            "implied_rate_pct(long_leg)``. The desk-recognised "
            "calendar-spread quantity. PR14-frozen unit suffix "
            "(``_implied_rate_pct``); do NOT rename to "
            "``_implied_rate`` / ``_bps`` / ``_pp``."
        ),
    )
    daily_change_raw_price_spread: Optional[float] = Field(
        None,
        description=(
            "1-trading-day change in ``raw_price_spread`` (raw "
            "subtraction; NOT multiplied by 100). For inverse-priced "
            "strips, the sign is the OPPOSITE of "
            "``daily_change_spread_implied_rate_pct``."
        ),
    )
    daily_change_spread_implied_rate_pct: Optional[float] = Field(
        None,
        description=(
            "1-trading-day change in ``spread_implied_rate_pct`` (raw "
            "subtraction in PERCENT POINTS; NOT multiplied by 100 to "
            "bps)."
        ),
    )
    z_score_spread_implied_rate: Optional[float] = Field(
        None,
        description=(
            "Rolling 252-trading-day z-score of the IMPLIED-RATE "
            "spread series. The z lives on the rate-spread axis (not "
            "the raw-price-spread axis) because the desk-recognised "
            "level IS the rate spread; z-scoring the raw-price-spread "
            "for an inverse-priced strip would flip the sign of every "
            "'extreme' reading relative to the rate spread."
        ),
    )
    high_252d_spread_implied_rate_pct: Optional[float] = Field(
        None,
        description=(
            "Highest implied-rate spread over the trailing 252 "
            "trading days, in PERCENT POINTS."
        ),
    )
    low_252d_spread_implied_rate_pct: Optional[float] = Field(
        None,
        description=(
            "Lowest implied-rate spread over the trailing 252 "
            "trading days, in PERCENT POINTS."
        ),
    )
    mid_252d_spread_implied_rate_pct: Optional[float] = Field(
        None,
        description=(
            "Midpoint of the trailing 252-day implied-rate spread "
            "range (``(high + low) / 2``), in PERCENT POINTS."
        ),
    )
    percentile_252d: Optional[float] = Field(
        None,
        description=(
            "Percentile rank of ``spread_implied_rate_pct`` within "
            "the trailing 252-day implied-rate spread range (0-100). "
            "Reported on the rate-spread axis because that is the "
            "desk-recognised view."
        ),
    )
    rolling_window_days: int = Field(
        ...,
        description=(
            "The trading-day window used for the z-score (mirrors the "
            "field name the sibling ``curve_spread`` snapshot uses "
            "for the same disclosure)."
        ),
    )
    observation_count: int = Field(
        ...,
        description=(
            "Number of aligned trading days within the "
            "``lookback_days`` window. Counts dates where BOTH legs "
            "have a value after cleaning + intersection."
        ),
    )


class FuturesCalendarSpreadOutput(BaseModel):
    """Top-level response for the policy_futures
    futures_calendar_spread tool.

    Wire shape:
      - ``current_metrics``: snapshot of the latest aligned spread
        bar + per-leg disclosure block (contract_code_short /
        contract_code_long / underlying_contract_code_* /
        security_name_* / expiry_date_* / inverse_priced /
        short_rate_regime).
      - ``time_series``: bespoke list of ``{date, raw_price_spread,
        spread_implied_rate_pct}`` rows over the displayed window.
        Both per-row fields are rounded with the same conventions the
        snapshot uses, so the snapshot equals ``time_series[-1]``
        STRICTLY at the latest row.
      - ``methodology_disclosure``: the P5 / ADR 0013 caveat carried
        on every response so consumers cannot drop the disclosure
        when relaying the snapshot. Includes the sign convention,
        the inverse-pricing rule, the regime label, the z-score
        lookback window, and the strip-position keying.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    current_metrics: FuturesCalendarSpreadCurrentMetrics
    time_series: List[FuturesCalendarSpreadTimeSeriesRow] = Field(
        default_factory=list,
        description=(
            "Historical calendar-spread observations over the display "
            "window (cleaned, intersected, rounded). Bespoke shape — "
            "not ``shared.schemas.TimeSeries`` — because each row "
            "carries two unit spaces side-by-side and "
            "``TimeSeriesUnits`` has no PRICE member in V1 (ADR-gated "
            "extension per P8). See module docstring."
        ),
    )
    methodology_disclosure: str = Field(
        ...,
        min_length=1,
        description=(
            "P5 / ADR 0013 caveat carried on every response. Includes "
            "the sign convention (`short_leg − long_leg` = `fronter − "
            "backer`), the per-curve_family regime label (RFR vs "
            "IBOR), the inverse-pricing rule, the z-score lookback "
            "window, the trailing-range window, the strip-position "
            "keying, and the rolling-generic-strip-read scope-limit "
            "caveat. Required so consumers cannot drop the disclosure "
            "when relaying the snapshot."
        ),
    )


__all__ = [
    "FuturesCalendarSpreadInput",
    "FuturesCalendarSpreadTimeSeriesRow",
    "FuturesCalendarSpreadCurrentMetrics",
    "FuturesCalendarSpreadOutput",
]
