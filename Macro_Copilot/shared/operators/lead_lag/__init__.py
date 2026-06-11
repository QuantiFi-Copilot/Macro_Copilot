"""shared.operators.lead_lag — public API.

Stable re-exports so callers can ``from shared.operators.lead_lag
import lead_lag`` without reaching into submodules.  The four exports
(CONFIG_PATH, the operator, the Params, the Error) are the OPR16
``__init__``-export contract every operator satisfies.
"""

from pathlib import Path

from shared.operators.lead_lag.operator import lead_lag, LeadLagError
from shared.operators.lead_lag.schemas import LeadLagMethod, LeadLagParams


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


__all__ = [
    "lead_lag",
    "LeadLagError",
    "LeadLagParams",
    "LeadLagMethod",
    "CONFIG_PATH",
]
