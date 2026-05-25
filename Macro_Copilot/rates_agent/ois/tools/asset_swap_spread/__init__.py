"""
rates_agent.ois.tools.asset_swap_spread — per-bond INGESTED Bloomberg
asset-swap spread (``ASSET_SWAP_SPD_MID``) for one sovereign cash
bond identified by ``vendor_ticker``.

Lives under the OIS domain per the bot's catalog (build_order 33,
``ois__asset_swap_spread``) — the OIS MCP server is the natural host
mirroring the precedent set by ``rates_agent/ois/tools/swap_spread/``
— but operates against the ``sovereign_cash_bonds`` playbook universe.

Pure INGEST primitive (P12) — the asw_spread value is the vendor's
Bloomberg ``ASSET_SWAP_SPD_MID``, surfaced VERBATIM with no rounding
(SQL parity within 1e-9 holds against a raw SELECT).  Never
recomputed from STIR / OIS / discount factors.

PR4 differentiation (load-bearing): distinct from
``rates_agent/ois/tools/swap_spread/`` which is a cross-domain
par-par approximation ``(sovereign_yield - ois_rate) * 100``.  Both
primitives' methodology cards cite each other (symmetric cite per
catalog guardrail).

External callers reach the public API via this package's path:

    from rates_agent.ois.tools.asset_swap_spread import (
        get_asset_swap_spread,
        AssetSwapSpreadInput,
        AssetSwapSpreadOutput,
        AssetSwapSpreadUnavailableError,
        CONFIG_PATH,
    )

Or via the OIS schemas hub:

    from rates_agent.ois.tools.schemas import (
        AssetSwapSpreadInput,
        AssetSwapSpreadUnavailableError,
    )

Note for tests: ``__init__.py`` re-exports only public symbols.
``fetch_single_bond_series`` and ``date`` live inside ``compute.py``'s
namespace; tests must patch them at
``...asset_swap_spread.compute.<name>``, NOT on the package init.
"""

from rates_agent.ois.tools.asset_swap_spread.compute import (
    CONFIG_PATH,
    get_asset_swap_spread,
)
from rates_agent.ois.tools.asset_swap_spread.schemas import (
    AssetSwapSpreadInput,
    AssetSwapSpreadMetrics,
    AssetSwapSpreadOutput,
    AssetSwapSpreadUnavailableError,
)


__all__ = [
    "CONFIG_PATH",
    "get_asset_swap_spread",
    "AssetSwapSpreadInput",
    "AssetSwapSpreadMetrics",
    "AssetSwapSpreadOutput",
    "AssetSwapSpreadUnavailableError",
]
