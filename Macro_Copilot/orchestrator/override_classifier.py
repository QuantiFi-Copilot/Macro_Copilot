"""orchestrator/override_classifier.py — R5.5 override-intent classifier.

Given a user message that arrives with a workspace-scoped preamble
(``[Workspace: slug · …]\n\n<text>``), detect when the user implicitly
asked to change a parameter on a previously-rendered workspace and
emit structured ``ProposedOverride`` entries.  Frontend renders these
as click-to-queue chips that dispatch into the shared workspace
overrides queue — UI plumbing shipped in PR #141.

Two paths
---------
1. **Heuristic fast-path** (default, always on): a small set of regex
   patterns covers the high-frequency phrases — "change [param] to
   [value]", "use [convention] instead", "with [N]-day window", etc.
   Zero LLM cost; ~95% precision for the patterns it knows.

2. **LLM structured-output fallback** (env-gated, off by default):
   when the heuristic returns no matches but the workspace context
   indicates a non-trivial set of editable params, fall through to a
   structured-output LLM call that returns ``ProposedOverridesList``.
   The call is opt-in via ``ORCHESTRATOR_OVERRIDE_CLASSIFIER_LLM=1``
   because each turn pays an extra LLM round-trip; teams should
   measure cost/recall before flipping it on.

Module is pure / side-effect-free.  No DB / WS access; callers (the
session done-emission path) feed it strings + workspace context dicts
and consume the returned overrides list.
"""

from __future__ import annotations

import logging
import re
from typing import List, Optional

from orchestrator.contracts import ProposedOverride

logger = logging.getLogger("orchestrator.override_classifier")


# ----------------------------------------------------------------------------
# Heuristic patterns
# ----------------------------------------------------------------------------

# Each pattern returns a list of ``(path_tuple, value, value_label?)``.
# The path uses the substrate's slot vocabulary (e.g. ``window_days``,
# ``lookback_days``, ``day_count``).  When a pattern is ambiguous about
# which slot owns the param, we map it to the most common slot for that
# vocabulary; finer disambiguation is the LLM fallback's job.

# 1. "change the [z-score|rolling|lookback] window to <N>d"
_WINDOW_PATTERN = re.compile(
    r"""
    \b(?:change|set|switch|update)\b
    .{0,40}?
    \b(z[-\s]?score|rolling|lookback|window)\b
    .{0,40}?
    \bto\b
    \s+(\d+)
    \s*(d|day|days|business\sday|business\sdays)?
    """,
    re.IGNORECASE | re.VERBOSE,
)

# 2. "use ACT/365" / "switch to ACT/360" / "with ACT/365F day-count"
_DAY_COUNT_PATTERN = re.compile(
    r"""
    \b(?:use|switch\s+to|with)\b
    .{0,20}?
    (ACT/\d+[A-Z]?)
    """,
    re.IGNORECASE | re.VERBOSE,
)

# 3. "compare to a <X>" / "compare this to a <X>" — flags follow-up
# intent but doesn't generate an override (no specific param to mutate
# without LLM grounding).  Listed here for completeness; returns empty.

# 4. "use a <N>-day window" / "with a <N>-day rolling"
#
# The context word (window / rolling / lookback / z-score) is REQUIRED.
# It used to be an optional trailing group, but a lazy gap
# (``.{0,20}?``) followed by an optional group means the regex engine
# succeeds immediately with the group unmatched — ``group(2)`` was
# ALWAYS None, so the handler's "no context word → drop" branch fired
# on every match and pattern 4 never produced an override.  Requiring
# the group is behaviour-preserving for the no-context case (those
# matches were dropped anyway) and makes the documented contexts
# actually classify.
_INLINE_WINDOW_PATTERN = re.compile(
    r"""
    \b(?:use|with)\b
    \s+(?:a\s+)?
    (\d+)
    \s*[-]?
    \s*(?:day|d)\b
    .{0,20}?
    \b(window|rolling|lookback|z[-\s]?score)\b
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _heuristic_overrides(text: str) -> List[ProposedOverride]:
    """Apply the regex patterns and return any matches as ProposedOverride
    objects.  Patterns are non-greedy and the function de-duplicates by
    path so multiple matches on the same slot collapse to one entry."""
    out: List[ProposedOverride] = []
    seen_paths: set = set()

    def _add(
        path: tuple,
        value,
        value_label: Optional[str] = None,
        rationale: Optional[str] = None,
    ) -> None:
        if path in seen_paths:
            return
        seen_paths.add(path)
        out.append(
            ProposedOverride(
                path=path,
                value=value,
                value_label=value_label,
                rationale=rationale,
            )
        )

    # Pattern 1 — explicit window change
    for m in _WINDOW_PATTERN.finditer(text):
        flavour = m.group(1).lower().replace(" ", "").replace("-", "")
        value = int(m.group(2))
        if "lookback" in flavour:
            _add(
                ("lookback_days",),
                value,
                value_label=f"{value}d",
                rationale=f"User said 'change {flavour} window to {value}d'",
            )
        else:
            # z-score / rolling / generic window all map to window_days.
            _add(
                ("window_days",),
                value,
                value_label=f"{value}d",
                rationale=f"User said 'change {flavour} window to {value}d'",
            )

    # Pattern 2 — day-count convention swap
    for m in _DAY_COUNT_PATTERN.finditer(text):
        convention = m.group(1).upper().replace(" ", "")
        _add(
            ("day_count",),
            convention,
            value_label=convention,
            rationale=f"User asked to use {convention}",
        )

    # Pattern 4 — inline window declaration
    for m in _INLINE_WINDOW_PATTERN.finditer(text):
        value = int(m.group(1))
        ctx = (m.group(2) or "").lower().replace(" ", "").replace("-", "")
        if ctx == "lookback":
            _add(
                ("lookback_days",),
                value,
                value_label=f"{value}d",
                rationale=f"User said 'use a {value}-day lookback'",
            )
        elif ctx in {"window", "rolling", "zscore"}:
            _add(
                ("window_days",),
                value,
                value_label=f"{value}d",
                rationale=f"User said 'use a {value}-day {ctx}'",
            )
        # Pattern 4 with no context word is too ambiguous to commit to
        # a slot — fall through to the LLM path or drop.

    return out


def classify_overrides(
    user_message: str,
    *,
    workspace_context: Optional[dict] = None,
) -> List[ProposedOverride]:
    """Public entry point.  Returns a list of ProposedOverride objects
    inferred from ``user_message``.  ``workspace_context`` (the dict
    from ``extract_workspace_context``) is consulted to gate the LLM
    fallback — when no workspace is in scope, only the heuristic path
    runs.

    Returns an empty list when no overrides are detected — callers
    should NOT append an empty ``proposed_overrides`` to the wire (the
    frontend's filter treats missing and empty identically, but
    skipping the key keeps the wire shape compact).
    """
    # Heuristic first — cheap and always on.
    heuristic = _heuristic_overrides(user_message)
    if heuristic:
        logger.info(
            "override_classifier: heuristic found %d override(s)",
            len(heuristic),
        )
        return heuristic

    # LLM fallback is wired but env-gated.  Today it's an explicit
    # opt-in to avoid paying a per-turn LLM call for every chat
    # message; the heuristic path covers the common cases.  Wiring
    # for the structured-output call lives in this module so the
    # interface is stable for follow-up work.
    import os
    if os.getenv("ORCHESTRATOR_OVERRIDE_CLASSIFIER_LLM") != "1":
        return []

    if workspace_context is None:
        # No workspace in scope → nothing to override.
        return []

    return _llm_classify_overrides(user_message, workspace_context)


def _llm_classify_overrides(
    user_message: str,
    workspace_context: dict,
) -> List[ProposedOverride]:
    """Structured-output LLM call for override-intent classification.

    Today this is a STUB — it imports ``langchain_anthropic`` lazily
    and runs a structured-output call against ``ProposedOverridesList``
    only when the env flag is set.  Production teams should benchmark
    cost / recall before flipping the flag on default.  Returns an
    empty list when the call fails or the env isn't fully configured
    so callers can treat a stub failure as "no overrides detected"
    rather than as a hard error.
    """
    try:
        from langchain_core.messages import HumanMessage, SystemMessage

        from orchestrator.config import (
            LLM_TEMPERATURE,
            OVERRIDE_CLASSIFIER_MODEL,
        )
        from orchestrator.contracts import ProposedOverridesList
        from orchestrator.llm_factory import LlmRole, make_chat_model

        system_prompt = (
            "You classify user messages from a macro-analysis copilot as "
            "parameter-override intents on a previously-rendered workspace. "
            "Output structured JSON matching ``ProposedOverridesList``. "
            "When the user did not ask to change a parameter, return an "
            "empty list. Workspace context (tools + params) is supplied "
            "to ground the slot names."
        )
        wc_text = f"Workspace context:\n{workspace_context}\n\nUser:\n{user_message}"

        # Single LLM chokepoint (P10); model id is now a config knob
        # (OVERRIDE_CLASSIFIER_MODEL) rather than a hardcoded literal.
        # ``include_raw`` defaults False, matching the prior plain
        # ``with_structured_output(ProposedOverridesList)`` consumption.
        structured = make_chat_model(
            role=LlmRole.OVERRIDE_CLASSIFIER,
            model_name=OVERRIDE_CLASSIFIER_MODEL,
            temperature=LLM_TEMPERATURE,
            max_tokens=400,
            structured_output=ProposedOverridesList,
            include_raw=False,
        )
        result = structured.invoke(
            [SystemMessage(content=system_prompt), HumanMessage(content=wc_text)],
        )
        if isinstance(result, ProposedOverridesList):
            logger.info(
                "override_classifier: LLM returned %d override(s)",
                len(result.overrides),
            )
            return list(result.overrides)
        return []
    except Exception:
        logger.exception("override_classifier: LLM path failed")
        return []
