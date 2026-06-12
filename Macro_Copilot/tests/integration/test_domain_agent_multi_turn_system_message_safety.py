"""tests/integration/test_domain_agent_multi_turn_system_message_safety.py

PR 16 end-to-end regression: drive a real ``DomainAgentSession`` through
the SAME turn sequence that crashed in the user's production logs and
assert the LLM is NEVER handed a non-consecutive-SystemMessage list.

Production crash sequence (from
``ws-5e7db3972b42`` in the user's docker logs, 2026-05-12):

  turn 1: multi_domain[sovereign, ois]   "Compare UST 2s10s vs SOFR 2s10s"  ✅
  turn 2: multi_domain[sovereign, ois]   "Bund 5s30s vs ESTR 5s30s"          ❌
  turn 3: multi_domain (retry)                                                ❌
  turn 4: single_domain[sovereign]       "what is the UST 2s10s spread"     ❌
  turn 5: single_domain[sovereign]       "what is the UST 2s10s spread"     ❌

The bug: turn 1's multi-domain scope SystemMessage got persisted into
``state["messages"]`` via the per-session-per-domain checkpointer (PR
5).  On turn 2+, ``agent_node`` prepended its cached SystemMessage in
front of state, producing two SystemMessages separated by
Human/AI/Tool messages → Anthropic API rejects with "non-consecutive
system messages".  Once tripped, the child remained poisoned for the
rest of the orchestrator session — even single-domain turns failed.

This test stands up a real ``DomainAgentSession`` against an
in-process MCP stub and an Anthropic-stub LLM, drives the same
3-turn pattern, and asserts the LLM saw exactly ONE SystemMessage at
index 0 on EVERY invocation.

Why it stays in tests/integration: it spins up the LangGraph state
machine + MemorySaver + MCP adapter shape (without subprocesses).
That's heavier than a unit test but smaller than the full WS
end-to-end.
"""

from __future__ import annotations

import asyncio
import os
import sys
import types
from pathlib import Path
from typing import Any, List

import pytest


_PROJECT_ROOT = str(Path(__file__).resolve().parents[2])
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


# ============================================================================
# Stubs — same convention as PR 13's diagnostic test.
# ============================================================================


def _install_stubs() -> None:
    # Stub ``dotenv`` ONLY when the real package is genuinely absent —
    # a partial fake registered ahead of the real package breaks later
    # ``dotenv.dotenv_values`` importers (pydantic_settings → mcp) in
    # full-tree pytest runs.
    try:
        import dotenv as _real_dotenv  # noqa: F401
    except ImportError:
        fake_dotenv = types.ModuleType("dotenv")
        fake_dotenv.load_dotenv = lambda *a, **k: False
        fake_dotenv.dotenv_values = lambda *a, **k: {}
        sys.modules["dotenv"] = fake_dotenv


_install_stubs()


# ============================================================================
# Skip the test cleanly if the real langchain / langgraph stack isn't
# installed.  In the state-layer CI lane those packages aren't
# available; this test belongs in the integration lane that has them.
#
# We can't use ``importlib.util.find_spec``: PR 13's diagnostic test
# installs a langchain_anthropic STUB module to sys.modules and that
# fake module's ``__spec__`` is None, which would crash find_spec.
# Instead, do a real import attempt and check for the real attribute
# we actually need.
# ============================================================================


def _real_stack_available() -> bool:
    # Importing from a SUBMODULE proves we have the real package, not
    # the PR 13 diagnostic stub (which only sets a top-level attribute
    # ``ChatAnthropic`` and doesn't define ``chat_models`` as a real
    # submodule).
    try:
        from langchain_anthropic.chat_models import ChatAnthropic  # noqa: F401
        from langchain_mcp_adapters.client import MultiServerMCPClient  # noqa: F401
        from langgraph.checkpoint.memory import MemorySaver  # noqa: F401
        from langgraph.graph import StateGraph  # noqa: F401
    except Exception:
        return False
    return True


pytestmark = pytest.mark.skipif(
    not _real_stack_available(),
    reason="LangChain / LangGraph stack not installed in this lane",
)


# ============================================================================
# Stub MCP client + stub LLM
# ============================================================================


# langgraph >= 1.x ``ToolNode`` validates its tools through
# ``langchain_core.tools.convert.tool`` and rejects duck-typed objects
# that merely expose ``name``/``ainvoke`` (ValueError: "first argument
# must be a string or a callable with a __name__").  The stub must be a
# REAL ``BaseTool`` subclass, exactly like the StructuredTool instances
# ``MultiServerMCPClient.get_tools()`` returns in production.  Import
# is guarded so the module still collects (and skips via ``pytestmark``)
# in lanes without the langchain stack.
try:
    from langchain_core.tools import BaseTool as _BaseTool
except Exception:  # pragma: no cover — skipif lane without langchain
    _BaseTool = object  # type: ignore[assignment, misc]


class _StubTool(_BaseTool):  # type: ignore[valid-type, misc]
    """Minimal REAL LangChain BaseTool (langgraph 1.x ``ToolNode``
    no longer accepts duck-types).  We only need ``name`` to satisfy
    ``MultiServerMCPClient.get_tools()`` and ``ToolNode`` construction;
    the capture LLM never emits tool calls, so it is never invoked.
    """

    name: str = "stub_curve_spread_tool"
    description: str = "stub tool for tests"

    def _run(self, *args, **kwargs):
        return {"value_bps": 50.0, "as_of": "2026-05-12"}

    async def _arun(self, *args, **kwargs):
        return {"value_bps": 50.0, "as_of": "2026-05-12"}


class _StubMCPClient:
    def __init__(self, *args, **kwargs):
        pass

    async def get_tools(self):
        return [_StubTool()]

    async def __aexit__(self, *args, **kwargs):
        return None


class _CaptureLLM:
    """Records the message list it receives on every invocation.

    On each ``ainvoke`` call, returns a NO-TOOL-CALL AIMessage so the
    LangGraph ReAct loop terminates immediately (no recursion into
    ``tools_condition`` → ``ToolNode`` → back into ``agent``).
    """

    def __init__(self) -> None:
        self.calls: List[List[Any]] = []

    def bind_tools(self, *args, **kwargs):
        return self

    async def ainvoke(self, messages, *args, **kwargs):
        # Defensive deep-ish copy of the message list so subsequent
        # mutations don't leak into our recorded snapshot.
        self.calls.append(list(messages))

        from langchain_core.messages import AIMessage

        # No tool calls → tools_condition routes to END → loop stops.
        return AIMessage(
            content=(
                "stubbed answer: curve spread is 50bps (test fixture)"
            ),
            usage_metadata={"input_tokens": 10, "output_tokens": 5},
        )


# ============================================================================
# Helpers — match the same name-based predicates the unit test uses.
# ============================================================================


def _system_count(messages: List[Any]) -> int:
    from langchain_core.messages import SystemMessage

    return sum(1 for m in messages if isinstance(m, SystemMessage))


def _system_positions(messages: List[Any]) -> List[int]:
    from langchain_core.messages import SystemMessage

    return [i for i, m in enumerate(messages) if isinstance(m, SystemMessage)]


# ============================================================================
# The test
# ============================================================================


@pytest.mark.asyncio
async def test_multi_then_single_then_multi_never_emits_non_consecutive_system() -> None:
    """Reproduce the user's production crash sequence and assert it
    no longer crashes after PR 16.
    """
    # Patch the LLM + MCP client BEFORE importing DomainAgentSession
    # so module-level references resolve to our stubs.
    import importlib

    import orchestrator.domain_agent as dom

    capture_llm = _CaptureLLM()

    # Patch ChatAnthropic to return our capture LLM.  The real
    # ChatAnthropic ctor takes model/temperature/max_tokens; the stub
    # ignores them.
    def _fake_chat_anthropic(*args, **kwargs):
        return capture_llm

    dom.ChatAnthropic = _fake_chat_anthropic  # type: ignore[attr-defined]
    dom.MultiServerMCPClient = _StubMCPClient  # type: ignore[attr-defined]

    from langgraph.checkpoint.memory import MemorySaver

    from orchestrator.contracts import Domain
    from orchestrator.domain_agent import DomainAgentSession

    # Per-session checkpointer (PR 5 semantics): shared across all
    # ``run`` calls in this test — this is the SHAPE that surfaces
    # the bug.
    checkpointer = MemorySaver()

    session = DomainAgentSession(
        domain=Domain.SOVEREIGN_BONDS,
        system_prompt="You are the sovereign bonds specialist.",
        mcp_servers={"sovereign_bonds": {"command": "noop"}},
        model_name="claude-stub",
        checkpointer=checkpointer,
    )

    await session.open()
    try:
        # ---- Turn 1: multi-domain (scope set) ----------------------
        await session.run(
            user_message="Compare UST 2s10s vs SOFR 2s10s.",
            domain_boundary="stay in sovereign_bonds lane",
            emit_tokens=False,
            turn_thread_id="multi-domain-thread",
        )

        # ---- Turn 2: single-domain (NO scope) on SAME thread -------
        # Pre-PR-16, this is where the production logs crashed:
        # turn 1's scope SystemMessage is still in state, and on
        # turn 2 the cached system + the stale scope = two
        # non-consecutive SystemMessages.
        await session.run(
            user_message="what is the UST 2s10s spread",
            domain_boundary=None,
            emit_tokens=False,
            turn_thread_id="multi-domain-thread",
        )

        # ---- Turn 3: multi-domain again ---------------------------
        await session.run(
            user_message="Compare UST 5s30s vs Bund 5s30s.",
            domain_boundary="stay in sovereign_bonds lane",
            emit_tokens=False,
            turn_thread_id="multi-domain-thread",
        )
    finally:
        await session.close()

    # ---- Assertions: every captured LLM call has exactly one
    # SystemMessage, and it is at index 0.
    assert len(capture_llm.calls) == 3, (
        f"Expected 3 LLM invocations (one per turn); got "
        f"{len(capture_llm.calls)}.  This suggests the agent looped "
        "or short-circuited unexpectedly."
    )

    for turn_idx, messages in enumerate(capture_llm.calls, start=1):
        sys_count = _system_count(messages)
        sys_positions = _system_positions(messages)
        assert sys_count == 1, (
            f"Turn {turn_idx}: expected exactly 1 SystemMessage "
            f"(the cached system prompt), got {sys_count} at "
            f"positions {sys_positions}.  Multiple SystemMessages "
            f"means the PR 16 fix has regressed — Anthropic's API "
            f"will reject the message list with 'non-consecutive "
            f"system messages'."
        )
        assert sys_positions == [0], (
            f"Turn {turn_idx}: SystemMessage must be at index 0, "
            f"found at {sys_positions}.  Non-zero position means "
            f"the cached system prefix is missing and a stale "
            f"SystemMessage leaked through from state."
        )


@pytest.mark.asyncio
async def test_multi_domain_then_multi_domain_same_session_succeeds() -> None:
    """Narrow version of the above focused on the exact production
    failure shape: two consecutive MULTI-DOMAIN turns on the same
    per-session-per-domain thread.  Pre-PR-16 the second always
    failed.
    """
    import orchestrator.domain_agent as dom

    capture_llm = _CaptureLLM()
    dom.ChatAnthropic = lambda *a, **k: capture_llm  # type: ignore[attr-defined]
    dom.MultiServerMCPClient = _StubMCPClient  # type: ignore[attr-defined]

    from langgraph.checkpoint.memory import MemorySaver

    from orchestrator.contracts import Domain
    from orchestrator.domain_agent import DomainAgentSession

    session = DomainAgentSession(
        domain=Domain.OIS,
        system_prompt="You are the OIS specialist.",
        mcp_servers={"ois": {"command": "noop"}},
        model_name="claude-stub",
        checkpointer=MemorySaver(),
    )

    await session.open()
    try:
        await session.run(
            "Compare UST 2s10s vs SOFR 2s10s.",
            domain_boundary="stay in ois lane",
            emit_tokens=False,
            turn_thread_id="ois-thread",
        )
        await session.run(
            "Bund 5s30s vs ESTR 5s30s, compare.",
            domain_boundary="stay in ois lane",
            emit_tokens=False,
            turn_thread_id="ois-thread",
        )
    finally:
        await session.close()

    assert len(capture_llm.calls) == 2
    for turn_idx, messages in enumerate(capture_llm.calls, start=1):
        assert _system_count(messages) == 1, (
            f"Turn {turn_idx}: produced {_system_count(messages)} "
            f"SystemMessages at {_system_positions(messages)}; the "
            f"production crash signature."
        )


@pytest.mark.asyncio
async def test_scope_appears_as_human_message_prefix_not_system_message() -> None:
    """Verify the POSITIVE side of the contract: when a scope hint
    is supplied, it lives as a content prefix on the FIRST
    HumanMessage of the turn — never as a SystemMessage.
    """
    import orchestrator.domain_agent as dom

    capture_llm = _CaptureLLM()
    dom.ChatAnthropic = lambda *a, **k: capture_llm  # type: ignore[attr-defined]
    dom.MultiServerMCPClient = _StubMCPClient  # type: ignore[attr-defined]

    from langchain_core.messages import HumanMessage
    from langgraph.checkpoint.memory import MemorySaver

    from orchestrator.contracts import Domain
    from orchestrator.domain_agent import DomainAgentSession

    session = DomainAgentSession(
        domain=Domain.SOVEREIGN_BONDS,
        system_prompt="sovereign prompt",
        mcp_servers={"sovereign_bonds": {"command": "noop"}},
        model_name="claude-stub",
        checkpointer=MemorySaver(),
    )

    await session.open()
    try:
        await session.run(
            "What's the UST 2s10s?",
            domain_boundary="stay in sovereign_bonds lane",
            emit_tokens=False,
            turn_thread_id="scope-prefix-thread",
        )
    finally:
        await session.close()

    assert len(capture_llm.calls) == 1
    messages = capture_llm.calls[0]
    # Find the human message — it should be at index 1 (after the
    # cached system prompt).
    assert len(messages) >= 2
    assert isinstance(messages[1], HumanMessage), (
        f"Expected a HumanMessage at index 1; got "
        f"{type(messages[1]).__name__}."
    )
    human_content = messages[1].content
    assert isinstance(human_content, str)
    assert "[scope for this query] stay in sovereign_bonds lane" in human_content, (
        f"Scope prefix missing from HumanMessage content: "
        f"{human_content[:200]!r}"
    )
    assert "What's the UST 2s10s?" in human_content, (
        f"Original user message missing from HumanMessage content: "
        f"{human_content[:200]!r}"
    )
    # The two should be separated by a blank line.
    assert (
        "stay in sovereign_bonds lane\n\nWhat's the UST 2s10s?"
        in human_content
    ), (
        f"Scope + user message must be separated by ``\\n\\n`` so the "
        f"LLM reads them as two paragraphs; got: {human_content!r}"
    )
