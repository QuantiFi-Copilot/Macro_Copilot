"""shared.artifacts.trades — TradeSet artifact for the backtest archetype.

Phase 1 PR 12.

A ``TradeSet`` is a closed-family typed artifact carrying an ordered
list of ``Trade`` records.  Each ``Trade`` describes one instance of
the strategy entering and (eventually) exiting positions; the
``leg_specs`` field captures the instrument-level composition (one or
more ``LegSpec`` rows) at entry time.

What this is, what this is NOT
------------------------------
- IS: a *pure data* description of "trades that fired".  No P&L, no
  pricing.  ``evaluate_trades`` consumes a ``TradeSet`` plus a price
  panel and emits a P&L ``Panel``; ``summarize_trades`` consumes the
  P&L panel and emits scalar metrics.

- IS NOT: a "live positions" view.  Positions over the holding window
  (the date-indexed position state) are a Phase 1 V2 concern
  (``PositionPath``).  V1 derives positions inside
  ``evaluate_trades``'s compute from the trade's entry/exit dates +
  leg weights — they are not surfaced as a separate artifact yet.

Closed-family discipline
------------------------
Adding ``TradeSet`` to the closed-family discriminator is the
Phase 1 PR 12 closed-family extension.  The existing five families
(Series / SeriesSet / EventSet / Panel / WindowedPanel) retain their
exact previous shapes — no field added, no field removed.

The Pydantic discriminator union in ``state/schemas.py`` and the
serialization registry in ``state/artifact_store.py`` both get the
new entry.  PR 2's pinned hash test (lineage step hash recipe) is
unchanged because ``TradeSet`` does NOT participate in
``PrimitiveStep``'s identity bits — its lineage chain hashes via
``OperatorStep`` and ``_compute_step_hash`` exactly like every other
artifact.

V1 scope
--------
- Long-short or single-leg trades only.
- Fixed-horizon holding rule only (signal_exit / stop_loss are
  declared but raise ``NotImplementedError`` in the operator that
  produces TradeSets).
- ``methodology_ref`` is an optional registry pointer
  (``methodology_versions.id``); not part of the artifact hash —
  metadata, like ``PrimitiveStep.methodology_version_id``.

The closed-family invariant is: artifact identity is content.  Two
``TradeSet``s with the same trades + same leg specs + same lineage
chain produce the same hash regardless of registry-id metadata.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Tuple

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator

from shared.artifacts.lineage import Lineage

# ============================================================================
# Leg spec — one row of an entry-side instrument composition
# ============================================================================


class LegSpec(BaseModel):
    """One leg of a trade — an instrument with a directed weight.

    ``instrument_key`` follows the substrate's series-key
    convention used elsewhere (e.g. ``UST.10Y.yield_mid``).  V1
    treats this as an opaque string; ``evaluate_trades`` looks it
    up in the supplied price/yield Panel by column name.

    ``weight`` is signed (positive = long, negative = short).
    Phase 1 V1 normalises weights at the leg level; portfolio-
    level constraints (gross / net caps, beta adjustment) are
    Phase 2 concerns.

    ``side`` is a redundant honesty tag: ``"long"`` when
    ``weight >= 0``, ``"short"`` otherwise.  A model validator
    keeps the two consistent so accidental sign flips at the
    caller surface loudly rather than silently producing a
    short trade labelled long.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    instrument_key: str = Field(..., min_length=1)
    weight: float
    side: Literal["long", "short"]
    units: Optional[str] = Field(
        default=None,
        description=(
            "Optional instrument-units tag (e.g. ``percent`` for yields, "
            "``bps`` for spreads).  Pure metadata; not used by the "
            "evaluation operator."
        ),
    )

    @model_validator(mode="after")
    def _check_side_matches_weight(self) -> "LegSpec":
        if self.weight >= 0 and self.side != "long":
            raise ValueError(
                f"LegSpec weight={self.weight} >= 0 but side={self.side!r}; "
                "weight sign and side must agree (positive = long)."
            )
        if self.weight < 0 and self.side != "short":
            raise ValueError(
                f"LegSpec weight={self.weight} < 0 but side={self.side!r}; "
                "weight sign and side must agree (negative = short)."
            )
        return self


# ============================================================================
# Trade — one entry-exit cycle of the strategy
# ============================================================================


class Trade(BaseModel):
    """A single trade record.

    ``entry_date`` is the ``pd.Timestamp`` at which the strategy
    enters its leg positions; ``exit_date`` is the ``pd.Timestamp``
    at which the positions close out.  For V1 (fixed_horizon
    holding rule), exit_date = entry_date + holding_window
    business days.  The construction operator is responsible for
    setting exit_date consistently with its declared holding rule.

    ``leg_specs`` carries the instrument composition at entry.
    Trades may have a single leg (directional) or multiple legs
    (spreads, butterflies).  Empty leg lists are rejected.

    ``methodology_ref`` is an optional pointer into
    ``copilot_state.methodology_versions`` recording which
    methodology YAML produced this trade record.  NOT part of the
    artifact hash — registry pointer, not identity content.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        arbitrary_types_allowed=True,
    )

    entry_date: pd.Timestamp
    exit_date: pd.Timestamp
    leg_specs: Tuple[LegSpec, ...]
    methodology_ref: Optional[int] = None

    @model_validator(mode="after")
    def _validate_trade(self) -> "Trade":
        if not self.leg_specs:
            raise ValueError("Trade must have at least one leg.")
        if self.exit_date < self.entry_date:
            raise ValueError(
                f"Trade exit_date ({self.exit_date}) must be >= entry_date "
                f"({self.entry_date})."
            )
        # Reject duplicate instrument_keys within a single trade —
        # otherwise net position calculation is ambiguous (two long
        # legs on the same instrument should be expressed as one leg
        # with combined weight).
        keys = [leg.instrument_key for leg in self.leg_specs]
        if len(set(keys)) != len(keys):
            raise ValueError(
                f"Trade leg_specs contains duplicate instrument_keys: "
                f"{keys}.  Combine duplicate legs at the caller."
            )
        return self

    @property
    def n_legs(self) -> int:
        return len(self.leg_specs)

    @property
    def holding_days(self) -> int:
        """Calendar-days between entry and exit.  Holding-window
        in business days lives in the constructing operator's
        params; this is a structural read."""
        return int((self.exit_date - self.entry_date).days)


# ============================================================================
# TradeSet artifact
# ============================================================================


class TradeSet(BaseModel):
    """An ordered list of trades + provenance.

    Phase 1 PR 12.  Closed-family member alongside Series, SeriesSet,
    EventSet, Panel, WindowedPanel.

    ``trades`` is ordered by ``entry_date``; the model validator
    enforces this so a downstream operator can assume monotone
    entries (events arrive in chronological order).  Ties in
    ``entry_date`` are allowed (two trades fire on the same day
    from different signals); the secondary sort is undefined.

    ``source_event_key`` is the (optional) ``EventSet.source_series_key``
    of the events that triggered these trades — surfaced for human
    debugging.  Not load-bearing for replay (the lineage chain is).

    ``methodology_policy`` is a short tag describing the methodology
    family the trades were constructed under (e.g.
    ``fixed_horizon_v1``).  Pure metadata; identity comes from the
    lineage chain.

    Hash discipline
    ---------------
    TradeSet's content-identity is the same as every other
    artifact: ``lineage.head_hash``.  The trade records themselves
    do not participate in the hash directly — the operator that
    produced them folds their structural identity into the
    ``OperatorStep.params`` dict via ``_compute_step_hash``.  Two
    TradeSets produced by the same operator with the same params
    against the same upstream EventSet share a head hash; the
    storage layer's idempotency gate short-circuits a re-put.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        arbitrary_types_allowed=True,
    )

    trades: Tuple[Trade, ...]
    source_event_key: Optional[str] = Field(
        default=None,
        description=(
            "Optional ``EventSet.source_series_key`` of the events that "
            "triggered these trades.  Metadata-only — surfaced on the "
            "methodology card for traceability."
        ),
    )
    methodology_policy: str = Field(
        ...,
        min_length=1,
        description=(
            "Short tag describing the construction methodology, e.g. "
            "``fixed_horizon_v1``.  The construction operator sets this; "
            "it surfaces on the methodology card."
        ),
    )
    lineage: Lineage

    @model_validator(mode="after")
    def _validate_trade_set(self) -> "TradeSet":
        if not self.trades:
            # Empty TradeSets are allowed — a strategy that fires
            # no events legitimately produces an empty trade list.
            # The downstream summarise step handles the empty case
            # explicitly (returns NaN metrics with a notes entry).
            return self
        # Enforce monotone entry_date ordering.
        for i in range(1, len(self.trades)):
            if self.trades[i].entry_date < self.trades[i - 1].entry_date:
                raise ValueError(
                    f"TradeSet trades must be ordered by entry_date "
                    f"(non-decreasing); trade {i} entry "
                    f"({self.trades[i].entry_date}) < trade {i - 1} entry "
                    f"({self.trades[i - 1].entry_date})."
                )
        return self

    @property
    def n_trades(self) -> int:
        return len(self.trades)

    @property
    def entry_dates(self) -> List[pd.Timestamp]:
        """Convenience read of the entry timeline.  Use this when
        the caller needs just the entry calendar — the operators
        always walk ``trades`` directly."""
        return [t.entry_date for t in self.trades]

    @property
    def exit_dates(self) -> List[pd.Timestamp]:
        return [t.exit_date for t in self.trades]

    def to_records(self) -> List[Dict[str, Any]]:
        """Flatten the trade list into a list of JSON-safe dicts.

        Used by the serialization layer (``state.artifact_store``)
        and by tests asserting structural shape.  ``pd.Timestamp``
        values become ISO strings; ``LegSpec`` becomes a nested
        dict; everything else is plain Python scalars.
        """
        out: List[Dict[str, Any]] = []
        for t in self.trades:
            out.append({
                "entry_date": t.entry_date.isoformat(),
                "exit_date": t.exit_date.isoformat(),
                "methodology_ref": t.methodology_ref,
                "leg_specs": [
                    {
                        "instrument_key": leg.instrument_key,
                        "weight": leg.weight,
                        "side": leg.side,
                        "units": leg.units,
                    }
                    for leg in t.leg_specs
                ],
            })
        return out

    @classmethod
    def from_records(
        cls,
        records: List[Dict[str, Any]],
        *,
        source_event_key: Optional[str],
        methodology_policy: str,
        lineage: Lineage,
    ) -> "TradeSet":
        """Inverse of ``to_records``.  Used by the serialization
        layer to rehydrate a TradeSet from storage.

        Each record is the dict shape produced by ``to_records``;
        the helper rebuilds ``Trade`` + ``LegSpec`` Pydantic
        instances, which re-runs the model validators (catches any
        corruption introduced between put and get).
        """
        trades: List[Trade] = []
        for r in records:
            legs = tuple(
                LegSpec(
                    instrument_key=leg["instrument_key"],
                    weight=float(leg["weight"]),
                    side=leg["side"],
                    units=leg.get("units"),
                )
                for leg in r["leg_specs"]
            )
            trades.append(
                Trade(
                    entry_date=pd.Timestamp(r["entry_date"]),
                    exit_date=pd.Timestamp(r["exit_date"]),
                    leg_specs=legs,
                    methodology_ref=r.get("methodology_ref"),
                )
            )
        return cls(
            trades=tuple(trades),
            source_event_key=source_event_key,
            methodology_policy=methodology_policy,
            lineage=lineage,
        )


__all__ = [
    "LegSpec",
    "Trade",
    "TradeSet",
]
