"""orchestrator.open_dag.answer — PR-9 L6 answer renderer.

The L6 lane.  Reads:

  - the original user prompt (verbatim),
  - the IntentChain (PR-9 — what the pipeline understood),
  - the executed terminal artifact (the number / Series / Panel
    PR-10's executor produced) — abstracted here as a free-form
    string the orchestrator pre-formats,
  - the substrate Lineage (PR-1 — content-addressed compute chain).

Emits:

  1. **Intent echo** (paragraph 1) — "Here is what I understood and
     built": Decomposed → Pulled → Wired.
  2. **Answer** (paragraph 2) — the number + a senior-PM-style
     sentence around it.  The LLM authors this from the executed
     result + intent chain.
  3. **Provenance footer** — per-leaf tool + domain + params +
     lineage hash.

R9 discipline (the most important rule)
========================================

  The lineage hash is in the PROVENANCE FOOTER, NEVER in the
  headline.  It is **reproducibility, NOT correctness**.  The intent
  echo is what protects correctness — wrong understanding shows up
  before a wrong-confident number ever does.

Three discipline tests in PR-9's test file pin this:
  - the renderer's footer template includes the hash; the headline
    + answer body explicitly do NOT.
  - the system prompt instructs the LLM never to present the hash as
    a correctness seal.
  - a grep test asserts the hash is rendered inside the FOOTER block
    only.

Refusal / clarification short-circuit
======================================

When the gate verdict is REFUSE or CLARIFY, the L6 layer does NOT
produce an answer.  Two pure functions handle the routing:

  - ``render_clarification(intent_chain)`` — surface the gate's
    one-precise-question to the user.
  - ``render_refusal(intent_chain)`` — surface the gate's reason
    string.

Both emit deterministic text without an LLM call — there's no
"writing prose" needed when the gate has already authored the
one-precise message.

Two-layer split (mirrors PR-6 / PR-7 / PR-8)
============================================

Pure layer:
  - ``render_intent_echo(intent_chain)`` — Paragraph 1.
  - ``render_provenance_footer(intent_chain, lineage_head_hash)`` —
    Footer.
  - ``render_clarification`` / ``render_refusal``.

LLM layer:
  - ``AnswerRenderer`` class.  ``open()`` / ``close()`` lifecycle.
    ``async render(intent_chain, executed_summary, lineage_head_hash)``
    invokes the model with the structured-output schema
    ``_AnswerLLMOutput`` and produces the final markdown.

Finance-blindness
=================

This module sits in ``orchestrator/open_dag/``.  No imports from
``rates_agent/``.  The intent echo + footer surface domain
vocabulary + the LLM-authored free-form English already in the
IntentChain — the renderer doesn't add finance opinion.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING, Any, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from orchestrator.open_dag.intent_chain import (
    IntentChain,
    SelectorIntentRecord,
)

if TYPE_CHECKING:  # pragma: no cover
    from shared.artifacts.lineage import Lineage


logger = logging.getLogger(__name__)

# Campaign FM-1 — unfilled-template detector.  Matches a square-bracket
# span whose content contains a space or a pipe (template-ese like
# "[value from the Series last observation]" or "[if |z| < 1: ...]")
# — trader prose never legitimately carries those; single-token
# brackets (e.g. "[sic]") deliberately do NOT match.
_UNFILLED_PLACEHOLDER_RE = re.compile(r"\[[^\]\n]*[ |][^\]\n]*\]")


# ============================================================================
# ANSWER LLM OUTPUT — structured-output schema
# ============================================================================


class _AnswerLLMOutput(BaseModel):
    """The Answer LLM's structured output.

    The model authors ONLY the prose body of the answer paragraph —
    the intent echo + provenance footer are deterministic code blocks
    the renderer assembles around it.  Keeping the LLM's surface
    narrow protects R9: the LLM cannot relocate the hash from the
    footer to the headline because it doesn't render the headline /
    footer at all.
    """

    model_config = ConfigDict(extra="forbid")

    answer_prose: str = Field(
        ...,
        min_length=1,
        description=(
            "One paragraph in trader lingo with the number embedded.  "
            "The renderer prepends the intent echo and appends the "
            "provenance footer; this field is JUST the middle "
            "paragraph.  Do NOT mention the lineage hash here — it "
            "is reproducibility-only and belongs in the footer."
        ),
    )

    @field_validator("answer_prose")
    @classmethod
    def _strip(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("answer_prose must be non-empty")
        return stripped


# ============================================================================
# RENDERED ANSWER — structured render output (consolidation target #3)
# ============================================================================


class RenderedAnswer(BaseModel):
    """The structured output of one L6 render.

    ``markdown`` is the full assembled document (intent echo + prose +
    provenance footer on PASS; the refusal / clarification message
    otherwise) — byte-identical to what ``AnswerRenderer.render``
    returned before this type existed, so every existing consumer and
    test keeps working through the delegating ``render()``.

    ``answer_prose`` is JUST the LLM-authored middle paragraph — the
    concise PM-facing sentence with the number embedded.  Populated
    only when ``kind == 'answer'``.  The Ask chat stream emits THIS as
    the token content (the clean box: concise summary, no L6 dump);
    the intent echo + provenance stay available structurally (the
    workflow_result event, the persisted workspace, the audit
    surfaces) instead of as a prose wall.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    markdown: str = Field(..., min_length=1)
    answer_prose: Optional[str] = None
    kind: Literal[
        "answer", "refusal", "clarification", "failsafe", "inconsistent",
    ] = Field(...)


# ============================================================================
# PURE LOGIC — INTENT ECHO
# ============================================================================


def _format_decomposition_bullet(chain: IntentChain) -> str:
    """Render the 'Decomposed: ...' bullet from the router record."""
    decomp = chain.router.decomposition
    if not decomp:
        return "Decomposed: (L1 produced no decomposition.)"
    parts = [f"{q.name} ({q.domain_hint.value})" for q in decomp]
    return "Decomposed: " + ", ".join(parts) + "."


def _format_pulled_bullet(chain: IntentChain) -> str:
    """Render the 'Pulled: ...' bullet from the selector records.

    PR-9: the selector records carry ``declared_semantic_role`` and
    ``bound_tool_name`` + ``domain``.  The trader-lingo phrasing
    uses the role + the domain (e.g. "spread_level from sovereign_bonds
    via calculate_curve_spread_tool").  Primitive name IS surfaced
    here — the L6 footer / 'Pulled' bullet is the documented exception
    to L3-primitive-blindness.
    """
    if not chain.selectors:
        return "Pulled: (no leaves were bound — the Composer may have refused.)"
    parts: List[str] = []
    for s in chain.selectors:
        if s.is_refusal:
            parts.append(f"{s.leaf_id}: REFUSED ({s.refusal})")
        else:
            parts.append(
                f"{s.declared_semantic_role} from {s.domain} via "
                f"{s.bound_tool_name}"
            )
    return "Pulled: " + "; ".join(parts) + "."


def _format_wired_bullet(chain: IntentChain) -> str:
    """Render the 'Wired: ...' bullet from the composer record.

    Operator names + terminal type are the structural summary L3
    produced.  When the composer refused, surface the refusal text.
    """
    if chain.composer.is_refusal:
        return f"Wired: Composer refused — {chain.composer.refusal}"
    ops = chain.composer.operator_names
    if not ops:
        # Degenerate 1-leaf lookup — no operators.
        return (
            "Wired: 1-leaf shape (terminal is the bound primitive — "
            "no operator chain)."
        )
    terminal = chain.composer.terminal_operator_name or "(leaf)"
    artifact = chain.composer.terminal_artifact_type or "(leaf artifact)"
    return (
        f"Wired: {' -> '.join(ops)}; terminal {terminal} -> {artifact}."
    )


def render_intent_echo(chain: IntentChain) -> str:
    """Render the intent echo paragraph (paragraph 1 of the answer).

    Format per §PR-9:

        Here is what I understood and built:
          • Decomposed: ...
          • Pulled: ...
          • Wired: ...

    Deterministic — same chain produces byte-identical output.
    """
    lines: List[str] = []
    lines.append("Here is what I understood and built:")
    lines.append(f"  • {_format_decomposition_bullet(chain)}")
    lines.append(f"  • {_format_pulled_bullet(chain)}")
    lines.append(f"  • {_format_wired_bullet(chain)}")
    return "\n".join(lines)


# ============================================================================
# PURE LOGIC — PROVENANCE FOOTER
# ============================================================================


def render_provenance_footer(
    chain: IntentChain,
    lineage_head_hash: str,
) -> str:
    """Render the provenance footer (last block of the answer).

    Format per §PR-9:

        Provenance:
          • Leaf A: <tool> · <domain> · <params>
          • Leaf B: <tool> · <domain> · <params>
          • Lineage hash: <head_hash>  (reproducibility, not correctness)

    The "(reproducibility, not correctness)" suffix is R9's discipline
    surfaced at the rendering layer — the user reads it adjacent to
    the hash so the affordance is unambiguous.
    """
    lines: List[str] = []
    lines.append("Provenance:")
    for s in chain.selectors:
        if s.is_refusal:
            lines.append(f"  • {s.leaf_id}: REFUSED ({s.refusal})")
        else:
            params_str = (
                ", ".join(f"{k}={v!r}" for k, v in sorted(s.bound_params.items()))
                if s.bound_params else "(no params)"
            )
            lines.append(
                f"  • {s.leaf_id}: {s.bound_tool_name} · {s.domain} · "
                f"{params_str}"
            )
    lines.append(
        f"  • Lineage hash: {lineage_head_hash}  "
        "(reproducibility, not correctness)"
    )
    return "\n".join(lines)


# ============================================================================
# PURE LOGIC — REFUSAL / CLARIFICATION SHORT-CIRCUIT
# ============================================================================


def render_clarification(chain: IntentChain) -> str:
    """Render the gate's clarification message — when the verdict is
    CLARIFY.  No LLM call; the gate already authored the one precise
    question.

    Includes the intent echo (so the user sees what was understood
    BEFORE the clarification) so they can adjust their answer with
    full context.
    """
    if chain.gate.status != "CLARIFY":
        raise ValueError(
            "render_clarification called on a non-CLARIFY chain "
            f"(status={chain.gate.status!r})"
        )
    lines: List[str] = []
    lines.append(render_intent_echo(chain))
    lines.append("")
    lines.append("I need one clarification before I can answer:")
    lines.append("")
    lines.append(f"  > {chain.gate.clarification_question}")
    lines.append("")
    lines.append(f"(Reason: {chain.gate.reason})")
    return "\n".join(lines)


def render_refusal(chain: IntentChain) -> str:
    """Render the gate's refusal message — when the verdict is REFUSE.

    No LLM call; surfaces the gate's reason + the intent echo so the
    user sees what was understood and why it can't be answered.
    """
    if chain.gate.status != "REFUSE":
        raise ValueError(
            "render_refusal called on a non-REFUSE chain "
            f"(status={chain.gate.status!r})"
        )
    lines: List[str] = []
    lines.append(render_intent_echo(chain))
    lines.append("")
    lines.append("I can't answer this query as posed:")
    lines.append("")
    lines.append(f"  {chain.gate.reason}")
    return "\n".join(lines)


# ============================================================================
# PURE LOGIC — FINAL ASSEMBLY (intent echo + answer + footer)
# ============================================================================


def assemble_final_answer(
    *,
    chain: IntentChain,
    answer_prose: str,
    lineage_head_hash: str,
) -> str:
    """Assemble the three blocks into the final markdown answer.

    R9 discipline: the answer prose body is sandwiched between the
    intent echo (always first) and the provenance footer (where the
    lineage hash lives).  The hash NEVER appears outside the footer.
    """
    sections: List[str] = []
    sections.append(render_intent_echo(chain))
    sections.append("")
    sections.append(answer_prose.strip())
    sections.append("")
    sections.append(render_provenance_footer(chain, lineage_head_hash))
    return "\n".join(sections)


# ============================================================================
# ANSWER RENDERER — orchestrating class (LLM-backed)
# ============================================================================


class AnswerRenderer:
    """The L6 answer renderer.

    Constructed once per session with an LLM model name + sampling
    params.  Exposes:

      - ``open()`` / ``close()`` lifecycle.
      - ``async render(intent_chain, executed_summary,
        lineage_head_hash, timeout_s)`` — returns the final markdown.

    The renderer:
      1. Short-circuits if ``intent_chain.is_answerable is False`` —
         emits the gate's clarification or refusal message via
         ``render_clarification`` / ``render_refusal`` (no LLM call).
      2. Otherwise invokes the LLM to author the answer prose,
         bounded by ``timeout_s``.
      3. Assembles intent echo + answer prose + provenance footer
         deterministically.

    On any LLM failure (timeout / exception / parsing error /
    malformed output), the renderer FAILS CLOSED by surfacing a
    structured "answer generation failed" message that includes the
    intent echo + a refusal-style explanation.  Mirrors the
    fail-closed discipline of PR-8's CoverageGate.
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
        self._model: Optional[Any] = None

    def open(self) -> None:
        """Build the cached SystemMessage + ChatAnthropic instance."""
        if self._is_open:
            return
        from langchain_anthropic import ChatAnthropic
        from langchain_core.messages import SystemMessage

        from orchestrator.prompts import ANSWER_SYSTEM_PROMPT

        self._cached_system_message = SystemMessage(
            content=[
                {
                    "type": "text",
                    "text": ANSWER_SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        )
        self._model = ChatAnthropic(
            model=self._model_name,
            temperature=self._temperature,
            max_tokens=self._max_tokens,
        ).with_structured_output(_AnswerLLMOutput, include_raw=True)
        self._is_open = True
        logger.info(
            "AnswerRenderer ready (model=%s)", self._model_name,
        )

    def close(self) -> None:
        """Drop cached state."""
        self._is_open = False
        self._cached_system_message = None
        self._model = None

    async def render(
        self,
        *,
        intent_chain: IntentChain,
        executed_summary: str,
        lineage_head_hash: str,
        timeout_s: float = 15.0,
    ) -> str:
        """Render the final answer markdown.

        Parameters
        ----------
        intent_chain :
            The PR-9 IntentChain — the four-record bundle.
        executed_summary :
            A free-form English summary the orchestrator pre-formats
            from the executed terminal artifact (a number, a Series,
            a Panel).  The LLM uses it to author the prose; the
            renderer does NOT inspect its content.

            **PR-9A Codex F4 — scope clarification.** This is the
            RENDERER CONTRACT, not a live integration with the L5
            substrate executor.  PR-9 ships the typed answer
            surface; PR-10's orchestrator does the typed-artifact →
            ``executed_summary: str`` translation (Series → "last
            value X, range Y..Z", ScalarMetric → "X (n=N)", etc.).
            Until PR-10, the caller is responsible for producing
            the summary string.
        lineage_head_hash :
            The substrate Lineage's head_hash (single content-
            addressed identifier).  Surfaced in the provenance
            footer.  Per R9 the renderer NEVER places this in the
            headline / answer body.
        timeout_s :
            LLM call timeout.  Default 15s; the answer is the only
            place the LLM authors substantial prose.

        Returns
        -------
        str — the final markdown answer.

        **PR-9A Codex F3 — fail-safe scope clarification.**  Failure
        modes split two ways:

          (a) RUNTIME failures the renderer can recover from — LLM
              timeout, LLM exception, parsing error, malformed
              ``_AnswerLLMOutput``.  All four return a structured
              fail-safe markdown (intent echo + provenance footer +
              a "answer prose could not be generated" note + the
              failure reason).  Does NOT raise.
          (b) PROGRAMMER errors — calling ``render`` without first
              calling ``open()``, or with internal state corrupted
              (e.g. the cached model went missing).  Raises
              ``RuntimeError`` so the bug surfaces immediately in
              the caller's code, not in production output.  Programmer
              errors are NOT runtime fail-safes.

        This split mirrors the same discipline used in PR-8's
        ``CoverageGate.check`` (runtime failures → REFUSE verdict;
        programmer errors → raise).  Both layers stay honest about
        which kind of failure is which.

        Refusal / clarification short-circuit:
        When ``intent_chain.gate.status`` is REFUSE or CLARIFY (or
        more generally ``intent_chain.is_answerable`` is False),
        ``render`` does NOT consult the LLM — it returns the gate's
        clarification or refusal message directly via the
        deterministic ``render_clarification`` / ``render_refusal``
        helpers.  No LLM tokens spent, no timeout risk.
        """
        parts = await self.render_parts(
            intent_chain=intent_chain,
            executed_summary=executed_summary,
            lineage_head_hash=lineage_head_hash,
            timeout_s=timeout_s,
        )
        return parts.markdown

    async def render_parts(
        self,
        *,
        intent_chain: IntentChain,
        executed_summary: str,
        lineage_head_hash: str,
        timeout_s: float = 15.0,
    ) -> RenderedAnswer:
        """Render the final answer as a structured ``RenderedAnswer``.

        Same semantics, fail-safes, and short-circuits as ``render``
        (which now delegates here) — but the caller ALSO receives the
        bare ``answer_prose`` paragraph on the answer path, so the
        chat stream can emit the concise summary while the assembled
        ``markdown`` stays available for audit surfaces (consolidation
        target #3: open-DAG and direct Ask answers read identically —
        no L6 dump in the prose zone).
        """
        # Short-circuit: refusal / clarification.
        if intent_chain.gate.status == "REFUSE":
            return RenderedAnswer(
                markdown=render_refusal(intent_chain),
                kind="refusal",
            )
        if intent_chain.gate.status == "CLARIFY":
            return RenderedAnswer(
                markdown=render_clarification(intent_chain),
                kind="clarification",
            )
        # Defensive: composer / selector refusals that somehow reached
        # here even with a PASS gate.  Treat as REFUSE-flavoured
        # output.
        if not intent_chain.is_answerable:
            return RenderedAnswer(
                markdown=(
                    render_intent_echo(intent_chain)
                    + "\n\nI can't answer this query — an upstream layer "
                    "refused (Composer or Selector); the gate's PASS is "
                    "inconsistent with the upstream refusal."
                ),
                kind="inconsistent",
            )

        from langchain_core.messages import HumanMessage

        if not self._is_open:
            raise RuntimeError(
                "AnswerRenderer is not open.  Call `renderer.open()` "
                "before render()."
            )
        if self._model is None or self._cached_system_message is None:
            raise RuntimeError(
                "AnswerRenderer is open but model / system message "
                "are missing — internal state corrupted."
            )

        user_text = render_answer_user_message(
            intent_chain=intent_chain,
            executed_summary=executed_summary,
        )
        messages = [
            self._cached_system_message,
            HumanMessage(content=user_text),
        ]

        try:
            result = await asyncio.wait_for(
                self._model.ainvoke(messages),
                timeout=timeout_s,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "AnswerRenderer LLM timed out after %.1fs", timeout_s,
            )
            return RenderedAnswer(
                markdown=_failsafe_answer(
                    intent_chain=intent_chain,
                    lineage_head_hash=lineage_head_hash,
                    reason=(
                        f"Answer LLM call timed out after {timeout_s}s."
                    ),
                ),
                kind="failsafe",
            )
        except Exception as exc:
            logger.exception("AnswerRenderer LLM call failed")
            return RenderedAnswer(
                markdown=_failsafe_answer(
                    intent_chain=intent_chain,
                    lineage_head_hash=lineage_head_hash,
                    reason=(
                        f"Answer LLM call failed: "
                        f"{type(exc).__name__}: {exc}."
                    ),
                ),
                kind="failsafe",
            )

        raw = result.get("raw") if isinstance(result, dict) else None
        parsed = result.get("parsed") if isinstance(result, dict) else None
        parsing_error = (
            result.get("parsing_error") if isinstance(result, dict) else None
        )
        if raw is not None:
            _log_usage("answer", raw)
        if parsed is None:
            return RenderedAnswer(
                markdown=_failsafe_answer(
                    intent_chain=intent_chain,
                    lineage_head_hash=lineage_head_hash,
                    reason=(
                        "Answer LLM did not produce a valid output; "
                        f"parsing_error={parsing_error!r}."
                    ),
                ),
                kind="failsafe",
            )

        # Campaign FM-1 hard guard — the LLM has shipped literal
        # unfilled templates to users ("**z-score [value from the
        # Series last observation]** — [if |z| < 1: ...]").  Prose
        # containing bracketed template spans is REPLACED with a
        # deterministic grounded sentence quoting the executed summary
        # verbatim — honest and number-bearing, never improvised.
        answer_prose = parsed.answer_prose
        if _UNFILLED_PLACEHOLDER_RE.search(answer_prose):
            logger.warning(
                "AnswerRenderer: prose contained unfilled template "
                "placeholders; substituting the deterministic grounded "
                "summary.  Draft was: %r",
                answer_prose[:300],
            )
            answer_prose = (
                f"Computed result: {executed_summary}."
            )

        return RenderedAnswer(
            markdown=assemble_final_answer(
                chain=intent_chain,
                answer_prose=answer_prose,
                lineage_head_hash=lineage_head_hash,
            ),
            answer_prose=answer_prose,
            kind="answer",
        )


# ============================================================================
# USER MESSAGE RENDERING
# ============================================================================


def render_answer_user_message(
    *,
    intent_chain: IntentChain,
    executed_summary: str,
) -> str:
    """Render the per-call user message the Answer LLM sees.

    Includes the original prompt + the intent echo (so the LLM has
    full context) + the executed summary (so it knows what number /
    artifact to report).  The system prompt instructs the LLM to
    author ONLY ``answer_prose`` — the renderer assembles the rest
    around it.
    """
    lines: List[str] = []
    lines.append("ORIGINAL USER PROMPT:")
    lines.append('"""')
    lines.append(intent_chain.user_prompt)
    lines.append('"""')
    lines.append("")
    lines.append("WHAT WAS UNDERSTOOD AND BUILT (verbatim — this will be")
    lines.append("the intent echo block in the rendered answer):")
    lines.append(render_intent_echo(intent_chain))
    lines.append("")
    lines.append("EXECUTED RESULT SUMMARY (from the substrate executor):")
    lines.append('"""')
    lines.append(executed_summary)
    lines.append('"""')
    lines.append("")
    lines.append(
        "Author ONE paragraph in senior-PM trader lingo with the "
        "number embedded.  Do NOT repeat the intent echo (the "
        "renderer adds it).  Do NOT mention the lineage hash (it "
        "belongs to the footer)."
    )
    return "\n".join(lines)


# ============================================================================
# FAIL-SAFE
# ============================================================================


def _failsafe_answer(
    *,
    intent_chain: IntentChain,
    lineage_head_hash: str,
    reason: str,
) -> str:
    """Render a structured "answer generation failed" message that
    still surfaces the intent echo + the provenance footer (the user
    sees what was computed, just not the LLM-authored prose).

    This is the L6 mirror of PR-8's fail-closed gate REFUSE — every
    failure path returns a well-formed markdown message; no
    exception escapes ``render``.
    """
    return (
        render_intent_echo(intent_chain)
        + "\n\nThe answer prose could not be generated.  "
        + reason
        + "  The substrate computed the result; only the PM-facing "
        "narration failed.  See provenance below for the lineage "
        "needed to inspect the raw result.\n\n"
        + render_provenance_footer(intent_chain, lineage_head_hash)
    )


# ============================================================================
# USAGE LOGGING
# ============================================================================


def _log_usage(label: str, response) -> None:
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
    "AnswerRenderer",
    "RenderedAnswer",
    "assemble_final_answer",
    "render_answer_user_message",
    "render_clarification",
    "render_intent_echo",
    "render_provenance_footer",
    "render_refusal",
]
