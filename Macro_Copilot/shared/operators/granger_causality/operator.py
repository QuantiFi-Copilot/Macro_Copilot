"""granger_causality — the Granger F-test between two Series.

Finance-blind ``statistical_relationship`` operator.  Runs the
single-equation Granger F-test: do the lagged values of ``left``
improve an OLS of ``right`` on its own lagged values?  Two nested
least-squares fits over the overlapping complete-case rows —

  restricted   : right_t ~ const + right_{t−1..t−p}
  unrestricted : right_t ~ const + right_{t−1..t−p} + left_{t−1..t−p}

— and the classical F statistic

  F = ((SSR_r − SSR_u) / p) / (SSR_u / (n − 2p − 1))

is emitted as ONE ``ScalarMetric``, with the p-value and degrees of
freedom recorded in lineage.  Mirroring ``cointegration``: the
substrate does NOT auto-translate the statistic into a yes/no — the
answer layer interprets it (a larger F / smaller p-value is stronger
evidence that left's history adds explanatory power).

SCOPE (plan §4): this is a DESCRIPTIVE temporal-association hypothesis
test on historical data — "did left's past values carry incremental
information about right's realized values" — not a forecast and not a
causal proof.  "Granger" is the statistic's standard name (like
"beta"); the operator makes no predictive or causal claim.

NOT commutative: granger_causality(left, right) tests left → right;
swapping the arms tests the reverse direction and generally yields a
different statistic.

Contract highlights (cite by OPR-number):

  - OPR1/OPR6 : one finance-blind statistical method; lags are ROWS of
    the shared index; zero finance vocabulary or math.
  - OPR2      : ONE output artifact type — always a ``ScalarMetric``
    (the F statistic).
  - OPR8      : ``params: Optional[GrangerCausalityParams] = None``;
    defaults resolve from ``config.yaml``.
  - OPR9      : two ``Series`` in, one ``ScalarMetric`` out.
  - OPR10     : one ``OperatorStep`` via ``.build``; left is the
    auxiliary chain (the output is anchored on the RESPONSE series,
    ``right``); F, p_value, df1, df2, n_obs, n_lags and both inputs'
    units recorded in params.
  - OPR11     : strict-by-default on frequency + missingness;
    unit-INVARIANT (an F statistic is dimensionless) — output RATIO.
  - OPR13     : every recoverable failure raises
    ``GrangerCausalityError`` (a ``ValueError`` subclass):
    insufficient complete-case rows (the df2 ≥ 1 floor is a
    mathematical invariant of the lag order, enforced in code), a
    zero-variance arm, an ill-conditioned design, or a non-finite
    statistic.
  - OPR12/14  : config name+version identity checked; pure + rerun-
    deterministic.

Composition contract: ``granger_causality`` does NOT align.  The two
Series must already share an identical ``DatetimeIndex`` — align
upstream with ``align_series``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy import stats as _scipy_stats

from shared.artifacts.lineage import OperatorStep, sanitize_params_for_lineage
from shared.artifacts.types import ScalarMetric, Series
from shared.artifacts.units import TimeSeriesUnits
from shared.config.operator_config import (
    OperatorConfig,
    _check_config_identity,
    load_operator_config,
)
from shared.operators.granger_causality.schemas import GrangerCausalityParams


_OPERATOR_NAME = "granger_causality"
_OPERATOR_VERSION = "1.0.0"

_CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


class GrangerCausalityError(ValueError):
    """Raised by ``granger_causality`` on a recoverable user-facing
    failure (a ``ValueError`` subclass, OPR13)."""


def _lag_matrix(arr: np.ndarray, p: int) -> np.ndarray:
    """Columns [arr_{t-1}, ..., arr_{t-p}] for t = p..n-1 (rows n-p)."""
    return np.column_stack([arr[p - j - 1: len(arr) - j - 1] for j in range(p)])


def granger_causality(
    left: Series,
    right: Series,
    *,
    params: Optional[GrangerCausalityParams] = None,
    config: Optional[OperatorConfig] = None,
) -> ScalarMetric:
    """Granger F-test of whether ``left``'s lags improve ``right``'s fit.

    Parameters
    ----------
    left :
        The candidate DRIVER ``Series`` — the test asks whether ITS
        lagged values add explanatory power for ``right``.
    right :
        The RESPONSE ``Series`` being explained.  NOT interchangeable
        with ``left`` (swapping tests the reverse direction).  Must
        share an identical ``DatetimeIndex`` with ``left`` (align
        upstream with ``align_series``).
    params :
        Optional ``GrangerCausalityParams``.  When ``None`` every
        field is resolved from the bundled ``config.yaml`` (OPR8).
    config :
        Optional ``OperatorConfig``.  When ``None`` the bundled
        ``config.yaml`` is loaded (process-cached).

    Returns
    -------
    ScalarMetric
        The F statistic (dimensionless, ``RATIO`` units), with
        p_value / df1 / df2 / n_obs / n_lags and both inputs' units
        recorded in lineage.

    Raises
    ------
    GrangerCausalityError
        Inputs are not ``Series``; indexes differ; frequency or
        missingness mismatch under strict mode; too few complete-case
        rows for the lag order (df2 = n − 2p − 1 must be ≥ 1); a
        zero-variance arm; an ill-conditioned design; or a non-finite
        statistic.
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
        params = GrangerCausalityParams(
            n_lags=int(config.default_value("n_lags")),
            condition_number_threshold=float(
                config.default_value("condition_number_threshold")
            ),
        )

    # ------------------------------------------------------------------
    # 3. Structural-input validation (OPR9 typed I/O; OPR11 metadata).
    # ------------------------------------------------------------------
    if not isinstance(left, Series) or not isinstance(right, Series):
        raise GrangerCausalityError(
            "granger_causality: both inputs must be Series artifacts; "
            f"got left={type(left).__name__}, right={type(right).__name__}."
        )

    if not left.payload.index.equals(right.payload.index):
        raise GrangerCausalityError(
            "granger_causality: left and right Series must share an "
            "identical DatetimeIndex.  Align upstream with align_series "
            f"first (left.len={len(left.payload)}, "
            f"right.len={len(right.payload)})."
        )

    if params.require_matching_frequency and left.frequency != right.frequency:
        raise GrangerCausalityError(
            f"granger_causality: incompatible frequencies "
            f"left={left.frequency!r} vs right={right.frequency!r}.  Pass "
            "require_matching_frequency=False to opt into mixed "
            "frequencies explicitly."
        )

    if params.require_matching_missingness:
        left_sig = json.dumps(
            left.missingness_policy.model_dump(mode="json"),
            sort_keys=True, separators=(",", ":"),
        )
        right_sig = json.dumps(
            right.missingness_policy.model_dump(mode="json"),
            sort_keys=True, separators=(",", ":"),
        )
        if left_sig != right_sig:
            raise GrangerCausalityError(
                "granger_causality: incompatible missingness policies "
                "left vs right.  Pass require_matching_missingness=False "
                "to opt into mixed policies explicitly."
            )

    # NOTE (OPR11): unit-INVARIANT — an F statistic is dimensionless;
    # both units are recorded in lineage; the output tag is RATIO.

    # ------------------------------------------------------------------
    # 4. Build the nested designs on complete-case rows.
    #    Lags are taken on the SHARED index first; rows where the
    #    response or ANY required lag is NaN are dropped (complete-case
    #    across all regressors — the standard Granger construction).
    # ------------------------------------------------------------------
    p = int(params.n_lags)
    y_full = right.payload.to_numpy(dtype=float)
    x_full = left.payload.to_numpy(dtype=float)
    n_raw = len(y_full)
    if n_raw <= p:
        raise GrangerCausalityError(
            f"granger_causality: only {n_raw} input row(s) for lag "
            f"order n_lags={p}; need at least 2*n_lags + 2 = {2 * p + 2} "
            "complete-case rows (df2 = n - 2p - 1 >= 1)."
        )

    y_t = y_full[p:]
    y_lags = _lag_matrix(y_full, p)
    x_lags = _lag_matrix(x_full, p)

    complete = (
        np.isfinite(y_t)
        & np.isfinite(y_lags).all(axis=1)
        & np.isfinite(x_lags).all(axis=1)
    )
    n_obs = int(complete.sum())
    df1 = p
    df2 = n_obs - 2 * p - 1
    if df2 < 1:
        raise GrangerCausalityError(
            f"granger_causality: only {n_obs} complete-case row(s) for "
            f"lag order n_lags={p}; the F-test needs "
            f"df2 = n - 2p - 1 >= 1, i.e. at least {2 * p + 2} rows.  "
            "Increase the input lookback or lower n_lags."
        )

    y = y_t[complete]
    yl = y_lags[complete]
    xl = x_lags[complete]

    # Zero-variance arms make the designs rank-deficient; lstsq would
    # silently return a least-norm fit and a meaningless F.
    if np.nanmax(y) - np.nanmin(y) == 0.0:
        raise GrangerCausalityError(
            "granger_causality: right (the response) has zero variance "
            "over the complete-case window — the test is undefined."
        )
    if np.nanmax(xl) - np.nanmin(xl) == 0.0:
        raise GrangerCausalityError(
            "granger_causality: left (the candidate driver) has zero "
            "variance over the complete-case window — its lags carry no "
            "information and the test is undefined."
        )

    ones = np.ones((n_obs, 1))
    design_r = np.hstack([ones, yl])
    design_u = np.hstack([ones, yl, xl])

    cond = float(np.linalg.cond(design_u))
    if cond > params.condition_number_threshold:
        raise GrangerCausalityError(
            f"granger_causality: unrestricted design-matrix condition "
            f"number {cond:.3e} exceeds the threshold "
            f"{params.condition_number_threshold:.1e}; the F statistic "
            "would be numerically meaningless (collinear lags?)."
        )

    coef_r, _, _, _ = np.linalg.lstsq(design_r, y, rcond=None)
    coef_u, _, _, _ = np.linalg.lstsq(design_u, y, rcond=None)
    ssr_r = float(np.sum((y - design_r @ coef_r) ** 2))
    ssr_u = float(np.sum((y - design_u @ coef_u) ** 2))

    # A (numerically) perfect unrestricted fit makes the F ratio
    # unbounded/meaningless — honest refusal rather than an astronomical
    # statistic (OPR14).  The threshold is machine precision relative to
    # the restricted model's scale (a numerical invariant, not a
    # methodology choice): float noise leaves SSR at ~eps·scale even
    # when the data is an exact deterministic function of the lags.
    if ssr_u <= np.finfo(float).eps * max(ssr_r, 1.0) * n_obs:
        raise GrangerCausalityError(
            "granger_causality: the unrestricted model fits the response "
            "(numerically) perfectly — the F statistic is unbounded; the "
            "inputs are deterministic functions of each other."
        )

    # Nested models guarantee SSR_r >= SSR_u up to float noise; clamp
    # the difference at 0 so noise cannot produce a tiny negative F.
    f_stat = max(ssr_r - ssr_u, 0.0) / df1 / (ssr_u / df2)
    if not np.isfinite(f_stat):
        raise GrangerCausalityError(
            "granger_causality: computed a non-finite F statistic; the "
            "inputs are numerically degenerate."
        )
    p_value = float(_scipy_stats.f.sf(f_stat, df1, df2))

    # ------------------------------------------------------------------
    # 5. Lineage — one OperatorStep.  The output is anchored on the
    #    RESPONSE series (right); the candidate driver (left) is the
    #    auxiliary chain (OPR10).
    # ------------------------------------------------------------------
    step_params: Dict[str, Any] = {
        "n_lags": p,
        "condition_number_threshold": params.condition_number_threshold,
        "f_statistic": float(f_stat),
        "p_value": p_value,
        "df1": df1,
        "df2": df2,
        "n_obs": n_obs,
        "direction": (
            "tests whether left's lagged values improve the OLS of "
            "right on right's own lagged values (left -> right)"
        ),
        "left_units": left.units.value,
        "right_units": right.units.value,
        "left_series_key": left.series_key,
        "right_series_key": right.series_key,
        "require_matching_frequency": params.require_matching_frequency,
        "require_matching_missingness": params.require_matching_missingness,
    }
    op_step = OperatorStep.build(
        name=_OPERATOR_NAME,
        version=_OPERATOR_VERSION,
        params=sanitize_params_for_lineage(step_params),
        input_hashes=(right.lineage.head_hash, left.lineage.head_hash),
        auxiliary_lineages=(left.lineage,),
    )
    out_lineage = right.lineage.append(op_step)

    return ScalarMetric(
        metric_key=f"granger__{left.series_key}__{right.series_key}",
        value=float(f_stat),
        units=TimeSeriesUnits.RATIO,
        lineage=out_lineage,
    )


__all__ = ["granger_causality", "GrangerCausalityError"]
