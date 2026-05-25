"""compute.py — Deterministic inflation-linker Panel assembly.

Phase 1 / Plan §5 Group 3 #20.

Builds a closed-family ``Panel`` artifact across the inflation-
linker universe (USD_TIPS / GBP_LINKER / EUR_FR_LINKER / CAD_RRB)
with rows = trade_date and columns = vendor_ticker (the canonical
Bloomberg identifier of each linker bond).  Substrate primitive
for downstream operator-shaped work (cross-country real-rate
regression, real-yield PCA, RV scanning at the row-vector level).

Column-key honesty (load-bearing)
---------------------------------
The catalog mandates ``columns = security_name``, but the
inflation-linker universe's
``macro_data.instrument_metadata_history.security_name`` is
universally NULL on the live SCD2 rows (the orchestrator's
pre-flight verified 0 non-NULL security_name rows across all 24
inflation_linker instruments).  Per the no-proxy rule this
primitive surfaces ``vendor_ticker`` under its own honest name
(NOT relabelled as ``security_name``).  Same treatment
``build_zcis_panel`` (commit 32c386f) and
``scan_inflation_linkers_extremes`` (commit 91a5714) apply.  The
substitution is disclosed on the methodology card's
``security_name_caveat`` field so the desk reader sees that the
naming did not silently swap labels.

No-proxy guard
--------------
The shared fetcher (``fetch_linker_panel_by_vendor_ticker``)
constrains on ``instrument_type='inflation_linker'``.  Unlike the
ZCIS helpers there is no ``pricing_type`` no-proxy guard — every
linker row carries ``attributes ->> 'pricing_type' = 'real_yield'``
and there is no proxy-pricing variant analogous to the ZCIS year-
on-year / zero-coupon split.  The
``instrument_type='inflation_linker'`` filter alone is the
structural identity guarantee that the primitive cannot silently
surface nominal sovereign rows under a real-yield label
(``instrument_type`` is a code-owned invariant — NOT YAML-tunable;
DESIGN_PRINCIPLES.md §5 keeps structural identity in code).

Test seam
---------
``fetch_linker_panel_by_vendor_ticker`` and
``fetch_linker_universe`` are imported here at module level so
unit tests can monkeypatch them via
``patch("rates_agent.inflation_indexed_bonds.tools.build_linker_panel.compute.X")``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
from sqlalchemy.engine import Engine

from rates_agent.inflation_indexed_bonds.tools.build_linker_panel.schemas import (
    BuildLinkerPanelInput,
    BuildLinkerPanelOutput,
)
from shared.analytics.panel_assembly import (
    apply_missing_data_policy,
    fetch_linker_panel_by_vendor_ticker,
    fetch_linker_universe,
    infer_units_for_field,
)
from shared.artifacts.lineage import Lineage, PrimitiveStep
from shared.artifacts.missingness import RawNoCleaning
from shared.artifacts.types import Panel
from shared.artifacts.units import TimeSeriesUnits
from shared.config import ToolConfig, load_tool_config


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"

_TOOL_NAME = "build_linker_panel_tool"
_TOOL_VERSION = "1.0.0"

# Full inflation-linker universe — matches the closed-family
# Literal in schemas.py and the ``inflation_indexed_bonds.yml``
# playbook.  Used to expand a None ``curve_families`` input into
# the live full universe scope (the fetcher itself is universe-
# agnostic; the expansion happens here so the methodology card
# discloses what scope was resolved).
_LINKER_FULL_UNIVERSE: List[str] = [
    "USD_TIPS",
    "GBP_LINKER",
    "EUR_FR_LINKER",
    "CAD_RRB",
]


# ============================================================================
# PUBLIC API
# ============================================================================


def build_linker_panel(
    engine: Engine,
    params: BuildLinkerPanelInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    """Assemble an inflation-linker Panel across the USD_TIPS /
    GBP_LINKER / EUR_FR_LINKER / CAD_RRB universe.

    Returns a dict carrying the wire-friendly summary
    (``output.model_dump()``) AND the typed Panel artifact under
    the ``"panel"`` key — the workflow executor extracts the typed
    artifact via ``BuildLinkerPanelOutput.model_validate(...)``;
    the MCP layer drops ``panel`` before serialising for the LLM
    (full per-row data blows the LLM's token budget).

    On recoverable failures returns ``{"error": "..."}`` with a
    human-readable message — same envelope build_zcis_panel /
    sovereign_yield_panel / every sibling rates tool uses.
    """
    if config is None:
        config = load_tool_config(CONFIG_PATH)

    # ------------------------------------------------------------------
    # Pull conventions
    # ------------------------------------------------------------------
    default_field = config.convention_value("default_field_name")
    ffill_limit = int(config.convention_value("ffill_limit_days"))
    default_missing_policy = config.convention_value(
        "default_missing_data_policy",
    )
    default_calendar_policy = config.convention_value("calendar_policy")
    methodology_label = config.methodology.what_it_does.strip()

    # Source-tag mirror for the methodology card.  Ships unchanged
    # with the YAML; bumping it requires a deliberate edit there
    # (which the cross-config lint will surface).
    ffill_source_tag = config.conventions["ffill_limit_days"].source

    field_name_resolved = (
        params.field_name if params.field_name is not None else default_field
    )
    calendar_policy = (
        params.calendar_policy
        if params.calendar_policy is not None
        else default_calendar_policy
    )
    missing_policy = (
        params.missing_data_policy
        if params.missing_data_policy is not None
        else default_missing_policy
    )

    # ------------------------------------------------------------------
    # Resolve the curve_family universe scope.
    #
    # Curve families: caller's explicit list wins; None expands to
    # the full linker universe.  The closed-family Literal in the
    # input schema enforces honesty on the type level.
    #
    # Unlike ZCIS there is no per-query ``tenors`` knob — linkers
    # are specific-maturity bonds, not tenor-pillar swaps (see the
    # schemas.py module docstring).
    # ------------------------------------------------------------------
    resolved_curve_families: List[str] = list(
        params.curve_families
        if params.curve_families is not None
        else _LINKER_FULL_UNIVERSE
    )

    universe_meta = fetch_linker_universe(
        engine=engine,
        curve_families=resolved_curve_families,
    )
    if universe_meta.empty:
        return {
            "error": (
                f"No inflation-linker instruments found in "
                f"instrument_master for "
                f"curve_families={resolved_curve_families!r} with "
                "instrument_type='inflation_linker'.  Verify the "
                "inflation_indexed_bonds playbook ingestion (see "
                "rates_agent/playbooks/inflation_indexed_bonds.yml — "
                "V1 ingested universe: USD_TIPS (4 bonds) / "
                "GBP_LINKER (9 bonds) / EUR_FR_LINKER (5 bonds) / "
                "CAD_RRB (6 bonds))."
            )
        }

    # ------------------------------------------------------------------
    # Fetch the wide panel via the shared backend.  The backend
    # owns the structural ``instrument_type='inflation_linker'``
    # guard so a future second caller cannot bypass it.
    # ------------------------------------------------------------------
    raw_panel = fetch_linker_panel_by_vendor_ticker(
        engine=engine,
        curve_families=resolved_curve_families,
        field_name=field_name_resolved,
        start_date=params.start_date,
        end_date=params.end_date,
        ffill_limit_days=ffill_limit,
    )
    if raw_panel.empty:
        return {
            "error": (
                f"No inflation-linker observations found for "
                f"curve_families={resolved_curve_families!r}, "
                f"field_name={field_name_resolved!r} between "
                f"{params.start_date.isoformat()} and "
                f"{params.end_date.isoformat() if params.end_date else 'latest'}.  "
                "Verify the inflation_indexed_bonds playbook "
                "ingestion (see rates_agent/playbooks/"
                "inflation_indexed_bonds.yml — V1 ingested "
                "universe: USD_TIPS (4 bonds) / GBP_LINKER "
                "(9 bonds) / EUR_FR_LINKER (5 bonds) / CAD_RRB "
                "(6 bonds))."
            )
        }

    # ------------------------------------------------------------------
    # Apply calendar policy.  ``business_days`` (the V1 default,
    # matching sibling build_zcis_panel + sovereign_yield_panel)
    # restricts to Mon-Fri only; ``instrument_native`` (the
    # per-query opt-in for cross-region full-universe queries)
    # passes every observed date through verbatim.  The multi-
    # region linker universe spans four trading calendars (US /
    # UK / France / Canada), so a unioned Mon-Fri filter is a
    # cross-region approximation — the methodology card's
    # ``cross_region_business_days_caveat`` field surfaces this.
    # ------------------------------------------------------------------
    if calendar_policy == "business_days":
        raw_panel = raw_panel[raw_panel.index.dayofweek < 5]
    elif calendar_policy == "instrument_native":
        pass  # explicit passthrough
    else:
        return {
            "error": (
                f"Unknown calendar_policy {calendar_policy!r}.  "
                "Expected one of ['business_days', "
                "'instrument_native']."
            )
        }

    # ------------------------------------------------------------------
    # Detect any column with ZERO observations across the window.
    # That signals a configuration / metadata bug (e.g. a bond
    # listed in instrument_master but with no enriched rows), NOT
    # a missing-data condition — surface it loudly so the caller
    # knows which leg is broken.
    # ------------------------------------------------------------------
    empty_columns = [
        col for col in raw_panel.columns if raw_panel[col].isna().all()
    ]
    if empty_columns:
        return {
            "error": (
                f"Inflation-linker columns with NO observations in "
                f"the requested window: {empty_columns!r}.  Verify "
                "each instrument has ingested rows in "
                "macro_data.market_data_daily for the requested "
                "field_name + date range."
            )
        }

    try:
        cleaned_panel = apply_missing_data_policy(raw_panel, missing_policy)
    except ValueError as exc:
        return {"error": str(exc)}

    if cleaned_panel.empty:
        return {
            "error": (
                f"After applying missing_data_policy="
                f"{missing_policy!r}, the panel has zero rows.  "
                "Either relax the policy, widen the start date, "
                "or narrow the universe."
            )
        }

    # ------------------------------------------------------------------
    # Build the per-column units map.  Every linker column shares
    # the same field_name (V1 — per-bond overrides are not exposed
    # on input), so the unit is uniform across the panel.
    # ------------------------------------------------------------------
    try:
        units_tag = TimeSeriesUnits(infer_units_for_field(field_name_resolved))
    except ValueError as exc:
        return {
            "error": (
                f"Cannot infer units for field_name="
                f"{field_name_resolved!r}: {exc}.  Extend "
                "shared/analytics/panel_assembly.py::"
                "infer_units_for_field if adding a new field / "
                "unit pair."
            )
        }
    units_by_column: Dict[str, TimeSeriesUnits] = {
        col: units_tag for col in cleaned_panel.columns
    }

    # ------------------------------------------------------------------
    # Surface the load-bearing per-curve reference metadata caveat
    # (US_CPI_URBAN / UK_RPI / EU_HICP / CAN_CPI reference
    # different inflation indices with different lags and
    # publication conventions).  The caveat strings are built from
    # the universe metadata so they stay honest if a future
    # curve_family is added.  Each curve_family also carries the
    # per-bond list (vendor_ticker / tenor / country /
    # maturity_date / security_name_attr) so the desk reader can
    # map a column back to its bond.
    # ------------------------------------------------------------------
    cf_reference: Dict[str, Dict[str, Any]] = {}
    panel_ticker_set = set(cleaned_panel.columns)
    for cf, sub in universe_meta.groupby("curve_family"):
        bonds_in_panel: List[Dict[str, Any]] = []
        for _, row in sub.iterrows():
            ticker = row["vendor_ticker"]
            if ticker not in panel_ticker_set:
                continue
            bonds_in_panel.append(
                {
                    "vendor_ticker": str(ticker),
                    "tenor": (
                        str(row["tenor"])
                        if pd.notna(row["tenor"]) else None
                    ),
                    "country": (
                        str(row["country"])
                        if pd.notna(row["country"]) else None
                    ),
                    "maturity_date": (
                        row["maturity_date"].strftime("%Y-%m-%d")
                        if pd.notna(row["maturity_date"])
                        and hasattr(row["maturity_date"], "strftime")
                        else (
                            str(row["maturity_date"])
                            if pd.notna(row["maturity_date"]) else None
                        )
                    ),
                    "security_name_attr": _first_non_null_value(
                        [row["security_name_attr"]],
                    ),
                }
            )
        if not bonds_in_panel:
            # The curve_family was requested but no bond from it
            # actually flowed into the panel (every column was
            # filtered out or NaN-dropped).  Skip — the wire
            # curve_families list is filtered to what actually
            # made it through downstream of this loop.
            continue
        cf_reference[str(cf)] = {
            "inflation_index_family": _first_non_null_value(
                sub["inflation_index_family"].tolist(),
            ),
            "pricing_type": _first_non_null_value(
                sub["pricing_type"].tolist(),
            ),
            "bond_count": len(bonds_in_panel),
            "bonds": bonds_in_panel,
        }

    # Restrict the curve_families wire list to the curve_families
    # that actually flowed into the panel.  Preserve the caller-
    # provided / default-universe input order so the wire
    # ``curve_families`` field stays consistent with the
    # vendor_ticker column ordering of the assembled Panel.
    resolved_curve_families_in_panel = [
        cf for cf in resolved_curve_families if cf in cf_reference
    ]
    if not resolved_curve_families_in_panel:
        # Defensive — the empty-panel / empty-column branches
        # above already returned an error envelope, so we should
        # never get here, but keep the fallback so the methodology
        # card is always populated.
        resolved_curve_families_in_panel = list(resolved_curve_families)

    methodology_card: Dict[str, Any] = {
        "field_name": field_name_resolved,
        "calendar_policy": calendar_policy,
        "missing_data_policy": missing_policy,
        "ffill_limit_days": ffill_limit,
        "ffill_source_tag": ffill_source_tag,
        "curve_families": resolved_curve_families_in_panel,
        "vendor_ticker_column_key": True,
        "security_name_caveat": (
            "Panel columns are keyed by ``vendor_ticker`` (the "
            "canonical Bloomberg identifier, e.g. 'GTII10 Govt' "
            "for the USD_TIPS 10Y, 'GTGBPII10Y Govt' for the "
            "GBP_LINKER 10Y).  Per the build_linker_panel catalog "
            "the ideal key is ``security_name``, but the "
            "inflation-linker universe's "
            "macro_data.instrument_metadata_history.security_name "
            "is universally NULL on the live SCD2 rows (the "
            "orchestrator's pre-flight verified 0 non-NULL "
            "security_name rows across all 24 inflation_linker "
            "instruments).  Surfacing NULL would be a dead column "
            "key; relabelling vendor_ticker under the "
            "security_name label would be a no-proxy violation.  "
            "Same treatment build_zcis_panel and "
            "scan_inflation_linkers_extremes apply."
        ),
        "index_family_caveat": (
            "Linker curves reference DIFFERENT inflation indices "
            "across USD_TIPS / GBP_LINKER / EUR_FR_LINKER / "
            "CAD_RRB (US_CPI_URBAN / UK_RPI / EU_HICP / CAN_CPI) "
            "with different index_lag and publication "
            "conventions; cross-country operators consuming this "
            "panel see all four regimes side-by-side, NOT a "
            "harmonised expected-inflation surface.  Per-curve "
            "reference metadata is exposed in "
            "``curve_family_reference``."
        ),
        "market_structure_caveat": (
            "Cross-country linker markets differ in benchmark "
            "availability at the same maturity, issuance size, "
            "liquidity premium, and deflation-floor treatment; "
            "operators that consume this panel as a substrate "
            "for cross-country RV work must apply the "
            "market-structure caveat from "
            "``scan_inflation_linkers_extremes`` to any extreme "
            "they surface from a row-vector read of the panel."
        ),
        "cross_region_business_days_caveat": (
            "The opt-in ``calendar_policy='business_days'`` "
            "policy unioned across the four-region universe is "
            "a cross-region approximation — US / UK / France / "
            "Canada have different national holiday calendars, "
            "so a Mon-Fri filter still surfaces dates that are "
            "holidays in one or more sessions.  The V1 default "
            "``instrument_native`` avoids this by surfacing "
            "every native session date; holiday-aware per-"
            "curve_family calendars require a holiday-table "
            "ingestion step not yet built (see "
            "config.yaml::methodology.planned_extensions)."
        ),
        "curve_family_reference": cf_reference,
        "methodology_label": methodology_label,
    }

    # ------------------------------------------------------------------
    # Build the lineage step.  Two callers running the same panel
    # request (same inputs, same YAML, same as-of) produce the
    # same primitive head hash; the artifact-store idempotency
    # gate then makes a second persist a no-op.  Matches the
    # discipline in build_zcis_panel / sovereign_yield_panel and
    # the ``shared.artifacts.adapters.from_time_series`` lift
    # path.
    # ------------------------------------------------------------------
    step_params: Dict[str, Any] = {
        "start_date": params.start_date.isoformat(),
        "end_date": (
            params.end_date.isoformat() if params.end_date else None
        ),
        "curve_families": resolved_curve_families_in_panel,
        "field_name": field_name_resolved,
        "calendar_policy": calendar_policy,
        "missing_data_policy": missing_policy,
        "ffill_limit_days": ffill_limit,
        "vendor_tickers": list(cleaned_panel.columns),
        "row_count": int(len(cleaned_panel)),
    }
    as_of_date_iso = cleaned_panel.index[-1].strftime("%Y-%m-%d")
    step = PrimitiveStep.build(
        name=_TOOL_NAME,
        version=_TOOL_VERSION,
        params=step_params,
        tool_config_hash=config.conventions_hash(),
        output_field="panel",
        as_of_date=as_of_date_iso,
        tool_config_path=str(CONFIG_PATH),
    )
    lineage = Lineage.from_steps([step])

    panel_artifact = Panel(
        payload=cleaned_panel,
        units_by_column=units_by_column,
        missingness_policy=RawNoCleaning(),
        lineage=lineage,
    )

    output = BuildLinkerPanelOutput(
        start_date=cleaned_panel.index[0].strftime("%Y-%m-%d"),
        end_date=cleaned_panel.index[-1].strftime("%Y-%m-%d"),
        row_count=int(len(cleaned_panel)),
        column_count=int(cleaned_panel.shape[1]),
        curve_families=resolved_curve_families_in_panel,
        vendor_tickers=list(cleaned_panel.columns),
        units_by_column={
            col: units.value for col, units in units_by_column.items()
        },
        methodology_card=methodology_card,
        panel=panel_artifact,
    )

    # ``model_dump(mode="python")`` preserves the typed Panel
    # object (vs ``mode="json"`` which would lose the typed
    # identity).  The workflow executor re-validates via
    # ``BuildLinkerPanelOutput.model_validate(...)`` to recover
    # the typed Panel — Pydantic does this correctly because
    # Panel is a BaseModel with ``arbitrary_types_allowed=True``.
    return output.model_dump(mode="python")


# ============================================================================
# HELPERS
# ============================================================================


def _first_non_null_value(values: List[Any]) -> Optional[Any]:
    """Return the first non-None / non-NaN value in ``values`` or
    None."""
    for v in values:
        if v is None:
            continue
        if isinstance(v, float) and pd.isna(v):
            continue
        return v
    return None


__all__ = [
    "CONFIG_PATH",
    "build_linker_panel",
]
