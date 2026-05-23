"""Pydantic schemas for the policy_futures futures_price_level tool.

V1 monitor for the ``policy_futures`` domain (ADR 0011) — single
strip-position price level + implied-rate level + 252d range /
z-score for one ``(curve_family, strip_position)`` pair (SFR1 on
SOFR_FUT, ER2 on EUR_SHORT_RATE_FUT, SFI1 on SONIA_FUT, ...).

Why a bespoke ``time_series`` row shape (not canonical TimeSeries)
-----------------------------------------------------------------
Each row in the historical series carries TWO unit spaces — the raw
futures price (no clean ``TimeSeriesUnits`` member; SFR / ER / SFI
quote ``100 - rate``) AND the implied rate in PERCENT. Emitting one
canonical TimeSeries forces a single unit declaration; emitting two
side-by-side TimeSeries inflates the wire without adding any
read-once-trace-back-to-snapshot guarantee. The bespoke
``{date, raw_price, implied_rate_pct}`` shape keeps both reads
honest, mirrors the sibling bond_futures ``futures_price_level``
choice, and is the same exempt pattern documented at
``shared/workflow/validate.py:368`` (``output_field_units={}``).
Extending ``TimeSeriesUnits`` is ADR-gated per
``docs_revamped/03_standards/closed_family_discipline.md`` §6, and
ADR 0011 (which sanctions this primitive) does NOT authorise that
extension. The per-strip ``inverse_priced`` flag + the regime label
are carried on the snapshot so a downstream consumer cannot misread
a raw price as a rate.

Field-name discipline on the snapshot (PR14 frozen)
---------------------------------------------------
- ``raw_price`` — latest cleaned, ffilled price in the contract's
  native quote space (for SFR / ER / SFI: ``100 - rate``).
- ``implied_rate_pct`` — desk-recognised implied rate in PERCENT.
  Catalog v2.1 PR14 freezes this name; the sibling policy_futures
  primitives (futures_calendar_spread, futures_butterfly_simple,
  futures_pack_average_simple, futures_strip_snapshot,
  scan_policy_futures_extremes) all reference it. Do NOT rename to
  ``implied_rate`` / ``implied_rate_percent`` / ``rate_pct``.
- ``daily_change_raw_price`` / ``daily_change_implied_rate_pct`` —
  one-trading-day raw subtraction in each unit space (NOT *100;
  the implied-rate delta is in PERCENT POINTS, not bps).
- ``high_252d_implied_rate_pct`` / ``low_252d_implied_rate_pct`` /
  ``mid_252d_implied_rate_pct`` — trailing 252-day range on the
  implied-rate series (the desk-recognised view).
- ``high_252d_raw_price`` / ``low_252d_raw_price`` /
  ``mid_252d_raw_price`` — trailing 252-day range on the raw-price
  series (carried in parallel so consumers can map back to the
  underlying futures price).
- ``percentile_252d`` — percentile rank of the current implied rate
  within the trailing 252-day implied-rate range (0-100). One number
  because the rank is identical in raw-price and implied-rate space
  for monotone inverse_pricing (the rank just inverts; the
  PERCENTILE on the rate is what desks read, so it's reported on the
  rate axis).
- ``z_score_implied_rate`` — rolling 252-trading-day z-score of the
  implied-rate level (NOT the raw-price level — z scoring inverse-
  priced raw prices flips the sign of every "extreme" reading
  relative to the rate; desks read the rate, so the z lives on the
  rate axis).
- ``contract_code`` (master stem, e.g. SFR1) and
  ``underlying_contract_code`` (current-front, e.g. SFRM26)
  disclosed side-by-side so the reader knows BOTH the strip slot
  AND the underlying contract the slot resolves to today.
- ``inverse_priced`` + ``short_rate_regime`` + ``quote_units``
  disclosed on every snapshot so the implied-rate conversion is
  transparent on the wire (P5 — no hidden methodology choice).

Why ``strip_position`` is the input keying axis (not ``contract_code``)
----------------------------------------------------------------------
Per the catalog (build_order 14) and ADR 0011, policy futures are
keyed by ``(curve_family, strip_position)``. The user-facing knob
is "which slot on the strip?" — SFR1 (front) vs SFR2 (front+1) —
not the master stem ``contract_code`` (which is just the strip
slot's playbook label, e.g. ``'SFR1'``). ``contract_code`` is the
DISAMBIGUATOR inside the strip resolution; exposing it as an LLM
input would be input-schema overreach (PR8) and would silently
duplicate the strip_position knob. The schema carries
``strip_position`` as a positive integer and surfaces the resolved
``contract_code`` on the OUTPUT for disclosure.

Validation layering
-------------------
- ``field_name`` defaults to ``None`` — the sentinel that means "use
  the YAML's ``default_price_field`` convention". MCP / HTTP
  wrappers translate their wire-level empty-string sentinel to None
  before constructing this input (same shadowing fix the
  curve_move_classifier migration pinned in commit b2605ee).

- ``as_of_date`` defaults to ``None`` — the sentinel that means
  "anchor at the universe's last observed trade_date for this strip
  position". When supplied AND beyond the data max, the compute
  layer returns the documented controlled-error envelope per P6 /
  PR8 / PR16 honest disclosure (no silent re-labelling of an
  unbounded read as a future-anchored read).

- ``frozen=True`` + ``extra="forbid"`` per typed-boundary discipline
  (``docs_revamped/03_standards/typed_boundary_discipline.md`` §1).
  None of the four models on this primitive carry pandas / numpy
  payloads, so ``arbitrary_types_allowed`` is intentionally
  omitted.
"""

from __future__ import annotations

from datetime import date
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class FuturesPriceLevelInput(BaseModel):
    """Parameters the LLM extracts to query a single policy-futures
    strip-position price + implied-rate level.

    Keyed by ``(curve_family, strip_position)`` per ADR 0011 — NOT
    ``contract_code``. See module docstring for the input-schema
    rationale.
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
    strip_position: int = Field(
        ...,
        ge=1,
        le=12,
        description=(
            "1-based strip position. 1 = front contract; higher numbers "
            "= quarterly forwards down the strip. The policy_futures "
            "playbook universe carries strip positions 1..8 today "
            "(whites positions 1-4, reds positions 5-8); the schema "
            "bound is 1..12 for headroom on a future universe "
            "expansion. A request for a strip_position not present in "
            "the universe returns the controlled-error envelope."
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
            "``trade_date`` for the requested ``(curve_family, "
            "strip_position)`` (post-fetch data-max anchor — same "
            "pattern as the sibling bond_futures monitors). When "
            "supplied AND BEYOND the universe's last observed "
            "``trade_date``, the monitor returns the documented "
            "controlled-error envelope (``{\"error\": \"no scoreable "
            "strip: ...\"}``) — the snapshot refuses to silently "
            "re-label an unbounded read as a future-anchored read "
            "(P5 / PR8 honest disclosure). When supplied AND within "
            "the universe range, the fetch is anchored at this date "
            "via the fetcher's ``end_date`` parameter, so the "
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
            "name to override per query. LLM/HTTP wrappers MUST translate "
            "their wire-level sentinel (empty string for MCP, missing "
            "param for FastAPI) to None before constructing this input — "
            "otherwise the YAML default is silently shadowed (same "
            "wrapper-shadowing pattern fixed for sovereign "
            "curve_move_classifier in commit b2605ee)."
        ),
    )


class FuturesPriceLevelTimeSeriesRow(BaseModel):
    """One observation in the bespoke price + implied-rate history.

    Carries both unit spaces side-by-side so a downstream consumer
    reading a row can map either the raw quote or the implied rate
    without re-deriving the conversion. The per-row ``raw_price`` is
    in the contract's native quote space (for SFR / ER / SFI:
    ``100 - rate``); the per-row ``implied_rate_pct`` is in PERCENT.
    Both are rounded with the same conventions the snapshot uses so
    ``current_metrics.raw_price`` equals ``time_series[-1].raw_price``
    and ``current_metrics.implied_rate_pct`` equals
    ``time_series[-1].implied_rate_pct`` STRICTLY at the latest row."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    date: str = Field(..., description="Trade date in YYYY-MM-DD form.")
    raw_price: float = Field(
        ...,
        description=(
            "Observation price in the contract's native quote space "
            "(``100 - rate`` for inverse-priced SFR / ER / SFI). "
            "Rounded with ``price_round_decimals`` from config.yaml."
        ),
    )
    implied_rate_pct: float = Field(
        ...,
        description=(
            "Desk-recognised implied rate in PERCENT, derived from "
            "``raw_price`` per the per-strip ``inverse_priced`` flag "
            "(when inverse: ``implied_rate_pct = 100 - raw_price``; "
            "when direct: ``implied_rate_pct = raw_price``). Rounded "
            "with ``implied_rate_round_decimals`` from config.yaml."
        ),
    )


class FuturesPriceLevelCurrentMetrics(BaseModel):
    """Snapshot of the latest strip-position observation.

    Field naming is unit-honest and regime-aware:

    - ``raw_price`` carries the contract's native quote space (NOT a
      yield in percent).
    - ``implied_rate_pct`` carries the desk-recognised implied rate
      in PERCENT (PR14 frozen name).
    - ``daily_change_raw_price`` / ``daily_change_implied_rate_pct``
      are raw subtractions in each unit space (NOT *100; the implied-
      rate delta is in PERCENT POINTS).
    - ``high_252d_implied_rate_pct`` / ``low_252d_implied_rate_pct``
      / ``mid_252d_implied_rate_pct`` embed the trailing-range window
      length (wire-frozen at 252 in V1).
    - ``high_252d_raw_price`` / ``low_252d_raw_price`` /
      ``mid_252d_raw_price`` carried in parallel on the raw-price
      axis.
    - ``inverse_priced`` / ``short_rate_regime`` / ``quote_units``
      carry the methodology disclosure inline so the implied-rate
      conversion is transparent on the wire.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    as_of_date: str = Field(..., description="Most recent trade date (YYYY-MM-DD).")
    curve_family: str = Field(..., description="Policy-futures curve family.")
    strip_position: int = Field(
        ..., ge=1, description="1-based strip position (1 = front)."
    )
    contract_code: str = Field(
        ...,
        description=(
            "Strip-slot master stem from the playbook universe (e.g. "
            "``'SFR1'``, ``'ER2'``, ``'SFI1'``). Stable across rolls — "
            "see ``underlying_contract_code`` for the current front."
        ),
    )
    underlying_contract_code: Optional[str] = Field(
        None,
        description=(
            "Current-front underlying contract that this strip slot "
            "resolves to AS OF ``as_of_date`` (e.g. ``'SFRM26'`` for "
            "SFR1 on 2026-05-22 — front SOFR is the June 2026 "
            "contract). Read off the SCD2 "
            "``instrument_metadata_history`` row whose effective "
            "window contains ``as_of_date``."
        ),
    )
    security_name: Optional[str] = Field(
        None,
        description=(
            "Latest-effective ``SECURITY_DES`` from the SCD2 rolling-"
            "contract metadata history bounded by ``as_of_date`` — "
            "names the underlying front contract (e.g. ``'SFRM26 "
            "COMB'``)."
        ),
    )
    expiry_date: Optional[str] = Field(
        None,
        description=(
            "Latest-effective ``LAST_TRADEABLE_DT`` (== "
            "``FUT_DLV_DT_LAST`` for STIR cash-settle) from the SCD2 "
            "history bounded by ``as_of_date``. YYYY-MM-DD."
        ),
    )
    contract_size: Optional[float] = Field(
        None,
        description=(
            "``FUT_CONT_SIZE`` for the current-front contract — "
            "notional per contract in the curve's home currency."
        ),
    )
    tick_size: Optional[float] = Field(
        None,
        description=(
            "``FUT_TICK_SIZE`` for the current-front contract — "
            "minimum quote increment in the raw-price quote space."
        ),
    )
    tick_value: Optional[float] = Field(
        None,
        description=(
            "``FUT_TICK_VAL`` for the current-front contract — "
            "monetary value of one tick in the curve's home currency."
        ),
    )
    inverse_priced: bool = Field(
        ...,
        description=(
            "Inverse-pricing flag from ``instrument_master.attributes"
            "->>'inverse_pricing'``. When ``True`` (SFR / ER / SFI), "
            "``implied_rate_pct = 100 - raw_price``. When ``False`` "
            "(direct-priced policy-futures families, if any are added "
            "to the playbook in future), ``implied_rate_pct = "
            "raw_price``. Drives the conversion off METADATA, not a "
            "hardcoded list in compute.py (PR8 / P6 — no hidden "
            "methodology choice in code)."
        ),
    )
    short_rate_regime: str = Field(
        ...,
        description=(
            "Methodology disclosure label for the underlying short-"
            "rate regime: ``'RFR'`` (compounded risk-free rate — "
            "SOFR / SONIA) or ``'IBOR'`` (unsecured term IBOR rate — "
            "Euribor). Resolved from the YAML's "
            "``short_rate_regime_map`` convention against the "
            "requested ``curve_family``. Carried verbatim on the "
            "methodology disclosure string per P5 + ADR 0011."
        ),
    )
    quote_units: str = Field(
        ...,
        description=(
            "Quote-unit label for ``raw_price`` (``'100 - rate'`` for "
            "inverse-priced; ``'rate (%)'`` for direct-priced). "
            "Carried inline so the consumer cannot misread a raw "
            "price as a rate."
        ),
    )
    raw_price: float = Field(
        ...,
        description=(
            "Latest cleaned, ffilled price in the contract's native "
            "quote space. NOT a rate. Rounded with "
            "``price_round_decimals`` from config.yaml."
        ),
    )
    implied_rate_pct: float = Field(
        ...,
        description=(
            "Desk-recognised implied rate in PERCENT, derived from "
            "``raw_price`` per ``inverse_priced``. PR14 frozen name; "
            "do NOT rename to ``implied_rate`` / "
            "``implied_rate_percent`` / ``rate_pct``."
        ),
    )
    daily_change_raw_price: Optional[float] = Field(
        None,
        description=(
            "1-trading-day change in ``raw_price`` (raw subtraction; "
            "NOT multiplied by 100). For inverse-priced strips, the "
            "sign is the OPPOSITE of the implied-rate change."
        ),
    )
    daily_change_implied_rate_pct: Optional[float] = Field(
        None,
        description=(
            "1-trading-day change in ``implied_rate_pct`` (raw "
            "subtraction in PERCENT POINTS; NOT multiplied by 100 to "
            "bps). For inverse-priced strips, this equals "
            "``-daily_change_raw_price`` by construction."
        ),
    )
    z_score_implied_rate: Optional[float] = Field(
        None,
        description=(
            "Rolling 252-trading-day z-score of the IMPLIED-RATE "
            "level. The z lives on the rate axis (not the raw-price "
            "axis) because the desk-recognised level IS the rate; "
            "z-scoring the inverse-priced raw price would flip the "
            "sign of every 'extreme' reading relative to the rate."
        ),
    )
    high_252d_implied_rate_pct: Optional[float] = Field(
        None,
        description=(
            "Highest implied rate over the trailing 252 trading days, "
            "in PERCENT."
        ),
    )
    low_252d_implied_rate_pct: Optional[float] = Field(
        None,
        description=(
            "Lowest implied rate over the trailing 252 trading days, "
            "in PERCENT."
        ),
    )
    mid_252d_implied_rate_pct: Optional[float] = Field(
        None,
        description=(
            "Midpoint of the trailing 252-day implied-rate range "
            "(``(high + low) / 2``), in PERCENT."
        ),
    )
    high_252d_raw_price: Optional[float] = Field(
        None,
        description=(
            "Highest raw price over the trailing 252 trading days. "
            "For inverse-priced strips, equals "
            "``100 - low_252d_implied_rate_pct`` by construction."
        ),
    )
    low_252d_raw_price: Optional[float] = Field(
        None,
        description=(
            "Lowest raw price over the trailing 252 trading days. "
            "For inverse-priced strips, equals "
            "``100 - high_252d_implied_rate_pct`` by construction."
        ),
    )
    mid_252d_raw_price: Optional[float] = Field(
        None,
        description=(
            "Midpoint of the trailing 252-day raw-price range, in "
            "raw quote units."
        ),
    )
    percentile_252d: Optional[float] = Field(
        None,
        description=(
            "Percentile rank of ``implied_rate_pct`` within the "
            "trailing 252-day implied-rate range (0-100). Reported on "
            "the rate axis because that is the desk-recognised view; "
            "for inverse-priced strips, the raw-price percentile is "
            "``100 - percentile_252d`` by construction."
        ),
    )
    observation_count: int = Field(
        ...,
        description=(
            "Number of trading days within the ``lookback_days`` "
            "window."
        ),
    )


class FuturesPriceLevelOutput(BaseModel):
    """Top-level response for the policy_futures futures_price_level
    tool.

    Wire shape:
      - ``current_metrics``: snapshot of the latest observation +
        per-strip disclosure block (contract_code,
        underlying_contract_code, security_name, expiry_date,
        contract_size, tick_size, tick_value, inverse_priced,
        short_rate_regime, quote_units).
      - ``time_series``: bespoke list of ``{date, raw_price,
        implied_rate_pct}`` rows over the displayed window. Both
        per-row fields are rounded with the same conventions the
        snapshot uses, so the snapshot equals ``time_series[-1]``
        STRICTLY at the latest row.
      - ``methodology_disclosure``: the P5 / ADR 0011 caveat carried
        on every response so consumers cannot drop the disclosure
        when relaying the snapshot.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    current_metrics: FuturesPriceLevelCurrentMetrics
    time_series: List[FuturesPriceLevelTimeSeriesRow] = Field(
        default_factory=list,
        description=(
            "Historical raw-price + implied-rate observations for the "
            "strip slot over the display window (cleaned, ffilled, "
            "rounded). Bespoke shape — not ``shared.schemas."
            "TimeSeries`` — because each row carries two unit spaces "
            "side-by-side and ``TimeSeriesUnits`` has no PRICE member "
            "in V1 (ADR-gated extension per P8). See module "
            "docstring."
        ),
    )
    methodology_disclosure: str = Field(
        ...,
        min_length=1,
        description=(
            "P5 / ADR 0011 caveat carried on every response. Includes "
            "the rolling-generic strip-read label, the per-strip "
            "regime label (RFR vs IBOR), the inverse-pricing rule, "
            "the implied-rate computation formula in plain English, "
            "and the z-score lookback window. Required so consumers "
            "cannot drop the disclosure when relaying the snapshot."
        ),
    )


__all__ = [
    "FuturesPriceLevelInput",
    "FuturesPriceLevelTimeSeriesRow",
    "FuturesPriceLevelCurrentMetrics",
    "FuturesPriceLevelOutput",
]
