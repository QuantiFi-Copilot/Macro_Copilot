"""rates_agent.policy_futures.tools.build_policy_futures_strip_panel.

Substrate primitive — wide multi-instrument Panel artifact of
policy-futures IMPLIED RATES (PERCENT) across the
SOFR_FUT / SONIA_FUT / EUR_SHORT_RATE_FUT universe × strip
positions 1..8 (rows = trade_date, columns = encoded
``(curve_family, strip_position)`` cells).

Plan §5 Group 3 #21.  Folder-per-tool layout:

    build_policy_futures_strip_panel/
      __init__.py     (this file — public-API re-exports)
      config.yaml     (conventions + methodology)
      schemas.py      (Pydantic input / output)
      compute.py      (deterministic Panel assembly, config-driven)

Column-key encoding (load-bearing)
----------------------------------
The Panel artifact's column axis is keyed by the FLAT STRING
encoding ``"<CURVE_FAMILY>|<STRIP_POSITION>"`` (e.g.
``"SOFR_FUT|1"``, ``"EUR_SHORT_RATE_FUT|8"``).  The desk-recognised
read is the 3-curve-family × 8-strip-position matrix; the closed-
family ``Panel`` artifact's ``units_by_column`` is declared
``Dict[str, TimeSeriesUnits]`` (Pydantic v2 enforces str keys), so
a literal ``pd.MultiIndex`` over tuples cannot live on the
artifact without breaking the typed-boundary contract.  The flat
encoding preserves BOTH pieces of information in every column key
AND remains honest under the Panel contract; the methodology card
surfaces ``column_axis_encoding`` + a per-column
``column_key_decomposition`` map so the desk reader sees the
(curve_family, strip_position, vendor_ticker) triple for every
column.

ADR 0013 V1 — EUR_SHORT_RATE_FUT IS preserved
---------------------------------------------
Unlike the sibling ``futures_pack_average_simple`` primitive
(commit 80a26cd), which raises ``NotImplementedError`` for
``curve_family = EUR_SHORT_RATE_FUT`` because the requested
OUTPUT (a pack-average) cannot be computed honestly without the
playbook's missing ``delivery_month_type`` metadata, this Panel
primitive PRESERVES the EUR_SHORT_RATE_FUT raw strip rows side-by-
side with SOFR_FUT / SONIA_FUT.  The Panel is the SUBSTRATE — no
collapsing, no averaging — so the Buba serial+quarterly mix sits
on the wire exactly as it sits on the DB.  The methodology card's
per-curve-family ``curve_family_reference`` block discloses the
mix explicitly AND points to ``futures_pack_average_simple``'s
PR11 planned-extension entry for the unblock path.

Test seam
---------
``fetch_policy_futures_strip_panel`` and
``fetch_policy_futures_strip_universe`` are imported at module
level in ``compute.py`` so unit tests can monkeypatch them via
``patch("rates_agent.policy_futures.tools.build_policy_futures_strip_panel.compute.X")``.
"""

from rates_agent.policy_futures.tools.build_policy_futures_strip_panel.compute import (
    CONFIG_PATH,
    build_policy_futures_strip_panel,
)
from rates_agent.policy_futures.tools.build_policy_futures_strip_panel.schemas import (
    BuildPolicyFuturesStripPanelInput,
    BuildPolicyFuturesStripPanelOutput,
    PolicyFuturesStripCurveFamily,
    PolicyFuturesStripPanelCalendarPolicy,
    PolicyFuturesStripPanelMissingDataPolicy,
    PolicyFuturesStripPosition,
)


__all__ = [
    "CONFIG_PATH",
    "build_policy_futures_strip_panel",
    "BuildPolicyFuturesStripPanelInput",
    "BuildPolicyFuturesStripPanelOutput",
    "PolicyFuturesStripCurveFamily",
    "PolicyFuturesStripPanelCalendarPolicy",
    "PolicyFuturesStripPanelMissingDataPolicy",
    "PolicyFuturesStripPosition",
]
