"""compute.py — financing-rate primitive compute.

Phase 1 PR 19.

Builds a typed ``Series`` artifact representing daily financing
rates over a date range.  Delegates to ``shared/analytics/
financing.py`` for the method-specific compute so this surface stays
free of business logic.

Test seam
---------
``constant_rate_series``, ``fetch_overnight_index_series``, and
``date`` are imported at module level so unit tests can patch them
via ``...financing_rate.compute.X``.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
from sqlalchemy.engine import Engine

from rates_agent.ois.tools.financing_rate.schemas import (
    FinancingRateInput,
    FinancingRateOutput,
)
from shared.analytics.financing import (
    constant_rate_series,
    fetch_overnight_index_series,
)
from shared.artifacts.lineage import Lineage, PrimitiveStep
from shared.artifacts.missingness import RawNoCleaning
from shared.artifacts.types import Panel
from shared.artifacts.units import TimeSeriesUnits
from shared.config import ToolConfig, load_tool_config


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"

_TOOL_NAME = "compute_financing_rate_tool"
_TOOL_VERSION = "1.0.0"


# ============================================================================
# PUBLIC API
# ============================================================================


def compute_financing_rate(
    engine: Engine,
    params: FinancingRateInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    """Compute a daily financing-rate Series.

    Returns the wire-friendly summary AND the typed Series artifact
    under ``"_series"`` (workflow executor extracts; MCP layer drops
    underscore keys before serialising for the LLM).
    """
    if config is None:
        config = load_tool_config(CONFIG_PATH)

    default_method = config.convention_value("default_method")
    default_basis = config.convention_value("default_day_count_basis")
    default_calendar = config.convention_value("default_calendar")

    method = params.method or default_method
    day_count_basis = params.day_count_basis or default_basis
    calendar = params.calendar or default_calendar

    # ------------------------------------------------------------------
    # Method dispatch + V1 NotImplementedError guards.
    # ------------------------------------------------------------------
    if method == "term_repo_curve":
        return {
            "error": (
                "method=term_repo_curve is declared in the closed-enum "
                "but V1 raises NotImplementedError — real term-repo "
                "data is not yet ingested.  See config.yaml "
                "methodology.planned_extensions."
            )
        }
    if method == "gc_special_blend":
        return {
            "error": (
                "method=gc_special_blend is declared in the closed-enum "
                "but V1 raises NotImplementedError — GC-special data is "
                "not yet ingested.  See config.yaml "
                "methodology.planned_extensions."
            )
        }

    # ------------------------------------------------------------------
    # Compute the daily rate Series per the chosen method.
    # ------------------------------------------------------------------
    if method == "constant_rate":
        # Pydantic validator already enforced constant_rate_pct is set.
        try:
            rate_series = constant_rate_series(
                start_date=params.start_date,
                end_date=params.end_date,
                constant_rate_pct=float(params.constant_rate_pct),
                calendar=calendar,
            )
        except ValueError as exc:
            return {"error": str(exc)}
        method_disclosure = (
            f"constant_rate at {params.constant_rate_pct}% over the "
            f"requested date range ({calendar}).  No data fetched — "
            "rate is caller-supplied."
        )
    elif method == "overnight_index_proxy":
        # Pydantic validator already enforced proxy_curve is set + valid.
        # as_of_date anchors the upper bound of the trade-date window:
        # a historical replay caps the proxy series at the as-of trade
        # date.  None → keep the caller's requested ``end_date`` (live
        # snapshot, byte-identical to pre-as_of behaviour).  Never
        # extend past the requested window, so cap to min(end_date,
        # as_of_date) when an as-of date is supplied.
        proxy_end = params.end_date
        if params.as_of_date is not None:
            proxy_end = min(params.end_date, params.as_of_date)
        try:
            rate_series = fetch_overnight_index_series(
                engine=engine,
                proxy_curve=params.proxy_curve,
                start_date=params.start_date,
                end_date=proxy_end,
            )
        except ValueError as exc:
            return {"error": str(exc)}
        if rate_series.empty:
            return {
                "error": (
                    f"No observations found for proxy_curve="
                    f"{params.proxy_curve!r} between "
                    f"{params.start_date.isoformat()} and "
                    f"{params.end_date.isoformat()}.  Verify the curve "
                    "is ingested + has data in the requested window."
                )
            }
        method_disclosure = (
            f"overnight_index_proxy reading {params.proxy_curve} at "
            "the shortest-available OIS tenor (1W per the current "
            "playbook) as the financing proxy.  True O/N OIS quotes "
            "are not yet ingested; the proxy understates volatility "
            "of true O/N rates by the small difference between 1W "
            "and 1D quoting."
        )
    else:
        # Caller passed an unrecognised method that survived Pydantic
        # (shouldn't happen given the Literal constraint, but defensive).
        return {
            "error": (
                f"Unknown method {method!r}; expected one of "
                "['constant_rate', 'overnight_index_proxy', "
                "'term_repo_curve', 'gc_special_blend']."
            )
        }

    if rate_series.empty:
        return {
            "error": (
                "Financing-rate series is empty after compute — "
                "check inputs (date range, calendar)."
            )
        }

    # ------------------------------------------------------------------
    # Build the typed Series artifact + lineage step.
    # ------------------------------------------------------------------
    series_key = _build_series_key(method, params)
    step_params: Dict[str, Any] = {
        "method": method,
        "start_date": params.start_date.isoformat(),
        "end_date": params.end_date.isoformat(),
        "day_count_basis": day_count_basis,
        "calendar": calendar,
        "n_observations": int(len(rate_series)),
    }
    if method == "constant_rate":
        step_params["constant_rate_pct"] = float(params.constant_rate_pct)
    elif method == "overnight_index_proxy":
        step_params["proxy_curve"] = params.proxy_curve

    as_of_date_iso = rate_series.index[-1].strftime("%Y-%m-%d")
    # PR-10E Codex audit gap #1: build the Panel payload + units mapping
    # upfront so we can fingerprint it BEFORE constructing the
    # PrimitiveStep.  Same values reused below when wrapping as the
    # typed Panel artifact; no behaviour change beyond the lineage-hash
    # hardening.
    panel_df = rate_series.astype(float).to_frame(name=series_key)
    units_by_column = {series_key: TimeSeriesUnits.PERCENT}
    from shared.artifacts.adapters.from_time_series import (
        _compute_panel_payload_fingerprint,
    )
    data_content_fingerprint = _compute_panel_payload_fingerprint(
        panel_df, units_by_column,
    )
    step = PrimitiveStep.build(
        name=_TOOL_NAME,
        version=_TOOL_VERSION,
        params=step_params,
        tool_config_hash=config.conventions_hash(),
        output_field="series",
        as_of_date=as_of_date_iso,
        tool_config_path=str(CONFIG_PATH),
        data_content_fingerprint=data_content_fingerprint,
        data_vintage=as_of_date_iso,
    )
    lineage = Lineage.from_steps([step])

    # Wrap as single-column Panel — the shape ``evaluate_trades``
    # consumes for financing.  Column name is the series_key so a
    # consumer can identify it.
    panel_artifact = Panel(
        payload=panel_df,
        units_by_column=units_by_column,
        missingness_policy=RawNoCleaning(),
        lineage=lineage,
    )

    mean_rate = float(rate_series.mean()) if not rate_series.empty else None

    output = FinancingRateOutput(
        method=method,
        as_of_start=rate_series.index[0].strftime("%Y-%m-%d"),
        as_of_end=as_of_date_iso,
        n_observations=int(len(rate_series)),
        mean_rate_pct=mean_rate,
        methodology_disclosures=[
            method_disclosure,
            f"day_count_basis={day_count_basis} (per-day fraction = rate/100/basis_days).",
        ],
        # PR 20: typed Panel on the declared output field.  Workflow
        # executor's bridge extracts it via standard attribute
        # traversal; MCP layer drops it before serialising for the LLM.
        panel=panel_artifact,
    )
    return output.model_dump(mode="python")


# ============================================================================
# Helpers
# ============================================================================


def _build_series_key(method: str, params: FinancingRateInput) -> str:
    """Compose a self-describing series_key for the financing-rate
    artifact.  Format: ``financing_<method>_<method_param_summary>``.
    Two calls with the same method+param produce the same key, so
    series-level dedup works as expected.
    """
    if method == "constant_rate":
        return f"financing_constant_{params.constant_rate_pct:g}pct"
    if method == "overnight_index_proxy":
        return f"financing_proxy_{params.proxy_curve.lower()}"
    return f"financing_{method}"


__all__ = [
    "CONFIG_PATH",
    "compute_financing_rate",
]
