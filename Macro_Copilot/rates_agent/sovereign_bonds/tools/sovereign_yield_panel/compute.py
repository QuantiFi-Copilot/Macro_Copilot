"""compute.py — Deterministic sovereign yield Panel assembly.

Phase 1 PR 19.

Builds a closed-family ``Panel`` artifact from a list of
``(curve_family, tenor)`` leg specs.  Delegates fetching + pivoting
to ``shared/analytics/panel_assembly.py`` so a sibling
``ois_rate_panel`` primitive can share the exact same backend
without duplicated logic.

Test seam
---------
``fetch_instrument_panel`` is imported at module level so unit tests
can monkeypatch it via ``patch("rates_agent.sovereign_bonds.tools.
sovereign_yield_panel.compute.fetch_instrument_panel")``.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
from sqlalchemy.engine import Engine

from rates_agent.sovereign_bonds.tools.sovereign_yield_panel.schemas import (
    SovereignYieldPanelInput,
    SovereignYieldPanelOutput,
)
from shared.analytics.panel_assembly import (
    apply_missing_data_policy,
    fetch_instrument_panel,
    infer_units_for_field,
)
from shared.analytics.rates_fetch import latest_trade_date
from shared.artifacts.lineage import Lineage, PrimitiveStep
from shared.artifacts.missingness import RawNoCleaning
from shared.artifacts.types import Panel
from shared.artifacts.units import TimeSeriesUnits
from shared.config import ToolConfig, load_tool_config


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"

_TOOL_NAME = "build_sovereign_yield_panel_tool"
_TOOL_VERSION = "1.0.0"


# ============================================================================
# PUBLIC API
# ============================================================================


def build_sovereign_yield_panel(
    engine: Engine,
    params: SovereignYieldPanelInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    """Assemble a sovereign yield Panel.

    Returns a dict with both the wire-friendly summary
    (``output.model_dump()``) AND the artifact under the ``"_panel"``
    key — the workflow executor extracts the typed artifact, the MCP
    layer drops it before serialising for the LLM (full per-row data
    blows the LLM's token budget).

    On recoverable failures returns ``{"error": "..."}`` with a
    human-readable message — same envelope used by curve_spread /
    cross_market_spread.
    """
    if config is None:
        config = load_tool_config(CONFIG_PATH)

    default_field = config.convention_value("default_field_name")
    ffill_limit = int(config.convention_value("ffill_limit_days"))
    default_policy = config.convention_value("default_missing_data_policy")
    calendar_policy = config.convention_value("calendar_policy")

    missing_policy = params.missing_data_policy or default_policy

    # Build the per-leg specs the shared fetcher consumes.
    leg_triples = [
        (
            leg.curve_family,
            leg.tenor,
            leg.field_name or default_field,
        )
        for leg in params.legs
    ]

    # ------------------------------------------------------------------
    # Resolve the as-of anchor.  ``as_of_date`` (the optional per-query
    # historical-replay knob) caps the panel's trade_date window to the
    # supplied trade date.  None → the latest available trade_date in the
    # DB (live snapshot) → fall back to ``date.today()`` only when the
    # probe finds no rows (e.g. the offline unit-test path with
    # ``engine=None`` and a monkeypatched fetcher).  This panel spans an
    # arbitrary list of ``(curve_family, tenor)`` legs, so there is no
    # single curve_family/tenor to anchor on — we probe the latest
    # in-DB trade_date across the whole universe.  When ``as_of_date`` is
    # None the anchor equals (or post-dates) every observed date, so the
    # cap drops zero rows beyond what the caller's ``end_date`` already
    # does and behaviour is byte-identical to before.
    # ------------------------------------------------------------------
    anchor = (
        params.as_of_date
        or latest_trade_date(engine)
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

    raw_panel = fetch_instrument_panel(
        engine=engine,
        leg_specs=leg_triples,
        start_date=params.start_date,
        end_date=effective_end_date,
        ffill_limit_days=ffill_limit,
    )
    if raw_panel.empty:
        return {
            "error": (
                f"No observations found for any of the {len(leg_triples)} "
                f"requested legs between {params.start_date.isoformat()} "
                f"and "
                f"{params.end_date.isoformat() if params.end_date else 'latest'}. "
                "Verify each (curve_family, tenor, field) triple exists in "
                "the database."
            )
        }

    # Apply calendar policy.  When ``business_days`` is selected we
    # restrict to Mon-Fri only.  ``instrument_native`` keeps every
    # observed date as-is (the DB already excludes weekends for most
    # instruments — this is a passthrough).
    if calendar_policy == "business_days":
        raw_panel = raw_panel[raw_panel.index.dayofweek < 5]

    # Detect any fully-empty column (no observations across the
    # whole date range) — that's a configuration bug, not a missing-
    # data issue.  Fail loudly so the caller knows which leg is
    # broken.
    empty_legs = [col for col in raw_panel.columns if raw_panel[col].isna().all()]
    if empty_legs:
        return {
            "error": (
                f"Legs with NO data in the requested window: {empty_legs}.  "
                "Verify each leg's curve_family + tenor + field_name "
                "triple is valid + has ingested observations."
            )
        }

    try:
        cleaned_panel = apply_missing_data_policy(raw_panel, missing_policy)
    except ValueError as exc:
        return {"error": str(exc)}

    if cleaned_panel.empty:
        return {
            "error": (
                f"After applying missing_data_policy={missing_policy!r}, "
                "the panel has zero rows.  Either relax the policy or "
                "widen the start date."
            )
        }

    # Build the per-column units mapping.  Every leg's units come from
    # its resolved field_name via the shared inference helper.  The
    # ``TimeSeriesUnits`` enum is the closed-family unit set.
    units_by_column: Dict[str, TimeSeriesUnits] = {}
    for leg, (cf, tn, fn) in zip(params.legs, leg_triples):
        column_name = f"{cf}_{tn}"
        units_tag = infer_units_for_field(fn)
        try:
            units_by_column[column_name] = TimeSeriesUnits(units_tag)
        except ValueError:
            return {
                "error": (
                    f"Field {fn!r} for leg ({cf}, {tn}) inferred to unit "
                    f"{units_tag!r}, which is not a known TimeSeriesUnits "
                    "value.  Extend shared/analytics/panel_assembly.py "
                    "if adding a new field/unit pair."
                )
            }

    # Build the lineage step.  Two callers running the same panel
    # request produce the same primitive head hash; the artifact-
    # store idempotency gate then makes a second persist a no-op.
    #
    # ``tool_config_hash`` and ``as_of_date`` are the same identity
    # bits every primitive ships with — content hash of the YAML
    # block + the snapshot's anchor date.  Matches the discipline
    # in ``shared.artifacts.adapters.from_time_series``.
    step_params: Dict[str, Any] = {
        "legs": [
            {
                "curve_family": cf,
                "tenor": tn,
                "field_name": fn,
            }
            for cf, tn, fn in leg_triples
        ],
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
    as_of_date_iso = cleaned_panel.index[-1].strftime("%Y-%m-%d")
    # PR-10E Codex audit gap #1: fold data content + vintage into the
    # lineage hash via PrimitiveStep.build's optional identity bits.
    # Same recipe the high-level Panel bridge uses, imported as a shared
    # helper so the hash is byte-identical regardless of whether the
    # artifact is constructed here (in the primitive) or by the bridge.
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

    disclosures = [
        f"calendar_policy={calendar_policy} (V1: weekends excluded; holiday tables not yet ingested).",
        f"missing_data_policy={missing_policy} (ffill_limit_days={ffill_limit}).",
        "Sovereign-family legs only; OIS legs rejected at input validation.",
    ]

    output = SovereignYieldPanelOutput(
        as_of_start=cleaned_panel.index[0].strftime("%Y-%m-%d"),
        as_of_end=cleaned_panel.index[-1].strftime("%Y-%m-%d"),
        columns=list(cleaned_panel.columns),
        n_observations=int(len(cleaned_panel)),
        units_by_column={
            col: units.value for col, units in units_by_column.items()
        },
        methodology_disclosures=disclosures,
        # PR 20: typed Panel artifact lives on the declared schema
        # field so the workflow executor's bridge extracts it via
        # standard Pydantic attribute traversal.  MCP layer drops
        # this field before serialising to JSON for the LLM.
        panel=panel_artifact,
    )

    # ``model_dump(mode="python")`` preserves the typed Panel object
    # (vs ``mode="json"`` which would lose the typed identity).  The
    # bridge re-validates via ``output_class.model_validate(...)`` so
    # round-tripping through dict + revalidation must preserve the
    # Panel instance — Pydantic does this correctly because Panel
    # is a Pydantic BaseModel with ``arbitrary_types_allowed=True``.
    return output.model_dump(mode="python")


__all__ = [
    "CONFIG_PATH",
    "build_sovereign_yield_panel",
]
