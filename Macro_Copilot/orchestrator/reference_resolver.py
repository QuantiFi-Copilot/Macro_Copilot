"""orchestrator/reference_resolver.py — NL → structured working-set ops.

Phase 0 PR 8.

A tiny structured-output LLM call that turns the user's natural-
language request into two pieces of structured intent:

  - ``save_as``           Optional[str] — if the user said something
                          like "save as tips_2y_v3" or "call this
                          foo", the alias to bind to this turn's
                          terminal artifact.  None when the user
                          made no explicit save request.
  - ``referenced_names``  List[str] — working-set names from the
                          current session that the user is reading
                          (e.g. "compare that with tips_2y_v1").
                          Empty when the user introduced a fresh
                          query.

This is intentionally a SEPARATE LLM call from the supervisor's
``route``.  Reasons:

  1. **Bounded scope.**  The resolver only ever returns a small
     structured object — never prose, never numbers.  That makes
     it cheap (low max_tokens), cache-eligible (its system prompt
     is static), and easy to test (the response shape is a
     two-field Pydantic model with a closed enum of behaviours).

  2. **Independence from routing.**  The supervisor's prompt
     should not grow knowledge of every new working-set name
     across turns — that would defeat its cache.  By isolating
     name resolution here, the supervisor's prompt stays static
     and the resolver's prompt carries the (small, per-session)
     list of currently-bound names.

  3. **Clean failure mode.**  If the LLM call fails or returns
     something we can't validate, the resolver returns an empty
     ``ReferenceResolution`` and the turn proceeds as if the user
     made a fresh query (the supervisor will route on the raw
     text, and commit_turn will use ``turn_<n>_result`` as the
     only auto-name).  Failures here MUST NOT break the turn.

The resolver does NOT consult Postgres — its input is the user
message plus the list of currently-visible names (supplied by the
caller, typically built from ``state.working_set.list_visible``).
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import List, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field

from orchestrator.prompts import REFERENCE_RESOLVER_SYSTEM_PROMPT

# Heavy imports are deferred to ``ReferenceResolver.__init__`` so the
# module can be imported (for ``ReferenceResolution``) without
# langchain installed.  Test code that injects a scripted resolver
# never touches ChatAnthropic.

logger = logging.getLogger("orchestrator.reference_resolver")


# ============================================================================
# Public contract — the resolver's structured output
# ============================================================================


class ReferenceResolution(BaseModel):
    """Structured output of the reference resolver."""

    model_config = ConfigDict(extra="forbid")

    save_as: Optional[str] = Field(
        default=None,
        description=(
            "If the user explicitly asked to save the result of this "
            "turn under a name (e.g. 'save as tips_2y_v3', 'call this "
            "foo'), the alias to bind.  Names must match the pattern "
            "[A-Za-z_][A-Za-z0-9_]{0,63}.  Use null when no such request "
            "is made."
        ),
    )
    referenced_names: List[str] = Field(
        default_factory=list,
        description=(
            "Working-set names from the visible-names list that the user "
            "is reading in this turn (e.g. 'compare that with tips_2y_v1' "
            "references tips_2y_v1).  Each name MUST appear in the "
            "visible-names list provided in the human message; never "
            "fabricate a name that wasn't shown to you.  Empty list when "
            "the user is asking a fresh question."
        ),
    )


# ============================================================================
# Validation helpers — defensive layer on top of structured output
# ============================================================================

# Reuse the same pattern enforced in state.working_set so a name the
# resolver invents is rejected here BEFORE it reaches a SQL INSERT
# that would have rejected it anyway.  Belt and suspenders.
_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")


def _sanitize(
    raw: ReferenceResolution,
    visible_names: Sequence[str],
) -> ReferenceResolution:
    """Filter the LLM's output against the visible-names allowlist
    and the safe-name pattern.

    Hallucinated references (names the resolver invented that aren't
    actually bound) are dropped silently — the supervisor will route
    on the raw message and the user will see a normal "I don't know
    what you're referring to" path.  Invalid ``save_as`` is dropped
    to None.
    """
    visible_set = set(visible_names)
    cleaned_refs: List[str] = []
    for name in raw.referenced_names:
        if not isinstance(name, str):
            continue
        if not _NAME_PATTERN.fullmatch(name):
            logger.debug("resolver: dropping malformed reference %r", name)
            continue
        if name not in visible_set:
            logger.info(
                "resolver: dropping hallucinated reference %r (not in "
                "visible names %s)",
                name, sorted(visible_set),
            )
            continue
        cleaned_refs.append(name)

    save_as = raw.save_as
    if save_as is not None:
        if not isinstance(save_as, str) or not _NAME_PATTERN.fullmatch(save_as):
            logger.info(
                "resolver: dropping invalid save_as %r (pattern violation)",
                save_as,
            )
            save_as = None

    return ReferenceResolution(
        save_as=save_as,
        referenced_names=cleaned_refs,
    )


# ============================================================================
# Resolver
# ============================================================================


class ReferenceResolver:
    """Wraps the structured-output LLM call.

    Constructed once per session (cheap).  Same model / temperature /
    max-tokens as the supervisor for cache-prefix consistency, but
    with ``max_tokens=256`` since the output is tiny.

    Heavy imports (``ChatAnthropic``, ``SystemMessage``) happen here
    in ``__init__`` so callers can ``import ReferenceResolution``
    from this module without paying for the LangChain stack.
    """

    def __init__(
        self,
        model_name: str,
        temperature: float = 0.0,
        max_tokens: int = 256,
    ):
        from langchain_anthropic import ChatAnthropic
        from langchain_core.messages import SystemMessage

        self._model_name = model_name
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._base_model = ChatAnthropic(
            model=model_name,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        self._structured_model = self._base_model.with_structured_output(
            ReferenceResolution,
            include_raw=False,
        )
        # Cached system message: the resolver's prompt is static
        # across turns so anchor a cache breakpoint on it.  Stored
        # per-instance because constructing a SystemMessage requires
        # langchain.
        self._cached_system = SystemMessage(
            content=[
                {
                    "type": "text",
                    "text": REFERENCE_RESOLVER_SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        )

    async def resolve(
        self,
        user_message: str,
        *,
        visible_names: Sequence[str],
        timeout_seconds: float = 8.0,
    ) -> ReferenceResolution:
        """Run the resolver against ``user_message``.

        ``visible_names`` is the list of currently-active working-set
        names this session has (from ``state.working_set.list_visible``).
        The LLM is told to only return names from this list; we
        sanitize defensively in case it hallucinates one.

        Failures (network error, timeout, parsing error) are CAUGHT and
        return an empty resolution.  The turn proceeds as if the user
        made a fresh query — degraded operation, not failed turn.
        """
        from langchain_core.messages import HumanMessage

        visible_block = _format_visible_names(visible_names)
        human_content = (
            f"VISIBLE WORKING-SET NAMES (this session):\n{visible_block}\n\n"
            f"USER MESSAGE:\n{user_message}"
        )

        try:
            result = await asyncio.wait_for(
                self._structured_model.ainvoke(
                    [
                        self._cached_system,
                        HumanMessage(content=human_content),
                    ]
                ),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "resolver: timed out after %.1fs; returning empty resolution",
                timeout_seconds,
            )
            return ReferenceResolution()
        except Exception:
            logger.exception(
                "resolver: LLM call failed; returning empty resolution"
            )
            return ReferenceResolution()

        if not isinstance(result, ReferenceResolution):
            logger.warning(
                "resolver: structured output produced %r; expected "
                "ReferenceResolution.  Falling back to empty.",
                type(result).__name__,
            )
            return ReferenceResolution()

        return _sanitize(result, visible_names)


def _format_visible_names(visible_names: Sequence[str]) -> str:
    if not visible_names:
        return "(none)"
    return "\n".join(f"- {n}" for n in visible_names)


__all__ = [
    "ReferenceResolution",
    "ReferenceResolver",
]
