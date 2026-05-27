"""compute.py — Deterministic FX forwards Panel assembly.

Phase F1 (2026-05-27).

Builds a closed-family ``Panel`` artifact from a (market_scope,
tenor) slice of the FX forwards universe. Delegates fetching +
pivoting to ``shared/analytics/fx_fetch.py``
(``fetch_fx_forwards_panel`` — instrument_type='fx_forward'
pinned).

Mirrors ``fx_agent.spot.tools.fx_panel.compute`` in every
architectural respect (lineage, missing-data policy, calendar
policy, typed-Panel output) — Phase F1 compliance check
explicitly re-uses the existing pattern, no parallel invention.

Asset-agnostic shared-compute discipline
----------------------------------------
This tool is a pure fetcher+pivot+package primitive. It does
NOT compute correlation / PCA / factor loadings / regression —
those asset-agnostic operators live in ``shared/operators``
and consume the returned ``Panel`` artifact directly.

Test seam
---------
``fetch_fx_forwards_panel`` is imported at module level so unit
tests can monkeypatch via
``patch("fx_agent.forwards.tools.fx_forwards_panel.compute.fetch_fx_forwards_panel")``.

Operationalises: P3, P5, P11; PR1, PR4, PR5, PR7, PR10, PR12, PR13.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
from sqlalchemy.engine import Engine

from fx_agent.forwards.tools.fx_forwards_panel.schemas import (
    FXForwardsPanelInput,
    FXForwardsPanelOutput,
)
from shared.analytics.fx_fetch import fetch_fx_forwards_panel
from shared.artifacts.lineage import Lineage, PrimitiveStep
from shared.artifacts.missingness import RawNoCleaning
from shared.artifacts.types import Panel
from shared.artifacts.units import TimeSeriesUnits
from shared.config import ToolConfig, load_tool_config


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"

_TOOL_NAME = "calculate_fx_forwards_panel_tool"
_TOOL_VERSION = "1.0.0"

_ALLOWED_MISSING_POLICIES = frozenset({
    "raise",
    "forward_fill_only",
    "drop_rows_any_missing",
})


# ============================================================================
# PUBLIC API
# ============================================================================


def calculate_fx_forwards_panel(
    engine: Engine,
    params: FXForwardsPanelInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    """Assemble an FX forwards Panel for the requested
    (market_scope, tenor) slice.

    Returns ``output.model_dump(mode="python")`` preserving the
    typed ``Panel`` artifact under the ``panel`` key.

    Raises
    ------
    ValueError
        - if selectors violate the closed Literal set (Pydantic upstream)
        - if ``missing_data_policy`` is not in the allowed set
        - if fetched panel is empty (zero rows)
        - if any column is fully-NaN (real ingestion gap)
        - if post-policy panel is empty
    """
    if config is None:
        config = load_tool_config(CONFIG_PATH)

    default_field = config.convention_value("default_field_name")
    ffill_limit = int(config.convention_value("ffill_limit_days"))
    default_policy = config.convention_value("default_missing_data_policy")
    calendar_policy = config.convention_value("calendar_policy")

    missing_policy = params.missing_data_policy or default_policy
    if missing_policy not in _ALLOWED_MISSING_POLICIES:
        raise ValueError(
            f"missing_data_policy={missing_policy!r} is not in the "
            f"allowed set {sorted(_ALLOWED_MISSING_POLICIES)}. Check "
            "fx_forwards_panel/config.yaml or the input override."
        )

    field_name = params.field_name or default_field

    long_df, instrument_meta = fetch_fx_forwards_panel(
        engine=engine,
        market_scope=params.market_scope,
        start_date=params.start_date,
        end_date=params.end_date,
        tenor=params.tenor,
        field_name=field_name,
    )

    if long_df.empty or not instrument_meta:
        raise ValueError(
            f"calculate_fx_forwards_panel: no observations found for "
            f"market_scope={params.market_scope!r}, "
            f"tenor={params.tenor!r}, "
            f"field_name={field_name!r}, "
            f"start_date={params.start_date.isoformat()}, "
            f"end_date="
            f"{params.end_date.isoformat() if params.end_date else 'latest'}. "
            "Verify ingestion (tests/test_fx_data_readiness.py --strict-metadata) "
            "and the date range overlaps the available history."
        )

    raw_panel = (
        long_df.pivot(index="trade_date", columns="pair", values="field_value")
        .sort_index()
        .sort_index(axis=1)
    )

    if calendar_policy == "business_days":
        raw_panel = raw_panel[raw_panel.index.dayofweek < 5]

    empty_pairs = [col for col in raw_panel.columns if raw_panel[col].isna().all()]
    if empty_pairs:
        raise ValueError(
            f"calculate_fx_forwards_panel: pairs with NO observations "
            f"in the requested window: {empty_pairs}. Verify ingestion "
            "(load_audit + tests/test_fx_data_readiness.py)."
        )

    cleaned_panel = raw_panel.ffill(limit=ffill_limit) if ffill_limit > 0 else raw_panel.copy()
    if missing_policy == "raise":
        residual = cleaned_panel.isna().sum().sum()
        if residual > 0:
            raise ValueError(
                f"calculate_fx_forwards_panel: missing_data_policy='raise' "
                f"but {int(residual)} NaN cells remain after "
                f"ffill_limit={ffill_limit}. Relax the policy or widen "
                "the date range."
            )
    elif missing_policy == "drop_rows_any_missing":
        cleaned_panel = cleaned_panel.dropna(axis=0, how="any")

    if cleaned_panel.empty:
        raise ValueError(
            f"calculate_fx_forwards_panel: after applying "
            f"missing_data_policy={missing_policy!r}, the panel has "
            "zero rows. Either relax the policy or widen the start date."
        )

    # Every column is PRICE-units (forward points = absolute pip quotes).
    units_by_column: Dict[str, TimeSeriesUnits] = {
        col: TimeSeriesUnits.PRICE for col in cleaned_panel.columns
    }

    step_params: Dict[str, Any] = {
        "market_scope": params.market_scope,
        "tenor": params.tenor,
        "field_name": field_name,
        "start_date": params.start_date.isoformat(),
        "end_date": params.end_date.isoformat() if params.end_date else None,
        "ffill_limit_days": ffill_limit,
        "missing_data_policy": missing_policy,
        "calendar_policy": calendar_policy,
        "n_observations": int(len(cleaned_panel)),
        "columns": list(cleaned_panel.columns),
    }
    as_of_date_iso = pd.Timestamp(cleaned_panel.index[-1]).strftime("%Y-%m-%d")
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

    disclosures = [
        f"market_scope={params.market_scope}; tenor={params.tenor}; "
        f"{len(cleaned_panel.columns)} pair(s) in the assembled panel.",
        f"calendar_policy={calendar_policy} (V1: weekends excluded).",
        f"missing_data_policy={missing_policy} "
        f"(ffill_limit_days={ffill_limit}).",
        "Column names are pair names (e.g. 'EURUSD'), NOT vendor tickers.",
        "Units = TimeSeriesUnits.PRICE (forward points are an absolute "
        "level series in pips, NOT a percentage).",
        (
            "Asset-agnostic discipline: this tool does NOT compute "
            "correlation / PCA / factor loadings — pipe the returned "
            "Panel into shared/operators for those operations."
        ),
    ]

    output = FXForwardsPanelOutput(
        as_of_start=pd.Timestamp(cleaned_panel.index[0]).strftime("%Y-%m-%d"),
        as_of_end=as_of_date_iso,
        market_scope=params.market_scope,
        tenor=params.tenor,
        columns=list(cleaned_panel.columns),
        n_observations=int(len(cleaned_panel)),
        n_pairs=int(len(cleaned_panel.columns)),
        units_by_column={
            col: units.value for col, units in units_by_column.items()
        },
        methodology_disclosures=disclosures,
        panel=panel_artifact,
    )

    return output.model_dump(mode="python")


__all__ = [
    "CONFIG_PATH",
    "calculate_fx_forwards_panel",
]
