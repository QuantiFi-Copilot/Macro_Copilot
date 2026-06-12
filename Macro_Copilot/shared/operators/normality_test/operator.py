"""normality_test — Jarque–Bera normality test of a Series.

Finance-blind ``statistical_relationship`` operator (the A5
diagnostics family; ``stationarity_adf`` set the conventions).  Runs
the Jarque–Bera moment-based normality test
(``statsmodels.stats.stattools.jarque_bera``) on one Series and emits
the JB STATISTIC as a ``ScalarMetric`` in RATIO units (the family's
emit-the-statistic convention).  Larger = stronger evidence against
normality; the p-value, the sample skewness and the sample kurtosis
ride in lineage.

KURTOSIS CONVENTION (named, not silently inherited — the
rolling_statistic excess-kurtosis lesson in reverse): statsmodels'
``jarque_bera`` reports RAW kurtosis (normal == 3), NOT the Fisher
excess form rolling_statistic emits (normal == 0).  The lineage field
is named ``kurtosis_raw`` so the two can never be confused.

DESIGN LOCK (OPR7): Jarque–Bera is the single canonical test —
moment-based, asymptotically χ²(2).  Anderson–Darling / Shapiro–Wilk
answer the same question through different statistics and are declared
planned extensions, never silent alternates.

NaN POLICY (A5 family ruling): moments are ORDER-INSENSITIVE, so plain
``dropna`` is exact (no adjacency to splice — unlike the lag-based
siblings, no interior-drop audit is owed); ``n_dropped`` rides in
lineage.

Contract highlights (cite by OPR-number):

  - OPR1/OPR6 : one finance-blind classical test; zero finance
    vocabulary.
  - OPR2      : ONE output artifact type — always a ``ScalarMetric``.
  - OPR8      : ``params: Optional[...] = None`` (zero knobs; the
    lock documented + stamped).
  - OPR9      : one ``Series`` in, one ``ScalarMetric`` out.
  - OPR10     : one ``OperatorStep``; statistic + p-value + skewness
    + raw kurtosis + drop count ride in params.
  - OPR11     : RATIO units (a χ²-type statistic is dimensionless);
    input units recorded in lineage.
  - OPR13     : typed ``NormalityTestError`` refusals: non-Series
    input; fewer than 3 finite observations (skewness/kurtosis need
    3 — the family floor; the χ² asymptotics' small-sample weakness
    is disclosed in lineage via n_obs); zero variance (moments
    degenerate — statsmodels emits NaN); a non-finite statistic.
  - OPR14     : pure + rerun-deterministic.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
from statsmodels.stats.stattools import jarque_bera

from shared.artifacts.lineage import OperatorStep, sanitize_params_for_lineage
from shared.artifacts.types import ScalarMetric, Series
from shared.artifacts.units import TimeSeriesUnits
from shared.config.operator_config import (
    OperatorConfig,
    _check_config_identity,
    load_operator_config,
)
from shared.operators.normality_test.schemas import NormalityTestParams


_OPERATOR_NAME = "normality_test"
_OPERATOR_VERSION = "1.0.0"

_CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"

# A5 family floor: the third and fourth sample moments need at least
# 3 observations (statsmodels accepts n=3; the χ²(2) asymptotics are
# weak at tiny n — n_obs in lineage discloses the sample size).
_MIN_OBS = 3


class NormalityTestError(ValueError):
    """Raised by ``normality_test`` on a recoverable user-facing
    failure (a ``ValueError`` subclass, OPR13)."""


def normality_test(
    series: Series,
    *,
    params: Optional[NormalityTestParams] = None,
    config: Optional[OperatorConfig] = None,
) -> ScalarMetric:
    """Jarque–Bera statistic of ``series``.

    Parameters
    ----------
    series :
        The input ``Series`` (any units; the statistic is
        dimensionless).
    params :
        Optional ``NormalityTestParams`` (no fields in v1).  ``None``
        is equivalent.
    config :
        Optional ``OperatorConfig``.  When ``None`` the bundled
        ``config.yaml`` is loaded (process-cached).

    Returns
    -------
    ScalarMetric
        The JB statistic (larger = stronger evidence against
        normality) in RATIO units; the p-value, sample skewness, raw
        kurtosis (normal == 3) and the NaN-drop count ride in lineage.

    Raises
    ------
    NormalityTestError
        The input is not a ``Series``; fewer than 3 finite
        observations; zero variance; or a non-finite statistic.
    """
    # ------------------------------------------------------------------
    # 1. Load config + identity check — OPR12.
    # ------------------------------------------------------------------
    if config is None:
        config = load_operator_config(_CONFIG_PATH)
    _check_config_identity(config, _OPERATOR_NAME, _OPERATOR_VERSION)

    # ------------------------------------------------------------------
    # 2. Resolve params — OPR8 (no fields in v1).
    # ------------------------------------------------------------------
    if params is None:
        params = NormalityTestParams()

    # ------------------------------------------------------------------
    # 3. Structural-input validation (OPR9 typed I/O; family floor).
    # ------------------------------------------------------------------
    if not isinstance(series, Series):
        raise NormalityTestError(
            "normality_test: input must be a Series artifact; got "
            f"{type(series).__name__}."
        )

    payload = series.payload
    cleaned = payload.dropna()
    n_total = int(len(payload))
    n_obs = int(len(cleaned))
    n_dropped = n_total - n_obs
    if n_obs < _MIN_OBS:
        raise NormalityTestError(
            f"normality_test: needs at least {_MIN_OBS} finite "
            f"observations (got {n_obs}) — the third and fourth "
            "sample moments are undefined below that."
        )

    values = cleaned.to_numpy(dtype=float)
    if float(np.var(values)) == 0.0:
        raise NormalityTestError(
            "normality_test: the input has zero variance — the "
            "standardised moments are degenerate on a constant series."
        )

    # ------------------------------------------------------------------
    # 4. The test (statsmodels; moments are order-insensitive).
    # ------------------------------------------------------------------
    stat, p_value, skewness, kurtosis_raw = jarque_bera(values)
    stat = float(stat)
    if not np.isfinite(stat):
        raise NormalityTestError(
            "normality_test: the test produced a non-finite statistic "
            "(numerical failure)."
        )

    # ------------------------------------------------------------------
    # 5. Lineage — one OperatorStep (OPR10); the moment diagnostics
    #    ride in params with the kurtosis convention NAMED.
    # ------------------------------------------------------------------
    step_params: Dict[str, Any] = {
        "test_statistic": stat,
        "p_value": (
            float(p_value) if np.isfinite(p_value) else None
        ),
        "skewness": (
            float(skewness) if np.isfinite(skewness) else None
        ),
        # RAW kurtosis (normal == 3) — statsmodels' convention, named
        # so it can never be confused with rolling_statistic's Fisher
        # EXCESS form (normal == 0).
        "kurtosis_raw": (
            float(kurtosis_raw) if np.isfinite(kurtosis_raw) else None
        ),
        "test_scope": "full_sample",  # descriptive disclosure (P5)
        "n_obs": n_obs,
        "n_dropped": n_dropped,
        "input_units": series.units.value,
        "input_series_key": series.series_key,
    }
    step = OperatorStep.build(
        name=_OPERATOR_NAME,
        version=_OPERATOR_VERSION,
        params=sanitize_params_for_lineage(step_params),
        input_hashes=(series.lineage.head_hash,),
    )

    return ScalarMetric(
        metric_key=f"normality_test__{series.series_key}",
        value=stat,
        units=TimeSeriesUnits.RATIO,
        lineage=series.lineage.append(step),
    )


__all__ = ["normality_test", "NormalityTestError"]
