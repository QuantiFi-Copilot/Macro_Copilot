"""
curve_move.py — Deterministic curve-move classification primitive
==================================================================

Pure-logic classifier for the six canonical rates curve-move tags:
``BULL_STEEPENER``, ``BEAR_STEEPENER``, ``BULL_FLATTENER``,
``BEAR_FLATTENER``, ``PARALLEL_SHIFT``, ``TWIST``.

Why this module is named ``curve_move`` (and not ``regime``)
------------------------------------------------------------
"Regime" in rates / macro carries a specific technical connotation —
a *persistent state* the market is in, lasting weeks to months,
typically estimated via a Markov-switching or HMM model.  This
classifier does no such inference.  It looks at a single observed
move (front-leg bps change, back-leg bps change, spread bps change,
average bps change) and returns a categorical tag.  Calling that a
"regime" overclaims; "move classification" is honest and leaves the
word "regime" available for an actual regime-detection tool that may
ship later as a Bucket 2 model-dependent tool.

Domain-agnostic.  No pandas, no DB access, no domain vocabulary.  The
caller supplies four scalar bps quantities and the two thresholds
(parallel-shift and move thresholds) and gets back a tag string.

Human-readable narration of each tag stays in the calling tool — it
can differ by domain (sovereign narrative vs OIS narrative, etc.).

Migration note
--------------
This module replaces ``shared.analytics.regime`` (deleted in the
``curve_move_classifier`` migration commit).  The classifier function
itself was renamed from no name (``classify_curve_move`` was always
the function name) — only the module path moved.  Module-level
``DEFAULT_PARALLEL_THRESHOLD_BPS`` / ``DEFAULT_MOVE_THRESHOLD_BPS``
constants are removed: every caller now passes the thresholds from
its own ``config.yaml`` explicitly, so a single source of truth lives
per-tool rather than as a shared module-level default that drifts
unnoticed.
"""

from __future__ import annotations

# Canonical tag strings.  Exposed so callers can match against them
# without typo risk.  These are domain-truth: bull/bear/steepener/
# flattener/twist/parallel are the labels every fixed-income desk
# uses, regardless of whether the curve is sovereign cash, OIS,
# corporate, or anything else.  Adding a 7th tag is a domain
# redefinition, not a configuration change.
BULL_STEEPENER = "BULL_STEEPENER"
BEAR_STEEPENER = "BEAR_STEEPENER"
BULL_FLATTENER = "BULL_FLATTENER"
BEAR_FLATTENER = "BEAR_FLATTENER"
PARALLEL_SHIFT = "PARALLEL_SHIFT"
TWIST = "TWIST"

CURVE_MOVE_TAGS: frozenset[str] = frozenset({
    BULL_STEEPENER,
    BEAR_STEEPENER,
    BULL_FLATTENER,
    BEAR_FLATTENER,
    PARALLEL_SHIFT,
    TWIST,
})


def classify_curve_move(
    front_change_bps: float,
    back_change_bps: float,
    spread_change_bps: float,
    avg_change_bps: float,
    *,
    parallel_threshold_bps: float,
    move_threshold_bps: float,
) -> str:
    """Classify a two-point curve move into one of the six tags.

    Both threshold kwargs are now **required** (no module-level
    defaults).  The owning tool reads them from its own ``config.yaml``
    and passes them in explicitly, so the convention values are
    auditable per-tool and visible at the callsite.  This is the
    pattern established by commit 2 of the tool-config pilot.

    Parameters
    ----------
    front_change_bps, back_change_bps : float
        Change in bps for the front leg and back leg over the lookback.
    spread_change_bps : float
        ``back_change_bps - front_change_bps`` (positive = steepening).
    avg_change_bps : float
        ``(front_change_bps + back_change_bps) / 2`` for the canonical
        ``arithmetic_mean`` aggregation; the calling tool may pass a
        differently-aggregated value if its config supports it (e.g.
        duration-weighted), but the classification semantics here
        treat the input as the signed average.  Positive = bear move.
    parallel_threshold_bps : float
        If ``|spread_change_bps|`` is below this, the move is
        classified ``PARALLEL_SHIFT`` rather than a steepener /
        flattener.  PM-tweakable; conservative PMs use 2.0,
        aggressive PMs use 0.5.
    move_threshold_bps : float
        If BOTH ``|front_change_bps|`` AND ``|back_change_bps|`` are
        below this, the move is classified ``PARALLEL_SHIFT``
        regardless of the spread direction (catches the flat-flat
        case cleanly).

    Returns
    -------
    str
        One of the six tags above.

    Notes
    -----
    The 4-quadrant classification rule (sign of avg_change × sign of
    spread_change) is mathematics, not opinion.  The two thresholds
    are PM-tweakable conventions.  The taxonomy itself is fixed
    domain truth.
    """
    # Edge case: neither leg moved materially → parallel (flat).
    if (abs(front_change_bps) < move_threshold_bps
            and abs(back_change_bps) < move_threshold_bps):
        return PARALLEL_SHIFT

    # Twist: front and back moved in opposite directions.  Takes
    # precedence over parallel — opposite-sign moves are always TWIST,
    # never PARALLEL.
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
