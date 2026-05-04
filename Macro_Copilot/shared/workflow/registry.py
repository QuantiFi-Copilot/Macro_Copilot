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

from shared.artifacts.types import (
    EventSet,
    Panel,
    Series,
    SeriesSet,
    WindowedPanel,
)
from shared.operators.align_series import align_series, AlignSeriesParams
from shared.operators.conditional_aggregate import (
    conditional_aggregate,
    ConditionalAggregateParams,
)
from shared.operators.event_windows import event_windows, EventWindowsParams
from shared.operators.series_arithmetic import (
    series_arithmetic,
    SeriesArithmeticParams,
)
from shared.operators.threshold_events import (
    threshold_events,
    ThresholdEventsParams,
)


# ============================================================================
# ARTIFACT TYPE NAMES (closed enum used by the validator)
# ============================================================================

# Names must match the artifact wrapper class names in
# ``shared.artifacts.types``.  Used by the validator to compare
# operator input/output slot types structurally.

ARTIFACT_TYPE_NAMES: tuple[str, ...] = (
    "Series",
    "SeriesSet",
    "EventSet",
    "Panel",
    "WindowedPanel",
)


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
    type_map = {
        Series: "Series",
        SeriesSet: "SeriesSet",
        EventSet: "EventSet",
        Panel: "Panel",
        WindowedPanel: "WindowedPanel",
    }
    for cls, name in type_map.items():
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
        relaxes for slots in this set.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    operator_name: str
    callable: Callable[..., Any]
    params_class: Optional[Type[BaseModel]]
    input_slots: Dict[str, str]
    output_type: str
    accepts_scalar_input: tuple[str, ...] = ()


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
    "series_arithmetic": OperatorSpec(
        operator_name="series_arithmetic",
        callable=series_arithmetic,
        params_class=SeriesArithmeticParams,
        input_slots={"left": "Series", "right": "Series"},
        output_type="Series",
        # `right` can be a Series OR a Python scalar (int/float)
        # for binary scalar arithmetic, or absent for unary ops.
        accepts_scalar_input=("right",),
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
    """

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    tool_name: str
    callable: Callable[..., Any]
    input_class: Type[BaseModel]
    output_class: Type[BaseModel]
    config_path: Path


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
