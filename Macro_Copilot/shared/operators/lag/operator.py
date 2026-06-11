"""lag — shift a Series back by k rows.

Finance-blind ``single_series_transform`` operator.  Emits the Series
whose value at each position is the input's value from ``periods``
rows EARLIER (pandas ``shift(k)`` semantics — the SIGN CONVENTION,
pinned here and in the YAML: positive ``periods`` always looks BACK;
the first ``periods`` rows are NaN).  The lagged series is the raw
material for lagged regressions, change-vs-level comparisons and
period-over-period composition (e.g. ``series_arithmetic(subtract)``
of a series and its lag).

LEAD IS DELIBERATELY NOT OFFERED: a negative shift pulls FUTURE values
back to t — silent look-ahead that would undermine the rolling
family's ``look_ahead_safe`` hygiene.  Declared in
``planned_extensions`` with the hazard documented (OPR7).

Contract highlights (cite by OPR-number):

  - OPR1/OPR6 : one finance-blind structural method (row shifting);
    zero finance vocabulary or math.
  - OPR2      : ONE output artifact type — always a ``Series``; units
    passthrough (a shifted BPS series is still BPS).
  - OPR8      : ``params: Optional[LagParams] = None``; defaults
    resolve from ``config.yaml``.
  - OPR9      : one ``Series`` in, one ``Series`` out.
  - OPR10     : one ``OperatorStep`` appended to the input's chain.
  - OPR13     : typed ``LagError`` refusals: non-Series input, or
    ``periods`` >= the input length (the output would be all-NaN).
    The NaN head is legitimate missingness.
  - OPR14     : pure + rerun-deterministic; no value arithmetic, so
    no overflow surface.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from shared.artifacts.lineage import OperatorStep, sanitize_params_for_lineage
from shared.artifacts.types import Series
from shared.config.operator_config import (
    OperatorConfig,
    _check_config_identity,
    load_operator_config,
)
from shared.operators.lag.schemas import LagParams


_OPERATOR_NAME = "lag"
_OPERATOR_VERSION = "1.0.0"

_CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


class LagError(ValueError):
    """Raised by ``lag`` on a recoverable user-facing failure (a
    ``ValueError`` subclass, OPR13)."""


def lag(
    series: Series,
    *,
    params: Optional[LagParams] = None,
    config: Optional[OperatorConfig] = None,
) -> Series:
    """Shift ``series`` back by ``params.periods`` rows.

    Parameters
    ----------
    series :
        The input ``Series``.
    params :
        Optional ``LagParams``.  When ``None`` every field is resolved
        from the bundled ``config.yaml`` (OPR8).
    config :
        Optional ``OperatorConfig``.  When ``None`` the bundled
        ``config.yaml`` is loaded (process-cached).

    Returns
    -------
    Series
        The input shifted back by ``periods`` rows on the SAME index
        (the value from k rows earlier appears at each position; the
        first k rows are NaN), in the input's units; frequency and
        missingness policy pass through; lineage extended by one
        ``OperatorStep``.

    Raises
    ------
    LagError
        The input is not a ``Series``, or ``periods`` >= the input
        length (the output would be all-NaN).
    """
    # ------------------------------------------------------------------
    # 1. Load config + identity check — OPR12.
    # ------------------------------------------------------------------
    if config is None:
        config = load_operator_config(_CONFIG_PATH)
    _check_config_identity(config, _OPERATOR_NAME, _OPERATOR_VERSION)

    # ------------------------------------------------------------------
    # 2. Resolve params from config when omitted — OPR8.
    # ------------------------------------------------------------------
    if params is None:
        params = LagParams(periods=int(config.default_value("periods")))

    # ------------------------------------------------------------------
    # 3. Structural-input validation (OPR9 typed I/O).
    # ------------------------------------------------------------------
    if not isinstance(series, Series):
        raise LagError(
            "lag: input must be a Series artifact; got "
            f"{type(series).__name__}."
        )

    periods = int(params.periods)
    if periods >= len(series.payload):
        raise LagError(
            f"lag: periods={periods} >= input length "
            f"({len(series.payload)} rows); the output would be "
            "all-NaN.  Lower periods or widen the input lookback."
        )

    # ------------------------------------------------------------------
    # 4. The shift (no value arithmetic — no overflow surface).
    # ------------------------------------------------------------------
    result = series.payload.shift(periods).astype(float)
    result.name = f"lag{periods}__{series.series_key}"

    # ------------------------------------------------------------------
    # 5. Lineage — one OperatorStep (OPR10); the sign convention is
    #    recorded so the direction is auditable.
    # ------------------------------------------------------------------
    step_params: Dict[str, Any] = {
        "periods": periods,
        "sign_convention": (
            "positive periods looks BACK: the value from k rows "
            "earlier appears at each position (pandas shift(k)); "
            "lead (negative shift) is deliberately not offered"
        ),
        "n_obs": int(len(series.payload)),
        "input_units": series.units.value,
        "input_series_key": series.series_key,
    }
    step = OperatorStep.build(
        name=_OPERATOR_NAME,
        version=_OPERATOR_VERSION,
        params=sanitize_params_for_lineage(step_params),
        input_hashes=(series.lineage.head_hash,),
    )
    out_lineage = series.lineage.append(step)

    return Series(
        series_key=f"lag{periods}__{series.series_key}",
        payload=result,
        units=series.units,
        frequency=series.frequency,
        missingness_policy=series.missingness_policy,
        lineage=out_lineage,
    )


__all__ = ["lag", "LagError"]
