"""rolling_pca — POINT-IN-TIME windowed PCA factor scores of a Panel.

Finance-blind ``cross_sectional`` operator and an A4 model-fit engine —
the POINT-IN-TIME complement of ``pca_decompose``.  At each date it
fits correlation PCA on the STRICTLY TRAILING window (reusing
``shared/quant/pca.py``) and emits the current factor scores, so each
score uses ONLY data up to that date (NO look-ahead).  Panel(features)
-> SeriesSet(rolling factor scores pc1..pcK).

THE KEY DISTINCTION (the WHOLE reason this operator exists):
``pca_decompose`` fits the loadings over the WHOLE Panel (full-sample;
it FORBIDS point-in-time use).  ``rolling_pca`` re-fits per trailing
window, so it IS SAFE for as-of / walk-forward / backtest chains — the
score at t is exactly what you would have computed at t with only the
data then available.  Stamped ``fit_scope='rolling_window'`` (NOT
full_sample).

CROSS-WINDOW SIGN CONTINUITY (the new subtlety): each window's loading
has a canonical sign (largest-|loading| positive), but the loading can
flip between adjacent windows, which would inject spurious jumps into
the factor series.  Each window is therefore SIGN-ALIGNED to the
previous (flip component k when its loading's dot with the previous
window's is negative) — the cross-window sign-flip count
(``n_sign_flips``) is a factor-rotation diagnostic.  A full Procrustes
rotation (genuine factor rotation, not just a flip) is a declared
planned extension; ``near_degenerate_any`` discloses when a window's
subspace was non-unique.

NaN POLICY (TWO-TIER — a rolling window over CONTIGUOUS rows): a row is
NaN iff ANY feature is.  Contiguous LEADING/TRAILING NaN rows are
TOLERATED (the rolling fit runs on the interior block; edges pass
through as NaN); an INTERIOR NaN row is REFUSED (a window must be
contiguous).  The warmup head (dates before the first full window)
carries NaN factors.

DESIGN LOCKS (OPR7, lineage-stamped, reused from pca_decompose):
correlation PCA (z-score per column), the SVD estimator, the canonical
per-window sign + the cross-window dot-alignment.

Contract highlights (cite by OPR-number):

  - OPR1/OPR6 : one finance-blind windowed linear-factor model; zero
    finance vocabulary; keys pc1..pcK.
  - OPR2      : ONE output artifact type — always a ``SeriesSet`` (the
    rolling factor scores; the per-window loadings ride in lineage as
    the last window + the flip count).
  - OPR8      : window + n_components required (no honest defaults).
  - OPR9      : one ``Panel`` in, one ``SeriesSet`` out.
  - OPR10     : one ``OperatorStep``; the window, the last loadings,
    the flip count + the locks + the scope ride in params.
  - OPR11     : FACTOR_LEVEL units for every member (eigen-units).
  - OPR13     : typed ``RollingPcaError`` refusals: non-Panel input;
    missing params; fewer than 2 feature columns; n_components out of
    [1, n_features]; window < n_features+1 or > the finite-block
    length; an INTERIOR NaN; a degenerate window.
  - OPR14     : pure + rerun-deterministic (sorted columns; trailing
    windows; deterministic sign alignment).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from shared.artifacts.lineage import OperatorStep, sanitize_params_for_lineage
from shared.artifacts.missingness import RawNoCleaning
from shared.artifacts.types import Panel, SeriesSet
from shared.artifacts.units import TimeSeriesUnits
from shared.config.operator_config import (
    OperatorConfig,
    _check_config_identity,
    load_operator_config,
)
from shared.operators.rolling_pca.schemas import RollingPcaParams
from shared.quant.pca import rolling_pca_scores


_OPERATOR_NAME = "rolling_pca"
_OPERATOR_VERSION = "1.0.0"

_CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"

_MIN_FEATURES = 2


class RollingPcaError(ValueError):
    """Raised by ``rolling_pca`` on a recoverable user-facing failure
    (a ``ValueError`` subclass, OPR13)."""


def rolling_pca(
    features: Panel,
    *,
    params: Optional[RollingPcaParams] = None,
    config: Optional[OperatorConfig] = None,
) -> SeriesSet:
    """Point-in-time rolling-PCA factor scores of a feature ``Panel``.

    Parameters
    ----------
    features :
        The input ``Panel`` (dates x feature-columns; >= 2 features).
        Contiguous leading/trailing NaN rows are tolerated; an INTERIOR
        NaN row is refused.
    params :
        ``RollingPcaParams`` — REQUIRED (window and n_components have no
        honest default).
    config :
        Optional ``OperatorConfig``.  When ``None`` the bundled
        ``config.yaml`` is loaded (process-cached).

    Returns
    -------
    SeriesSet
        The rolling factor-score Series (keys pc1..pcK) on the FULL
        Panel index, every member in FACTOR_LEVEL units; NaN in the
        warmup head and the edge rows.  POINT-IN-TIME-safe.

    Raises
    ------
    RollingPcaError
        Missing params; a non-Panel input; fewer than 2 feature
        columns; n_components out of [1, n_features]; window <
        n_features+1 or > the finite-block length; an INTERIOR NaN; or a
        degenerate window.
    """
    # ------------------------------------------------------------------
    # 1. Load config + identity check — OPR12.
    # ------------------------------------------------------------------
    if config is None:
        config = load_operator_config(_CONFIG_PATH)
    _check_config_identity(config, _OPERATOR_NAME, _OPERATOR_VERSION)

    # ------------------------------------------------------------------
    # 2. window + n_components are methodology — params=None refuses
    #    naming them (the pca_decompose precedent; OPR8).
    # ------------------------------------------------------------------
    if params is None:
        raise RollingPcaError(
            "rolling_pca requires explicit params with window (the "
            "trailing window length) and n_components (the number of "
            "components per window) — both are methodology choices with "
            "no honest default."
        )
    window = int(params.window)
    n_components = int(params.n_components)

    # ------------------------------------------------------------------
    # 3. Structural-input validation (OPR9 typed I/O).
    # ------------------------------------------------------------------
    if not isinstance(features, Panel):
        raise RollingPcaError(
            "rolling_pca: input must be a Panel artifact; got "
            f"{type(features).__name__}."
        )

    frame = features.payload
    columns = sorted(str(c) for c in frame.columns)
    n_features = len(columns)
    if n_features < _MIN_FEATURES:
        raise RollingPcaError(
            f"rolling_pca: needs at least {_MIN_FEATURES} feature "
            f"columns (got {n_features})."
        )
    if n_components > n_features:
        raise RollingPcaError(
            f"rolling_pca: n_components ({n_components}) cannot exceed "
            f"the number of feature columns ({n_features})."
        )
    if window < n_features + 1:
        raise RollingPcaError(
            f"rolling_pca: window ({window}) must be at least "
            f"n_features+1 = {n_features + 1} so each window's PCA is "
            "full-rank."
        )

    full_index = frame.index
    n_total = int(len(full_index))
    sorted_frame = frame[columns]

    # TWO-TIER NaN: a rolling window runs over CONTIGUOUS rows.  A row is
    # finite iff ALL features are.  Contiguous leading/trailing tolerated;
    # INTERIOR refused.
    row_finite = sorted_frame.notna().all(axis=1).to_numpy()
    n_finite = int(row_finite.sum())
    if n_finite == 0:
        raise RollingPcaError(
            "rolling_pca: the input has no fully-finite rows."
        )
    first = int(np.argmax(row_finite))
    last = n_total - int(np.argmax(row_finite[::-1]))  # exclusive
    core_finite = row_finite[first:last]
    n_interior_nan = int((~core_finite).sum())
    if n_interior_nan > 0:
        raise RollingPcaError(
            f"rolling_pca: the input has {n_interior_nan} INTERIOR NaN "
            "row(s).  A rolling window runs over ADJACENT rows; "
            "dropping an interior gap would splice non-adjacent dates "
            "into one window.  Fill the gaps upstream (align_series "
            "ffill) and re-run.  (Contiguous leading/trailing NaN rows "
            "are tolerated.)"
        )

    n_core = last - first
    if window > n_core:
        raise RollingPcaError(
            f"rolling_pca: window ({window}) exceeds the contiguous "
            f"finite-block length ({n_core}) — not even one full window "
            "fits.  Shorten the window or widen the lookback."
        )

    x = sorted_frame.to_numpy(dtype=float)[first:last]

    # ------------------------------------------------------------------
    # 4. The rolling fit (shared/quant; design-locked correlation PCA +
    #    sign alignment).  A degenerate window refuses inside.
    # ------------------------------------------------------------------
    try:
        result = rolling_pca_scores(x, window, n_components)
    except ValueError as exc:
        raise RollingPcaError(f"rolling_pca: {exc}") from exc

    # ------------------------------------------------------------------
    # 5. Lineage — ONE OperatorStep (OPR10); the last window + the flip
    #    count; reused for the set lineage and every member's upstream.
    # ------------------------------------------------------------------
    keys = [f"pc{k + 1}" for k in range(n_components)]
    step_params: Dict[str, Any] = {
        "window": window,
        "n_components": n_components,
        "n_windows": int(result.n_windows),
        "n_sign_flips": int(result.n_sign_flips),
        "near_degenerate_any": bool(result.near_degenerate_any),
        "last_loadings": [
            [float(v) for v in row] for row in result.last_loadings
        ],
        "last_explained_variance_ratio": [
            float(v) for v in result.last_explained_variance_ratio
        ],
        "last_singular_values": [
            float(v) for v in result.last_singular_values
        ],
        "pca_kind": "correlation",                # design-locked (OPR7)
        "feature_standardization": "zscore_per_column",
        "sign_convention": "largest_abs_loading_positive",
        "sign_continuity": "previous_window_dot_alignment",
        "algorithm": "svd",                        # design-locked (OPR7)
        "fit_scope": "rolling_window",             # POINT-IN-TIME (P5)
        "output_keys": keys,
        "n_features": n_features,
        "feature_columns": columns,
        "n_obs": n_total,
        "n_core_rows": n_core,
        "n_leading_nan": first,
        "n_trailing_nan": n_total - last,
    }
    step = OperatorStep.build(
        name=_OPERATOR_NAME,
        version=_OPERATOR_VERSION,
        params=sanitize_params_for_lineage(step_params),
        input_hashes=(features.lineage.head_hash,),
    )
    member_lineage = features.lineage.append(step)

    # ------------------------------------------------------------------
    # 6. Build the output SeriesSet — one rolling-factor Series per
    #    component on the FULL Panel index; warmup + edge rows NaN.
    # ------------------------------------------------------------------
    series_by_key: Dict[str, pd.Series] = {}
    units_by_key: Dict[str, TimeSeriesUnits] = {}
    missingness_by_key: Dict[str, RawNoCleaning] = {}
    upstream_lineage_by_key: Dict[str, Any] = {}
    for k, key in enumerate(keys):
        col_values = np.full(n_total, np.nan, dtype=float)
        col_values[first:last] = result.scores[:, k]
        payload = pd.Series(col_values, index=full_index, dtype=float)
        payload.name = key
        series_by_key[key] = payload
        units_by_key[key] = TimeSeriesUnits.FACTOR_LEVEL
        missingness_by_key[key] = RawNoCleaning()
        upstream_lineage_by_key[key] = member_lineage

    return SeriesSet(
        series_by_key=series_by_key,
        units_by_key=units_by_key,
        missingness_by_key=missingness_by_key,
        upstream_lineage_by_key=upstream_lineage_by_key,
        common_index=full_index,
        frequency=None,  # a Panel carries no frequency tag
        lineage=member_lineage,
    )


__all__ = ["rolling_pca", "RollingPcaError"]
