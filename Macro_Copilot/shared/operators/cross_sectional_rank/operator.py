"""cross_sectional_rank — finance-blind cross-sectional ranking of a Panel.

Owns the **ranking** structural method family.  For each row (date) of
a typed ``Panel``, ranks the columns against each other and emits a
``Panel`` of the same shape whose cells are the per-date ranks.

Listed as a Pass example in docs/architecture/operator_architecture.md
("cross_sectional_rank — Finance-blind ranking ... still a normal
operator").  The doc's illustrative signature shows a SeriesSet input;
this V1 takes a ``Panel`` instead, for two reasons:

  1. consistency with ``rolling_zscore_panel`` (the sibling panel
     operator) and the wide-cross-section artifacts that flow through
     the system (FX panels, rates curve panels);
  2. a Panel IS the natural first-class collection type for "rank the
     columns within each row" — no per-key alignment step is needed.

A ``SeriesSet`` input adapter is listed as a planned extension if a
SeriesSet-native consumer appears.

Scope discipline: ranking ONLY.  Rolling z-score normalization is a
SEPARATE family (``rolling_zscore_panel``).

Errors
------
Like the rest of ``shared.operators.*``: raise typed exceptions on
user-facing failures; the template / public-tool layer converts them
into ``{"error": ...}`` envelopes.

Lineage contract
----------------
Output ``Panel.lineage`` = input lineage + this operator's
``OperatorStep``; ``input_hashes`` carries the input Panel's
``lineage.head_hash``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import pandas as pd

from shared.artifacts.lineage import OperatorStep
from shared.artifacts.types import Panel
from shared.artifacts.units import TimeSeriesUnits
from shared.config.operator_config import (
    OperatorConfig,
    OperatorConfigError,
    load_operator_config,
)
from shared.operators.cross_sectional_rank.schemas import (
    CrossSectionalRankParams,
)


_OPERATOR_NAME = "cross_sectional_rank"
_OPERATOR_VERSION = "1.0.0"

_CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"

# method → output unit (the ranking family's unit algebra).
_METHOD_UNIT = {
    "rank": TimeSeriesUnits.COUNT,
    "percentile": TimeSeriesUnits.PCT_RANK,
    "normalized": TimeSeriesUnits.RATIO,
}


class CrossSectionalRankError(ValueError):
    """Raised by ``cross_sectional_rank`` on a recoverable user-facing
    failure (empty panel, single-column panel where ranking is
    degenerate, etc.).

    Subclass of ``ValueError`` so existing ``except ValueError`` blocks
    in the rest of ``shared/`` keep working.
    """


def cross_sectional_rank(
    panel: Panel,
    params: CrossSectionalRankParams,
    config: Optional[OperatorConfig] = None,
) -> Panel:
    """Rank the columns of ``panel`` within each row (cross-section).

    Parameters
    ----------
    panel :
        Input typed ``Panel`` (rows = DatetimeIndex, columns = named
        series).  Must have >= 2 columns (ranking a 1-column cross-
        section is degenerate) and >= 1 row.
    params :
        ``CrossSectionalRankParams`` — ``method`` (rank / percentile /
        normalized), ``ascending``, ``tie_method``.
    config :
        Optional ``OperatorConfig``.  When omitted the bundled
        ``config.yaml`` is loaded (process-cached).

    Returns
    -------
    Panel
        Same index + columns as the input; ``units_by_column`` set per
        ``method`` (COUNT / PCT_RANK / RATIO); input
        ``missingness_policy`` carried forward; lineage = input lineage
        + this operator's step.

    Raises
    ------
    CrossSectionalRankError
        If the panel has < 2 columns or zero rows.
    OperatorConfigError
        If ``config`` is the wrong type or names a different operator.
    """
    if config is None:
        config = load_operator_config(_CONFIG_PATH)

    if not isinstance(config, OperatorConfig):
        raise OperatorConfigError(
            f"cross_sectional_rank: 'config' must be an OperatorConfig "
            f"instance; got {type(config).__name__}."
        )
    if config.operator.name != _OPERATOR_NAME:
        raise OperatorConfigError(
            f"cross_sectional_rank: config name mismatch — expected "
            f"{_OPERATOR_NAME!r}, got {config.operator.name!r}."
        )

    payload = panel.payload
    if payload.shape[0] == 0:
        raise CrossSectionalRankError(
            "cross_sectional_rank: input Panel has zero rows."
        )
    if payload.shape[1] < 2:
        raise CrossSectionalRankError(
            f"cross_sectional_rank: input Panel has {payload.shape[1]} "
            "column(s); ranking a cross-section needs >= 2 columns. "
            "A 1-column rank is degenerate (every row would be rank 1)."
        )

    # ------------------------------------------------------------------
    # Cross-sectional rank: axis=1 ranks COLUMNS within each ROW (date).
    # na_option='keep' leaves NaN cells unranked (they don't consume a
    # rank slot).  pct=True yields the 0..1 fractional rank; ×100 for
    # the percentile variant.
    # ------------------------------------------------------------------
    use_pct = params.method in ("percentile", "normalized")
    ranked = payload.astype(float).rank(
        axis=1,
        method=params.tie_method,
        ascending=params.ascending,
        na_option="keep",
        pct=use_pct,
    )
    if params.method == "percentile":
        ranked = ranked * 100.0
    ranked = ranked.reindex(index=payload.index, columns=payload.columns)

    out_unit = _METHOD_UNIT[params.method]
    out_units = {col: out_unit for col in payload.columns}

    step_params = {
        "method": params.method,
        "ascending": params.ascending,
        "tie_method": params.tie_method,
        "columns": list(payload.columns),
        "n_rows": int(payload.shape[0]),
        "output_unit": out_unit.value,
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
        payload=ranked,
        units_by_column=out_units,
        missingness_policy=panel.missingness_policy,
        lineage=out_lineage,
    )


__all__ = [
    "cross_sectional_rank",
    "CrossSectionalRankError",
]
