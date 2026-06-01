"""shared.workflow.validation_result — structured multi-error validation outcome.

The substrate's collect-all alternative to ``WorkflowValidationError``.
Where the legacy validator raised on the *first* problem it found, the
PR-1 refactor walks the whole DAG and returns every problem in one pass
as a frozen ``ValidationResult``.  Each problem carries a stable
``ErrorCode`` (closed family, per P8) and an ``OwnerLayer`` tag naming
which layer of the open-DAG pipeline owns fixing it.

The shape exists for one reason: the PR-4 bounded-repair controller
needs the full error set to dispatch retries by owner layer in a single
round.  A first-error-raise loop would round-trip the Composer / Selector
N times for N errors — wasteful and the wrong shape for the repair
protocol the architecture rests on.

Back-compat is preserved via the legacy strict wrapper
``shared.workflow.validate.validate_workflow``: it calls the collect-all
path and re-raises the first error as ``WorkflowValidationError``, so
the existing executor caller and existing tests stay green.

Closed-family discipline (P8)
-----------------------------
``ErrorCode`` is a closed enum.  Every entry maps to exactly one raise
site that previously lived inside ``validate_workflow``.  Adding a new
code requires:

  1. an ADR documenting the new structural check it represents,
  2. a new code value here (existing values never change meaning), AND
  3. tests asserting the new code surfaces from the validator.

Three tags exhaust the dispatch space today:

  * ``L3_WIRING``  — Composer (PR-7) owns the fix.  The shape, edges,
    operator selection, or knob choice is wrong.
  * ``L2_BINDING`` — per-domain Selector (PR-6) owns the fix.  The
    primitive bound to a leaf is wrong (resolver miss, wrong
    output_field, type-mismatched output for the slot it feeds).
  * ``ASSEMBLER``  — the assembler itself (PR-4) failed mechanically
    (e.g. substitution miss).  No LLM retry; the assembler retries
    internally.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field


# ============================================================================
# CLOSED FAMILIES
# ============================================================================


class ErrorCode(str, Enum):
    """Closed family of validation error codes (P8).

    The first 13 codes (E_UNKNOWN_OPERATOR ... E_PRIMITIVE_RESOLVE_FAIL)
    each correspond to exactly one structural check inside
    ``shared.workflow.validate.validate_workflow_result``.  Order of
    definition matches the order of checks inside the validator so a
    reviewer can grep the validator for the code and see the raising
    condition immediately.

    ``E_FREQUENCY_MISMATCH`` is declared here but NOT raised by
    ``validate_workflow_result`` itself in PR-1 — the PR-1 raise sites
    are exactly the legacy 13.  PR-3 / PR-4 introduce the leaf-contract
    role-discriminant check (``LeafRequest.expected_frequency`` vs
    ``BoundLeaf.declared_frequency``) and that's where the new code's
    raise site lands.  Declaring it up-front honours P8 closed-family
    discipline: the substrate ships the full taxonomy as ONE coherent
    enum and the test that asserts "no silent enum drift" works across
    PR boundaries.

    The mapping is stable across the lifetime of the substrate;
    extensions require an ADR.
    """

    E_UNKNOWN_OPERATOR = "E_UNKNOWN_OPERATOR"
    E_UNKNOWN_SLOT = "E_UNKNOWN_SLOT"
    E_EDGE_TARGETS_PRIMITIVE = "E_EDGE_TARGETS_PRIMITIVE"
    E_LITERAL_TARGETS_NON_OPERATOR = "E_LITERAL_TARGETS_NON_OPERATOR"
    E_LITERAL_UNKNOWN_SLOT = "E_LITERAL_UNKNOWN_SLOT"
    E_LITERAL_SLOT_NO_SCALAR = "E_LITERAL_SLOT_NO_SCALAR"
    E_ARITY_VIOLATION = "E_ARITY_VIOLATION"
    E_UNBOUND_REQUIRED_SLOT = "E_UNBOUND_REQUIRED_SLOT"
    E_TYPE_MISMATCH = "E_TYPE_MISMATCH"
    E_UNKNOWN_OUTPUT_FIELD = "E_UNKNOWN_OUTPUT_FIELD"
    E_UNIT_MISMATCH = "E_UNIT_MISMATCH"
    E_DAG_CYCLE = "E_DAG_CYCLE"
    E_PRIMITIVE_RESOLVE_FAIL = "E_PRIMITIVE_RESOLVE_FAIL"
    # Declared in PR-1, raised in PR-3 / PR-4 — see class docstring.
    E_FREQUENCY_MISMATCH = "E_FREQUENCY_MISMATCH"
    # Added in PR-4 for the free-form Boundary A check; HARDENED in
    # PR-10D from WARNING to ERROR.  Emitted with severity=ERROR and
    # owner_layer=L2_BINDING — a mismatch (after the Selector's one
    # bounded rebind round) drives AssemblyResult to status=REFUSED,
    # so no role-mismatched DAG ever reaches Boundary B.  Still
    # honours the no-role-enum ruling (rulings #1, #5): the
    # comparison is a mechanical normalised-string check of
    # LLM-authored free-form English, not a lookup into a curated
    # role vocabulary.  detail['field'] indicates which free-form
    # subfield mismatched ('semantic_role' or
    # 'requested_output_meaning').
    E_ROLE_DISCRIMINANT_MISMATCH = "E_ROLE_DISCRIMINANT_MISMATCH"


class OwnerLayer(str, Enum):
    """Which layer of the open-DAG pipeline owns fixing a validation error.

    Read by PR-4's bounded-repair controller to dispatch retries to the
    correct LLM (or to retry mechanically inside the assembler).  The
    set is intentionally minimal: three values cover every dispatch
    decision the repair loop needs to make today.
    """

    L3_WIRING = "L3_WIRING"
    L2_BINDING = "L2_BINDING"
    ASSEMBLER = "ASSEMBLER"


class Severity(str, Enum):
    """Severity of a validation entry.

    Added in PR-4 (open-DAG PoC).  The hard-substrate validator from
    PR-1 emits everything at ``ERROR`` (the legacy behaviour); PR-4's
    Boundary A contract check emits ``WARNING`` for the free-form
    role-discriminant mismatches (semantic_role / requested_output_meaning)
    that fail normalised-string equality but are NOT structural
    failures.  Hard errors block execution; warnings flow through to
    Boundary B (PR-8) as supplementary evidence.

    Default on ``ValidationError`` is ``ERROR`` so every PR-1 raise
    site keeps producing hard errors without a code change.
    """

    ERROR = "ERROR"
    WARNING = "WARNING"


# ============================================================================
# ERROR RECORD
# ============================================================================


class ValidationError(BaseModel):
    """One structured validation error.

    Frozen Pydantic.  Carries the stable ``code`` + ``owner_layer`` for
    repair-loop dispatch and a fully-formatted ``message`` string that
    is byte-identical to the legacy ``WorkflowValidationError``'s
    message — so the strict-wrapper path (``validate_workflow``) keeps
    raising with the exact text existing tests assert on.

    Optional structural fields (``node_id``, ``edge``,
    ``target_input_slot``, ``operator_name``, ``tool_name``) are
    populated only when the underlying check has them; ``detail`` is a
    free-form dict for extras (e.g. expected vs actual artifact type
    on ``E_TYPE_MISMATCH``).  None of these are used to render the
    message — the message is computed at the raise site to preserve
    the legacy string format.  These fields exist for programmatic
    repair (PR-4 reads them) and for CI-grade error-taxonomy asserts
    in tests.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: ErrorCode
    owner_layer: OwnerLayer
    message: str = Field(..., min_length=1)
    severity: Severity = Field(
        default=Severity.ERROR,
        description=(
            "ERROR (default) blocks execution; WARNING flows to "
            "Boundary B as supplementary evidence without failing "
            "Boundary A.  All PR-1 raise sites produce ERROR.  PR-4's "
            "free-form contract checks (semantic_role / "
            "requested_output_meaning) produce WARNING."
        ),
    )
    node_id: Optional[str] = None
    edge: Optional[Tuple[str, str]] = Field(
        default=None,
        description="(source_node_id, target_node_id) when the error is about an edge.",
    )
    target_input_slot: Optional[str] = None
    operator_name: Optional[str] = None
    tool_name: Optional[str] = None
    leaf_id: Optional[str] = Field(
        default=None,
        description=(
            "Set when the error pertains to a specific LeafHole / "
            "BoundLeaf in a PR-4 assembly (matches the leaf's "
            "node_id).  Lets the repair controller dispatch L2_BINDING "
            "errors to the right per-leaf rebinder."
        ),
    )
    detail: Dict[str, Any] = Field(default_factory=dict)


# ============================================================================
# RESULT
# ============================================================================


class ValidationResult(BaseModel):
    """Outcome of a collect-all validation pass.

    Frozen.  An empty ``errors`` tuple means the DAG is structurally
    legal at this layer — Boundary A passed for the structural checks
    the validator owns.  (Role/intent compatibility on hole/fill is a
    PR-3 + PR-4 concern, layered on top of this primitive.)

    Helpers (``is_clean``, ``by_owner_layer``, ``by_code``, ``first``)
    exist to keep the PR-4 repair-controller dispatch concise.

    The ``workflow_id`` is echoed from the input workflow so a single
    pipeline run that validates multiple intermediate workflows can
    keep its results disambiguated (the assembler may revalidate after
    repair, producing a second result for the same workflow_id —
    that's expected and the caller treats them as two passes).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    workflow_id: str
    errors: Tuple[ValidationError, ...] = ()

    @property
    def is_clean(self) -> bool:
        """True iff no validation entries with severity=ERROR were
        collected.  Warnings do NOT block — they flow through to
        Boundary B (PR-8) as supplementary evidence.

        Per PR-4: ``is_clean`` means "Boundary A passed" — the
        assembled workflow can move to execution (or to Boundary B's
        coverage check, whichever the orchestrator gates next)."""
        return not self.hard_errors

    @property
    def hard_errors(self) -> Tuple[ValidationError, ...]:
        """Entries with ``severity == ERROR``.  PR-1's structural
        validator emits exclusively at this severity (back-compat
        guaranteed by the default-ERROR field on ValidationError)."""
        return tuple(e for e in self.errors if e.severity == Severity.ERROR)

    @property
    def warnings(self) -> Tuple[ValidationError, ...]:
        """Entries with ``severity == WARNING``.  Populated by PR-4's
        Boundary A contract check for free-form mismatches
        (semantic_role / requested_output_meaning).  Boundary B (PR-8)
        consumes these as supplementary evidence."""
        return tuple(e for e in self.errors if e.severity == Severity.WARNING)

    def by_owner_layer(self, layer: OwnerLayer) -> Tuple[ValidationError, ...]:
        """Entries whose ``owner_layer`` matches ``layer``.  Used by
        the PR-4 repair controller to dispatch fixes to the right
        caller.  Includes BOTH errors and warnings — the controller
        filters by severity as needed."""
        return tuple(e for e in self.errors if e.owner_layer == layer)

    def by_code(self, code: ErrorCode) -> Tuple[ValidationError, ...]:
        """Entries with the given ``code``.  Useful for tests and
        diagnostic surfaces."""
        return tuple(e for e in self.errors if e.code == code)

    def by_leaf(self, leaf_id: str) -> Tuple[ValidationError, ...]:
        """Entries pointing at a specific leaf (matches
        ``ValidationError.leaf_id``).  Used by PR-4 to gather all
        errors targeting one BoundLeaf before calling the rebinder."""
        return tuple(e for e in self.errors if e.leaf_id == leaf_id)

    def first(self) -> Optional[ValidationError]:
        """Convenience for the legacy strict wrapper.  Returns the
        first hard error in collection order (matches the order the
        pre-refactor first-error-raise validator would have surfaced),
        or ``None`` when no hard errors exist.

        Warnings are NEVER returned by this — the strict wrapper from
        PR-1 must surface only blocking failures, otherwise existing
        callers (executor) would crash on what PR-4 considers a
        SOFT signal."""
        for e in self.errors:
            if e.severity == Severity.ERROR:
                return e
        return None


__all__ = [
    "ErrorCode",
    "OwnerLayer",
    "Severity",
    "ValidationError",
    "ValidationResult",
]
