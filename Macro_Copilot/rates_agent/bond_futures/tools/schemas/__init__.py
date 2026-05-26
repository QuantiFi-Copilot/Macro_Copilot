"""Re-exports for bond_futures tool schemas.

Populated as the OpenClaw primitive-automation factory builds each
catalogued primitive — each tool's schemas re-export here so the MCP
server (``rates_agent/bond_futures/mcp_server.py``) can import a stable
hub. Mirrors ``rates_agent/ois/tools/schemas/__init__.py``'s pattern.
"""

from rates_agent.bond_futures.tools.futures_price_level.schemas import (
    FuturesPriceLevelCurrentMetrics,
    FuturesPriceLevelInput,
    FuturesPriceLevelOutput,
    FuturesPriceLevelTimeSeriesRow,
)
from rates_agent.bond_futures.tools.futures_volume_oi.schemas import (
    FuturesVolumeOICurrentMetrics,
    FuturesVolumeOIInput,
    FuturesVolumeOIOutput,
    FuturesVolumeOITimeSeriesRow,
)
from rates_agent.bond_futures.tools.scan_bond_futures_extremes.schemas import (
    ScanBondFuturesExtremesInput,
    ScanBondFuturesExtremesOutput,
    ScanBondFuturesExtremesResultRow,
    ScanMetric,
)


__all__ = [
    "FuturesPriceLevelInput",
    "FuturesPriceLevelCurrentMetrics",
    "FuturesPriceLevelTimeSeriesRow",
    "FuturesPriceLevelOutput",
    "FuturesVolumeOIInput",
    "FuturesVolumeOICurrentMetrics",
    "FuturesVolumeOITimeSeriesRow",
    "FuturesVolumeOIOutput",
    "ScanBondFuturesExtremesInput",
    "ScanBondFuturesExtremesOutput",
    "ScanBondFuturesExtremesResultRow",
    "ScanMetric",
]
