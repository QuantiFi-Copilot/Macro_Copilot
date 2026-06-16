"""Pydantic schemas for the futures_price_level tool.

V1 monitor for the ``bond_futures`` domain (ADR 0013) — single rolling-
generic bond-futures price level + 252d range / z-score for one
``(curve_family, contract_code)`` pair (TY1 on UST_FUT 10Y, RX1 on
DE_FUT 10Y, ...).

Why a bespoke ``time_series`` row shape (not canonical TimeSeries)
-----------------------------------------------------------------
Bond-futures rolling-generics quote in PRICE space — TY1 in ``points``,
RX1 in ``% of par value``, YM1 / XM1 in ``100 - yield``, JB1 in
``points``, G1 in ``GBP``. ``shared.schemas.time_series.TimeSeriesUnits``
is a closed-enum family (P8) and has no ``PRICE`` member; declaring
``PERCENT`` or ``BPS`` on a bond-futures price series would silently
lie. Extending ``TimeSeriesUnits`` is ADR-gated per
``docs_revamped/03_standards/closed_family_discipline.md`` §6, and
ADR 0013 (which sanctions this primitive) did NOT authorise that
extension. The bespoke per-row ``{date, price}`` list remains the
frontend wire shape, with the per-contract ``quote_units`` carried
on the snapshot so downstream consumers cannot misread.

ADR 0017 authorises the ``TimeSeriesUnits.PRICE`` member ("a quoted
price in the contract's NATIVE quote space; ``quote_units`` is the
authoritative disclosure of the exact space"), so this Output NOW
ALSO carries a canonical companion ``time_series_price: TimeSeries``
built from the SAME display slice as the bespoke rows (1-to-1 by
construction) — that is what the open-DAG Series bridge lifts as a
typed leaf (``output_field='time_series_price'``).

Field-name discipline on the snapshot
-------------------------------------
- ``current_price`` (NOT ``current_yield_pct``) — bond-futures
  monitors live in price space, NOT yield space. The methodology
  card states the rolling-generic-price caveat verbatim (ADR 0013
  + P5). The naming makes the unit unambiguous.

- ``daily_change_price`` / ``weekly_change_price`` /
  ``monthly_change_price`` (NOT ``..._bps``) — bond-futures price
  changes are reported in price units (same as ``quote_units``),
  NOT bps. Calling them ``daily_change_bps`` would multiply by 100
  and lie about the unit.

- ``high_252d_price`` / ``low_252d_price`` / ``percentile_252d`` —
  the trailing-range fields embed the trailing window length in the
  field name (wire-frozen at 252 per the same pattern as
  yield_levels / ois rate_level).

- ``quote_units`` / ``contract_size`` / ``expiry_date`` /
  ``security_name`` are surfaced on the snapshot so the consumer
  can disclose the contract context without a second tool call.
  ``expiry_date`` + ``security_name`` come from the latest-effective
  row of the SCD2 rolling-contract metadata history — they rotate
  as the front rolls.

Validation layering
-------------------
- ``field_name`` defaults to ``None`` — the sentinel that means "use
  the YAML's ``default_price_field`` convention". MCP / HTTP
  wrappers translate their wire-level empty-string sentinel to None.

- Cross-field invariants stay in code (Pydantic validators), not
  in YAML.

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


class FuturesPriceLevelInput(BaseModel):
    """Parameters the LLM extracts to query a single rolling-generic
    bond-futures price level."""

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
            "control the rolling z-score window (fixed by config "
            "convention z_score_window_days, currently 252) or the "
            "trailing range window (fixed by trailing_range_window_days, "
            "locked at 252 in V1)."
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
    as_of_date: Optional[date] = Field(
        default=None,
        description=(
            "Optional as-of date (YYYY-MM-DD): compute as of this trade "
            "date instead of the latest available data.  None → latest "
            "(live snapshot).  Supply a date for a historical, replayable view."
        ),
    )


class FuturesPriceLevelTimeSeriesRow(BaseModel):
    """One observation in the bespoke price history.

    Bespoke shape (NOT ``shared.schemas.time_series.TimeSeriesRow``)
    because the row's ``price`` is in the contract's ``quote_units``
    rather than a closed-enum unit; per-contract ``quote_units`` is
    carried on the snapshot so a consumer reading a row knows what
    the price means. See module docstring."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    date: str = Field(..., description="Trade date in YYYY-MM-DD form.")
    price: float = Field(
        ...,
        description=(
            "Observation price in the contract's native quote_units "
            "(carried on the snapshot — e.g. 'points' for TY1, "
            "'% of par value' for RX1, '100 - yield' for YM1)."
        ),
    )


class FuturesPriceLevelCurrentMetrics(BaseModel):
    """Snapshot of the latest front-month bond-futures observation.

    Field naming is unit-honest:

    - ``current_price`` / ``..._change_price`` carry the contract's
      native price units (NOT bps; NOT a yield in percent).
    - ``high_252d_price`` / ``low_252d_price`` embed the trailing
      window length (wire-frozen at 252 in V1; see
      ``config.yaml``'s ``planned_extensions``).
    - ``quote_units`` / ``contract_size`` / ``expiry_date`` /
      ``security_name`` carry the per-contract disclosure so the
      reader cannot mistake price space for yield space.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    as_of_date: str = Field(..., description="Most recent trade date (YYYY-MM-DD).")
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
    quote_units: Optional[str] = Field(
        None,
        description=(
            "Bloomberg ``QUOTE_UNITS`` for this rolling-generic — e.g. "
            "'points' (TY1 / UXY1 / US1 / WN1 / TU1 / FV1 / JB1 / CN1), "
            "'% of par value' (RX1 / UB1 / DU1 / OE1 / OAT1 / BTS1 / IK1 "
            "/ KOA1), '100 - yield' (YM1 / XM1), 'GBP' (G1). "
            "Lives on ``instrument_master.attributes`` JSONB."
        ),
    )
    contract_size: Optional[float] = Field(
        None,
        description=(
            "Bloomberg ``FUT_CONT_SIZE`` for this rolling-generic — "
            "notional per contract in the curve's home currency. Lives "
            "on ``instrument_master.attributes`` JSONB."
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
    current_price: float = Field(
        ...,
        description=(
            "Latest cleaned, ffilled price in ``quote_units``. NOT a "
            "yield. Rounded with ``price_round_decimals`` from "
            "config.yaml."
        ),
    )
    daily_change_price: Optional[float] = Field(
        None,
        description=(
            "1-trading-day change in ``quote_units`` (raw subtraction; "
            "NOT multiplied by 100)."
        ),
    )
    weekly_change_price: Optional[float] = Field(
        None,
        description=(
            "5-trading-day change in ``quote_units`` (raw subtraction; "
            "NOT multiplied by 100)."
        ),
    )
    monthly_change_price: Optional[float] = Field(
        None,
        description=(
            "22-trading-day change in ``quote_units`` (raw subtraction; "
            "NOT multiplied by 100)."
        ),
    )
    z_score: Optional[float] = Field(
        None,
        description="Rolling 252-trading-day z-score of the price level.",
    )
    high_252d_price: Optional[float] = Field(
        None,
        description=(
            "Highest price over the trailing 252 trading days, in "
            "``quote_units``."
        ),
    )
    low_252d_price: Optional[float] = Field(
        None,
        description=(
            "Lowest price over the trailing 252 trading days, in "
            "``quote_units``."
        ),
    )
    percentile_252d: Optional[float] = Field(
        None,
        description=(
            "Percentile rank of ``current_price`` within trailing 252-"
            "day range (0-100)."
        ),
    )
    observation_count: int = Field(
        ...,
        description=(
            "Number of trading days within the ``lookback_days`` window."
        ),
    )


class FuturesPriceLevelOutput(BaseModel):
    """Top-level response for the futures_price_level tool.

    Wire shape:
      - ``current_metrics``: snapshot of the latest observation +
        per-contract disclosure block (quote_units, contract_size,
        expiry_date, security_name).
      - ``time_series``: bespoke list of ``{date, price}`` rows over
        the displayed window. Rounded with the SAME
        ``price_round_decimals`` convention the snapshot uses, so
        ``current_metrics.current_price`` equals
        ``time_series[-1].price`` STRICTLY at the latest row.
      - ``time_series_price``: canonical TimeSeries companion of the
        SAME price history (``TimeSeriesUnits.PRICE`` per ADR 0017) —
        the open-DAG Series bridge's typed leaf. Values match
        ``time_series[i].price`` 1-to-1 by construction.
      - ``methodology_disclosure``: the P5 / ADR 0013 caveat verbatim
        so every consumer carries the rolling-generic-price reading
        forward.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    current_metrics: FuturesPriceLevelCurrentMetrics
    time_series: List[FuturesPriceLevelTimeSeriesRow] = Field(
        default_factory=list,
        description=(
            "Historical front-month prices for the rolling-generic over "
            "the display window (cleaned, ffilled, rounded to "
            "``price_round_decimals``). Bespoke shape kept for the "
            "frontend wire; the canonical companion for substrate "
            "consumption is ``time_series_price``. See module docstring."
        ),
    )
    time_series_price: TimeSeries = Field(
        ...,
        description=(
            "Canonical TimeSeries of the rolling-generic price history "
            "in the contract's NATIVE quote space (closed-enum "
            "``TimeSeriesUnits.PRICE`` per ADR 0017; read "
            "``current_metrics.quote_units`` for the exact space). "
            "series_name = ``'<curve_family_lower>_<contract_code_lower>"
            "_price'``. Values match ``time_series[i].price`` 1-to-1 by "
            "construction."
        ),
    )
    methodology_disclosure: str = Field(
        ...,
        min_length=1,
        description=(
            "P5 / ADR 0013 caveat carried on every response: 'this is "
            "the rolling-generic price; the CTD-implied yield is not "
            "yet a primitive in this build'. Required so consumers "
            "cannot drop the disclosure when relaying the snapshot."
        ),
    )


__all__ = [
    "FuturesPriceLevelInput",
    "FuturesPriceLevelTimeSeriesRow",
    "FuturesPriceLevelCurrentMetrics",
    "FuturesPriceLevelOutput",
]
