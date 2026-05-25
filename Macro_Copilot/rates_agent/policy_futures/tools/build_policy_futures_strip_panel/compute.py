"""compute.py — Deterministic policy-futures strip Panel assembly.

Plan §5 Group 3 #21.

Builds a closed-family ``Panel`` artifact across the policy-
futures strip universe (SOFR_FUT / SONIA_FUT /
EUR_SHORT_RATE_FUT) × strip positions 1..8 with rows =
trade_date and columns = flat ``"<CURVE_FAMILY>|<STRIP_POSITION>"``
encoded keys, values = ``implied_rate_pct`` (PERCENT).  Substrate
primitive for downstream operator-shaped work (cross-CB pricing
comparison, strip-curve PCA, RV scanning at the
curve_family × strip_position level).

Column-key encoding (load-bearing)
----------------------------------
The Panel artifact's column axis is keyed by the flat string
``"<CURVE_FAMILY>|<STRIP_POSITION>"`` (e.g. ``"SOFR_FUT|1"``,
``"EUR_SHORT_RATE_FUT|8"``).  Pydantic v2 enforces str keys on
``Dict[str, TimeSeriesUnits]``, so a literal ``pd.MultiIndex``
over tuple keys would break the typed-boundary contract.  The
flat encoding preserves BOTH pieces of information in every
column key AND remains honest under the Panel contract; the
methodology card surfaces ``column_axis_encoding`` +
``column_axis_encoding_separator`` so downstream consumers can
split keys back into ``(curve_family, strip_position)`` and the
per-column ``vendor_ticker`` lookup lives under
``curve_family_reference``.

Implied-rate conversion (metadata-driven)
-----------------------------------------
For each (curve_family, strip_position) cell:
  - inverse-priced (V1 universe — all three curve_families):
        implied_rate_pct = 100 - raw_price
  - direct-priced (none in V1):
        implied_rate_pct = raw_price
The flag is resolved per cell off
``instrument_master.attributes->>'inverse_pricing'`` via
``fetch_policy_futures_strip_universe``.  All cells WITHIN ONE
curve_family must agree on the flag — a mixed-flag set within
ONE curve_family is refused with the controlled-error envelope
(P5 — refusing to silently re-label a mixed convention).  Cross-
curve-family flag inconsistency is NOT refused (it is expected
as the universe grows beyond V1 direct-priced families).

EUR_SHORT_RATE_FUT preservation (ADR 0011 V1)
---------------------------------------------
Unlike the sibling ``futures_pack_average_simple`` primitive
(commit 80a26cd), which raises ``NotImplementedError`` for
``curve_family = EUR_SHORT_RATE_FUT`` because a pack-average
output cannot be computed honestly without per-row
``delivery_month_type`` metadata, this Panel primitive
PRESERVES the EUR_SHORT_RATE_FUT raw strip rows side-by-side
with SOFR_FUT / SONIA_FUT.  The Panel is the SUBSTRATE — no
aggregation step — so the Buba serial+quarterly mix sits on the
wire exactly as it sits on the DB; the methodology card's
``curve_family_reference['EUR_SHORT_RATE_FUT']`` block
discloses the mix presence + cites the futures_pack_average_
simple PR11 planned-extension entry.

Test seam
---------
``fetch_policy_futures_strip_panel`` and
``fetch_policy_futures_strip_universe`` are imported here at
module level so unit tests can monkeypatch them via
``patch("rates_agent.policy_futures.tools.build_policy_futures_strip_panel.compute.X")``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
from sqlalchemy.engine import Engine

from rates_agent.policy_futures.tools.build_policy_futures_strip_panel.schemas import (
    BuildPolicyFuturesStripPanelInput,
    BuildPolicyFuturesStripPanelOutput,
)
from shared.analytics.panel_assembly import (
    _strip_panel_column_key,
    apply_missing_data_policy,
    fetch_policy_futures_strip_panel,
    fetch_policy_futures_strip_universe,
    infer_units_for_field,
)
from shared.artifacts.lineage import Lineage, PrimitiveStep
from shared.artifacts.missingness import RawNoCleaning
from shared.artifacts.types import Panel
from shared.artifacts.units import TimeSeriesUnits
from shared.config import ToolConfig, load_tool_config


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"

_TOOL_NAME = "build_policy_futures_strip_panel_tool"
_TOOL_VERSION = "1.0.0"

# Full policy-futures universe — matches the closed-family
# Literal in schemas.py and the ``policy_futures.yml`` playbook.
# Used to expand a None ``curve_families`` input into the live
# full universe scope (the fetcher itself is universe-agnostic;
# the expansion happens here so the methodology card discloses
# what scope was resolved).
_POLICY_FUTURES_STRIP_FULL_UNIVERSE: List[str] = [
    "SOFR_FUT",
    "SONIA_FUT",
    "EUR_SHORT_RATE_FUT",
]

# Full strip-position universe (V1 playbook universe).
_POLICY_FUTURES_STRIP_FULL_POSITIONS: List[int] = [
    1, 2, 3, 4, 5, 6, 7, 8,
]

# Disclosure-only short-rate regime mapping.  Matches the sibling
# futures_strip_snapshot / futures_pack_average_simple /
# scan_policy_futures_extremes regime maps verbatim so cross-tool
# reads stay consistent.  RFR = compounded daily risk-free rate
# (SOFR / SONIA); IBOR = unsecured 3M term IBOR (Euribor).  Lives
# in compute (NOT YAML) because the per-curve label is structural
# disclosure tied to the curve_family Literal, not a tunable knob.
_SHORT_RATE_REGIME_MAP: Dict[str, str] = {
    "SOFR_FUT": "RFR",
    "SONIA_FUT": "RFR",
    "EUR_SHORT_RATE_FUT": "IBOR",
}


# ============================================================================
# PUBLIC API
# ============================================================================


def build_policy_futures_strip_panel(
    engine: Engine,
    params: BuildPolicyFuturesStripPanelInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    """Assemble a policy-futures strip Panel across the
    SOFR_FUT / SONIA_FUT / EUR_SHORT_RATE_FUT × strip-positions
    1..8 universe.

    Returns a dict carrying the wire-friendly summary
    (``output.model_dump()``) AND the typed Panel artifact under
    the ``"panel"`` key — the workflow executor extracts the
    typed artifact via
    ``BuildPolicyFuturesStripPanelOutput.model_validate(...)``;
    the MCP layer drops ``panel`` before serialising for the LLM
    (full per-row data blows the LLM's token budget).

    On recoverable failures returns ``{"error": "..."}`` with a
    human-readable message — same envelope build_zcis_panel /
    build_linker_panel / every sibling rates tool uses.
    """
    if config is None:
        config = load_tool_config(CONFIG_PATH)

    # ------------------------------------------------------------------
    # Pull conventions
    # ------------------------------------------------------------------
    default_field = config.convention_value("default_price_field")
    ffill_limit = int(config.convention_value("ffill_limit_days"))
    default_missing_policy = config.convention_value(
        "default_missing_data_policy",
    )
    default_calendar_policy = config.convention_value("calendar_policy")
    methodology_label = config.methodology.what_it_does.strip()

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
    # Resolve the (curve_family, strip_position) universe scope.
    # Caller's explicit lists win; None expands to the full
    # universe (full 3-family Cartesian × 8-position strip).  The
    # closed-family Literals in the input schema enforce honesty
    # on the type level.
    # ------------------------------------------------------------------
    resolved_curve_families: List[str] = list(
        params.curve_families
        if params.curve_families is not None
        else _POLICY_FUTURES_STRIP_FULL_UNIVERSE
    )
    resolved_strip_positions: List[int] = sorted(
        int(p) for p in (
            params.strip_positions
            if params.strip_positions is not None
            else _POLICY_FUTURES_STRIP_FULL_POSITIONS
        )
    )

    # ------------------------------------------------------------------
    # Fetch the wide panel + universe metadata via the shared
    # backend.  The backend owns the structural
    # ``instrument_type='policy_future' AND is_rolling_contract=
    # TRUE`` guards so a future second caller cannot bypass them.
    # ------------------------------------------------------------------
    raw_panel, universe_meta = fetch_policy_futures_strip_panel(
        engine=engine,
        curve_families=resolved_curve_families,
        strip_positions=resolved_strip_positions,
        field_name=field_name_resolved,
        start_date=params.start_date,
        end_date=params.end_date,
        ffill_limit_days=ffill_limit,
    )
    if universe_meta.empty:
        return {
            "error": (
                f"No policy-futures instruments found in "
                f"instrument_master for "
                f"curve_families={resolved_curve_families!r}, "
                f"strip_positions={resolved_strip_positions!r} "
                f"with instrument_type='policy_future' AND "
                "is_rolling_contract=TRUE.  Verify the "
                "policy_futures playbook ingestion (see "
                "rates_agent/playbooks/policy_futures.yml — V1 "
                "ingested universe: SOFR_FUT / SONIA_FUT / "
                "EUR_SHORT_RATE_FUT across strip positions 1..8)."
            )
        }
    if raw_panel.empty:
        return {
            "error": (
                f"No policy-futures observations found for "
                f"curve_families={resolved_curve_families!r}, "
                f"strip_positions={resolved_strip_positions!r}, "
                f"field_name={field_name_resolved!r} between "
                f"{params.start_date.isoformat()} and "
                f"{params.end_date.isoformat() if params.end_date else 'latest'}.  "
                "Verify the policy_futures playbook ingestion "
                "(see rates_agent/playbooks/policy_futures.yml — "
                "V1 ingested universe: SOFR_FUT / SONIA_FUT / "
                "EUR_SHORT_RATE_FUT across strip positions 1..8)."
            )
        }

    # ------------------------------------------------------------------
    # Resolve the per-curve-family inverse-pricing flag from the
    # universe metadata.  All cells within ONE curve_family must
    # agree on the flag; a mixed-flag set within ONE curve_family
    # is refused (P5).  Cross-curve-family flag inconsistency is
    # NOT refused (it is expected as the universe grows beyond V1
    # direct-priced families).
    # ------------------------------------------------------------------
    inverse_pricing_by_curve_family: Dict[str, bool] = {}
    for cf, sub in universe_meta.groupby("curve_family"):
        flags = sub["inverse_pricing"].dropna().tolist()
        distinct = set(bool(f) for f in flags)
        if not flags:
            return {
                "error": (
                    f"Policy-futures curve_family={cf!r} cells in "
                    "instrument_master are missing the "
                    "'inverse_pricing' flag on attributes.  The "
                    "implied-rate conversion rule is metadata-"
                    "driven (PR8 / P6 — no hidden methodology in "
                    "code); a curve_family without this flag "
                    "cannot be priced honestly.  Surface this as a "
                    "metadata gap to the playbook owner."
                )
            }
        if len(distinct) > 1:
            per_pos = ", ".join(
                f"strip_position={int(r['strip_position'])}: "
                f"inverse_pricing={r['inverse_pricing']!r}"
                for _, r in sub.iterrows()
            )
            return {
                "error": (
                    f"Policy-futures curve_family={cf!r} cells "
                    "disagree on the 'inverse_pricing' flag "
                    f"({per_pos}).  A same-curve panel column "
                    "group requires every cell on that curve_"
                    "family to use the same price-to-rate "
                    "convention; refusing to produce a mixed-"
                    "convention panel column group (P5)."
                )
            }
        inverse_pricing_by_curve_family[str(cf)] = next(iter(distinct))

    # ------------------------------------------------------------------
    # Convert raw_price columns to implied_rate_pct columns
    # per-cell using the curve-family inverse-pricing flag.
    # Cells with inverse_pricing=True: implied_rate_pct = 100 -
    # raw_price.  Cells with inverse_pricing=False (none in V1):
    # implied_rate_pct = raw_price.
    # ------------------------------------------------------------------
    implied_rate_panel = raw_panel.copy()
    for col in implied_rate_panel.columns:
        cf, _pos_str = col.split("|", 1)
        inv = inverse_pricing_by_curve_family.get(cf)
        if inv is None:
            # Should not happen — universe_meta groupby above
            # would have produced an entry for every cf in the
            # raw_panel columns.  Defensive belt-and-braces.
            return {
                "error": (
                    f"Internal inconsistency: column {col!r} "
                    f"references curve_family={cf!r} which has no "
                    "resolved inverse_pricing flag from "
                    "universe_meta.  Verify the policy_futures "
                    "playbook ingestion."
                )
            }
        if inv:
            implied_rate_panel[col] = 100.0 - implied_rate_panel[col]

    # ------------------------------------------------------------------
    # Apply calendar policy.  ``business_days`` (V1 default)
    # restricts to Mon-Fri only; ``instrument_native`` passes
    # every observed date through verbatim.  The multi-region
    # policy-futures universe spans three trading calendars
    # (US CME / UK ICE / EUR ICE), so a unioned Mon-Fri filter is
    # a cross-region approximation — the methodology card's
    # ``cross_region_business_days_caveat`` field surfaces this.
    # ------------------------------------------------------------------
    if calendar_policy == "business_days":
        implied_rate_panel = implied_rate_panel[
            implied_rate_panel.index.dayofweek < 5
        ]
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
    # That signals a configuration / metadata bug (e.g. a strip
    # cell listed in instrument_master but with no enriched
    # rows), NOT a missing-data condition — surface it loudly so
    # the caller knows which cell is broken.
    # ------------------------------------------------------------------
    empty_columns = [
        col for col in implied_rate_panel.columns
        if implied_rate_panel[col].isna().all()
    ]
    if empty_columns:
        return {
            "error": (
                f"Policy-futures cells with NO observations in the "
                f"requested window: {empty_columns!r}.  Verify "
                "each (curve_family, strip_position) cell has "
                "ingested rows in macro_data.market_data_daily for "
                "the requested field_name + date range."
            )
        }

    try:
        cleaned_panel = apply_missing_data_policy(
            implied_rate_panel, missing_policy,
        )
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
    # Build the per-column units map.  Every column is the
    # implied_rate_pct (PERCENT) — same unit across the panel
    # because the value field is uniform.
    # ------------------------------------------------------------------
    try:
        units_tag = TimeSeriesUnits(infer_units_for_field("PX_LAST"))
    except ValueError as exc:
        return {
            "error": (
                f"Cannot infer units for the implied-rate panel: "
                f"{exc}.  Extend shared/analytics/panel_assembly."
                "py::infer_units_for_field if adding a new field / "
                "unit pair."
            )
        }
    units_by_column: Dict[str, TimeSeriesUnits] = {
        col: units_tag for col in cleaned_panel.columns
    }

    # ------------------------------------------------------------------
    # Surface the per-curve-family reference metadata block.
    # Each row carries the per-cell (strip_position,
    # vendor_ticker, contract_code, country, currency,
    # inverse_pricing) and the curve-family-level regime label.
    # The per-curve-family Buba-mix caveat AND the SOFR / SONIA /
    # Euribor RFR-vs-IBOR caveat live here so consumers see the
    # disclosure inline.
    # ------------------------------------------------------------------
    cf_reference: Dict[str, Dict[str, Any]] = {}
    column_set = set(cleaned_panel.columns)
    for cf, sub in universe_meta.groupby("curve_family"):
        cf_str = str(cf)
        cells_in_panel: List[Dict[str, Any]] = []
        for _, row in sub.iterrows():
            pos = int(row["strip_position"])
            column_key = _strip_panel_column_key(cf_str, pos)
            if column_key not in column_set:
                continue
            cells_in_panel.append(
                {
                    "column_key": column_key,
                    "curve_family": cf_str,
                    "strip_position": pos,
                    "vendor_ticker": (
                        str(row["vendor_ticker"])
                        if pd.notna(row["vendor_ticker"]) else None
                    ),
                    "contract_code": (
                        str(row["contract_code"])
                        if pd.notna(row["contract_code"]) else None
                    ),
                    "country": (
                        str(row["country"])
                        if pd.notna(row["country"]) else None
                    ),
                    "currency": (
                        str(row["currency"])
                        if pd.notna(row["currency"]) else None
                    ),
                    "inverse_pricing": bool(row["inverse_pricing"]),
                }
            )
        if not cells_in_panel:
            continue

        regime_label = _SHORT_RATE_REGIME_MAP.get(cf_str, "UNKNOWN")
        inv_flag = inverse_pricing_by_curve_family.get(cf_str)
        cf_entry: Dict[str, Any] = {
            "short_rate_regime": regime_label,
            "inverse_pricing": bool(inv_flag) if inv_flag is not None else None,
            "cell_count": len(cells_in_panel),
            "cells": cells_in_panel,
            "regime_caveat": _regime_caveat_for(cf_str, regime_label),
        }
        if cf_str == "EUR_SHORT_RATE_FUT":
            cf_entry["buba_mix_caveat"] = (
                "EUR_SHORT_RATE_FUT (Euribor) interleaves serial "
                "and quarterly contracts at the front of the "
                "strip; the Panel preserves the raw Buba mix on "
                "every (curve_family, strip_position) cell because "
                "the panel is the SUBSTRATE — no aggregation step "
                "is applied.  ``rates_agent/playbooks/policy_"
                "futures.yml`` does not yet annotate per-row "
                "``delivery_month_type`` (``serial`` | "
                "``quarterly``), so a downstream pack-average / "
                "uniform-cadence-only consumer cannot filter the "
                "mix from this panel alone.  The sibling "
                "``futures_pack_average_simple`` primitive "
                "(commit 80a26cd) raises ``NotImplementedError`` "
                "on this curve_family for that reason — once the "
                "``delivery_month_type`` annotation lands on the "
                "playbook the pack-average gate flips AND a future "
                "panel-extension can carry the annotation per cell "
                "(see config.yaml::methodology.planned_extensions)."
            )
        cf_reference[cf_str] = cf_entry

    # Restrict the curve_families wire list to the curve_families
    # that actually flowed into the panel.  Preserve the caller-
    # provided / default-universe input order so the wire
    # ``curve_families`` field stays consistent with the column-
    # axis ordering of the assembled Panel.
    resolved_curve_families_in_panel = [
        cf for cf in resolved_curve_families if cf in cf_reference
    ]
    if not resolved_curve_families_in_panel:
        # Defensive — the empty-panel / empty-column branches
        # above already returned an error envelope, so we should
        # never get here.  Keep the fallback so the methodology
        # card is always populated.
        resolved_curve_families_in_panel = list(resolved_curve_families)

    # Restrict the strip_positions wire list to positions that
    # actually flowed into the panel (preserving ascending
    # integer order).
    panel_strip_positions = sorted({
        int(col.split("|", 1)[1])
        for col in cleaned_panel.columns
    })

    column_keys_in_panel = list(cleaned_panel.columns)

    methodology_card: Dict[str, Any] = {
        "field_name": field_name_resolved,
        "panel_value_field": "implied_rate_pct",
        "calendar_policy": calendar_policy,
        "missing_data_policy": missing_policy,
        "ffill_limit_days": ffill_limit,
        "ffill_source_tag": ffill_source_tag,
        "curve_families": resolved_curve_families_in_panel,
        "strip_positions": panel_strip_positions,
        "column_axis_encoding": (
            "<CURVE_FAMILY>|<STRIP_POSITION> (flat string).  The "
            "Panel typed artifact's ``units_by_column`` is "
            "declared Dict[str, TimeSeriesUnits] (Pydantic v2 "
            "enforces str keys), so a literal pd.MultiIndex over "
            "tuple keys would break the typed-boundary contract.  "
            "The flat encoding preserves BOTH curve_family AND "
            "strip_position in every column key AND remains "
            "honest under the Panel contract; the "
            "``curve_family_reference`` block carries the "
            "(curve_family, strip_position, vendor_ticker) "
            "decomposition for every column."
        ),
        "column_axis_encoding_separator": "|",
        "inverse_pricing_handling": (
            "implied_rate_pct = 100 - raw_price for inverse-"
            "priced cells; implied_rate_pct = raw_price for "
            "direct-priced cells.  The inverse_pricing flag is "
            "resolved per cell off "
            "``instrument_master.attributes->>'inverse_pricing'`` "
            "via fetch_policy_futures_strip_universe.  All V1 "
            "universe cells (SOFR_FUT / SONIA_FUT / "
            "EUR_SHORT_RATE_FUT) are inverse-priced; the per-"
            "curve-family flag values resolved at panel-build "
            "time are surfaced under "
            "``curve_family_reference[<cf>].inverse_pricing``."
        ),
        "cross_region_business_days_caveat": (
            "The opt-in ``calendar_policy='business_days'`` "
            "policy unioned across the three-region policy-"
            "futures universe is a cross-region approximation — "
            "US Federal / UK Bank / EUR TARGET holidays differ, "
            "so a Mon-Fri filter still surfaces dates that are "
            "holidays in one or more sessions.  The opt-in "
            "``instrument_native`` avoids this by surfacing "
            "every native session date; holiday-aware per-"
            "curve_family calendars require a holiday-table "
            "ingestion step not yet built (see "
            "config.yaml::methodology.planned_extensions)."
        ),
        "rolling_generic_strip_caveat": (
            "Each cell is the rolling-generic strip-position "
            "implied rate (the front-N quarterly STIR contract "
            "that rolls quarterly per ADR 0011).  This panel is "
            "NOT a CTD-of-futures-of-OIS strip — the CTD-implied-"
            "OIS curve is not yet a primitive in this build "
            "(ADR 0011 V1 scope: policy_futures ships strip-"
            "position-keyed monitors only).  The per-contract "
            "underlying rolls quarterly so each strip slot mixes "
            "contracts across rolls; this is the canonical desk "
            "read but it does NOT equal the price of a single "
            "underlying contract over time."
        ),
        "curve_family_reference": cf_reference,
        "methodology_label": methodology_label,
    }

    # ------------------------------------------------------------------
    # Build the lineage step.  Two callers running the same panel
    # request (same inputs, same YAML, same as-of) produce the
    # same primitive head hash; the artifact-store idempotency
    # gate then makes a second persist a no-op.
    # ------------------------------------------------------------------
    step_params: Dict[str, Any] = {
        "start_date": params.start_date.isoformat(),
        "end_date": (
            params.end_date.isoformat() if params.end_date else None
        ),
        "curve_families": resolved_curve_families_in_panel,
        "strip_positions": panel_strip_positions,
        "field_name": field_name_resolved,
        "panel_value_field": "implied_rate_pct",
        "calendar_policy": calendar_policy,
        "missing_data_policy": missing_policy,
        "ffill_limit_days": ffill_limit,
        "column_keys": column_keys_in_panel,
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

    output = BuildPolicyFuturesStripPanelOutput(
        start_date=cleaned_panel.index[0].strftime("%Y-%m-%d"),
        end_date=cleaned_panel.index[-1].strftime("%Y-%m-%d"),
        row_count=int(len(cleaned_panel)),
        column_count=int(cleaned_panel.shape[1]),
        curve_families=resolved_curve_families_in_panel,
        strip_positions=panel_strip_positions,
        column_keys=column_keys_in_panel,
        units_by_column={
            col: units.value for col, units in units_by_column.items()
        },
        methodology_card=methodology_card,
        panel=panel_artifact,
    )

    return output.model_dump(mode="python")


# ============================================================================
# HELPERS
# ============================================================================


def _regime_caveat_for(curve_family: str, regime_label: str) -> str:
    """Build the per-curve-family RFR / IBOR caveat string for the
    methodology card's ``curve_family_reference`` block."""
    if regime_label == "RFR":
        underlying = (
            "compounded daily SOFR" if curve_family == "SOFR_FUT"
            else "compounded daily SONIA" if curve_family == "SONIA_FUT"
            else "compounded daily risk-free rate"
        )
        return (
            f"{curve_family} short-rate regime: RFR "
            f"(compounded daily risk-free rate — {underlying}, "
            "3-month look-back at expiry).  Cross-CB operators "
            "consuming this panel must NOT mix the RFR strip "
            "with the IBOR Euribor strip in one read without "
            "naming the regime difference explicitly."
        )
    if regime_label == "IBOR":
        return (
            f"{curve_family} short-rate regime: IBOR (unsecured "
            "3-month term Euribor), a structurally different "
            "short-rate object from compounded daily RFRs "
            "(SOFR / SONIA).  Cross-CB operators consuming this "
            "panel must NOT mix the IBOR Euribor strip with the "
            "RFR SOFR / SONIA strips in one read without naming "
            "the regime difference explicitly."
        )
    return (
        f"{curve_family} short-rate regime label is "
        f"{regime_label!r} — verify the regime map disclosure "
        "(P5) before treating this column on the panel as a "
        "RFR / IBOR equivalent."
    )


__all__ = [
    "CONFIG_PATH",
    "build_policy_futures_strip_panel",
]
