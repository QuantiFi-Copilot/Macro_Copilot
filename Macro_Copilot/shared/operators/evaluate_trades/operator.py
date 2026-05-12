"""evaluate_trades — TradeSet + price Panel → P&L Panel.

Phase 1 PR 12.

Pure function: for each Trade in the TradeSet, walk the holding
window date by date and compute per-leg P&L contributions
(``leg.weight * (price[t] - price[entry])``).  Aggregate to a
single ``trade_<i>_pnl`` column per trade.  Output Panel rows are
dates in the union of holding windows intersected with the price
Panel's calendar.

V1 disclosures (frictionless):
  - mid-price only
  - no transaction costs, no slippage, no bid/ask
  - no financing assumption (P&L is pure price-change)

The brief documents this scope: "frictionless V1 with methodology
disclosure."  Disclosure is structural — V1 raises
``NotImplementedError`` if the caller tries to override any of the
three knobs above, and the workspace methodology card reads the
values from the persisted lineage step so a reader knows what V1
omits.

Pure-function discipline (the brief's "operators producing
``TradeSet`` / ``PositionPath`` are still pure functions over the
inputs"): the temporal walk lives INSIDE this compute; it is NOT
visible to the operator's input → output relation.  Upstream
operators see deterministic, idempotent semantics.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from shared.artifacts.lineage import Lineage, OperatorStep
from shared.artifacts.missingness import RawNoCleaning
from shared.artifacts.trades import LegSpec, Trade, TradeSet
from shared.artifacts.types import Panel
from shared.artifacts.units import TimeSeriesUnits
from shared.config.operator_config import (
    OperatorConfig,
    OperatorConfigError,
    load_operator_config,
)
from shared.operators.evaluate_trades.schemas import (
    EvaluateTradesParams,
    FinancingAssumption,
    PricingConvention,
)


_OPERATOR_NAME = "evaluate_trades"
_OPERATOR_VERSION = "1.0.0"

_CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"

# P&L column-naming convention: ``trade_<idx>_pnl`` where idx is
# the zero-padded 0-based index of the trade in the input TradeSet.
# 6-digit pad supports up to 1M trades while keeping the column
# names lex-sortable; a 1M-trade TradeSet exceeds the inline-vs-
# blob threshold by orders of magnitude anyway.
_TRADE_COL_FMT = "trade_{:06d}_pnl"


class EvaluateTradesError(ValueError):
    """Raised on a recoverable user-facing failure."""


def evaluate_trades(
    trades: TradeSet,
    price_panel: Panel,
    params: Optional[EvaluateTradesParams] = None,
    config: Optional[OperatorConfig] = None,
) -> Panel:
    """Compute per-trade P&L over each trade's holding window.

    Parameters
    ----------
    trades :
        ``TradeSet`` produced by ``construct_trades`` (or any other
        operator that emits the closed-family ``TradeSet`` shape).
    price_panel :
        ``Panel`` whose columns are instrument keys (matching
        ``LegSpec.instrument_key``) and whose rows are dates.  The
        Panel's calendar bounds what we can price — trades whose
        holding window extends beyond the panel's last date are
        truncated to that last date.
    params :
        Optional ``EvaluateTradesParams``.  All fields default to
        ``None`` and resolve from ``config.yaml`` at runtime.  V1
        enforces frictionless + mid + no-financing; the operator
        raises ``NotImplementedError`` for any other resolved
        combination.
    config :
        Optional ``OperatorConfig``.  When omitted, the bundled
        ``config.yaml`` is loaded (process-cached).

    Returns
    -------
    Panel
        Wide P&L panel with one column per trade
        (``trade_000000_pnl``, ``trade_000001_pnl``, ...) and rows
        spanning the union of holding windows intersected with the
        price panel's calendar.  Pre-entry and post-exit cells are
        NaN.  ``units_by_column`` repeats the asset-unit tag for
        every trade column (V1 picks the first leg's units; the
        decomposition refinements in ``planned_extensions`` will
        introduce per-component units).
    """
    if params is None:
        params = EvaluateTradesParams()
    if config is None:
        config = _load_default_config()

    pricing = _resolve_pricing_convention(params, config)
    frictionless = _resolve_frictionless(params, config)
    financing = _resolve_financing(params, config)

    # ------------------------------------------------------------------
    # V1 scope guards — these surface the structural disclosures.
    # ------------------------------------------------------------------
    if pricing != "mid":
        raise NotImplementedError(
            f"evaluate_trades V1 supports only the 'mid' pricing "
            f"convention; got {pricing!r}.  See config.yaml "
            "planned_extensions for bid/ask + slippage."
        )
    if not frictionless:
        raise NotImplementedError(
            "evaluate_trades V1 is hard-coded frictionless; "
            "frictionless=False raises until transaction-cost / "
            "borrow-availability models land.  See config.yaml "
            "planned_extensions."
        )
    if financing != "none":
        raise NotImplementedError(
            f"evaluate_trades V1 supports only "
            f"financing_assumption='none'; got {financing!r}.  The "
            "financing_rate primitive lands in Phase 1 PR 13."
        )

    # ------------------------------------------------------------------
    # Validate price_panel coverage of every leg.
    # ------------------------------------------------------------------
    required_keys = sorted({
        leg.instrument_key
        for t in trades.trades
        for leg in t.leg_specs
    })
    panel_columns = set(price_panel.payload.columns)
    missing = [k for k in required_keys if k not in panel_columns]
    if missing:
        raise EvaluateTradesError(
            f"price_panel is missing columns for instrument keys "
            f"referenced by the TradeSet: {missing}.  Caller must "
            "align the panel's columns to every leg's instrument_key."
        )

    # ------------------------------------------------------------------
    # Build the output panel.
    # ------------------------------------------------------------------
    if trades.n_trades == 0:
        # Empty TradeSet → empty-but-typed Panel.  Carries the
        # operator lineage step so the empty-case methodology
        # card still surfaces the disclosures.
        empty_df = pd.DataFrame(
            index=pd.DatetimeIndex([], name=None),
        )
        return _build_panel(
            df=empty_df,
            units_tag=_first_leg_units_tag(trades),
            trades=trades,
            price_panel=price_panel,
            pricing=pricing,
            frictionless=frictionless,
            financing=financing,
        )

    panel_index: pd.DatetimeIndex = price_panel.payload.index
    if not isinstance(panel_index, pd.DatetimeIndex):
        # The Panel model_validator already enforces this — defensive
        # repeat so the failure surfaces here with our error type.
        raise EvaluateTradesError(
            "price_panel must have a DatetimeIndex."
        )

    # The full union of holding windows clamped to the panel's calendar.
    windows = [
        _trade_window(t, panel_index) for t in trades.trades
    ]
    union_dates = sorted(
        set().union(*(set(w) for w in windows)) if windows else set()
    )
    if not union_dates:
        empty_df = pd.DataFrame(
            index=pd.DatetimeIndex([], name=None),
        )
        return _build_panel(
            df=empty_df,
            units_tag=_first_leg_units_tag(trades),
            trades=trades,
            price_panel=price_panel,
            pricing=pricing,
            frictionless=frictionless,
            financing=financing,
        )

    output_index = pd.DatetimeIndex(union_dates)
    columns = [_TRADE_COL_FMT.format(i) for i in range(trades.n_trades)]
    df = pd.DataFrame(
        np.nan, index=output_index, columns=columns, dtype=float,
    )

    for i, trade in enumerate(trades.trades):
        col = columns[i]
        df[col] = _compute_trade_pnl(
            trade=trade,
            price_panel=price_panel,
            output_index=output_index,
        )

    return _build_panel(
        df=df,
        units_tag=_first_leg_units_tag(trades),
        trades=trades,
        price_panel=price_panel,
        pricing=pricing,
        frictionless=frictionless,
        financing=financing,
    )


# ============================================================================
# Internals
# ============================================================================


def _compute_trade_pnl(
    *,
    trade: Trade,
    price_panel: Panel,
    output_index: pd.DatetimeIndex,
) -> pd.Series:
    """Per-date P&L for a single trade.  NaN outside the holding
    window.

    Logic:
      - For each leg, P&L[t] = leg.weight * (price[t] - price[entry])
      - Total trade P&L[t] = sum over legs

    Edge cases:
      - Entry date not in the panel → trade contributes NaN
        everywhere (a hard error would be too strict — a bad entry
        date should surface as a NaN column the caller can detect).
      - Price at entry is NaN → trade contributes NaN everywhere
        for the same reason.
      - Exit date past the panel's end → P&L is computed up to the
        panel's last date and NaN afterwards.
    """
    panel = price_panel.payload
    panel_dates = panel.index

    # Clamp entry/exit to the panel calendar.
    entry_ts = pd.Timestamp(trade.entry_date)
    exit_ts = pd.Timestamp(trade.exit_date)

    if entry_ts not in panel_dates:
        # Could not establish baseline → trade contributes NaN.
        return pd.Series(np.nan, index=output_index, dtype=float)

    # The slice of the panel relevant to this trade.
    in_window = (
        (panel_dates >= entry_ts) & (panel_dates <= exit_ts)
    )
    window_dates = panel_dates[in_window]

    trade_pnl = pd.Series(0.0, index=window_dates, dtype=float)
    any_nan_entry = False
    for leg in trade.leg_specs:
        entry_price = panel.loc[entry_ts, leg.instrument_key]
        if pd.isna(entry_price):
            any_nan_entry = True
            break
        leg_pnl = leg.weight * (
            panel.loc[window_dates, leg.instrument_key] - entry_price
        )
        # Replace any NaN observation inside the window with 0 for
        # this leg's contribution so a missing day on one leg
        # doesn't wipe the entire trade's P&L; the methodology
        # card mentions this fill-on-window-NaN choice.  An
        # entry-time NaN is a different beast (no baseline) — we
        # NaN the whole trade in that case (any_nan_entry above).
        trade_pnl = trade_pnl + leg_pnl.fillna(0.0)

    if any_nan_entry:
        return pd.Series(np.nan, index=output_index, dtype=float)

    # Align back to the output index (NaN outside the window).
    return trade_pnl.reindex(output_index)


def _trade_window(
    trade: Trade, panel_index: pd.DatetimeIndex,
) -> pd.DatetimeIndex:
    """Dates in the panel that fall inside [entry_date, exit_date]."""
    mask = (
        (panel_index >= pd.Timestamp(trade.entry_date))
        & (panel_index <= pd.Timestamp(trade.exit_date))
    )
    return panel_index[mask]


def _first_leg_units_tag(trades: TradeSet) -> str:
    """Return the units tag for the first leg of the first trade,
    or a fallback when the TradeSet has no trades.  V1 assumes
    every leg shares the asset's native unit family — the
    decomposition refinements in ``planned_extensions`` introduce
    per-component units.

    Returns a value from ``TimeSeriesUnits`` so the output Panel's
    ``units_by_column`` typechecks."""
    if trades.n_trades == 0:
        return TimeSeriesUnits.BPS.value
    first_leg = trades.trades[0].leg_specs[0]
    if first_leg.units:
        # Validate against the closed-family unit set; fall back to
        # bps on a non-recognised tag.
        try:
            return TimeSeriesUnits(first_leg.units).value
        except ValueError:
            pass
    return TimeSeriesUnits.BPS.value


def _build_panel(
    *,
    df: pd.DataFrame,
    units_tag: str,
    trades: TradeSet,
    price_panel: Panel,
    pricing: str,
    frictionless: bool,
    financing: str,
) -> Panel:
    """Wrap the computed DataFrame in a Panel with full lineage.

    The lineage step records both the TradeSet's head hash and the
    price Panel's head hash as input_hashes so the resulting Panel
    is content-addressed by both upstream inputs.  Two calls with
    the same trades + same price panel + same resolved knobs
    produce the same Panel head hash.
    """
    units_by_column = {col: TimeSeriesUnits(units_tag) for col in df.columns}

    step_params: Dict[str, Any] = {
        "pricing_convention": pricing,
        "frictionless": frictionless,
        "financing_assumption": financing,
        "n_trades": trades.n_trades,
        "source_event_key": trades.source_event_key,
        "methodology_policy": trades.methodology_policy,
        "panel_columns": list(df.columns),
    }

    op_step = OperatorStep.build(
        name=_OPERATOR_NAME,
        version=_OPERATOR_VERSION,
        params=step_params,
        input_hashes=(trades.lineage.head_hash, price_panel.lineage.head_hash),
        auxiliary_lineages=(price_panel.lineage,),
    )
    lineage = trades.lineage.append(op_step)

    return Panel(
        payload=df,
        units_by_column=units_by_column,
        missingness_policy=RawNoCleaning(),
        lineage=lineage,
    )


# ============================================================================
# Config resolution
# ============================================================================


def _load_default_config() -> OperatorConfig:
    try:
        return load_operator_config(_CONFIG_PATH)
    except OperatorConfigError as exc:  # pragma: no cover — defensive
        raise EvaluateTradesError(
            f"Could not load evaluate_trades config.yaml: {exc}"
        ) from exc


def _resolve_pricing_convention(
    params: EvaluateTradesParams, config: OperatorConfig,
) -> PricingConvention:
    if params.pricing_convention is not None:
        return params.pricing_convention
    return config.default_value("pricing_convention")  # type: ignore[return-value]


def _resolve_frictionless(
    params: EvaluateTradesParams, config: OperatorConfig,
) -> bool:
    if params.frictionless is not None:
        return params.frictionless
    return bool(config.default_value("frictionless"))


def _resolve_financing(
    params: EvaluateTradesParams, config: OperatorConfig,
) -> FinancingAssumption:
    if params.financing_assumption is not None:
        return params.financing_assumption
    return config.default_value("financing_assumption")  # type: ignore[return-value]


__all__ = ["evaluate_trades", "EvaluateTradesError"]
