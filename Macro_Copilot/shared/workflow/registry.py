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
from shared.operators.beta import (
    beta, BetaParams,
    CONFIG_PATH as _BETA_CONFIG_PATH,
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
from shared.operators.covariance import (
    covariance, CovarianceParams,
    CONFIG_PATH as _COVARIANCE_CONFIG_PATH,
)
from shared.operators.convert_units import (
    convert_units, ConvertUnitsParams,
    CONFIG_PATH as _CONVERT_UNITS_CONFIG_PATH,
)
from shared.operators.demean_cross_section import (
    demean_cross_section, DemeanCrossSectionParams,
    CONFIG_PATH as _DEMEAN_CROSS_SECTION_CONFIG_PATH,
)
from shared.operators.detrend import (
    detrend, DetrendParams,
    CONFIG_PATH as _DETREND_CONFIG_PATH,
)
from shared.operators.event_windows import (
    event_windows, EventWindowsParams,
    CONFIG_PATH as _EVENT_WINDOWS_CONFIG_PATH,
)
from shared.operators.ewm_statistic import (
    ewm_statistic, EwmStatisticParams,
    CONFIG_PATH as _EWM_STATISTIC_CONFIG_PATH,
)
from shared.operators.cross_sectional_rank import (
    cross_sectional_rank, CrossSectionalRankParams,
    CONFIG_PATH as _CROSS_SECTIONAL_RANK_CONFIG_PATH,
)
from shared.operators.cross_sectional_statistic import (
    cross_sectional_statistic, CrossSectionalStatisticParams,
    CONFIG_PATH as _CROSS_SECTIONAL_STATISTIC_CONFIG_PATH,
)
from shared.operators.cross_sectional_zscore import (
    cross_sectional_zscore, CrossSectionalZscoreParams,
    CONFIG_PATH as _CROSS_SECTIONAL_ZSCORE_CONFIG_PATH,
)
from shared.operators.granger_causality import (
    granger_causality, GrangerCausalityParams,
    CONFIG_PATH as _GRANGER_CAUSALITY_CONFIG_PATH,
)
from shared.operators.cumulative import (
    cumulative, CumulativeParams,
    CONFIG_PATH as _CUMULATIVE_CONFIG_PATH,
)
from shared.operators.hp_filter import (
    hp_filter, HpFilterParams,
    CONFIG_PATH as _HP_FILTER_CONFIG_PATH,
)
from shared.operators.lag import (
    lag, LagParams,
    CONFIG_PATH as _LAG_CONFIG_PATH,
)
from shared.operators.lead_lag import (
    lead_lag, LeadLagParams,
    CONFIG_PATH as _LEAD_LAG_CONFIG_PATH,
)
from shared.operators.resample import (
    resample, ResampleParams,
    CONFIG_PATH as _RESAMPLE_CONFIG_PATH,
)
from shared.operators.rolling_regression import (
    rolling_regression,
    RollingRegressionParams,
    CONFIG_PATH as _ROLLING_REGRESSION_CONFIG_PATH,
)
from shared.operators.pairwise_spread_matrix import (
    pairwise_spread_matrix, PairwiseSpreadMatrixParams,
    CONFIG_PATH as _PAIRWISE_SPREAD_MATRIX_CONFIG_PATH,
)
from shared.operators.percentile_rank import (
    percentile_rank,
    PercentileRankParams,
    CONFIG_PATH as _PERCENTILE_RANK_CONFIG_PATH,
)
from shared.operators.regression_residual import (
    regression_residual,
    RegressionResidualParams,
    CONFIG_PATH as _REGRESSION_RESIDUAL_CONFIG_PATH,
)
from shared.operators.rolling_correlation import (
    rolling_correlation,
    RollingCorrelationParams,
    CONFIG_PATH as _ROLLING_CORRELATION_CONFIG_PATH,
)
from shared.operators.rolling_covariance import (
    rolling_covariance,
    RollingCovarianceParams,
    CONFIG_PATH as _ROLLING_COVARIANCE_CONFIG_PATH,
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
from shared.operators.top_n import (
    top_n, TopNParams,
    CONFIG_PATH as _TOP_N_CONFIG_PATH,
)
from shared.operators.winsorize import (
    winsorize, WinsorizeParams,
    CONFIG_PATH as _WINSORIZE_CONFIG_PATH,
)
from shared.operators.weighted_combination import (
    weighted_combination, WeightedCombinationParams,
    CONFIG_PATH as _WEIGHTED_COMBINATION_CONFIG_PATH,
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
    param_sanity_validator :
        Optional per-operator STATIC param-sanity hook (plan D4).
        Called by ``validate_workflow_result`` with the node's
        ``params`` dict; returns ``None`` (OK) or an error message.
        Catches param-internal degeneracies the type system is
        blind to (e.g. ``min_periods > window``).  Pure function,
        no external data — registration-clean.  Data-dependent
        failures (window >= available rows) are NOT checked here;
        they surface at execute-time and route to the
        self-correction loop.
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
    # Orchestration-upgrade plan Decision D4: optional per-operator STATIC
    # param-sanity hook.  Called by ``validate_workflow_result`` with the
    # node's ``params`` dict; returns ``None`` (OK) or an error message.
    # Catches purely param-internal degeneracies the type system is blind
    # to (e.g. ``min_periods > window``).  Pure function, no external data —
    # so it is registration-clean (adding the Nth operator's check is a
    # one-line hook).  DATA-DEPENDENT failures (window >= available rows)
    # are NOT checked here — they are caught at execute-time as the
    # operator's own typed error and routed into the self-correction loop.
    param_sanity_validator: Optional[
        Callable[[Dict[str, Any]], Optional[str]]
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


def _covariance_unit_validator(
    node_params: Dict[str, Any],
    source_units: Dict[str, Optional[str]],
) -> Optional[str]:
    """Validate-time mirror of covariance's runtime same-units rule
    (OPR11).  A covariance is unit-BEARING (its semantic unit is the
    product of its inputs' units), so the operator requires matching
    units by default; this hook surfaces a declared-unit mismatch as
    ``E_UNIT_MISMATCH`` at ``validate_workflow`` time instead of an
    execute-time failure.

    Best-effort discipline (same as the series_arithmetic hook): when
    either source's units are unknown (``None``) the substrate skips
    the check and the operator's runtime refusal stays authoritative.
    An explicit ``require_matching_units: false`` in the node's params
    opts out, exactly as it does at runtime.
    """
    require_matching = node_params.get("require_matching_units", True)
    if not require_matching:
        return None
    left = source_units.get("left")
    right = source_units.get("right")
    if left is not None and right is not None and left != right:
        return (
            f"covariance requires matching units across left + right by "
            f"default (its output's semantic unit is the product of the "
            f"inputs' units), but the substrate detected "
            f"left.units={left!r} vs right.units={right!r} from declared "
            "upstream sources.  Insert convert_units on the offending "
            "arm, or set require_matching_units: false in the node's "
            "params to opt into a mixed-unit covariance explicitly."
        )
    return None


# ============================================================================
# PARAM-SANITY HOOKS (orchestration-upgrade plan D4)
# ============================================================================
#
# Per-operator STATIC param-sanity predicates.  Pure functions over the
# node's raw ``params`` dict; return ``None`` (OK) or an error message.
# They catch param-internal degeneracies the type system + Pydantic
# field bounds don't (Pydantic enforces single-field ``ge=``/``le=`` but
# NOT cross-field relationships).  Registration-clean: attach via the
# ``param_sanity_validator`` field on the relevant OperatorSpec(s).


def _rolling_min_periods_param_sanity(
    node_params: Dict[str, Any],
) -> Optional[str]:
    """Shared by the rolling operators (rolling_zscore / rolling_statistic
    / rolling_correlation / rolling_covariance / rolling_regression),
    all of which carry ``window: int`` and ``min_periods: Optional[int]``.

    The Pydantic schemas enforce ``window >= 2`` and ``min_periods >= 1``
    individually, but NOT the cross-field invariant ``min_periods <=
    window``.  A min_periods larger than the window can never accumulate
    enough observations in any trailing window → the operator emits
    all-NaN.  We catch that STATICALLY (both values present in params)
    here; the DATA-DEPENDENT case (window >= available rows) is not
    statically knowable and surfaces as the operator's own typed
    execute-time error, which routes to the self-correction loop.
    """
    window = node_params.get("window")
    min_periods = node_params.get("min_periods")
    # Only check when BOTH are explicit ints.  Absent/None min_periods
    # defaults to ``window`` (so the invariant holds); absent window
    # uses the operator's safe schema default.
    if not isinstance(window, int) or not isinstance(min_periods, int):
        return None
    if min_periods > window:
        return (
            f"min_periods={min_periods} exceeds window={window}; a rolling "
            "computation whose min_periods is larger than its window can "
            "never accumulate enough observations and would emit all-NaN.  "
            "Set min_periods <= window (or leave it null to default to "
            "window)."
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
                (
                    "List-shaped fan-in slot for N >= 2 typed Series that "
                    "must end up on a single shared DatetimeIndex.  Every "
                    "inbound edge targeting this slot contributes one "
                    "Series; the operator reindexes them all under "
                    "params.join_policy (inner = only common dates, outer "
                    "= union with NaNs).  USE this slot whenever "
                    "downstream operators require index identity across "
                    "the Series (correlation, rolling_correlation, "
                    "cointegration, rolling_regression, two-Series "
                    "series_arithmetic) or when upstream primitives draw "
                    "from different calendars.  DO NOT use for a single "
                    "Series (no alignment needed; pass it directly to the "
                    "downstream operator), and DO NOT use for "
                    "SeriesSets / EventSets / Panels.  Different-but-"
                    "convertible units (PERCENT vs BPS) must be reconciled "
                    "by inserting convert_units on the offending branch "
                    "FIRST — this operator does not convert units, only "
                    "indexes.  Acceptable inputs: any primitive Series "
                    "output, any operator Series output, or a "
                    "select_from_series_set extraction.  Use "
                    "params.output_keys to rename the SeriesSet's per-key "
                    "dict entries so downstream selects can be template-"
                    "controlled instead of tied to each primitive's wire-"
                    "naming convention."
                ),
            ),
        },
        output=OutputDescriptor.of(
            "SeriesSet",
            (
                "A typed SeriesSet whose series_by_key maps each input "
                "Series's identifier (or the matching params.output_keys "
                "entry when supplied) to that Series reindexed onto the "
                "chosen common DatetimeIndex.  Per-key units / frequency "
                "/ missingness mirror the corresponding input 1:1.  "
                "Almost always followed by select_from_series_set x N to "
                "extract each Series back out by key for downstream "
                "single-Series or pair-stats operators."
            ),
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
                (
                    "Single-artifact slot expecting a typed SeriesSet "
                    "whose series_by_key mapping contains the key named "
                    "by params.series_key.  Canonical upstream sources: "
                    "align_series (SeriesSet keyed by each input Series's "
                    "identifier or params.output_keys when supplied) and "
                    "rolling_regression (SeriesSet keyed by {'beta', "
                    "'alpha', 'r_squared'}).  USE this slot whenever the "
                    "next operator expects a single Series — pair-stats "
                    "operators (correlation, rolling_correlation, "
                    "cointegration, rolling_regression, two-Series "
                    "series_arithmetic) need TWO sibling select nodes; "
                    "single-Series transforms (rolling_zscore, "
                    "percentile_rank, rolling_statistic, threshold_events, "
                    "summarize_series) need ONE.  DO NOT use this slot to "
                    "construct a new SeriesSet (construction is "
                    "align_series's job) and DO NOT wire it on raw Series "
                    "(the slot rejects Series inputs at type-check)."
                ),
            ),
        },
        output=OutputDescriptor.of(
            "Series",
            (
                "The single typed Series stored under params.series_key "
                "in the input SeriesSet, with its units / frequency / "
                "missingness_policy preserved 1:1 and its lineage "
                "carrying the full upstream chain plus this select step "
                "appended.  Drop-in input for any Series-consuming "
                "downstream operator."
            ),
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
                (
                    "The single typed Series that is the input to a unary "
                    "op (diff, pct_change) OR the LHS of a binary op "
                    "(add, subtract, multiply, divide).  Always artifact-"
                    "only — no scalar literal here; the operator's frame "
                    "of reference is anchored on this Series, and its "
                    "units / frequency / missingness flow through to the "
                    "output for add / subtract / diff / scalar-multiply / "
                    "scalar-divide.  USE this slot for every "
                    "series_arithmetic call regardless of op.  For binary "
                    "Series-Series ops, this slot AND 'right' MUST share "
                    "an identical DatetimeIndex — wire align_series -> "
                    "select_from_series_set upstream on both arms.  DO "
                    "NOT use for SeriesSet / EventSet / Panel inputs "
                    "(extract a Series first with select_from_series_set)."
                ),
            ),
            "right": SlotDescriptor.of(
                "Series",
                (
                    "RHS for binary ops (add, subtract, multiply, divide).  "
                    "The ONLY operator slot in the registry that accepts "
                    "BOTH an artifact edge AND a scalar literal via "
                    "LiteralBinding (accepts_scalar=True): pass a Series "
                    "edge for element-wise A op B, or pass a scalar "
                    "(int/float) via LiteralBinding(slot='right', "
                    "value=...) for A op scalar (the only multiply / "
                    "divide-by-scalar path).  MUST be UNBOUND for unary "
                    "ops (diff, pct_change); the per-op arity_validator "
                    "refuses a bound 'right' under unary and an unbound "
                    "'right' under binary.  When wired as a Series, the "
                    "unit_validator enforces matching units across 'left' "
                    "+ 'right' for add / subtract / Series-Series divide; "
                    "insert convert_units on the offending arm to "
                    "reconcile PERCENT vs BPS mismatches before this "
                    "slot.  DO NOT pass both an edge AND a "
                    "LiteralBinding for the same call (the substrate "
                    "refuses double-binding)."
                ),
                accepts_scalar=True,
            ),
        },
        output=OutputDescriptor.of(
            "Series",
            (
                "The Series produced by applying params.op element-wise.  "
                "Unit algebra: add / subtract / diff preserve left units; "
                "pct_change and Series-Series divide emit RATIO; scalar "
                "multiply / divide preserve left units.  Index matches "
                "the (necessarily identical) input index for binary "
                "Series-Series ops and the left index for everything else."
            ),
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
                (
                    "One arm of the correlation pair.  Single-artifact "
                    "slot (no scalar literal) expecting a typed Series "
                    "that shares an IDENTICAL DatetimeIndex with 'right' "
                    "— this operator does NOT align internally; canonical "
                    "upstream is align_series -> select_from_series_set "
                    "on both arms.  USE this slot whenever the user asks "
                    "for a single full-sample correlation number between "
                    "two series.  The operator is COMMUTATIVE — "
                    "corr(left, right) == corr(right, left) — so the "
                    "choice of which Series to wire here vs 'right' is "
                    "cosmetic, though BOTH inputs' units are recorded in "
                    "lineage.  DO NOT use this slot if the user wants a "
                    "TIME-VARYING correlation (use rolling_correlation); "
                    "DO NOT use it if the user wants the slope / beta of "
                    "one series on another (use rolling_regression); DO "
                    "NOT use it for stationarity of a linear combination "
                    "(use cointegration).  The slot is unit-INVARIANT — "
                    "correlation is dimensionless — so PERCENT, BPS, "
                    "RATIO, Z_SCORE inputs are all accepted; no "
                    "convert_units coercion is required."
                ),
            ),
            "right": SlotDescriptor.of(
                "Series",
                (
                    "The other arm of the correlation pair.  Symmetric "
                    "counterpart to 'left' — same artifact type, same "
                    "DatetimeIndex requirement (align upstream), same "
                    "unit-invariance, same strict-by-default discipline.  "
                    "USE for the second Series in the pair.  DO NOT bind "
                    "both 'left' and 'right' to the same upstream Series "
                    "(degenerate — correlation of a series with itself "
                    "is identically 1) and DO NOT pass a scalar literal "
                    "here (artifact-only slot)."
                ),
            ),
        },
        output=OutputDescriptor.of(
            "ScalarMetric",
            (
                "The full-sample correlation coefficient (a single "
                "dimensionless number in [-1, 1]) wrapped as a typed "
                "ScalarMetric in RATIO units.  Lineage records the chosen "
                "method (pearson / spearman / kendall), the overlap "
                "n_observations, and both inputs' units.  Feeds the "
                "terminal answer directly.  Raises CorrelationError when "
                "the overlap is below params.min_periods or either input "
                "has zero variance over the overlap (undefined coefficient "
                "is a typed refusal, never a non-finite value)."
            ),
        ),
    ),
    # Track-A (fable_build) — statistical_relationship: full-sample
    # covariance between two index-aligned Series.  Two Series in, one
    # ScalarMetric out.  The unit-BEARING sibling of ``correlation``:
    # same units required by default (a covariance's unit is the
    # product of its inputs' units); output tagged RATIO with both
    # input units recorded in lineage (rolling_regression-beta /
    # cointegration precedent).
    "covariance": OperatorSpec(
        operator_name="covariance",
        callable=covariance,
        params_class=CovarianceParams,
        config_path=_COVARIANCE_CONFIG_PATH,
        unit_validator=_covariance_unit_validator,
        input_slots={
            "left": SlotDescriptor.of(
                "Series",
                (
                    "One arm of the covariance pair.  Single-artifact "
                    "slot (no scalar literal) expecting a typed Series "
                    "that shares an IDENTICAL DatetimeIndex with 'right' "
                    "— this operator does NOT align internally; canonical "
                    "upstream is align_series -> select_from_series_set "
                    "on both arms.  USE when the user asks how much two "
                    "series move together in LEVEL terms (the raw, "
                    "unit-bearing co-movement number).  COMMUTATIVE — "
                    "the choice of arm is cosmetic.  Unlike correlation "
                    "this operator is unit-BEARING: both Series must "
                    "share the SAME units by default (insert "
                    "convert_units upstream to reconcile PERCENT vs "
                    "BPS).  DO NOT use for the normalised [-1, 1] "
                    "strength (use correlation), a time-varying "
                    "co-movement (use rolling_correlation), or the slope "
                    "of one series on another (use rolling_regression)."
                ),
            ),
            "right": SlotDescriptor.of(
                "Series",
                (
                    "The other arm of the covariance pair.  Symmetric "
                    "counterpart to 'left' — same artifact type, same "
                    "DatetimeIndex requirement (align upstream), same "
                    "same-units-by-default discipline (convert_units "
                    "upstream to reconcile).  DO NOT bind both arms to "
                    "the same upstream Series (that is just the variance "
                    "of the series) and DO NOT pass a scalar literal "
                    "here (artifact-only slot)."
                ),
            ),
        },
        output=OutputDescriptor.of(
            "ScalarMetric",
            (
                "The full-sample covariance (a single number) wrapped as "
                "a typed ScalarMetric.  Tagged RATIO because the closed "
                "unit enum has no product unit — the TRUE dimension "
                "(left_units × right_units) is recorded in lineage, with "
                "ddof and the overlap n_obs.  Feeds the terminal answer "
                "directly.  Raises CovarianceError below min_periods or "
                "on a strict-mode units mismatch; a constant input is "
                "NOT an error — its covariance is a legitimate 0.0."
            ),
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
                (
                    "Single-artifact slot expecting a typed Series with a "
                    "known source unit (typically declared on the upstream "
                    "PrimitiveSpec.output_field_units, or carried by an "
                    "operator's documented output unit algebra).  USE "
                    "when two Series with different-but-convertible units "
                    "(v1: PERCENT <-> BPS, plus identity) need to be "
                    "combined by series_arithmetic add / subtract / "
                    "divide (which the unit_validator otherwise refuses), "
                    "OR when a bound primitive's output unit disagrees "
                    "with the downstream operator's expected unit.  This "
                    "is the SINGLE sanctioned unit-conversion site in "
                    "the substrate (ADR 0016 Decision 4) — "
                    "series_arithmetic refuses to silently coerce.  The "
                    "L4 repair loop most commonly inserts this operator "
                    "as an additive adapter when Boundary A surfaces a "
                    "unit mismatch.  DO NOT use for dimensionless units "
                    "(Z_SCORE, RATIO, PCT_RANK, COUNT) — those cannot be "
                    "dimensionally converted and the operator refuses "
                    "with NotImplementedError (OPR8)."
                ),
            ),
        },
        output=OutputDescriptor.of(
            "Series",
            (
                "The input Series with its payload multiplied by the "
                "exact dimensional factor for (source_units -> "
                "params.target_units) and re-tagged.  Identity "
                "conversion (source == target) is a no-op re-tag.  "
                "Lineage, frequency, missingness propagate unchanged.  "
                "Raises ConvertUnitsError when a conversion overflows."
            ),
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
                (
                    "Source Series whose per-date values are compared "
                    "against a closed-family rule in params (mode in "
                    "{above, below, between, outside, crosses_above, "
                    "crosses_below, percentile_above, percentile_below}) "
                    "to produce a boolean date-index mask.  USE when you "
                    "need to mark dates where a quantity satisfies an "
                    "economic condition (e.g. days when realized vol > "
                    "20%, or when a z-score crossed 2σ from below) so a "
                    "downstream operator can subset another series by "
                    "those dates (apply_mask) or aggregate around them "
                    "(event_windows + conditional_aggregate).  DO NOT use "
                    "for arithmetic transforms (use series_arithmetic) or "
                    "for ranking (use percentile_rank — it emits a "
                    "continuous Series, not a boolean EventSet)."
                ),
            ),
        },
        output=OutputDescriptor.of(
            "EventSet",
            (
                "Boolean EventSet aligned to the input Series's index, "
                "True on dates that satisfy the threshold rule and False "
                "elsewhere.  Drop-in input for apply_mask (subset another "
                "Series by True dates), event_windows (anchor windows on "
                "True dates), or as a leg of an event-study DAG."
            ),
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
                (
                    "Boolean EventSet whose True dates anchor each "
                    "window.  Upstream is most often threshold_events "
                    "(e.g. dates a z-score crossed 2σ) or a primitive "
                    "EventSet output (NFP surprises, FOMC meetings).  "
                    "USE when the user asks 'how does X behave around Y' "
                    "— each True date in `events` becomes the t=0 anchor "
                    "of one row of the output panel.  DO NOT use for "
                    "continuous-window operations (use rolling_statistic) "
                    "or for masking (use apply_mask); event_windows is "
                    "specifically for sampling a target Series at "
                    "discrete event anchors."
                ),
            ),
            "target": SlotDescriptor.of(
                "Series",
                (
                    "The Series to be sampled at params.offsets around "
                    "each event anchor.  Must share the input EventSet's "
                    "calendar (insert align_series upstream if not).  "
                    "USE the underlying quantity whose response to the "
                    "events the user wants to study (yields, spreads, "
                    "vol — any Series).  DO NOT use a SeriesSet here "
                    "(extract with select_from_series_set first); DO NOT "
                    "wire the same Series to both 'events' and 'target' "
                    "(that's a degenerate self-windowing — use "
                    "rolling_statistic instead)."
                ),
            ),
        },
        output=OutputDescriptor.of(
            "WindowedPanel",
            (
                "Per-event panel with one row per True date in `events` "
                "and one column per offset in params.offsets.  Cell [e, "
                "k] is the target Series value at (event_date_e + "
                "offset_k) trading days.  Drop-in input for "
                "conditional_aggregate (column-wise reduce into a Series "
                "of per-offset summaries) — the canonical 'event study' "
                "two-step."
            ),
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
                (
                    "Per-event WindowedPanel (one row per event anchor, "
                    "one column per offset).  Canonical upstream is "
                    "event_windows.  USE for the second step of an "
                    "event-study DAG: collapse the per-event rows into a "
                    "single per-offset Series via params.aggregator (mean, "
                    "median, p25, p75, etc.) — the resulting Series is "
                    "the average / median / quantile response trajectory "
                    "around the events.  DO NOT use this slot for a "
                    "regular Series or SeriesSet (the panel's two-dim "
                    "shape is load-bearing) and DO NOT use it for "
                    "per-event reduction (the operator reduces ACROSS "
                    "events for each offset — not within an event)."
                ),
            ),
        },
        output=OutputDescriptor.of(
            "Series",
            (
                "Per-offset Series indexed by the panel's offset axis "
                "(values from t-K to t+K), where each value is the "
                "cross-event aggregator (mean / median / quantile) of "
                "that column.  Units inherit from the original target "
                "Series.  The canonical 'average response curve' output."
            ),
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
                (
                    "Source Series to subsample.  USE when the user "
                    "wants to look at a quantity ONLY during a specific "
                    "regime (e.g. 'yields when fed funds was hiking', "
                    "'spreads when vol > 20%').  Wire the mask Series's "
                    "boolean condition into the 'mask' slot and the "
                    "Series-of-interest here.  Index must match the "
                    "mask's index — insert align_series upstream if not.  "
                    "DO NOT use for arithmetic transforms (use "
                    "series_arithmetic) and DO NOT use for windowed "
                    "operations (use rolling_statistic) — apply_mask is "
                    "specifically for regime / event-conditioned "
                    "subsetting."
                ),
            ),
            "mask": SlotDescriptor.of(
                "EventSet",
                (
                    "Boolean EventSet (typically from threshold_events) "
                    "aligned to the input Series's index.  True dates "
                    "are KEPT; False dates are dropped from the output.  "
                    "USE the same EventSet you'd anchor event_windows "
                    "on, OR a regime-mask EventSet from threshold_events "
                    "(e.g. 'days where fed_funds_rate is rising').  DO "
                    "NOT pass a continuous Series here (the operator "
                    "refuses non-boolean inputs)."
                ),
            ),
        },
        output=OutputDescriptor.of(
            "Series",
            (
                "Series restricted to the True-dates of `mask`, "
                "preserving units / frequency.  Index is a strict subset "
                "of the input Series's index.  Drop-in input for "
                "summarize_series, percentile_rank, or further "
                "subsetting."
            ),
        ),
    ),
    # Track-A (fable_build) — cross_sectional: per-date rank of every
    # member of an aligned SeriesSet among the cross-section.  One
    # SeriesSet in, one SeriesSet out (same keys; rank payloads; COUNT
    # units for ordinal, PCT_RANK for normalized).  Same-units-across-
    # members required outright (ranking compares values).
    "cross_sectional_rank": OperatorSpec(
        operator_name="cross_sectional_rank",
        callable=cross_sectional_rank,
        params_class=CrossSectionalRankParams,
        config_path=_CROSS_SECTIONAL_RANK_CONFIG_PATH,
        input_slots={
            "series_set": SlotDescriptor.of(
                "SeriesSet",
                (
                    "An aligned SeriesSet of N >= 2 members sharing ONE "
                    "unit — canonical upstream is align_series over the "
                    "universe's Series (insert convert_units on "
                    "offending members first; mixed-unit ranking is "
                    "refused outright, no opt-out).  USE for the "
                    "morning-screen / 'ranked across the universe' "
                    "step: at each date every member is ranked among "
                    "the cross-section (a member NaN at a date gets a "
                    "NaN rank; dates with fewer than params.min_members "
                    "non-NaN members emit NaN for all).  DO NOT use to "
                    "rank ONE series against its own history (use "
                    "percentile_rank), to keep only the top members "
                    "per date (use top_n), or for a per-date "
                    "cross-member summary number (use "
                    "cross_sectional_statistic)."
                ),
            ),
        },
        output=OutputDescriptor.of(
            "SeriesSet",
            (
                "A SeriesSet with the SAME keys and common index; each "
                "member's payload is its per-date rank among the "
                "non-NaN members — ordinal positions 1..N in COUNT "
                "units (default) or normalized 0–100 percentiles in "
                "PCT_RANK units (params.rank_method).  Missingness is "
                "fresh (ranks are fresh derivations); frequency passes "
                "through; the set-level lineage extends the input's "
                "chain.  Typical follow-on: select_from_series_set to "
                "extract one member's rank path.  Raises "
                "CrossSectionalRankError on <2 members, mixed units, "
                "or an all-NaN output."
            ),
        ),
    ),
    # Track-A (fable_build) — cross_sectional: per-date demeaning of an
    # aligned SeriesSet (member − cross-mean; the "vs peers" relative
    # series).  One SeriesSet in, one SeriesSet out (units passthrough).
    # Same-units-across-members required outright.
    "demean_cross_section": OperatorSpec(
        operator_name="demean_cross_section",
        callable=demean_cross_section,
        params_class=DemeanCrossSectionParams,
        config_path=_DEMEAN_CROSS_SECTION_CONFIG_PATH,
        input_slots={
            "series_set": SlotDescriptor.of(
                "SeriesSet",
                (
                    "An aligned SeriesSet of N >= 2 members sharing ONE "
                    "unit — canonical upstream is align_series (insert "
                    "convert_units on offending members first; "
                    "mixed-unit demeaning is refused outright, no "
                    "opt-out).  USE when the user asks for each member "
                    "RELATIVE TO THE UNIVERSE AVERAGE per date ('vs "
                    "peers') while KEEPING the input's units — the "
                    "level-preserving alternative to "
                    "cross_sectional_zscore.  A member NaN at a date "
                    "stays NaN; dates with fewer than "
                    "params.min_members non-NaN members emit NaN for "
                    "all; an all-EQUAL date demeans to a legitimate "
                    "0.0 row.  DO NOT use for sigma-scaled deviations "
                    "(use cross_sectional_zscore), peer positions (use "
                    "cross_sectional_rank), or the average itself (use "
                    "cross_sectional_statistic)."
                ),
            ),
        },
        output=OutputDescriptor.of(
            "SeriesSet",
            (
                "A SeriesSet with the SAME keys and common index; each "
                "member's payload is member − cross-sectional mean per "
                "date, in the members' common unit (passthrough — BPS "
                "in, BPS out).  Missingness is fresh; frequency passes "
                "through; the set-level lineage extends the input's "
                "chain.  Typical follow-ons: cross_sectional_rank, "
                "select_from_series_set, threshold_events on an "
                "extracted member.  Raises DemeanCrossSectionError on "
                "<2 members, mixed units, overflow, or an all-NaN "
                "output."
            ),
        ),
    ),
    # Track-A (fable_build) — cross_sectional: per-date summary of an
    # aligned SeriesSet's cross-section (mean/median/std/min/max/sum).
    # One SeriesSet in, one Series out (units passthrough; std is the
    # dispersion gauge).  Same-units-across-members required outright.
    "cross_sectional_statistic": OperatorSpec(
        operator_name="cross_sectional_statistic",
        callable=cross_sectional_statistic,
        params_class=CrossSectionalStatisticParams,
        config_path=_CROSS_SECTIONAL_STATISTIC_CONFIG_PATH,
        input_slots={
            "series_set": SlotDescriptor.of(
                "SeriesSet",
                (
                    "An aligned SeriesSet of N >= 2 members sharing ONE "
                    "unit — canonical upstream is align_series (insert "
                    "convert_units on offending members first; "
                    "mixed-unit summaries are refused outright, no "
                    "opt-out).  USE when the user asks for ONE number "
                    "per date across the universe: the average/median "
                    "level (statistic=mean/median), the cross-sectional "
                    "DISPERSION gauge (statistic=std — 'how spread out "
                    "is the universe today'), the per-date extremes "
                    "(min/max), or the total (sum).  Dates with fewer "
                    "than params.min_members non-NaN members emit NaN.  "
                    "DO NOT use for per-member positions vs peers (use "
                    "cross_sectional_rank / cross_sectional_zscore), a "
                    "rolling summary of ONE series (use "
                    "rolling_statistic), or a full-sample scalar of one "
                    "series (use summarize_series)."
                ),
            ),
        },
        output=OutputDescriptor.of(
            "Series",
            (
                "One value per date — the chosen statistic across the "
                "non-NaN members — in the members' common unit "
                "(passthrough for every statistic; std of BPS members "
                "is BPS).  Missingness is fresh; frequency passes "
                "through; lineage extends the input set's chain.  "
                "Drop-in input for rolling_zscore (dispersion regime), "
                "threshold_events (alerts), series_arithmetic, or "
                "direct surface.  Raises CrossSectionalStatisticError "
                "on <2 members, mixed units, overflow, or an all-NaN "
                "output."
            ),
        ),
    ),
    # Track-A (fable_build) — cross_sectional: per-date z-score of
    # every member of an aligned SeriesSet against the cross-section.
    # One SeriesSet in, one SeriesSet out (same keys; Z_SCORE units).
    # Same-units-across-members required outright.
    "cross_sectional_zscore": OperatorSpec(
        operator_name="cross_sectional_zscore",
        callable=cross_sectional_zscore,
        params_class=CrossSectionalZscoreParams,
        config_path=_CROSS_SECTIONAL_ZSCORE_CONFIG_PATH,
        input_slots={
            "series_set": SlotDescriptor.of(
                "SeriesSet",
                (
                    "An aligned SeriesSet of N >= 2 members sharing ONE "
                    "unit — canonical upstream is align_series (insert "
                    "convert_units on offending members first; "
                    "mixed-unit standardisation is refused outright, no "
                    "opt-out).  USE when the user asks how far each "
                    "member of a universe sits FROM ITS PEERS at each "
                    "date: z = (member − cross-mean) / cross-std over "
                    "the non-NaN members.  A member NaN at a date gets "
                    "a NaN z; dates with fewer than params.min_members "
                    "non-NaN members OR zero cross-sectional dispersion "
                    "emit NaN for all.  DO NOT use to standardise ONE "
                    "series against its own history (use "
                    "rolling_zscore) or for per-date peer positions / "
                    "percentiles (use cross_sectional_rank)."
                ),
            ),
        },
        output=OutputDescriptor.of(
            "SeriesSet",
            (
                "A SeriesSet with the SAME keys and common index; each "
                "member's payload is its per-date z-score vs the "
                "cross-section, in Z_SCORE units (dimensionless).  "
                "Missingness is fresh (z-scores are fresh derivations); "
                "frequency passes through; the set-level lineage "
                "extends the input's chain.  Typical follow-ons: "
                "cross_sectional_rank (rank the z-scores), "
                "select_from_series_set (extract one member's "
                "peer-relative path).  Raises CrossSectionalZscoreError "
                "on <2 members, mixed units, or an all-NaN output."
            ),
        ),
    ),
    # Track-A (fable_build) — statistical_relationship: the Granger
    # F-test (do left's lags improve an OLS of right on its own lags).
    # Two Series in, one ScalarMetric out (the F statistic; p-value +
    # dfs in lineage).  Descriptive historical test — no forecast, no
    # causal claim.  NOT commutative.
    "granger_causality": OperatorSpec(
        operator_name="granger_causality",
        callable=granger_causality,
        params_class=GrangerCausalityParams,
        config_path=_GRANGER_CAUSALITY_CONFIG_PATH,
        input_slots={
            "left": SlotDescriptor.of(
                "Series",
                (
                    "The candidate DRIVER of the pair — the test asks "
                    "whether THIS series' lagged values improve an OLS "
                    "of 'right' on right's own lags.  NOT commutative "
                    "(swapping tests the reverse direction).  Must share "
                    "an IDENTICAL DatetimeIndex with 'right' "
                    "(align_series -> select_from_series_set upstream on "
                    "both arms).  USE when the user asks whether one "
                    "series' HISTORY adds explanatory power for another "
                    "— a single hypothesis-test number, descriptive "
                    "only (never a forecast or causal proof).  "
                    "Unit-invariant (the F statistic is dimensionless).  "
                    "DO NOT use for the per-lag correlation profile "
                    "(use lead_lag), contemporaneous strength (use "
                    "correlation), or level-relationship stationarity "
                    "(use cointegration)."
                ),
            ),
            "right": SlotDescriptor.of(
                "Series",
                (
                    "The RESPONSE series being explained.  Same artifact "
                    "type, same DatetimeIndex requirement (align "
                    "upstream).  DO NOT bind both arms to the same "
                    "upstream Series (the unrestricted fit is perfectly "
                    "collinear and the operator refuses) and DO NOT "
                    "pass a scalar literal here (artifact-only slot)."
                ),
            ),
        },
        output=OutputDescriptor.of(
            "ScalarMetric",
            (
                "The Granger F statistic (dimensionless, RATIO units).  "
                "Larger F = stronger evidence that left's history adds "
                "explanatory power for right.  Lineage records the "
                "p-value, df1 = n_lags, df2 = n − 2·n_lags − 1, the "
                "complete-case n_obs, the direction tested, and both "
                "inputs' units.  The substrate does NOT auto-translate "
                "the statistic to a yes/no — the answer layer "
                "interprets it.  Raises GrangerCausalityError on too "
                "few complete-case rows for the lag order, a "
                "zero-variance arm, an ill-conditioned design, or a "
                "perfect unrestricted fit (unbounded F)."
            ),
        ),
    ),
    # Track-A (fable_build) — statistical_relationship: the
    # cross-correlation function corr(left_t, right_{t+k}) for
    # k ∈ [−max_lag, +max_lag].  Two Series in, one Series out on the
    # substrate's synthetic offset-anchor index (the
    # conditional_aggregate precedent; integer lags recorded in
    # lineage).  Sign convention: positive k = left LEADS right.
    "lead_lag": OperatorSpec(
        operator_name="lead_lag",
        callable=lead_lag,
        params_class=LeadLagParams,
        config_path=_LEAD_LAG_CONFIG_PATH,
        input_slots={
            "left": SlotDescriptor.of(
                "Series",
                (
                    "The candidate LEADER of the pair — positive output "
                    "lags mean THIS series leads 'right' by k rows.  "
                    "Single-artifact slot expecting a typed Series that "
                    "shares an IDENTICAL DatetimeIndex with 'right' "
                    "(align_series -> select_from_series_set upstream on "
                    "both arms).  USE when the user asks whether one "
                    "series moves BEFORE another, or at which lag the "
                    "relationship peaks.  Unit-invariant (correlation is "
                    "dimensionless; PERCENT vs BPS is fine).  Swapping "
                    "the arms mirrors the profile (lag k ↔ lag −k) — "
                    "documented, not an error.  DO NOT use for the "
                    "contemporaneous correlation number (use "
                    "correlation), the correlation's evolution over time "
                    "(use rolling_correlation), or causal claims (this "
                    "is descriptive)."
                ),
            ),
            "right": SlotDescriptor.of(
                "Series",
                (
                    "The candidate FOLLOWER of the pair.  Same artifact "
                    "type, same DatetimeIndex requirement (align "
                    "upstream), same unit-invariance.  DO NOT bind both "
                    "arms to the same upstream Series (the profile is "
                    "the series' autocorrelation — ask for that "
                    "explicitly if wanted) and DO NOT pass a scalar "
                    "literal here (artifact-only slot)."
                ),
            ),
        },
        output=OutputDescriptor.of(
            "Series",
            (
                "The CCF profile: one correlation (RATIO units, in "
                "[-1, 1]) per integer lag k ∈ [−max_lag, +max_lag], "
                "encoded on the substrate's synthetic offset-anchor "
                "DatetimeIndex (lag k → 1970-01-01 + k days; "
                "frequency=None).  The integer lags, sign convention "
                "(positive k = left leads), per-lag overlap counts and "
                "both inputs' units ride in lineage params.  A lag with "
                "insufficient overlap or zero variance is NaN; an "
                "all-NaN profile raises LeadLagError.  Typically the "
                "terminal artifact, or reduced via summarize_series."
            ),
        ),
    ),
    # Track-A (fable_build) — statistical_relationship: full-sample OLS
    # slope of lhs on rhs.  Two Series in, one ScalarMetric out (the
    # single sensitivity number).  NOT commutative.  Unit-invariant
    # across inputs (β absorbs the regressor's units); output tagged
    # RATIO with the true dimension (lhs_units/rhs_units) + fitted
    # α/R²/n_obs recorded in lineage.
    "beta": OperatorSpec(
        operator_name="beta",
        callable=beta,
        params_class=BetaParams,
        config_path=_BETA_CONFIG_PATH,
        input_slots={
            "lhs": SlotDescriptor.of(
                "Series",
                (
                    "Dependent / target Series — the y in y = α + βx + "
                    "ε.  NOT commutative with 'rhs' (swapping fits a "
                    "different line).  Must share an IDENTICAL "
                    "DatetimeIndex with 'rhs' (align_series -> "
                    "select_from_series_set upstream on both arms).  "
                    "USE when the user asks for the sensitivity of one "
                    "series to another as ONE number — how much lhs "
                    "moves per unit move in rhs.  Unit-invariant across "
                    "inputs (β absorbs the regressor's units; no "
                    "convert_units coercion required).  DO NOT use for "
                    "the TIME-VARYING slope (use rolling_regression -> "
                    "select 'beta'), the residual series (use "
                    "regression_residual), or normalised co-movement "
                    "strength (use correlation)."
                ),
            ),
            "rhs": SlotDescriptor.of(
                "Series",
                (
                    "The single explanatory / driver Series — the x in "
                    "y = α + βx + ε (V1 is single-regressor).  Same "
                    "DatetimeIndex requirement as 'lhs' (align "
                    "upstream).  DO NOT pass a constant-valued Series "
                    "(the slope is undefined — typed refusal) and DO "
                    "NOT bind the same upstream Series to both arms "
                    "(the slope is identically 1)."
                ),
            ),
        },
        output=OutputDescriptor.of(
            "ScalarMetric",
            (
                "The full-sample OLS slope β (a single number) wrapped "
                "as a typed ScalarMetric.  Tagged RATIO because the "
                "closed unit enum has no quotient unit — the TRUE "
                "dimension (lhs_units / rhs_units) is recorded in "
                "lineage with the fitted α, R², n_obs and both inputs' "
                "units, so the fit is fully auditable.  Feeds the "
                "terminal answer directly.  Raises BetaError on "
                "insufficient overlap, a zero-variance rhs, an "
                "ill-conditioned fit, or a non-finite result."
            ),
        ),
    ),
    # Track-A (fable_build) — statistical_relationship: full-sample OLS
    # residual of lhs on rhs.  Two Series in, one Series out (the
    # per-date distance from the fitted line — the relative-value
    # signal).  NOT commutative.  Unit-invariant across inputs (β
    # absorbs units); output honestly tagged in the LHS's units; the
    # fitted α/β/R²/n_obs are recorded in lineage.
    "regression_residual": OperatorSpec(
        operator_name="regression_residual",
        callable=regression_residual,
        params_class=RegressionResidualParams,
        config_path=_REGRESSION_RESIDUAL_CONFIG_PATH,
        input_slots={
            "lhs": SlotDescriptor.of(
                "Series",
                (
                    "Dependent / target Series — the y in y = α + βx + "
                    "ε, whose distance from the fitted line the residual "
                    "measures.  NOT commutative with 'rhs' (swapping "
                    "fits a different line).  Must share an IDENTICAL "
                    "DatetimeIndex with 'rhs' (align_series -> "
                    "select_from_series_set upstream).  The OUTPUT lives "
                    "in THIS series's units.  USE when the user asks how "
                    "far one series sits from the level implied by "
                    "another.  Unit-invariant across inputs (β absorbs "
                    "the regressor's units).  DO NOT use for the "
                    "time-varying slope/alpha/R² paths (use "
                    "rolling_regression), co-movement strength (use "
                    "correlation), or a stationarity verdict (use "
                    "cointegration)."
                ),
            ),
            "rhs": SlotDescriptor.of(
                "Series",
                (
                    "The single explanatory / driver Series — the x in "
                    "y = α + βx + ε (V1 is single-regressor).  Same "
                    "DatetimeIndex requirement as 'lhs' (align "
                    "upstream).  DO NOT pass a constant-valued Series "
                    "(the slope is undefined — the operator refuses "
                    "with a typed error) and DO NOT bind the same "
                    "upstream Series to both arms (residuals are "
                    "identically zero)."
                ),
            ),
        },
        output=OutputDescriptor.of(
            "Series",
            (
                "The residual series lhs_t − (α + β·rhs_t) on the shared "
                "index, NaN where either input is NaN, in the LHS's "
                "units (honest passthrough).  Lineage records the fitted "
                "α, β, R², n_obs and both inputs' units — the fitted "
                "line is fully auditable.  Drop-in input for "
                "rolling_zscore, threshold_events, or summarize_series.  "
                "Raises RegressionResidualError on insufficient overlap, "
                "a zero-variance rhs, an ill-conditioned fit, or "
                "overflow."
            ),
        ),
    ),
    "rolling_regression": OperatorSpec(
        operator_name="rolling_regression",
        param_sanity_validator=_rolling_min_periods_param_sanity,
        callable=rolling_regression,
        params_class=RollingRegressionParams,
        config_path=_ROLLING_REGRESSION_CONFIG_PATH,
        # lhs = dependent / target; rhs = single regressor in V1.
        # Output is a SeriesSet keyed by {beta, alpha, r_squared}.
        input_slots={
            "lhs": SlotDescriptor.of(
                "Series",
                (
                    "Dependent / target Series — the y in y = α + βx + ε.  "
                    "Single-artifact slot, NOT commutative with `rhs` "
                    "(swapping them flips the regression).  Must share "
                    "an identical DatetimeIndex with `rhs` (insert "
                    "align_series + select_from_series_set upstream on "
                    "both arms).  USE when the user asks for the "
                    "TIME-VARYING beta of one series on another ('how "
                    "has US 10Y's beta to German Bund evolved'), the "
                    "rolling-window slope, alpha, or R^2.  DO NOT use "
                    "for static / full-sample relationships (use "
                    "correlation for the single coefficient, or "
                    "cointegration for stationarity testing).  Unit-"
                    "invariant — regression coefficients are "
                    "dimensionless or in (lhs_units / rhs_units), all "
                    "supported by the operator."
                ),
            ),
            "rhs": SlotDescriptor.of(
                "Series",
                (
                    "Single regressor Series (V1: one explanatory "
                    "variable) — the x in y = α + βx + ε.  Same "
                    "DatetimeIndex requirement as `lhs` (align upstream). "
                    " USE the explanatory / driver Series the user wants "
                    "the LHS measured against.  DO NOT pass multiple "
                    "regressors here (V1 is single-RHS; multi-regressor "
                    "extension is a planned operator)."
                ),
            ),
        },
        output=OutputDescriptor.of(
            "SeriesSet",
            (
                "Typed SeriesSet keyed by 'beta', 'alpha', 'r_squared' — "
                "three rolling-window time-series, one each.  Common "
                "wiring: select_from_series_set(series_key='beta') for "
                "the rolling beta the user usually asks for; or all "
                "three for full diagnostic.  Beta is in (lhs_units / "
                "rhs_units); alpha in lhs_units; r_squared in RATIO."
            ),
        ),
    ),
    # Track-A (fable_build) — single_series_transform: remove a
    # full-sample deterministic trend (mean or OLS line on row
    # positions).  One Series in, one Series out (units passthrough);
    # the look-ahead scope is disclosed in lineage.
    "detrend": OperatorSpec(
        operator_name="detrend",
        callable=detrend,
        params_class=DetrendParams,
        config_path=_DETREND_CONFIG_PATH,
        input_slots={
            "series": SlotDescriptor.of(
                "Series",
                (
                    "The single typed Series whose deterministic trend "
                    "should be REMOVED.  USE to centre a trending "
                    "series before a distribution/stationarity-style "
                    "read: method=linear subtracts an OLS line fitted "
                    "on the 0..n−1 ROW positions (FULL-SAMPLE fit — "
                    "look-ahead disclosed in lineage; never for "
                    "point-in-time compositions); method=demean "
                    "subtracts the full-sample mean.  NaN positions "
                    "stay NaN.  DO NOT use for deviation from a "
                    "ROLLING baseline (rolling_statistic mean + "
                    "series_arithmetic subtract), residuals vs ANOTHER "
                    "series (regression_residual), or smooth "
                    "trend/cycle decomposition."
                ),
            ),
        },
        output=OutputDescriptor.of(
            "Series",
            (
                "The residual (input − fitted trend) on the input "
                "index, in the input's units (passthrough; NaN "
                "untouched).  Lineage records the method, the FITTED "
                "parameters (mean, or intercept/slope/R²) and "
                "trend_scope='full_sample' (the look-ahead "
                "disclosure).  Typical follow-ons: "
                "summarize_series(std), rolling_zscore, "
                "threshold_events.  Raises DetrendError on <2 finite "
                "observations, a degenerate fit, or overflow."
            ),
        ),
    ),
    # Track-A (fable_build) — single_series_transform: downsample a
    # Series to W/M/Q/Y buckets (period-end labelled).  THE frequency-
    # transition site (the convert_units analogue for the time axis).
    # One Series in, one Series out (units passthrough; frequency tag
    # stamped to the target).
    "resample": OperatorSpec(
        operator_name="resample",
        callable=resample,
        params_class=ResampleParams,
        config_path=_RESAMPLE_CONFIG_PATH,
        input_slots={
            "series": SlotDescriptor.of(
                "Series",
                (
                    "The single typed Series to downsample — "
                    "canonically business-daily; a KNOWN input "
                    "frequency must be strictly higher than the "
                    "target.  USE when the user wants a COARSER view "
                    "('weekly closes', 'monthly averages') or two "
                    "series must meet at a common lower frequency "
                    "before comparison (resample the finer one, then "
                    "align_series).  Buckets anchor to period END "
                    "(W-FRI / month-end / quarter-end / year-end; "
                    "design-locked); the final partial bucket is "
                    "included and disclosed in lineage.  DO NOT use "
                    "for smoothing on the same index (use "
                    "rolling_statistic / ewm_statistic), for "
                    "upsampling (refused — fabrication), or for "
                    "same-frequency calendar alignment (align_series)."
                ),
            ),
        },
        output=OutputDescriptor.of(
            "Series",
            (
                "One value per target period, indexed by period-end "
                "dates, in the input's units; the frequency tag is "
                "STAMPED to the target — the toolbox's only licensed "
                "frequency transition.  Lineage records the locked "
                "rule/label/closed and final_period_complete "
                "(frequency-aware for B/D inputs; one-sided for "
                "unknown: True proves complete, False does not imply "
                "missing).  Typical "
                "follow-ons: align_series, series_arithmetic, "
                "rolling_statistic at the new frequency.  Raises "
                "ResampleError on upsampling/identity targets, the "
                "row-count fabrication guard, or an all-NaN output."
            ),
        ),
    ),
    # Track-A (fable_build) — single_series_transform: cap a Series'
    # extremes at full-sample quantile bounds or fixed bounds.  One
    # Series in, one Series out (units passthrough); the quantile
    # scope's look-ahead is disclosed in lineage.
    "winsorize": OperatorSpec(
        operator_name="winsorize",
        callable=winsorize,
        params_class=WinsorizeParams,
        config_path=_WINSORIZE_CONFIG_PATH,
        input_slots={
            "series": SlotDescriptor.of(
                "Series",
                (
                    "The single typed Series whose extremes should be "
                    "CAPPED.  USE for pre-fit outlier hygiene (clip "
                    "before correlation/beta/regression_residual): "
                    "mode=quantile clips at the FULL-SAMPLE [q, 1−q] "
                    "empirical bounds (LOOK-AHEAD — bounds include "
                    "later data; disclosed in lineage; never use in "
                    "point-in-time compositions), mode=absolute clips "
                    "at fixed caller bounds in the series' own units.  "
                    "NaN positions untouched.  DO NOT use to DETECT "
                    "extremes (use threshold_events — flags, not "
                    "caps), to standardise them (use rolling_zscore), "
                    "or with bounds quoted in other units (convert_"
                    "units first)."
                ),
            ),
        },
        output=OutputDescriptor.of(
            "Series",
            (
                "The clipped series on the input index, in the input's "
                "units (passthrough; NaN untouched).  Lineage records "
                "the mode, the RESOLVED bounds, the clipped count and "
                "quantile_scope='full_sample' (the look-ahead "
                "disclosure).  Typical follow-ons: correlation, beta, "
                "regression_residual, rolling_zscore.  Raises "
                "WinsorizeError on a non-Series input, absolute mode "
                "without bounds, lower >= upper, bounds in quantile "
                "mode, or an all-NaN input."
            ),
        ),
    ),
    # Track-A (fable_build) — single_series_transform: running
    # sum/max/min of one Series from its first row.  One Series in,
    # one Series out (units passthrough); product excluded
    # (dimensional honesty).
    "cumulative": OperatorSpec(
        operator_name="cumulative",
        callable=cumulative,
        params_class=CumulativeParams,
        config_path=_CUMULATIVE_CONFIG_PATH,
        input_slots={
            "series": SlotDescriptor.of(
                "Series",
                (
                    "The single typed Series to accumulate.  USE when "
                    "the user wants a SINCE-INCEPTION running read: a "
                    "level path rebuilt from period changes "
                    "(statistic=sum over a diff'd series), or the "
                    "running peak/trough to date (statistic=max/min — "
                    "e.g. the cummax leg of a drawdown comparison).  "
                    "NaN positions stay NaN; accumulation continues "
                    "over non-NaN values.  DO NOT use for a "
                    "TRAILING-window statistic (use rolling_statistic), "
                    "a full-sample scalar (use summarize_series), or "
                    "compounded growth/cumprod (deliberately "
                    "unsupported on unit-bearing series)."
                ),
            ),
        },
        output=OutputDescriptor.of(
            "Series",
            (
                "The running statistic on the input index, in the "
                "input's units (passthrough).  Typical follow-ons: "
                "series_arithmetic (x − cummax(x) drawdown shapes), "
                "threshold_events, direct surface.  Raises "
                "CumulativeError on a non-Series input, an overflowing "
                "running sum, or an all-NaN output."
            ),
        ),
    ),
    # Track-A (fable_build) — single_series_transform: Hodrick-
    # Prescott trend/cycle decomposition (two-sided, statsmodels).
    # One Series in, one Series out (units passthrough); lamb is
    # REQUIRED (frequency-dependent methodology).
    "hp_filter": OperatorSpec(
        operator_name="hp_filter",
        callable=hp_filter,
        params_class=HpFilterParams,
        config_path=_HP_FILTER_CONFIG_PATH,
        input_slots={
            "series": SlotDescriptor.of(
                "Series",
                (
                    "The single typed Series to decompose — no "
                    "INTERIOR NaN gaps (refused; fill upstream with "
                    "align_series ffill), though contiguous "
                    "leading/trailing warmup NaN from rolling/ewm/lag "
                    "upstreams passes through; >= 3 finite rows.  USE "
                    "for a smooth long-run path through a noisy series "
                    "(component=trend) or the cyclical deviation from "
                    "it (component=cycle); lamb MUST be supplied and "
                    "is frequency-dependent (~1600 quarterly, ~14400 "
                    "monthly, ~1e6-1e7 daily).  TWO-SIDED: the trend "
                    "at every position uses the whole sample "
                    "(look-ahead disclosed in lineage; never for "
                    "point-in-time compositions).  DO NOT use when a "
                    "straight-line/mean detrend suffices (detrend), "
                    "for one-sided recency-weighted smoothing "
                    "(ewm_statistic), or flat trailing windows "
                    "(rolling_statistic)."
                ),
            ),
        },
        output=OutputDescriptor.of(
            "Series",
            (
                "The chosen HP component on the input index, in the "
                "input's units (trend + cycle reconstruct the input "
                "exactly).  Lineage records lamb, the component, "
                "filter_scope='full_sample_two_sided' (the look-ahead "
                "disclosure) and the cycle-variance share.  Typical "
                "follow-ons: series_arithmetic, summarize_series(std) "
                "on the cycle, threshold_events.  Raises HpFilterError "
                "on missing lamb, interior NaN, < 3 finite rows, or a "
                "non-finite solve."
            ),
        ),
    ),
    # Track-A (fable_build) — single_series_transform: shift a Series
    # back by k rows (pandas shift(k); positive = look BACK; lead
    # deliberately unsupported).  One Series in, one Series out.
    "lag": OperatorSpec(
        operator_name="lag",
        callable=lag,
        params_class=LagParams,
        config_path=_LAG_CONFIG_PATH,
        input_slots={
            "series": SlotDescriptor.of(
                "Series",
                (
                    "The single typed Series to shift back.  USE when "
                    "a PAST value of the series must appear at each "
                    "position — comparing a series to its own level k "
                    "rows ago (lag it, then series_arithmetic "
                    "subtract), or building a lagged regressor (lag "
                    "the x leg, then beta / regression_residual).  "
                    "Positive periods always looks BACK (the first k "
                    "rows are NaN); lead (negative shift) is "
                    "deliberately unsupported — look-ahead hazard.  "
                    "DO NOT use for the k-row CHANGE directly (use "
                    "series_arithmetic op=diff), or to hand-roll "
                    "no-look-ahead hygiene inside rolling stats (the "
                    "rolling/ewm operators have their own "
                    "look_ahead_safe knob)."
                ),
            ),
        },
        output=OutputDescriptor.of(
            "Series",
            (
                "The input shifted back by k rows on the SAME index "
                "(value from k rows earlier at each position; first k "
                "rows NaN), in the input's units.  Frequency and "
                "missingness pass through; the sign convention rides "
                "in lineage.  Typical follow-ons: series_arithmetic "
                "(x − lag(x)), beta, correlation.  Raises LagError on "
                "a non-Series input or periods >= the input length."
            ),
        ),
    ),
    # Track-A (fable_build) — single_series_transform: exponentially-
    # weighted mean (EWMA) or std of one Series.  One Series in, one
    # Series out (units passthrough).  Decay via span only; adjust=True
    # and debiased std design-locked.
    "ewm_statistic": OperatorSpec(
        operator_name="ewm_statistic",
        callable=ewm_statistic,
        params_class=EwmStatisticParams,
        config_path=_EWM_STATISTIC_CONFIG_PATH,
        input_slots={
            "series": SlotDescriptor.of(
                "Series",
                (
                    "The single typed Series to recency-weight.  USE "
                    "when the user asks for an exponentially-weighted / "
                    "smoothed level (statistic=mean — the EWMA) or "
                    "recency-weighted dispersion of the SERIES VALUES "
                    "(statistic=std, debiased, in the input's units).  "
                    "Decay is the pandas span (≈2/(span+1) per row); "
                    "warmup below min_periods is NaN.  For dispersion "
                    "of CHANGES, difference upstream first "
                    "(series_arithmetic diff/pct_change).  DO NOT use "
                    "for a flat equal-weight trailing window (use "
                    "rolling_statistic), for z-scores (use "
                    "rolling_zscore), or for ANNUALIZED return vol "
                    "(calendar scaling is finance math — primitives "
                    "own it)."
                ),
            ),
        },
        output=OutputDescriptor.of(
            "Series",
            (
                "The EW statistic per date (NaN during warmup), in the "
                "input's units (passthrough for both statistics).  "
                "Lineage records span, the resolved min_periods, and "
                "the design-locked adjust/bias choices.  Drop-in input "
                "for series_arithmetic, threshold_events, "
                "rolling_zscore, or direct surface.  Raises "
                "EwmStatisticError on a non-Series input or an all-NaN "
                "output."
            ),
        ),
    ),
    # v2.0 (ADR 0016) — single_series_transform: trailing-window
    # standardisation of a Series.  One Series in, one Series out
    # (always in Z_SCORE units).  The composition primitive for any
    # "where are we vs our own recent history" question.
    "rolling_zscore": OperatorSpec(
        operator_name="rolling_zscore",
        param_sanity_validator=_rolling_min_periods_param_sanity,
        callable=rolling_zscore,
        params_class=RollingZscoreParams,
        config_path=_ROLLING_ZSCORE_CONFIG_PATH,
        input_slots={
            "series": SlotDescriptor.of(
                "Series",
                (
                    "Single typed Series whose values will be "
                    "standardised against their own trailing-window "
                    "history.  USE when the user asks 'how unusual is "
                    "today vs recent history' / 'how many sigmas from "
                    "fair value' / 'is this 2σ rich or cheap' — the "
                    "z-score is the canonical macro 'right-now vs "
                    "recent context' transform.  Window length and "
                    "minimum-periods are knobs (params.window_days, "
                    "params.min_periods).  DO NOT use for absolute "
                    "ranking against full history (use percentile_rank "
                    "instead — z-score is reference-window-relative) or "
                    "for cross-series standardisation (z-score only "
                    "standardises against its own past)."
                ),
            ),
        },
        output=OutputDescriptor.of(
            "Series",
            (
                "Trailing-window z-score in Z_SCORE units (dimensionless): "
                "(value_t - μ_window_t) / σ_window_t.  Warmup period "
                "(first window_days - 1 dates) is NaN.  Drop-in input "
                "for threshold_events (e.g. trip-wires at ±2σ), "
                "correlation against another z-score, or direct surface "
                "to the user."
            ),
        ),
    ),
    # v2.0 (ADR 0016) — single_series_transform: generic windowed
    # reducer (mean / std / min / max / sum / skew / kurtosis) over a
    # Series.  One Series in, one Series out (input unit preserved;
    # RATIO for the dimensionless higher moments — v1.1.0).  The
    # composition primitive for rolling vol, moving averages, rolling
    # ranges, rolling distribution shape.
    "rolling_statistic": OperatorSpec(
        operator_name="rolling_statistic",
        param_sanity_validator=_rolling_min_periods_param_sanity,
        callable=rolling_statistic,
        params_class=RollingStatisticParams,
        config_path=_ROLLING_STATISTIC_CONFIG_PATH,
        input_slots={
            "series": SlotDescriptor.of(
                "Series",
                (
                    "Single typed Series to be reduced over a trailing "
                    "window via params.statistic in {mean, std, min, "
                    "max, sum, skew, kurtosis}.  USE for moving "
                    "averages (statistic=mean), realised vol "
                    "(statistic=std), rolling ranges (min / max), "
                    "rolling cumulative quantities (sum), or rolling "
                    "distribution shape (skew / kurtosis — "
                    "dimensionless RATIO out; kurtosis is EXCESS, "
                    "normal == 0) — every 'over the last N days' "
                    "summary.  Window length is params.window_days.  "
                    "DO NOT use for z-scores (use rolling_zscore — it "
                    "emits Z_SCORE units), for recency-weighted stats "
                    "(use ewm_statistic), and DO NOT use for "
                    "cross-series rolling stats like rolling "
                    "correlation or rolling beta (use rolling_correlation "
                    "/ rolling_regression respectively)."
                ),
            ),
        },
        output=OutputDescriptor.of(
            "Series",
            (
                "Windowed reduction (mean / std / min / max / sum / "
                "skew / kurtosis) preserving input units — except the "
                "dimensionless higher moments, which emit RATIO.  "
                "Warmup period is NaN.  Drop-in input for "
                "series_arithmetic, threshold_events, or direct "
                "surface."
            ),
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
                (
                    "Single typed Series to rank against its OWN "
                    "history.  USE this slot for the canonical macro "
                    "'rich / cheap vs history' question — 'is the curve "
                    "in the 90th percentile of where it's been?', "
                    "'where does this spread sit in its own 5-year "
                    "history?'.  Window mode is configured by "
                    "params.mode (trailing window of length N, or "
                    "expanding from inception); output is always in "
                    "PCT_RANK units (0-100).  DO NOT use this slot for "
                    "z-score-style normalisation (use rolling_zscore — "
                    "z-score is sigma units, percentile_rank is rank "
                    "units) or for cross-series ranking (only ranks "
                    "against own history)."
                ),
            ),
        },
        output=OutputDescriptor.of(
            "Series",
            (
                "Percentile rank vs history (PCT_RANK units, 0-100).  "
                "Higher = richer / wider / higher / more extreme vs the "
                "Series's own past, depending on the underlying "
                "quantity.  Warmup period is NaN.  Drop-in input for "
                "threshold_events (e.g. 'top decile' alerts), or direct "
                "surface."
            ),
        ),
    ),
    # v2.0 (ADR 0016) — statistical_relationship: windowed correlation
    # between two index-aligned Series.  Two Series in, one Series out
    # (rolling coefficients in RATIO units).  The SEPARATE-operator
    # counterpart of ``correlation`` per OPR2 — does NOT align, does
    # NOT take a window flag on ``correlation`` itself.
    "rolling_correlation": OperatorSpec(
        operator_name="rolling_correlation",
        param_sanity_validator=_rolling_min_periods_param_sanity,
        callable=rolling_correlation,
        params_class=RollingCorrelationParams,
        config_path=_ROLLING_CORRELATION_CONFIG_PATH,
        input_slots={
            "left": SlotDescriptor.of(
                "Series",
                (
                    "One arm of the rolling correlation pair.  Single-"
                    "artifact slot (no scalar literal) expecting a typed "
                    "Series that shares an IDENTICAL DatetimeIndex with "
                    "'right' — this operator does NOT align internally; "
                    "wire align_series -> select_from_series_set on both "
                    "arms upstream.  USE when the user asks for a "
                    "TIME-VARYING correlation ('how has the correlation "
                    "between US 2s10s and 5Y breakeven evolved').  "
                    "Window length is params.window_days.  The operator "
                    "is COMMUTATIVE — corr(left, right) == corr(right, "
                    "left) — so the choice of arm is cosmetic.  DO NOT "
                    "use for a full-sample correlation NUMBER (use "
                    "correlation — emits ScalarMetric instead of Series).  "
                    "Unit-invariant (correlation is dimensionless)."
                ),
            ),
            "right": SlotDescriptor.of(
                "Series",
                (
                    "Symmetric counterpart to 'left' for the rolling "
                    "correlation pair.  Same artifact type, same "
                    "DatetimeIndex requirement (align upstream), same "
                    "unit-invariance.  DO NOT bind both arms to the "
                    "same upstream Series (degenerate)."
                ),
            ),
        },
        output=OutputDescriptor.of(
            "Series",
            (
                "Per-date rolling correlation coefficient (RATIO units, "
                "values in [-1, 1]) computed over each trailing window "
                "of size params.window_days.  Warmup is NaN.  Drop-in "
                "input for threshold_events (e.g. flag dates when "
                "rolling corr fell below 0.3), rolling_zscore (corr-of-"
                "corr surprise), or direct surface."
            ),
        ),
    ),
    # Track-A (fable_build) — statistical_relationship: trailing-window
    # covariance between two index-aligned Series.  Two Series in, one
    # Series out (per-date rolling covariances).  The SEPARATE-operator
    # counterpart of ``covariance`` per OPR2, and the unit-BEARING
    # sibling of ``rolling_correlation`` (same units required by
    # default; output RATIO with the true product dimension recorded
    # in lineage).
    "rolling_covariance": OperatorSpec(
        operator_name="rolling_covariance",
        callable=rolling_covariance,
        params_class=RollingCovarianceParams,
        config_path=_ROLLING_COVARIANCE_CONFIG_PATH,
        param_sanity_validator=_rolling_min_periods_param_sanity,
        unit_validator=_covariance_unit_validator,
        input_slots={
            "left": SlotDescriptor.of(
                "Series",
                (
                    "One arm of the rolling covariance pair.  Single-"
                    "artifact slot (no scalar literal) expecting a typed "
                    "Series that shares an IDENTICAL DatetimeIndex with "
                    "'right' — this operator does NOT align internally; "
                    "canonical upstream is align_series -> "
                    "select_from_series_set on both arms.  USE when the "
                    "user asks how the LEVEL co-movement of two series "
                    "has EVOLVED over time (one covariance value per "
                    "date).  COMMUTATIVE — the choice of arm is "
                    "cosmetic.  Unit-BEARING like covariance: both "
                    "Series must share the SAME units by default "
                    "(insert convert_units upstream to reconcile "
                    "PERCENT vs BPS).  DO NOT use for ONE full-sample "
                    "covariance number (use covariance), the normalised "
                    "[-1, 1] strength over time (use "
                    "rolling_correlation), or the time-varying slope "
                    "(use rolling_regression)."
                ),
            ),
            "right": SlotDescriptor.of(
                "Series",
                (
                    "Symmetric counterpart to 'left' for the rolling "
                    "covariance pair.  Same artifact type, same "
                    "DatetimeIndex requirement (align upstream), same "
                    "same-units-by-default discipline (convert_units "
                    "upstream to reconcile).  DO NOT bind both arms to "
                    "the same upstream Series (that is just the rolling "
                    "variance) and DO NOT pass a scalar literal here "
                    "(artifact-only slot)."
                ),
            ),
        },
        output=OutputDescriptor.of(
            "Series",
            (
                "Per-date trailing-window covariance (window = "
                "params.window rows).  Tagged RATIO because the closed "
                "unit enum has no product unit — the TRUE dimension "
                "(left_units × right_units) is recorded in lineage, with "
                "ddof, window, min_periods and the overlap counts.  "
                "Warmup is NaN.  A constant window is NOT degenerate — "
                "its covariance is a legitimate 0.0 (unlike "
                "rolling_correlation).  Raises RollingCovarianceError on "
                "strict-mode units mismatch, overflow, or an all-NaN "
                "output.  Drop-in input for threshold_events, "
                "rolling_zscore, summarize_series, or direct surface."
            ),
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
                (
                    "One leg of the cointegration pair.  Engle-Granger "
                    "is NOT symmetric — the test regresses left on "
                    "right and ADF-tests the residuals — so swapping "
                    "the arms yields a (slightly) different test "
                    "statistic.  USE when the user asks 'are these two "
                    "series cointegrated' / 'is this spread stationary' "
                    "/ 'is this a tradeable pair'.  Single-artifact "
                    "slot expecting a typed Series that shares an "
                    "IDENTICAL DatetimeIndex with 'right' (align "
                    "upstream).  DO NOT use this slot for full-sample "
                    "linear correlation (use correlation) or rolling "
                    "correlation (use rolling_correlation) — "
                    "cointegration is a stationarity test on the "
                    "regression residuals, a different statistical "
                    "object."
                ),
            ),
            "right": SlotDescriptor.of(
                "Series",
                (
                    "The other leg of the cointegration pair.  Same "
                    "DatetimeIndex requirement as 'left'.  The arms "
                    "are NOT commutative (the test direction matters)."
                ),
            ),
        },
        output=OutputDescriptor.of(
            "ScalarMetric",
            (
                "The Engle-Granger ADF test statistic (dimensionless, "
                "RATIO units).  More-negative values reject the unit-"
                "root null more strongly (i.e. evidence the residual is "
                "stationary, hence the pair is cointegrated).  Lineage "
                "records the direction (left ~ right) and overlap "
                "n_observations.  The substrate does NOT auto-translate "
                "this to a yes/no judgement — the answer layer "
                "interprets against critical-value tables."
            ),
        ),
    ),
    # Track-A (fable_build) — cross_sectional: all pairwise differences
    # of an aligned SeriesSet.  One SeriesSet in, one Panel out (rows =
    # dates; one column per unordered pair; units passthrough).  Same-
    # units-across-members required outright; <= 50 members
    # (design-locked ceiling).
    "pairwise_spread_matrix": OperatorSpec(
        operator_name="pairwise_spread_matrix",
        callable=pairwise_spread_matrix,
        params_class=PairwiseSpreadMatrixParams,
        config_path=_PAIRWISE_SPREAD_MATRIX_CONFIG_PATH,
        input_slots={
            "series_set": SlotDescriptor.of(
                "SeriesSet",
                (
                    "An aligned SeriesSet of 2–50 members sharing ONE "
                    "unit — canonical upstream is align_series (insert "
                    "convert_units on offending members first; "
                    "mixed-unit differences are refused outright).  USE "
                    "when the user wants EVERY pair spread across a "
                    "small universe at once, as one wide artifact over "
                    "time.  Pair enumeration, direction "
                    "('a__minus__b' = a − b, sorted upper triangle), "
                    "column naming and the 50-member ceiling are "
                    "design-locked and recorded in lineage.  DO NOT use "
                    "for ONE named pair (use select_from_series_set ×2 "
                    "+ series_arithmetic), a per-date dispersion gauge "
                    "(use cross_sectional_statistic(std)), or "
                    "vs-average series (use demean_cross_section)."
                ),
            ),
        },
        output=OutputDescriptor.of(
            "Panel",
            (
                "A Panel whose rows are the shared dates and whose "
                "columns are the unordered member pairs "
                "('<ki>__minus__<kj>', value = ki − kj), every column "
                "in the members' common unit.  A pair value is NaN "
                "where either member is NaN.  Typically the TERMINAL "
                "artifact (no live operator consumes Panel).  Lineage "
                "extends the input set's chain with the design locks "
                "recorded.  Raises PairwiseSpreadMatrixError on <2 or "
                ">50 members, mixed units, overflow, or an all-NaN "
                "output."
            ),
        ),
    ),
    # Track-A (fable_build) — cross_sectional: per-date top/bottom-n
    # selection mask over an aligned SeriesSet.  One SeriesSet in, one
    # SeriesSet out (same keys; non-selected values masked to NaN —
    # the semantics ride in lineage).  Units passthrough; same-units-
    # across-members required outright.
    "top_n": OperatorSpec(
        operator_name="top_n",
        callable=top_n,
        params_class=TopNParams,
        config_path=_TOP_N_CONFIG_PATH,
        input_slots={
            "series_set": SlotDescriptor.of(
                "SeriesSet",
                (
                    "An aligned SeriesSet of N >= 2 members sharing ONE "
                    "unit — canonical upstream is align_series, often "
                    "after cross_sectional_zscore or "
                    "demean_cross_section (insert convert_units on "
                    "offending members first; mixed-unit selection is "
                    "refused outright).  USE when the user wants to "
                    "KEEP only the n most extreme members per date "
                    "('the five richest points per day') as a "
                    "composable basket over time — at each date the n "
                    "largest (mode=top) or smallest (mode=bottom) "
                    "non-NaN members keep their values and the rest "
                    "are masked to NaN (NaN = NOT SELECTED, disclosed "
                    "in lineage; dates below params.min_members mask "
                    "everything; dates with fewer than n valid members "
                    "keep all of them).  DO NOT use for every member's "
                    "rank (use cross_sectional_rank), for threshold-"
                    "based selection on one series (use "
                    "threshold_events + apply_mask), or for a per-date "
                    "summary (use cross_sectional_statistic)."
                ),
            ),
        },
        output=OutputDescriptor.of(
            "SeriesSet",
            (
                "A SeriesSet with the SAME keys, common index and "
                "frequency (dates are never dropped — only payloads "
                "are masked); each member keeps its value where it is "
                "among the per-date top/bottom n and is NaN elsewhere.  "
                "Units passthrough; missingness fresh; the set-level "
                "lineage extends the input's chain with the full "
                "selection rule (n, mode, design-locked tie-break, "
                "floor) recorded.  Typical follow-ons: "
                "cross_sectional_statistic (basket average), "
                "select_from_series_set.  Raises TopNError on <2 "
                "members, mixed units, or an all-NaN output."
            ),
        ),
    ),
    # Track-A (fable_build) — arithmetic: weighted sum of named
    # SeriesSet members (N-leg baskets / synthetic series).  One
    # SeriesSet in, one Series out (units passthrough of the named
    # members' common unit).  weights mapping REQUIRED (no meaningful
    # default — the select_from_series_set precedent).
    "weighted_combination": OperatorSpec(
        operator_name="weighted_combination",
        callable=weighted_combination,
        params_class=WeightedCombinationParams,
        config_path=_WEIGHTED_COMBINATION_CONFIG_PATH,
        input_slots={
            "series_set": SlotDescriptor.of(
                "SeriesSet",
                (
                    "An aligned SeriesSet containing every member the "
                    "params.weights mapping names — canonical upstream "
                    "is align_series (use its output_keys to control "
                    "the member names the mapping must match; insert "
                    "convert_units on offending members first — "
                    "mixed-unit sums across the NAMED members are "
                    "refused outright).  USE when the user wants a "
                    "CUSTOM weighted combination of several series as "
                    "ONE series ('long A, short two of B, long C' — "
                    "weights {a:1, b:-2, c:1}); members absent from "
                    "the mapping are excluded; negative weights are "
                    "first-class; at least 2 members must be named.  "
                    "DO NOT use for exactly two unit-weight legs (use "
                    "series_arithmetic), for scaling one series (use "
                    "series_arithmetic multiply with a scalar "
                    "literal), or for the equal-weight average of the "
                    "whole set (use cross_sectional_statistic mean)."
                ),
            ),
        },
        output=OutputDescriptor.of(
            "Series",
            (
                "The weighted sum Σ w_k · member_k on the shared "
                "index, NaN exactly where ANY named member is NaN "
                "(unnamed members' NaNs are inert), in the named "
                "members' common unit (weights are dimensionless).  "
                "The full weights mapping rides in lineage — the "
                "content-defining basket recipe.  Drop-in input for "
                "rolling_zscore, threshold_events, summarize_series, "
                "or direct surface.  Raises WeightedCombinationError "
                "on missing weights, <2 named members, an unknown "
                "named key, a zero/non-finite weight, mixed units, "
                "overflow, or an all-NaN output."
            ),
        ),
    ),
    "summarize_series": OperatorSpec(
        operator_name="summarize_series",
        callable=summarize_series,
        params_class=SummarizeSeriesParams,
        config_path=_SUMMARIZE_SERIES_CONFIG_PATH,
        # Single-Series input → ONE scalar summary (ScalarMetric).  This
        # is the canonical terminal for "what is the average / std /
        # median / current value of X" queries: one number out, which is
        # exactly what the L4.5 CoverageGate expects for single-number
        # questions.
        input_slots={
            "series": SlotDescriptor.of(
                "Series",
                (
                    "Single typed Series to collapse to ONE scalar "
                    "full-sample summary via params.statistic — exactly "
                    "{mean, median, std, sum, count, last, first, "
                    "quantile}.  ``last`` = the latest finite "
                    "observation (the 'current value' of the series); "
                    "``first`` = the earliest; ``quantile`` = the q-th "
                    "full-sample empirical quantile (type-7 linear "
                    "interpolation) in the INPUT'S UNITS "
                    "(set params.q — 0.05/0.95 for tail reads).  For a "
                    "min/max use rolling_statistic (as a rolling "
                    "Series) — there is no scalar min/max here.  "
                    "PRIMARY use: a plain descriptive summary ('the mean "
                    "/ std / median / current value / percentile level "
                    "of X over the period') — summarize_series is the "
                    "terminal, one number out.  DO NOT use for a "
                    "value-per-date rolling statistic (that is "
                    "rolling_statistic); DO NOT use for cross-series "
                    "summaries (single-Series only)."
                ),
            ),
        },
        output=OutputDescriptor.of(
            "ScalarMetric",
            (
                "A single finite scalar (ScalarMetric) carrying the "
                "reduction of the input via params.statistic, with "
                "metric_key equal to the statistic name "
                "('quantile_<q>' for quantiles) and units preserved "
                "1:1 from the input.  This is a terminal answer "
                "artifact — the single number the user asked for "
                "(e.g. the average / std / current value / percentile "
                "level of X)."
            ),
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
