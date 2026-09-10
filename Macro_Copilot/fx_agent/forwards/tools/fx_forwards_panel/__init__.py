"""fx_agent.forwards.tools.fx_forwards_panel — cross-sectional FX forwards Panel.

Phase F1 (2026-05-27).

Folder-per-tool layout (mirror of
``fx_agent.spot.tools.fx_panel`` for the fx_forward substrate):

    fx_forwards_panel/
      __init__.py     (this file — public-API re-exports)
      config.yaml     (conventions + methodology)
      schemas.py      (Pydantic input / output)
      compute.py      (deterministic Panel assembly, config-driven)

Purpose
-------
Phase F1 primitive: assemble a wide multi-pair ``Panel`` of FX
forward points keyed by pair name (e.g. EURUSD, USDJPY) for a
(market_scope, tenor) slice. Substrate is ``instrument_type=
'fx_forward'``. Returned Panel uses PRICE units (forward points
are absolute pip quotes — they're not percentages or bps).

Asset-agnostic discipline
-------------------------
This tool is a pure fetcher+pivot+package primitive. It does
NOT compute correlation / PCA / factor loadings / regression —
those operators live in ``shared/operators``.

Note for tests
--------------
Test seam (``fetch_fx_forwards_panel``) lives in ``compute.py``'s
namespace only; tests patch via
``...fx_forwards_panel.compute.fetch_fx_forwards_panel``.
"""

from fx_agent.forwards.tools.fx_forwards_panel.compute import (
    CONFIG_PATH,
    calculate_fx_forwards_panel,
)
from fx_agent.forwards.tools.fx_forwards_panel.schemas import (
    FXForwardsPanelInput,
    FXForwardsPanelOutput,
)


__all__ = [
    "CONFIG_PATH",
    "calculate_fx_forwards_panel",
    "FXForwardsPanelInput",
    "FXForwardsPanelOutput",
]
