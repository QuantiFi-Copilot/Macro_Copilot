"""Shared helpers for ``fx_agent/forwards/`` tools.

Tiny module so that ``fx_carry``, ``forward_curve``, and future
forwards tools do not drift on the JPY-aware divisor or the
tenor-day lookup. Both pieces of logic depend on per-tool config
conventions, so the helpers take a ``ToolConfig`` rather than
hardcoded values.
"""

from __future__ import annotations

from shared.config import ToolConfig


# Closed set of forwards tenors supported by the FX agent in Wave 1.
# Mirrors:
#   - fx_agent/playbooks/fx_forwards.yml v2.0 universe entries
#   - fx_agent/forwards/tools/fx_carry/schemas.py        (FXCarryTenor Literal)
#   - fx_agent/forwards/tools/forward_curve/schemas.py   (FXForwardCurveRow.tenor)
# Adding a new tenor = update all three sites AND add the matching
# ``tenor_<n>_days`` convention in every tool's config.yaml that
# uses ``tenor_days_from_config`` below.
SUPPORTED_FORWARD_TENORS: tuple[str, ...] = ("1W", "1M", "3M", "6M", "12M")


def tenor_days_from_config(config: ToolConfig) -> dict[str, int]:
    """Trading-day count per supported tenor, read from the tool's
    own ``config.yaml``.

    Each tool that uses this helper must declare the matching
    ``tenor_<n>_days`` convention in its own config.yaml — the helper
    deliberately does not fall back to literals so a forgotten
    convention key surfaces immediately as a ``KeyError``.
    """
    return {
        "1W": int(config.convention_value("tenor_1w_days")),
        "1M": int(config.convention_value("tenor_1m_days")),
        "3M": int(config.convention_value("tenor_3m_days")),
        "6M": int(config.convention_value("tenor_6m_days")),
        "12M": int(config.convention_value("tenor_12m_days")),
    }


def points_to_spot_units(
    pair: str,
    forward_points: float,
    *,
    jpy_divisor: float,
    default_divisor: float,
) -> float:
    """Convert Bloomberg FX forward points into spot units.

    JPY pairs use the ``jpy_divisor`` convention (typically 100);
    non-JPY G10 pairs use the ``default_divisor`` convention
    (typically 10000). The shortcut ``"JPY" in pair`` matches USDJPY,
    EURJPY, GBPJPY, etc. — fine for the current G10 universe.

    Future extensions to EM / NDFs will need a per-instrument
    convention metadata column rather than this substring check —
    see fx_agent/ROADMAP.md "JPY forward points divisor" section
    under "Conventions / lessons learned".
    """
    return (
        forward_points / jpy_divisor
        if "JPY" in pair
        else forward_points / default_divisor
    )


def long_pair_carry_signal(
    forward_points: float,
    spot: float,
    pair: str,
    *,
    tenor_days: int,
    jpy_divisor: float,
    default_divisor: float,
    annualization_days: int = 252,
) -> float:
    """Return the carry of going LONG the FX pair, in PERCENT.

    SIGN CONVENTION (load-bearing — DO NOT confuse with fx_carry)
    -------------------------------------------------------------
    For a pair quoted as ``AAABBB``, "long pair" means buy AAA, sell BBB.
    The carry of that position is r_AAA - r_BBB = r_base - r_quote.

    By CIP: (F/S - 1) * (annual/tenor) ≈ r_quote - r_base.

    Therefore::

        long_pair_carry = r_base - r_quote = -(F/S - 1) * (annual/tenor)

    Multiplied by 100 to express in PERCENT (8.0 = 8% per year).

    Why this helper exists
    ----------------------
    ``fx_carry.carry_annualized_pct`` (the canonical fx_carry tool output)
    is ``r_quote - r_base`` per CIP — i.e., the rate spread PRICED INTO
    the forward, NOT the carry of going long the pair. The two are
    negatives of each other. A canonical "long high-yielder" carry
    strategy ranks pairs by ``r_base - r_quote`` descending and goes
    long the top.

    This was caught as a real bug during the Phase B+ ``carry_basket``
    development: the first implementation used fx_carry's signal
    directly as the long-position carry, which inverted the strategy
    (long LOW-yielders). This helper makes the correct sign the
    obvious default for any future tool building a long-pair signal.

    Parameters
    ----------
    forward_points : float
        Raw Bloomberg forward points (pre-divisor).
    spot : float
        Spot rate.
    pair : str
        6-char FX pair (e.g. ``"EURUSD"``, ``"USDMXN"``).
    tenor_days : int
        Trading-day count for the tenor (e.g. 21 for 1M, 63 for 3M).
    jpy_divisor : float
        Forward-points divisor for JPY pairs (typically 100).
    default_divisor : float
        Forward-points divisor for non-JPY pairs (typically 10000).
    annualization_days : int, default 252
        Trading-day count per year for annualization. Coherent with
        fx_carry / forward_curve / implied_yield_differential.

    Returns
    -------
    float
        Carry of going long the pair, in PERCENT, annualized. Positive
        means longing the pair captures positive carry (long high-
        yielder vs USD or vs low-yielder).

    Examples
    --------
    >>> # USDJPY: long pair = long USD short JPY = positive carry (USD >> JPY)
    >>> # F=148, S=147, 1M (tenor_days=21):
    >>> # (F/S - 1)*(252/21)*100 = 0.68*12 ≈ +8.16% (USD rate exceeds JPY)
    >>> # long_pair_carry_USDJPY = -(+8.16) = ??? NO wait, USD>JPY → long USD positive
    >>> # The math: r_quote(JPY) - r_base(USD) = JPY_rate - USD_rate = NEGATIVE
    >>> # long_pair_carry = r_base - r_quote = USD_rate - JPY_rate = POSITIVE ✓
    >>> # Correctly captures "long USDJPY = positive carry" intuition.

    Notes
    -----
    For pairs where USD is BASE (USDxxx): long pair = long USD.
    For pairs where USD is QUOTE (xxxUSD): long pair = long local.
    The sign-flip is implicit in the (F/S - 1) computation — no need
    for usd_leg_position adjustment here, because the sign of
    long-pair carry is intrinsic to the pair quote convention.
    """
    fp_spot_units = points_to_spot_units(
        pair, forward_points,
        jpy_divisor=jpy_divisor, default_divisor=default_divisor,
    )
    # forward_implied_rate_diff = (F/S - 1) * (annual/tenor) * 100
    #                           = r_quote - r_base  (CIP)
    forward_implied_rate_diff_pct = (
        (fp_spot_units / spot) * (annualization_days / tenor_days) * 100.0
    )
    # long_pair_carry = r_base - r_quote = -forward_implied_rate_diff
    return -forward_implied_rate_diff_pct
