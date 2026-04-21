"""
regime.py — Deterministic curve-move classification
=====================================================

Pure-logic classifier for the six canonical rates curve-move regimes.
Used by sovereign_bonds curve_regime today; reusable by any future OIS
regime tool because the classification thresholds are rates-agnostic
(a bull steepener is a bull steepener whether the curve is cash
sovereigns or OIS swaps).

Domain-agnostic.  No pandas, no DB access, no domain vocabulary.  The
caller supplies four scalar bps quantities and gets back a tag string.

Human-readable regime descriptions stay in the calling tool — they can
differ by domain (sovereign narrative vs OIS narrative vs FX etc.).
"""

from __future__ import annotations

# Canonical regime tag strings.  Exposed so callers can match against
# them without typo risk.
BULL_STEEPENER = "BULL_STEEPENER"
BEAR_STEEPENER = "BEAR_STEEPENER"
BULL_FLATTENER = "BULL_FLATTENER"
BEAR_FLATTENER = "BEAR_FLATTENER"
PARALLEL_SHIFT = "PARALLEL_SHIFT"
TWIST = "TWIST"

REGIME_TAGS: frozenset[str] = frozenset({
    BULL_STEEPENER,
    BEAR_STEEPENER,
    BULL_FLATTENER,
    BEAR_FLATTENER,
    PARALLEL_SHIFT,
    TWIST,
})

# Default thresholds — chosen to match the historical sovereign_bonds
# behaviour exactly, so migrating curve_regime.py is a pure refactor.
DEFAULT_PARALLEL_THRESHOLD_BPS = 1.0
DEFAULT_MOVE_THRESHOLD_BPS = 0.5


def classify_curve_move(
    front_change_bps: float,
    back_change_bps: float,
    spread_change_bps: float,
    avg_change_bps: float,
    *,
    parallel_threshold_bps: float = DEFAULT_PARALLEL_THRESHOLD_BPS,
    move_threshold_bps: float = DEFAULT_MOVE_THRESHOLD_BPS,
) -> str:
    """Classify a two-point curve move into one of the six regime tags.

    Parameters
    ----------
    front_change_bps, back_change_bps : float
        Change in bps for the front leg and back leg over the lookback.
    spread_change_bps : float
        back_change_bps minus front_change_bps (positive = steepening).
    avg_change_bps : float
        (front_change_bps + back_change_bps) / 2. Positive = bear move.
    parallel_threshold_bps : float
        If |spread_change_bps| is below this, the move is classified
        PARALLEL_SHIFT rather than a steepener/flattener.  Default 1.0.
    move_threshold_bps : float
        If BOTH |front_change_bps| AND |back_change_bps| are below this,
        the move is classified PARALLEL_SHIFT regardless of the spread
        change (catches the flat-flat case cleanly).  Default 0.5.

    Returns
    -------
    str
        One of ``BULL_STEEPENER``, ``BEAR_STEEPENER``,
        ``BULL_FLATTENER``, ``BEAR_FLATTENER``, ``PARALLEL_SHIFT``,
        ``TWIST``.
    """
    # Edge case: neither leg moved materially → parallel (flat).
    if (abs(front_change_bps) < move_threshold_bps
            and abs(back_change_bps) < move_threshold_bps):
        return PARALLEL_SHIFT

    # Twist: front and back moved in opposite directions.
    if front_change_bps * back_change_bps < 0:
        return TWIST

    # Parallel: both legs moved together, spread barely changed.
    if abs(spread_change_bps) < parallel_threshold_bps:
        return PARALLEL_SHIFT

    # Standard 4-quadrant classification.
    is_bull = avg_change_bps < 0            # yields fell on average
    is_steepener = spread_change_bps > 0    # spread widened

    if is_bull and is_steepener:
        return BULL_STEEPENER
    if not is_bull and is_steepener:
        return BEAR_STEEPENER
    if is_bull and not is_steepener:
        return BULL_FLATTENER
    return BEAR_FLATTENER
