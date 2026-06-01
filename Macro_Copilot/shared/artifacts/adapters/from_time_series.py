"""from_time_series — primitive ``TimeSeries`` ↔ ``Series`` adapter.

The primitive→operator bridge (Phase 1B Work Items 2 & 3).  Lifts a
canonical ``shared.schemas.time_series.TimeSeries`` payload — emitted
by every per-tool-folder primitive in
``rates_agent.{sovereign_bonds,ois}.tools.*`` — into a typed
``shared.artifacts.Series`` that the operator layer can consume, AND
demotes operator outputs back to the wire format for serialization
to the LLM / frontend / future REST.

Per the bridge plan (Phase 1B), this adapter is the ONLY path between
primitive outputs and operator inputs.  No bypass channel: operators
take frozen ``Series`` artifacts; primitives emit JSON-shaped dicts;
the adapter is the bridge.  That is what makes the lineage chain
trustworthy — every artifact built from a primitive output starts
with a ``PrimitiveStep`` (added in PR #67), and downstream operators
extend the chain via ``OperatorStep`` entries.

Three functions ship from this module:

  - ``time_series_to_artifact_series`` — low-level forward
    conversion.  Takes a ``TimeSeries`` plus a fully-built
    ``PrimitiveStep`` plus a typed ``MissingnessPolicy``.  Used by
    tests and advanced callers that want fine-grained control.

  - ``tool_output_to_artifact_series`` — high-level forward
    convenience.  Takes the primitive's raw output dict + tool
    identity bits + the already-loaded ``ToolConfig``.  Auto-derives
    the ``CleanSingleSeriesV1`` policy from the config when possible,
    builds the ``PrimitiveStep`` for the caller, and calls the
    low-level function.

  - ``artifact_series_to_time_series`` — reverse conversion (Work
    Item 3).  Takes a frozen ``Series`` and produces a wire-
    compatible ``TimeSeries``.  ``NaN`` → ``None`` at every row,
    closing the missingness round-trip documented at the forward
    path.  ``description`` is filled with a linear lineage summary
    (``"derived: <step0.name> → <step1.name> → ..."``) by default;
    callers can override.  No ``TimeSeries`` schema bump — the full
    structured lineage stays on the artifact, the wire carries a
    human-readable summary suitable for serialization to the LLM /
    frontend / future REST.

Round-trip discipline
---------------------
The forward+reverse round trip is *semantic-faithful*:

  - ``(rows, units, series_name)`` round-trip BYTE-identical
    (modulo the documented ``NaN`` ↔ ``None`` semantic mapping).
  - ``description`` differs by design — forward consumes the
    primitive's free-form description, reverse generates a
    lineage summary.  Recovering the original description is
    possible from the structured lineage (``PrimitiveStep.params``
    + downstream ``OperatorStep.params``); the wire representation
    optimises for human-readable provenance, not lossless echo.

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

import hashlib
from typing import Any, Dict, List, Literal, Optional, Type

import numpy as np
import pandas as pd
from pydantic import BaseModel

from shared.artifacts.lineage import Lineage, PrimitiveStep, _canonical_json
from shared.artifacts.missingness import (
    CleanSingleSeriesV1,
    MissingnessPolicy,
    RawNoCleaning,
)
from shared.artifacts.types import Panel, Series
from shared.config import ToolConfig
from shared.schemas import TimeSeries, TimeSeriesRow


_BRIDGE_NAME = "time_series_to_artifact_series"
_PANEL_BRIDGE_NAME = "tool_output_to_artifact_panel"


# ============================================================================
# CONTENT FINGERPRINTING — PR-10E Codex audit gap #1
# ============================================================================
# Lineage hash must change when vendor data content changes, even when
# (params, tool_config_hash, as_of_date) are byte-identical (e.g. a vendor
# revises the previous day's value overnight without bumping the snapshot
# date).  PrimitiveStep.build folds an optional data_content_fingerprint
# into the hashed_params dict; the bridge is the production code path that
# must COMPUTE and SUPPLY that fingerprint.  These helpers are the
# canonical computation, re-used from the five primitives in rates_agent/*
# that construct PrimitiveStep before wrapping their payload in a Panel.
#
# Determinism contract:
#   - SHA-256 over canonical-JSON (via lineage._canonical_json, the SAME
#     canonicaliser the lineage hash recipe itself uses — guarantees
#     byte-identical serialisation across NumPy / Pandas / Python versions).
#   - The encoded payload includes series identity bits (series_name +
#     units) so two structurally-different series with coincidentally
#     identical rows still differ.
#   - None / NaN cells serialise to JSON null — preserves the
#     missingness-as-gap contract from the module docstring.
#   - Empty payloads are well-defined (never raises).
# ============================================================================


def _compute_time_series_fingerprint(ts: TimeSeries) -> str:
    """Deterministic SHA-256 over a canonical TimeSeries payload."""
    sorted_rows = sorted(
        ts.rows,
        key=lambda r: r.date,
    ) if ts.rows else []
    payload: Dict[str, Any] = {
        "series_name": ts.series_name,
        "units": ts.units.value,
        "rows": [[r.date, r.value] for r in sorted_rows],
    }
    encoded = _canonical_json(payload)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _compute_panel_payload_fingerprint(
    payload: "pd.DataFrame",
    units_by_column: Dict[str, Any],
) -> str:
    """Deterministic SHA-256 over a Panel payload DataFrame + units mapping.

    The inner helper.  Used by both the high-level Panel bridge and the
    five primitive compute.py sites in rates_agent/* that construct a
    PrimitiveStep BEFORE wrapping their payload in a Panel artifact (they
    don't have a Panel instance yet).  Encoding is byte-identical to the
    Panel-instance wrapper below.
    """
    sorted_cols = sorted(payload.columns)
    units_block: Dict[str, str] = {
        str(col): units_by_column[col].value for col in sorted_cols
    }
    data_block: Dict[str, List[List[Any]]] = {}
    for col in sorted_cols:
        col_series = payload[col]
        rows: List[List[Any]] = []
        for idx in col_series.index:
            iso_date = (
                idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)
            )
            raw = col_series.loc[idx]
            cell: Optional[float]
            if pd.isna(raw):
                cell = None
            else:
                cell = float(raw)
            rows.append([iso_date, cell])
        rows.sort(key=lambda r: r[0])
        data_block[str(col)] = rows
    encoded_payload: Dict[str, Any] = {
        "columns": [str(c) for c in sorted_cols],
        "units_by_column": units_block,
        "data": data_block,
    }
    encoded = _canonical_json(encoded_payload)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _compute_panel_fingerprint(panel: Panel) -> str:
    """Deterministic SHA-256 over a Panel artifact's payload + units.

    Thin wrapper around _compute_panel_payload_fingerprint that accepts a
    built Panel instance.  Used by the high-level Panel bridge.
    """
    return _compute_panel_payload_fingerprint(
        panel.payload, panel.units_by_column,
    )


# ============================================================================
# LOW-LEVEL: TimeSeries -> Series
# ============================================================================

def time_series_to_artifact_series(
    ts: TimeSeries,
    *,
    primitive_step: PrimitiveStep,
    missingness_policy: MissingnessPolicy,
    frequency: Optional[Literal["B", "D", "W", "M", "Q", "Y", "irregular"]] = None,
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

    # Derive the frequency tag from the index when the caller did not
    # supply one (Decision 3 — frequency is load-bearing and must be
    # populated at the bridge, not left None on every production artifact,
    # which would make every operator's frequency-match check a no-op).
    # An explicit ``frequency=`` argument always wins.
    effective_frequency = (
        frequency if frequency is not None
        else _infer_frequency(payload.index)
    )

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
        frequency=effective_frequency,
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
    frequency: Optional[Literal["B", "D", "W", "M", "Q", "Y", "irregular"]] = None,
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
        ``tool_config.conventions['ffill_limit_days'].value``, with
        ``drop_nan=True`` and ``dedup_keep="last"`` passed explicitly
        to match ``shared.analytics.levels.clean_single_series``'s
        invariants — these defaults are pinned at the bridge so a
        future change to those invariants surfaces here as a
        deliberate update rather than silent drift.

        The auto-derived policy captures the cleaning REGIME of the
        primitive's upstream pipeline (ffill_limit / drop_nan /
        dedup_keep), NOT data identity.  Source field /
        as_of_date / output_field / curve+tenor are identity bits
        that live elsewhere on the artifact (``Series.units``,
        ``Series.series_key``, ``PrimitiveStep.params``,
        ``PrimitiveStep.as_of_date``, ``PrimitiveStep.output_field``).
        Two artifacts whose primitives share the same cleaning
        regime ARE compatible at the missingness layer by
        construction — even if they came from different primitives,
        different fields, or different markets.

        If that convention is not declared, the bridge raises
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
    # The MissingnessPolicy captures the CLEANING REGIME of the
    # primitive's upstream pipeline, NOT data identity.  Three
    # observable knobs in v1, all from ``shared.analytics.levels.
    # clean_single_series``:
    #
    #   - ``ffill_limit``   — max consecutive ffilled rows.  Read from
    #     the tool's YAML convention ``ffill_limit_days`` (the only
    #     parameterized knob; declared by every rates primitive).
    #   - ``drop_nan``      — always True under ``clean_single_series``.
    #     Passed explicitly so a future change to that invariant
    #     forces a deliberate update here.
    #   - ``dedup_keep``    — always "last" under ``clean_single_series``.
    #     Same explicit-pass-through rationale as ``drop_nan``.
    #
    # What missingness policy does NOT carry (data identity, captured
    # elsewhere on the artifact and lineage):
    #
    #   - source field name (``PX_LAST`` vs ``YLD_YTM_MID``) — lives in
    #     ``PrimitiveStep.params['field_name']`` and the lineage hash.
    #   - snapshot ``as_of_date``                          — lives in
    #     ``PrimitiveStep.as_of_date``.
    #   - which TimeSeries field was extracted              — lives in
    #     ``PrimitiveStep.output_field`` and ``Series.units``.
    #   - which curve / tenor / pair                        — lives in
    #     ``PrimitiveStep.params`` and ``Series.series_key``.
    #
    # Architectural intent: two artifacts whose primitives share the
    # SAME cleaning regime (e.g. both used ``ffill_limit_days=5`` with
    # the same ``clean_single_series`` invariants) ARE compatible at
    # the missingness layer by construction — even if they came from
    # different primitives, different fields, or different markets.
    # Operators discriminate via units / series_key / lineage; they
    # don't need missingness to also encode data identity.
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
        # Pass drop_nan / dedup_keep explicitly even though they
        # match the Pydantic field defaults — self-documenting at
        # the call site, and pins the bridge to the matching
        # ``clean_single_series`` invariants.  If those invariants
        # ever change, this line forces a deliberate update here
        # rather than silently inheriting drift from a default.
        resolved_policy = CleanSingleSeriesV1(
            ffill_limit=ffill_limit,
            drop_nan=True,
            dedup_keep="last",
        )

    # ------------------------------------------------------------------
    # 4. Pull the snapshot's as_of_date.  Every canonical primitive
    #    output has ``current_metrics.as_of_date`` (wire convention
    #    — see every per-tool-folder schema).  We read it from the
    #    validated model so the value is the schema-faithful one.
    # ------------------------------------------------------------------
    as_of_date = _extract_as_of_date(validated)

    # ------------------------------------------------------------------
    # 5. Build the PrimitiveStep with all six identity bits.
    #    PR-10E Codex audit gap #1: data_content_fingerprint +
    #    data_vintage close the original L5 contract — "Lineage hash
    #    includes input data content / vendor data-vintage stamp."
    #    When the vendor revises a previously-published value (same
    #    as_of_date, same params, same YAML), the fingerprint changes
    #    and the lineage hash changes with it.  data_vintage echoes
    #    the snapshot's as_of_date as a named first-class field so
    #    downstream consumers don't have to infer the vintage.
    # ------------------------------------------------------------------
    data_content_fingerprint = _compute_time_series_fingerprint(ts_obj)
    primitive_step = PrimitiveStep.build(
        name=tool_name,
        version=primitive_version,
        params=params.model_dump(mode="json"),
        tool_config_hash=tool_config.conventions_hash(),
        output_field=output_field,
        as_of_date=as_of_date,
        tool_config_path=tool_config_path,
        data_content_fingerprint=data_content_fingerprint,
        data_vintage=as_of_date,
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
# HIGH-LEVEL: tool output dict -> Panel  (Phase 1 PR 20)
# ============================================================================


def tool_output_to_artifact_panel(
    tool_output: Dict[str, Any],
    *,
    output_class: Type[BaseModel],
    output_field: str,
    tool_name: str,
    tool_config: ToolConfig,
    params: BaseModel,
    tool_config_path: Optional[str] = None,
    missingness_policy: Optional[MissingnessPolicy] = None,
    primitive_version: str = "1.0.0",
) -> Panel:
    """Convenience wrapper: primitive output dict → ``Panel`` artifact.

    Parallel to :func:`tool_output_to_artifact_series` but for
    Panel-emitting primitives (e.g.
    ``build_sovereign_yield_panel_tool``, ``compute_financing_rate_tool``).

    Why a separate bridge
    ---------------------
    The Series bridge is TimeSeries-shape-specific: it expects a
    canonical ``TimeSeries`` Pydantic object (rows + units +
    series_name) and converts it into a one-column ``pd.Series``
    with the bridge supplying the lineage step.

    A Panel-emitting primitive is structurally different:
      - The output schema's Panel field IS the already-built typed
        Panel artifact (validated by ``Panel``'s own model
        validator at primitive construction time).
      - The columns + units + missingness policy are owned by the
        primitive itself (not by the bridge's missingness inference).
      - The bridge's job is only: (a) validate the output dict
        against the primitive's *Output schema; (b) extract the
        Panel field; (c) rebuild the Panel with the bridge-built
        ``PrimitiveStep`` as lineage head so two callers running the
        same params produce the same head hash.

    Mirrors the discipline of the Series path: same identity-bits
    folded into the PrimitiveStep (params + tool_config_hash +
    output_field + as_of_date), same validate-first order, same
    raise-loudly-on-shape-error policy.

    Parameters
    ----------
    tool_output :
        The primitive's raw ``.model_dump()``'d output dict.
    output_class :
        The primitive's Pydantic ``*Output`` class.  Must declare a
        ``Panel`` (or ``Optional[Panel]``) field at ``output_field``.
    output_field :
        Which Panel-typed field of the output to lift (e.g.
        ``"panel"``).
    tool_name :
        MCP tool name; folded into the lineage step.
    tool_config :
        Loaded ``ToolConfig`` for the primitive (used to fold the
        conventions hash into the lineage step).
    params :
        The primitive's *Input instance (already validated).
    tool_config_path :
        Optional bookkeeping path; not in hash.
    missingness_policy :
        Optional explicit missingness policy.  When omitted, defaults
        to ``RawNoCleaning()`` because Panel columns can have
        independent NaN regimes — there is no unified per-column
        cleaning convention the bridge can auto-derive.  Caller can
        pass an explicit policy if the upstream primitive owns one.
    primitive_version :
        Folded into the lineage step's version field.

    Returns
    -------
    Panel
        Frozen artifact with lineage = a single ``PrimitiveStep``.

    Raises
    ------
    ValueError
        - Output dict does NOT validate against ``output_class``.
        - ``output_field`` is not declared on ``output_class``.
        - The extracted field is not a ``Panel`` instance.
    """
    # ------------------------------------------------------------------
    # 1. Validate the output dict against the primitive's *Output.
    # ------------------------------------------------------------------
    validated = output_class.model_validate(tool_output)

    # ------------------------------------------------------------------
    # 2. Confirm output_field exists + is Panel-typed.  Friendly
    #    error names the Panel-typed fields actually available.
    # ------------------------------------------------------------------
    fields = output_class.model_fields
    if output_field not in fields:
        panel_fields = [
            name for name, field in fields.items()
            if _is_panel_type(field.annotation)
        ]
        raise ValueError(
            f"{_PANEL_BRIDGE_NAME}: output_field={output_field!r} is "
            f"not declared on {output_class.__name__}.  Panel-typed "
            f"fields available: {sorted(panel_fields) if panel_fields else 'none'}."
        )
    if not _is_panel_type(fields[output_field].annotation):
        raise ValueError(
            f"{_PANEL_BRIDGE_NAME}: output_field={output_field!r} on "
            f"{output_class.__name__} is not a Panel (or Optional[Panel]) "
            f"field.  Use ``tool_output_to_artifact_series`` for "
            "TimeSeries-typed fields instead."
        )
    panel_obj = getattr(validated, output_field)
    if panel_obj is None:
        raise ValueError(
            f"{_PANEL_BRIDGE_NAME}: {output_class.__name__}."
            f"{output_field} is None — the primitive returned a None "
            "in the Panel field where a Panel artifact was expected."
        )
    if not isinstance(panel_obj, Panel):
        raise ValueError(
            f"{_PANEL_BRIDGE_NAME}: {output_class.__name__}."
            f"{output_field} is not a Panel instance — got "
            f"{type(panel_obj).__name__}."
        )

    # ------------------------------------------------------------------
    # 3. Resolve missingness policy.  Panel columns can have
    #    independent NaN regimes; the bridge does NOT auto-derive a
    #    unified policy (no per-column ``ffill_limit_days`` convention
    #    exists today).  Default to RawNoCleaning() so the Panel
    #    carries an honest "no unified regime" tag — the primitive's
    #    own ``missing_data_policy`` knob (if it has one) documents
    #    what cleaning happened upstream.
    # ------------------------------------------------------------------
    resolved_policy: MissingnessPolicy = (
        missingness_policy if missingness_policy is not None else RawNoCleaning()
    )

    # ------------------------------------------------------------------
    # 4. Pull the as_of_date.  Panel-emitting primitives may NOT carry
    #    a ``current_metrics.as_of_date`` block (e.g. a multi-leg
    #    Panel is a multi-instrument snapshot, not a single-point
    #    one).  Fall back to the last date in the Panel's index when
    #    ``current_metrics`` is absent.
    # ------------------------------------------------------------------
    if hasattr(validated, "current_metrics") and hasattr(
        validated.current_metrics, "as_of_date",
    ):
        as_of_date = str(validated.current_metrics.as_of_date)
    elif hasattr(validated, "as_of_end"):
        # Panel-shaped outputs typically carry as_of_end / as_of_start
        # bounding the Panel's calendar — use as_of_end as the snapshot
        # anchor for replay determinism.
        as_of_date = str(validated.as_of_end)
    else:
        # Last resort: last date in the Panel's index.
        if len(panel_obj.payload.index) == 0:
            raise ValueError(
                f"{_PANEL_BRIDGE_NAME}: cannot determine as_of_date — "
                f"{output_class.__name__} has neither ``current_metrics."
                "as_of_date`` nor ``as_of_end``, AND the Panel's index "
                "is empty.  The primitive must declare one of these to "
                "anchor replay determinism."
            )
        as_of_date = panel_obj.payload.index[-1].strftime("%Y-%m-%d")

    # ------------------------------------------------------------------
    # 5. Build the PrimitiveStep with all six identity bits.
    #    PR-10E Codex audit gap #1: data_content_fingerprint +
    #    data_vintage close the original L5 contract — see the
    #    matching comment in tool_output_to_artifact_series above.
    # ------------------------------------------------------------------
    data_content_fingerprint = _compute_panel_fingerprint(panel_obj)
    primitive_step = PrimitiveStep.build(
        name=tool_name,
        version=primitive_version,
        params=params.model_dump(mode="json"),
        tool_config_hash=tool_config.conventions_hash(),
        output_field=output_field,
        as_of_date=as_of_date,
        tool_config_path=tool_config_path,
        data_content_fingerprint=data_content_fingerprint,
        data_vintage=as_of_date,
    )

    # ------------------------------------------------------------------
    # 6. Construct the frozen Panel with the bridge's lineage.
    #    The primitive's own ``panel_obj`` has lineage = the primitive's
    #    internal step chain; we replace it with the bridge-anchored
    #    step so two callers running the same params (across the
    #    workflow executor) produce the same head hash.  The Panel
    #    validator re-runs on payload + units_by_column.
    # ------------------------------------------------------------------
    return Panel(
        payload=panel_obj.payload,
        units_by_column=panel_obj.units_by_column,
        missingness_policy=resolved_policy,
        lineage=Lineage.from_steps([primitive_step]),
    )


# ============================================================================
# INTERNAL HELPERS
# ============================================================================

def _coarse_frequency(alias: str) -> str:
    """Map a pandas offset alias (possibly anchored / pandas-2.2-renamed)
    to the coarse B/D/W/M/Q/Y tag, or 'irregular' if unrecognised."""
    base = alias.upper().split("-", 1)[0]
    if base in ("BME", "BM", "BMS", "CBM", "CBME"):
        return "M"
    if base in ("BQ", "BQE", "BQS"):
        return "Q"
    if base in ("BA", "BY", "BYE", "BAS", "BYS"):
        return "Y"
    if base == "B":
        return "B"
    if base in ("D", "C"):
        return "D"
    if base.startswith("W"):
        return "W"
    if base in ("M", "ME", "MS", "SM", "SMS"):
        return "M"
    if base in ("Q", "QE", "QS"):
        return "Q"
    if base in ("A", "Y", "YE", "AS", "YS"):
        return "Y"
    return "irregular"


def _infer_frequency(
    index: Any,
) -> Optional[Literal["B", "D", "W", "M", "Q", "Y", "irregular"]]:
    """Derive a coarse frequency tag from a sorted DatetimeIndex (Decision 3).

    Returns B/D/W/M/Q/Y when pandas infers a regular cadence, ``'irregular'``
    for a non-uniform but >=3-point index, or ``None`` for a too-short
    (<3-point) index where inference is unreliable.  Never raises.
    """
    if not isinstance(index, pd.DatetimeIndex) or len(index) < 3:
        return None
    try:
        inferred = pd.infer_freq(index)
    except (ValueError, TypeError):
        return "irregular"
    if inferred is None:
        return "irregular"
    return _coarse_frequency(inferred)  # type: ignore[return-value]


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


def _is_panel_type(annotation: Any) -> bool:
    """Best-effort check: does this Pydantic field annotation declare
    a ``Panel`` (or ``Optional[Panel]``) shape?

    Parallel to ``_is_time_series_type``.  Used by
    ``tool_output_to_artifact_panel`` to surface a clear error when a
    caller picks a non-Panel output_field for a Panel-producing
    primitive.
    """
    if annotation is Panel:
        return True
    args = getattr(annotation, "__args__", None)
    if args:
        return any(arg is Panel for arg in args)
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


# ============================================================================
# REVERSE PATH: Series -> TimeSeries  (Work Item 3)
# ============================================================================

# Public marker that downstream consumers can match on to detect a
# bridge-generated description.  Kept module-level so callers /
# tests can reference it without re-deriving the prefix string —
# avoids drift if the format ever changes.
_LINEAGE_DESCRIPTION_PREFIX = "derived: "
_LINEAGE_STEP_SEPARATOR = " → "


def artifact_series_to_time_series(
    series: Series,
    *,
    description_override: Optional[str] = None,
) -> TimeSeries:
    """Convert a frozen ``Series`` artifact into a wire ``TimeSeries``.

    The reverse path of ``time_series_to_artifact_series``.  Used to
    serialize operator outputs back to the wire format for the LLM /
    frontend / future REST endpoints, AND to chain operator outputs
    into downstream primitives that consume ``PastedTimeSeries``-shaped
    inputs.

    Round-trip with the forward path is *semantic-faithful*:

      - ``(rows, units, series_name)`` round-trip BYTE-identical
        (modulo the documented ``NaN`` ↔ ``None`` semantic mapping —
        see module docstring).
      - ``description`` differs by design.  The forward path
        consumes the primitive's free-form description; this reverse
        path generates a lineage summary suitable for human-readable
        provenance.  The original description is recoverable from the
        structured ``Series.lineage`` (``PrimitiveStep.params`` +
        downstream ``OperatorStep.params``) but is NOT echoed on the
        wire.  Callers who need a different description can pass
        ``description_override``.

    Parameters
    ----------
    series :
        Frozen artifact to demote.  May be empty (a downstream
        operator that produced no rows is legitimate, e.g.
        ``align_series`` over disjoint indices); the wire shape
        accepts an empty ``rows`` list.
    description_override :
        Optional explicit description to use on the wire instead of
        the auto-generated lineage summary.  Honored verbatim — the
        bridge does NOT prepend ``"derived: "`` or otherwise mutate.
        Useful when the artifact will be displayed to a human and
        the lineage summary isn't audience-appropriate (e.g.
        marketing-friendly chart titles, free-form annotations).

    Returns
    -------
    TimeSeries
        Wire-compatible Pydantic model with one ``TimeSeriesRow`` per
        index position in the artifact's payload.  ``NaN`` values
        become ``None``.

    Notes
    -----
    Linear lineage summary only.  ``OperatorStep.auxiliary_lineages``
    (the chains for non-primary inputs to binary/N-ary operators
    like ``series_arithmetic``) are NOT walked into the description
    string — they remain visible on the structured artifact's
    ``Series.lineage``.  A future PR can extend the format if
    desk-facing summaries need the auxiliary chains inline; the
    plan's resolved Q1 was "description-only summary this sprint",
    and any richer wire format requires a ``TimeSeries`` schema
    bump (deferred).
    """
    # ------------------------------------------------------------------
    # 1. Build wire rows: NaN → None at the same DatetimeIndex
    #    position; numeric values cast through ``float`` to drop any
    #    pandas-native dtypes (e.g. np.float64) that would survive
    #    ``model_dump`` but feel surprising in ad-hoc inspection.
    # ------------------------------------------------------------------
    rows: List[TimeSeriesRow] = []
    for ts, val in series.payload.items():
        wire_val: Optional[float]
        if pd.isna(val):
            wire_val = None
        else:
            wire_val = float(val)
        rows.append(
            TimeSeriesRow(
                date=ts.strftime("%Y-%m-%d"),
                value=wire_val,
            )
        )

    # ------------------------------------------------------------------
    # 2. Resolve description.  Override wins when present; otherwise
    #    derive a linear lineage summary.  ``TimeSeries.description``
    #    is required + ``min_length=1``, so every reverse-path output
    #    carries SOMETHING — the override path must not pass an empty
    #    string (Pydantic will reject it loudly).
    # ------------------------------------------------------------------
    description: str
    if description_override is not None:
        description = description_override
    else:
        description = _format_lineage_summary(series.lineage)

    return TimeSeries(
        series_name=series.series_key,
        units=series.units,
        description=description,
        rows=rows,
    )


# ============================================================================
# REVERSE-PATH HELPERS
# ============================================================================

def _format_lineage_summary(lineage: Lineage) -> str:
    """Render a ``Lineage`` chain as a one-line human-readable summary.

    Format::

        "derived: <step0.name>[ → <stepN.name>]*"

    Steps are emitted in order from oldest to newest (matches
    ``Lineage.steps``).  Only the linear primary chain is walked;
    ``OperatorStep.auxiliary_lineages`` (right-hand operands of
    binary operators) are NOT included — they remain on the
    structured artifact via ``series.lineage``.  Keeps the wire
    description bounded and readable; full provenance is recoverable
    from the structured chain when needed.

    A non-empty ``Lineage`` is guaranteed by ``Lineage.from_steps``
    (which raises if given an empty list), so this helper is safe
    to call on any ``Series.lineage``.
    """
    names = [step.name for step in lineage.steps]
    return _LINEAGE_DESCRIPTION_PREFIX + _LINEAGE_STEP_SEPARATOR.join(names)


__all__ = [
    "time_series_to_artifact_series",
    "tool_output_to_artifact_series",
    "tool_output_to_artifact_panel",
    "artifact_series_to_time_series",
    "_compute_time_series_fingerprint",
    "_compute_panel_fingerprint",
    "_compute_panel_payload_fingerprint",
]
