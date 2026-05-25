"""Pydantic schemas for the policy_futures
futures_cross_market_spread tool.

V1 cross-market-spread primitive for the ``policy_futures`` domain
(ADR 0013) — matched-strip implied-rate differential between TWO
different ``curve_family`` values at ONE strip position (e.g.
``SOFR_FUT`` vs ``SONIA_FUT`` strip_position 1 → SFR1 − SFI1, or
``SOFR_FUT`` vs ``EUR_SHORT_RATE_FUT`` strip_position 4 → SFR4 − ER4).

Why a new primitive rather than two ``futures_price_level`` calls
-----------------------------------------------------------------
A cross-market spread is the cross-CB divergence object — its own
macro RV target, not a difference of two outright reads. The desk-
recognised quantity is the SPREAD SERIES on the implied-rate axis
(its current value, its 1-day change, its rolling z-score, its
trailing range / percentile). None of those derivatives are
available by post-processing two outright tool outputs: each
outright series is aligned on its own market's trading calendar,
while the cross-market spread is honest only on the INTERSECTION
of the two markets' trading days (US vs UK vs Euro area calendars
differ significantly). The catalog's standardness disclosure
("cross-central-bank divergence in futures pricing is its own
macro RV object, not just two separate outrights") makes this
explicit; this primitive owns its own two-leg read so the snapshot
is constructed on aligned dates and the display + canonical
TimeSeries are consistent end-to-end.

Sign convention (wire-frozen)
-----------------------------
``spread_value_pct = implied_rate_pct(curve_family_a) -
implied_rate_pct(curve_family_b)`` where A is the requested
``curve_family_a`` and B is ``curve_family_b``. Wire-frozen
orientation; swapping the inputs flips the sign by construction.
The methodology-disclosure card on every response echoes the
specific A and B labels back so a desk reader cannot misread the
direction.

The input enforces ``curve_family_a != curve_family_b`` as a code
invariant (``model_validator(mode='after')``) — a self-spread is
mathematically zero and operationally not a real desk object.

Raw cross-market differential (catalog guardrail)
--------------------------------------------------
The catalog's methodology guardrail REQUIRES that the output is
labelled as a RAW cross-market implied-rate differential — NOT
basis-adjusted (cross-currency basis netted) and NOT beta-adjusted
(regression-residual). The methodology card carries this label
verbatim. Basis-adjusted and beta-adjusted variants are PR11
planned-extension territory and ship as separate primitives in
future builds; this primitive's methodology card REFUSES to mix
them under the ``cross_market_spread`` name. There are NO input
knobs for basis / beta selection — the absence of those knobs is
load-bearing on the catalog guardrail.

Mixed RFR/IBOR pair handling
----------------------------
The catalog's methodology guardrail REQUIRES the primitive to:
  - surface benchmark-family caveats explicitly (RFR vs IBOR per
    ADR 0013); AND
  - NOT auto-collapse mixed RFR/IBOR curves into a pack-average —
    refuse and surface the choice.

The snapshot honours both rules by carrying the per-leg
``short_rate_regime_a`` and ``short_rate_regime_b`` labels (NOT a
single collapsed label) on every response. Mixed-regime pairs are
EXPLICITLY supported (a SOFR_FUT vs EUR_SHORT_RATE_FUT spread is a
real desk object) — the regime mismatch is surfaced as METHODOLOGY
DISCLOSURE so a consumer cannot mistake the spread for a like-for-
like measure. The methodology card calls out mixed-regime pairs in
plain English.

Why a bespoke ``time_series`` row shape (plus canonical TimeSeries)
------------------------------------------------------------------
The bespoke ``{date, spread_value_pct, z_score}`` row shape mirrors
the sovereign cross_market_spread tool's bespoke ``time_series``
(frontend-friendly per-row pair carrying both the value AND its
rolling z). The spread value itself is single-unit (PERCENT POINTS
= PERCENT) so we ALSO emit canonical ``TimeSeriesUnits.PERCENT`` /
``TimeSeriesUnits.Z_SCORE`` series next to the bespoke list — the
closed-enum exemption used by the same-curve calendar_spread
sibling (two units per row) does NOT apply here, just as the
butterfly_simple sibling already broke that pattern.
``time_series_spread`` and ``time_series_zscore`` are the operator-
compatible canonical exports; ``time_series`` is the wire-frozen
bespoke shape PR15 expects. All three share the same display rows
so they cannot drift.

Field-name discipline on the snapshot (PR14 frozen)
---------------------------------------------------
- ``spread_value_pct`` — desk-recognised cross-market spread in
  PERCENT POINTS (NOT bps — the policy-futures domain stays in
  PERCENT POINTS on implied-rate-derived objects, matching the
  ``implied_rate_pct`` / ``spread_implied_rate_pct`` /
  ``butterfly_value_pct`` siblings; the sovereign
  cross_market_spread tool's ``*_bps`` convention is a *100
  conversion that does NOT apply here).
- ``daily_change_spread_value_pct`` — one-trading-day raw
  subtraction in PERCENT POINTS.
- ``z_score_spread`` — rolling 252-trading-day z-score of the
  SPREAD series.
- ``high_252d_spread_value_pct`` / ``low_252d_spread_value_pct`` /
  ``mid_252d_spread_value_pct`` — trailing 252-day range on the
  spread series.
- ``percentile_252d`` — percentile rank of the current spread within
  the trailing 252-day range (0-100).
- ``implied_rate_pct_a`` / ``implied_rate_pct_b`` — per-leg current
  implied rates in PERCENT (PR14-frozen ``implied_rate_pct`` suffix
  per the sibling futures_price_level tool).
- ``contract_code_a`` / ``contract_code_b`` (master stems, e.g.
  ``SFR1`` / ``SFI1``) and ``underlying_contract_code_a`` /
  ``underlying_contract_code_b`` (current-front per leg) disclosed
  side-by-side per leg so the reader knows BOTH the strip slot AND
  the underlying contract each slot resolves to today on each
  market.
- ``inverse_priced_a`` / ``inverse_priced_b`` + ``short_rate_regime_a``
  / ``short_rate_regime_b`` carry the regime disclosure inline so
  the implied-rate conversion is transparent on the wire AND the
  per-leg regime labels are preserved for mixed-regime pairs (P5 +
  catalog guardrail — no hidden methodology choice, no silent
  pack-average collapse).

Why ``curve_family_a`` + ``curve_family_b`` + ``strip_position`` are
the input keying axes
--------------------------------------------------------------------
Per the catalog (build_order 18) and ADR 0013, policy futures are
keyed by ``(curve_family, strip_position)``. The user-facing knob
for a cross-market spread is "which TWO markets, which strip slot?"
— SFR1 vs SFI1 (SOFR / SONIA front), SFR4 vs ER4 (matched whites
backs), etc. — not the master stems. ``contract_code`` is the
DISAMBIGUATOR inside the strip resolution; exposing it as an LLM
input would be input-schema overreach (PR8) and would silently
duplicate the strip-position knob. The schema carries
``curve_family_a``, ``curve_family_b``, and ``strip_position`` and
surfaces the resolved ``contract_code_a`` / ``contract_code_b`` /
``underlying_contract_code_*`` on the OUTPUT for disclosure. Same
input-keying-axis discipline the sibling ``futures_price_level`` /
``volume_open_interest_snapshot`` / ``futures_calendar_spread`` /
``futures_butterfly_simple`` use.

Validation layering
-------------------
- ``as_of_date`` defaults to ``None`` — the sentinel that means
  "anchor at the universe's last observed trade_date where BOTH
  legs have a value". When supplied AND beyond the data max on
  EITHER leg, the compute layer returns the documented controlled-
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
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from shared.schemas import TimeSeries


class FuturesCrossMarketSpreadInput(BaseModel):
    """Parameters the LLM extracts to query a single policy-futures
    matched-strip cross-market spread.

    Keyed by ``(curve_family_a, curve_family_b, strip_position)`` per
    ADR 0013. The sign convention is "A minus B"; the validator
    enforces ``curve_family_a != curve_family_b`` so the spread is a
    real desk object (a self-spread is mathematically zero).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    curve_family_a: str = Field(
        ...,
        min_length=1,
        description=(
            "First (numerator / 'A') policy-futures curve family. The "
            "spread is computed as ``rate_A - rate_B`` with A and B "
            "echoed back on the methodology card. Valid V1 values: "
            "'SOFR_FUT' (US Fed SOFR strip, RFR regime), "
            "'EUR_SHORT_RATE_FUT' (ECB Euribor strip, IBOR regime), "
            "'SONIA_FUT' (BOE SONIA strip, RFR regime). Do NOT pass "
            "bond-futures curve families (UST_FUT / DE_FUT / UK_FUT / "
            "JP_FUT / ...) — those route to the bond_futures agent."
        ),
    )
    curve_family_b: str = Field(
        ...,
        min_length=1,
        description=(
            "Second (denominator / 'B') policy-futures curve family. "
            "Must differ from ``curve_family_a`` (enforced by validator). "
            "Same closed enum as ``curve_family_a`` above."
        ),
    )
    strip_position: int = Field(
        ...,
        ge=1,
        le=12,
        description=(
            "1-based strip position on BOTH legs (matched-strip read). "
            "1 = front contract on each market; higher numbers = "
            "quarterly forwards down each strip. The cross-market "
            "spread is a SAME-strip-position differential across two "
            "different curves; same strip position on each market "
            "ensures the two legs are at comparable expiry horizons "
            "(modulo each market's own contract calendar)."
        ),
    )
    lookback_days: int = Field(
        default=365,
        ge=30,
        le=7300,
        description=(
            "Calendar days of history fetched from the DB and used to "
            "scope the observation_count cutoff in the output. Does "
            "NOT control the rolling z-score window (fixed by config "
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
            "anchor — same pattern as the sibling "
            "futures_calendar_spread / futures_butterfly_simple). When "
            "supplied AND BEYOND the universe's last observed "
            "``trade_date`` for EITHER leg, the monitor returns the "
            "documented controlled-error envelope (``{\"error\": \"no "
            "scoreable strip: ...\"}``). When supplied AND within the "
            "universe range, the fetch is anchored at this date so the "
            "snapshot is DETERMINISTIC across runs (same as_of_date + "
            "same DB state ⇒ same numbers)."
        ),
    )
    field_name: Optional[str] = Field(
        default=None,
        description=(
            "Bloomberg observation field mnemonic. When None (default), "
            "the tool falls through to ``default_price_field`` from "
            "config.yaml (currently 'PX_LAST'). Pass an explicit field "
            "name to override per query. LLM/HTTP wrappers MUST "
            "translate their wire-level sentinel (empty string for MCP, "
            "missing param for FastAPI) to None before constructing "
            "this input — otherwise the YAML default is silently "
            "shadowed (same wrapper-shadowing pattern fixed for "
            "sovereign curve_move_classifier in commit b2605ee)."
        ),
    )

    @model_validator(mode="after")
    def _curves_must_differ(self) -> "FuturesCrossMarketSpreadInput":
        # Code-level invariant (PR9 / PR10 — invariants in code, not
        # YAML). Mirrors the sovereign cross_market_spread tool's
        # ``_curves_must_differ`` validator.
        if self.curve_family_a == self.curve_family_b:
            raise ValueError(
                "curve_family_a and curve_family_b must be different, "
                f"but both are '{self.curve_family_a}'. For same-curve "
                "calendar spreads on the policy-futures strip, use the "
                "``futures_calendar_spread`` tool instead."
            )
        return self


class FuturesCrossMarketSpreadTimeSeriesRow(BaseModel):
    """One observation in the bespoke cross-market-spread history.

    Mirrors the sovereign cross_market_spread tool's per-row ``{date,
    spread_*, z_score}`` shape; carries the spread value plus its
    rolling z-score side-by-side. Both quantities are rounded with
    the same conventions the snapshot uses so the snapshot equals
    ``time_series[-1]`` STRICTLY at the latest row."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    date: str = Field(..., description="Trade date in YYYY-MM-DD form.")
    spread_value_pct: float = Field(
        ...,
        description=(
            "Cross-market spread value in PERCENT POINTS at the trade "
            "date: ``implied_rate_pct(curve_family_a) - "
            "implied_rate_pct(curve_family_b)``. Rounded with "
            "``spread_value_round_decimals`` from config.yaml."
        ),
    )
    z_score: Optional[float] = Field(
        None,
        description=(
            "Rolling z-score of the spread at this trade date "
            "(``None`` during the warmup window before "
            "``z_score_min_periods`` observations accumulate). Rounded "
            "with ``z_score_round_decimals`` from config.yaml."
        ),
    )


class FuturesCrossMarketSpreadCurrentMetrics(BaseModel):
    """Snapshot of the latest aligned cross-market-spread bar.

    Field naming is unit-honest, orientation-honest, and regime-aware:

    - ``spread_value_pct`` carries the desk-recognised RAW cross-
      market differential in PERCENT POINTS (NOT bps; NOT basis-
      adjusted; NOT beta-adjusted).
    - ``daily_change_spread_value_pct`` is a raw subtraction (NOT
      *100).
    - ``high_252d_spread_value_pct`` / ``low_252d_spread_value_pct`` /
      ``mid_252d_spread_value_pct`` embed the trailing-range window
      length (wire-frozen at 252 in V1).
    - ``inverse_priced_a`` / ``inverse_priced_b`` carry the per-leg
      metadata-driven inverse-pricing flags side-by-side; the two
      legs are read INDEPENDENTLY (same-strip-position cross-market
      pairs in the V1 universe happen to agree on the flag but the
      schema preserves both).
    - ``short_rate_regime_a`` / ``short_rate_regime_b`` carry the
      per-leg regime labels (RFR vs IBOR) — the methodology
      disclosure surface for the catalog's mixed-regime guardrail.
      Both labels are surfaced even when they differ (NO pack-
      average collapse).
    - Per-leg disclosure block carries both the strip-slot master
      stem (stable across rolls) and the current-front underlying
      contract per leg (rotates at roll), plus the per-leg expiry +
      security name from the SCD2 ``instrument_metadata_history``
      row whose effective window contains ``as_of_date``.
    - Per-leg ``implied_rate_pct_a`` / ``implied_rate_pct_b`` carries
      the latest implied rate on each leg in PERCENT (PR14-frozen
      suffix).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    as_of_date: str = Field(
        ...,
        description=(
            "Most recent trade date on which BOTH legs have a value "
            "(YYYY-MM-DD). The snapshot is anchored to the "
            "intersection of the two markets' trading days so the row "
            "cannot pair a fresh A-leg quote with a stale B-leg quote "
            "(or vice versa) — load-bearing here because the two "
            "markets' trading calendars differ (US vs UK vs Euro)."
        ),
    )
    curve_family_a: str = Field(
        ..., description="Policy-futures curve family A (numerator).",
    )
    curve_family_b: str = Field(
        ..., description="Policy-futures curve family B (denominator).",
    )
    strip_position: int = Field(
        ...,
        ge=1,
        description="1-based strip position on BOTH legs.",
    )
    spread_label: str = Field(
        ...,
        description=(
            "Human-readable label for the cross-market pair at the "
            "matched strip slot, e.g. ``'SFR1-SFI1'`` (SOFR_FUT vs "
            "SONIA_FUT strip 1) or ``'SFR4-ER4'`` (SOFR_FUT vs "
            "EUR_SHORT_RATE_FUT strip 4). Derived from the two legs' "
            "resolved ``contract_code`` stems."
        ),
    )
    contract_code_a: str = Field(
        ...,
        description=(
            "Strip-slot master stem of leg A (e.g. ``'SFR1'``). "
            "Stable across rolls — see ``underlying_contract_code_a`` "
            "for the current front."
        ),
    )
    contract_code_b: str = Field(
        ...,
        description=(
            "Strip-slot master stem of leg B (e.g. ``'SFI1'``)."
        ),
    )
    underlying_contract_code_a: Optional[str] = Field(
        None,
        description=(
            "Current-front underlying contract leg A resolves to AS "
            "OF ``as_of_date`` (e.g. ``'SFRM26'``)."
        ),
    )
    underlying_contract_code_b: Optional[str] = Field(
        None,
        description=(
            "Current-front underlying contract leg B resolves to AS "
            "OF ``as_of_date`` (e.g. ``'SFIM26'``)."
        ),
    )
    security_name_a: Optional[str] = Field(
        None,
        description=(
            "Latest-effective ``SECURITY_DES`` for leg A's current-"
            "front contract."
        ),
    )
    security_name_b: Optional[str] = Field(
        None,
        description=(
            "Latest-effective ``SECURITY_DES`` for leg B's current-"
            "front contract."
        ),
    )
    expiry_date_a: Optional[str] = Field(
        None,
        description=(
            "``LAST_TRADEABLE_DT`` for leg A's current-front "
            "contract. YYYY-MM-DD."
        ),
    )
    expiry_date_b: Optional[str] = Field(
        None,
        description=(
            "``LAST_TRADEABLE_DT`` for leg B's current-front "
            "contract. YYYY-MM-DD."
        ),
    )
    inverse_priced_a: bool = Field(
        ...,
        description=(
            "Inverse-pricing flag for leg A (read from "
            "``instrument_master.attributes->>'inverse_pricing'``). "
            "When ``True`` (SFR / ER / SFI in V1), "
            "``implied_rate_pct = 100 - raw_price`` on this leg; "
            "drives the conversion off METADATA, not a hardcoded "
            "list in compute.py (PR8 / P6 — no hidden methodology "
            "choice in code)."
        ),
    )
    inverse_priced_b: bool = Field(
        ...,
        description=(
            "Inverse-pricing flag for leg B (read INDEPENDENTLY from "
            "``instrument_master.attributes->>'inverse_pricing'``). "
            "Same metadata-driven rule as ``inverse_priced_a``; the "
            "two legs are read independently so a future direct-"
            "priced family integrates without code changes."
        ),
    )
    short_rate_regime_a: str = Field(
        ...,
        description=(
            "Methodology disclosure label for leg A's underlying "
            "short-rate regime: ``'RFR'`` (compounded risk-free "
            "rate — SOFR / SONIA) or ``'IBOR'`` (unsecured term IBOR "
            "rate — Euribor). Resolved from the YAML's "
            "``short_rate_regime_map`` convention against "
            "``curve_family_a``. Carried verbatim on the methodology "
            "disclosure string per P5 + ADR 0013 + the catalog's "
            "mixed-regime guardrail (BOTH per-leg labels are "
            "surfaced even when they differ — NO pack-average "
            "collapse)."
        ),
    )
    short_rate_regime_b: str = Field(
        ...,
        description=(
            "Methodology disclosure label for leg B's underlying "
            "short-rate regime. Resolved from the YAML against "
            "``curve_family_b``. Carried verbatim alongside "
            "``short_rate_regime_a`` so a mixed-regime pair (e.g. "
            "SOFR_FUT vs EUR_SHORT_RATE_FUT — RFR vs IBOR) is "
            "transparent on the wire."
        ),
    )
    implied_rate_pct_a: float = Field(
        ...,
        description=(
            "Latest implied rate on leg A in PERCENT. PR14-frozen "
            "unit suffix (``_pct``); do NOT rename to ``_bps`` or "
            "``_rate``."
        ),
    )
    implied_rate_pct_b: float = Field(
        ...,
        description=(
            "Latest implied rate on leg B in PERCENT."
        ),
    )
    spread_value_pct: float = Field(
        ...,
        description=(
            "Latest aligned RAW cross-market implied-rate "
            "differential in PERCENT POINTS: "
            "``implied_rate_pct(curve_family_a) - "
            "implied_rate_pct(curve_family_b)``. Wire-frozen "
            "orientation: swapping the inputs flips the sign by "
            "construction. NOT basis-adjusted; NOT beta-adjusted."
        ),
    )
    daily_change_spread_value_pct: Optional[float] = Field(
        None,
        description=(
            "1-trading-day change in ``spread_value_pct`` (raw "
            "subtraction in PERCENT POINTS; NOT multiplied by 100)."
        ),
    )
    z_score_spread: Optional[float] = Field(
        None,
        description=(
            "Rolling 252-trading-day z-score of the SPREAD series."
        ),
    )
    high_252d_spread_value_pct: Optional[float] = Field(
        None,
        description=(
            "Highest spread value over the trailing 252 trading "
            "days, in PERCENT POINTS."
        ),
    )
    low_252d_spread_value_pct: Optional[float] = Field(
        None,
        description=(
            "Lowest spread value over the trailing 252 trading "
            "days, in PERCENT POINTS."
        ),
    )
    mid_252d_spread_value_pct: Optional[float] = Field(
        None,
        description=(
            "Midpoint of the trailing 252-day spread range "
            "(``(high + low) / 2``), in PERCENT POINTS."
        ),
    )
    percentile_252d: Optional[float] = Field(
        None,
        description=(
            "Percentile rank of ``spread_value_pct`` within the "
            "trailing 252-day spread range (0-100)."
        ),
    )
    rolling_window_days: int = Field(
        ...,
        description=(
            "The trading-day window used for the z-score (mirrors "
            "the field name the sibling tools use for the same "
            "disclosure)."
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


class FuturesCrossMarketSpreadOutput(BaseModel):
    """Top-level response for the policy_futures
    futures_cross_market_spread tool.

    Wire shape:
      - ``current_metrics``: snapshot of the latest aligned cross-
        market spread bar + per-leg disclosure block + the two
        per-leg implied rates + the per-leg regime labels.
      - ``time_series``: bespoke per-row ``{date, spread_value_pct,
        z_score}`` list (mirrors the sovereign cross_market_spread
        tool's frontend-friendly shape).
      - ``time_series_spread``: canonical TimeSeries of the spread
        series in PERCENT POINTS (closed-enum
        ``TimeSeriesUnits.PERCENT``). Values match
        ``time_series[i].spread_value_pct`` 1-to-1 by construction.
      - ``time_series_zscore``: canonical TimeSeries of the rolling
        z-score (closed-enum ``TimeSeriesUnits.Z_SCORE``).
      - ``methodology_disclosure``: the P5 / ADR 0013 caveat carried
        on every response. Includes the wire-frozen A − B sign
        convention with specific A and B labels echoed, the per-leg
        short-rate regime labels (RFR vs IBOR) — explicitly calling
        out mixed-regime pairs, the per-leg inverse-pricing rule, the
        z-score lookback window, the trailing-range window, the
        matched-strip-position keying, the catalog guardrail
        ("RAW differential — NOT basis-adjusted, NOT beta-adjusted;
        those are planned_extension territory per PR11"), AND the
        explicit refusal of pack-average collapse on mixed-regime
        pairs.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    current_metrics: FuturesCrossMarketSpreadCurrentMetrics
    time_series: List[FuturesCrossMarketSpreadTimeSeriesRow] = Field(
        default_factory=list,
        description=(
            "Historical spread + z-score observations over the "
            "display window (cleaned, intersected, rounded). Bespoke "
            "row shape so the frontend chart pulls both the value and "
            "the z-score from one row; the canonical TimeSeries "
            "fields below carry the same series for operator-layer "
            "consumption."
        ),
    )
    time_series_spread: TimeSeries = Field(
        ...,
        description=(
            "Historical cross-market spread series in PERCENT POINTS. "
            "Closed-enum ``TimeSeriesUnits.PERCENT``; series_name = "
            "``'<curve_family_a_lower>_<curve_family_b_lower>_"
            "<strip_position>_spread'``. Values match "
            "``time_series[i].spread_value_pct`` 1-to-1 by "
            "construction."
        ),
    )
    time_series_zscore: TimeSeries = Field(
        ...,
        description=(
            "Historical rolling z-score of the cross-market spread "
            "vs its own trailing window. Closed-enum "
            "``TimeSeriesUnits.Z_SCORE``; series_name = "
            "``'<curve_family_a_lower>_<curve_family_b_lower>_"
            "<strip_position>_zscore'``. Values match "
            "``time_series[i].z_score`` 1-to-1 (``None`` for rows in "
            "the rolling-window warmup)."
        ),
    )
    methodology_disclosure: str = Field(
        ...,
        min_length=1,
        description=(
            "P5 / ADR 0013 caveat carried on every response. "
            "Includes the wire-frozen A − B sign convention with the "
            "specific A and B labels echoed, the per-leg short-rate "
            "regime labels (RFR vs IBOR) and explicit mixed-regime "
            "labelling, the per-leg inverse-pricing rule, the z-score "
            "lookback window, the trailing-range window, the matched-"
            "strip-position keying, the catalog guardrail (RAW "
            "differential — NOT basis-adjusted, NOT beta-adjusted; "
            "those are planned_extension territory per PR11), AND "
            "the explicit refusal of pack-average collapse on "
            "mixed-regime pairs. Required so consumers cannot drop "
            "the disclosure when relaying the snapshot."
        ),
    )


__all__ = [
    "FuturesCrossMarketSpreadInput",
    "FuturesCrossMarketSpreadTimeSeriesRow",
    "FuturesCrossMarketSpreadCurrentMetrics",
    "FuturesCrossMarketSpreadOutput",
]
