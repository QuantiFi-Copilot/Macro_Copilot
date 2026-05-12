"""rates_agent.ois.tools.financing_rate — financing-rate primitive.

Phase 1 PR 19.

Folder-per-tool layout:

    financing_rate/
      __init__.py     (this file — public-API re-exports)
      config.yaml     (conventions + methodology)
      schemas.py      (Pydantic input / output)
      compute.py      (deterministic compute, config-driven)

Purpose
-------
Emit a daily Series of financing rates over a date range, suitable
for feeding into ``shared/operators/evaluate_trades`` to add carry-
of-financing to a backtest's P&L.

The tool surface lives in the OIS agent because the only V1 data-
driven method (``overnight_index_proxy``) reads OIS data.  Same
precedent as the cross-domain ``calculate_swap_spread_tool`` that
also lives in the OIS agent.

Methods (closed-enum, declared in ``config.yaml``)
-------------------------------------------------
  - ``constant_rate``           — caller supplies a fixed rate
  - ``overnight_index_proxy``   — read SOFR/ESTR/SONIA/... O/N tenor
  - ``term_repo_curve``         — NotImplementedError (real repo data
                                  not yet ingested)
  - ``gc_special_blend``        — NotImplementedError (GC-special
                                  data not yet ingested)

Caller MUST supply per-method params (``constant_rate_pct`` for
constant_rate; ``proxy_curve`` for overnight_index_proxy) — there are
NO opinionated defaults for these.  The Pydantic input validator
rejects requests that omit the method-specific param.

Test seam
---------
``fetch_overnight_index_series`` and ``date`` are imported at
module level in ``compute.py`` so tests can monkeypatch them via
``...financing_rate.compute.X``.
"""

from rates_agent.ois.tools.financing_rate.compute import (
    CONFIG_PATH,
    compute_financing_rate,
)
from rates_agent.ois.tools.financing_rate.schemas import (
    FinancingMethod,
    FinancingRateInput,
    FinancingRateOutput,
)


__all__ = [
    "CONFIG_PATH",
    "compute_financing_rate",
    "FinancingMethod",
    "FinancingRateInput",
    "FinancingRateOutput",
]
