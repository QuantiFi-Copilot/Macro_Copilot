"""pca_decompose — principal-component factor scores of a Panel.

Finance-blind ``cross_sectional`` operator and an A4 model-fit engine.
Decomposes a feature Panel into its principal-component FACTOR-SCORE
time series via correlation PCA (the SVD math lives in
``shared/quant/pca.py``, the seventh module there — no library) and
emits them as a SeriesSet (one member Series per component: pc1, pc2,
...).  The second Panel-consuming operator (after fit_regime_gmm) and
the first Panel→SeriesSet bridge.

This is the finance-BLIND generalisation of the rates yield-curve PCA
to ANY feature Panel — the code would not change for FX or equity
features.  The finance (which features, naming PC1 "level") is the B2
primitive's job; this operator never anchors on a tenor/curve position.

The loadings (eigenvectors) and explained-variance ratios do NOT fit
the closed artifact family, so they ride in LINEAGE only (NOT a second
output — the fit_regime_gmm means-in-lineage precedent).  The residual
/ fair-value reconstruction is a SEPARATE operator
(``reconstruct_from_factors``, a declared planned extension).

THE SIGN CONVENTION (OPR14 — determinism): eigenvector signs are
arbitrary, so each component is flipped so its LARGEST-|loading| element
is positive (ties broken by the lowest column index) — finance-blind
(no "longest-tenor" anchor), making the factor scores byte-identical
and sign-stable across reruns.  Near-tied singular values (a non-unique
subspace) are DISCLOSED via ``near_degenerate`` in lineage, not refused.

STANDARDISATION (OPR7, design-locked): CORRELATION PCA — each feature
column is z-scored before the SVD (so one large-scale feature cannot
dominate, the right default for heterogeneous features).  Covariance
PCA (demean-only) is a declared planned extension.

LOOK-AHEAD (P5): the loadings are fit over the WHOLE Panel, so each
date's factor score uses full-sample loadings — disclosed as
``fit_scope='full_sample'``; never feed into point-in-time
compositions (``rolling_pca`` — a windowed re-fit — is a planned
extension).

NaN POLICY (ORDER-INSENSITIVE — the per-date projection is not a
sequential recursion): a row with a NaN in ANY feature is DROPPED
(complete-case); the loadings are fit on the remaining rows, every
complete row is projected, and each factor Series carries a NaN on the
dropped dates, aligned to the FULL Panel index (the fit_regime_gmm
precedent).

Contract highlights (cite by OPR-number):

  - OPR1/OPR6 : one finance-blind linear-factor model; zero finance
    vocabulary (no tenor/curve sign anchor; keys pc1..pcK).
  - OPR2      : ONE output artifact type — always a ``SeriesSet`` (the
    factor scores; the loadings ride in lineage).
  - OPR8      : n_components required (no honest default); the
    standardisation/sign/estimator locks documented + stamped.
  - OPR9      : one ``Panel`` in, one ``SeriesSet`` out.
  - OPR10     : one ``OperatorStep``; loadings + explained-variance +
    the locks + the scope + the drop count ride in params.
  - OPR11     : FACTOR_LEVEL units for every member (eigen-units).
  - OPR13     : typed ``PcaDecomposeError`` refusals: non-Panel input;
    missing params (n_components); fewer than 2 feature columns;
    n_components out of [1, n_features]; fewer than
    max(12, n_features+1) complete rows; a zero-variance feature
    column; a rank-deficient retained component.
  - OPR14     : pure + rerun-deterministic (canonical sign; sorted
    columns).
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
from shared.operators.pca_decompose.schemas import PcaDecomposeParams
from shared.quant.pca import fit_pca


_OPERATOR_NAME = "pca_decompose"
_OPERATOR_VERSION = "1.0.0"

_CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"

_MIN_FEATURES = 2
_FAMILY_FLOOR = 12


class PcaDecomposeError(ValueError):
    """Raised by ``pca_decompose`` on a recoverable user-facing failure
    (a ``ValueError`` subclass, OPR13)."""


def pca_decompose(
    features: Panel,
    *,
    params: Optional[PcaDecomposeParams] = None,
    config: Optional[OperatorConfig] = None,
) -> SeriesSet:
    """Principal-component factor scores of a feature ``Panel``.

    Parameters
    ----------
    features :
        The input ``Panel`` (dates x feature-columns; >= 2 features).
        Rows with any NaN feature are dropped (complete-case); the
        factor Series carry a NaN on those dates.
    params :
        ``PcaDecomposeParams`` — REQUIRED (n_components has no honest
        default).
    config :
        Optional ``OperatorConfig``.  When ``None`` the bundled
        ``config.yaml`` is loaded (process-cached).

    Returns
    -------
    SeriesSet
        The factor-score Series (keys pc1..pcK) on the FULL Panel
        index, every member in FACTOR_LEVEL units; the loadings,
        explained-variance ratios and the locks ride in lineage.

    Raises
    ------
    PcaDecomposeError
        Missing params (n_components required); a non-Panel input;
        fewer than 2 feature columns; n_components out of
        [1, n_features]; fewer than max(12, n_features+1) complete
        rows; a zero-variance feature column; or a rank-deficient
        retained component.
    """
    # ------------------------------------------------------------------
    # 1. Load config + identity check — OPR12.
    # ------------------------------------------------------------------
    if config is None:
        config = load_operator_config(_CONFIG_PATH)
    _check_config_identity(config, _OPERATOR_NAME, _OPERATOR_VERSION)

    # ------------------------------------------------------------------
    # 2. n_components is methodology — params=None refuses naming it
    #    (the fit_regime_gmm/changepoint precedent; OPR8).
    # ------------------------------------------------------------------
    if params is None:
        raise PcaDecomposeError(
            "pca_decompose requires explicit params with n_components "
            "(the number of principal components to retain) — it is "
            "the central methodology choice with no honest default."
        )
    n_components = int(params.n_components)

    # ------------------------------------------------------------------
    # 3. Structural-input validation (OPR9 typed I/O).
    # ------------------------------------------------------------------
    if not isinstance(features, Panel):
        raise PcaDecomposeError(
            "pca_decompose: input must be a Panel artifact; got "
            f"{type(features).__name__}."
        )

    frame = features.payload
    # Canonical (sorted) column order so the SVD input + the sign
    # tie-break are reproducible regardless of the input column order.
    columns = sorted(str(c) for c in frame.columns)
    n_features = len(columns)
    if n_features < _MIN_FEATURES:
        raise PcaDecomposeError(
            f"pca_decompose: needs at least {_MIN_FEATURES} feature "
            f"columns (got {n_features})."
        )
    if n_components > n_features:
        raise PcaDecomposeError(
            f"pca_decompose: n_components ({n_components}) cannot exceed "
            f"the number of feature columns ({n_features})."
        )

    full_index = frame.index
    n_total = int(len(full_index))
    sorted_frame = frame[columns]
    complete_mask = sorted_frame.notna().all(axis=1).to_numpy()
    n_complete = int(complete_mask.sum())
    n_dropped = n_total - n_complete

    floor = max(_FAMILY_FLOOR, n_features + 1)
    if n_complete < floor:
        raise PcaDecomposeError(
            f"pca_decompose: needs at least max(12, n_features+1) = "
            f"{floor} complete-case rows (got {n_complete} after "
            f"dropping {n_dropped} NaN row(s)) — PCA needs more rows "
            "than features for a full-rank correlation."
        )

    x = sorted_frame.to_numpy(dtype=float)[complete_mask]

    # ------------------------------------------------------------------
    # 4. The decomposition (shared/quant; design-locked correlation PCA
    #    + SVD + canonical sign).  A zero-variance column / rank
    #    deficiency refuses inside the quant.
    # ------------------------------------------------------------------
    try:
        result = fit_pca(x, n_components)
    except ValueError as exc:
        raise PcaDecomposeError(f"pca_decompose: {exc}") from exc

    # ------------------------------------------------------------------
    # 5. Lineage — ONE OperatorStep (OPR10); the loadings + variance +
    #    locks; reused for the set lineage and every member's upstream.
    # ------------------------------------------------------------------
    keys = [f"pc{k + 1}" for k in range(n_components)]
    step_params: Dict[str, Any] = {
        "n_components": n_components,
        "loadings": [[float(v) for v in row] for row in result.loadings],
        "explained_variance_ratio": [
            float(v) for v in result.explained_variance_ratio
        ],
        "singular_values": [float(v) for v in result.singular_values],
        "near_degenerate": bool(result.near_degenerate),
        "pca_kind": "correlation",                # design-locked (OPR7)
        "feature_standardization": "zscore_per_column",
        "sign_convention": "largest_abs_loading_positive",
        "algorithm": "svd",                        # design-locked (OPR7)
        "fit_scope": "full_sample",                # LOOK-AHEAD (P5)
        "output_keys": keys,
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
    member_lineage = features.lineage.append(step)

    # ------------------------------------------------------------------
    # 6. Build the output SeriesSet — one factor Series per component on
    #    the FULL Panel index; dropped dates carry a NaN factor score.
    # ------------------------------------------------------------------
    # series_by_key holds RAW pandas Series (the payloads); the per-key
    # units/missingness/upstream-lineage ride in the parallel dicts (the
    # align_series SeriesSet convention).
    series_by_key: Dict[str, pd.Series] = {}
    units_by_key: Dict[str, TimeSeriesUnits] = {}
    missingness_by_key: Dict[str, RawNoCleaning] = {}
    upstream_lineage_by_key: Dict[str, Any] = {}
    for k, key in enumerate(keys):
        col_values = np.full(n_total, np.nan, dtype=float)
        col_values[complete_mask] = result.factor_scores[:, k]
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


__all__ = ["pca_decompose", "PcaDecomposeError"]
