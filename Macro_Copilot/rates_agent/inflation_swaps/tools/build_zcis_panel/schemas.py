"""Pydantic schemas for the build_zcis_panel tool.

Phase 1 / Plan §5 Group 3 #19.

The primitive's Input shape is a (start_date, end_date) date range
plus optional scoping knobs for ``curve_families`` (closed-enum ZCIS
families) and ``tenors`` (list of tenor identifiers).  Every
methodology default (field_name, ffill_limit, missingness, calendar)
flows through from ``config.yaml``; the LLM only controls the
per-query universe and the optional missingness / calendar overrides.

Closed-family discipline
------------------------
``ZcisCurveFamily`` is a closed ``Literal`` over the live ZCIS
curve_family set on this branch (``USD_ZCIS`` / ``EUR_ZCIS`` /
``GBP_ZCIS``).  Adding a new ZCIS curve family is a one-line edit
here + an ingestion-playbook update + (per ADR 0013's general
closed-family discipline) a new ADR if the addition crosses a
region / index-regime boundary.  Non-ZCIS curve families (sovereign
``UST`` / ``DE_BUND``, OIS ``USD_SOFR_OIS``, linker ``USD_TIPS``,
policy futures ``USD_RFR_STRIP``, …) are REFUSED at input-validation
time.

Column-key honesty (load-bearing)
---------------------------------
The Panel artifact's columns are keyed by ``vendor_ticker`` (the
canonical Bloomberg identifier, e.g. ``'USSWIT1 Curncy'``,
``'EUSWI10 Curncy'``).  Per the build_zcis_panel catalog entry the
ideal key would be ``security_name``, but the ZCIS universe's
``macro_data.instrument_metadata_history.security_name`` is
universally NULL on the live SCD2 rows (the
``inflation_swaps.yml`` playbook maps ``SECURITY_DES`` →
``security_name`` but the field is not populated).  Surfacing NULL
would be a dead column key; relabelling ``vendor_ticker`` under
the ``security_name`` label would violate the no-proxy rule.  The
``vendor_tickers`` wire field surfaces the column list under its
honest name; the ``methodology_card.security_name_caveat`` field
discloses the substitution explicitly.  Same no-proxy treatment
``scan_inflation_swaps_extremes`` applies (a11c095 / commit
91a5714).
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from shared.artifacts.types import Panel


# Closed-enum ZCIS curve family set.  Adding a new ZCIS curve
# family (e.g. ``JPY_ZCIS``, ``CAD_ZCIS``) is a one-line edit
# here + an ingestion playbook + a new ADR if it crosses a
# region / index-regime boundary.
ZcisCurveFamily = Literal["USD_ZCIS", "EUR_ZCIS", "GBP_ZCIS"]

_ZCIS_CURVE_FAMILIES_SET = frozenset({"USD_ZCIS", "EUR_ZCIS", "GBP_ZCIS"})


# Closed-enum missing-data policy set.  Matches sovereign_yield_panel
# and the panel_assembly helper's ``apply_missing_data_policy``.
ZcisPanelMissingDataPolicy = Literal[
    "raise",
    "forward_fill_only",
    "drop_rows_any_missing",
]


# Closed-enum calendar policy set.  Matches sovereign_yield_panel.
ZcisPanelCalendarPolicy = Literal[
    "business_days",
    "instrument_native",
]


class BuildZcisPanelInput(BaseModel):
    """Parameters the LLM extracts to assemble a ZCIS panel.

    Only legitimate per-query knobs are exposed (PR8 discipline):
    universe scoping (``curve_families``, ``tenors``), the date
    window, and the missingness / calendar overrides.  Methodology
    knobs (``field_name`` default, ``ffill_limit_days``,
    ``default_missing_data_policy``, ``calendar_policy``) live in
    ``config.yaml`` and are owned by the system, not the LLM.
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
    curve_families: Optional[List[ZcisCurveFamily]] = Field(
        default=None,
        description=(
            "ZCIS curve families to scope the panel.  None → full "
            "universe (USD_ZCIS, EUR_ZCIS, GBP_ZCIS).  Non-ZCIS "
            "curve families are REFUSED at validation; for sovereign "
            "/ OIS / linker / policy-futures panels use the "
            "corresponding domain's panel primitive."
        ),
    )
    tenors: Optional[List[str]] = Field(
        default=None,
        description=(
            "Tenor identifiers to scope the panel (e.g. "
            "['1Y', '2Y', '5Y', '10Y']).  None → every tenor present "
            "in the DB for the resolved curve_families.  Tenors that "
            "do not exist on the requested curve_families are "
            "silently dropped at fetch time; the wire surface lists "
            "the tenors that actually flowed into the panel so the "
            "caller sees what was resolved."
        ),
    )
    field_name: Optional[str] = Field(
        default=None,
        description=(
            "Bloomberg observation field for the ZCIS rate.  None "
            "(default) resolves against the YAML's "
            "``default_zcis_rate_field`` convention (currently "
            "'PX_MID' — the canonical mid quoted ZCIS rate).  Pass "
            "an explicit field name to override per query.  LLM / "
            "HTTP wrappers MUST translate their wire-level sentinel "
            "(empty string for MCP, missing param for FastAPI) to "
            "None before constructing this input — otherwise the "
            "YAML default is silently shadowed."
        ),
    )
    calendar_policy: Optional[ZcisPanelCalendarPolicy] = Field(
        default=None,
        description=(
            "Calendar policy override.  None → resolved from the "
            "YAML's ``calendar_policy`` convention (currently "
            "'business_days').  Allowed: ['business_days', "
            "'instrument_native']."
        ),
    )
    missing_data_policy: Optional[ZcisPanelMissingDataPolicy] = Field(
        default=None,
        description=(
            "Missing-data policy override.  None → resolved from "
            "the YAML's ``default_missing_data_policy`` convention "
            "(currently 'forward_fill_only').  Allowed: ['raise', "
            "'forward_fill_only', 'drop_rows_any_missing']."
        ),
    )

    @model_validator(mode="after")
    def _date_range_sensible(self) -> "BuildZcisPanelInput":
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
                if cf not in _ZCIS_CURVE_FAMILIES_SET:
                    # Defensive: Literal validation should catch
                    # this first; keep the explicit message for the
                    # rare bypass case (e.g. validate_python with
                    # ``strict=False``).
                    raise ValueError(
                        f"curve_family={cf!r} is not a recognised "
                        "ZCIS family.  Allowed: "
                        f"{sorted(_ZCIS_CURVE_FAMILIES_SET)}."
                    )
                seen_cfs.add(cf)
            if not self.curve_families:
                raise ValueError(
                    "curve_families must be non-empty when "
                    "provided; pass None to scope to the full "
                    "ZCIS universe."
                )

        if self.tenors is not None:
            seen_tnrs: set = set()
            for tnr in self.tenors:
                if not isinstance(tnr, str) or not tnr:
                    raise ValueError(
                        f"tenor {tnr!r} must be a non-empty string "
                        "(e.g. '1Y', '5Y', '10Y')."
                    )
                if tnr in seen_tnrs:
                    raise ValueError(
                        f"Duplicate tenor {tnr!r} in tenors; every "
                        "entry must be unique."
                    )
                seen_tnrs.add(tnr)
            if not self.tenors:
                raise ValueError(
                    "tenors must be non-empty when provided; pass "
                    "None to scope to every available tenor."
                )

        return self


class BuildZcisPanelOutput(BaseModel):
    """Top-level response for the build_zcis_panel tool.

    PR14-frozen wire fields mirror the sovereign_yield_panel
    summary shape (``start_date``, ``end_date``, ``row_count``,
    ``column_count``, ``curve_families``, ``vendor_tickers``,
    ``methodology_card``, ``panel``) so downstream consumers can
    rely on stable identifiers.  ``vendor_tickers`` substitutes
    for the catalog's ``securities`` field because
    ``instrument_metadata_history.security_name`` is universally
    NULL across the ZCIS universe — the
    ``methodology_card.security_name_caveat`` field discloses the
    substitution.
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
        description="Number of columns (ZCIS instruments) in the Panel.",
    )
    curve_families: List[str] = Field(
        ...,
        description=(
            "ZCIS curve families that actually flowed into the "
            "Panel (deterministic sort)."
        ),
    )
    tenors: List[str] = Field(
        ...,
        description=(
            "Tenor identifiers that actually flowed into the Panel "
            "(deterministic sort by tenor-year)."
        ),
    )
    vendor_tickers: List[str] = Field(
        ...,
        description=(
            "Column names of the Panel (one canonical Bloomberg "
            "vendor_ticker per ZCIS instrument, e.g. "
            "'USSWIT10 Curncy', 'EUSWI10 Curncy', "
            "'BPSWIT10 Curncy').  Honest substitute for the "
            "catalog's ``securities`` field — see "
            "``methodology_card.security_name_caveat``."
        ),
    )
    units_by_column: Dict[str, str] = Field(
        ...,
        description=(
            "Per-column unit tag (closed-enum ``TimeSeriesUnits`` "
            "value).  Every ZCIS column carries ``percent``."
        ),
    )
    methodology_card: Dict[str, Any] = Field(
        ...,
        description=(
            "Structured methodology disclosure block surfaced on "
            "the wire AND on the Panel artifact's lineage step.  "
            "Required keys: ``field_name``, ``calendar_policy``, "
            "``missing_data_policy``, ``ffill_limit_days``, "
            "``ffill_source_tag``, ``curve_families``, ``tenors``, "
            "``index_family_caveat``, ``security_name_caveat``, "
            "``methodology_label``."
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
    "ZcisCurveFamily",
    "ZcisPanelCalendarPolicy",
    "ZcisPanelMissingDataPolicy",
    "BuildZcisPanelInput",
    "BuildZcisPanelOutput",
]
