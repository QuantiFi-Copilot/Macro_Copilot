"""shared.artifacts.units — re-export of the canonical unit enum.

Per build plan v5 / R3: the artifact layer reuses ``TimeSeriesUnits``
from ``shared.schemas.time_series`` directly.  No parallel taxonomy.

If a future operator needs a unit the closed enum does not cover
(e.g., ``unitless`` distinct from ``ratio``), extend
``TimeSeriesUnits`` centrally — do NOT introduce a second enum here.
That single rule keeps wire formats, primitive outputs, adapters, and
operator artifacts in one taxonomy.
"""

from shared.schemas.time_series import TimeSeriesUnits


__all__ = ["TimeSeriesUnits"]
