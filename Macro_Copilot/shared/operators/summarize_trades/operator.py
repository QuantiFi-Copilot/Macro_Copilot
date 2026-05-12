"""summarize_trades — P&L Panel → summary Panel.

Phase 1 PR 12.  Terminal operator of the backtest archetype's chain.

Takes the per-trade P&L Panel from ``evaluate_trades`` and emits a
single-row Panel keyed by summary metrics.  Pure function.

V1 metric set
-------------
  - ``hit_rate``               fraction of trades with realised P&L >= 0
  - ``mean_pnl``               arithmetic mean of realised P&L
  - ``sharpe_annualized``      annualised Sharpe (uses upstream TradeSet's
                               holding_window, read off the lineage)
  - ``max_drawdown``           max drawdown of cumulative realised P&L
                               (negative number; 0 if no drawdown)
  - ``p10_pnl`` / ``p50_pnl`` / ``p90_pnl``  distribution percentiles

For an empty TradeSet (zero trade columns in the P&L panel) the
default policy ``nan_metrics`` emits a NaN-filled row + a
``notes`` lineage entry.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from shared.artifacts.lineage import Lineage, OperatorStep
from shared.artifacts.missingness import RawNoCleaning
from shared.artifacts.types import Panel
from shared.artifacts.units import TimeSeriesUnits
from shared.config.operator_config import (
    OperatorConfig,
    OperatorConfigError,
    load_operator_config,
)
from shared.operators.summarize_trades.schemas import (
    EmptyTradesetPolicy,
    PnLAggregationMethod,
    SummarizeTradesParams,
)


_OPERATOR_NAME = "summarize_trades"
_OPERATOR_VERSION = "1.0.0"

_CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"

# Canonical metric column names + their unit family.  Order is
# preserved when constructing the Panel so the workspace renderer's
# methodology card lists them in a predictable order.
_METRIC_COLUMNS: Tuple[Tuple[str, TimeSeriesUnits], ...] = (
    ("hit_rate", TimeSeriesUnits.RATIO),
    ("mean_pnl", TimeSeriesUnits.BPS),
    ("sharpe_annualized", TimeSeriesUnits.RATIO),
    ("max_drawdown", TimeSeriesUnits.BPS),
    ("p10_pnl", TimeSeriesUnits.BPS),
    ("p50_pnl", TimeSeriesUnits.BPS),
    ("p90_pnl", TimeSeriesUnits.BPS),
)


class SummarizeTradesError(ValueError):
    """Raised on a recoverable user-facing failure."""


def summarize_trades(
    pnl_panel: Panel,
    params: Optional[SummarizeTradesParams] = None,
    config: Optional[OperatorConfig] = None,
) -> Panel:
    """Compute the V1 summary-metric Panel from a P&L Panel.

    Parameters
    ----------
    pnl_panel :
        ``Panel`` produced by ``evaluate_trades`` — columns
        ``trade_<i>_pnl``, rows = dates in the union of holding
        windows.
    params :
        Optional ``SummarizeTradesParams``; fields default to None
        and resolve from ``config.yaml``.
    config :
        Optional ``OperatorConfig``.

    Returns
    -------
    Panel
        Single-row Panel with ``_METRIC_COLUMNS`` columns and an
        index at the latest exit date in the P&L panel (or a
        sentinel "1970-01-01" timestamp on an empty input).
    """
    if params is None:
        params = SummarizeTradesParams()
    if config is None:
        config = _load_default_config()

    aggregation = _resolve_aggregation(params, config)
    trading_days = _resolve_trading_days(params, config)
    empty_policy = _resolve_empty_policy(params, config)

    if aggregation != "final_pnl":
        raise NotImplementedError(
            f"summarize_trades V1 supports only 'final_pnl' "
            f"aggregation; got {aggregation!r}.  See config.yaml "
            "planned_extensions for cumulative_max / time_weighted."
        )

    notes: List[str] = []
    trade_cols = [c for c in pnl_panel.payload.columns if c.endswith("_pnl")]
    n_trades = len(trade_cols)

    # ------------------------------------------------------------------
    # Holding window — read off the upstream TradeSet lineage step.
    # ------------------------------------------------------------------
    holding_window_days = _extract_holding_window(pnl_panel.lineage)

    # ------------------------------------------------------------------
    # Empty-input handling.
    # ------------------------------------------------------------------
    if n_trades == 0:
        if empty_policy == "raise":
            raise SummarizeTradesError(
                "P&L panel has no trade columns (upstream TradeSet "
                "was empty) and empty_tradeset_policy='raise'."
            )
        notes.append(
            "P&L panel has no trade columns — emitting NaN-filled summary."
        )
        return _build_summary_panel(
            metrics={c: float("nan") for c, _ in _METRIC_COLUMNS},
            as_of=pd.Timestamp("1970-01-01"),
            input_panel=pnl_panel,
            n_trades=0,
            holding_window_days=holding_window_days,
            trading_days_per_year=trading_days,
            aggregation=aggregation,
            notes=notes,
        )

    # ------------------------------------------------------------------
    # Realised per-trade P&L: final non-NaN value per column.
    # ------------------------------------------------------------------
    realised: List[float] = []
    for col in trade_cols:
        series = pnl_panel.payload[col].dropna()
        if len(series) == 0:
            # A trade with no in-window observations contributes
            # NaN — handled below by filtering NaNs out of the
            # metric calculations.
            realised.append(float("nan"))
        else:
            realised.append(float(series.iloc[-1]))

    realised_arr = np.array(realised, dtype=float)
    valid_mask = ~np.isnan(realised_arr)
    valid = realised_arr[valid_mask]

    if valid.size == 0:
        notes.append(
            "All trade columns are NaN-only — no realised P&L observations."
        )
        return _build_summary_panel(
            metrics={c: float("nan") for c, _ in _METRIC_COLUMNS},
            as_of=_as_of_timestamp(pnl_panel),
            input_panel=pnl_panel,
            n_trades=n_trades,
            holding_window_days=holding_window_days,
            trading_days_per_year=trading_days,
            aggregation=aggregation,
            notes=notes,
        )

    metrics: Dict[str, float] = {}
    metrics["hit_rate"] = float((valid >= 0.0).mean())
    metrics["mean_pnl"] = float(valid.mean())
    # Sharpe annualisation: returns are PER-TRADE; annualise by
    # sqrt(trading_days_per_year / holding_window_days).  When
    # holding_window_days is unknown (lineage didn't record it),
    # fall back to 252 → factor 1.0; surface in notes.
    if holding_window_days is None or holding_window_days <= 0:
        annualisation = 1.0
        notes.append(
            "Sharpe not annualised — upstream lineage did not record "
            "holding_window_days; reporting per-trade Sharpe."
        )
    else:
        annualisation = float(np.sqrt(
            float(trading_days) / float(holding_window_days)
        ))
    std = float(valid.std(ddof=1)) if valid.size > 1 else float("nan")
    if std and std > 0 and not np.isnan(std):
        metrics["sharpe_annualized"] = float(
            (valid.mean() / std) * annualisation
        )
    else:
        metrics["sharpe_annualized"] = float("nan")

    # Drawdown over cumulative realised P&L (trades in input order).
    cum = np.cumsum(valid)
    running_max = np.maximum.accumulate(cum)
    drawdowns = cum - running_max
    metrics["max_drawdown"] = float(drawdowns.min())  # <= 0

    metrics["p10_pnl"] = float(np.percentile(valid, 10))
    metrics["p50_pnl"] = float(np.percentile(valid, 50))
    metrics["p90_pnl"] = float(np.percentile(valid, 90))

    if valid_mask.sum() < n_trades:
        n_nan = int(n_trades - valid_mask.sum())
        notes.append(
            f"{n_nan} trade column(s) had no in-window observations and "
            "were excluded from the summary statistics."
        )

    return _build_summary_panel(
        metrics=metrics,
        as_of=_as_of_timestamp(pnl_panel),
        input_panel=pnl_panel,
        n_trades=n_trades,
        holding_window_days=holding_window_days,
        trading_days_per_year=trading_days,
        aggregation=aggregation,
        notes=notes,
    )


# ============================================================================
# Internals
# ============================================================================


def _as_of_timestamp(pnl_panel: Panel) -> pd.Timestamp:
    """Pick the row index for the output Panel.  Latest non-empty
    row in the input; defaults to today if the panel is empty."""
    if len(pnl_panel.payload.index) == 0:
        return pd.Timestamp("1970-01-01")
    return pd.Timestamp(pnl_panel.payload.index[-1])


def _extract_holding_window(lineage: Lineage) -> Optional[int]:
    """Walk the lineage chain backwards looking for the
    ``construct_trades`` step's ``holding_window_days`` param.

    Returns None when not found — V1 falls back to no-annualisation
    in that case (surfaced via a notes entry on the lineage step).
    """
    for step in reversed(list(lineage.steps)):
        if step.kind == "operator" and step.name == "construct_trades":
            params = getattr(step, "params", {}) or {}
            value = params.get("holding_window_days")
            if isinstance(value, (int, float)) and int(value) > 0:
                return int(value)
            return None
    return None


def _build_summary_panel(
    *,
    metrics: Dict[str, float],
    as_of: pd.Timestamp,
    input_panel: Panel,
    n_trades: int,
    holding_window_days: Optional[int],
    trading_days_per_year: int,
    aggregation: str,
    notes: List[str],
) -> Panel:
    columns = [c for c, _ in _METRIC_COLUMNS]
    units_by_column = {c: u for c, u in _METRIC_COLUMNS}
    df = pd.DataFrame(
        [[metrics.get(c, float("nan")) for c in columns]],
        index=pd.DatetimeIndex([as_of]),
        columns=columns,
        dtype=float,
    )

    step_params: Dict[str, Any] = {
        "aggregation": aggregation,
        "trading_days_per_year": trading_days_per_year,
        "holding_window_days": holding_window_days,
        "n_trades": n_trades,
        "metric_columns": list(columns),
        "notes": list(notes),
    }
    op_step = OperatorStep.build(
        name=_OPERATOR_NAME,
        version=_OPERATOR_VERSION,
        params=step_params,
        input_hashes=(input_panel.lineage.head_hash,),
    )
    lineage = input_panel.lineage.append(op_step)

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
    except OperatorConfigError as exc:  # pragma: no cover
        raise SummarizeTradesError(
            f"Could not load summarize_trades config.yaml: {exc}"
        ) from exc


def _resolve_aggregation(
    params: SummarizeTradesParams, config: OperatorConfig,
) -> PnLAggregationMethod:
    if params.pnl_aggregation_method is not None:
        return params.pnl_aggregation_method
    return config.default_value("pnl_aggregation_method")  # type: ignore[return-value]


def _resolve_trading_days(
    params: SummarizeTradesParams, config: OperatorConfig,
) -> int:
    if params.trading_days_per_year is not None:
        return int(params.trading_days_per_year)
    return int(config.default_value("trading_days_per_year"))


def _resolve_empty_policy(
    params: SummarizeTradesParams, config: OperatorConfig,
) -> EmptyTradesetPolicy:
    if params.empty_tradeset_policy is not None:
        return params.empty_tradeset_policy
    return config.default_value("empty_tradeset_policy")  # type: ignore[return-value]


__all__ = ["summarize_trades", "SummarizeTradesError"]
