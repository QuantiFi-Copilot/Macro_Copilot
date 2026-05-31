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
from shared.workflow.slots import OutputDescriptor, SlotDescriptor
from shared.operators.align_series import (
    align_series, AlignSeriesParams,
    CONFIG_PATH as _ALIGN_SERIES_CONFIG_PATH,
)
from shared.operators.apply_mask import (
    apply_mask, ApplyMaskParams,
    CONFIG_PATH as _APPLY_MASK_CONFIG_PATH,
)
from shared.operators.conditional_aggregate import (
    conditional_aggregate,
    ConditionalAggregateParams,
    CONFIG_PATH as _CONDITIONAL_AGGREGATE_CONFIG_PATH,
)
from shared.operators.cointegration import (
    cointegration, CointegrationParams,
    CONFIG_PATH as _COINTEGRATION_CONFIG_PATH,
)
from shared.operators.correlation import (
    correlation, CorrelationParams,
    CONFIG_PATH as _CORRELATION_CONFIG_PATH,
)
from shared.operators.convert_units import (
    convert_units, ConvertUnitsParams,
    CONFIG_PATH as _CONVERT_UNITS_CONFIG_PATH,
)
from shared.operators.event_windows import (
    event_windows, EventWindowsParams,
    CONFIG_PATH as _EVENT_WINDOWS_CONFIG_PATH,
)
from shared.operators.rolling_regression import (
    rolling_regression,
    RollingRegressionParams,
    CONFIG_PATH as _ROLLING_REGRESSION_CONFIG_PATH,
)
from shared.operators.percentile_rank import (
    percentile_rank,
    PercentileRankParams,
    CONFIG_PATH as _PERCENTILE_RANK_CONFIG_PATH,
)
from shared.operators.rolling_correlation import (
    rolling_correlation,
    RollingCorrelationParams,
    CONFIG_PATH as _ROLLING_CORRELATION_CONFIG_PATH,
)
from shared.operators.rolling_statistic import (
    rolling_statistic,
    RollingStatisticParams,
    CONFIG_PATH as _ROLLING_STATISTIC_CONFIG_PATH,
)
from shared.operators.rolling_zscore import (
    rolling_zscore,
    RollingZscoreParams,
    CONFIG_PATH as _ROLLING_ZSCORE_CONFIG_PATH,
)
from shared.operators.select_from_series_set import (
    select_from_series_set,
    SelectFromSeriesSetParams,
    CONFIG_PATH as _SELECT_FROM_SERIES_SET_CONFIG_PATH,
)
from shared.operators.series_arithmetic import (
    series_arithmetic,
    SeriesArithmeticParams,
    CONFIG_PATH as _SERIES_ARITHMETIC_CONFIG_PATH,
)
from shared.operators.summarize_series import (
    summarize_series,
    SummarizeSeriesParams,
    CONFIG_PATH as _SUMMARIZE_SERIES_CONFIG_PATH,
)
from shared.operators.threshold_events import (
    threshold_events,
    ThresholdEventsParams,
    CONFIG_PATH as _THRESHOLD_EVENTS_CONFIG_PATH,
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
        Map of input-slot name → ``SlotDescriptor``.  Each
        descriptor declares the closed-family artifact type the slot
        expects, a one-line description, whether the slot is
        list-shaped (fan-in), and whether the slot accepts a scalar
        literal in lieu of an artifact edge.  Replaces the prior
        ``Dict[str, str]`` encoding (with sibling
        ``accepts_scalar_input`` tuple and ``"List[X]"`` string
        prefixes).
    output :
        ``OutputDescriptor`` declaring the closed-family artifact
        type the operator emits plus a one-line description.
        Replaces the prior bare ``output_type: str`` field.
    arity_validator :
        Optional per-operator arity hook.  Called by
        ``validate_workflow`` with ``(node_params, bound_slots,
        literal_slots)`` and returns either ``None`` (OK) or an
        error message.  Operators with conditional arity (e.g.
        ``series_arithmetic`` whose ``right`` slot's requiredness
        depends on ``op``) declare one.  Operators with simple
        always-required slots leave this as ``None`` and rely on
        the substrate's default per-slot
        ``SlotDescriptor.accepts_scalar`` check.
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
    input_slots: Dict[str, SlotDescriptor]
    output: OutputDescriptor
    # PR-2 of the open-DAG PoC: pointer to the operator's bundled
    # ``config.yaml`` (mirrors ``PrimitiveSpec.config_path``).  Read by
    # ``shared.workflow.operator_catalogue`` to render the per-operator
    # LLM card that the L3 Composer sees in its prompt.  P10: the
    # description content lives ONLY inside the YAML — this field is a
    # path, not duplicated content.
    config_path: Path
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
        config_path=_ALIGN_SERIES_CONFIG_PATH,
        # ``series_list`` is list-shaped: multiple inbound edges fan
        # in as a Python ``List[Series]`` (replaces the prior
        # ``"List[Series]"`` string-encoded prefix).
        input_slots={
            "series_list": SlotDescriptor.list_of(
                "Series",
                "N Series to align onto a common DatetimeIndex.",
            ),
        },
        output=OutputDescriptor.of(
            "SeriesSet",
            "Aligned bundle keyed by each input Series's identifier.",
        ),
    ),
    "select_from_series_set": OperatorSpec(
        operator_name="select_from_series_set",
        callable=select_from_series_set,
        params_class=SelectFromSeriesSetParams,
        config_path=_SELECT_FROM_SERIES_SET_CONFIG_PATH,
        input_slots={
            "series_set": SlotDescriptor.of(
                "SeriesSet",
                "Source bundle to pick one named Series from.",
            ),
        },
        output=OutputDescriptor.of(
            "Series",
            "The single Series extracted by ``params.key``.",
        ),
    ),
    "series_arithmetic": OperatorSpec(
        operator_name="series_arithmetic",
        callable=series_arithmetic,
        params_class=SeriesArithmeticParams,
        config_path=_SERIES_ARITHMETIC_CONFIG_PATH,
        # ``right`` accepts a Series OR a Python scalar (int/float)
        # for binary scalar arithmetic, or is absent for unary ops.
        # ``accepts_scalar=True`` replaces the prior
        # ``accepts_scalar_input=("right",)`` sibling tuple; the
        # arity_validator below enforces the per-``op`` conditional
        # rules.
        input_slots={
            "left": SlotDescriptor.of(
                "Series",
                "Left operand (unary input or binary LHS).",
            ),
            "right": SlotDescriptor.of(
                "Series",
                (
                    "Right operand for binary ops; may be a Series "
                    "(via edge) or a scalar literal (via LiteralBinding); "
                    "must be unbound for unary ops."
                ),
                accepts_scalar=True,
            ),
        },
        output=OutputDescriptor.of(
            "Series",
            "Resulting Series after applying ``params.op``.",
        ),
        # ``op`` is a declared discriminator (OPR15) — the operator
        # resolves it from params; the executor injects nothing by name.
        discriminator_args=("op",),
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
        config_path=_CORRELATION_CONFIG_PATH,
        input_slots={
            "left": SlotDescriptor.of(
                "Series",
                "Left Series in the correlation pair.",
            ),
            "right": SlotDescriptor.of(
                "Series",
                "Right Series in the correlation pair.",
            ),
        },
        output=OutputDescriptor.of(
            "ScalarMetric",
            "Full-sample correlation coefficient between left and right.",
        ),
    ),
    # v2.0 — the single sanctioned unit-conversion operator (ADR 0016
    # Decision 4).  One Series in, one Series out (re-tagged to the
    # target unit).  ``unit_conversion`` method family.
    "convert_units": OperatorSpec(
        operator_name="convert_units",
        callable=convert_units,
        params_class=ConvertUnitsParams,
        config_path=_CONVERT_UNITS_CONFIG_PATH,
        input_slots={
            "series": SlotDescriptor.of(
                "Series",
                "Source Series to convert to ``params.target_units``.",
            ),
        },
        output=OutputDescriptor.of(
            "Series",
            "Series re-expressed in the requested target units.",
        ),
    ),
    "threshold_events": OperatorSpec(
        operator_name="threshold_events",
        callable=threshold_events,
        params_class=ThresholdEventsParams,
        config_path=_THRESHOLD_EVENTS_CONFIG_PATH,
        input_slots={
            "series": SlotDescriptor.of(
                "Series",
                "Source Series whose values are compared against ``params``.",
            ),
        },
        output=OutputDescriptor.of(
            "EventSet",
            "Boolean EventSet marking dates that satisfy the threshold rule.",
        ),
    ),
    "event_windows": OperatorSpec(
        operator_name="event_windows",
        callable=event_windows,
        params_class=EventWindowsParams,
        config_path=_EVENT_WINDOWS_CONFIG_PATH,
        input_slots={
            "events": SlotDescriptor.of(
                "EventSet",
                "Event timestamps that anchor each window.",
            ),
            "target": SlotDescriptor.of(
                "Series",
                "Series to sample around each event timestamp.",
            ),
        },
        output=OutputDescriptor.of(
            "WindowedPanel",
            "Per-event panel with one row per event and one column per offset.",
        ),
    ),
    "conditional_aggregate": OperatorSpec(
        operator_name="conditional_aggregate",
        callable=conditional_aggregate,
        params_class=ConditionalAggregateParams,
        config_path=_CONDITIONAL_AGGREGATE_CONFIG_PATH,
        input_slots={
            "panel": SlotDescriptor.of(
                "WindowedPanel",
                "Per-event panel to reduce column-wise via ``params.aggregator``.",
            ),
        },
        output=OutputDescriptor.of(
            "Series",
            "Per-offset reduction of the panel into a Series indexed by offset.",
        ),
    ),
    "apply_mask": OperatorSpec(
        operator_name="apply_mask",
        callable=apply_mask,
        params_class=ApplyMaskParams,
        config_path=_APPLY_MASK_CONFIG_PATH,
        input_slots={
            "series": SlotDescriptor.of(
                "Series",
                "Series payload to subsample by the boolean mask.",
            ),
            "mask": SlotDescriptor.of(
                "EventSet",
                "Boolean mask whose True dates select Series values to keep.",
            ),
        },
        output=OutputDescriptor.of(
            "Series",
            "Series restricted to the dates where the mask is True.",
        ),
    ),
    "rolling_regression": OperatorSpec(
        operator_name="rolling_regression",
        callable=rolling_regression,
        params_class=RollingRegressionParams,
        config_path=_ROLLING_REGRESSION_CONFIG_PATH,
        # lhs = dependent / target; rhs = single regressor in V1.
        # Output is a SeriesSet keyed by {beta, alpha, r_squared}.
        input_slots={
            "lhs": SlotDescriptor.of(
                "Series",
                "Dependent (target) Series for the rolling OLS fit.",
            ),
            "rhs": SlotDescriptor.of(
                "Series",
                "Single regressor Series (V1: one explanatory variable).",
            ),
        },
        output=OutputDescriptor.of(
            "SeriesSet",
            "SeriesSet keyed by {beta, alpha, r_squared} over the rolling window.",
        ),
    ),
    # v2.0 (ADR 0016) — single_series_transform: trailing-window
    # standardisation of a Series.  One Series in, one Series out
    # (always in Z_SCORE units).  The composition primitive for any
    # "where are we vs our own recent history" question.
    "rolling_zscore": OperatorSpec(
        operator_name="rolling_zscore",
        callable=rolling_zscore,
        params_class=RollingZscoreParams,
        config_path=_ROLLING_ZSCORE_CONFIG_PATH,
        input_slots={
            "series": SlotDescriptor.of(
                "Series",
                "Source Series to standardise over a trailing window.",
            ),
        },
        output=OutputDescriptor.of(
            "Series",
            "Trailing-window z-score (Z_SCORE units).",
        ),
    ),
    # v2.0 (ADR 0016) — single_series_transform: generic windowed
    # reducer (mean / std / min / max / sum) over a Series.  One Series
    # in, one Series out (input unit preserved).  The composition
    # primitive for rolling vol, moving averages, rolling ranges.
    "rolling_statistic": OperatorSpec(
        operator_name="rolling_statistic",
        callable=rolling_statistic,
        params_class=RollingStatisticParams,
        config_path=_ROLLING_STATISTIC_CONFIG_PATH,
        input_slots={
            "series": SlotDescriptor.of(
                "Series",
                "Source Series to reduce over a trailing window.",
            ),
        },
        output=OutputDescriptor.of(
            "Series",
            "Windowed reduction (mean / std / min / max / sum) preserving input units.",
        ),
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
        config_path=_PERCENTILE_RANK_CONFIG_PATH,
        input_slots={
            "series": SlotDescriptor.of(
                "Series",
                "Source Series to rank against its own trailing/expanding history.",
            ),
        },
        output=OutputDescriptor.of(
            "Series",
            "Percentile rank vs history (PCT_RANK units, 0–100).",
        ),
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
        config_path=_ROLLING_CORRELATION_CONFIG_PATH,
        input_slots={
            "left": SlotDescriptor.of(
                "Series",
                "Left Series in the rolling correlation pair.",
            ),
            "right": SlotDescriptor.of(
                "Series",
                "Right Series in the rolling correlation pair.",
            ),
        },
        output=OutputDescriptor.of(
            "Series",
            "Rolling correlation coefficient over each window (RATIO units).",
        ),
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
        config_path=_COINTEGRATION_CONFIG_PATH,
        input_slots={
            "left": SlotDescriptor.of(
                "Series",
                "Left Series in the Engle–Granger cointegration pair.",
            ),
            "right": SlotDescriptor.of(
                "Series",
                "Right Series in the Engle–Granger cointegration pair.",
            ),
        },
        output=OutputDescriptor.of(
            "ScalarMetric",
            "Engle–Granger ADF test statistic (dimensionless, RATIO units).",
        ),
    ),
    "summarize_series": OperatorSpec(
        operator_name="summarize_series",
        callable=summarize_series,
        params_class=SummarizeSeriesParams,
        config_path=_SUMMARIZE_SERIES_CONFIG_PATH,
        # Single-Series input → single-row summary Series at a fixed
        # sentinel date.  Lets two per-regime summaries feed into
        # series_arithmetic.subtract for the canonical "compare across
        # regimes" step.
        input_slots={
            "series": SlotDescriptor.of(
                "Series",
                "Source Series to collapse to a 1-row summary at the sentinel date.",
            ),
        },
        output=OutputDescriptor.of(
            "Series",
            "1-row summary Series at the operator's fixed sentinel timestamp.",
        ),
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
