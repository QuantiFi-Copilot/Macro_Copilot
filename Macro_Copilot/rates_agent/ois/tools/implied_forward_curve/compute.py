"""compute.py — OIS implied forward strip (§7-C, Bucket-1A analytic).

The forward STRIP: for a fixed forward window (e.g. 1Y), the implied forward
rate anchored at each tenor across the curve grid (1y1y, 2y1y, …), assembled
as a Panel.  The generalization of the single-point forward_rate tool to the
whole term-structure-of-forwards.  Reuses the par-as-zero forward engine.

Test seam: ``_fetch_curve`` is imported at module level for monkeypatching.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rates_agent.ois.tools.implied_forward_curve.schemas import (
    ForwardCurvePoint,
    ImpliedForwardCurveCurrentMetrics,
    ImpliedForwardCurveInput,
    ImpliedForwardCurveOutput,
)
from shared.analytics.curve_bootstrap import (
    forward_rate_between,
    sort_tenors_by_years,
    tenor_to_years,
)
from shared.artifacts.lineage import Lineage, PrimitiveStep
from shared.artifacts.missingness import RawNoCleaning
from shared.artifacts.types import Panel
from shared.artifacts.units import TimeSeriesUnits as ArtifactUnits
from shared.config import ToolConfig, load_tool_config


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"

_TOOL_NAME = "calculate_implied_forward_curve_tool"
_TOOL_VERSION = "1.0.0"

_FETCH_SQL = text("""
    SELECT trade_date, tenor, field_value
    FROM macro_data.v_market_data_daily_enriched
    WHERE curve_family = :curve_family
      AND field_name   = :field_name
      AND instrument_type = 'ois_swap'
      AND trade_date  >= :start_date
      AND tenor IS NOT NULL
    ORDER BY trade_date, tenor
""")


def _fetch_curve(engine: Engine, curve_family: str, field_name: str,
                 start_date: date) -> pd.DataFrame:
    with engine.connect() as conn:
        result = conn.execute(_FETCH_SQL, {
            "curve_family": curve_family, "field_name": field_name,
            "start_date": start_date.isoformat(),
        })
        rows = result.fetchall()
        columns = list(result.keys())
    return pd.DataFrame(rows, columns=columns)


def calculate_implied_forward_curve(
    engine: Engine,
    params: ImpliedForwardCurveInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    """Assemble the implied forward strip of an OIS curve.

    Returns ``output.model_dump(mode='python')`` on success (preserving the
    typed Panel) or ``{"error": "..."}`` on a recoverable failure.
    """
    if config is None:
        config = load_tool_config(CONFIG_PATH)

    default_field = config.convention_value("default_swap_rate_field")
    ffill_limit = int(config.convention_value("ffill_limit_days"))
    ffill_source_tag = config.conventions["ffill_limit_days"].source
    default_horizon = config.convention_value("default_forward_horizon")
    round_dec = int(config.convention_value("forward_round_decimals"))

    field = params.field_name or default_field
    horizon = params.forward_horizon or default_horizon

    try:
        h_years = tenor_to_years(horizon)
        anchor_years = {a: tenor_to_years(a) for a in params.anchor_tenors}
    except (ValueError, KeyError) as exc:
        return {"error": f"implied_forward_curve: bad tenor/horizon — {exc}"}
    if h_years <= 0:
        return {"error": "implied_forward_curve: forward_horizon must be > 0."}

    # ------------------------------------------------------------------
    # 1. Fetch the OIS curve.
    # ------------------------------------------------------------------
    start_date = date.today() - timedelta(days=int(params.lookback_days) + 30)
    raw = _fetch_curve(engine, params.curve_family, field, start_date)
    if raw.empty:
        return {
            "error": (
                f"No OIS data for curve_family={params.curve_family!r} "
                f"(field {field}) since {start_date.isoformat()}."
            )
        }
    raw["trade_date"] = pd.to_datetime(raw["trade_date"])
    raw["field_value"] = pd.to_numeric(raw["field_value"], errors="coerce")
    raw = raw.dropna(subset=["field_value"]).drop_duplicates(
        subset=["trade_date", "tenor"], keep="last",
    )

    columns = [f"{a}_fwd" for a in params.anchor_tenors]

    # ------------------------------------------------------------------
    # 2. Per-day forward strip from the curve.
    # ------------------------------------------------------------------
    rows: Dict[pd.Timestamp, Dict[str, float]] = {}
    for ts, day in raw.groupby("trade_date"):
        ordered = sort_tenors_by_years(day["tenor"].tolist())
        if len(ordered) < 2:
            continue
        rate_by_tenor = dict(zip(day["tenor"], day["field_value"]))
        years = [tenor_to_years(t) for t in ordered]
        rates_dec = [float(rate_by_tenor[t]) / 100.0 for t in ordered]
        row: Dict[str, float] = {}
        for anchor, col in zip(params.anchor_tenors, columns):
            a = anchor_years[anchor]
            end = a + h_years
            # Skip anchors whose forward window is entirely outside the grid.
            if a > years[-1] or end < years[0]:
                continue
            try:
                fwd = forward_rate_between(years, rates_dec, a, end)
            except (ValueError, ZeroDivisionError):
                continue
            row[col] = fwd * 100.0
        if row:
            rows[ts] = row
    if not rows:
        return {
            "error": (
                f"implied_forward_curve: could not compute a forward strip "
                f"for {params.curve_family} (curve may lack tenors to bracket "
                "the requested anchors)."
            )
        }

    frame = pd.DataFrame(rows).T.sort_index()
    # Ensure all requested anchor columns exist (fully-NaN if never computed).
    for col in columns:
        if col not in frame.columns:
            frame[col] = float("nan")
    frame = frame[columns].ffill(limit=ffill_limit)

    as_of_ts = frame.index[-1]
    cutoff = as_of_ts - pd.Timedelta(days=int(params.lookback_days))
    display = frame.loc[frame.index >= cutoff]
    if display.empty:
        return {"error": "implied_forward_curve: no rows within the lookback."}
    display.index = pd.DatetimeIndex(display.index)
    as_of_iso = as_of_ts.strftime("%Y-%m-%d")

    # ------------------------------------------------------------------
    # 3. Build the typed Panel artifact (the forward strip).
    # ------------------------------------------------------------------
    units_by_column = {c: ArtifactUnits.PERCENT for c in columns}
    step_params: Dict[str, Any] = {
        "curve_family": params.curve_family,
        "forward_horizon": horizon,
        "anchor_tenors": list(params.anchor_tenors),
        "field_name": field,
        "n_observations": int(len(display)),
        "columns": columns,
        # The forward strip is ffill-bridged (limit=ffill_limit days) before
        # the Panel is stamped RawNoCleaning — record the imputation in the
        # lineage step so it is reachable from config-in-lineage (PR10) and
        # the methodology card is honest about the cleaning that occurred
        # (P5).  Mirrors the sibling build_zcis_panel step_params.
        "ffill_limit_days": ffill_limit,
        "ffill_source_tag": ffill_source_tag,
    }
    panel = Panel(
        payload=display.astype(float),
        units_by_column=units_by_column,
        missingness_policy=RawNoCleaning(),
        lineage=Lineage.from_steps([
            PrimitiveStep.build(
                name=_TOOL_NAME, version=_TOOL_VERSION, params=step_params,
                tool_config_hash=config.conventions_hash(),
                output_field="forward_strip", as_of_date=as_of_iso,
                tool_config_path=str(CONFIG_PATH),
            )
        ]),
    )

    # ------------------------------------------------------------------
    # 4. The latest forward-curve cross-section.
    # ------------------------------------------------------------------
    last = display.iloc[-1]
    curve_points: List[ForwardCurvePoint] = []
    for anchor, col in zip(params.anchor_tenors, columns):
        v = last[col]
        curve_points.append(ForwardCurvePoint(
            anchor_tenor=anchor,
            forward_label=f"{anchor}{horizon}",
            forward_rate_pct=None if pd.isna(v) else round(float(v), round_dec),
        ))

    current_metrics = ImpliedForwardCurveCurrentMetrics(
        as_of_date=as_of_iso,
        curve_family=params.curve_family,
        forward_horizon=horizon,
        n_anchors=len(params.anchor_tenors),
        n_observations=int(len(display)),
        forward_curve=curve_points,
    )

    disclosures = [
        f"The forward strip: forward rate over (anchor, anchor+{horizon}) at "
        "each anchor tenor — the forward curve as a Panel.",
        "PAR-AS-ZERO approximation for the forward (the forward_rate tool's "
        "convention); descriptive curve analytic, NOT a forecast.",
    ]

    output = ImpliedForwardCurveOutput(
        current_metrics=current_metrics,
        methodology_disclosures=disclosures,
        forward_strip=panel,
    )
    return output.model_dump(mode="python")


__all__ = ["CONFIG_PATH", "calculate_implied_forward_curve"]
