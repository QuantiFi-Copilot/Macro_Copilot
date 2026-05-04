"""from_time_series — primitive ``TimeSeries`` → ``Series`` adapter.

The primitive→operator bridge (Phase 1B Work Item 2).  Lifts a
canonical ``shared.schemas.time_series.TimeSeries`` payload — emitted
by every per-tool-folder primitive in
``rates_agent.{sovereign_bonds,ois}.tools.*`` — into a typed
``shared.artifacts.Series`` that the operator layer can consume.

Per the bridge plan (Phase 1B), this adapter is the ONLY path between
primitive outputs and operator inputs.  No bypass channel: operators
take frozen ``Series`` artifacts; primitives emit JSON-shaped dicts;
the adapter is the bridge.  That is what makes the lineage chain
trustworthy — every artifact built from a primitive output starts
with a ``PrimitiveStep`` (added in PR #67), and downstream operators
extend the chain via ``OperatorStep`` entries.

Two functions ship from this module:

  - ``time_series_to_artifact_series`` — low-level conversion.
    Takes a ``TimeSeries`` plus a fully-built ``PrimitiveStep`` plus
    a typed ``MissingnessPolicy``.  Used by tests and advanced
    callers that want fine-grained control.

  - ``tool_output_to_artifact_series`` — high-level convenience.
    Takes the primitive's raw output dict + tool identity bits +
    the already-loaded ``ToolConfig``.  Auto-derives the
    ``CleanSingleSeriesV1`` policy from the config when possible,
    builds the ``PrimitiveStep`` for the caller, and calls the
    low-level function.

Semantic-faithful, NOT byte-identical, on missingness
-----------------------------------------------------
``TimeSeriesRow.value`` is ``Optional[float]`` — a ``None`` row is the
wire encoding for "the tool emitted a gap" (typically the rolling-
window warmup period, or a date the per-day forward-rate guard
skipped).  The artifact's payload is a numeric ``pd.Series``; there
is no null-mask sidecar in v1.  So:

  - wire ``None``  →  artifact ``NaN``  at the same DatetimeIndex
    position
  - artifact ``NaN``  →  wire ``None``  in the reverse path

This is *semantic-faithful* (the ``MissingnessPolicy`` carries the
contract that NaN means "missing", not "zero") but NOT byte-identical.
A future null-mask sidecar on ``Series.payload`` could close the gap
if a desk use case ever needs to distinguish "missing because no data"
from "explicit None reported by the upstream system" — deferred until
that need is real.

The adapter does NOT silently drop ``None`` rows.  Every input row
maps to one output index position; cleaning happens in the primitive
(via ``ffill_limit_days``), not here.

Naming + location
-----------------
Lives under ``shared.artifacts.adapters`` per the existing adapter-
layer architecture (the package's ``__init__`` already reserved
``time_series_to_artifact_series`` as the future sibling of
``raw_dataframe_to_artifact_series``).  No new top-level namespace
— a separate ``shared/bridge/`` package would have split the same
conversion concern across two namespaces.
"""

from __future__ import annotations

from typing import Any, Dict, Literal, Optional, Type

import numpy as np
import pandas as pd
from pydantic import BaseModel

from shared.artifacts.lineage import Lineage, PrimitiveStep
from shared.artifacts.missingness import (
    CleanSingleSeriesV1,
    MissingnessPolicy,
)
from shared.artifacts.types import Series
from shared.config import ToolConfig
from shared.schemas import TimeSeries


_BRIDGE_NAME = "time_series_to_artifact_series"


# ============================================================================
# LOW-LEVEL: TimeSeries -> Series
# ============================================================================

def time_series_to_artifact_series(
    ts: TimeSeries,
    *,
    primitive_step: PrimitiveStep,
    missingness_policy: MissingnessPolicy,
    frequency: Optional[Literal["B", "D", "W", "M", "Q", "Y"]] = None,
) -> Series:
    """Convert a canonical ``TimeSeries`` payload into a typed ``Series``.

    Pure conversion.  The caller has already constructed the
    ``PrimitiveStep`` (with all four identity bits — params /
    tool_config_hash / output_field / as_of_date) and chosen the
    ``MissingnessPolicy`` explicitly.  The adapter only does the
    mechanical transform.

    Parameters
    ----------
    ts :
        The primitive's canonical TimeSeries.  ``ts.series_name``
        becomes the artifact's ``series_key`` (the wire convention
        already says ``series_name`` is a stable, lower-snake-case
        identifier, so it slots into ``series_key`` 1:1 without
        re-derivation).
    primitive_step :
        Fully-built ``PrimitiveStep`` (see ``PrimitiveStep.build``).
        This becomes the single step in the resulting artifact's
        lineage chain.
    missingness_policy :
        Typed structured policy describing how the primitive's
        upstream raw data was cleaned.  Caller responsibility — the
        low-level adapter does NOT auto-derive this; use the
        high-level ``tool_output_to_artifact_series`` for the
        convenience wrapper.
    frequency :
        Optional explicit frequency tag.  Adapter does NOT infer —
        passing None preserves ``Series.frequency=None`` (the
        operator layer can refuse compositions whose frequencies
        are ambiguous).

    Returns
    -------
    Series
        Frozen artifact with the lineage starting at ``primitive_step``.

    Raises
    ------
    ValueError
        On empty rows, or any shape problem the ``Series`` validator
        catches downstream (non-monotonic dates, duplicates, etc.) —
        re-raised through Pydantic's normal ValidationError path.
    """
    if not ts.rows:
        raise ValueError(
            f"{_BRIDGE_NAME}: TimeSeries '{ts.series_name}' has no rows; "
            "an empty primitive output cannot be lifted to a Series "
            "artifact (operator layer requires a non-empty payload)."
        )

    # ------------------------------------------------------------------
    # 1. Build the pd.Series.  None values map to NaN at the same
    #    index position — semantically faithful to the wire's gap
    #    encoding.  See the module docstring for the missingness
    #    contract.
    # ------------------------------------------------------------------
    dates = pd.to_datetime([row.date for row in ts.rows])
    values = np.array(
        [row.value if row.value is not None else np.nan for row in ts.rows],
        dtype=float,
    )
    payload = pd.Series(values, index=pd.DatetimeIndex(dates), dtype=float)

    # The Series validator enforces monotonic + dedup'd index.  Sort
    # defensively so a primitive that emits chronologically-ordered
    # rows in a slightly different order (e.g. tied dates resolved
    # last-write-wins) still produces a valid artifact.  Duplicates
    # are NOT silently deduped: the Series validator raises loudly,
    # which is the correct behaviour (a primitive shouldn't emit
    # duplicate dates and the bridge shouldn't paper over it).
    payload = payload.sort_index()

    # ------------------------------------------------------------------
    # 2. Build the lineage — single step, the primitive that
    #    produced this TimeSeries.
    # ------------------------------------------------------------------
    lineage = Lineage.from_steps([primitive_step])

    # ------------------------------------------------------------------
    # 3. Construct the frozen artifact.  Series.payload validator
    #    catches any remaining shape issues (DatetimeIndex,
    #    monotonic, no duplicates, numeric dtype).
    # ------------------------------------------------------------------
    return Series(
        series_key=ts.series_name,
        payload=payload,
        units=ts.units,
        frequency=frequency,
        missingness_policy=missingness_policy,
        lineage=lineage,
    )


# ============================================================================
# HIGH-LEVEL: tool output dict -> Series
# ============================================================================

def tool_output_to_artifact_series(
    tool_output: Dict[str, Any],
    *,
    output_class: Type[BaseModel],
    output_field: str,
    tool_name: str,
    tool_config: ToolConfig,
    params: BaseModel,
    tool_config_path: Optional[str] = None,
    missingness_policy: Optional[MissingnessPolicy] = None,
    frequency: Optional[Literal["B", "D", "W", "M", "Q", "Y"]] = None,
    primitive_version: str = "1.0.0",
) -> Series:
    """Convenience wrapper: primitive output dict → ``Series`` artifact.

    The 95% callsite.  Validates the dict against the primitive's
    declared output schema (catches malformed primitive outputs early),
    extracts the requested ``time_series*`` field, auto-derives the
    ``MissingnessPolicy`` from ``tool_config`` when possible, builds
    the ``PrimitiveStep`` with all four identity bits, and calls the
    low-level adapter.

    Parameters
    ----------
    tool_output :
        The primitive's raw output dict (i.e. what
        ``calculate_*`` / ``get_*`` returns — already
        ``model_dump()``'d).
    output_class :
        The primitive's Pydantic output class (e.g.
        ``OISCurveSpreadOutput``).  Used to validate ``tool_output``
        before extracting the field — gates malformed dicts with a
        clean Pydantic ``ValidationError`` instead of a downstream
        ``KeyError``.
    output_field :
        Which TimeSeries-typed field to extract (e.g.
        ``"time_series_spread"``, ``"time_series_zscore"``,
        ``"time_series"``, ``"time_series_forward"``).  Each
        primitive declares one or more such fields; the caller
        picks the one they want as a ``Series`` artifact.
    tool_name :
        The primitive's MCP tool name (e.g.
        ``"calculate_ois_curve_spread_tool"``).  Becomes
        ``PrimitiveStep.name``.
    tool_config :
        The already-loaded ``ToolConfig`` (cached upstream by
        ``load_tool_config``).  The bridge calls
        ``tool_config.conventions_hash()`` to populate
        ``PrimitiveStep.tool_config_hash`` — so a YAML edit that
        changes any convention's value invalidates the lineage hash.
    params :
        The primitive's ``*Input`` Pydantic model.  Becomes
        ``PrimitiveStep.params`` via ``model_dump()`` (mode='json'
        so dates / enums serialize deterministically).
    tool_config_path :
        Optional path the YAML was loaded from.  Bookkeeping only —
        NOT in the lineage hash.  Two callers loading the same YAML
        from different paths produce the same hash if the content is
        identical.
    missingness_policy :
        Optional explicit policy.  When ``None`` (default), the
        bridge auto-derives a ``CleanSingleSeriesV1`` from
        ``tool_config.conventions['ffill_limit_days'].value``.  If
        that convention is not declared, the bridge raises
        ``ValueError`` with a pointer to pass an explicit policy
        rather than silently defaulting to ``RawNoCleaning``.
    frequency :
        Optional explicit frequency tag (passed through unchanged).
    primitive_version :
        The primitive's intrinsic version (defaults to ``"1.0.0"``
        matching every other Step kind).  Bumped only when the
        primitive's compute-path contract changes.

    Returns
    -------
    Series
        Frozen artifact with a single-step lineage rooted at the
        ``PrimitiveStep`` for this invocation.

    Raises
    ------
    ValueError
        If ``output_field`` is missing from the validated output, OR
        if ``missingness_policy`` is None and the tool config has no
        ``ffill_limit_days`` convention.
    pydantic.ValidationError
        If ``tool_output`` does not validate against ``output_class``.
    """
    # ------------------------------------------------------------------
    # 1. Validate the output dict against the primitive's schema.
    #    This is the gate that catches malformed primitive outputs
    #    BEFORE we try to extract a field.  Without this, a typo in
    #    a primitive's keys would surface as a confusing AttributeError
    #    at the .time_series_* access below; with it, the caller gets
    #    a clean Pydantic ValidationError naming the missing/extra
    #    field.
    # ------------------------------------------------------------------
    validated = output_class.model_validate(tool_output)

    # ------------------------------------------------------------------
    # 2. Extract the requested field; verify it is a TimeSeries.
    # ------------------------------------------------------------------
    if not hasattr(validated, output_field):
        # Build a list of the TimeSeries-typed fields on the schema
        # so the error message names the legal options.  Inspect via
        # model_fields rather than __fields__ (Pydantic v2 API).
        ts_fields = [
            name for name, field in output_class.model_fields.items()
            if _is_time_series_type(field.annotation)
        ]
        raise ValueError(
            f"{_BRIDGE_NAME}: output_field={output_field!r} is not "
            f"declared on {output_class.__name__}.  TimeSeries-typed "
            f"fields available: {sorted(ts_fields) if ts_fields else 'none'}."
        )

    ts_obj = getattr(validated, output_field)
    if not isinstance(ts_obj, TimeSeries):
        raise ValueError(
            f"{_BRIDGE_NAME}: output_field={output_field!r} on "
            f"{output_class.__name__} is not a TimeSeries — got "
            f"{type(ts_obj).__name__}.  The bridge only converts "
            "canonical TimeSeries fields; bespoke wire-frozen "
            "shapes (e.g. List[*TimeSeriesRow]) need their own "
            "conversion path."
        )

    # ------------------------------------------------------------------
    # 3. Resolve the missingness policy.
    # ------------------------------------------------------------------
    resolved_policy: MissingnessPolicy
    if missingness_policy is not None:
        resolved_policy = missingness_policy
    else:
        # Auto-derive CleanSingleSeriesV1 from the tool config's
        # ffill_limit_days convention.  Every rates primitive that
        # cleans its inputs declares this knob; if it isn't there,
        # we refuse to silently default to RawNoCleaning (would
        # mis-tag the artifact's missingness regime).
        if "ffill_limit_days" not in tool_config.conventions:
            raise ValueError(
                f"{_BRIDGE_NAME}: tool '{tool_name}' has no "
                "'ffill_limit_days' convention in its YAML; cannot "
                "auto-derive a CleanSingleSeriesV1 missingness policy.  "
                "Pass an explicit ``missingness_policy=...`` (e.g. "
                "RawNoCleaning() if no cleaning was applied, or a "
                "custom CleanSingleSeriesV1 instance with the right "
                "fields) — the bridge will not assume the regime."
            )
        ffill_limit = int(tool_config.convention_value("ffill_limit_days"))
        resolved_policy = CleanSingleSeriesV1(ffill_limit=ffill_limit)

    # ------------------------------------------------------------------
    # 4. Pull the snapshot's as_of_date.  Every canonical primitive
    #    output has ``current_metrics.as_of_date`` (wire convention
    #    — see every per-tool-folder schema).  We read it from the
    #    validated model so the value is the schema-faithful one.
    # ------------------------------------------------------------------
    as_of_date = _extract_as_of_date(validated)

    # ------------------------------------------------------------------
    # 5. Build the PrimitiveStep with all four identity bits.
    # ------------------------------------------------------------------
    primitive_step = PrimitiveStep.build(
        name=tool_name,
        version=primitive_version,
        params=params.model_dump(mode="json"),
        tool_config_hash=tool_config.conventions_hash(),
        output_field=output_field,
        as_of_date=as_of_date,
        tool_config_path=tool_config_path,
    )

    # ------------------------------------------------------------------
    # 6. Delegate to the low-level adapter for the mechanical
    #    conversion.  Single source of truth for the
    #    None→NaN / index-validation / lineage-attachment logic.
    # ------------------------------------------------------------------
    return time_series_to_artifact_series(
        ts_obj,
        primitive_step=primitive_step,
        missingness_policy=resolved_policy,
        frequency=frequency,
    )


# ============================================================================
# INTERNAL HELPERS
# ============================================================================

def _is_time_series_type(annotation: Any) -> bool:
    """Best-effort check: does this Pydantic field annotation declare
    a ``TimeSeries`` (or ``Optional[TimeSeries]``) shape?

    Used purely for friendlier error messages when a caller passes an
    unknown ``output_field``.  Falsey on edge cases (generics we don't
    recognise) — the caller still gets a correct error, just with a
    less-informative "available fields" list.
    """
    if annotation is TimeSeries:
        return True
    # Optional[TimeSeries] / Union[..., TimeSeries] cases.
    args = getattr(annotation, "__args__", None)
    if args:
        return any(arg is TimeSeries for arg in args)
    return False


def _extract_as_of_date(validated_output: BaseModel) -> str:
    """Pull ``current_metrics.as_of_date`` from a validated primitive
    output.

    Every canonical primitive output declares a
    ``current_metrics`` snapshot with an ``as_of_date: str`` field
    (wire convention — see every per-tool-folder ``schemas.py``).
    The bridge reads it via attribute traversal so the validated
    Pydantic types are preserved end-to-end.
    """
    if not hasattr(validated_output, "current_metrics"):
        raise ValueError(
            f"{_BRIDGE_NAME}: output {type(validated_output).__name__} "
            "has no 'current_metrics' field; cannot extract as_of_date.  "
            "All canonical primitive outputs declare current_metrics by "
            "wire convention — please verify the primitive emits the "
            "snapshot block."
        )
    cm = validated_output.current_metrics
    if not hasattr(cm, "as_of_date"):
        raise ValueError(
            f"{_BRIDGE_NAME}: {type(validated_output).__name__}."
            "current_metrics has no 'as_of_date' field; cannot build "
            "the PrimitiveStep replay-determinism identity bit."
        )
    return str(cm.as_of_date)


__all__ = [
    "time_series_to_artifact_series",
    "tool_output_to_artifact_series",
]
