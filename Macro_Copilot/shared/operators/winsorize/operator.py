"""winsorize — cap a Series' extreme values.

Finance-blind ``single_series_transform`` operator.  Clips the input's
values to bounds — either the FULL-SAMPLE empirical quantiles [q, 1−q]
(mode='quantile', the canonical pre-fit hygiene transform) or fixed
caller-supplied values (mode='absolute') — emitting a Series in the
input's units with the clipped count recorded in lineage.

LOOK-AHEAD DISCLOSURE (P5, load-bearing): quantile-mode bounds are
computed over the WHOLE sample, so a value early in the series is
clipped using quantiles that include later data.  That is the standard
pre-fit hygiene semantics (outlier-capping before fitting/correlating)
and it is disclosed here, in the YAML, in the card, and in lineage
(``quantile_scope='full_sample'``).  Do NOT feed quantile-mode output
into a point-in-time backtest-style composition; a rolling variant is
declared in ``planned_extensions``.

Contract highlights (cite by OPR-number):

  - OPR1/OPR6 : one finance-blind structural method (value capping);
    zero finance vocabulary or math.
  - OPR2      : ONE output artifact type — always a ``Series``; units
    passthrough for both modes (clipping never changes dimension).
  - OPR8      : ``params: Optional[WinsorizeParams] = None``; defaults
    resolve from ``config.yaml``.
  - OPR9      : one ``Series`` in, one ``Series`` out.
  - OPR10     : one ``OperatorStep``; the RESOLVED bounds and the
    clipped count are recorded (the mask is auditable).
  - OPR13     : typed ``WinsorizeError`` refusals: non-Series input;
    absolute mode with no bounds, or lower >= upper; bounds supplied
    in quantile mode (ambiguous intent); an all-NaN input.  NaN
    positions pass through untouched.
  - OPR14     : pure + rerun-deterministic; ``quantile`` is nulled in
    lineage params under absolute mode and the bounds under quantile
    mode are data-derived outputs recorded for audit.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np

from shared.artifacts.lineage import OperatorStep, sanitize_params_for_lineage
from shared.artifacts.types import Series
from shared.config.operator_config import (
    OperatorConfig,
    _check_config_identity,
    load_operator_config,
)
from shared.operators.winsorize.schemas import WinsorizeParams


_OPERATOR_NAME = "winsorize"
_OPERATOR_VERSION = "1.0.0"

_CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


class WinsorizeError(ValueError):
    """Raised by ``winsorize`` on a recoverable user-facing failure (a
    ``ValueError`` subclass, OPR13)."""


def winsorize(
    series: Series,
    *,
    params: Optional[WinsorizeParams] = None,
    config: Optional[OperatorConfig] = None,
) -> Series:
    """Clip ``series``' values to quantile or absolute bounds.

    Parameters
    ----------
    series :
        The input ``Series``.
    params :
        Optional ``WinsorizeParams``.  When ``None`` every field is
        resolved from the bundled ``config.yaml`` (OPR8).
    config :
        Optional ``OperatorConfig``.  When ``None`` the bundled
        ``config.yaml`` is loaded (process-cached).

    Returns
    -------
    Series
        The clipped series on the input index (NaN positions
        untouched), in the input's units; frequency and missingness
        policy pass through; lineage records the mode, the RESOLVED
        bounds and the clipped count.

    Raises
    ------
    WinsorizeError
        The input is not a ``Series``; absolute mode with no bounds, a
        non-finite supplied bound, or lower >= upper; explicit bounds
        supplied in quantile mode; or the input has no finite values.
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
        params = WinsorizeParams(
            mode=config.default_value("mode"),
            quantile=float(config.default_value("quantile")),
        )

    # ------------------------------------------------------------------
    # 3. Structural-input validation (OPR9 typed I/O; mode coherence).
    # ------------------------------------------------------------------
    if not isinstance(series, Series):
        raise WinsorizeError(
            "winsorize: input must be a Series artifact; got "
            f"{type(series).__name__}."
        )

    values = series.payload
    finite = values.dropna()
    if len(finite) == 0:
        raise WinsorizeError(
            "winsorize: the input has no finite values to clip "
            f"({len(values)} rows, all NaN)."
        )

    if params.mode == "quantile":
        if params.lower_bound is not None or params.upper_bound is not None:
            raise WinsorizeError(
                "winsorize: explicit lower_bound/upper_bound are "
                "forbidden in quantile mode (ambiguous intent) — use "
                "mode='absolute' for fixed bounds."
            )
        lo = float(finite.quantile(params.quantile))
        hi = float(finite.quantile(1.0 - params.quantile))
        quantile_for_lineage: Optional[float] = float(params.quantile)
        scope = "full_sample"
    elif params.mode == "absolute":
        if params.lower_bound is None and params.upper_bound is None:
            raise WinsorizeError(
                "winsorize: absolute mode needs at least one of "
                "lower_bound/upper_bound (in the series' own units)."
            )
        # A NaN/Inf bound would silently degrade to "no bound" — the
        # threshold_events non-finite-threshold precedent: refuse, the
        # caller's intent is unrepresentable (OPR13/P6).
        for side, supplied in (
            ("lower_bound", params.lower_bound),
            ("upper_bound", params.upper_bound),
        ):
            if supplied is not None and not np.isfinite(supplied):
                raise WinsorizeError(
                    f"winsorize: {side} must be finite; got "
                    f"{supplied!r}.  To leave that side uncapped, omit "
                    "it instead."
                )
        lo = (
            float(params.lower_bound)
            if params.lower_bound is not None else -np.inf
        )
        hi = (
            float(params.upper_bound)
            if params.upper_bound is not None else np.inf
        )
        # quantile is meaningless here — nulled in lineage (OPR14).
        quantile_for_lineage = None
        scope = "fixed"
    else:
        # Defensive (the schema's Literal closes the set) — OPR8.
        raise NotImplementedError(
            f"winsorize: mode={params.mode!r} is declared but not "
            "implemented; see config.yaml methodology.planned_extensions."
        )

    if lo >= hi:
        raise WinsorizeError(
            f"winsorize: degenerate bounds (lower {lo!r} >= upper "
            f"{hi!r}) — nothing between them to keep."
        )

    # ------------------------------------------------------------------
    # 4. The clip (NaN positions untouched; pandas clip propagates NaN).
    # ------------------------------------------------------------------
    clip_lo = None if np.isneginf(lo) else lo
    clip_hi = None if np.isposinf(hi) else hi
    result = values.clip(lower=clip_lo, upper=clip_hi).astype(float)
    n_clipped = int(
        ((values < lo) | (values > hi)).sum()  # NaN compares False
    )
    result.name = f"winsorized__{series.series_key}"

    # ------------------------------------------------------------------
    # 5. Lineage — one OperatorStep (OPR10); the resolved bounds, the
    #    clipped count and the look-ahead scope are recorded.
    # ------------------------------------------------------------------
    step_params: Dict[str, Any] = {
        "mode": params.mode,
        "quantile": quantile_for_lineage,
        "quantile_scope": scope if params.mode == "quantile" else None,
        "resolved_lower": None if np.isneginf(lo) else lo,
        "resolved_upper": None if np.isposinf(hi) else hi,
        "n_clipped": n_clipped,
        "n_obs": int(len(values)),
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
        series_key=f"winsorized__{series.series_key}",
        payload=result,
        units=series.units,
        frequency=series.frequency,
        missingness_policy=series.missingness_policy,
        lineage=out_lineage,
    )


__all__ = ["winsorize", "WinsorizeError"]
