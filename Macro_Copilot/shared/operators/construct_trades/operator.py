"""construct_trades — EventSet → TradeSet (backtest archetype step 1).

Phase 1 PR 12.

Pure function: given an EventSet whose mask marks "entry" dates and
a holding rule + leg specification, emit a TradeSet whose ordered
trades describe entries at the event dates and exits per the
configured holding rule.

V1 scope
--------
- ``fixed_horizon`` holding rule only.  ``signal_exit`` and
  ``stop_loss`` are valid YAML values (declared in the
  ``HoldingRule`` Literal for forward-compat) but the compute
  branch raises ``NotImplementedError`` with a pointer to
  ``planned_extensions`` per the substrate's honesty discipline.

- ``equal_weight_signed`` leg-construction rule only.  Other
  values raise the same way.

- No portfolio-level normalisation — leg weights are passed
  through to the persisted ``LegSpec``s with signs preserved.

Determinism
-----------
The operator is a pure function over its inputs.  Two calls with
the same ``EventSet`` head hash + same params produce the same
``TradeSet`` head hash by the standard ``OperatorStep`` recipe.
The artifact-store idempotency gate then makes the second put a
no-op.

Calendar / business-day handling
--------------------------------
Exit dates are computed via ``pd.tseries.offsets.BDay`` so weekends
are skipped.  Bank holidays are NOT excluded in V1 — the brief
documents this as ``frictionless V1`` so a holiday-falling exit
just lands on what the BDay walker says it should.  Real-calendar
exit dates are scoped to a later PR alongside the financing-curve
work.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from shared.artifacts.lineage import Lineage, OperatorStep
from shared.artifacts.trades import LegSpec, Trade, TradeSet
from shared.artifacts.types import EventSet
from shared.config.operator_config import (
    OperatorConfig,
    OperatorConfigError,
    load_operator_config,
)
from shared.operators.construct_trades.schemas import (
    ConstructTradesParams,
    HoldingRule,
    LegConstructionRule,
    LegSpecInput,
)


_OPERATOR_NAME = "construct_trades"
_OPERATOR_VERSION = "1.0.0"

_CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


class ConstructTradesError(ValueError):
    """Raised on a recoverable user-facing failure (bad params,
    inconsistent inputs).  Subclass of ``ValueError`` so existing
    ``except ValueError`` blocks elsewhere in ``shared/`` continue
    to work."""


def construct_trades(
    events: EventSet,
    params: ConstructTradesParams,
    config: Optional[OperatorConfig] = None,
) -> TradeSet:
    """Convert ``events`` into a ``TradeSet`` per ``params``.

    Parameters
    ----------
    events :
        Input ``EventSet`` artifact.  Each True date in ``mask``
        becomes one entry.
    params :
        ``ConstructTradesParams`` carrying the holding rule,
        holding window, leg list, and leg construction rule.
        Fields whose value is ``None`` are resolved from the
        bundled ``config.yaml`` — same convention as
        ``threshold_events`` (PR 1A).
    config :
        Optional ``OperatorConfig``.  When omitted, the bundled
        ``config.yaml`` is loaded (process-cached).

    Returns
    -------
    TradeSet
        Ordered list of trades with the operator step appended to
        the events' lineage.  ``source_event_key`` carries the
        events' ``source_series_key`` for traceability;
        ``methodology_policy`` is stamped from the resolved
        ``methodology_policy_tag`` so the workspace methodology
        card surfaces what construction policy fired.
    """
    # ------------------------------------------------------------------
    # 1. Resolve config-driven params.
    # ------------------------------------------------------------------
    if config is None:
        config = _load_default_config()

    holding_rule = _resolve_holding_rule(params, config)
    leg_rule = _resolve_leg_construction_rule(params, config)
    methodology_policy = _resolve_methodology_policy(config)

    # ------------------------------------------------------------------
    # 2. V1 scope guards.
    # ------------------------------------------------------------------
    if holding_rule != "fixed_horizon":
        raise NotImplementedError(
            f"construct_trades V1 supports only the 'fixed_horizon' "
            f"holding rule; got {holding_rule!r}.  See "
            "config.yaml methodology.planned_extensions for the "
            "deferred holding-rule families (signal_exit, "
            "stop_loss)."
        )
    if leg_rule != "equal_weight_signed":
        raise NotImplementedError(
            f"construct_trades V1 supports only "
            f"'equal_weight_signed' leg construction; got "
            f"{leg_rule!r}.  See config.yaml planned_extensions "
            "for gross_unity / risk_parity."
        )

    holding_window_days = _resolve_holding_window(params, config)
    if holding_window_days is None or holding_window_days < 1:
        raise ConstructTradesError(
            f"holding_window_days must be >= 1 for the "
            f"fixed_horizon rule; got {holding_window_days!r}."
        )

    # ------------------------------------------------------------------
    # 3. Build the trade list.
    # ------------------------------------------------------------------
    frozen_legs = tuple(_freeze_leg(leg) for leg in params.legs)
    trades: List[Trade] = []
    bday_offset = pd.tseries.offsets.BDay(int(holding_window_days))

    for entry_ts in events.event_dates:
        entry = pd.Timestamp(entry_ts)
        exit_ts = entry + bday_offset
        trades.append(
            Trade(
                entry_date=entry,
                exit_date=exit_ts,
                leg_specs=frozen_legs,
                methodology_ref=None,
            )
        )

    # ------------------------------------------------------------------
    # 4. Build the lineage step + emit the artifact.
    # ------------------------------------------------------------------
    step_params: Dict[str, Any] = {
        "holding_rule": holding_rule,
        "holding_window_days": int(holding_window_days),
        "leg_construction_rule": leg_rule,
        "methodology_policy": methodology_policy,
        "n_trades": len(trades),
        # Folding the leg structural identity into the step params
        # keeps two identical-on-input runs producing the same
        # head hash regardless of caller-side LegSpec identity.
        "legs": [
            {
                "instrument_key": leg.instrument_key,
                "weight": float(leg.weight),
                "side": leg.side,
                "units": leg.units,
            }
            for leg in frozen_legs
        ],
        "source_event_key": events.source_series_key,
    }

    op_step = OperatorStep.build(
        name=_OPERATOR_NAME,
        version=_OPERATOR_VERSION,
        params=step_params,
        input_hashes=(events.lineage.head_hash,),
    )
    lineage = events.lineage.append(op_step)

    return TradeSet(
        trades=tuple(trades),
        source_event_key=events.source_series_key,
        methodology_policy=methodology_policy,
        lineage=lineage,
    )


# ============================================================================
# Helpers
# ============================================================================


def _load_default_config() -> OperatorConfig:
    try:
        return load_operator_config(_CONFIG_PATH)
    except OperatorConfigError as exc:  # pragma: no cover — guard
        raise ConstructTradesError(
            f"Could not load construct_trades config.yaml: {exc}"
        ) from exc


def _resolve_holding_rule(
    params: ConstructTradesParams, config: OperatorConfig,
) -> HoldingRule:
    if params.holding_rule is not None:
        return params.holding_rule
    return config.default_value("holding_rule")  # type: ignore[return-value]


def _resolve_leg_construction_rule(
    params: ConstructTradesParams, config: OperatorConfig,
) -> LegConstructionRule:
    if params.leg_construction_rule is not None:
        return params.leg_construction_rule
    return config.default_value("leg_construction_rule")  # type: ignore[return-value]


def _resolve_holding_window(
    params: ConstructTradesParams, config: OperatorConfig,
) -> int:
    if params.holding_window_days is not None:
        return int(params.holding_window_days)
    return int(config.default_value("holding_window_days"))


def _resolve_methodology_policy(config: OperatorConfig) -> str:
    return str(config.default_value("methodology_policy_tag"))


def _freeze_leg(leg: LegSpecInput) -> LegSpec:
    """Convert the user-input leg shape into the frozen
    artifact-side ``LegSpec``.  Derives ``side`` from the sign
    of ``weight`` so the persisted leg is self-describing."""
    side = "long" if leg.weight >= 0 else "short"
    return LegSpec(
        instrument_key=leg.instrument_key,
        weight=float(leg.weight),
        side=side,
        units=leg.units,
    )


__all__ = ["construct_trades", "ConstructTradesError"]
