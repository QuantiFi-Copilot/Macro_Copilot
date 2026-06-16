"""compute.py — Deterministic ZCIS Panel assembly.

Phase 1 / Plan §5 Group 3 #19.

Builds a closed-family ``Panel`` artifact across the ZCIS universe
(USD / EUR / GBP) with rows = trade_date and columns = vendor_ticker
(the canonical Bloomberg identifier).  Substrate primitive for
downstream operator-shaped work (cross-curve regression, PCA, RV
scanning).

Column-key honesty (load-bearing)
---------------------------------
The catalog mandates ``columns = security_name``, but the ZCIS
universe's ``macro_data.instrument_metadata_history.security_name``
is universally NULL on the live SCD2 rows.  Per the no-proxy rule
this primitive surfaces ``vendor_ticker`` under its own honest
name (NOT relabelled as ``security_name``).  Same treatment
``scan_inflation_swaps_extremes`` applies (commit 91a5714).  The
substitution is disclosed on the methodology card's
``security_name_caveat`` field so the desk reader sees that the
naming did not silently swap labels.

No-proxy guard
--------------
The shared fetcher (``fetch_inflation_swap_panel_by_vendor_ticker``)
constrains on ``instrument_type='inflation_swap'`` AND
``pricing_type='zero_coupon_breakeven'``.  Without these filters a
future ingest of a different ZCIS pricing variant (e.g. year-on-
year inflation swaps) sharing a ``curve_family`` label would
silently flow through.  Both filters are owned by the fetcher's
code (NOT YAML) — DESIGN_PRINCIPLES.md §5 keeps structural
identity in code.

Test seam
---------
``fetch_inflation_swap_panel_by_vendor_ticker`` and
``fetch_inflation_swap_universe`` are imported here at module level
so unit tests can monkeypatch them via
``patch("rates_agent.inflation_swaps.tools.build_zcis_panel.compute.X")``.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
from sqlalchemy.engine import Engine

from rates_agent.inflation_swaps.tools.build_zcis_panel.schemas import (
    BuildZcisPanelInput,
    BuildZcisPanelOutput,
)
from shared.analytics.panel_assembly import (
    apply_missing_data_policy,
    fetch_inflation_swap_panel_by_vendor_ticker,
    fetch_inflation_swap_universe,
    infer_units_for_field,
)
from shared.analytics.rates_fetch import latest_trade_date
from shared.artifacts.lineage import Lineage, PrimitiveStep
from shared.artifacts.missingness import RawNoCleaning
from shared.artifacts.types import Panel
from shared.artifacts.units import TimeSeriesUnits
from shared.config import ToolConfig, load_tool_config


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"

_TOOL_NAME = "build_zcis_panel_tool"
_TOOL_VERSION = "1.0.0"

# Full ZCIS universe — matches the closed-family Literal in schemas.py
# and the ``inflation_swaps.yml`` playbook.  Used to expand a None
# ``curve_families`` input into the live full universe scope (the
# fetcher itself is universe-agnostic; the expansion happens here so
# the methodology card discloses what scope was resolved).
_ZCIS_FULL_UNIVERSE: List[str] = ["USD_ZCIS", "EUR_ZCIS", "GBP_ZCIS"]


# ============================================================================
# PUBLIC API
# ============================================================================


def build_zcis_panel(
    engine: Engine,
    params: BuildZcisPanelInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    """Assemble a ZCIS Panel across the USD / EUR / GBP universe.

    Returns a dict carrying the wire-friendly summary
    (``output.model_dump()``) AND the typed Panel artifact under
    the ``"panel"`` key — the workflow executor extracts the typed
    artifact via ``BuildZcisPanelOutput.model_validate(...)``; the
    MCP layer drops ``panel`` before serialising for the LLM
    (full per-row data blows the LLM's token budget).

    On recoverable failures returns ``{"error": "..."}`` with a
    human-readable message — same envelope sovereign_yield_panel /
    every sibling rates tool uses.
    """
    if config is None:
        config = load_tool_config(CONFIG_PATH)

    # ------------------------------------------------------------------
    # Pull conventions
    # ------------------------------------------------------------------
    default_field = config.convention_value("default_zcis_rate_field")
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
    # Resolve the curve_family + tenor universe scope.
    #
    # Curve families: caller's explicit list wins; None expands to
    # the full ZCIS universe.  The closed-family Literal in the
    # input schema enforces honesty on the type level.
    #
    # Tenors: caller's explicit list wins; None means "every tenor
    # present in the DB for the resolved curve_families".  We
    # resolve the live tenor set from instrument_master via
    # ``fetch_inflation_swap_universe`` rather than hard-coding it
    # in YAML (the catalog explicitly forbids hard-coding the
    # 21-stem list — that creates drift the moment the playbook
    # adds a tenor).
    # ------------------------------------------------------------------
    resolved_curve_families: List[str] = list(
        params.curve_families
        if params.curve_families is not None
        else _ZCIS_FULL_UNIVERSE
    )

    universe_meta = fetch_inflation_swap_universe(
        engine=engine,
        curve_families=resolved_curve_families,
    )
    if universe_meta.empty:
        return {
            "error": (
                f"No ZCIS instruments found in instrument_master for "
                f"curve_families={resolved_curve_families!r} with "
                "instrument_type='inflation_swap' AND "
                "pricing_type='zero_coupon_breakeven'.  Verify the "
                "inflation_swaps playbook ingestion (see "
                "rates_agent/playbooks/inflation_swaps.yml — V1 "
                "ingested universe: USD_ZCIS / EUR_ZCIS / GBP_ZCIS "
                "at 1Y / 2Y / 3Y / 5Y / 10Y / 20Y / 30Y)."
            )
        }

    available_tenors = sorted(
        set(universe_meta["tenor"].dropna().tolist()),
        key=_tenor_year_or_inf,
    )
    if params.tenors is not None:
        requested_tenors = list(params.tenors)
        resolved_tenors = [t for t in requested_tenors if t in set(available_tenors)]
        if not resolved_tenors:
            return {
                "error": (
                    f"None of the requested tenors {requested_tenors!r} "
                    f"exist on curve_families={resolved_curve_families!r}.  "
                    f"Available tenors on this universe: {available_tenors!r}."
                )
            }
    else:
        resolved_tenors = available_tenors

    # ------------------------------------------------------------------
    # Resolve the as-of anchor.  ``as_of_date`` (the optional per-query
    # historical-replay knob) caps the panel's trade_date window to the
    # supplied trade date.  None → the latest available trade_date for
    # the ZCIS universe (live snapshot) → fall back to ``date.today()``
    # only when the probe finds no rows (e.g. the offline unit-test path
    # with ``engine=None`` and a monkeypatched fetcher).
    # ``latest_trade_date`` takes a single scalar ``curve_family``, so
    # the multi-family ZCIS universe is anchored on the structural
    # ``instrument_type='inflation_swap'`` + ``field_name`` filter only
    # (the same structural filter the data fetch applies across all
    # three families); when ``as_of_date`` is None the anchor equals the
    # latest in-DB trade_date, so the cap drops zero rows beyond what the
    # caller's ``end_date`` already does and behaviour is byte-identical
    # to before.
    # ------------------------------------------------------------------
    anchor = (
        params.as_of_date
        or latest_trade_date(
            engine,
            instrument_type="inflation_swap",
            field_name=field_name_resolved,
        )
        or date.today()
    )
    # Combine the caller's explicit ``end_date`` window with the as-of
    # anchor — the tighter (earlier) of the two wins.  ``params.end_date``
    # None → anchor alone caps; both present → ``min``.
    effective_end_date: date = (
        anchor
        if params.end_date is None
        else min(params.end_date, anchor)
    )

    # ------------------------------------------------------------------
    # Fetch the wide panel via the shared backend.  The backend
    # owns the no-proxy guard (instrument_type + pricing_type
    # filters) so a future second caller cannot bypass it.
    # ------------------------------------------------------------------
    raw_panel = fetch_inflation_swap_panel_by_vendor_ticker(
        engine=engine,
        curve_families=resolved_curve_families,
        tenors=resolved_tenors,
        field_name=field_name_resolved,
        start_date=params.start_date,
        end_date=effective_end_date,
        ffill_limit_days=ffill_limit,
    )
    if raw_panel.empty:
        return {
            "error": (
                f"No ZCIS observations found for "
                f"curve_families={resolved_curve_families!r}, "
                f"tenors={resolved_tenors!r}, "
                f"field_name={field_name_resolved!r} between "
                f"{params.start_date.isoformat()} and "
                f"{params.end_date.isoformat() if params.end_date else 'latest'}.  "
                "Verify the inflation_swaps playbook ingestion "
                "(see rates_agent/playbooks/inflation_swaps.yml — "
                "V1 ingested universe: USD_ZCIS / EUR_ZCIS / "
                "GBP_ZCIS at 1Y / 2Y / 3Y / 5Y / 10Y / 20Y / 30Y)."
            )
        }

    # ------------------------------------------------------------------
    # Apply calendar policy.  ``business_days`` restricts to
    # Mon-Fri only; ``instrument_native`` passes every observed
    # date through (the DB already excludes weekends for ZCIS, so
    # this is effectively a passthrough today).
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
    # That signals a configuration / metadata bug (e.g. a tenor
    # ingested in instrument_master but with no enriched rows),
    # NOT a missing-data condition — surface it loudly so the
    # caller knows which leg is broken.
    # ------------------------------------------------------------------
    empty_columns = [
        col for col in raw_panel.columns if raw_panel[col].isna().all()
    ]
    if empty_columns:
        return {
            "error": (
                f"ZCIS columns with NO observations in the requested "
                f"window: {empty_columns!r}.  Verify each instrument "
                "has ingested rows in macro_data.market_data_daily "
                "for the requested field_name + date range."
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
                "Either relax the policy, widen the start date, or "
                "narrow the universe."
            )
        }

    # ------------------------------------------------------------------
    # Build the per-column units map.  Every ZCIS column shares the
    # same field_name (V1 — per-leg overrides are not exposed on
    # input), so the unit is uniform across the panel.
    # ------------------------------------------------------------------
    try:
        units_tag = TimeSeriesUnits(infer_units_for_field(field_name_resolved))
    except ValueError as exc:
        return {
            "error": (
                f"Cannot infer units for field_name="
                f"{field_name_resolved!r}: {exc}.  Extend "
                "shared/analytics/panel_assembly.py::"
                "infer_units_for_field if adding a new field / unit "
                "pair."
            )
        }
    units_by_column: Dict[str, TimeSeriesUnits] = {
        col: units_tag for col in cleaned_panel.columns
    }

    # ------------------------------------------------------------------
    # Surface the load-bearing per-curve reference metadata caveat
    # (US_CPI_URBAN / EU_HICP / UK_RPI references different
    # inflation indices with different lags and interpolation
    # conventions).  The caveat string is built from the universe
    # metadata so it stays honest if a future curve_family is
    # added.
    # ------------------------------------------------------------------
    cf_index_summary: Dict[str, Dict[str, Any]] = {}
    for cf, sub in universe_meta.groupby("curve_family"):
        cf_index_summary[str(cf)] = {
            "inflation_index_family": _first_non_null(
                sub["inflation_index_family"].tolist(),
            ),
            "index_lag": _first_non_null(sub["index_lag"].tolist()),
            "interpolation": _first_non_null(sub["interpolation"].tolist()),
            "underlying_index": _first_non_null(
                sub["underlying_index"].tolist(),
            ),
        }
    # Restrict the index summary to the curve_families that
    # actually flowed into the panel (the universe meta is queried
    # against the resolved curve_families list).  Preserve the
    # caller-provided / default-universe input order so the wire
    # ``curve_families`` field stays consistent with the vendor_ticker
    # column ordering of the assembled Panel.
    in_panel_set = {
        str(cf)
        for cf in universe_meta[
            universe_meta["tenor"].isin(set(resolved_tenors))
        ]["curve_family"].tolist()
    }
    resolved_curve_families_in_panel = [
        cf for cf in resolved_curve_families if cf in in_panel_set
    ]
    if not resolved_curve_families_in_panel:
        # Defensive — the empty-panel branch above already returned
        # an error envelope, so we should never get here, but keep
        # the fallback so the methodology card is always populated.
        resolved_curve_families_in_panel = list(resolved_curve_families)

    methodology_card: Dict[str, Any] = {
        "field_name": field_name_resolved,
        "calendar_policy": calendar_policy,
        "missing_data_policy": missing_policy,
        "ffill_limit_days": ffill_limit,
        "ffill_source_tag": ffill_source_tag,
        "curve_families": resolved_curve_families_in_panel,
        "tenors": list(resolved_tenors),
        "vendor_ticker_column_key": True,
        "security_name_caveat": (
            "Panel columns are keyed by ``vendor_ticker`` (the "
            "canonical Bloomberg identifier, e.g. "
            "'USSWIT10 Curncy').  Per the build_zcis_panel catalog "
            "the ideal key is ``security_name``, but the ZCIS "
            "universe's "
            "macro_data.instrument_metadata_history.security_name "
            "is universally NULL on the live SCD2 rows (the "
            "inflation_swaps.yml playbook maps SECURITY_DES to "
            "security_name but the field is not populated).  "
            "Surfacing NULL would be a dead column key; "
            "relabelling vendor_ticker under the security_name "
            "label would be a no-proxy violation.  Same treatment "
            "scan_inflation_swaps_extremes applies."
        ),
        "index_family_caveat": (
            "ZCIS curves reference DIFFERENT inflation indices "
            "across USD_ZCIS / EUR_ZCIS / GBP_ZCIS (CPI-U / "
            "HICP-xT / RPI) with different index_lag and "
            "interpolation conventions; cross-curve operators "
            "consuming this panel see all three regimes side-by-"
            "side, NOT a harmonised expected-inflation surface.  "
            "Per-curve reference metadata is exposed in "
            "``curve_family_reference``."
        ),
        "curve_family_reference": cf_index_summary,
        "methodology_label": methodology_label,
    }

    # ------------------------------------------------------------------
    # Build the lineage step.  Two callers running the same panel
    # request (same inputs, same YAML, same as-of) produce the
    # same primitive head hash; the artifact-store idempotency
    # gate then makes a second persist a no-op.  Matches the
    # discipline in sovereign_yield_panel and the
    # ``shared.artifacts.adapters.from_time_series`` lift path.
    # ------------------------------------------------------------------
    step_params: Dict[str, Any] = {
        "start_date": params.start_date.isoformat(),
        "end_date": (
            params.end_date.isoformat() if params.end_date else None
        ),
        "curve_families": resolved_curve_families_in_panel,
        "tenors": list(resolved_tenors),
        "field_name": field_name_resolved,
        "calendar_policy": calendar_policy,
        "missing_data_policy": missing_policy,
        "ffill_limit_days": ffill_limit,
        "vendor_tickers": list(cleaned_panel.columns),
        "row_count": int(len(cleaned_panel)),
    }
    as_of_date_iso = cleaned_panel.index[-1].strftime("%Y-%m-%d")
    # PR-10E Codex audit gap #1: fold data content + vintage into the
    # lineage hash via PrimitiveStep.build's optional identity bits.
    from shared.artifacts.adapters.from_time_series import (
        _compute_panel_payload_fingerprint,
    )
    data_content_fingerprint = _compute_panel_payload_fingerprint(
        cleaned_panel, units_by_column,
    )
    step = PrimitiveStep.build(
        name=_TOOL_NAME,
        version=_TOOL_VERSION,
        params=step_params,
        tool_config_hash=config.conventions_hash(),
        output_field="panel",
        as_of_date=as_of_date_iso,
        tool_config_path=str(CONFIG_PATH),
        data_content_fingerprint=data_content_fingerprint,
        data_vintage=as_of_date_iso,
    )
    lineage = Lineage.from_steps([step])

    panel_artifact = Panel(
        payload=cleaned_panel,
        units_by_column=units_by_column,
        missingness_policy=RawNoCleaning(),
        lineage=lineage,
    )

    output = BuildZcisPanelOutput(
        start_date=cleaned_panel.index[0].strftime("%Y-%m-%d"),
        end_date=cleaned_panel.index[-1].strftime("%Y-%m-%d"),
        row_count=int(len(cleaned_panel)),
        column_count=int(cleaned_panel.shape[1]),
        curve_families=resolved_curve_families_in_panel,
        tenors=list(resolved_tenors),
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
    # ``BuildZcisPanelOutput.model_validate(...)`` to recover the
    # typed Panel — Pydantic does this correctly because Panel is
    # a BaseModel with ``arbitrary_types_allowed=True``.
    return output.model_dump(mode="python")


# ============================================================================
# HELPERS
# ============================================================================


def _tenor_year_or_inf(tnr: str) -> int:
    """Stable sort key for tenor identifiers shaped as ``<int>Y``.

    Anything not in the ``<int>Y`` shape sorts to the end so the
    sort never raises — defensive, since the V1 ZCIS universe
    uses 1Y / 2Y / 3Y / 5Y / 10Y / 20Y / 30Y exclusively.
    """
    if isinstance(tnr, str) and tnr.endswith("Y") and tnr[:-1].isdigit():
        return int(tnr[:-1])
    return 10**9


def _first_non_null(values: List[Any]) -> Optional[Any]:
    """Return the first non-None value in ``values`` or None."""
    for v in values:
        if v is not None and not (isinstance(v, float) and pd.isna(v)):
            return v
    return None


__all__ = [
    "CONFIG_PATH",
    "build_zcis_panel",
]
