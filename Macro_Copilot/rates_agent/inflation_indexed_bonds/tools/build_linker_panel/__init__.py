"""rates_agent.inflation_indexed_bonds.tools.build_linker_panel.

Substrate primitive — wide multi-instrument Panel artifact of
inflation-linker REAL yields across the
USD_TIPS / GBP_LINKER / EUR_FR_LINKER / CAD_RRB universe
(rows = trade_date, columns = vendor_ticker).

Plan §5 Group 3 #20.  Folder-per-tool layout:

    build_linker_panel/
      __init__.py     (this file — public-API re-exports)
      config.yaml     (conventions + methodology)
      schemas.py      (Pydantic input / output)
      compute.py      (deterministic Panel assembly, config-driven)

Column-key honesty (load-bearing)
---------------------------------
Panel columns are keyed by ``vendor_ticker`` (the canonical
Bloomberg identifier, e.g. ``'GTII10 Govt'`` for the USD_TIPS 10Y,
``'GTGBPII10Y Govt'`` for the GBP_LINKER 10Y).  Per the catalog
the ideal key is ``security_name``, but the inflation-linker
universe's
``macro_data.instrument_metadata_history.security_name`` is
universally NULL on the live SCD2 rows (the orchestrator pre-
flight verified 0 non-NULL security_name rows across all 24
linker instruments — USD_TIPS=4 / GBP_LINKER=9 / EUR_FR_LINKER=5
/ CAD_RRB=6).  Surfacing NULL would be a dead column key;
relabelling ``vendor_ticker`` under the ``security_name`` label
would be a no-proxy violation.  Same treatment ``build_zcis_panel``
(commit 32c386f) and ``scan_inflation_linkers_extremes`` (commit
91a5714) apply.  The methodology card discloses the substitution
explicitly.

Test seam
---------
``fetch_linker_panel_by_vendor_ticker`` and
``fetch_linker_universe`` are imported at module level in
``compute.py`` so unit tests can monkeypatch them via
``patch("rates_agent.inflation_indexed_bonds.tools.build_linker_panel.compute.X")``.
"""

from rates_agent.inflation_indexed_bonds.tools.build_linker_panel.compute import (
    CONFIG_PATH,
    build_linker_panel,
)
from rates_agent.inflation_indexed_bonds.tools.build_linker_panel.schemas import (
    BuildLinkerPanelInput,
    BuildLinkerPanelOutput,
    LinkerCurveFamily,
    LinkerPanelCalendarPolicy,
    LinkerPanelMissingDataPolicy,
)


__all__ = [
    "CONFIG_PATH",
    "build_linker_panel",
    "BuildLinkerPanelInput",
    "BuildLinkerPanelOutput",
    "LinkerCurveFamily",
    "LinkerPanelCalendarPolicy",
    "LinkerPanelMissingDataPolicy",
]
