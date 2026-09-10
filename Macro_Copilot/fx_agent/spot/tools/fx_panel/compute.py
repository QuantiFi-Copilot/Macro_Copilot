"""compute.py — Deterministic FX spot Panel assembly.

Phase B (2026-05-25).

Builds a closed-family ``Panel`` artifact from a market_scope subset
of the FX spot universe (G10 / EM / G10_CROSSES / ALL). Delegates
fetching + pivoting to ``shared/analytics/fx_fetch.py`` so future FX
sub-domain panel primitives (e.g. forwards by tenor, NDF by pair)
can share the exact same backend pattern without duplicated SQL.

Mirrors ``rates_agent.sovereign_bonds.tools.sovereign_yield_panel.compute``
in every architectural respect (lineage, missing-data policy, calendar
policy, typed-Panel output) — Phase B compliance check explicitly
re-uses Sreeram's pattern, no parallel invention.

Codex divergence from sovereign_yield_panel pattern (2026-05-25):
on zero rows / unknown scope we raise ``ValueError`` rather than
returning an ``{"error": ...}`` envelope. Rationale: this is a
substrate primitive — zero rows means a real data integrity issue
(wrong scope, empty DB, mid-ingestion state) that downstream code
must NOT be allowed to mistake for a valid empty Panel.

Test seam
---------
``fetch_fx_spot_panel`` is imported at module level so unit tests
can monkeypatch it via ``patch("fx_agent.spot.tools.fx_panel.
compute.fetch_fx_spot_panel")``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
from sqlalchemy.engine import Engine

from fx_agent.spot.tools.fx_panel.schemas import (
    FXPanelInput,
    FXPanelOutput,
)
from shared.analytics.fx_fetch import fetch_fx_spot_panel
from shared.artifacts.lineage import Lineage, PrimitiveStep
from shared.artifacts.missingness import RawNoCleaning
from shared.artifacts.types import Panel
from shared.artifacts.units import TimeSeriesUnits
from shared.config import ToolConfig, load_tool_config


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"

_TOOL_NAME = "calculate_fx_panel_tool"
_TOOL_VERSION = "1.0.0"

_ALLOWED_MISSING_POLICIES = frozenset({
    "raise",
    "forward_fill_only",
    "drop_rows_any_missing",
})


# ============================================================================
# PUBLIC API
# ============================================================================


def calculate_fx_panel(
    engine: Engine,
    params: FXPanelInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    """Assemble an FX spot Panel for the requested market_scope.

    Returns a dict ``output.model_dump(mode="python")`` that preserves
    the typed ``Panel`` artifact under the ``panel`` key — the workflow
    executor extracts the typed artifact, the MCP layer drops it
    before serialising for the LLM.

    Raises
    ------
    ValueError
        - if ``params.market_scope`` is not in the closed Literal set
          (Pydantic enforces this upstream; fx_fetch enforces again)
        - if ``params.missing_data_policy`` (or the config default)
          is not in the allowed-policies set
        - if the fetched panel is empty (zero rows) — substrate
          primitive cannot return a valid empty Panel artifact
        - if any column is fully-NaN after fetch — that pair has no
          observations in the window (data integrity issue)
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
            "fx_panel/config.yaml or the input override."
        )

    field_name = params.field_name or default_field

    # Fetch the long-format substrate from shared.analytics.fx_fetch.
    # The fetcher validates market_scope and returns (long_df, meta).
    long_df, instrument_meta = fetch_fx_spot_panel(
        engine=engine,
        market_scope=params.market_scope,
        start_date=params.start_date,
        end_date=params.end_date,
        field_name=field_name,
    )

    # Fail-loud on empty result (Codex divergence from sovereign_yield_panel).
    if long_df.empty or not instrument_meta:
        raise ValueError(
            f"calculate_fx_panel: no observations found for "
            f"market_scope={params.market_scope!r}, "
            f"field_name={field_name!r}, "
            f"start_date={params.start_date.isoformat()}, "
            f"end_date="
            f"{params.end_date.isoformat() if params.end_date else 'latest'}. "
            "Verify the universe is ingested (run "
            "tests/test_fx_data_readiness.py --strict-metadata) and the "
            "date range overlaps the available history."
        )

    # Pivot to wide DataFrame: rows = trade_date, columns = pair name,
    # values = field_value. Alphabetical column order keeps downstream
    # determinism (correlation matrices, factor loadings, etc.).
    raw_panel = (
        long_df.pivot(index="trade_date", columns="pair", values="field_value")
        .sort_index()
        .sort_index(axis=1)
    )

    # Apply calendar policy.
    if calendar_policy == "business_days":
        raw_panel = raw_panel[raw_panel.index.dayofweek < 5]

    # Detect any fully-empty column — that's a real ingestion gap, not
    # a missing-data smoothing case. Fail loudly so the caller knows
    # which pair is broken.
    empty_pairs = [col for col in raw_panel.columns if raw_panel[col].isna().all()]
    if empty_pairs:
        raise ValueError(
            f"calculate_fx_panel: pairs with NO observations in the "
            f"requested window: {empty_pairs}. Verify ingestion for "
            "these pairs (load_audit + tests/test_fx_data_readiness.py)."
        )

    # Apply missing-data policy:
    #  - forward_fill_only: ffill up to ffill_limit days, leave residual NaN
    #  - raise: ffill, then raise if any NaN remains
    #  - drop_rows_any_missing: ffill, then drop any row still containing NaN
    cleaned_panel = raw_panel.ffill(limit=ffill_limit) if ffill_limit > 0 else raw_panel.copy()
    if missing_policy == "raise":
        residual = cleaned_panel.isna().sum().sum()
        if residual > 0:
            raise ValueError(
                f"calculate_fx_panel: missing_data_policy='raise' but "
                f"{int(residual)} NaN cells remain after "
                f"ffill_limit={ffill_limit}. Relax the policy or widen "
                "the date range."
            )
    elif missing_policy == "drop_rows_any_missing":
        cleaned_panel = cleaned_panel.dropna(axis=0, how="any")

    if cleaned_panel.empty:
        raise ValueError(
            f"calculate_fx_panel: after applying "
            f"missing_data_policy={missing_policy!r}, the panel has "
            "zero rows. Either relax the policy or widen the start date."
        )

    # Build per-column units mapping — every FX spot column is a
    # PRICE level (closed-enum extension added Phase B for this
    # primitive — see shared/schemas/time_series.py).
    units_by_column: Dict[str, TimeSeriesUnits] = {
        col: TimeSeriesUnits.PRICE for col in cleaned_panel.columns
    }

    # Build the lineage step. Two callers running the same panel
    # request produce the same primitive head hash; the artifact-
    # store idempotency gate then makes a second persist a no-op.
    step_params: Dict[str, Any] = {
        "market_scope": params.market_scope,
        "field_name": field_name,
        "start_date": params.start_date.isoformat(),
        "end_date": (
            params.end_date.isoformat() if params.end_date else None
        ),
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
        f"market_scope={params.market_scope}; "
        f"{len(cleaned_panel.columns)} pair(s) in the assembled panel.",
        f"calendar_policy={calendar_policy} (V1: weekends excluded; "
        "per-currency holiday tables not yet ingested).",
        f"missing_data_policy={missing_policy} "
        f"(ffill_limit_days={ffill_limit}).",
        "Column names are pair names (e.g. 'EURUSD'), NOT vendor "
        "tickers (e.g. 'EURUSD Curncy') — cleaner for downstream code.",
        "Units = TimeSeriesUnits.PRICE (FX spot is a price level, "
        "not a percent rate).",
    ]

    output = FXPanelOutput(
        as_of_start=pd.Timestamp(cleaned_panel.index[0]).strftime("%Y-%m-%d"),
        as_of_end=as_of_date_iso,
        market_scope=params.market_scope,
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
    "calculate_fx_panel",
]
