"""Pydantic schemas for the build_linker_panel tool.

Phase 1 / Plan §5 Group 3 #20.

The primitive's Input shape is a (start_date, end_date) date range
plus optional scoping by ``curve_families`` (closed-enum linker
families) and methodology overrides for the missingness / calendar
policies.  Every methodology default (field_name, ffill_limit,
missingness, calendar) flows through from ``config.yaml``; the LLM
only controls the per-query universe and the optional missingness /
calendar overrides.

Closed-family discipline
------------------------
``LinkerCurveFamily`` is a closed ``Literal`` over the live
inflation-linker curve_family set on this branch (``USD_TIPS`` /
``GBP_LINKER`` / ``EUR_FR_LINKER`` / ``CAD_RRB``).  The set was
cross-verified against (a) the
``rates_agent/playbooks/inflation_indexed_bonds.yml`` playbook and
(b) the live ``macro_data.instrument_master.curve_family`` distinct
set under ``instrument_type='inflation_linker' AND is_active=TRUE``
— both sources agree.  Non-linker curve families (sovereign
``UST`` / ``DE_BUND``, OIS ``USD_SOFR_OIS``, ZCIS ``USD_ZCIS``,
policy futures ``USD_RFR_STRIP``, …) are REFUSED at input-
validation time.  Adding a new linker curve_family is a one-line
edit here + an ingestion-playbook update + (per ADR 0008
+ the closed-family discipline) a new ADR if the addition crosses
a region / index-regime boundary.

No tenor scoping
----------------
Unlike ZCIS (where the universe has clean 1Y / 2Y / 3Y / 5Y / 10Y
/ 20Y / 30Y tenor pillars and a per-query ``tenors`` knob is
meaningful), the inflation-linker universe consists of specific-
maturity bonds (e.g. ``GTII10 Govt`` is the on-the-run USD 10Y
TIPS, currently maturing 2036-01-15).  There is no useful per-
query "give me only the 1Y / 5Y / 10Y legs" knob — each bond is a
single point on a maturity ladder, and the desk-honest universe
scoping is ``curve_families`` only.  Per-bond filtering belongs
on downstream operators that consume this panel's
``vendor_tickers`` column list, not on the substrate primitive.

Column-key honesty (load-bearing)
---------------------------------
The Panel artifact's columns are keyed by ``vendor_ticker`` (the
canonical Bloomberg identifier, e.g. ``'GTII10 Govt'``,
``'GTGBPII10Y Govt'``).  Per the build_linker_panel catalog entry
the ideal key would be ``security_name``, but the inflation-
linker universe's
``macro_data.instrument_metadata_history.security_name`` is
universally NULL on the live SCD2 rows (the orchestrator's
pre-flight verified 0 non-NULL security_name rows across all 24
inflation_linker instruments).  Surfacing NULL would be a dead
column key; relabelling ``vendor_ticker`` under the
``security_name`` label would violate the no-proxy rule.  The
``vendor_tickers`` wire field surfaces the column list under its
honest name; the ``methodology_card.security_name_caveat`` field
discloses the substitution explicitly.  Same no-proxy treatment
``build_zcis_panel`` (commit 32c386f) and
``scan_inflation_linkers_extremes`` (commit 91a5714) apply.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from shared.artifacts.types import Panel


# Closed-enum linker curve family set.  Adding a new linker
# curve family (e.g. ``EUR_DE_LINKER`` for German Bund linkers,
# ``AUD_LINKER`` for Australian indexed bonds) is a one-line edit
# here + an ingestion playbook + a new ADR if it crosses a
# region / index-regime boundary.
LinkerCurveFamily = Literal[
    "USD_TIPS",
    "GBP_LINKER",
    "EUR_FR_LINKER",
    "CAD_RRB",
]

_LINKER_CURVE_FAMILIES_SET = frozenset({
    "USD_TIPS",
    "GBP_LINKER",
    "EUR_FR_LINKER",
    "CAD_RRB",
})


# Closed-enum missing-data policy set.  Matches sovereign_yield_panel
# / build_zcis_panel and the panel_assembly helper's
# ``apply_missing_data_policy``.
LinkerPanelMissingDataPolicy = Literal[
    "raise",
    "forward_fill_only",
    "drop_rows_any_missing",
]


# Closed-enum calendar policy set.  Matches sovereign_yield_panel /
# build_zcis_panel.
LinkerPanelCalendarPolicy = Literal[
    "business_days",
    "instrument_native",
]


class BuildLinkerPanelInput(BaseModel):
    """Parameters the LLM extracts to assemble an inflation-linker
    panel.

    Only legitimate per-query knobs are exposed (PR8 discipline):
    universe scoping (``curve_families``), the date window, and the
    missingness / calendar overrides.  Methodology knobs
    (``field_name`` default, ``ffill_limit_days``,
    ``default_missing_data_policy``, ``calendar_policy``) live in
    ``config.yaml`` and are owned by the system, not the LLM.

    NB: there is no ``tenors`` per-query knob — linkers are
    specific-maturity bonds rather than tenor-pillar swaps, so
    per-tenor scoping is not desk-meaningful at the substrate
    layer (see the module docstring).
    """

    model_config = ConfigDict(extra="forbid")

    start_date: date = Field(
        ...,
        description="Earliest trade_date to include (inclusive).",
    )
    end_date: Optional[date] = Field(
        default=None,
        description=(
            "Latest trade_date to include (inclusive).  None → "
            "include every observation up to the latest in the DB."
        ),
    )
    as_of_date: Optional[date] = Field(
        default=None,
        description=(
            "Optional as-of date (YYYY-MM-DD): compute as of this trade "
            "date instead of the latest available data.  None → latest "
            "(live snapshot).  Supply a date for a historical, replayable "
            "view.  Acts as an upper-bound cap on the panel's trade_date "
            "window (combined with ``end_date`` — the tighter of the two "
            "wins); the universe + data fetches never surface rows after "
            "this date."
        ),
    )
    curve_families: Optional[List[LinkerCurveFamily]] = Field(
        default=None,
        description=(
            "Inflation-linker curve families to scope the panel.  "
            "None → full universe (USD_TIPS, GBP_LINKER, "
            "EUR_FR_LINKER, CAD_RRB).  Non-linker curve families "
            "are REFUSED at validation; for sovereign / OIS / ZCIS "
            "/ policy-futures panels use the corresponding "
            "domain's panel primitive."
        ),
    )
    field_name: Optional[str] = Field(
        default=None,
        description=(
            "Bloomberg observation field for the linker real "
            "yield.  None (default) resolves against the YAML's "
            "``default_field_name`` convention (currently "
            "'YLD_YTM_MID' — the canonical mid real-yield-to-"
            "maturity).  Pass an explicit field name to override "
            "per query.  LLM / HTTP wrappers MUST translate their "
            "wire-level sentinel (empty string for MCP, missing "
            "param for FastAPI) to None before constructing this "
            "input — otherwise the YAML default is silently "
            "shadowed."
        ),
    )
    calendar_policy: Optional[LinkerPanelCalendarPolicy] = Field(
        default=None,
        description=(
            "Calendar policy override.  None → resolved from the "
            "YAML's ``calendar_policy`` convention (currently "
            "'business_days' — matches the sibling "
            "build_sovereign_yield_panel + build_zcis_panel "
            "convention value so the cross-tool config lint stays "
            "clean; the multi-region linker calendar caveat is "
            "surfaced on the methodology card via "
            "``cross_region_business_days_caveat``).  Pass "
            "``instrument_native`` per query to surface every "
            "native session date across the four-region universe "
            "verbatim.  Allowed: ['business_days', "
            "'instrument_native']."
        ),
    )
    missing_data_policy: Optional[LinkerPanelMissingDataPolicy] = Field(
        default=None,
        description=(
            "Missing-data policy override.  None → resolved from "
            "the YAML's ``default_missing_data_policy`` "
            "convention (currently 'forward_fill_only').  "
            "Allowed: ['raise', 'forward_fill_only', "
            "'drop_rows_any_missing']."
        ),
    )

    @model_validator(mode="after")
    def _date_range_sensible(self) -> "BuildLinkerPanelInput":
        if self.end_date is not None and self.end_date < self.start_date:
            raise ValueError(
                f"end_date {self.end_date} cannot be before "
                f"start_date {self.start_date}."
            )

        # Enforce uniqueness on the scoping knobs — duplicate
        # entries would silently distort the universe.
        if self.curve_families is not None:
            seen_cfs: set = set()
            for cf in self.curve_families:
                if cf in seen_cfs:
                    raise ValueError(
                        f"Duplicate curve_family {cf!r} in "
                        "curve_families; every entry must be unique."
                    )
                if cf not in _LINKER_CURVE_FAMILIES_SET:
                    # Defensive: Literal validation should catch
                    # this first; keep the explicit message for the
                    # rare bypass case (e.g. validate_python with
                    # ``strict=False``).
                    raise ValueError(
                        f"curve_family={cf!r} is not a recognised "
                        "inflation-linker family.  Allowed: "
                        f"{sorted(_LINKER_CURVE_FAMILIES_SET)}."
                    )
                seen_cfs.add(cf)
            if not self.curve_families:
                raise ValueError(
                    "curve_families must be non-empty when "
                    "provided; pass None to scope to the full "
                    "linker universe."
                )

        return self


class BuildLinkerPanelOutput(BaseModel):
    """Top-level response for the build_linker_panel tool.

    PR14-frozen wire fields mirror the build_zcis_panel /
    sovereign_yield_panel summary shape
    (``start_date``, ``end_date``, ``row_count``, ``column_count``,
    ``curve_families``, ``vendor_tickers``, ``methodology_card``,
    ``panel``).  ``vendor_tickers`` substitutes for the catalog's
    ``securities`` field because
    ``instrument_metadata_history.security_name`` is universally
    NULL across the inflation-linker universe — the
    ``methodology_card.security_name_caveat`` field discloses the
    substitution.

    NB: there is no ``tenors`` wire field — linkers are specific-
    maturity bonds rather than tenor-pillar swaps (see the module
    docstring); the per-bond ``tenor`` reference column is
    surfaced in the methodology card's
    ``curve_family_reference[<cf>].bonds`` list alongside each
    bond's ``maturity_date`` so the desk reader can map a
    vendor_ticker back to its maturity ladder position.
    """

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    start_date: str = Field(
        ...,
        description=(
            "Earliest trade_date in the assembled Panel (YYYY-MM-DD)."
        ),
    )
    end_date: str = Field(
        ...,
        description=(
            "Latest trade_date in the assembled Panel (YYYY-MM-DD)."
        ),
    )
    row_count: int = Field(
        ...,
        description="Number of rows (trade dates) in the Panel.",
    )
    column_count: int = Field(
        ...,
        description=(
            "Number of columns (linker bonds) in the Panel."
        ),
    )
    curve_families: List[str] = Field(
        ...,
        description=(
            "Linker curve families that actually flowed into the "
            "Panel (deterministic sort matching the column-key "
            "ordering)."
        ),
    )
    vendor_tickers: List[str] = Field(
        ...,
        description=(
            "Column names of the Panel (one canonical Bloomberg "
            "vendor_ticker per linker bond, e.g. 'GTII10 Govt', "
            "'GTGBPII10Y Govt', 'GTFRFII10Y Govt', "
            "'GTCADII10Y Govt').  Honest substitute for the "
            "catalog's ``securities`` field — see "
            "``methodology_card.security_name_caveat``."
        ),
    )
    units_by_column: Dict[str, str] = Field(
        ...,
        description=(
            "Per-column unit tag (closed-enum ``TimeSeriesUnits`` "
            "value).  Every linker column carries ``percent``."
        ),
    )
    methodology_card: Dict[str, Any] = Field(
        ...,
        description=(
            "Structured methodology disclosure block surfaced on "
            "the wire AND on the Panel artifact's lineage step.  "
            "Required keys: ``field_name``, ``calendar_policy``, "
            "``missing_data_policy``, ``ffill_limit_days``, "
            "``ffill_source_tag``, ``curve_families``, "
            "``index_family_caveat``, ``security_name_caveat``, "
            "``methodology_label``, ``curve_family_reference``."
        ),
    )
    panel: Optional[Panel] = Field(
        default=None,
        description=(
            "The typed closed-family Panel artifact (rows = "
            "trade_dates, columns = vendor_ticker).  Present when "
            "compute succeeded; the MCP layer drops this field "
            "before serialising for the LLM."
        ),
    )


__all__ = [
    "LinkerCurveFamily",
    "LinkerPanelCalendarPolicy",
    "LinkerPanelMissingDataPolicy",
    "BuildLinkerPanelInput",
    "BuildLinkerPanelOutput",
]
