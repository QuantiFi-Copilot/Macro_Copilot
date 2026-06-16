"""Pydantic schemas for the futures_volume_oi tool.

V1 monitor #2 for the ``bond_futures`` domain (ADR 0013) — daily
volume + open-interest + ΔOI + OI z-score for one
``(curve_family, contract_code)`` rolling-generic pair (TY1 on UST_FUT
10Y, RX1 on DE_FUT 10Y, ...).

Why a bespoke ``time_series`` row shape (not canonical TimeSeries)
-----------------------------------------------------------------
Bond-futures volume + open interest are WHOLE-CONTRACT COUNTS — not
percent, not bps, not a level on a rate curve.
``shared.schemas.time_series.TimeSeriesUnits`` is a closed-enum family
(P8) and has no ``CONTRACTS`` member; declaring ``PERCENT``, ``BPS``,
or ``COUNT`` (which is reserved for discrete enumeration flags like
condition codes) on a futures-volume series would silently lie. The
row also carries TWO semantically distinct series (volume = contracts
traded; open_interest = contracts outstanding); the canonical
``TimeSeries`` payload assumes a single value column. Extending
``TimeSeriesUnits`` is ADR-gated per
``docs_revamped/03_standards/closed_family_discipline.md`` §6, and
ADR 0013 (which sanctions this primitive) did NOT authorise that
extension. The bespoke per-row ``{date, volume, open_interest}`` list
remains the frontend wire shape, with the ``contract_size``
disclosure carried on the snapshot so downstream consumers can convert
to notional if they want it.

ADR 0017 authorises the ``TimeSeriesUnits.CONTRACTS`` member (futures
contract counts — distinct from ``COUNT``'s observation-count / flag
semantics), so this Output NOW ALSO carries canonical single-column
companions ``time_series_volume: TimeSeries`` and
``time_series_open_interest: TimeSeries`` built from the SAME display
slices as the bespoke rows (1-to-1 by construction) — those are what
the open-DAG Series bridge lifts as typed leaves.

Field-name discipline on the snapshot
-------------------------------------
- ``current_volume`` / ``current_open_interest`` (NOT ``..._notional``)
  — bond-futures volume + OI live in contract-count space, NOT
  notional dollars. Multiplying by ``contract_size`` would change
  the unit silently. The methodology disclosure surfaces the per-
  contract size so the consumer can convert if needed.

- ``delta_open_interest_1d`` (NOT ``..._bps``) — ΔOI is a contract-
  count change in the OI LEVEL, NOT a bps quantity. Calling it
  ``delta_oi_bps`` would silently lie about the unit.

- ``oi_high_252d`` / ``oi_low_252d`` / ``oi_percentile_252d`` —
  trailing-range fields embed the 252-trading-day window in the
  field name (wire-frozen at 252 per the same pattern as
  yield_levels / ois rate_level / futures_price_level).

- ``volume_rolling_mean_22d`` / ``volume_rolling_max_22d`` —
  volume short-context fields embed the 22-trading-day window in
  the field name (wire-frozen at 22). The window is INTENTIONALLY
  shorter than the OI window because volume is much more volatile
  intra-month and a 1Y window over-smooths the recent-activity read
  every desk question on volume needs. See ``config.yaml`` for the
  rationale.

- ``contract_size`` / ``expiry_date`` / ``security_name`` are surfaced
  on the snapshot so the consumer can disclose the contract context
  without a second tool call. ``expiry_date`` + ``security_name``
  come from the latest-effective row of the SCD2 rolling-contract
  metadata history — they rotate as the front rolls.

  (``quote_units`` is omitted on this snapshot — volume and OI are
   in contracts regardless of the underlying contract's price-side
   ``quote_units``; surfacing it would invite the reader to multiply
   counts by a price-side label.)

Why no ``field_name`` input
---------------------------
This primitive's concept is specifically tied to PX_VOLUME +
OPEN_INT (the playbook's volume + open-interest mnemonics). Exposing
``field_name`` as an input would be input-schema overreach (PR9 /
OPR8 — methodology knob masquerading as a per-query choice). The
two field mnemonics are owned by the YAML so an ingestion-side
rename can be absorbed by a config edit rather than a code change;
the LLM cannot ask for a non-standard field per query.

Validation layering
-------------------
- Cross-field invariants stay in code (Pydantic validators), not in
  YAML.

- ``frozen=True`` + ``extra="forbid"`` per typed-boundary discipline
  (``docs_revamped/03_standards/typed_boundary_discipline.md`` §1).
  Frozen models are required so the lineage layer can hash a step's
  inputs / outputs reliably (``hash_determinism.md``). None of the
  four models on this primitive carry pandas / numpy payloads, so
  ``arbitrary_types_allowed`` is intentionally omitted — only the
  two universal flags apply.
"""

from __future__ import annotations

from datetime import date
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from shared.schemas import TimeSeries


class FuturesVolumeOIInput(BaseModel):
    """Parameters the LLM extracts to query a single rolling-generic
    bond-futures volume / open-interest snapshot.

    No ``field_name`` input — see module docstring for the input-
    schema-overreach rationale. The YAML's ``default_volume_field``
    and ``default_open_interest_field`` conventions are authoritative.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    curve_family: str = Field(
        ...,
        min_length=1,
        description=(
            "Bond-futures curve family. Examples: 'UST_FUT' (TY1 / UXY1 / "
            "US1 / WN1 / TU1 / FV1), 'DE_FUT' (RX1 / UB1 / DU1 / OE1), "
            "'UK_FUT' (G1), 'JP_FUT' (JB1), 'FR_FUT' (OAT1), 'IT_FUT' "
            "(IK1 / BTS1), 'ES_FUT' (KOA1), 'CA_FUT' (CN1), 'AU_FUT' "
            "(YM1 / XM1). Do NOT pass policy-futures curves "
            "(SOFR_FUT / EUR_SHORT_RATE_FUT / SONIA_FUT) — those route "
            "to the policy_futures agent."
        ),
    )
    contract_code: str = Field(
        ...,
        min_length=1,
        description=(
            "Rolling-generic stem from the bond_futures playbook universe "
            "— the canonical disambiguator per TD#11. Examples: 'TY1', "
            "'UXY1', 'US1', 'WN1', 'TU1', 'FV1', 'RX1', 'UB1', 'DU1', "
            "'OE1', 'G1', 'JB1', 'OAT1', 'IK1', 'BTS1', 'KOA1', 'CN1', "
            "'YM1', 'XM1'. Required because (curve_family, tenor) alone "
            "is ambiguous for several universes (TY1 vs UXY1 both UST_FUT "
            "10Y; US1 vs WN1 both UST_FUT 30Y)."
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
            "trailing range window (oi_trailing_range_window_days, locked "
            "at 252 in V1), or the volume short-context window "
            "(volume_avg_window_days, locked at 22 in V1)."
        ),
    )
    as_of_date: Optional[date] = Field(
        default=None,
        description=(
            "Optional as-of date (YYYY-MM-DD): compute as of this trade "
            "date instead of the latest available data.  None → latest "
            "(live snapshot).  Supply a date for a historical, replayable "
            "view."
        ),
    )


class FuturesVolumeOITimeSeriesRow(BaseModel):
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
            "roll on the rolling-generic series."
        ),
    )


class FuturesVolumeOICurrentMetrics(BaseModel):
    """Snapshot of the latest aligned bond-futures volume + OI bar.

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
    - ``contract_size`` / ``expiry_date`` / ``security_name`` carry
      the per-contract disclosure so the reader can convert counts
      to notional if needed.
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
    curve_family: str = Field(..., description="Bond-futures curve family.")
    contract_code: str = Field(
        ..., description="Rolling-generic stem (TY1 / UXY1 / ...)."
    )
    tenor: str = Field(
        ...,
        description=(
            "Tenor label on the rolling-generic (e.g. '10Y' for TY1)."
        ),
    )
    contract_size: Optional[float] = Field(
        None,
        description=(
            "Bloomberg ``FUT_CONT_SIZE`` for this rolling-generic — "
            "notional per contract in the curve's home currency. Lives "
            "on ``instrument_master.attributes`` JSONB. Multiply by "
            "``current_volume`` / ``current_open_interest`` to obtain "
            "notional traded / outstanding."
        ),
    )
    expiry_date: Optional[str] = Field(
        None,
        description=(
            "Latest-effective ``LAST_TRADEABLE_DT`` from the SCD2 "
            "rolling-contract metadata history (the front contract's "
            "expiry as of today). YYYY-MM-DD."
        ),
    )
    security_name: Optional[str] = Field(
        None,
        description=(
            "Latest-effective ``SECURITY_DES`` from the SCD2 rolling-"
            "contract metadata history — names the underlying front "
            "contract (e.g. 'TYZ6 COMB')."
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
            "headline desk-recognised positioning extreme signal; the "
            "252-day window is disclosed verbatim on the response's "
            "``methodology_disclosure``."
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
            "Rolling 22-trading-day mean of daily volume, in CONTRACTS. "
            "Short window deliberately — see config.yaml rationale."
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
            "Number of aligned trading days within the ``lookback_days`` "
            "window. Counts dates where BOTH volume AND open interest "
            "have a value after cleaning."
        ),
    )


class FuturesVolumeOIOutput(BaseModel):
    """Top-level response for the futures_volume_oi tool.

    Wire shape:
      - ``current_metrics``: snapshot of the latest aligned bar +
        per-contract disclosure block (contract_size, expiry_date,
        security_name).
      - ``time_series``: bespoke list of ``{date, volume,
        open_interest}`` rows over the displayed window, aligned on
        the intersection of the two series. Rounded with the SAME
        ``volume_round_decimals`` / ``oi_round_decimals`` conventions
        the snapshot uses, so ``current_metrics.current_volume`` and
        ``current_metrics.current_open_interest`` equal
        ``time_series[-1].volume`` / ``time_series[-1].open_interest``
        STRICTLY at the latest row.
      - ``methodology_disclosure``: the P5 / ADR 0013 caveat + the
        explicit OI z-score lookback disclosure (per the catalog's
        methodology guardrail). Required so consumers cannot drop the
        disclosure when relaying the snapshot.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    current_metrics: FuturesVolumeOICurrentMetrics
    time_series: List[FuturesVolumeOITimeSeriesRow] = Field(
        default_factory=list,
        description=(
            "Historical aligned daily volume + open-interest rows for "
            "the rolling-generic over the display window (cleaned, "
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
            "series_name = ``'<curve_family_lower>_<contract_code_"
            "lower>_volume'``. Values match ``time_series[i].volume`` "
            "1-to-1 by construction."
        ),
    )
    time_series_open_interest: TimeSeries = Field(
        ...,
        description=(
            "Canonical TimeSeries of end-of-day open interest in "
            "CONTRACTS (closed-enum ``TimeSeriesUnits.CONTRACTS`` per "
            "ADR 0017); series_name = ``'<curve_family_lower>_"
            "<contract_code_lower>_open_interest'``. Values match "
            "``time_series[i].open_interest`` 1-to-1 by construction."
        ),
    )
    methodology_disclosure: str = Field(
        ...,
        min_length=1,
        description=(
            "P5 / ADR 0013 caveat carried on every response, including "
            "the explicit OI z-score lookback window per the catalog's "
            "methodology guardrail. Required so consumers cannot drop "
            "the disclosure when relaying the snapshot."
        ),
    )


__all__ = [
    "FuturesVolumeOIInput",
    "FuturesVolumeOITimeSeriesRow",
    "FuturesVolumeOICurrentMetrics",
    "FuturesVolumeOIOutput",
]
