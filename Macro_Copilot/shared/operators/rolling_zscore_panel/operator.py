"""rolling_zscore_panel — finance-blind rolling z-score over a Panel.

Owns the **rolling-normalization** structural method family.  For each
column of a typed ``Panel`` independently, computes a trailing rolling
z-score::

    z(t) = (value(t) - rolling_mean(t, window)) / rolling_std(t, window, ddof)

and emits a ``Panel`` with the same index / columns and
``units_by_column = Z_SCORE`` for every column.

Why this operator exists
------------------------
Six FX call sites re-implemented "trailing-window z-score per column":
``scan_fx_calendar_spread``, ``scan_fx_vol_skew``,
``scan_fx_cross_currency_basis``, ``scan_fx_implied_yield_differential``
(Phase F1), plus legacy ``vol_z_score`` and ``vol_scanner``.  Each
hand-rolled `(current - mean) / std` on a trailing window.  This
operator collapses all six onto one finance-blind primitive that runs
unchanged on rates / equities / temperature panels.

Scope discipline (operator-architecture review 2026-05-28)
----------------------------------------------------------
Z-score ONLY.  Cross-sectional rank / percentile are a DISTINCT
structural family (ranking) and ship as a separate operator
(``cross_sectional_rank`` is already a Pass example in the architecture
doc).  Folding them in here would make this a wastebasket operator.

Errors
------
Like the rest of ``shared.operators.*``: raise typed exceptions on
user-facing failures; the template / public-tool layer converts them
into ``{"error": ...}`` envelopes.

Lineage contract
----------------
The output ``Panel.lineage`` is the input Panel's lineage with this
operator's ``OperatorStep`` appended.  ``input_hashes`` carries the
input Panel's ``lineage.head_hash``.  Input units are discarded (a
z-score is dimensionless) but recorded in the step params as
``input_units_by_column`` for provenance.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from shared.artifacts.lineage import OperatorStep
from shared.artifacts.types import Panel
from shared.artifacts.units import TimeSeriesUnits
from shared.config.operator_config import (
    OperatorConfig,
    OperatorConfigError,
    load_operator_config,
)
from shared.operators.rolling_zscore_panel.schemas import (
    RollingZscorePanelParams,
)


_OPERATOR_NAME = "rolling_zscore_panel"
_OPERATOR_VERSION = "1.0.0"

_CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


class RollingZscorePanelError(ValueError):
    """Raised by ``rolling_zscore_panel`` on a recoverable user-facing
    failure (empty panel, window longer than available history with no
    valid rows, etc.).

    Subclass of ``ValueError`` so existing ``except ValueError`` blocks
    in the rest of ``shared/`` keep working.
    """


def rolling_zscore_panel(
    panel: Panel,
    params: RollingZscorePanelParams,
    config: Optional[OperatorConfig] = None,
) -> Panel:
    """Compute a trailing rolling z-score for every column of ``panel``.

    Parameters
    ----------
    panel :
        Input typed ``Panel`` (rows = DatetimeIndex, columns = named
        series).  Must contain >= 1 column and >= 1 row.
    params :
        ``RollingZscorePanelParams`` — ``window`` (required),
        ``min_periods``, ``ddof``.
    config :
        Optional ``OperatorConfig``.  When omitted, the bundled
        ``config.yaml`` is loaded (and process-cached).

    Returns
    -------
    Panel
        Same index + columns as the input; ``units_by_column`` =
        Z_SCORE for every column; input ``missingness_policy`` carried
        forward; lineage = input lineage + this operator's step.

    Raises
    ------
    RollingZscorePanelError
        If the input panel has zero columns or zero rows.
    OperatorConfigError
        If ``config`` is the wrong type or names a different operator.
    """
    if config is None:
        config = load_operator_config(_CONFIG_PATH)

    if not isinstance(config, OperatorConfig):
        raise OperatorConfigError(
            f"rolling_zscore_panel: 'config' must be an OperatorConfig "
            f"instance; got {type(config).__name__}."
        )
    if config.operator.name != _OPERATOR_NAME:
        raise OperatorConfigError(
            f"rolling_zscore_panel: config name mismatch — expected "
            f"{_OPERATOR_NAME!r}, got {config.operator.name!r}."
        )

    payload = panel.payload
    if payload.shape[1] == 0:
        raise RollingZscorePanelError(
            "rolling_zscore_panel: input Panel has zero columns."
        )
    if payload.shape[0] == 0:
        raise RollingZscorePanelError(
            "rolling_zscore_panel: input Panel has zero rows."
        )

    # ------------------------------------------------------------------
    # Rolling z-score per column.  pandas applies the rolling op
    # column-wise on a DataFrame, so this is vectorised across columns.
    # The current row is included in its own trailing window (standard
    # z-score convention; the last row reproduces the FX scanners'
    # single trailing-window z value exactly).
    # ------------------------------------------------------------------
    numeric = payload.astype(float)
    roll = numeric.rolling(window=params.window, min_periods=params.min_periods)
    rolling_mean = roll.mean()
    rolling_std = roll.std(ddof=params.ddof)

    z = (numeric - rolling_mean) / rolling_std
    # A constant window has std == 0 → 0/0 = NaN, finite/0 = +/-inf.
    # A constant series cannot be normalized; emit NaN, never inf.
    z = z.replace([np.inf, -np.inf], np.nan)
    # Preserve column order + index exactly.
    z = z.reindex(index=payload.index, columns=payload.columns)

    # ------------------------------------------------------------------
    # Every output column is Z_SCORE (dimensionless).  Input units are
    # deliberately discarded but recorded in the step params.
    # ------------------------------------------------------------------
    out_units = {col: TimeSeriesUnits.Z_SCORE for col in payload.columns}

    step_params = {
        "window": params.window,
        "min_periods": params.min_periods,
        "ddof": params.ddof,
        "columns": list(payload.columns),
        "n_rows": int(payload.shape[0]),
        "input_units_by_column": {
            str(col): unit.value for col, unit in panel.units_by_column.items()
        },
    }
    op_step = OperatorStep.build(
        name=_OPERATOR_NAME,
        version=_OPERATOR_VERSION,
        params=step_params,
        input_hashes=(panel.lineage.head_hash,),
    )
    out_lineage = panel.lineage.append(op_step)

    return Panel(
        payload=z,
        units_by_column=out_units,
        missingness_policy=panel.missingness_policy,
        lineage=out_lineage,
    )


__all__ = [
    "rolling_zscore_panel",
    "RollingZscorePanelError",
]
