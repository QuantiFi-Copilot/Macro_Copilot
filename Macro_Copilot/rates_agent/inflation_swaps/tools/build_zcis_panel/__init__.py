"""rates_agent.inflation_swaps.tools.build_zcis_panel.

Substrate primitive — wide multi-instrument Panel artifact of
zero-coupon inflation swap (ZCIS) rates across the
USD_ZCIS / EUR_ZCIS / GBP_ZCIS universe (rows = trade_date,
columns = vendor_ticker).

Plan §5 Group 3 #19.  Folder-per-tool layout:

    build_zcis_panel/
      __init__.py     (this file — public-API re-exports)
      config.yaml     (conventions + methodology)
      schemas.py      (Pydantic input / output)
      compute.py      (deterministic Panel assembly, config-driven)

Column-key honesty (load-bearing)
---------------------------------
Panel columns are keyed by ``vendor_ticker`` (the canonical
Bloomberg identifier, e.g. ``'USSWIT10 Curncy'``).  Per the
catalog the ideal key is ``security_name``, but the ZCIS universe's
``macro_data.instrument_metadata_history.security_name`` is
universally NULL on the live SCD2 rows — surfacing NULL would be
a dead column key; relabelling ``vendor_ticker`` under the
``security_name`` label would be a no-proxy violation.  Same
treatment ``scan_inflation_swaps_extremes`` applies.  The
methodology card discloses the substitution explicitly.

Test seam
---------
``fetch_inflation_swap_panel_by_vendor_ticker`` and
``fetch_inflation_swap_universe`` are imported at module level in
``compute.py`` so unit tests can monkeypatch them via
``patch("rates_agent.inflation_swaps.tools.build_zcis_panel.compute.X")``.
"""

from rates_agent.inflation_swaps.tools.build_zcis_panel.compute import (
    CONFIG_PATH,
    build_zcis_panel,
)
from rates_agent.inflation_swaps.tools.build_zcis_panel.schemas import (
    BuildZcisPanelInput,
    BuildZcisPanelOutput,
    ZcisCurveFamily,
    ZcisPanelCalendarPolicy,
    ZcisPanelMissingDataPolicy,
)


__all__ = [
    "CONFIG_PATH",
    "build_zcis_panel",
    "BuildZcisPanelInput",
    "BuildZcisPanelOutput",
    "ZcisCurveFamily",
    "ZcisPanelCalendarPolicy",
    "ZcisPanelMissingDataPolicy",
]
