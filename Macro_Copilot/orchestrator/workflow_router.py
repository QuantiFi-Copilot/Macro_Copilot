"""orchestrator/workflow_router.py — LLM-driven workflow-template router.

The new routing layer that sits parallel to ``orchestrator.supervisor``.
The existing Supervisor routes user queries to a DOMAIN
(sovereign_bonds vs ois) and the domain agent runs a ReAct loop over
its primitive MCP tools.  This router is the analog for WORKFLOW
templates — given a user prompt, it picks ONE registered template and
binds its slots from the user's words, then optionally executes the
bound workflow.

Two-layer surface
-----------------
    route(user_message)    -> WorkflowRouteDecision
    execute(user_message)  -> WorkflowExecutionResult  (route + run)

``route`` is the routing-only path used during the manual gauntlet
testing phase ("type a prompt, see which template the LLM picks").
``execute`` adds the bind + execute step (delegates to
``rates_agent.workflows._runner.run_template``) so a single call goes
end-to-end on a ROUTE-action decision.

Guardrails enforced at the code level (not just the prompt)
-----------------------------------------------------------
- Pydantic structured output: the LLM cannot return free-form prose
  in place of a routing decision.
- Post-flight ``_normalise_workflow_route``: unknown template_id,
  slot type mismatch, or missing required slot → demote to
  ``CLARIFY`` and record the adjustment.  Mirrors the
  ``_normalise_route_decision`` discipline in
  ``orchestrator/supervisor.py``.
- The router has NO MCP tools bound.  It physically cannot call a
  primitive.

Resolver-injection design
-------------------------
``execute`` accepts an optional ``primitive_resolver`` so tests can
swap in a synthetic resolver without monkey-patching production
code.  Production callers (CLI, future session integration) leave
the parameter at its default so the rates resolver is used.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

# Lazy-import langchain inside ``WorkflowRouter.__init__`` so this
# module imports cleanly in environments where langchain isn't
# installed (e.g. minimal test envs).  The normaliser
# ``_normalise_workflow_route`` is langchain-free and importable
# everywhere — tests of the normalisation logic work without
# the LLM dependency.

from orchestrator.workflow_contracts import (
    WorkflowExecutionResult,
    WorkflowRouteAction,
    WorkflowRouteDecision,
)
from orchestrator.workflow_prompts import render_router_system_prompt
from rates_agent.workflows._runner import (
    run_template,
    run_template_with_resolver,
)
from shared.workflow import (
    PrimitiveResolver,
    SlotBindingError,
    get_template,
    known_template_ids,
)
from shared.workflow.template_registry import TemplateRegistryError

logger = logging.getLogger("orchestrator.workflow_router")


# ===========================================================================
# USAGE LOGGING (mirrors the Supervisor's helper)
# ===========================================================================


def _log_usage(label: str, response) -> None:
    """Log token usage + cache hits from an Anthropic response.
    Same format as ``orchestrator/supervisor.py::_log_usage`` so
    observability scaling cleanly across the two routers."""
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


# ===========================================================================
# WORKFLOW ROUTER
# ===========================================================================


class WorkflowRouter:
    """LLM-driven router that maps a user prompt to one workflow
    template + slot bindings.

    Constructed once per session.  Holds no MCP tools and no domain
    knowledge beyond the rendered template catalogue baked into the
    system prompt.

    Parameters
    ----------
    model_name : str
        Anthropic model id (e.g. ``"claude-sonnet-4-20250514"``).
    temperature : float
        LLM temperature.  Default 0.0 for deterministic routing.
    max_tokens : int
        Max output tokens.  Default 2048 — enough for a routing
        decision with a fully-bound slot dict for either V1 template.
    system_prompt : Optional[str]
        Override the default system prompt (used by tests that want
        to pin a specific catalogue snapshot).  Production callers
        leave this as ``None`` so ``render_router_system_prompt()``
        is called and the live catalogue is embedded.
    """

    def __init__(
        self,
        model_name: str,
        temperature: float = 0.0,
        max_tokens: int = 2048,
        *,
        system_prompt: Optional[str] = None,
    ):
        self._model_name = model_name
        self._temperature = temperature
        self._max_tokens = max_tokens

        # Lazy import so the module imports without langchain in test
        # environments that don't need the LLM dependency.  Same
        # discipline as the per-domain MCP servers' lazy DB-engine
        # acquisition.
        from langchain_core.messages import SystemMessage

        from orchestrator.llm_factory import LlmRole, make_chat_model

        # Build the system prompt once at construction time.  The
        # registry is process-wide; in V1 there is no hot-swap path,
        # so a one-shot render is correct.  Tests pin a specific
        # catalogue snapshot via the ``system_prompt`` kwarg.
        prompt_text = (
            system_prompt
            if system_prompt is not None
            else render_router_system_prompt()
        )
        self._cached_system_message = SystemMessage(
            content=[
                {
                    "type": "text",
                    "text": prompt_text,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        )

        # Base model — no tools bound.  The router has structured-
        # output enforcement; the LLM cannot smuggle free-form prose
        # in place of a decision.  Single LLM chokepoint (P10); KI-09:
        # the factory strips temperature for no-sampling models.
        self._route_model = make_chat_model(
            role=LlmRole.WORKFLOW_ROUTER,
            model_name=model_name,
            temperature=temperature,
            max_tokens=max_tokens,
            structured_output=WorkflowRouteDecision,
            include_raw=True,
        )

    # ------------------------------------------------------------------
    # ROUTE
    # ------------------------------------------------------------------

    async def route(self, user_message: str) -> WorkflowRouteDecision:
        """Decide which template should answer the user's prompt.

        Returns a ``WorkflowRouteDecision`` with:
          - action: ``ROUTE`` / ``OUT_OF_SCOPE`` / ``CLARIFY``
          - template_id + slot_values: populated when ``ROUTE``
          - rationale: one-sentence log trail
          - clarification_question: populated when ``CLARIFY``
          - adjustments: post-hoc normalisation notes

        Errors are logged + propagated; structured-output failures
        raise (caller is responsible for wrapping in a session-level
        error event).
        """
        # Lazy-import here too so test environments without langchain
        # can still import this module to exercise the normaliser.
        from langchain_core.messages import HumanMessage

        messages = [
            self._cached_system_message,
            HumanMessage(content=user_message),
        ]

        try:
            result = await self._route_model.ainvoke(messages)
        except Exception:
            logger.exception("WorkflowRouter route call failed")
            raise

        raw = result.get("raw") if isinstance(result, dict) else None
        parsed: Optional[WorkflowRouteDecision] = (
            result.get("parsed") if isinstance(result, dict) else None
        )
        parsing_error = (
            result.get("parsing_error")
            if isinstance(result, dict) else None
        )

        if raw is not None:
            _log_usage("workflow_router.route", raw)

        if parsed is None:
            raise RuntimeError(
                f"WorkflowRouter failed to produce a valid "
                f"WorkflowRouteDecision: {parsing_error!r}"
            )

        normalised = _normalise_workflow_route(parsed)
        logger.info(
            "WorkflowRouter route: action=%s template_id=%s rationale=%r",
            normalised.action.value,
            normalised.template_id,
            normalised.rationale,
        )
        return normalised

    # ------------------------------------------------------------------
    # EXECUTE (route + bind + run)
    # ------------------------------------------------------------------

    async def execute(
        self,
        user_message: str,
        *,
        engine: Any = None,
        primitive_resolver: Optional[PrimitiveResolver] = None,
    ) -> WorkflowExecutionResult:
        """Route + execute end-to-end.

        On ``ROUTE`` action, delegates to
        ``rates_agent.workflows._runner.run_template`` (or the
        resolver-injected variant when a synthetic resolver is
        passed) and packages both the routing decision and the
        execution envelope into a ``WorkflowExecutionResult``.

        On ``CLARIFY`` / ``OUT_OF_SCOPE`` actions, the execution
        field is ``None`` — there's nothing to run.

        Parameters
        ----------
        user_message : str
            The prompt to route + execute.
        engine :
            Optional SQLAlchemy engine; passed through to the runner.
            Production callers pass a live engine (or rely on the
            runner's lazy-engine path).  Tests with synthetic
            fetchers can pass ``None``.
        primitive_resolver :
            Optional resolver override.  Production = ``None`` so the
            rates resolver is injected via ``run_template``; tests
            pass synthetic resolvers via ``run_template_with_resolver``.
        """
        decision = await self.route(user_message)
        if decision.action != WorkflowRouteAction.ROUTE:
            return WorkflowExecutionResult(route=decision, execution=None)

        if primitive_resolver is None:
            envelope = run_template(
                decision.template_id,
                decision.slot_values,
                engine=engine,
            )
        else:
            envelope = run_template_with_resolver(
                decision.template_id,
                decision.slot_values,
                engine=engine,
                primitive_resolver=primitive_resolver,
            )
        return WorkflowExecutionResult(route=decision, execution=envelope)


# ===========================================================================
# POST-FLIGHT NORMALISATION
# ===========================================================================
#
# The structured-output API doesn't fully prevent LLM mistakes:
#  - LLM picks a template_id that's not in the catalogue
#  - LLM returns slot_values for action=CLARIFY / OUT_OF_SCOPE
#  - LLM returns the wrong slot type (e.g. str instead of float)
#  - LLM omits a required slot
#  - LLM populates ``adjustments`` (it's a post-hoc, code-only field)
#
# Mirrors the discipline of ``orchestrator.supervisor::
# _normalise_route_decision``: each rewrite is logged AND recorded on
# the returned decision's ``adjustments`` list so eval harnesses /
# debug panels can detect drift.


def _normalise_workflow_route(
    decision: WorkflowRouteDecision,
) -> WorkflowRouteDecision:
    """Repair a raw LLM-emitted ``WorkflowRouteDecision`` to enforce
    internal consistency.  Returns a new decision with ``adjustments``
    listing every rewrite (empty list = clean LLM output)."""
    action = decision.action
    template_id = decision.template_id
    slot_values: Dict[str, Any] = dict(decision.slot_values or {})
    clarification_question = decision.clarification_question

    # ``adjustments`` is a post-hoc, code-generated field.  Discard
    # anything the LLM tried to write here so only real notes from
    # this function survive.
    adjustments: list[str] = []

    # ------------------------------------------------------------------
    # Step 1: branch on action; enforce shape consistency.
    # ------------------------------------------------------------------
    if action == WorkflowRouteAction.OUT_OF_SCOPE:
        if template_id is not None:
            note = (
                f"out_of_scope decision carried template_id="
                f"{template_id!r}; clearing"
            )
            logger.warning("WorkflowRouter: %s", note)
            adjustments.append(note)
            template_id = None
        if slot_values:
            note = (
                f"out_of_scope decision carried slot_values "
                f"({sorted(slot_values.keys())}); clearing"
            )
            logger.warning("WorkflowRouter: %s", note)
            adjustments.append(note)
            slot_values = {}
        if clarification_question is not None:
            adjustments.append(
                "out_of_scope decision carried clarification_question; clearing"
            )
            clarification_question = None

    elif action == WorkflowRouteAction.CLARIFY:
        if template_id is not None:
            note = (
                f"clarify decision carried template_id="
                f"{template_id!r}; clearing"
            )
            logger.warning("WorkflowRouter: %s", note)
            adjustments.append(note)
            template_id = None
        if slot_values:
            note = (
                f"clarify decision carried slot_values "
                f"({sorted(slot_values.keys())}); clearing"
            )
            logger.warning("WorkflowRouter: %s", note)
            adjustments.append(note)
            slot_values = {}
        if (
            clarification_question is None
            or not clarification_question.strip()
        ):
            note = (
                "clarify decision missing clarification_question; "
                "synthesizing fallback"
            )
            logger.warning("WorkflowRouter: %s", note)
            adjustments.append(note)
            clarification_question = (
                "Could you clarify which workflow you want? "
                "I have templates for event studies and for "
                "regime-conditioned relationship analyses."
            )

    elif action == WorkflowRouteAction.ROUTE:
        # The interesting case — must have a known template_id and
        # bindable slot_values.

        # 1a. template_id must be in the catalogue.
        if template_id is None:
            note = "route decision missing template_id; demoting to clarify"
            logger.warning("WorkflowRouter: %s", note)
            adjustments.append(note)
            return _force_clarify(
                rationale=decision.rationale,
                adjustments=adjustments,
                clarification_question=(
                    "Which workflow should I run for this question?"
                ),
            )
        known = set(known_template_ids())
        if template_id not in known:
            note = (
                f"unknown template_id={template_id!r}; demoting to "
                f"clarify.  Known: {sorted(known)}"
            )
            logger.warning("WorkflowRouter: %s", note)
            adjustments.append(note)
            return _force_clarify(
                rationale=decision.rationale,
                adjustments=adjustments,
                clarification_question=(
                    f"I don't have a template called {template_id!r}.  "
                    f"Available templates: {sorted(known)}.  Which "
                    "workflow did you want?"
                ),
            )

        # 1b. clarification_question doesn't belong on a route action.
        if clarification_question is not None:
            adjustments.append(
                "route decision carried clarification_question; clearing"
            )
            clarification_question = None

        # 1c. Bindable slot_values: dry-run ``template.bind`` and
        #     normalise to CLARIFY on failure (preserves the
        #     bind-time error message for the user-facing follow-up).
        try:
            template = get_template(template_id)
        except TemplateRegistryError as exc:
            # Should be unreachable given the catalogue check above,
            # but defensive in case the registry was mutated mid-call.
            adjustments.append(
                f"template registry rejected {template_id!r} mid-route: "
                f"{exc}"
            )
            return _force_clarify(
                rationale=decision.rationale,
                adjustments=adjustments,
                clarification_question=(
                    f"Internal: template {template_id!r} disappeared "
                    "from the registry between route and bind.  "
                    "Please restate your question."
                ),
            )
        try:
            template.bind(slot_values)
        except SlotBindingError as exc:
            note = f"bind failed: {exc}"
            logger.warning("WorkflowRouter: %s", note)
            adjustments.append(note)
            return _force_clarify(
                rationale=decision.rationale,
                adjustments=adjustments,
                clarification_question=(
                    f"I couldn't bind the {template_id!r} template's "
                    f"slots from your question: {exc}.  Could you "
                    "restate the question with the missing detail?"
                ),
            )

    return WorkflowRouteDecision(
        action=action,
        template_id=template_id,
        slot_values=slot_values,
        rationale=decision.rationale,
        clarification_question=clarification_question,
        adjustments=adjustments,
    )


def _force_clarify(
    *,
    rationale: str,
    adjustments: list[str],
    clarification_question: str,
) -> WorkflowRouteDecision:
    """Build a CLARIFY decision with the supplied rationale +
    clarification_question + adjustments.  Used by the normaliser to
    demote a malformed ROUTE decision."""
    return WorkflowRouteDecision(
        action=WorkflowRouteAction.CLARIFY,
        template_id=None,
        slot_values={},
        rationale=rationale,
        clarification_question=clarification_question,
        adjustments=adjustments,
    )


__all__ = [
    "WorkflowRouter",
]
