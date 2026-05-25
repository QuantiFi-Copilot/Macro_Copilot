"""Pydantic schemas for the build_policy_futures_strip_panel tool.

Plan §5 Group 3 #21.

The primitive's Input shape is a (start_date, end_date) date range
plus optional scoping by ``curve_families`` (closed-enum policy-
futures families) and ``strip_positions`` (closed-enum 1..8) and
methodology overrides for the missingness / calendar policies.
Every methodology default (field_name, ffill_limit, missingness,
calendar) flows through from ``config.yaml``; the LLM only controls
the per-query universe and the optional missingness / calendar
overrides.

Closed-family discipline (P8 / PR8)
-----------------------------------
``PolicyFuturesStripCurveFamily`` is a closed ``Literal`` over the
live policy-futures curve_family set on this branch (``SOFR_FUT``
/ ``SONIA_FUT`` / ``EUR_SHORT_RATE_FUT``).  The set was cross-
verified against (a) ``rates_agent/playbooks/policy_futures.yml``
(3 curve families × 8 strip positions = 24 rolling-generic stems)
and (b) the live ``macro_data.instrument_master.curve_family``
distinct set under ``instrument_type='policy_future' AND
is_rolling_contract=TRUE``; both sources agree.  Non-policy-
futures curve families (sovereign ``UST`` / ``DE_BUND``, OIS
``USD_SOFR_OIS``, ZCIS ``USD_ZCIS``, linker ``USD_TIPS``, bond
futures ``UST_FUT``) are REFUSED at input-validation time.  Adding
a new policy-futures curve_family requires a new ADR + a playbook
entry + this Literal extension + a regime-map entry on every
sibling tool's ``short_rate_regime_map`` convention.

``PolicyFuturesStripPosition`` is a closed ``Literal`` over the
integer set ``[1, 2, 3, 4, 5, 6, 7, 8]`` — the V1 playbook
universe ingests exactly the front 8 quarterly strip positions on
each curve family.  Out-of-range integers are rejected at the
schema layer.

Column-key encoding (load-bearing)
----------------------------------
The Panel artifact's columns are keyed by the FLAT STRING
encoding ``"<CURVE_FAMILY>|<STRIP_POSITION>"`` (e.g.
``"SOFR_FUT|1"``, ``"EUR_SHORT_RATE_FUT|8"``).  The desk-
recognised 2-dimensional read is the
(curve_family, strip_position) matrix; the Panel typed artifact's
``units_by_column`` is declared ``Dict[str, TimeSeriesUnits]``
(Pydantic v2 enforces str keys), so a literal ``pd.MultiIndex``
over tuple keys cannot live on the artifact without breaking the
typed-boundary contract.  The flat encoding preserves BOTH pieces
of information in every column key AND remains honest under the
Panel contract; the ``methodology_card.column_axis_encoding`` and
``methodology_card.curve_family_reference`` blocks surface the
per-column (curve_family, strip_position, vendor_ticker)
decomposition explicitly so a downstream consumer can resolve a
column back to its underlying ``instrument_master`` row.

EUR_SHORT_RATE_FUT preservation (load-bearing)
---------------------------------------------
This Panel primitive PRESERVES EUR_SHORT_RATE_FUT raw rows in the
panel — the Buba serial+quarterly mix sits on the wire exactly as
it sits on the DB.  Unlike the sibling
``futures_pack_average_simple`` primitive (which REFUSES
EUR_SHORT_RATE_FUT with a PR11 NotImplementedError because a
4-leg pack-average cannot be computed honestly without per-row
``delivery_month_type`` metadata), the Panel substrate has no
aggregation step — the consumer sees both serial and quarterly
strip rows side-by-side and the methodology card's
``curve_family_reference['EUR_SHORT_RATE_FUT']`` block discloses
the Buba mix presence + cites the futures_pack_average_simple
PR11 planned-extension entry.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from shared.artifacts.types import Panel


# Closed-enum policy-futures curve family set.  Adding a new
# curve family (e.g. ``TIIE_FUT`` for Mexican TIIE swaps) is a
# one-line edit here + an ingestion playbook + a new ADR per the
# closed-family discipline.
PolicyFuturesStripCurveFamily = Literal[
    "SOFR_FUT",
    "SONIA_FUT",
    "EUR_SHORT_RATE_FUT",
]

_POLICY_FUTURES_STRIP_CURVE_FAMILIES_SET = frozenset({
    "SOFR_FUT",
    "SONIA_FUT",
    "EUR_SHORT_RATE_FUT",
})


# Canonical curve-family ordering used when the caller passes
# ``curve_families=None`` (full universe).  Matches the policy_
# futures.yml playbook section ordering AND the sibling
# futures_strip_snapshot / futures_pack_average_simple regime-map
# disclosure ordering so cross-tool reads stay consistent.
_POLICY_FUTURES_STRIP_CURVE_FAMILIES_DEFAULT_ORDER: List[str] = [
    "SOFR_FUT",
    "SONIA_FUT",
    "EUR_SHORT_RATE_FUT",
]


# Closed-enum strip position set (V1 playbook universe ingests
# positions 1..8 across all three curve families).  Expansion
# requires a playbook ingestion update + this Literal extension.
PolicyFuturesStripPosition = Literal[1, 2, 3, 4, 5, 6, 7, 8]

_POLICY_FUTURES_STRIP_POSITIONS_SET = frozenset({1, 2, 3, 4, 5, 6, 7, 8})

_POLICY_FUTURES_STRIP_POSITIONS_DEFAULT_ORDER: List[int] = [
    1, 2, 3, 4, 5, 6, 7, 8,
]


# Closed-enum missing-data policy set.  Matches sovereign_yield_
# panel / build_zcis_panel / build_linker_panel and the
# panel_assembly helper's ``apply_missing_data_policy``.
PolicyFuturesStripPanelMissingDataPolicy = Literal[
    "raise",
    "forward_fill_only",
    "drop_rows_any_missing",
]


# Closed-enum calendar policy set.  Matches sovereign_yield_panel
# / build_zcis_panel / build_linker_panel.
PolicyFuturesStripPanelCalendarPolicy = Literal[
    "business_days",
    "instrument_native",
]


class BuildPolicyFuturesStripPanelInput(BaseModel):
    """Parameters the LLM extracts to assemble a policy-futures
    strip panel.

    Only legitimate per-query knobs are exposed (PR8 discipline):
    universe scoping (``curve_families``, ``strip_positions``),
    the date window, an optional Bloomberg-field override, and
    the missingness / calendar overrides.  Methodology knobs
    (``field_name`` default, ``ffill_limit_days``,
    ``default_missing_data_policy``, ``calendar_policy``) live in
    ``config.yaml`` and are owned by the system, not the LLM.  No
    inverse-pricing-override knob is exposed — the conversion is
    metadata-driven (P5 / P6).  No ``delivery_month_type`` knob
    is exposed — the Panel substrate preserves the raw Buba mix
    on EUR_SHORT_RATE_FUT.
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
    curve_families: Optional[List[PolicyFuturesStripCurveFamily]] = Field(
        default=None,
        description=(
            "Policy-futures curve families to scope the panel.  "
            "None → full universe (SOFR_FUT, SONIA_FUT, "
            "EUR_SHORT_RATE_FUT).  Non-policy-futures curve "
            "families are REFUSED at validation; for sovereign / "
            "OIS / ZCIS / linker / bond-futures panels use the "
            "corresponding domain's panel primitive."
        ),
    )
    strip_positions: Optional[List[PolicyFuturesStripPosition]] = Field(
        default=None,
        description=(
            "Strip positions (1..8) to scope the panel.  None → "
            "the full strip (1, 2, 3, 4, 5, 6, 7, 8).  Out-of-"
            "range integers are REFUSED at validation per the "
            "closed-family discipline."
        ),
    )
    field_name: Optional[str] = Field(
        default=None,
        description=(
            "Bloomberg observation field for the policy-futures "
            "price series.  None (default) resolves against the "
            "YAML's ``default_price_field`` convention (currently "
            "'PX_LAST' — the canonical last-traded futures price "
            "mnemonic).  Pass an explicit field name to override "
            "per query.  LLM / HTTP wrappers MUST translate their "
            "wire-level sentinel (empty string for MCP, missing "
            "param for FastAPI) to None before constructing this "
            "input — otherwise the YAML default is silently "
            "shadowed."
        ),
    )
    calendar_policy: Optional[
        PolicyFuturesStripPanelCalendarPolicy
    ] = Field(
        default=None,
        description=(
            "Calendar policy override.  None → resolved from the "
            "YAML's ``calendar_policy`` convention (currently "
            "'business_days' — matches the sibling "
            "build_sovereign_yield_panel / build_zcis_panel / "
            "build_linker_panel convention value so the cross-"
            "tool config lint stays clean; the multi-region "
            "policy-futures calendar caveat is surfaced on the "
            "methodology card via "
            "``cross_region_business_days_caveat``).  Pass "
            "``instrument_native`` per query to surface every "
            "native session date across the three-region "
            "universe verbatim.  Allowed: ['business_days', "
            "'instrument_native']."
        ),
    )
    missing_data_policy: Optional[
        PolicyFuturesStripPanelMissingDataPolicy
    ] = Field(
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
    def _date_range_sensible(
        self,
    ) -> "BuildPolicyFuturesStripPanelInput":
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
                        "curve_families; every entry must be "
                        "unique."
                    )
                if cf not in _POLICY_FUTURES_STRIP_CURVE_FAMILIES_SET:
                    # Defensive: Literal validation should catch
                    # this first; keep the explicit message for
                    # the rare bypass case (e.g. validate_python
                    # with ``strict=False``).
                    raise ValueError(
                        f"curve_family={cf!r} is not a recognised "
                        "policy-futures family.  Allowed: "
                        f"{sorted(_POLICY_FUTURES_STRIP_CURVE_FAMILIES_SET)}."
                    )
                seen_cfs.add(cf)
            if not self.curve_families:
                raise ValueError(
                    "curve_families must be non-empty when "
                    "provided; pass None to scope to the full "
                    "policy-futures universe."
                )

        if self.strip_positions is not None:
            seen_pos: set = set()
            for pos in self.strip_positions:
                if not isinstance(pos, int) or isinstance(pos, bool):
                    raise ValueError(
                        f"strip_position {pos!r} must be an "
                        "integer in 1..8."
                    )
                if pos in seen_pos:
                    raise ValueError(
                        f"Duplicate strip_position {pos!r} in "
                        "strip_positions; every entry must be "
                        "unique."
                    )
                if pos not in _POLICY_FUTURES_STRIP_POSITIONS_SET:
                    raise ValueError(
                        f"strip_position={pos!r} is out of range; "
                        "the V1 playbook ingests positions 1..8 "
                        "only.  Expanding the strip requires a "
                        "playbook ingestion update + Literal "
                        "extension."
                    )
                seen_pos.add(pos)
            if not self.strip_positions:
                raise ValueError(
                    "strip_positions must be non-empty when "
                    "provided; pass None to scope to the full "
                    "strip (1..8)."
                )

        return self


class BuildPolicyFuturesStripPanelOutput(BaseModel):
    """Top-level response for the build_policy_futures_strip_panel
    tool.

    PR14-frozen wire fields mirror the sibling Panel primitives'
    summary shape (``start_date``, ``end_date``, ``row_count``,
    ``column_count``, ``curve_families``, ``strip_positions``,
    ``column_keys``, ``units_by_column``, ``methodology_card``,
    ``panel``) so downstream consumers can rely on stable
    identifiers.  ``column_keys`` carries the per-column flat
    ``"<CURVE_FAMILY>|<STRIP_POSITION>"`` encoding; the per-
    column ``(curve_family, strip_position, vendor_ticker)``
    decomposition lives on the methodology card's
    ``curve_family_reference`` block.
    """

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    start_date: str = Field(
        ...,
        description=(
            "Earliest trade_date in the assembled Panel "
            "(YYYY-MM-DD)."
        ),
    )
    end_date: str = Field(
        ...,
        description=(
            "Latest trade_date in the assembled Panel "
            "(YYYY-MM-DD)."
        ),
    )
    row_count: int = Field(
        ...,
        description="Number of rows (trade dates) in the Panel.",
    )
    column_count: int = Field(
        ...,
        description=(
            "Number of columns "
            "((curve_family, strip_position) cells) in the Panel."
        ),
    )
    curve_families: List[str] = Field(
        ...,
        description=(
            "Policy-futures curve families that actually flowed "
            "into the Panel (deterministic sort matching the "
            "column-key ordering)."
        ),
    )
    strip_positions: List[int] = Field(
        ...,
        description=(
            "Strip positions that actually flowed into the Panel "
            "(deterministic ascending integer sort)."
        ),
    )
    column_keys: List[str] = Field(
        ...,
        description=(
            "Per-column flat ``\"<CURVE_FAMILY>|<STRIP_POSITION>\"`` "
            "encoded keys, in the deterministic column-axis "
            "ordering (curve_family × strip_position Cartesian "
            "product).  The methodology card's "
            "``curve_family_reference`` block carries the "
            "(curve_family, strip_position, vendor_ticker) "
            "decomposition for each column."
        ),
    )
    units_by_column: Dict[str, str] = Field(
        ...,
        description=(
            "Per-column unit tag (closed-enum "
            "``TimeSeriesUnits`` value).  Every policy-futures "
            "implied-rate column carries ``percent``."
        ),
    )
    methodology_card: Dict[str, Any] = Field(
        ...,
        description=(
            "Structured methodology disclosure block surfaced on "
            "the wire AND on the Panel artifact's lineage step.  "
            "Required keys: ``field_name``, "
            "``panel_value_field``, ``calendar_policy``, "
            "``missing_data_policy``, ``ffill_limit_days``, "
            "``ffill_source_tag``, ``curve_families``, "
            "``strip_positions``, ``column_axis_encoding``, "
            "``column_axis_encoding_separator``, "
            "``inverse_pricing_handling``, "
            "``cross_region_business_days_caveat``, "
            "``rolling_generic_strip_caveat``, "
            "``curve_family_reference``, ``methodology_label``."
        ),
    )
    panel: Optional[Panel] = Field(
        default=None,
        description=(
            "The typed closed-family Panel artifact (rows = "
            "trade_dates, columns = encoded "
            "``\"<CURVE_FAMILY>|<STRIP_POSITION>\"`` keys, values "
            "= ``implied_rate_pct`` PERCENT).  Present when "
            "compute succeeded; the MCP layer drops this field "
            "before serialising for the LLM."
        ),
    )


__all__ = [
    "PolicyFuturesStripCurveFamily",
    "PolicyFuturesStripPanelCalendarPolicy",
    "PolicyFuturesStripPanelMissingDataPolicy",
    "PolicyFuturesStripPosition",
    "BuildPolicyFuturesStripPanelInput",
    "BuildPolicyFuturesStripPanelOutput",
]
