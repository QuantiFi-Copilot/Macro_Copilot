"""cointegration — Engle–Granger two-step test between two Series.

A v2.0 finance-blind operator (ADR 0016) in the
``statistical_relationship`` method family.  Emits a single
``ScalarMetric`` — the Engle–Granger ADF test statistic on the
cointegrating regression residuals.

Contract highlights (cite by OPR-number):

  - OPR1/OPR6 : one finance-blind statistical method; zero finance
    vocabulary or math.  The cointegration test is a structural
    relationship between two numeric series — same operator runs on
    rates pairs, FX crosses, equity-sector pairs, or any I(1) pair.
  - OPR2      : ONE output artifact type for every parameter value —
    always a ``ScalarMetric`` (the test stat).  The p-value, n_obs,
    regression beta + intercept, chosen trend / lag / autolag are
    recorded in step.params for diagnostic recovery — they are NOT
    additional outputs that would change the artifact shape.
  - OPR8      : ``params: Optional[CointegrationParams] = None``;
    every default resolves from ``config.yaml`` when omitted; schema
    defaults mirror the YAML.  ``method`` is a closed set
    (``engle_granger`` only in v1); Johansen / Phillips-Ouliaris are
    SEPARATE operators per planned_extensions.
  - OPR9      : two ``Series`` in, one ``ScalarMetric`` out (closed
    family).
  - OPR10     : appends exactly one ``OperatorStep`` via ``.build``;
    the right operand's chain rides in ``auxiliary_lineages``; params
    pass through ``sanitize_params_for_lineage``.
  - OPR11     : strict-by-default on frequency + missingness;
    unit-INVARIANT (test stat is dimensionless).  Both inputs' units
    are recorded in lineage for provenance.
  - OPR13     : every recoverable failure raises ``CointegrationError``
    (a ``ValueError`` subclass); an undefined statistic (rank-
    deficient input, non-finite test stat, < min_periods overlap) is
    a typed refusal — NEVER a NaN/Inf ``ScalarMetric`` (the artifact
    layer rejects those at construction anyway).
  - OPR12/14  : config name+version identity checked; pure +
    rerun-deterministic (same inputs + same params → same
    ``head_hash``).

Composition contract: ``cointegration`` does NOT align.  The two
Series must already share an identical ``DatetimeIndex`` — align
upstream with ``align_series``.
"""

from __future__ import annotations

import json
import warnings
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import numpy as np
import pandas as pd
from statsmodels.regression.linear_model import OLS
from statsmodels.tsa.stattools import coint

from shared.artifacts.lineage import OperatorStep, sanitize_params_for_lineage
from shared.artifacts.types import ScalarMetric, Series
from shared.artifacts.units import TimeSeriesUnits
from shared.config.operator_config import (
    OperatorConfig,
    _check_config_identity,
    load_operator_config,
)
from shared.operators.cointegration.schemas import (
    CointegrationMethod,
    CointegrationParams,
)


_OPERATOR_NAME = "cointegration"
_OPERATOR_VERSION = "1.0.0"

_CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"

# Methods implemented in v1.  Defensive — the schema Literal already
# enforces the closed set, but a Literal extension landed without a
# matching dispatch branch must refuse loudly (OPR8 honest refusal).
_IMPLEMENTED_METHODS: Tuple[CointegrationMethod, ...] = ("engle_granger",)


class CointegrationError(ValueError):
    """Raised by ``cointegration`` on a recoverable user-facing failure
    (a ``ValueError`` subclass, OPR13)."""


def _build_design_matrix(
    right_arr: np.ndarray, trend: str,
) -> np.ndarray:
    """Construct the cointegrating-regression design matrix per the
    chosen trend.  Mirrors statsmodels' internal trend handling so the
    OLS beta we report agrees with the regression coint() ran
    internally."""
    n = len(right_arr)
    if trend == "n":
        return right_arr.reshape(-1, 1)
    if trend == "c":
        return np.column_stack([np.ones(n), right_arr])
    if trend == "ct":
        return np.column_stack([np.ones(n), np.arange(n, dtype=float), right_arr])
    if trend == "ctt":
        t = np.arange(n, dtype=float)
        return np.column_stack([np.ones(n), t, t ** 2, right_arr])
    raise CointegrationError(
        f"cointegration: unsupported trend={trend!r}.  Valid: "
        "{'c', 'ct', 'ctt', 'n'}."
    )


def cointegration(
    left: Series,
    right: Series,
    *,
    params: Optional[CointegrationParams] = None,
    config: Optional[OperatorConfig] = None,
) -> ScalarMetric:
    """Run the Engle–Granger cointegration test on ``left`` and
    ``right``.

    Parameters
    ----------
    left, right :
        The two ``Series`` artifacts.  They MUST share an identical
        ``DatetimeIndex`` (align upstream with ``align_series``).
    params :
        Optional ``CointegrationParams``.  When ``None`` every field
        is resolved from the bundled ``config.yaml`` (OPR8).
    config :
        Optional ``OperatorConfig``.  When ``None`` the bundled
        ``config.yaml`` is loaded (process-cached).

    Returns
    -------
    ScalarMetric
        The ADF test statistic in ``RATIO`` units (dimensionless),
        with metric_key ``coint__{left.series_key}__{right.series_key}``
        and full diagnostic context in the lineage step.

    Raises
    ------
    CointegrationError
        Inputs are not ``Series``; indexes differ; frequency or
        missingness mismatch under strict mode; fewer than
        ``min_periods`` overlapping non-NaN pairs; zero-variance input;
        statsmodels returned a non-finite test statistic (e.g.
        perfectly colinear inputs).
    NotImplementedError
        ``method`` is declared in the valid set but not yet built
        (defensive — not reachable through the schema's Literal).
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
        raw_max_lag = config.default_value("max_lag")
        raw_autolag = config.default_value("autolag")
        params = CointegrationParams(
            method=config.default_value("method"),
            trend=config.default_value("trend"),
            max_lag=(int(raw_max_lag) if raw_max_lag is not None else None),
            autolag=(raw_autolag if raw_autolag is not None else None),
            min_periods=int(config.default_value("min_periods")),
        )

    # ------------------------------------------------------------------
    # 3. Defensive honest refusal for declared-but-unbuilt method
    #    (Literal-bypassing path; not reachable from *Params).
    # ------------------------------------------------------------------
    if params.method not in _IMPLEMENTED_METHODS:
        raise NotImplementedError(
            f"cointegration: method={params.method!r} is declared in "
            "the valid set but not yet implemented.  Implemented "
            f"methods: {list(_IMPLEMENTED_METHODS)}."
        )

    # ------------------------------------------------------------------
    # 4. Structural-input validation (OPR9 typed I/O; OPR11 metadata).
    # ------------------------------------------------------------------
    if not isinstance(left, Series) or not isinstance(right, Series):
        raise CointegrationError(
            "cointegration: both inputs must be Series artifacts; got "
            f"left={type(left).__name__}, right={type(right).__name__}."
        )

    if not left.payload.index.equals(right.payload.index):
        raise CointegrationError(
            "cointegration: left and right Series must share an "
            "identical DatetimeIndex.  Align upstream with align_series "
            f"first (left.len={len(left.payload)}, "
            f"right.len={len(right.payload)})."
        )

    # Frequency — strict by default (OPR11).
    if params.require_matching_frequency and left.frequency != right.frequency:
        raise CointegrationError(
            f"cointegration: incompatible frequencies "
            f"left={left.frequency!r} vs right={right.frequency!r}.  "
            "Pass require_matching_frequency=False to opt into "
            "mixed-frequency cointegration explicitly."
        )

    # Missingness — strict by default (OPR11).  Same JSON-canonical
    # compare as align_series / correlation / rolling_regression.
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
            raise CointegrationError(
                "cointegration: incompatible missingness policies left "
                "vs right.  Pass require_matching_missingness=False to "
                "opt into mixed policies explicitly."
            )

    # NOTE (OPR11): unit-INVARIANT — a cointegration test statistic is
    # dimensionless.  Both units recorded in lineage for provenance;
    # output unit is RATIO.

    # ------------------------------------------------------------------
    # 5. Drop pairwise-NaN rows + enforce min_periods.
    # ------------------------------------------------------------------
    pair = pd.concat(
        [left.payload, right.payload], axis=1, keys=["left", "right"],
    ).dropna()
    n_obs = int(len(pair))
    if n_obs < params.min_periods:
        raise CointegrationError(
            f"cointegration: only {n_obs} overlapping non-NaN pair(s); "
            f"min_periods={params.min_periods} required."
        )

    left_col = pair["left"]
    right_col = pair["right"]

    # Pre-screen for zero-variance — statsmodels raises
    # "Invalid input, x is constant" otherwise.  Catch it here with our
    # typed error so callers see a single error family.
    if left_col.nunique() <= 1 or right_col.nunique() <= 1:
        raise CointegrationError(
            "cointegration: undefined — at least one series has zero "
            "variance over the overlapping window (the cointegrating "
            "regression and ADF residual test are undefined when an "
            "input is constant)."
        )

    # ------------------------------------------------------------------
    # 6. Engle–Granger test via statsmodels.  ``method='aeg'`` is the
    #    only one statsmodels supports today; map our user-facing
    #    ``engle_granger`` to it.
    # ------------------------------------------------------------------
    y0 = left_col.to_numpy(dtype=float)
    y1 = right_col.to_numpy(dtype=float)

    try:
        # statsmodels emits CollinearityWarning + a divide-by-zero
        # RuntimeWarning when inputs are near-perfectly colinear; the
        # finite-test-stat check below converts that into a clean
        # typed refusal so we don't need the warning to escape.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            test_stat, p_value, _crit_values = coint(
                y0=y0, y1=y1,
                trend=params.trend,
                method="aeg",
                maxlag=params.max_lag,
                autolag=params.autolag,
            )
    except ValueError as exc:
        # statsmodels raises bare ValueError for some degenerate paths
        # (e.g. "Invalid input, x is constant").  Translate to the
        # operator's own typed error so the substrate's error envelope
        # treats it uniformly (OPR13 / ERR-1).
        raise CointegrationError(
            f"cointegration: statsmodels rejected the inputs: {exc}"
        ) from exc

    test_stat = float(test_stat)
    p_value = float(p_value)

    if not np.isfinite(test_stat):
        # Near-perfect colinearity yields ``-inf``; the ScalarMetric
        # validator would reject a non-finite value anyway, so we
        # raise our typed error here with a useful message instead of
        # the artifact validator's generic one.
        raise CointegrationError(
            "cointegration: statsmodels returned a non-finite test "
            f"statistic ({test_stat!r}); this typically indicates the "
            "inputs are (near-)perfectly colinear and the test is not "
            "reliable.  Inspect the inputs."
        )
    # A non-finite p-value would equally violate the lineage contract
    # (sanitised to None) but is not the published metric value — record
    # as None when degenerate.
    p_value_for_lineage: Optional[float] = (
        p_value if np.isfinite(p_value) else None
    )

    # ------------------------------------------------------------------
    # 7. Cointegrating regression: re-run OLS to extract the beta and
    #    intercept for diagnostic recovery in lineage.params.
    # ------------------------------------------------------------------
    design = _build_design_matrix(y1, params.trend)
    try:
        ols_fit = OLS(y0, design).fit()
    except Exception as exc:
        raise CointegrationError(
            f"cointegration: OLS regression failed: {exc}"
        ) from exc

    # In every trend specification the cointegrating beta is the LAST
    # coefficient (right_arr is the last column of the design).
    beta = float(ols_fit.params[-1])
    # Intercept: when trend includes 'c' it is the FIRST coefficient;
    # otherwise (trend='n') there is no intercept.
    intercept: Optional[float]
    if params.trend == "n":
        intercept = None
    else:
        intercept = float(ols_fit.params[0])

    # ------------------------------------------------------------------
    # 8. Lineage — one OperatorStep; right's chain in auxiliary_lineages;
    #    params sanitised (finite-or-None) before hashing (OPR10).
    # ------------------------------------------------------------------
    step_params: Dict[str, Any] = {
        "method": params.method,
        "trend": params.trend,
        "max_lag": params.max_lag,
        "autolag": params.autolag,
        "min_periods": params.min_periods,
        "n_obs": n_obs,
        "test_statistic": test_stat,
        "p_value": p_value_for_lineage,
        "regression_beta": beta,
        "regression_intercept": intercept,
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
        input_hashes=(left.lineage.head_hash, right.lineage.head_hash),
        auxiliary_lineages=(right.lineage,),
    )
    out_lineage = left.lineage.append(op_step)

    return ScalarMetric(
        metric_key=f"coint__{left.series_key}__{right.series_key}",
        value=test_stat,
        units=TimeSeriesUnits.RATIO,
        lineage=out_lineage,
    )


__all__ = ["cointegration", "CointegrationError"]
