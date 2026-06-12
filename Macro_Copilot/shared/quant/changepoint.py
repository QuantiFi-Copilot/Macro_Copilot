"""shared.quant.changepoint — binary-segmentation changepoint detection.

The FOURTH numeric in ``shared/quant/`` (after variance_ratio, hurst,
ou).  Pure, finance-blind, deterministic per the package boundary
rules — the L3 ``changepoint_detection`` operator is the only
production consumer.

THE METHOD (binary segmentation with an L2 mean-shift cost — the
canonical structural-break detector; design-locked).  The cost of a
half-open segment ``[a, b)`` is its sum of squared deviations from the
segment mean::

    SSE(a, b) = Σ_{i∈[a,b)} (x_i − mean)²
              = (Σ x²) − (Σ x)² / (b − a)         (prefix-sum form)

A split at ``t`` reduces the cost by ``gain(t) = SSE(a,b) − SSE(a,t) −
SSE(t,b) ≥ 0``.  Binary segmentation greedily accepts the split with
the largest gain across all current segments, recurses on the two
halves, and stops at ``n_changepoints`` accepted splits OR when no
split has POSITIVE gain (an already-flat segment — the honest "no more
breaks" stop, so the result may contain FEWER than the requested
number of changepoints, down to zero).

DESIGN LOCKS (the operator stamps them in lineage): the L2 mean-shift
cost (``cost_model='l2_mean_shift'``) and the greedy binary-
segmentation algorithm (``algorithm='binary_segmentation'``).
Variance-shift / kernel costs and exact PELT / dynamic-programming are
declared planned extensions.

LOOK-AHEAD: every break is placed using the WHOLE sample (full-sample
detection) — the operator discloses this; never feed the output into
point-in-time compositions.

Refuses degenerate input with a plain ``ValueError`` (the operator
pre-validates with its typed error — belt-and-suspenders): ``n_change
points < 1``; ``min_size < 2``; fewer than ``(n_changepoints+1)·min
size`` observations; a non-finite input; or zero variance (a constant
series has no definable break).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np


_MIN_SIZE_FLOOR = 2


@dataclass(frozen=True)
class ChangepointResult:
    """The result of :func:`binary_segmentation`.

    Attributes
    ----------
    breakpoints :
        The interior split indices (the FIRST index of each new
        segment), ascending.  May be shorter than the requested
        ``n_changepoints`` when no further split has positive gain.
    gains :
        The SSE reduction per break, aligned to ``breakpoints``.
    segment_means :
        The mean of each of ``len(breakpoints) + 1`` segments, in
        position order.
    n_changepoints :
        ``len(breakpoints)`` — the number actually found.
    n_obs :
        The number of observations.
    cost_model :
        The design-locked cost (``"l2_mean_shift"``).
    """

    breakpoints: Tuple[int, ...]
    gains: Tuple[float, ...]
    segment_means: Tuple[float, ...]
    n_changepoints: int
    n_obs: int
    cost_model: str


def _best_split(
    s1: np.ndarray, s2: np.ndarray, a: int, b: int, min_size: int,
) -> Tuple[Optional[int], float]:
    """The split ``t`` in ``[a+min_size, b-min_size]`` of segment
    ``[a, b)`` with the largest L2 gain; ``(None, 0.0)`` if no valid
    split exists."""
    if b - a < 2 * min_size:
        return None, 0.0
    t = np.arange(a + min_size, b - min_size + 1)
    sse_ab = (s2[b] - s2[a]) - (s1[b] - s1[a]) ** 2 / (b - a)
    sse_at = (s2[t] - s2[a]) - (s1[t] - s1[a]) ** 2 / (t - a)
    sse_tb = (s2[b] - s2[t]) - (s1[b] - s1[t]) ** 2 / (b - t)
    gain = sse_ab - sse_at - sse_tb
    k = int(np.argmax(gain))
    return int(t[k]), float(gain[k])


def binary_segmentation(
    values: np.ndarray,
    *,
    n_changepoints: int,
    min_size: int = 2,
) -> ChangepointResult:
    """Detect up to ``n_changepoints`` mean-shift breaks in a 1-D series.

    Parameters
    ----------
    values :
        A 1-D array of FINITE floats (the caller drops NaNs first).
    n_changepoints :
        The number of breaks to find (``>= 1``); the segmentation
        returns the top-gain splits, fewer if gains are exhausted.
    min_size :
        The minimum segment length (``>= 2``).

    Returns
    -------
    ChangepointResult

    Raises
    ------
    ValueError
        ``n_changepoints < 1``; ``min_size < 2``; fewer than
        ``(n_changepoints+1)*min_size`` observations; a non-finite
        input; or zero variance.
    """
    if n_changepoints < 1:
        raise ValueError(
            f"binary_segmentation: n_changepoints must be >= 1 (got "
            f"{n_changepoints})."
        )
    if min_size < _MIN_SIZE_FLOOR:
        raise ValueError(
            f"binary_segmentation: min_size must be >= {_MIN_SIZE_FLOOR} "
            f"(got {min_size})."
        )
    arr = np.asarray(values, dtype=float).ravel()
    n = int(arr.size)
    need = (n_changepoints + 1) * min_size
    if n < need:
        raise ValueError(
            f"binary_segmentation: needs at least (n_changepoints+1)*"
            f"min_size = {need} observations for {n_changepoints} "
            f"break(s) of min_size {min_size} (got {n})."
        )
    if not np.all(np.isfinite(arr)):
        raise ValueError(
            "binary_segmentation: all values must be finite (drop NaNs "
            "upstream)."
        )
    if float(np.var(arr)) == 0.0:
        raise ValueError(
            "binary_segmentation: the input has zero variance — a "
            "constant series has no definable break."
        )

    # Prefix sums of x and x² (leading zero) — O(1) segment SSE.  CENTER
    # by the mean first: the L2 mean-shift cost is translation-invariant
    # (SSE around the segment mean is unchanged by a constant shift), so
    # centering is EXACT and avoids the catastrophic cancellation that
    # the raw "Σx² − (Σx)²/n" form suffers on a large-mean input (where
    # it would subtract two near-equal large magnitudes and lose all
    # significant digits — silently returning a wrong break or a false
    # empty result).  The sibling ou.py/hurst.py modules center likewise.
    centered = arr - arr.mean()
    s1 = np.concatenate([[0.0], np.cumsum(centered)])
    s2 = np.concatenate([[0.0], np.cumsum(centered * centered)])

    segments: List[Tuple[int, int]] = [(0, n)]
    found: List[Tuple[int, float]] = []  # (breakpoint, gain)
    while len(found) < n_changepoints:
        best_t: Optional[int] = None
        best_gain = 0.0
        best_seg: Optional[Tuple[int, int]] = None
        for (a, b) in segments:
            t, gain = _best_split(s1, s2, a, b, min_size)
            if t is not None and gain > best_gain:
                best_gain = gain
                best_t = t
                best_seg = (a, b)
        if best_t is None or best_gain <= 0.0:
            break  # no positive-gain split remains — honest stop
        found.append((best_t, best_gain))
        a, b = best_seg  # type: ignore[misc]
        segments.remove(best_seg)  # type: ignore[arg-type]
        segments.append((a, best_t))
        segments.append((best_t, b))

    found.sort(key=lambda pair: pair[0])
    breakpoints = tuple(bp for bp, _ in found)
    gains = tuple(g for _, g in found)

    # Segment means in position order ([0, bp0), [bp0, bp1), ..., [bpL, n)).
    bounds = [0, *breakpoints, n]
    segment_means = tuple(
        float(arr[bounds[i]:bounds[i + 1]].mean())
        for i in range(len(bounds) - 1)
    )

    return ChangepointResult(
        breakpoints=breakpoints,
        gains=gains,
        segment_means=segment_means,
        n_changepoints=len(breakpoints),
        n_obs=n,
        cost_model="l2_mean_shift",
    )


__all__ = ["binary_segmentation", "ChangepointResult"]
