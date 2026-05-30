"""shared.workflow.registry — operator dispatch + primitive resolver protocol.

The substrate's mapping between node ``operator_name`` /
``tool_name`` strings and the actual callable + parameter-class.

Two registries, two policies:

  - **Operator registry** (closed family).  Operators live in
    ``shared/operators/`` — they're already finance-blind, so the
    substrate can hardcode dispatch entries for each one without
    violating the finance-blindness discipline.  Adding a new
    operator requires editing this file AND the operator passing
    its admission checklist (``operator_architecture.md``).

  - **Primitive resolver** (caller-supplied protocol).  Primitives
    live in ``rates_agent/<domain>/tools/`` — the substrate must
    NOT import from there.  The executor accepts a
    ``PrimitiveResolver`` protocol that the agent layer
    implements; this is what keeps ``shared/workflow/`` finance-
    blind.

Operator-input slot types
-------------------------
Each operator declares what artifact type each of its named input
slots expects, so the validator can check edge type-compatibility
before execution.  Closed-enum artifact-type names so the
validator can compare structurally.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Protocol, Type

from pydantic import BaseModel, ConfigDict, Field

from shared.artifacts.registry import (
    ARTIFACT_CLASS_TO_NAME,
    ArtifactTypeName,
)
from shared.operators.align_series import align_series, AlignSeriesParams
from shared.operators.apply_mask import apply_mask, ApplyMaskParams
from shared.operators.conditional_aggregate import (
    conditional_aggregate,
    ConditionalAggregateParams,
)
from shared.operators.cointegration import cointegration, CointegrationParams
from shared.operators.correlation import correlation, CorrelationParams
from shared.operators.convert_units import convert_units, ConvertUnitsParams
from shared.operators.event_windows import event_windows, EventWindowsParams
from shared.operators.rolling_regression import (
    rolling_regression,
    RollingRegressionParams,
)
from shared.operators.percentile_rank import (
    percentile_rank,
    PercentileRankParams,
)
from shared.operators.rolling_correlation import (
    rolling_correlation,
    RollingCorrelationParams,
)
from shared.operators.rolling_statistic import (
    rolling_statistic,
    RollingStatisticParams,
)
from shared.operators.rolling_zscore import (
    rolling_zscore,
    RollingZscoreParams,
)
from shared.operators.select_from_series_set import (
    select_from_series_set,
    SelectFromSeriesSetParams,
)
from shared.operators.series_arithmetic import (
    series_arithmetic,
    SeriesArithmeticParams,
)
from shared.operators.summarize_series import (
    summarize_series,
    SummarizeSeriesParams,
)
from shared.operators.threshold_events import (
    threshold_events,
    ThresholdEventsParams,
)


# ============================================================================
# ARTIFACT TYPE NAMES (closed enum used by the validator)
# ============================================================================

# Both ``ARTIFACT_TYPE_NAMES`` and ``_ARTIFACT_TYPE_MAP`` are DERIVED from
# the canonical ``ArtifactTypeName`` enum in ``shared.artifacts.registry``
# (ART2 / ART6: single source of truth).  Adding a new artifact wrapper
# means adding a member to that enum + ``ARTIFACT_CLASS_TO_NAME`` — every
# downstream tuple/dict re-derives automatically and the lock-step test
# (``tests/test_artifact_closed_family_lockstep.py``) gates the rest.
#
# Canonical ordering (Series, SeriesSet, EventSet, Panel, WindowedPanel,
# ScalarMetric) is preserved because ``Enum`` iteration is
# definition-order.

ARTIFACT_TYPE_NAMES: tuple[str, ...] = tuple(
    member.value for member in ArtifactTypeName
)


# Runtime class -> closed-enum name map.  A DERIVED companion to
# ``ARTIFACT_TYPE_NAMES`` (identical membership, keyed by class for the
# ``isinstance`` dispatch in ``artifact_type_name`` below).  Hoisted to
# module scope so it is built once (not per call) AND importable, so the
# lock-step test ``tests/test_artifact_closed_family_lockstep.py`` can
# assert it stays in sync with the canonical enum (ART2/ART6).  Values
# are plain ``str`` (each enum member's ``.value``) so
# ``artifact_type_name`` returns the same ``str`` it always returned.
_ARTIFACT_TYPE_MAP: dict[type, str] = {
    cls: name.value for cls, name in ARTIFACT_CLASS_TO_NAME.items()
}


def artifact_type_name(artifact: Any) -> str:
    """Return the closed-enum artifact type name for a runtime
    artifact instance.  Used by the executor to label produced
    artifacts so the validator can compare them at the typed-edge
    layer.

    Raises ValueError if the artifact is not a recognised type —
    deliberately loud so a non-artifact return value (e.g. a raw
    dict) surfaces at execution time rather than silently
    propagating downstream.
    """
    for cls, name in _ARTIFACT_TYPE_MAP.items():
        if isinstance(artifact, cls):
            return name
    raise ValueError(
        f"Workflow substrate does not recognise artifact type "
        f"{type(artifact).__name__}.  Expected one of "
        f"{ARTIFACT_TYPE_NAMES}.  If a primitive returned a non-"
        "artifact value, the bridge layer "
        "(``shared.artifacts.adapters.from_time_series``) was "
        "bypassed somewhere."
    )


# ============================================================================
# OPERATOR REGISTRY (closed family)
# ============================================================================


class OperatorSpec(BaseModel):
    """Closed-family entry describing how the substrate dispatches
    one operator.

    Fields
    ------
    operator_name :
        The closed-family identifier; matches
        ``OperatorNode.operator_name``.
    callable :
        The operator's public callable (e.g.
        ``shared.operators.align_series.align_series``).
    params_class :
        The operator's Pydantic ``*Params`` schema.  The executor
        constructs an instance from the node's ``params`` dict
        (or passes ``None`` if the dict is empty + the operator's
        signature accepts ``params=None``).
    input_slots :
        Map of input-slot name → artifact type name (closed enum).
        The validator uses this to check edge type-compatibility.
        A slot may also have ``"List[Series]"`` etc. for
        list-shaped inputs.
    output_type :
        Artifact type name the operator emits (closed enum).
    accepts_scalar_input :
        Some operators accept a scalar OR an artifact at certain
        slots (e.g. ``series_arithmetic.right`` can be a Series or
        a Python scalar).  The validator's type-compat check
        relaxes for slots in this set.  Codex P2 follow-up
        (PR #78): scalar slots can also be filled by
        ``LiteralBinding`` instances on the workflow.
    arity_validator :
        Optional per-operator arity hook.  Called by
        ``validate_workflow`` with ``(node_params, bound_slots,
        literal_slots)`` and returns either ``None`` (OK) or an
        error message.  Operators with conditional arity (e.g.
        ``series_arithmetic`` whose ``right`` slot's requiredness
        depends on ``op``) declare one.  Operators with simple
        always-required slots leave this as ``None`` and rely on
        the substrate's default ``slot in accepts_scalar_input``
        check.
    unit_validator :
        Optional per-operator unit-compatibility hook.  Called by
        ``validate_workflow`` with ``(node_params,
        source_units_by_slot)`` where ``source_units_by_slot`` is
        a dict mapping each bound slot name to the unit string
        ``str`` (one of ``TimeSeriesUnits`` enum values) or
        ``None`` if the upstream source's unit is not declared.
        Returns ``None`` (OK) or an error message.  Operators
        with cross-slot unit-algebra (``series_arithmetic`` requires
        same units for ``add``/``subtract``) declare one; the
        validator skips checks where source units are
        ``None`` (best-effort discipline — operator runtime
        check stays as the authoritative gate).
    """

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    operator_name: str
    callable: Callable[..., Any]
    params_class: Optional[Type[BaseModel]]
    input_slots: Dict[str, str]
    output_type: str
    accepts_scalar_input: tuple[str, ...] = ()
    # OPR15: a discriminator / positional arg (e.g. series_arithmetic's
    # ``op``) is DECLARED here, not injected by name in the executor.
    # The executor stays generic; the operator resolves the value from
    # ``params``.  The registry-consistency meta-test allows these
    # declared names in the operator's signature.
    discriminator_args: tuple[str, ...] = ()
    arity_validator: Optional[
        Callable[[Dict[str, Any], set, set], Optional[str]]
    ] = None
    unit_validator: Optional[
        Callable[[Dict[str, Any], Dict[str, Optional[str]]], Optional[str]]
    ] = None


# ============================================================================
# OPERATOR-SPECIFIC ARITY + UNIT HOOKS
# ============================================================================
#
# Per-operator hooks live next to the registry so any future
# operator with conditional arity / unit algebra has a clear
# precedent.  Hooks are pure functions (no side effects) so the
# validator can call them repeatedly without ordering risk.


# series_arithmetic op categorization — kept in sync with
# shared.operators.series_arithmetic.operator._UNARY_OPS.
_SERIES_ARITHMETIC_UNARY_OPS = ("diff", "pct_change")
_SERIES_ARITHMETIC_BINARY_OPS = ("add", "subtract", "multiply", "divide")


def _series_arithmetic_arity_validator(
    node_params: Dict[str, Any],
    bound_edge_slots: set,
    bound_literal_slots: set,
) -> Optional[str]:
    """Codex P2 follow-up (PR #78): the prior validator's blanket
    ``accepts_scalar_input`` skip allowed binary ops to validate
    without a ``right`` operand.  This hook fixes that — binary
    ops require ``right`` bound (via edge OR literal), unary ops
    forbid it.
    """
    op = node_params.get("op")
    has_right = "right" in bound_edge_slots or "right" in bound_literal_slots
    if op in _SERIES_ARITHMETIC_UNARY_OPS:
        if has_right:
            return (
                f"series_arithmetic op={op!r} is unary; ``right`` "
                "must be unbound (no edge AND no literal binding "
                "targeting ``right``)."
            )
    elif op in _SERIES_ARITHMETIC_BINARY_OPS:
        if not has_right:
            return (
                f"series_arithmetic op={op!r} is binary; ``right`` "
                "must be bound by either an edge (Series operand) "
                "or a LiteralBinding (scalar operand)."
            )
    elif op is None:
        return (
            "series_arithmetic requires params.op (one of "
            f"{_SERIES_ARITHMETIC_UNARY_OPS + _SERIES_ARITHMETIC_BINARY_OPS}) "
            "but it was not supplied."
        )
    # Unknown op falls through; the operator's own *Params validator
    # catches it at execution time.
    return None


def _series_arithmetic_unit_validator(
    node_params: Dict[str, Any],
    source_units: Dict[str, Optional[str]],
) -> Optional[str]:
    """Codex P2 follow-up (PR #78): substrate-level same-unit
    enforcement for binary same-unit ops.  Mirrors the operator's
    own _resolve_output_units logic but at validate-time so
    template authors catch unit mismatches before any node runs.

    Best-effort: if either source's units are unknown (None — e.g.
    the upstream is a primitive whose ``output_field_units``
    aren't declared in PrimitiveSpec, or an operator chain whose
    unit propagation isn't yet declared in the registry), the
    substrate skips the check and lets the operator's runtime
    refusal fire as the authoritative gate.
    """
    op = node_params.get("op")
    # Operators that require same units across left + right.
    same_unit_ops = ("add", "subtract")
    # divide(Series, Series) also requires same units (output is
    # RATIO).  divide(Series, scalar) preserves left units; the
    # validator can't easily distinguish those without the right
    # operand in hand, so we check only when both sources are
    # known to be Series-typed (i.e. both have a declared unit).
    if op in same_unit_ops + ("divide",):
        left = source_units.get("left")
        right = source_units.get("right")
        if left is not None and right is not None and left != right:
            return (
                f"series_arithmetic op={op!r} requires matching "
                f"units across left + right, but the substrate "
                f"detected left.units={left!r} vs right.units="
                f"{right!r} from declared upstream sources.  Add "
                "an explicit unit conversion at the template "
                "layer or pass operands of matching units."
            )
    return None


# The closed-family registry.  Adding a new operator requires
# adding an entry here AND the operator passing its admission
# checklist (``operator_architecture.md``).
OPERATOR_REGISTRY: Dict[str, OperatorSpec] = {
    "align_series": OperatorSpec(
        operator_name="align_series",
        callable=align_series,
        params_class=AlignSeriesParams,
        # series_list takes List[Series] — encoded as a special
        # marker; validator handles list-aggregation across edges.
        input_slots={"series_list": "List[Series]"},
        output_type="SeriesSet",
    ),
    "select_from_series_set": OperatorSpec(
        operator_name="select_from_series_set",
        callable=select_from_series_set,
        params_class=SelectFromSeriesSetParams,
        # The lone consumer slot accepts a SeriesSet (e.g. produced by
        # align_series upstream).  Output is a single Series.
        input_slots={"series_set": "SeriesSet"},
        output_type="Series",
    ),
    "series_arithmetic": OperatorSpec(
        operator_name="series_arithmetic",
        callable=series_arithmetic,
        params_class=SeriesArithmeticParams,
        input_slots={"left": "Series", "right": "Series"},
        output_type="Series",
        # ``op`` is a declared discriminator (OPR15) — the operator
        # resolves it from params; the executor injects nothing by name.
        discriminator_args=("op",),
        # `right` can be a Series OR a Python scalar (int/float)
        # for binary scalar arithmetic, or absent for unary ops.
        # The arity_validator below enforces the conditional
        # rules; the substrate-default ``accepts_scalar_input``
        # blanket-skip is now superseded by the explicit hook.
        accepts_scalar_input=("right",),
        arity_validator=_series_arithmetic_arity_validator,
        unit_validator=_series_arithmetic_unit_validator,
    ),
    # v2.0 reference operator (ADR 0016) — the canonical finance-blind
    # ``statistical_relationship`` operator: two Series → one
    # ScalarMetric (the full-sample correlation coefficient).  Built to
    # OPR1–OPR16; the OPR16 meta-test gates it.
    "correlation": OperatorSpec(
        operator_name="correlation",
        callable=correlation,
        params_class=CorrelationParams,
        input_slots={"left": "Series", "right": "Series"},
        output_type="ScalarMetric",
    ),
    # v2.0 — the single sanctioned unit-conversion operator (ADR 0016
    # Decision 4).  One Series in, one Series out (re-tagged to the
    # target unit).  ``unit_conversion`` method family.
    "convert_units": OperatorSpec(
        operator_name="convert_units",
        callable=convert_units,
        params_class=ConvertUnitsParams,
        input_slots={"series": "Series"},
        output_type="Series",
    ),
    "threshold_events": OperatorSpec(
        operator_name="threshold_events",
        callable=threshold_events,
        params_class=ThresholdEventsParams,
        input_slots={"series": "Series"},
        output_type="EventSet",
    ),
    "event_windows": OperatorSpec(
        operator_name="event_windows",
        callable=event_windows,
        params_class=EventWindowsParams,
        input_slots={"events": "EventSet", "target": "Series"},
        output_type="WindowedPanel",
    ),
    "conditional_aggregate": OperatorSpec(
        operator_name="conditional_aggregate",
        callable=conditional_aggregate,
        params_class=ConditionalAggregateParams,
        input_slots={"panel": "WindowedPanel"},
        output_type="Series",
    ),
    "apply_mask": OperatorSpec(
        operator_name="apply_mask",
        callable=apply_mask,
        params_class=ApplyMaskParams,
        # Two slots: a Series payload + an EventSet boolean mask.
        # Output is a Series subsampled to mask=True dates.
        input_slots={"series": "Series", "mask": "EventSet"},
        output_type="Series",
    ),
    "rolling_regression": OperatorSpec(
        operator_name="rolling_regression",
        callable=rolling_regression,
        params_class=RollingRegressionParams,
        # lhs = dependent / target; rhs = single regressor in V1.
        # Output is a SeriesSet keyed by {beta, alpha, r_squared}.
        input_slots={"lhs": "Series", "rhs": "Series"},
        output_type="SeriesSet",
    ),
    # v2.0 (ADR 0016) — single_series_transform: trailing-window
    # standardisation of a Series.  One Series in, one Series out
    # (always in Z_SCORE units).  The composition primitive for any
    # "where are we vs our own recent history" question.
    "rolling_zscore": OperatorSpec(
        operator_name="rolling_zscore",
        callable=rolling_zscore,
        params_class=RollingZscoreParams,
        input_slots={"series": "Series"},
        output_type="Series",
    ),
    # v2.0 (ADR 0016) — single_series_transform: generic windowed
    # reducer (mean / std / min / max / sum) over a Series.  One Series
    # in, one Series out (input unit preserved).  The composition
    # primitive for rolling vol, moving averages, rolling ranges.
    "rolling_statistic": OperatorSpec(
        operator_name="rolling_statistic",
        callable=rolling_statistic,
        params_class=RollingStatisticParams,
        input_slots={"series": "Series"},
        output_type="Series",
    ),
    # v2.0 (ADR 0016) — single_series_transform: trailing- or
    # expanding-window percentile rank ("where does today sit vs
    # history").  One Series in, one Series out, always in PCT_RANK
    # units (0–100).  The canonical macro "rich/cheap vs history"
    # primitive.
    "percentile_rank": OperatorSpec(
        operator_name="percentile_rank",
        callable=percentile_rank,
        params_class=PercentileRankParams,
        input_slots={"series": "Series"},
        output_type="Series",
    ),
    # v2.0 (ADR 0016) — statistical_relationship: windowed correlation
    # between two index-aligned Series.  Two Series in, one Series out
    # (rolling coefficients in RATIO units).  The SEPARATE-operator
    # counterpart of ``correlation`` per OPR2 — does NOT align, does
    # NOT take a window flag on ``correlation`` itself.
    "rolling_correlation": OperatorSpec(
        operator_name="rolling_correlation",
        callable=rolling_correlation,
        params_class=RollingCorrelationParams,
        input_slots={"left": "Series", "right": "Series"},
        output_type="Series",
    ),
    # v2.0 (ADR 0016) — statistical_relationship: Engle–Granger
    # two-step cointegration test between two index-aligned Series.
    # Two Series in, one ScalarMetric out (the ADF test statistic, in
    # RATIO units — dimensionless).  The pairs / relative-value
    # workhorse for "is this spread stationary?" research.
    "cointegration": OperatorSpec(
        operator_name="cointegration",
        callable=cointegration,
        params_class=CointegrationParams,
        input_slots={"left": "Series", "right": "Series"},
        output_type="ScalarMetric",
    ),
    "summarize_series": OperatorSpec(
        operator_name="summarize_series",
        callable=summarize_series,
        params_class=SummarizeSeriesParams,
        # Single-Series input → single-row summary Series at a fixed
        # sentinel date.  Lets two per-regime summaries feed into
        # series_arithmetic.subtract for the canonical "compare across
        # regimes" step.
        input_slots={"series": "Series"},
        output_type="Series",
    ),
}


def known_operators() -> List[str]:
    """Return the sorted list of operator names the substrate
    knows how to dispatch.  Exposed for diagnostic / catalogue
    purposes (e.g. so a workflow validator can list candidates
    when an unknown operator_name is referenced)."""
    return sorted(OPERATOR_REGISTRY.keys())


# ============================================================================
# PRIMITIVE RESOLVER (caller-supplied protocol)
# ============================================================================


class PrimitiveSpec(BaseModel):
    """The resolved bundle for one primitive call.

    Returned by a ``PrimitiveResolver`` lookup; carries everything
    the executor needs to invoke a primitive AND lift its output
    through the bridge into a typed ``Series`` artifact.

    Fields
    ------
    tool_name :
        The MCP tool name (echoed for symmetry with
        ``OperatorSpec.operator_name``).
    callable :
        The primitive's public callable (e.g.
        ``rates_agent.ois.tools.swap_spread.calculate_swap_spread``).
        Called as ``callable(engine=engine, params=params,
        config=config)``.
    input_class :
        The primitive's Pydantic ``*Input`` class.  The executor
        constructs an instance from the node's ``params`` dict.
    output_class :
        The primitive's Pydantic ``*Output`` class.  Passed to the
        bridge's ``tool_output_to_artifact_series`` so it can
        validate the dict before extracting the requested
        ``time_series*`` field.
    config_path :
        Path to the primitive's bundled ``config.yaml``.  Loaded
        via ``shared.config.load_tool_config`` (cached) and passed
        to the primitive call AND the bridge.
    output_field_units :
        OPTIONAL.  Map of ``time_series*`` field name (matching
        ``PrimitiveNode.output_field``) → unit string (one of the
        ``TimeSeriesUnits`` enum values, e.g. ``"bps"``,
        ``"percent"``, ``"z_score"``).  When declared, the
        substrate's validator can perform best-effort
        unit-compatibility checks at validate-time across operator
        boundaries (Codex P2 follow-up — PR #78).  When omitted,
        the validator skips the unit check for primitive outputs
        of this tool and falls back to the operator's runtime
        unit-algebra refusal.

        Each agent's primitive resolver populates this from the
        primitive's known output declarations.  E.g. for OIS
        curve_spread::

            output_field_units = {
                "time_series": "bps",
                "time_series_spread": "bps",
                "time_series_zscore": "z_score",
            }

        Best-effort discipline: declaring units here is OPTIONAL,
        not required.  Operator runtime checks remain the
        authoritative gate.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    tool_name: str
    callable: Callable[..., Any]
    input_class: Type[BaseModel]
    output_class: Type[BaseModel]
    config_path: Path
    output_field_units: Dict[str, str] = Field(default_factory=dict)
    # PR 20: executor uses this hint to pick the right bridge
    # (Series vs Panel).  Defaults to "Series" so every PR-12-era
    # primitive registration keeps working unchanged.  New
    # Panel-producing primitives (build_sovereign_yield_panel_tool,
    # compute_financing_rate_tool) set this to "Panel".  The bridge
    # function lives at
    # ``shared/artifacts/adapters/from_time_series.py:tool_output_to_artifact_panel``.
    output_artifact_type: str = Field(
        default="Series",
        description=(
            "Artifact-type hint the executor uses to pick the bridge "
            "(Series → tool_output_to_artifact_series, "
            "Panel → tool_output_to_artifact_panel).  Closed-enum "
            "values from ``ARTIFACT_TYPE_NAMES``."
        ),
    )


class PrimitiveResolver(Protocol):
    """Caller-supplied protocol for resolving a tool name into the
    callable + schemas + config needed to invoke it.

    The substrate stays finance-blind by accepting this protocol
    rather than importing primitives directly.  Agent layers
    (e.g. ``rates_agent``) provide the concrete implementation;
    tests provide a synthetic resolver that returns mock
    primitives.

    Implementations should:

      - Raise ``KeyError`` (or a more specific subclass) when
        ``tool_name`` is not recognised.
      - Be deterministic — the same tool_name always returns the
        same ``PrimitiveSpec`` (so the substrate's caching and
        replay-determinism guarantees hold).
      - Be cheap to call (the executor may resolve the same tool
        multiple times across a multi-node workflow).
    """

    def __call__(self, tool_name: str) -> PrimitiveSpec: ...


__all__ = [
    "ARTIFACT_TYPE_NAMES",
    "artifact_type_name",
    "OperatorSpec",
    "OPERATOR_REGISTRY",
    "known_operators",
    "PrimitiveSpec",
    "PrimitiveResolver",
]
