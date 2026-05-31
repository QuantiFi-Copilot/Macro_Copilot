"""orchestrator.open_dag.coverage_gate — PR-8 of the open-DAG PoC.

The Boundary B Coverage Gate.  A **hard-block** LLM call between
``Assembler.assemble`` (PR-4) and Workflow execution (PR-9 / PR-10):

  - The gate's INPUT is the **original user prompt** (verbatim) +
    a deterministic English echo of the assembled DAG (from
    ``dag_echo``) + L1 router decomposition (as supplementary
    evidence per R8 / Codex's correction point 4) + Boundary A's
    SOFT warnings.

  - The gate's OUTPUT is a typed ``GateVerdict`` with status
    ``PASS`` / ``REFUSE`` / ``CLARIFY`` + always-populated
    ``reason`` + ``clarification_question`` when status=CLARIFY +
    surfaced ``soft_warnings`` from Boundary A.

  - The pipeline NEVER executes when ``status != PASS``.  This is
    R8's hard-block discipline.  PR-8 exposes the typed contract
    here via ``GateVerdict.is_pass`` / ``is_hard_block``; PR-10's
    orchestrator wiring binds it to executor dispatch.  Live
    enforcement is deferred to PR-10 per the plan's "End-to-end
    wiring" stage.

Why a hard-block gate
=====================

The PR-1 structural validator + the PR-4 contract check together
guarantee the DAG is type-legal and the binds match the L3 holes.
But neither knows whether the DAG actually answers the user's
question.  Example: L1 misroutes a "JPY OIS swap-spread vs UST 2s10s"
prompt to only sovereign_bonds → the Composer dutifully emits a
single-domain 2s10s shape that VALIDATES CLEAN — but the answer is
half the user's question.  Boundary B catches that by reading the
prompt + the English echo and refusing or asking a clarification.

Source-of-truth discipline (R8 + Codex point 4)
==============================================

The **original prompt** is the source of truth.  L1 decomposition is
**supplementary evidence**.  When they contradict, the prompt wins —
if L1 dropped a domain, the decomposition is already wrong.  The
prompt and the system message both make this explicit so the LLM
doesn't take L1's wrong decomposition as truth.

Refusal vs clarification
========================

  - ``CLARIFY`` is preferred when the gap is fixable by user input
    (e.g. composite-noun "5y5y" without a market — one precise
    question resolves it).
  - ``REFUSE`` is reserved for impossible-given-universe cases — no
    operator chain or selector binding could produce the requested
    quantity, OR the DAG plausibly answers a DIFFERENT question.

Both verdicts are hard-block; the difference is which downstream path
the orchestrator takes.

Two-layer split (mirrors PR-6 / PR-7)
====================================

The pure logic (prompt rendering, structured-output schema,
LLM-output transformer) lives in this module alongside the orchestrating
``CoverageGate`` class.  Tests target the pure layer first; session-
level tests mock the LLM via the LangChain
``with_structured_output(include_raw=True)`` contract.

Finance-blindness
=================

This module sits in ``orchestrator/open_dag/`` (agent layer).  It
imports from ``orchestrator.contracts`` (RouteDecision /
EconomicQuantity), ``orchestrator.open_dag`` siblings (AssemblyResult,
dag_echo).  It does NOT import from ``rates_agent/``.  The gate's
system prompt is finance-blind English; the per-call user message
carries the user's actual prompt (which may contain finance vocabulary
the gate reasons over).
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, List, Literal, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from orchestrator.contracts import EconomicQuantity, RouteDecision
from orchestrator.open_dag.contracts import BoundLeaf
from orchestrator.open_dag.dag_echo import (
    DagEcho,
    DagEchoError,
    build_dag_echo,
    render_dag_echo,
)
from shared.workflow.types import Workflow
from shared.workflow.validation_result import ValidationError

if TYPE_CHECKING:  # pragma: no cover
    from orchestrator.open_dag.assembler import AssemblyResult


logger = logging.getLogger(__name__)


# ============================================================================
# GATE VERDICT (the public output contract)
# ============================================================================


GateStatus = Literal["PASS", "REFUSE", "CLARIFY"]


class GateVerdict(BaseModel):
    """The Coverage Gate's verdict on an assembled DAG.

    Mirrors the contract in ``tmp/orchestration.md`` §PR-8:

      status: PASS | REFUSE | CLARIFY
      reason: str (always populated, audit trail)
      clarification_question: Optional[str] (populated iff CLARIFY)
      soft_warnings: list[str] = []

    Pipeline hard-block discipline (R8): the orchestrator (PR-10) MUST
    check ``verdict.is_pass`` before executing the DAG.  REFUSE +
    CLARIFY are both hard-block flavours; the difference is which
    user-facing message the orchestrator routes to next.

    PR-8A scope clarification (Codex F3+F4):
      - The hard-block **contract** is exposed here via ``is_pass``
        / ``is_hard_block``.  Live ENFORCEMENT (the executor refusing
        to run when ``is_pass`` is False) is wired in PR-10's
        orchestrator layer per the plan's "End-to-end wiring" stage;
        PR-8 ships the typed surface only.
      - The verdict's ``reason`` field is always populated for the
        audit trail.  Surfacing it in **lineage** (so the L6 answer
        template can echo the gate's verdict + reason before any
        downstream prose) is PR-9's lineage chain work — also
        documented in the plan's PR-9 acceptance criteria.

    Both deferrals are intentional: PR-8 establishes the contract +
    the LLM-driven verdict logic + the fail-closed discipline; PR-9
    threads the verdict through lineage; PR-10 binds it to executor
    dispatch.

    Frozen.  ``model_validator`` enforces the CLARIFY → question
    invariant + the PASS-must-not-have-clarification rule so a
    malformed verdict cannot be constructed at all.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: GateStatus
    reason: str = Field(..., min_length=1)
    clarification_question: Optional[str] = Field(
        default=None,
        description=(
            "Populated when status=CLARIFY.  Must be ONE precise "
            "question phrased the way a senior PM would phrase it — "
            "NOT free-form prose, NOT a list of options.  Per "
            "tmp/orchestration.md §PR-8 acceptance criterion 3."
        ),
    )
    soft_warnings: List[str] = Field(
        default_factory=list,
        description=(
            "SOFT warnings surfaced from Boundary A (PR-4) — typically "
            "free-form role / output-meaning mismatches between "
            "LeafRequest and BoundLeaf.  Surfaced here so the gate's "
            "audit trail records what biased its decision."
        ),
    )

    @model_validator(mode="after")
    def _validate_clarification_consistency(self) -> "GateVerdict":
        if self.status == "CLARIFY":
            if (
                self.clarification_question is None
                or not self.clarification_question.strip()
            ):
                raise ValueError(
                    "GateVerdict(status=CLARIFY) requires a non-empty "
                    "clarification_question.  Per PR-8 the gate's "
                    "CLARIFY verdict MUST be ONE precise question "
                    "(not free-form prose)."
                )
        else:  # PASS or REFUSE
            if self.clarification_question is not None:
                raise ValueError(
                    f"GateVerdict(status={self.status}) MUST NOT carry "
                    "a clarification_question — that field is only "
                    "valid when status=CLARIFY."
                )
        return self

    @property
    def is_pass(self) -> bool:
        """True iff the orchestrator may proceed to execution.  The
        hard-block discipline (R8): the executor must check this
        before kicking off; any False outcome routes back to the
        user-facing layer instead of executing the DAG."""
        return self.status == "PASS"

    @property
    def is_hard_block(self) -> bool:
        """True iff the orchestrator must NOT execute (REFUSE or
        CLARIFY).  Convenience alias for ``not is_pass``."""
        return self.status != "PASS"


# ============================================================================
# GATE LLM STRUCTURED OUTPUT (parsed via LangChain with_structured_output)
# ============================================================================


class _GateLLMOutput(BaseModel):
    """The Gate LLM's structured output before the post-LLM wrap.

    Mirrors ``GateVerdict`` minus the ``soft_warnings`` (which the
    code surfaces from the AssemblyResult's ValidationResult — the LLM
    does NOT author them).
    """

    model_config = ConfigDict(extra="forbid")

    status: GateStatus = Field(
        ...,
        description=(
            "PASS when the DAG's English echo faithfully answers the "
            "user's prompt; CLARIFY when one precise question would "
            "resolve a gap; REFUSE when no clarification helps."
        ),
    )
    reason: str = Field(
        ...,
        min_length=1,
        description=(
            "One short paragraph naming the specific signal that drove "
            "the verdict.  Always populated — audit trail."
        ),
    )
    clarification_question: Optional[str] = Field(
        default=None,
        description=(
            "Populated ONLY when status=CLARIFY.  ONE precise question "
            "phrased the way a senior PM would phrase it.  No multi-"
            "part questions, no lists of options."
        ),
    )

    @field_validator("reason")
    @classmethod
    def _strip_reason(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("reason must be non-empty")
        return stripped

    @field_validator("clarification_question")
    @classmethod
    def _strip_question(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        stripped = v.strip()
        return stripped or None


# ============================================================================
# PURE LOGIC — USER-MESSAGE RENDERING
# ============================================================================


def _format_decomposition_block(
    decomposition: Sequence[EconomicQuantity],
    adjustments: Sequence[str],
) -> str:
    """Render the L1 decomposition + the supervisor's adjustment notes.

    Per R8 + Codex's correction point 4: the decomposition is
    SUPPLEMENTARY evidence, NOT the source of truth.  The system
    prompt + the per-call message both call this out — the LLM
    must not treat the decomposition as gospel if the prompt
    contradicts it.

    The supervisor's adjustments (e.g. "decomposition implies domain X
    (entry Y) but routing domains are [...]. Possible under-scoped
    routing — Boundary B should treat as supplementary evidence.") are
    the gold signal for "L1 dropped a domain".  Surface them verbatim.
    """
    lines: List[str] = []
    if not decomposition:
        lines.append("(L1 produced no decomposition — supervisor may have routed CLARIFY)")
    else:
        lines.append(f"L1 DECOMPOSITION ({len(decomposition)} quantities — SUPPLEMENTARY EVIDENCE, NOT TRUTH):")
        for i, q in enumerate(decomposition, start=1):
            lines.append(f"  {i}. name={q.name!r}")
            lines.append(f"     domain_hint={q.domain_hint.value}")
            lines.append(f"     nl_description={q.nl_description!r}")
    if adjustments:
        lines.append("")
        lines.append(f"L1 NORMALISER ADJUSTMENTS ({len(adjustments)} — read carefully; these flag drift):")
        for i, adj in enumerate(adjustments, start=1):
            lines.append(f"  {i}. {adj}")
    return "\n".join(lines)


# Closed allowlist of ``ValidationError.detail`` keys safe to surface
# at L4.5.  Every key produced by the PR-4 contract_check is in this
# set (``field``, ``expected``, ``declared``, ``requested``); any
# future ``detail`` key the substrate adds is dropped here until it's
# audited and added explicitly.
#
# Per PR-8A Codex F2: the raw ``message`` text and the raw ``detail``
# dict can contain interpolated ``mcp_tool_name`` strings (the
# Assembler's f-string formatters include them — see assembler.py
# lines 705-825).  An allowlist + a redaction step is the only way
# to keep L4.5 primitive-blind without changing the substrate's
# message format.
_SAFE_WARNING_DETAIL_KEYS = frozenset({
    "field",
    "expected",
    "declared",
    "requested",
})


def _redacted_warning_view(warning: ValidationError) -> Dict[str, Any]:
    """Render one ``ValidationError`` (warning) as a gate-safe dict.

    Only ``code`` + ``severity`` + ``owner_layer`` + ``leaf_id`` +
    ``node_id`` + an allowlisted slice of ``detail`` are surfaced.
    The free-form ``message`` text and ``tool_name`` field are
    REDACTED entirely — primitive identities have been observed in
    them via the Assembler's f-string interpolation, and at L4.5 the
    gate must remain primitive-blind.

    For each allowlisted ``detail`` value the gate surfaces the
    closed-substrate string (e.g. ``ArtifactTypeName.SERIES.value`` →
    ``"Series"``).  These values come from PR-4 contract_check
    detail-dict construction (assembler.py lines 716-820) and never
    contain primitive identities.
    """
    safe_detail = {
        k: v for k, v in warning.detail.items()
        if k in _SAFE_WARNING_DETAIL_KEYS
    }
    return {
        "code": warning.code.value,
        "severity": warning.severity.value,
        "owner_layer": warning.owner_layer.value,
        "leaf_id": warning.leaf_id,
        "node_id": warning.node_id,
        "detail": safe_detail,
    }


def _format_warnings_block(warnings: Sequence[ValidationError]) -> str:
    """Render Boundary A's SOFT warnings (free-form role /
    output-meaning mismatches) for the gate's USER MESSAGE.

    PR-8A Codex F2: uses ``_redacted_warning_view`` so the gate-side
    prompt does NOT include the raw ``message`` text or
    ``tool_name`` — both have been observed to interpolate primitive
    identities at the substrate's f-string layer.  The gate gets the
    structural diagnostic (code + which closed-substrate field
    mismatched + the expected-vs-declared values) it needs to bias
    its verdict; primitive identities never cross this boundary.
    """
    if not warnings:
        return "BOUNDARY A SOFT WARNINGS: (none)"
    lines: List[str] = []
    lines.append(
        f"BOUNDARY A SOFT WARNINGS ({len(warnings)} — bias your "
        "verdict toward CLARIFY when relevant):"
    )
    for i, w in enumerate(warnings, start=1):
        view = _redacted_warning_view(w)
        lines.append(
            f"  {i}. code={view['code']} | "
            f"owner_layer={view['owner_layer']} | "
            f"leaf_id={view['leaf_id']} | "
            f"node_id={view['node_id']} | "
            f"detail={view['detail']}"
        )
    return "\n".join(lines)


def warnings_to_string_list(
    warnings: Sequence[ValidationError],
) -> List[str]:
    """Convert Boundary A's structured warnings into the
    ``soft_warnings: list[str]`` field on ``GateVerdict``.

    PR-8A Codex F2: each entry uses ``_redacted_warning_view`` so the
    verdict's audit trail does NOT contain the raw ``message`` text
    or ``tool_name``.  The trail still names the diagnostic kind
    (E_TYPE_MISMATCH / E_ROLE_DISCRIMINANT_MISMATCH / etc.), the
    leaf, and the structural mismatch (expected vs declared on the
    relevant closed-substrate field) — enough for downstream lineage
    (PR-9) to act on without leaking primitive identity.
    """
    out: List[str] = []
    for w in warnings:
        view = _redacted_warning_view(w)
        detail_str = (
            ", ".join(f"{k}={v}" for k, v in view["detail"].items())
            if view["detail"] else ""
        )
        suffix = f" ({detail_str})" if detail_str else ""
        out.append(
            f"{view['code']} on leaf {view['leaf_id']} / "
            f"node {view['node_id']}{suffix}"
        )
    return out


def render_gate_user_message(
    *,
    user_prompt: str,
    dag_echo_text: str,
    decomposition: Sequence[EconomicQuantity],
    adjustments: Sequence[str],
    warnings: Sequence[ValidationError],
) -> str:
    """Render the per-call user message the Gate LLM sees.

    The user prompt is surfaced VERBATIM — that's the source of truth.
    The L1 decomposition + adjustments are labelled as supplementary
    evidence.  Boundary A warnings are surfaced as soft bias.

    Deterministic (sorted keys / stable ordering) so the prompt cache
    hits on identical inputs.
    """
    lines: List[str] = []
    lines.append("ORIGINAL USER PROMPT (SOURCE OF TRUTH — verbatim):")
    lines.append('"""')
    lines.append(user_prompt)
    lines.append('"""')
    lines.append("")
    lines.append(dag_echo_text)
    lines.append("")
    lines.append(_format_decomposition_block(decomposition, adjustments))
    lines.append("")
    lines.append(_format_warnings_block(warnings))
    lines.append("")
    lines.append(
        "Emit a structured GateVerdict: status PASS / REFUSE / CLARIFY "
        "+ reason + clarification_question (ONLY when CLARIFY).  Hard-"
        "block: any verdict other than PASS halts execution."
    )
    return "\n".join(lines)


# ============================================================================
# COVERAGE GATE — orchestrating class
# ============================================================================


class CoverageGate:
    """The PR-8 Boundary B Coverage Gate.

    Constructed once per session with an LLM model name + sampling
    params.  Exposes:

      - ``open()`` / ``close()`` lifecycle — build / drop the cached
        SystemMessage + structured-output ChatAnthropic instance.
      - ``async check(user_prompt, assembly_result, route_decision)``
        — returns a ``GateVerdict``.  Bounded by ``timeout_s``; LLM
        exceptions / timeouts convert to a structured REFUSE verdict
        (hard-block by default — fail safe).

    Hard-block invariant: every code path returns a ``GateVerdict``.
    The orchestrator (PR-10) checks ``verdict.is_pass`` before
    executing.  There is NO path that returns "we couldn't decide,
    proceed anyway" — when in doubt, the gate REFUSES (R8).
    """

    def __init__(
        self,
        *,
        model_name: str = "claude-sonnet-4-5",
        temperature: float = 0.0,
        max_tokens: int = 1024,
    ) -> None:
        self._model_name = model_name
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._is_open: bool = False
        self._cached_system_message: Optional[Any] = None
        self._gate_model: Optional[Any] = None

    # ------------------------------------------------------------------
    # LIFECYCLE
    # ------------------------------------------------------------------

    def open(self) -> None:
        """Build the cached SystemMessage + ChatAnthropic instance.

        Idempotent.  Imports LangChain lazily so the module loads even
        when the optional Anthropic stack is unavailable (tests use
        ``_make_gate_with_state`` to bypass).
        """
        if self._is_open:
            return

        from langchain_anthropic import ChatAnthropic
        from langchain_core.messages import SystemMessage

        from orchestrator.prompts import COVERAGE_GATE_SYSTEM_PROMPT

        self._cached_system_message = SystemMessage(
            content=[
                {
                    "type": "text",
                    "text": COVERAGE_GATE_SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        )
        self._gate_model = ChatAnthropic(
            model=self._model_name,
            temperature=self._temperature,
            max_tokens=self._max_tokens,
        ).with_structured_output(_GateLLMOutput, include_raw=True)

        self._is_open = True
        logger.info(
            "CoverageGate ready (model=%s, max_tokens=%d)",
            self._model_name, self._max_tokens,
        )

    def close(self) -> None:
        """Drop the cached model + system message.  Idempotent."""
        self._is_open = False
        self._cached_system_message = None
        self._gate_model = None

    # ------------------------------------------------------------------
    # CHECK
    # ------------------------------------------------------------------

    async def check(
        self,
        *,
        user_prompt: str,
        assembly_result: "AssemblyResult",
        route_decision: RouteDecision,
        leaves: Sequence[BoundLeaf],
        timeout_s: float = 10.0,
    ) -> GateVerdict:
        """Evaluate the assembled DAG against the user's original
        prompt.  Returns a typed ``GateVerdict``.

        Caller contract
        ---------------
        - ``assembly_result.status`` MUST be CLEAN (so
          ``assembly_result.workflow`` is not None).  When the
          Assembler refused, the orchestrator skips the gate entirely
          and surfaces the refusal directly.
        - ``leaves`` is the list of BoundLeaf the Assembler used for
          substitution (the gate reads declared_semantic_role and
          declared_output_meaning per leaf via the DagEcho).
        - ``route_decision`` is the supervisor's L1 output;
          ``decomposition`` + ``adjustments`` are passed through to
          the gate's prompt as supplementary evidence.

        Failure modes
        -------------
        - Gate not open → ``RuntimeError``.
        - AssemblyResult is not CLEAN → ``ValueError`` (caller bug;
          the gate should not be asked to evaluate a REFUSED
          assembly).
        - LLM timeout / exception → returns a REFUSE GateVerdict
          (fail-safe per R8 — never returns PASS on uncertainty).
        - LLM parsing error → returns a REFUSE GateVerdict.
        - LLM authors a malformed verdict (e.g. CLARIFY without a
          question) → Pydantic's model_validator on GateVerdict
          raises; the gate catches and converts to REFUSE.
        """
        from langchain_core.messages import HumanMessage
        from orchestrator.open_dag.assembler import (
            AssemblyResult,
            AssemblyStatus,
        )

        if not self._is_open:
            raise RuntimeError(
                "CoverageGate is not open.  Call `gate.open()` before "
                "check()."
            )
        if self._gate_model is None or self._cached_system_message is None:
            raise RuntimeError(
                "CoverageGate is open but model / system message are "
                "missing — internal state corrupted."
            )
        if not isinstance(assembly_result, AssemblyResult):
            raise TypeError(
                "assembly_result must be an AssemblyResult instance"
            )
        if assembly_result.status != AssemblyStatus.CLEAN:
            raise ValueError(
                "CoverageGate.check requires an AssemblyResult with "
                "status=CLEAN.  REFUSED assemblies are handled by the "
                "orchestrator's refusal path, not by the gate."
            )
        workflow: Workflow = assembly_result.workflow  # type: ignore[assignment]

        # Surface Boundary A's SOFT warnings into both the prompt and
        # the verdict's audit-trail field.  Computed BEFORE the echo
        # so the fail-closed branch below can still attach warnings.
        warnings = assembly_result.validation_result.warnings
        soft_warnings_str = warnings_to_string_list(warnings)

        # Build the DAG echo (deterministic, primitive-blind).
        # PR-8A Codex F1: an incoherent (workflow, leaves) pair raises
        # DagEchoError — convert to a REFUSE verdict so the gate's
        # fail-closed discipline catches the caller-bug case the same
        # way it catches LLM failures.
        try:
            echo: DagEcho = build_dag_echo(workflow, leaves)
        except DagEchoError as exc:
            logger.warning(
                "CoverageGate: build_dag_echo refused — %s", exc,
            )
            return GateVerdict(
                status="REFUSE",
                reason=(
                    f"CoverageGate: leaf coverage mismatch between the "
                    f"assembled workflow and the supplied BoundLeaf "
                    f"list — {exc}.  Refusing by default per the hard-"
                    "block discipline (R8)."
                ),
                soft_warnings=soft_warnings_str,
            )
        echo_text: str = render_dag_echo(echo)

        user_text = render_gate_user_message(
            user_prompt=user_prompt,
            dag_echo_text=echo_text,
            decomposition=route_decision.decomposition,
            adjustments=route_decision.adjustments,
            warnings=warnings,
        )

        messages = [
            self._cached_system_message,
            HumanMessage(content=user_text),
        ]

        try:
            result = await asyncio.wait_for(
                self._gate_model.ainvoke(messages),
                timeout=timeout_s,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "CoverageGate LLM timed out after %.1fs", timeout_s,
            )
            return GateVerdict(
                status="REFUSE",
                reason=(
                    f"CoverageGate LLM call timed out after {timeout_s}s "
                    "without producing a structured verdict; refusing "
                    "by default per the hard-block discipline."
                ),
                soft_warnings=soft_warnings_str,
            )
        except Exception as exc:
            logger.exception("CoverageGate LLM call failed")
            return GateVerdict(
                status="REFUSE",
                reason=(
                    f"CoverageGate LLM call failed: "
                    f"{type(exc).__name__}: {exc}.  Refusing by default."
                ),
                soft_warnings=soft_warnings_str,
            )

        raw = result.get("raw") if isinstance(result, dict) else None
        parsed = result.get("parsed") if isinstance(result, dict) else None
        parsing_error = (
            result.get("parsing_error") if isinstance(result, dict) else None
        )
        if raw is not None:
            _log_usage("coverage_gate.check", raw)

        if parsed is None:
            return GateVerdict(
                status="REFUSE",
                reason=(
                    f"CoverageGate LLM did not produce a valid "
                    f"_GateLLMOutput; parsing_error={parsing_error!r}.  "
                    "Refusing by default."
                ),
                soft_warnings=soft_warnings_str,
            )

        # Assemble the final GateVerdict.  If the LLM authored a
        # malformed verdict (e.g. CLARIFY without a question), the
        # GateVerdict model_validator raises; convert to REFUSE.
        try:
            return GateVerdict(
                status=parsed.status,
                reason=parsed.reason,
                clarification_question=parsed.clarification_question,
                soft_warnings=soft_warnings_str,
            )
        except Exception as exc:
            return GateVerdict(
                status="REFUSE",
                reason=(
                    f"CoverageGate LLM emitted a structurally invalid "
                    f"verdict: {type(exc).__name__}: {exc}.  Refusing "
                    "by default."
                ),
                soft_warnings=soft_warnings_str,
            )


# ============================================================================
# USAGE LOGGING
# ============================================================================


def _log_usage(label: str, response) -> None:
    """Mirror ``orchestrator.domain_agent._log_usage`` so gate calls
    show up in the same observability format."""
    meta = getattr(response, "usage_metadata", None)
    if not meta:
        return
    if not isinstance(meta, dict):
        try:
            meta = dict(meta)
        except (TypeError, ValueError):
            return
    details = meta.get("input_token_details", {}) or {}
    logger.info(
        "[%s] tokens input=%s output=%s cache_create=%s cache_read=%s",
        label,
        meta.get("input_tokens"),
        meta.get("output_tokens"),
        details.get("cache_creation"),
        details.get("cache_read"),
    )


__all__ = [
    "GateStatus",
    "GateVerdict",
    "CoverageGate",
    "warnings_to_string_list",
    "render_gate_user_message",
]
