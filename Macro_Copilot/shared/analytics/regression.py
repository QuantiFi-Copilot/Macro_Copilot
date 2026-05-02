"""
regression.py — Rolling-OLS primitive used by rolling_regression and
beta_adjusted_spread.

Introduced in the v6 sprint by the ``rolling_regression`` tool (the
first new-pattern tool to need a regression primitive).  Lives in
``shared/analytics/`` rather than per-tool because both
``rolling_regression`` and ``beta_adjusted_spread`` (the next sprint
tool) consume the same primitive — keeping a single authoritative
implementation prevents methodology drift between the two tools.

Solver lock
-----------
We use ``numpy.linalg.lstsq(X, y, rcond=None)`` (SVD-based).  The
NORMAL-equation form ``np.linalg.solve(X.T @ X, X.T @ y)`` is NOT used
because it amplifies floating-point error on near-rank-deficient X.
The ``regression_solver`` YAML convention pins this choice; tools
calling ``rolling_ols`` raise ``NotImplementedError`` on any other
value via the honest-placeholder pattern.

Determinism contract
--------------------
Given fixed inputs (y, X, window, min_periods, add_constant) the
output is bit-stable across runs.  No RNG, no global state, no
wallclock dependence.  Iteration order over the time index is
deterministic (pandas Index.sort_values + sequential window slicing).

NaN / rank-deficiency policy
----------------------------
* Within each rolling window ending at t, drop rows where ANY column
  (target OR any regressor) is NaN.
* If the surviving row count is < ``min_periods``, emit NaN for
  betas / alpha / residual / r_squared at t.
* Compute the X-matrix condition number per window via
  ``np.linalg.cond``.  When it exceeds
  ``condition_number_warning_threshold`` (caller-supplied), emit
  betas / alpha / residual / r_squared as NaN AND raise the
  ``condition_flag`` for that t to 1.  The data is NOT silently
  fitted on a degenerate panel — this catches the multicollinearity
  case where a meaningless beta would otherwise be emitted.
* On numerical failure inside lstsq (extremely rare; would indicate
  a non-finite input that slipped past the NaN check), emit NaN +
  flag = 1.

Output shape
------------
``RollingOlsResult`` is a frozen dataclass holding four pandas Series
indexed by the input panel's date index — ``betas`` is a DataFrame
because there's one beta per regressor per t.  Callers turn each
column / Series into a tool-level ``TimeSeries`` for the wire
output.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List

import numpy as np
import pandas as pd


__all__ = [
    "RollingOlsResult",
    "rolling_ols",
]


@dataclass(frozen=True)
class RollingOlsResult:
    """Rolling-OLS output, all indexed by the input panel's date index.

    Attributes
    ----------
    betas : pd.DataFrame
        Per-regressor beta time series.  Columns ordered identically
        to the regressor columns of the caller's input X.  When
        ``add_constant=True``, the constant column is NOT included
        here — the intercept is in ``alpha``.
    alpha : pd.Series
        Intercept time series.  When ``add_constant=False``, alpha is
        all zeros (so callers can read it uniformly).
    residual : pd.Series
        ``y[t] − X[t] @ betas[t] − alpha[t]``.  Same units as y.
    r_squared : pd.Series
        ``1 − SS_res / SS_tot`` over each window.  Bounded in [0, 1]
        for windows where SS_tot > 0; NaN for degenerate windows.
    condition_flag : pd.Series
        Integer 0 / 1 quality flag.  1 when either (a) the X-matrix
        condition number exceeded the warning threshold, or (b)
        lstsq raised numerically.  0 otherwise (including for
        warmup-NaN rows — the flag is for caught failures only).
    """

    betas: pd.DataFrame
    alpha: pd.Series
    residual: pd.Series
    r_squared: pd.Series
    condition_flag: pd.Series


def rolling_ols(
    y: pd.Series,
    X: pd.DataFrame,
    *,
    window: int,
    min_periods: int,
    add_constant: bool = True,
    condition_number_warning_threshold: float = 1e10,
) -> RollingOlsResult:
    """Run a rolling OLS regression of y on the columns of X.

    Parameters
    ----------
    y : pd.Series
        Target series.  Must share an index with X (callers usually
        align via ``pd.concat([y, X], axis=1).dropna()`` before
        calling, but this function tolerates per-window NaN drops on
        its own).
    X : pd.DataFrame
        Regressor panel.  Each column is one regressor; the column
        order is preserved in the output ``betas`` DataFrame.
    window : int
        Trailing-window length (in rows of the input panel) for each
        rolling fit.  Must be > 0.  Constrained against
        ``min_periods`` by the caller — passing ``window <
        min_periods`` is a usage error and raises ValueError here.
    min_periods : int
        Minimum non-NaN row count within a window required to emit a
        non-NaN fit.  Passing ``min_periods <= 0`` raises ValueError.
    add_constant : bool
        Whether to prepend a constant column to X.  Default True
        (matches the standard with-intercept regression convention
        used by every existing rates analytic).  When False, alpha is
        emitted as zeros so the result shape is uniform.
    condition_number_warning_threshold : float
        X-matrix condition numbers above this value trigger a
        ``condition_flag = 1`` for that t and emit NaN coefficients
        rather than fitting on a near-singular panel.  Default 1e10
        is conservative — typical well-conditioned regression panels
        sit around 1e2..1e4; values above 1e10 mean the X matrix is
        effectively rank-deficient at numpy's default float64
        precision.

    Returns
    -------
    RollingOlsResult
        See dataclass docstring.
    """
    if window <= 0:
        raise ValueError(f"window must be > 0, got {window}")
    if min_periods <= 0:
        raise ValueError(f"min_periods must be > 0, got {min_periods}")
    if window < min_periods:
        raise ValueError(
            f"window={window} < min_periods={min_periods}; the rolling "
            "fit can never emit a row with this combination"
        )
    if not y.index.equals(X.index):
        raise ValueError(
            "y and X must share an index; callers should align with "
            "pd.concat / pd.merge before calling rolling_ols"
        )

    n = len(y)
    regressor_names: List[str] = list(X.columns)
    n_regressors = len(regressor_names)

    # Initialise output buffers as float NaN (or int 0 for the flag).
    # We assemble values into Python lists indexed by row position and
    # build pandas objects at the end — simpler than mutating Series in
    # a loop, and keeps all NaNs as float64 not object dtype.
    betas_buf: List[List[float]] = [[math.nan] * n_regressors for _ in range(n)]
    alpha_buf: List[float] = [math.nan] * n
    residual_buf: List[float] = [math.nan] * n
    r_squared_buf: List[float] = [math.nan] * n
    flag_buf: List[int] = [0] * n

    y_arr = y.to_numpy(dtype=float)
    X_arr = X.to_numpy(dtype=float)

    for t in range(n):
        lo = max(0, t - window + 1)
        # Slice the trailing window
        y_win = y_arr[lo : t + 1]
        X_win = X_arr[lo : t + 1, :]

        # Drop rows where y or any X column is NaN
        valid_mask = np.isfinite(y_win) & np.all(np.isfinite(X_win), axis=1)
        y_clean = y_win[valid_mask]
        X_clean = X_win[valid_mask, :]

        if len(y_clean) < min_periods:
            # Warmup or insufficient overlap — leave NaN, flag stays 0
            continue

        # Optionally prepend a constant column.
        if add_constant:
            const_col = np.ones((X_clean.shape[0], 1), dtype=float)
            design = np.concatenate([const_col, X_clean], axis=1)
        else:
            design = X_clean

        # Condition-number gate — catches multicollinearity / near-
        # rank-deficiency before lstsq returns silently bad numbers.
        try:
            cond = float(np.linalg.cond(design))
        except Exception:
            cond = math.inf
        if not math.isfinite(cond) or cond > condition_number_warning_threshold:
            flag_buf[t] = 1
            continue

        # Solve OLS via SVD-based lstsq (rcond=None = numpy's default
        # SVD-truncation rule, locked-in for determinism).
        try:
            coefs, *_ = np.linalg.lstsq(design, y_clean, rcond=None)
        except Exception:
            flag_buf[t] = 1
            continue

        if add_constant:
            alpha_buf[t] = float(coefs[0])
            betas_buf[t] = [float(c) for c in coefs[1:]]
        else:
            alpha_buf[t] = 0.0
            betas_buf[t] = [float(c) for c in coefs]

        # In-window R²: 1 - SS_res / SS_tot
        fitted = design @ coefs
        ss_res = float(np.sum((y_clean - fitted) ** 2))
        y_mean = float(np.mean(y_clean))
        ss_tot = float(np.sum((y_clean - y_mean) ** 2))
        if ss_tot > 0:
            r_squared_buf[t] = 1.0 - ss_res / ss_tot
        else:
            # Degenerate target — all values identical; R² undefined.
            r_squared_buf[t] = math.nan

        # Residual at THIS row (t) — only if X[t] and y[t] are
        # themselves finite; otherwise we don't have a current
        # observation to score against.
        if np.isfinite(y_arr[t]) and np.all(np.isfinite(X_arr[t, :])):
            if add_constant:
                fit_t = float(coefs[0]) + float(np.dot(coefs[1:], X_arr[t, :]))
            else:
                fit_t = float(np.dot(coefs, X_arr[t, :]))
            residual_buf[t] = float(y_arr[t]) - fit_t
        # else: leave residual_buf[t] = NaN

    # Assemble pandas outputs.  Keep dtype float64 for the numeric
    # series and int64 for the flag.
    betas_df = pd.DataFrame(
        betas_buf, index=y.index, columns=regressor_names, dtype=float,
    )
    alpha_s = pd.Series(alpha_buf, index=y.index, dtype=float, name="alpha")
    residual_s = pd.Series(residual_buf, index=y.index, dtype=float, name="residual")
    r2_s = pd.Series(r_squared_buf, index=y.index, dtype=float, name="r_squared")
    flag_s = pd.Series(flag_buf, index=y.index, dtype=int, name="condition_flag")

    return RollingOlsResult(
        betas=betas_df,
        alpha=alpha_s,
        residual=residual_s,
        r_squared=r2_s,
        condition_flag=flag_s,
    )
