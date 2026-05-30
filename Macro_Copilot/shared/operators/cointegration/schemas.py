"""cointegration — parameter schema.

Per OPR8: every consequential method variant is an explicit typed
field; closed Literals where the choice has finite alternatives.
Each field's schema default mirrors the ``config.yaml`` default.

Multi-input operator (≥2 Series); CARRIES the two
``require_matching_*`` OPR11 controls (strict by default — same
discipline as ``correlation`` and ``rolling_correlation``).
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


# Closed set of cointegration test methods.  v1 implements only the
# Engle–Granger ADF-on-residual test (``engle_granger``, mapped to
# statsmodels' "aeg" string).  Johansen / Phillips-Ouliaris ship as
# SEPARATE operators per OPR2 if/when needed (Johansen is multivariate
# — a different input shape — and PO would be a distinct test family).
CointegrationMethod = Literal["engle_granger"]

# Trend specification for the cointegrating regression — passes through
# to ``statsmodels.tsa.stattools.coint`` directly.
CointegrationTrend = Literal["c", "ct", "ctt", "n"]

# Lag selection criterion — passes through to the inner adfuller call.
# ``None`` means "use ``max_lag`` directly without selection".  Pydantic
# carries Optional[Literal[...]] as the natural type for this.
CointegrationAutolag = Literal["aic", "bic", "t-stat"]


class CointegrationParams(BaseModel):
    """Parameters for the ``cointegration`` operator.

    Variants:
      - ``method``      — only ``engle_granger`` in v1 (closed set).
      - ``trend``       — constant only / constant+trend / quadratic /
                          none.  Changes the adfuller critical-value
                          table used inside the coint call.
      - ``max_lag``     — maximum lag length passed to adfuller; ``None``
                          defers to ``autolag``.
      - ``autolag``     — information criterion for lag selection;
                          ``None`` uses ``max_lag`` directly.
      - ``min_periods`` — minimum overlapping non-NaN observations
                          required (the Engle–Granger small-sample
                          properties are poor below ~30).

    Structural-metadata flags (OPR11), strict by default:
      - ``require_matching_frequency``
      - ``require_matching_missingness``
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    method: CointegrationMethod = "engle_granger"
    trend: CointegrationTrend = "c"
    max_lag: Optional[int] = Field(default=None, ge=0)
    autolag: Optional[CointegrationAutolag] = "aic"
    min_periods: int = Field(default=30, ge=10)
    require_matching_frequency: bool = True
    require_matching_missingness: bool = True


__all__ = [
    "CointegrationParams",
    "CointegrationMethod",
    "CointegrationTrend",
    "CointegrationAutolag",
]
