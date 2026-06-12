"""Pydantic schemas for the policy_futures volume_open_interest_snapshot tool.

V1 monitor #2 for the ``policy_futures`` domain (ADR 0013) — daily
volume + open-interest + ΔOI + OI z-score + percentile-of-range +
22-day rolling volume context for one
``(curve_family, strip_position)`` pair (SFR1 on SOFR_FUT, ER2 on
EUR_SHORT_RATE_FUT, SFI1 on SONIA_FUT, ...).

Why a bespoke ``time_series`` row shape (not canonical TimeSeries)
-----------------------------------------------------------------
Policy-futures volume + open interest are WHOLE-CONTRACT COUNTS —
not percent, not bps, not a level on a rate curve.
``shared.schemas.time_series.TimeSeriesUnits`` is a closed-enum
family (P8) and has no ``CONTRACTS`` member; declaring ``PERCENT``,
``BPS``, or ``COUNT`` (which is reserved for discrete enumeration
flags like condition codes) on a futures-volume series would
silently lie. The row also carries TWO semantically distinct series
(volume = contracts traded; open_interest = contracts outstanding);
the canonical ``TimeSeries`` payload assumes a single value column.
Extending ``TimeSeriesUnits`` is ADR-gated per
``docs_revamped/03_standards/closed_family_discipline.md`` §6, and
ADR 0013 (which sanctions this primitive) did NOT authorise that
extension. The bespoke per-row ``{date, volume, open_interest}``
list remains the frontend wire shape, with the ``contract_size``
disclosure carried on the snapshot so downstream consumers can
convert to notional if they want it. Same pattern the sibling
bond_futures.futures_volume_oi uses.

ADR 0017 authorises the ``TimeSeriesUnits.CONTRACTS`` member
(futures contract counts — distinct from ``COUNT``'s observation-
count / flag semantics), so this Output NOW ALSO carries canonical
single-column companions ``time_series_volume: TimeSeries`` and
``time_series_open_interest: TimeSeries`` built from the SAME
display slices as the bespoke rows (1-to-1 by construction) — those
are what the open-DAG Series bridge lifts as typed leaves.

Field-name discipline on the snapshot
-------------------------------------
- ``current_volume`` / ``current_open_interest`` (NOT ``..._notional``)
  — policy-futures volume + OI live in contract-count space, NOT
  notional dollars. Multiplying by ``contract_size`` would change
  the unit silently. The methodology disclosure surfaces the per-
  contract size so the consumer can convert if needed.

- ``delta_open_interest_1d`` (NOT ``..._bps``) — ΔOI is a contract-
  count change in the OI LEVEL, NOT a bps quantity. Calling it
  ``delta_oi_bps`` would silently lie about the unit.

- ``oi_high_252d`` / ``oi_low_252d`` / ``oi_percentile_252d`` —
  trailing-range fields embed the 252-trading-day window in the
  field name (wire-frozen at 252 per the same pattern as
  futures_price_level / bond_futures futures_volume_oi).

- ``volume_rolling_mean_22d`` / ``volume_rolling_max_22d`` —
  volume short-context fields embed the 22-trading-day window in
  the field name (wire-frozen at 22). The window is INTENTIONALLY
  shorter than the OI window because volume is much more volatile
  intra-month and a 1Y window over-smooths the recent-activity read
  every desk question on volume needs. See ``config.yaml`` for the
  rationale.

- ``contract_code`` (master stem, e.g. SFR1) and
  ``underlying_contract_code`` (current-front, e.g. SFRM26)
  disclosed side-by-side so the reader knows BOTH the strip slot
  AND the underlying contract the slot resolves to today. Same
  SCD2-rotation disclosure pattern the sibling futures_price_level
  uses.

- ``contract_size`` / ``expiry_date`` / ``security_name`` are
  surfaced on the snapshot so the consumer can disclose the contract
  context without a second tool call. These come from the as_of-
  bounded SCD2 ``instrument_metadata_history`` row.

  (``quote_units`` is omitted on this snapshot — volume and OI are
   in contracts regardless of the underlying contract's price-side
   ``quote_units``; surfacing it would invite the reader to multiply
   counts by a price-side label. ``tick_size`` / ``tick_value`` are
   also omitted because they are price-side metadata, not relevant
   to a positioning / flow read.)

Why no ``field_name`` input
---------------------------
This primitive's concept is specifically tied to PX_VOLUME +
OPEN_INT (the playbook's volume + open-interest mnemonics). Exposing
``field_name`` as an input would be input-schema overreach (PR9 /
OPR8 — methodology knob masquerading as a per-query choice). The
two field mnemonics are owned by the YAML so an ingestion-side
rename can be absorbed by a config edit rather than a code change;
the LLM cannot ask for a non-standard field per query. Same
discipline the sibling bond_futures.futures_volume_oi uses.

Why ``strip_position`` is the input keying axis
-----------------------------------------------
Per the catalog (build_order 15) and ADR 0013, policy futures are
keyed by ``(curve_family, strip_position)``. The user-facing knob
is "which slot on the strip?" — SFR1 (front) vs SFR2 (front+1) —
not the master stem ``contract_code``. Same keying axis the sibling
futures_price_level uses.

Validation layering
-------------------
- ``as_of_date`` defaults to ``None`` — the sentinel that means
  "anchor at the universe's last observed trade_date for this strip
  position". When supplied AND beyond the data max, the compute
  layer returns the documented controlled-error envelope per P6 /
  PR8 / PR16 honest disclosure (no silent re-labelling of an
  unbounded read as a future-anchored read). Mirrors the sibling
  futures_price_level pattern.

- ``frozen=True`` + ``extra="forbid"`` per typed-boundary discipline
  (``docs_revamped/03_standards/typed_boundary_discipline.md`` §1).
  None of the four models on this primitive carry pandas / numpy
  payloads, so ``arbitrary_types_allowed`` is intentionally omitted.
"""

from __future__ import annotations

from datetime import date
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from shared.schemas import TimeSeries


class VolumeOpenInterestSnapshotInput(BaseModel):
    """Parameters the LLM extracts to query a single policy-futures
    strip-position volume + open-interest snapshot.

    Keyed by ``(curve_family, strip_position)`` per ADR 0013 — NOT
    ``contract_code``. No ``field_name`` input — the volume / OI
    mnemonics are YAML-owned (see module docstring for the input-
    schema-overreach rationale).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    curve_family: str = Field(
        ...,
        min_length=1,
        description=(
            "Policy-futures curve family. Valid V1 values: "
            "'SOFR_FUT' (US Fed SOFR strip), "
            "'EUR_SHORT_RATE_FUT' (ECB Euribor strip), "
            "'SONIA_FUT' (BOE SONIA strip). Do NOT pass bond-futures "
            "curve families (UST_FUT / DE_FUT / UK_FUT / JP_FUT / ...) "
            "— those route to the bond_futures agent."
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
            "control the OI rolling z-score window (fixed by config "
            "convention oi_z_score_window_days, currently 252), the OI "
            "trailing range window (oi_trailing_range_window_days, "
            "locked at 252 in V1), or the volume short-context window "
            "(volume_avg_window_days, locked at 22 in V1)."
        ),
    )
    as_of_date: Optional[date] = Field(
        default=None,
        description=(
            "As-of date for the snapshot. When omitted (default), the "
            "monitor anchors at the universe's last observed "
            "``trade_date`` for the requested ``(curve_family, "
            "strip_position)`` (post-fetch data-max anchor — same "
            "pattern as the sibling futures_price_level). When supplied "
            "AND BEYOND the universe's last observed ``trade_date`` "
            "(probed against the OPEN_INT series), the monitor returns "
            "the documented controlled-error envelope (``{\"error\": "
            "\"no scoreable strip: ...\"}``) — the snapshot refuses to "
            "silently re-label an unbounded read as a future-anchored "
            "read (P5 / PR8 honest disclosure). When supplied AND "
            "within the universe range, the fetch is anchored at this "
            "date via the fetcher's ``end_date`` parameter, so the "
            "snapshot is DETERMINISTIC across runs (same as_of_date + "
            "same DB state ⇒ same numbers)."
        ),
    )


class VolumeOpenInterestSnapshotTimeSeriesRow(BaseModel):
    """One observation in the bespoke volume + open-interest history.

    Bespoke shape (NOT ``shared.schemas.time_series.TimeSeriesRow``)
    because the row carries TWO contract-count series and the closed-
    enum ``TimeSeriesUnits`` family has no ``CONTRACTS`` member. See
    module docstring."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    date: str = Field(..., description="Trade date in YYYY-MM-DD form.")
    volume: float = Field(
        ...,
        description=(
            "Daily traded volume in CONTRACTS (NOT notional). Whole-"
            "contract count; sub-contract precision is noise."
        ),
    )
    open_interest: float = Field(
        ...,
        description=(
            "End-of-day open interest in CONTRACTS (NOT notional). "
            "Whole-contract count; rotates underlying contracts at "
            "roll on the strip-slot rolling-generic series."
        ),
    )


class VolumeOpenInterestSnapshotCurrentMetrics(BaseModel):
    """Snapshot of the latest aligned policy-futures strip-slot
    volume + OI bar.

    Field naming is unit-honest:

    - ``current_volume`` / ``current_open_interest`` carry contract
      counts (NOT notional; NOT bps; NOT percent).
    - ``delta_open_interest_1d`` is a contract-count change (raw
      subtraction; NOT bps).
    - ``oi_high_252d`` / ``oi_low_252d`` embed the trailing window
      length (wire-frozen at 252 in V1; see ``config.yaml``'s
      ``planned_extensions``).
    - ``volume_rolling_mean_22d`` / ``volume_rolling_max_22d`` embed
      the short-context window length (wire-frozen at 22 in V1).
    - ``contract_code`` / ``underlying_contract_code`` /
      ``security_name`` / ``expiry_date`` / ``contract_size`` carry
      the per-strip + per-front-contract disclosure block (same
      SCD2-rotation pattern as the sibling futures_price_level).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    as_of_date: str = Field(
        ...,
        description=(
            "Most recent trade date on which BOTH volume AND open "
            "interest are observed (YYYY-MM-DD). The snapshot is "
            "anchored to the intersection of the two series so the "
            "snapshot row cannot show a volume reading paired with a "
            "stale OI reading."
        ),
    )
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
            "SFR1). Read off the SCD2 "
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
            "Latest-effective ``LAST_TRADEABLE_DT`` from the SCD2 "
            "history bounded by ``as_of_date``. YYYY-MM-DD."
        ),
    )
    contract_size: Optional[float] = Field(
        None,
        description=(
            "Bloomberg ``FUT_CONT_SIZE`` for the current-front "
            "contract — notional per contract in the curve's home "
            "currency. Multiply by ``current_volume`` / "
            "``current_open_interest`` to obtain notional traded / "
            "outstanding."
        ),
    )
    current_volume: float = Field(
        ...,
        description=(
            "Latest daily traded volume in CONTRACTS. Rounded with "
            "``volume_round_decimals`` from config.yaml."
        ),
    )
    current_open_interest: float = Field(
        ...,
        description=(
            "Latest end-of-day open interest in CONTRACTS. Rounded "
            "with ``oi_round_decimals`` from config.yaml."
        ),
    )
    delta_open_interest_1d: Optional[float] = Field(
        None,
        description=(
            "1-trading-day change in OI level (raw subtraction; NOT "
            "multiplied by 100; NOT a bps quantity). Whole-contract "
            "delta."
        ),
    )
    oi_z_score: Optional[float] = Field(
        None,
        description=(
            "Rolling 252-trading-day z-score of the OI level. The "
            "headline desk-recognised positioning extreme signal; "
            "the 252-day window is disclosed verbatim on the "
            "response's ``methodology_disclosure``."
        ),
    )
    oi_high_252d: Optional[float] = Field(
        None,
        description=(
            "Highest OI level over the trailing 252 trading days, in "
            "CONTRACTS."
        ),
    )
    oi_low_252d: Optional[float] = Field(
        None,
        description=(
            "Lowest OI level over the trailing 252 trading days, in "
            "CONTRACTS."
        ),
    )
    oi_percentile_252d: Optional[float] = Field(
        None,
        description=(
            "Percentile rank of ``current_open_interest`` within the "
            "trailing 252-day range (0-100)."
        ),
    )
    volume_rolling_mean_22d: Optional[float] = Field(
        None,
        description=(
            "Rolling 22-trading-day mean of daily volume, in "
            "CONTRACTS. Short window deliberately — see config.yaml "
            "rationale."
        ),
    )
    volume_rolling_max_22d: Optional[float] = Field(
        None,
        description=(
            "Rolling 22-trading-day maximum of daily volume, in "
            "CONTRACTS. Short window deliberately — see config.yaml "
            "rationale."
        ),
    )
    observation_count: int = Field(
        ...,
        description=(
            "Number of aligned trading days within the "
            "``lookback_days`` window. Counts dates where BOTH volume "
            "AND open interest have a value after cleaning."
        ),
    )


class VolumeOpenInterestSnapshotOutput(BaseModel):
    """Top-level response for the policy_futures
    volume_open_interest_snapshot tool.

    Wire shape:
      - ``current_metrics``: snapshot of the latest aligned bar +
        per-contract disclosure block (contract_code,
        underlying_contract_code, security_name, expiry_date,
        contract_size).
      - ``time_series``: bespoke list of ``{date, volume,
        open_interest}`` rows over the displayed window, aligned on
        the intersection of the two series. Rounded with the SAME
        ``volume_round_decimals`` / ``oi_round_decimals`` conventions
        the snapshot uses, so
        ``current_metrics.current_volume`` and
        ``current_metrics.current_open_interest`` equal
        ``time_series[-1].volume`` / ``time_series[-1].open_interest``
        STRICTLY at the latest row.
      - ``methodology_disclosure``: the P5 / ADR 0013 caveat carried
        on every response so consumers cannot drop the disclosure
        when relaying the snapshot. Includes the explicit OI z-score
        lookback window per the catalog's standardness guardrail.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    current_metrics: VolumeOpenInterestSnapshotCurrentMetrics
    time_series: List[VolumeOpenInterestSnapshotTimeSeriesRow] = Field(
        default_factory=list,
        description=(
            "Historical aligned daily volume + open-interest rows for "
            "the strip slot over the display window (cleaned, "
            "ffilled, intersected on shared dates, rounded). Bespoke "
            "shape — not ``shared.schemas.TimeSeries`` — because each "
            "row carries two semantically distinct count series. The "
            "single-column canonical companions for substrate "
            "consumption are ``time_series_volume`` and "
            "``time_series_open_interest``. See module docstring."
        ),
    )
    time_series_volume: TimeSeries = Field(
        ...,
        description=(
            "Canonical TimeSeries of daily traded volume in CONTRACTS "
            "(closed-enum ``TimeSeriesUnits.CONTRACTS`` per ADR 0017); "
            "series_name = ``'<curve_family_lower>_<strip_position>_"
            "volume'``. Values match ``time_series[i].volume`` 1-to-1 "
            "by construction."
        ),
    )
    time_series_open_interest: TimeSeries = Field(
        ...,
        description=(
            "Canonical TimeSeries of end-of-day open interest in "
            "CONTRACTS (closed-enum ``TimeSeriesUnits.CONTRACTS`` per "
            "ADR 0017); series_name = ``'<curve_family_lower>_"
            "<strip_position>_open_interest'``. Values match "
            "``time_series[i].open_interest`` 1-to-1 by construction."
        ),
    )
    methodology_disclosure: str = Field(
        ...,
        min_length=1,
        description=(
            "P5 / ADR 0013 caveat carried on every response, including "
            "the explicit OI z-score lookback window per the catalog's "
            "standardness guardrail. Required so consumers cannot drop "
            "the disclosure when relaying the snapshot."
        ),
    )


__all__ = [
    "VolumeOpenInterestSnapshotInput",
    "VolumeOpenInterestSnapshotTimeSeriesRow",
    "VolumeOpenInterestSnapshotCurrentMetrics",
    "VolumeOpenInterestSnapshotOutput",
]
