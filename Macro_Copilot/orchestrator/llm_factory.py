"""
orchestrator/llm_factory.py — the single LLM construction chokepoint
====================================================================

Every ``ChatAnthropic`` the orchestrator builds is constructed here, and
nowhere else.  This module exists for two reasons:

1. **Single source of truth (P10).**  Before this module, ten call sites
   each constructed ``ChatAnthropic`` inline; four routed through
   ``orchestrator.config.anthropic_chat_kwargs`` and six passed literal
   kwargs (the latter would send a ``temperature`` to a no-sampling model
   — a latent 400 if ``LLM_MODEL`` flips to Opus 4.7+/Fable).  Routing all
   ten through one factory makes the per-model API surface (handled by
   ``anthropic_chat_kwargs``) apply uniformly.

2. **The offline replay seam (env-gated, default OFF).**  When
   ``ORCHESTRATOR_LLM_REPLAY=1``, the factory returns a
   :class:`ReplayChatModel` that serves pre-recorded structured outputs
   and streamed tokens / tool_calls from a directory on disk — with ZERO
   Anthropic API calls.  This makes the live Ask surface testable offline
   and gives the platform a deterministic regression-replay capability.
   Mirrors the conductor's hard-guard philosophy: a replay-source miss
   raises a typed :class:`LlmReplayError`; it NEVER falls back to the API
   and NEVER fabricates an answer (P6).

**Default-OFF is byte-identical (P1).**  With the flag unset, the factory
constructs exactly the ``ChatAnthropic`` each site constructed before, with
the same structured-output / bind_tools wrappers applied.  A test asserts
this per role (``tests/test_llm_factory.py``).

Error layering (per ``docs_revamped/03_standards/error_handling.md``):

* :class:`LlmFactoryError` subclasses ``Exception`` — programmer-fix errors
  raised at construction (unknown role, bad config), not input-shape errors.
* :class:`LlmReplayError` subclasses ``RuntimeError`` — a runtime condition
  outside the caller's input control (the recorded fixture is missing).
"""

from __future__ import annotations

import json
import os
import threading
from contextvars import ContextVar
from enum import Enum
from pathlib import Path
from typing import Any, AsyncIterator, Optional

from pydantic import BaseModel

from orchestrator.config import anthropic_chat_kwargs

# ===========================================================================
# ENV FLAGS  (names are the public contract; documented in the SPEC)
# ===========================================================================

#: Master switch.  When ``"1"``, every LLM role is served from the replay
#: source and NO ``ChatAnthropic`` is constructed.  Default OFF.
REPLAY_FLAG_ENV: str = "ORCHESTRATOR_LLM_REPLAY"

#: Directory that holds the recorded outputs (see :class:`ReplaySource`).
REPLAY_DIR_ENV: str = "ORCHESTRATOR_LLM_REPLAY_DIR"

#: Optional default session / turn so the structured (ainvoke) lane is
#: exercisable without the live backend setting a replay context.  The
#: live backend sets the context per turn via :func:`set_replay_context`.
REPLAY_SESSION_ENV: str = "ORCHESTRATOR_LLM_REPLAY_SESSION"
REPLAY_TURN_ENV: str = "ORCHESTRATOR_LLM_REPLAY_TURN"


def replay_enabled() -> bool:
    """True iff the replay seam is switched on.  Read at construction time
    so a test can toggle the flag between factory calls."""
    return os.getenv(REPLAY_FLAG_ENV) == "1"


# ===========================================================================
# ROLE FAMILY  (closed family — keys replay + audit)
# ===========================================================================


class LlmRole(str, Enum):
    """The closed family of LLM call sites.  Each value keys the replay
    source and is the audit handle for the call.  Adding a site means
    adding a member here AND routing that site through
    :func:`make_chat_model` — there is no other way to construct a model.

    A ``str`` enum so the value is JSON-serialisable and usable directly
    as a filename component in the replay directory.
    """

    SUPERVISOR_ROUTE = "supervisor_route"
    SUPERVISOR_SYNTHESIS = "supervisor_synthesis"
    DOMAIN_REACT = "domain_react"
    SELECTOR = "selector"
    REFERENCE_RESOLVER = "reference_resolver"
    WORKFLOW_ROUTER = "workflow_router"
    COMPOSER_COMPOSE = "composer_compose"
    COMPOSER_REPAIR = "composer_repair"
    GATE = "gate"
    ANSWER = "answer"
    OVERRIDE_CLASSIFIER = "override_classifier"


_ROLE_VALUES: frozenset[str] = frozenset(r.value for r in LlmRole)


def _coerce_role(role: str | LlmRole) -> LlmRole:
    """Validate ``role`` against the closed family; raise typed on miss."""
    if isinstance(role, LlmRole):
        return role
    if role in _ROLE_VALUES:
        return LlmRole(role)
    raise LlmFactoryError(
        f"Unknown LLM role {role!r}.  Role must be one of the closed "
        f"family: {sorted(_ROLE_VALUES)}.  A new call site must add a "
        f"member to orchestrator.llm_factory.LlmRole."
    )


# ===========================================================================
# TYPED ERRORS  (per error_handling.md)
# ===========================================================================


class LlmFactoryError(Exception):
    """Construction-time / config error in the LLM factory.

    Subclasses ``Exception`` (not ``ValueError``) because it is a
    programmer-fix error — an unknown role, or a mutually-exclusive
    argument combination — raised before any compute, and it should not be
    swallowed by an ``except ValueError`` block downstream.
    """


class LlmReplayError(RuntimeError):
    """The replay source could not serve a recorded output for a call.

    Subclasses ``RuntimeError`` because it is a runtime condition outside
    the caller's input control (the fixture is absent / malformed), mirroring
    the conductor's hard guard.  It is raised — never swallowed into a
    fabricated answer and never used to fall through to the live API (P6).
    """


# ===========================================================================
# REPLAY CONTEXT  (which (session, turn) the current call belongs to)
# ===========================================================================
#
# There is no ambient session/turn contextvar in the orchestrator today, so
# the replay source needs an out-of-band signal for which recorded turn to
# serve.  The live backend sets it per turn; tests and the default lane fall
# back to the env-configured session/turn so the structured lane is
# exercisable standalone.

_replay_session: ContextVar[Optional[str]] = ContextVar(
    "_llm_replay_session", default=None
)
_replay_turn: ContextVar[Optional[int]] = ContextVar(
    "_llm_replay_turn", default=None
)

# Per-(session, turn, role) monotonically increasing call counter, so the
# Nth call to a role within a turn reads ``<role>.<N>.json``.  Keyed by a
# tuple; guarded by a lock because LangGraph may invoke nodes concurrently.
_call_counters: dict[tuple[str, int, str], int] = {}
_counter_lock = threading.Lock()


def set_replay_context(*, session_id: str, turn_index: int) -> None:
    """Bind the current (session, turn) for replay key resolution.  The
    live backend calls this at the start of each replayed turn; it also
    resets the per-role call counters for that turn so call indices are
    stable across re-runs of the same turn."""
    _replay_session.set(session_id)
    _replay_turn.set(int(turn_index))
    with _counter_lock:
        stale = [k for k in _call_counters if k[0] == session_id and k[1] == int(turn_index)]
        for k in stale:
            del _call_counters[k]


def _current_session_turn() -> tuple[str, int]:
    """Resolve the (session, turn) for the current call, falling back to
    env defaults so the structured lane is exercisable without a live
    backend setting the context."""
    session = _replay_session.get()
    if session is None:
        session = os.getenv(REPLAY_SESSION_ENV)
    turn = _replay_turn.get()
    if turn is None:
        env_turn = os.getenv(REPLAY_TURN_ENV)
        turn = int(env_turn) if env_turn is not None else 0
    if session is None:
        raise LlmReplayError(
            "Replay is ON but no session is bound.  Set the replay "
            f"context via set_replay_context(...) or the {REPLAY_SESSION_ENV} "
            "env var so the replay source knows which recorded turn to serve."
        )
    return session, int(turn)


def _next_call_index(role: LlmRole) -> int:
    """Return-and-increment the per-(session, turn, role) call counter."""
    session, turn = _current_session_turn()
    key = (session, turn, role.value)
    with _counter_lock:
        idx = _call_counters.get(key, 0)
        _call_counters[key] = idx + 1
    return idx


# ===========================================================================
# REPLAY SOURCE  (directory of recorded outputs)
# ===========================================================================
#
# KEY SCHEME (canonical):
#     <REPLAY_DIR>/<session_id>/<turn_index>/<role>.<call_index>.json
#
# Each file holds the recorded payload for ONE call of ONE role within ONE
# turn.  ``call_index`` starts at 0 and increments per role per turn (so a
# turn that calls ``selector`` for three leaves reads ``selector.0.json``,
# ``selector.1.json``, ``selector.2.json``).
#
# File shapes by lane:
#   * structured (ainvoke) roles — the recorded structured object as JSON,
#     i.e. the fields of the role's Pydantic schema.  The loader validates
#     it against the schema passed to ``with_structured_output`` and, for
#     ``include_raw=True`` sites, wraps it in the
#     ``{"raw", "parsed", "parsing_error"}`` envelope the sites consume.
#   * streamed roles — a JSON object:
#         {"tokens": ["...", "..."],                # supervisor_synthesis
#          "messages": [{"content": "...",          # domain_react (per ainvoke)
#                        "tool_calls": [{"name","args","id"}]}]}
#     ``messages`` is a list because the ReAct loop calls the model once per
#     turn of the agent/tools cycle; each ainvoke pops the next message.
#
# CONDUCTOR ADAPTER:  the conductor (tmp/prompt_tests/sim/conductor.py)
# already writes per-pid directories under tmp/prompt_tests/sim/state/<pid>/
# with role-shaped files (route.json, 02_compose_out.json, 04_gate_out.json,
# 05_answer_out.json, 03_sel_leaf_<id>_out.json).  Those map onto this
# scheme as session=<pid>, turn=0, role per the table below.  The adapter
# mapping is documented here and applied by ``ReplaySource`` when the
# canonical ``<role>.<idx>.json`` is absent but a conductor file exists.

# Conductor filename(s) per role (for the turn-0 single-turn adapter).
_CONDUCTOR_FILES: dict[LlmRole, tuple[str, ...]] = {
    LlmRole.SUPERVISOR_ROUTE: ("route.json",),
    LlmRole.COMPOSER_COMPOSE: ("02_compose_out.json",),
    LlmRole.COMPOSER_REPAIR: ("02_repair_out.json",),
    LlmRole.GATE: ("04_gate_out.json",),
    LlmRole.ANSWER: ("05_answer_out.json",),
    LlmRole.WORKFLOW_ROUTER: ("workflow_route.json",),
}


class ReplaySource:
    """Loads recorded outputs from the replay directory.

    Stateless beyond the resolved root path; one instance is shared by all
    replay-backed runnables in the process (built lazily by the factory).
    """

    def __init__(self, root: Path):
        if not root.is_dir():
            raise LlmReplayError(
                f"Replay is ON but the replay directory does not exist: "
                f"{root}.  Set {REPLAY_DIR_ENV} to a directory of recorded "
                "outputs."
            )
        self._root = root

    @classmethod
    def from_env(cls) -> "ReplaySource":
        raw = os.getenv(REPLAY_DIR_ENV)
        if not raw:
            raise LlmReplayError(
                f"Replay is ON ({REPLAY_FLAG_ENV}=1) but {REPLAY_DIR_ENV} "
                "is not set.  Point it at a directory of recorded outputs."
            )
        return cls(Path(raw))

    def _candidate_paths(
        self, role: LlmRole, call_index: int
    ) -> list[Path]:
        session, turn = _current_session_turn()
        turn_dir = self._root / session / str(turn)
        paths = [turn_dir / f"{role.value}.{call_index}.json"]
        # Conductor adapter: turn-0, call-0 single-turn layout under
        # <root>/<session>/ directly (no turn subdir) OR under the turn dir.
        if call_index == 0:
            for fname in _CONDUCTOR_FILES.get(role, ()):
                paths.append(self._root / session / fname)
                paths.append(turn_dir / fname)
        return paths

    def load_json(self, role: LlmRole, call_index: int) -> Any:
        """Return the parsed JSON payload for (current session/turn, role,
        call_index).  Raise :class:`LlmReplayError` if no file matches."""
        candidates = self._candidate_paths(role, call_index)
        for path in candidates:
            if path.is_file():
                try:
                    return json.loads(path.read_text())
                except json.JSONDecodeError as e:
                    raise LlmReplayError(
                        f"Replay fixture for role {role.value} "
                        f"(call {call_index}) is not valid JSON: {path}"
                    ) from e
        session, turn = _current_session_turn()
        raise LlmReplayError(
            f"No replay fixture for role={role.value} "
            f"session={session} turn={turn} call_index={call_index}.  "
            f"Looked in: {[str(p) for p in candidates]}.  Replay never "
            "falls back to the API (P6) — record the missing fixture or "
            "fix the key."
        )


# Process-shared source, built lazily on first replay call.
_source_singleton: Optional[ReplaySource] = None
_source_lock = threading.Lock()


def _get_source() -> ReplaySource:
    global _source_singleton
    with _source_lock:
        if _source_singleton is None:
            _source_singleton = ReplaySource.from_env()
        return _source_singleton


def reset_replay_source() -> None:
    """Drop the cached :class:`ReplaySource` so a test can repoint
    ``ORCHESTRATOR_LLM_REPLAY_DIR`` between cases.  Also clears the
    per-turn call counters."""
    global _source_singleton
    with _source_lock:
        _source_singleton = None
    with _counter_lock:
        _call_counters.clear()


# ===========================================================================
# REPLAY RUNNABLES  (the langchain Runnable surface, replay-backed)
# ===========================================================================
#
# These deliberately implement ONLY the surface the ten sites use:
#   * ainvoke              — structured roles + the ReAct agent node
#   * astream              — supervisor synthesis
#   * astream_events       — present on the base for completeness; the
#                            domain ReAct path drives it via the compiled
#                            LangGraph, which calls the node's ainvoke.
#   * with_structured_output(schema, include_raw=...) — structured roles
#   * bind_tools(tools)    — the ReAct agent
# They are not general ChatAnthropic replacements; they are a replay backing
# for these specific consumption patterns.


def _to_aimessage(payload: dict) -> Any:
    """Build a langchain ``AIMessage`` from a recorded message payload."""
    from langchain_core.messages import AIMessage

    content = payload.get("content", "")
    tool_calls = payload.get("tool_calls", []) or []
    normalised = [
        {
            "name": tc["name"],
            "args": tc.get("args", {}),
            "id": tc.get("id", f"call_{i}"),
            "type": "tool_call",
        }
        for i, tc in enumerate(tool_calls)
    ]
    return AIMessage(content=content, tool_calls=normalised)


class _ReplayStructuredRunnable:
    """Replay backing for a ``with_structured_output(...)`` model.

    Serves the recorded structured object for the bound role.  Honours the
    site's ``include_raw`` contract: when True, returns the
    ``{"raw", "parsed", "parsing_error"}`` envelope; when False, returns the
    parsed object directly (the reference_resolver shape).
    """

    def __init__(
        self,
        *,
        role: LlmRole,
        schema: type[BaseModel],
        include_raw: bool,
    ):
        self._role = role
        self._schema = schema
        self._include_raw = include_raw

    def _build(self) -> Any:
        call_index = _next_call_index(self._role)
        data = _get_source().load_json(self._role, call_index)
        try:
            parsed = self._schema.model_validate(data)
        except Exception as e:
            raise LlmReplayError(
                f"Replay fixture for role {self._role.value} "
                f"(call {call_index}) does not validate against "
                f"{self._schema.__name__}: {e}"
            ) from e
        if not self._include_raw:
            return parsed
        from langchain_core.messages import AIMessage

        raw = AIMessage(content=json.dumps(data))
        return {"raw": raw, "parsed": parsed, "parsing_error": None}

    async def ainvoke(self, _messages: Any, *args: Any, **kwargs: Any) -> Any:
        return self._build()

    def invoke(self, _messages: Any, *args: Any, **kwargs: Any) -> Any:
        return self._build()


class _ReplayToolBoundRunnable:
    """Replay backing for a ``bind_tools(tools)`` model (the ReAct agent).

    Each ``ainvoke`` pops the next recorded ``AIMessage`` for the role, so a
    multi-step ReAct loop (tool_call → tool_result → answer) replays the
    recorded sequence of model turns.  The recorded tool_calls drive the
    REAL MCP tools to execute against the dev DB — only the decision is
    replayed (per the SPEC's record-the-decision design)."""

    def __init__(self, *, role: LlmRole):
        self._role = role

    async def ainvoke(self, _messages: Any, *args: Any, **kwargs: Any) -> Any:
        # A ReAct turn is the Nth call to the model within the agent/tools
        # loop.  The fixture for one turn is ONE file
        # (``domain_react.0.json``) holding {"messages": [msg0, msg1, ...]},
        # where msg0 is the first model turn (tool_call), msg1 the next, etc.
        # The running per-role counter selects which message; the file index
        # stays 0 because all turns of one ReAct loop live in one fixture.
        # (A fixture may instead be a bare message object for the trivial
        # single-turn case.)
        seq_index = _next_call_index(self._role)
        data = _get_source().load_json(self._role, 0)
        if isinstance(data, dict) and "messages" in data:
            messages = data["messages"]
            if seq_index >= len(messages):
                raise LlmReplayError(
                    f"domain_react fixture exhausted: requested message "
                    f"{seq_index} but fixture has {len(messages)}."
                )
            return _to_aimessage(messages[seq_index])
        if isinstance(data, dict):
            return _to_aimessage(data)
        raise LlmReplayError(
            f"domain_react fixture for message {seq_index} has unexpected "
            f"shape (expected object with 'messages' or a message object)."
        )


class ReplayChatModel:
    """Replay-backed stand-in for ``ChatAnthropic`` at the factory boundary.

    Implements only the Runnable surface the ten sites use.  Construction
    performs NO network work and builds NO ``ChatAnthropic`` (the whole
    point of the seam)."""

    def __init__(self, *, role: LlmRole):
        self._role = role

    # -- structured / tools wrappers ------------------------------------
    def with_structured_output(
        self, schema: type[BaseModel], *, include_raw: bool = True
    ) -> _ReplayStructuredRunnable:
        # Supervisor special case: ONE base is shared by route (structured)
        # and synthesis (.astream).  The base is keyed SUPERVISOR_SYNTHESIS
        # because synthesis streams off the bare base; the structured
        # wrapper is ONLY used by route, so re-key it to SUPERVISOR_ROUTE
        # here so the two roles stay distinguishable in the replay source.
        # Every other site builds a dedicated structured model whose role
        # already matches, so this re-key is a no-op for them.
        wrapper_role = (
            LlmRole.SUPERVISOR_ROUTE
            if self._role is LlmRole.SUPERVISOR_SYNTHESIS
            else self._role
        )
        return _ReplayStructuredRunnable(
            role=wrapper_role, schema=schema, include_raw=include_raw
        )

    def bind_tools(self, _tools: Any) -> _ReplayToolBoundRunnable:
        return _ReplayToolBoundRunnable(role=self._role)

    # -- streamed-token surface (supervisor synthesis) ------------------
    async def astream(
        self, _messages: Any, *args: Any, **kwargs: Any
    ) -> AsyncIterator[Any]:
        from langchain_core.messages import AIMessageChunk

        call_index = _next_call_index(self._role)
        data = _get_source().load_json(self._role, call_index)
        tokens = data.get("tokens") if isinstance(data, dict) else None
        if tokens is None:
            raise LlmReplayError(
                f"streamed fixture for role {self._role.value} "
                f"(call {call_index}) must be an object with a 'tokens' list."
            )
        for tok in tokens:
            yield AIMessageChunk(content=tok)

    # -- bare ainvoke (not used by the current sites, kept for surface) --
    async def ainvoke(self, _messages: Any, *args: Any, **kwargs: Any) -> Any:
        from langchain_core.messages import AIMessage

        call_index = _next_call_index(self._role)
        data = _get_source().load_json(self._role, call_index)
        if isinstance(data, dict) and "content" in data:
            return _to_aimessage(data)
        return AIMessage(content=json.dumps(data))

    # -- astream_events surface (driven by LangGraph in the ReAct path) --
    async def astream_events(
        self, *args: Any, **kwargs: Any
    ) -> AsyncIterator[Any]:
        # The domain ReAct path calls astream_events on the COMPILED
        # LangGraph, not on the model — LangGraph wraps the node's ainvoke
        # (served by _ReplayToolBoundRunnable.ainvoke).  This stub keeps the
        # model's own surface complete for any direct caller.
        raise LlmReplayError(
            "ReplayChatModel.astream_events called directly; the ReAct path "
            "must drive events via the compiled LangGraph (which calls the "
            "bound model's ainvoke)."
        )
        # pragma: no cover - generator marker so the body is async-gen typed
        yield  # noqa: unreachable


# ===========================================================================
# THE FACTORY CHOKEPOINT
# ===========================================================================


def make_chat_model(
    *,
    role: str | LlmRole,
    model_name: str,
    temperature: float,
    max_tokens: int,
    structured_output: Optional[type[BaseModel]] = None,
    include_raw: bool = True,
    tools: Optional[list] = None,
) -> Any:
    """Construct the model for one LLM call site.

    ``role`` keys replay + audit and MUST be a member of :class:`LlmRole`.

    Default (flag unset): builds
    ``ChatAnthropic(**anthropic_chat_kwargs(...))`` and applies
    ``.with_structured_output(structured_output, include_raw=include_raw)``
    if a schema is given, else ``.bind_tools(tools)`` if tools are given,
    else returns the bare base.  Byte-identical to each site's prior
    construction (P1).

    Replay (``ORCHESTRATOR_LLM_REPLAY=1``): builds NO ``ChatAnthropic`` and
    returns a :class:`ReplayChatModel` (or its structured/tool-bound
    wrapper) backed by the replay source.

    ``structured_output`` and ``tools`` are mutually exclusive (no site
    needs both); passing both is a programmer error.
    """
    role_enum = _coerce_role(role)

    if structured_output is not None and tools is not None:
        raise LlmFactoryError(
            f"make_chat_model(role={role_enum.value}): structured_output and "
            "tools are mutually exclusive; no call site binds tools to a "
            "structured-output model."
        )

    if replay_enabled():
        base = ReplayChatModel(role=role_enum)
        if structured_output is not None:
            return base.with_structured_output(
                structured_output, include_raw=include_raw
            )
        if tools is not None:
            return base.bind_tools(tools)
        return base

    # -- default-OFF: real ChatAnthropic, byte-identical to the sites -----
    from langchain_anthropic import ChatAnthropic

    base = ChatAnthropic(
        **anthropic_chat_kwargs(
            model_name=model_name,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    )
    if structured_output is not None:
        return base.with_structured_output(
            structured_output, include_raw=include_raw
        )
    if tools is not None:
        return base.bind_tools(tools)
    return base
