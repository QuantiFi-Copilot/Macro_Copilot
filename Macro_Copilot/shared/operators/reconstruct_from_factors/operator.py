"""reconstruct_from_factors — PCA fair-value residual of a Panel column.

Finance-blind ``cross_sectional`` operator and an A4 model-fit engine —
the natural sibling of ``pca_decompose``.  Re-fits correlation PCA on a
feature Panel (reusing ``shared/quant/pca.py``), reconstructs the TARGET
feature from the top-k principal components, and emits the RESIDUAL
(observed − the PCA-implied level) as a Series in the target column's
own units: a DESCRIPTIVE in-sample fair-value gauge.

THE SIGNATURE (the closed-family ruling): the plan's
"SeriesSet(factors) → Series(residual)" cannot be honoured literally —
the loadings do NOT fit the closed artifact family, so they cannot flow
between operators.  The honest, closed-family-respecting design re-fits
PCA INTERNALLY from the Panel (it reuses ``fit_pca``, never duplicating
the math) and emits one feature's residual.

DESCRIPTIVE, NOT A VERDICT (P5/P12): the residual is the number
``observed − top-k-PC reconstruction``.  A positive residual means the
observed value is ABOVE its PCA-implied level; this operator DISCLOSES
the sign but does NOT adjudicate rich/cheap — naming the richness is the
B2 ``curve_fair_value`` primitive's job.

UNITS (OPR11): the residual is in the TARGET column's ORIGINAL units
(both the observed value and the un-standardised reconstruction are) —
units passthrough from ``Panel.units_by_column[target_column]``, NOT
FACTOR_LEVEL (that is the factor scores' unit).

DETERMINISM (OPR14): the residual is SIGN-INVARIANT (a PC sign flip
flips both scores and loadings, cancelling), so it does NOT depend on
the PCA sign convention; the feature columns are still sorted into a
canonical order so the SVD input is reproducible.

LOOK-AHEAD (P5): the loadings are fit over the WHOLE Panel, so the
residual at each date uses full-sample loadings — disclosed as
``fit_scope='full_sample'``; never feed into point-in-time
compositions.

NaN POLICY (ORDER-INSENSITIVE — the pca_decompose precedent): a row
with a NaN in ANY feature is DROPPED (complete-case); the loadings are
fit on the remaining rows and the residual is computed there; the
output carries a NaN on the dropped dates, aligned to the FULL Panel
index.

Contract highlights (cite by OPR-number):

  - OPR1/OPR6 : one finance-blind linear-factor residual; zero finance
    vocabulary; no rich/cheap verdict.
  - OPR2      : ONE output artifact type — always a ``Series`` (the
    target's residual; an all-columns SeriesSet is a separate
    operator).
  - OPR8      : n_components + target_column required (no honest
    defaults); the PCA locks reused from the pca_decompose twin.
  - OPR9      : one ``Panel`` in, one ``Series`` out.
  - OPR10     : one ``OperatorStep``; the loadings + explained-variance
    + the target + the locks + the scope + the drop count ride in
    params.
  - OPR11     : units passthrough (the target column's units).
  - OPR13     : typed ``ReconstructFromFactorsError`` refusals:
    non-Panel input; missing params; target_column not in the Panel;
    fewer than 2 feature columns; n_components out of [1, n_features];
    fewer than max(12, n_features+1) complete rows; a zero-variance
    column; a rank-deficient component.
  - OPR14     : pure + rerun-deterministic.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

from shared.artifacts.lineage import OperatorStep, sanitize_params_for_lineage
from shared.artifacts.missingness import RawNoCleaning
from shared.artifacts.types import Panel, Series
from shared.config.operator_config import (
    OperatorConfig,
    _check_config_identity,
    load_operator_config,
)
from shared.operators.reconstruct_from_factors.schemas import (
    ReconstructFromFactorsParams,
)
from shared.quant.pca import fit_pca, reconstruct_residual


_OPERATOR_NAME = "reconstruct_from_factors"
_OPERATOR_VERSION = "1.0.0"

_CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"

_MIN_FEATURES = 2
_FAMILY_FLOOR = 12


class ReconstructFromFactorsError(ValueError):
    """Raised by ``reconstruct_from_factors`` on a recoverable
    user-facing failure (a ``ValueError`` subclass, OPR13)."""


def reconstruct_from_factors(
    features: Panel,
    *,
    params: Optional[ReconstructFromFactorsParams] = None,
    config: Optional[OperatorConfig] = None,
) -> Series:
    """PCA fair-value residual of one feature of a ``Panel``.

    Parameters
    ----------
    features :
        The input ``Panel`` (dates x feature-columns; >= 2 features).
        Rows with any NaN feature are dropped (complete-case); the
        residual carries a NaN on those dates.
    params :
        ``ReconstructFromFactorsParams`` — REQUIRED (n_components and
        target_column have no honest default).
    config :
        Optional ``OperatorConfig``.  When ``None`` the bundled
        ``config.yaml`` is loaded (process-cached).

    Returns
    -------
    Series
        The target column's residual (observed − the top-k-PC
        reconstruction) on the FULL Panel index, in the target column's
        units (series_key pca_residual__<target>); the loadings,
        explained-variance ratios and the locks ride in lineage.

    Raises
    ------
    ReconstructFromFactorsError
        Missing params; a non-Panel input; target_column not in the
        Panel; fewer than 2 feature columns; n_components out of
        [1, n_features]; fewer than max(12, n_features+1) complete
        rows; a zero-variance column; or a rank-deficient component.
    """
    # ------------------------------------------------------------------
    # 1. Load config + identity check — OPR12.
    # ------------------------------------------------------------------
    if config is None:
        config = load_operator_config(_CONFIG_PATH)
    _check_config_identity(config, _OPERATOR_NAME, _OPERATOR_VERSION)

    # ------------------------------------------------------------------
    # 2. n_components + target_column are methodology — params=None
    #    refuses naming them (the pca_decompose precedent; OPR8).
    # ------------------------------------------------------------------
    if params is None:
        raise ReconstructFromFactorsError(
            "reconstruct_from_factors requires explicit params with "
            "n_components (the number of components defining the "
            "reconstruction) and target_column (the feature whose "
            "residual to emit) — both are methodology choices with no "
            "honest default."
        )
    n_components = int(params.n_components)
    target_column = str(params.target_column)

    # ------------------------------------------------------------------
    # 3. Structural-input validation (OPR9 typed I/O).
    # ------------------------------------------------------------------
    if not isinstance(features, Panel):
        raise ReconstructFromFactorsError(
            "reconstruct_from_factors: input must be a Panel artifact; "
            f"got {type(features).__name__}."
        )

    frame = features.payload
    columns = sorted(str(c) for c in frame.columns)
    n_features = len(columns)
    if n_features < _MIN_FEATURES:
        raise ReconstructFromFactorsError(
            f"reconstruct_from_factors: needs at least {_MIN_FEATURES} "
            f"feature columns (got {n_features})."
        )
    if target_column not in columns:
        raise ReconstructFromFactorsError(
            f"reconstruct_from_factors: target_column "
            f"{target_column!r} is not one of the Panel's columns "
            f"({columns})."
        )
    if n_components > n_features:
        raise ReconstructFromFactorsError(
            f"reconstruct_from_factors: n_components ({n_components}) "
            f"cannot exceed the number of feature columns "
            f"({n_features})."
        )

    full_index = frame.index
    n_total = int(len(full_index))
    sorted_frame = frame[columns]
    complete_mask = sorted_frame.notna().all(axis=1).to_numpy()
    n_complete = int(complete_mask.sum())
    n_dropped = n_total - n_complete

    floor = max(_FAMILY_FLOOR, n_features + 1)
    if n_complete < floor:
        raise ReconstructFromFactorsError(
            f"reconstruct_from_factors: needs at least max(12, "
            f"n_features+1) = {floor} complete-case rows (got "
            f"{n_complete} after dropping {n_dropped} NaN row(s))."
        )

    x = sorted_frame.to_numpy(dtype=float)[complete_mask]

    # ------------------------------------------------------------------
    # 4. Fit + reconstruct (shared/quant; design-locked correlation
    #    PCA).  A zero-variance column / rank deficiency refuses inside.
    # ------------------------------------------------------------------
    target_index = columns.index(target_column)
    try:
        fit = fit_pca(x, n_components)
        residual = reconstruct_residual(fit, x, target_index)
    except ValueError as exc:
        raise ReconstructFromFactorsError(
            f"reconstruct_from_factors: {exc}"
        ) from exc

    # ------------------------------------------------------------------
    # 5. Build the output Series — the residual on the FULL Panel index;
    #    dropped dates carry a NaN residual.  Units passthrough.
    # ------------------------------------------------------------------
    out_values = np.full(n_total, np.nan, dtype=float)
    out_values[complete_mask] = residual
    out_key = f"pca_residual__{target_column}"
    out_payload = pd.Series(out_values, index=full_index, dtype=float)
    out_payload.name = out_key
    target_units = features.units_by_column[target_column]

    # ------------------------------------------------------------------
    # 6. Lineage — one OperatorStep (OPR10); the PCA fit + the target.
    # ------------------------------------------------------------------
    step_params: Dict[str, Any] = {
        "n_components": n_components,
        "target_column": target_column,
        "loadings": [[float(v) for v in row] for row in fit.loadings],
        "explained_variance_ratio": [
            float(v) for v in fit.explained_variance_ratio
        ],
        "singular_values": [float(v) for v in fit.singular_values],
        "near_degenerate": bool(fit.near_degenerate),
        "residual_definition": "observed_minus_topk_reconstruction",
        "reconstruction_units": target_units.value,
        "pca_kind": "correlation",                # design-locked (OPR7)
        "feature_standardization": "zscore_per_column",
        "sign_convention": "largest_abs_loading_positive",
        "algorithm": "svd",                        # design-locked (OPR7)
        "fit_scope": "full_sample",                # LOOK-AHEAD (P5)
        "n_features": n_features,
        "feature_columns": columns,
        "n_obs": n_total,
        "n_complete_rows": n_complete,
        "n_dropped_rows": n_dropped,
    }
    step = OperatorStep.build(
        name=_OPERATOR_NAME,
        version=_OPERATOR_VERSION,
        params=sanitize_params_for_lineage(step_params),
        input_hashes=(features.lineage.head_hash,),
    )

    return Series(
        series_key=out_key,
        payload=out_payload,
        units=target_units,
        frequency=None,  # a Panel carries no frequency tag
        missingness_policy=RawNoCleaning(),  # a freshly-computed residual
        lineage=features.lineage.append(step),
    )


__all__ = ["reconstruct_from_factors", "ReconstructFromFactorsError"]
