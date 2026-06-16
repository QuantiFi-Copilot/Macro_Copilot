"""Pydantic schemas for the financing_rate tool.

Phase 1 PR 19.

Per-method param validation lives here as Pydantic
``@model_validator``s so the caller cannot construct an input that
fails downstream — ``method=constant_rate`` without
``constant_rate_pct`` is rejected at parse time, not at compute time.
This is the explicit guard that operationalises the "no opinionated
default rate value" principle.
"""

from __future__ import annotations

from datetime import date
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from shared.artifacts.types import Panel


FinancingMethod = Literal[
    "constant_rate",
    "overnight_index_proxy",
    "term_repo_curve",
    "gc_special_blend",
]


_VALID_PROXY_CURVES = frozenset({
    "USD_SOFR_OIS",
    "EUR_ESTR_OIS",
    "GBP_SONIA_OIS",
    "JPY_TONA_OIS",
    "AUD_AONIA_OIS",
    "CAD_CORRA_OIS",
})


class FinancingRateInput(BaseModel):
    """Parameters for ``compute_financing_rate``.

    Per-method requirements (enforced by ``_validate_method_params``):

      - ``method=constant_rate``:
            ``constant_rate_pct`` is REQUIRED.  No default — caller
            picks the value.
      - ``method=overnight_index_proxy``:
            ``proxy_curve`` is REQUIRED.  Must be one of:
            USD_SOFR_OIS, EUR_ESTR_OIS, GBP_SONIA_OIS, JPY_TONA_OIS,
            AUD_AONIA_OIS, CAD_CORRA_OIS.
      - ``method=term_repo_curve`` / ``gc_special_blend``:
            V1 raises NotImplementedError at compute time.  Inputs
            still parse so the YAML enum and Pydantic schema stay
            in sync with the V2 surface.
    """

    model_config = ConfigDict(extra="forbid")

    method: Optional[FinancingMethod] = Field(
        default=None,
        description=(
            "Financing-rate method.  None → resolved from config.yaml "
            "(``overnight_index_proxy``).  Closed-enum: see "
            "``FinancingMethod`` Literal."
        ),
    )
    start_date: date = Field(
        ...,
        description="Earliest date to include (inclusive).",
    )
    end_date: date = Field(
        ...,
        description="Latest date to include (inclusive).",
    )
    as_of_date: Optional[date] = Field(
        default=None,
        description=(
            "Optional as-of date (YYYY-MM-DD): compute as of this trade "
            "date instead of the latest available data.  None → latest "
            "(live snapshot).  Supply a date for a historical, replayable "
            "view.  When set, caps the financing-rate window's upper bound "
            "to this trade date (never extends past the requested "
            "``end_date``)."
        ),
    )

    # --- Per-method params ------------------------------------------------
    constant_rate_pct: Optional[float] = Field(
        default=None,
        description=(
            "REQUIRED when method=constant_rate.  Rate in PERCENT "
            "(e.g. 5.30 for 5.30%).  No Python-side default — caller "
            "supplies the rate explicitly."
        ),
    )
    proxy_curve: Optional[str] = Field(
        default=None,
        description=(
            "REQUIRED when method=overnight_index_proxy.  One of: "
            f"{sorted(_VALID_PROXY_CURVES)}.  No default — caller picks "
            "the OIS curve to use as the financing proxy."
        ),
    )

    # --- Optional knobs ---------------------------------------------------
    day_count_basis: Optional[str] = Field(
        default=None,
        description=(
            "Day-count basis for the per-day accrual fraction.  None → "
            "resolved from config.yaml (``act_360``).  Allowed: "
            "['act_360', 'act_365', 'act_act_isda']."
        ),
    )
    calendar: Optional[str] = Field(
        default=None,
        description=(
            "Calendar for the constant-rate method.  None → resolved "
            "from config.yaml (``business_days``).  Allowed: "
            "['business_days', 'calendar_days'].  Ignored for the "
            "overnight_index_proxy method (uses the DB's observed "
            "trading days)."
        ),
    )

    @model_validator(mode="after")
    def _validate_dates(self) -> "FinancingRateInput":
        if self.end_date < self.start_date:
            raise ValueError(
                f"end_date {self.end_date} cannot be before start_date "
                f"{self.start_date}."
            )
        return self

    @model_validator(mode="after")
    def _validate_method_params(self) -> "FinancingRateInput":
        if self.method == "constant_rate":
            if self.constant_rate_pct is None:
                raise ValueError(
                    "method=constant_rate requires ``constant_rate_pct`` "
                    "(no Python-side default).  Pass the rate in "
                    "PERCENT (e.g. 5.30 for 5.30%)."
                )
        if self.method == "overnight_index_proxy":
            if self.proxy_curve is None:
                raise ValueError(
                    "method=overnight_index_proxy requires ``proxy_curve``.  "
                    f"Pass one of: {sorted(_VALID_PROXY_CURVES)}."
                )
            if self.proxy_curve not in _VALID_PROXY_CURVES:
                raise ValueError(
                    f"proxy_curve={self.proxy_curve!r} is not a recognised "
                    f"OIS family.  Allowed: {sorted(_VALID_PROXY_CURVES)}."
                )
        return self


class FinancingRateOutput(BaseModel):
    """Top-level response.

    PR 20 declares ``panel: Panel`` as a first-class field on the
    output schema so the workflow executor's
    ``tool_output_to_artifact_panel`` bridge can extract the typed
    artifact via standard Pydantic attribute traversal.  The MCP
    layer drops ``panel`` before serialising to JSON for the LLM
    (it carries the per-day rate data, which would blow the LLM's
    token budget).
    """

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    method: FinancingMethod
    as_of_start: str
    as_of_end: str
    n_observations: int
    mean_rate_pct: Optional[float] = Field(
        default=None,
        description="Arithmetic mean of the daily rate series (PERCENT).",
    )
    methodology_disclosures: List[str] = Field(
        default_factory=list,
        description=(
            "Inline methodology disclosure for the workspace card.  "
            "Includes method-specific notes (e.g. 'OIS overnight proxy "
            "uses 1W tenor as the shortest-available approximation; "
            "true O/N OIS quotes are not yet ingested')."
        ),
    )
    panel: Optional[Panel] = Field(
        default=None,
        description=(
            "Single-column Panel carrying the daily financing-rate "
            "series in PERCENT.  Present when compute succeeded; "
            "the MCP layer drops this field before serialising "
            "for the LLM."
        ),
    )


__all__ = [
    "FinancingMethod",
    "FinancingRateInput",
    "FinancingRateOutput",
]
