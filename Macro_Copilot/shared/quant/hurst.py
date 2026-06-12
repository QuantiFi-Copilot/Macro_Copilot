"""shared.quant.hurst — the Hurst exponent via rescaled-range (R/S).

The SECOND numeric in ``shared/quant/`` (after ``variance_ratio.py``).
Pure, finance-blind, deterministic per the package boundary rules.
The L3 ``hurst_exponent`` operator is the only production consumer.

THE ESTIMATOR (classical rescaled-range analysis — Hurst 1951,
Mandelbrot & Wallis 1969, with the Anis–Lloyd 1976 / Peters 1994
small-sample correction).  For each window size ``n`` the series is cut
into ``floor(N/n)`` contiguous non-overlapping blocks; per block the
rescaled range ``R/S`` is the range of the mean-adjusted cumulative
deviations divided by the block standard deviation; ``(R/S)_n`` is the
average over blocks.  The Hurst exponent ``H`` is the slope of
``log(R/S)_n`` against ``log n``.

  - H ≈ 0.5 — no long memory (the increments behave like white noise;
    a random-walk-like series).
  - H > 0.5 — PERSISTENT / long memory (trending).
  - H < 0.5 — ANTI-PERSISTENT (mean-reverting).

This is the textbook R/S of the series AS GIVEN — it does NOT difference
internally.  To characterise a price/level series for mean-reversion,
difference it first (the ``series_arithmetic(diff) -> hurst`` pattern,
exactly as ``ljung_box``/``normality_test`` document) so that a random
walk maps to H ≈ 0.5.

THE SMALL-SAMPLE CORRECTION (design-locked, the honesty ruling):
uncorrected classical R/S provably OVERESTIMATES H at finite N — on a
true random walk it returns H ≈ 0.55–0.58, a bias that straddles the
H = 0.5 decision boundary.  So the canonical estimate subtracts the
Anis–Lloyd theoretical expected ``E[R/S]_n`` under the iid null::

    H = 0.5 + slope(log observed R/S vs log n)
            - slope(log Anis–Lloyd E[R/S] vs log n)

which recentres an iid series to H ≈ 0.5 exactly.  The raw (uncorrected)
slope rides alongside as ``h_uncorrected`` so the correction's effect
is auditable.

DESIGN LOCKS (recorded by the operator in lineage): classical R/S
(not DFA / aggregated-variance / periodogram — declared planned
extensions); the Anis–Lloyd correction; and a geometric scale set from
``min_window`` to ``N // 2`` (no caller-chosen scales — auto-scale
selection is a planned extension).  The scale set is a pure function of
N → byte-identical replay.

Refuses degenerate input with a plain ``ValueError`` (the operator
pre-validates with its typed error so this is belt-and-suspenders):
fewer than ``_MIN_OBS`` observations; a non-finite input; or
zero-variance (constant) input where every block's S is zero.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple

import numpy as np
from scipy.special import gammaln


# The geometric scale set: window sizes from _MIN_WINDOW to N//2.  A
# dense geometric grid (≥ _MIN_SCALES distinct sizes) gives a
# well-conditioned log-log slope.  Design-locked — not caller-chosen.
_MIN_WINDOW = 8
_N_SCALES_TARGET = 20
_MIN_SCALES = 4

# The estimator needs a generous sample: R/S is meaningless and its bias
# explodes on short series (the operator enforces this floor too).
_MIN_OBS = 128


@dataclass(frozen=True)
class HurstResult:
    """The result of :func:`hurst_rs`.

    Attributes
    ----------
    h :
        The Anis–Lloyd-corrected Hurst exponent (the canonical
        estimate).  ≈ 0.5 random-walk-like; > 0.5 persistent/trending;
        < 0.5 anti-persistent/mean-reverting.
    h_uncorrected :
        The raw slope of ``log(R/S)`` vs ``log n`` (upward-biased at
        finite N — carried for audit).
    r_squared :
        The R² of the observed log-log fit — the estimate's quality
        (a low value means a poor power-law fit and an untrustworthy H).
    n_scales :
        The number of window sizes that entered the fit.
    scales :
        The window sizes used (a pure function of N).
    log_rs :
        ``log((R/S)_n)`` per scale (same order as ``scales``).
    correction :
        The small-sample correction applied (``"anis_lloyd"``).
    n_obs :
        The number of observations.
    """

    h: float
    h_uncorrected: float
    r_squared: float
    n_scales: int
    scales: Tuple[int, ...]
    log_rs: Tuple[float, ...]
    correction: str
    n_obs: int


def _scale_set(n_obs: int) -> List[int]:
    """The design-locked geometric window sizes for a length-``n_obs``
    series: distinct integers from ``_MIN_WINDOW`` to ``n_obs // 2``."""
    max_window = n_obs // 2
    if max_window < _MIN_WINDOW:
        return []
    raw = np.geomspace(_MIN_WINDOW, max_window, num=_N_SCALES_TARGET)
    scales = sorted({int(round(x)) for x in raw})
    return [s for s in scales if _MIN_WINDOW <= s <= max_window]


def _rs_for_scale(values: np.ndarray, n: int) -> float | None:
    """Mean rescaled range ``(R/S)_n`` over the non-overlapping blocks
    of size ``n``; ``None`` if no block yields a defined R/S."""
    n_windows = values.size // n
    rs_vals: List[float] = []
    for w in range(n_windows):
        block = values[w * n:(w + 1) * n]
        dev = block - block.mean()
        cumulative = np.cumsum(dev)
        r = float(cumulative.max() - cumulative.min())
        s = float(block.std())  # population std (ddof=0)
        if s > 0.0 and r > 0.0:
            rs_vals.append(r / s)
    if not rs_vals:
        return None
    return float(np.mean(rs_vals))


def _anis_lloyd_expected(n: int) -> float:
    """Anis–Lloyd (1976) theoretical ``E[R/S]_n`` under the iid null.

    ``E[R/S]_n = ((n-0.5)/n) · Γ((n-1)/2)/(sqrt(π)·Γ(n/2))
                 · Σ_{i=1}^{n-1} sqrt((n-i)/i)``

    The Γ ratio is evaluated via ``gammaln`` to avoid overflow at large
    n (it tends to ``sqrt(2/n)``, the usual large-n simplification).
    """
    i = np.arange(1, n)
    tail = float(np.sum(np.sqrt((n - i) / i)))
    gamma_ratio = np.exp(gammaln((n - 1) / 2.0) - gammaln(n / 2.0))
    return ((n - 0.5) / n) * (gamma_ratio / np.sqrt(np.pi)) * tail


def hurst_rs(values: np.ndarray) -> HurstResult:
    """Anis–Lloyd-corrected Hurst exponent of a 1-D series via R/S.

    Parameters
    ----------
    values :
        A 1-D array of FINITE floats (the caller drops NaNs first — this
        is a pure numeric and does not impute).

    Returns
    -------
    HurstResult

    Raises
    ------
    ValueError
        Fewer than ``_MIN_OBS`` observations; a non-finite input;
        fewer than ``_MIN_SCALES`` usable scales; or a constant series
        (every block's standard deviation is zero).
    """
    arr = np.asarray(values, dtype=float).ravel()
    n_obs = int(arr.size)
    if n_obs < _MIN_OBS:
        raise ValueError(
            f"hurst_rs: needs at least {_MIN_OBS} observations (got "
            f"{n_obs}); rescaled-range analysis is meaningless and "
            "sharply biased on shorter series."
        )
    if not np.all(np.isfinite(arr)):
        raise ValueError(
            "hurst_rs: all values must be finite (drop NaNs upstream)."
        )

    scales = _scale_set(n_obs)
    used_scales: List[int] = []
    log_rs: List[float] = []
    for n in scales:
        rs = _rs_for_scale(arr, n)
        if rs is not None and rs > 0.0:
            used_scales.append(n)
            log_rs.append(float(np.log(rs)))

    if len(used_scales) < _MIN_SCALES:
        raise ValueError(
            f"hurst_rs: only {len(used_scales)} usable scales (need "
            f">= {_MIN_SCALES}) — the input is too short or degenerate "
            "(near-constant blocks)."
        )

    log_n = np.log(np.asarray(used_scales, dtype=float))
    log_rs_arr = np.asarray(log_rs, dtype=float)

    # Observed slope (the uncorrected Hurst) + the fit quality.
    slope_obs, intercept = np.polyfit(log_n, log_rs_arr, 1)
    predicted = slope_obs * log_n + intercept
    ss_res = float(np.sum((log_rs_arr - predicted) ** 2))
    ss_tot = float(np.sum((log_rs_arr - log_rs_arr.mean()) ** 2))
    r_squared = 1.0 - ss_res / ss_tot if ss_tot > 0.0 else 0.0

    # Anis–Lloyd correction: recentre so an iid series → H ≈ 0.5.
    log_e = np.log(
        np.asarray([_anis_lloyd_expected(n) for n in used_scales], dtype=float)
    )
    slope_exp = float(np.polyfit(log_n, log_e, 1)[0])
    h = 0.5 + float(slope_obs) - slope_exp

    return HurstResult(
        h=float(h),
        h_uncorrected=float(slope_obs),
        r_squared=float(r_squared),
        n_scales=len(used_scales),
        scales=tuple(used_scales),
        log_rs=tuple(log_rs),
        correction="anis_lloyd",
        n_obs=n_obs,
    )


__all__ = ["hurst_rs", "HurstResult"]
