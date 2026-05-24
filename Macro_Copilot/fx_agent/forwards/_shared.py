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
