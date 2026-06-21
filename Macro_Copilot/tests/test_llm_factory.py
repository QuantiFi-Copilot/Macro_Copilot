"""
tests/test_llm_factory.py — the offline LLM replay seam.

Covers the four contract assertions for ``orchestrator.llm_factory``:

1. Default-OFF byte-identity: for each of the 11 role configs, the
   factory's constructed ``ChatAnthropic`` kwargs equal what
   ``anthropic_chat_kwargs`` would build for that site, and the applied
   structured-output / bind_tools wrapper matches the site (model,
   temperature presence/absence per ``_NO_SAMPLING_PARAM_PREFIXES``,
   max_tokens, schema, include_raw, bind_tools).  No network call.
2. Flag-ON no-API: with ``ORCHESTRATOR_LLM_REPLAY=1`` + a fixture replay
   dir, a replayed structured call (composer_compose ainvoke) runs
   end-to-end and returns the recorded object, while ``ChatAnthropic`` is
   monkeypatched to raise on construction (so any API construction fails
   the test loudly).
3. Replay-miss → ``LlmReplayError`` (no API, no fabrication).
4. Streaming role replay: supervisor_synthesis (astream tokens) and
   domain_react (astream_events: bind_tools ainvoke serving tool_call then
   answer) replay correctly.

NEVER hits the live API; NEVER runs the full suite (touches no DB).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict

from orchestrator import config, llm_factory
from orchestrator.llm_factory import (
    LlmFactoryError,
    LlmReplayError,
    LlmRole,
    make_chat_model,
)


# ===========================================================================
# Helpers
# ===========================================================================


class _SpyChatAnthropic:
    """Records constructor kwargs + the wrappers applied to it, so the
    default-OFF path can be asserted without any network call."""

    def __init__(self, **kwargs):
        self.init_kwargs = dict(kwargs)
        self.structured_call = None  # (schema, include_raw)
        self.bound_tools = None

    def with_structured_output(self, schema, *, include_raw=True):
        self.structured_call = (schema, include_raw)
        return self  # the spy stands in for the wrapper too

    def bind_tools(self, tools):
        self.bound_tools = list(tools)
        return self


class _RaisingChatAnthropic:
    """Constructing this is an immediate failure — used to prove the
    flag-ON path never builds a real model."""

    def __init__(self, **kwargs):  # pragma: no cover - must never run
        raise AssertionError(
            "ChatAnthropic was constructed while replay was ON — the seam "
            "must build NO ChatAnthropic and make NO API call."
        )


@pytest.fixture
def spy_chat_anthropic(monkeypatch):
    """Monkeypatch ``langchain_anthropic.ChatAnthropic`` to the recording
    spy and return the list of instances created."""
    import langchain_anthropic

    created: list[_SpyChatAnthropic] = []

    def _factory(**kwargs):
        inst = _SpyChatAnthropic(**kwargs)
        created.append(inst)
        return inst

    monkeypatch.setattr(langchain_anthropic, "ChatAnthropic", _factory)
    return created


@pytest.fixture(autouse=True)
def _clean_replay_env(monkeypatch):
    """Ensure each test starts with replay OFF and a fresh source/context."""
    monkeypatch.delenv(llm_factory.REPLAY_FLAG_ENV, raising=False)
    monkeypatch.delenv(llm_factory.REPLAY_DIR_ENV, raising=False)
    monkeypatch.delenv(llm_factory.REPLAY_SESSION_ENV, raising=False)
    monkeypatch.delenv(llm_factory.REPLAY_TURN_ENV, raising=False)
    llm_factory.reset_replay_source()
    yield
    llm_factory.reset_replay_source()


class _Schema(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    value: str = "x"


# The 11 role configs as the sites construct them (model knob, temperature,
# max_tokens, structured schema / include_raw / tools).  Mirrors the real
# call sites so byte-identity is asserted per role.  ``schema`` is a stand-in
# (_Schema) — the byte-identity assertion is about the ChatAnthropic kwargs +
# the wrapper shape (which schema object is forwarded, include_raw, tools),
# not about the specific Pydantic class.
def _role_configs():
    return [
        # (role, model_name, temperature, max_tokens, schema, include_raw, tools)
        (LlmRole.SUPERVISOR_ROUTE, config.LLM_MODEL, 0.0, 1024, _Schema, True, None),
        (LlmRole.SUPERVISOR_SYNTHESIS, config.LLM_MODEL, 0.0, 1024, None, True, None),
        (LlmRole.DOMAIN_REACT, config.LLM_MODEL, 0.0, 4096, None, True, ["t1", "t2"]),
        (LlmRole.SELECTOR, config.LLM_MODEL, 0.0, 4096, _Schema, True, None),
        (LlmRole.REFERENCE_RESOLVER, config.LLM_MODEL, 0.0, 256, _Schema, False, None),
        (LlmRole.WORKFLOW_ROUTER, config.LLM_MODEL, 0.0, 1024, _Schema, True, None),
        (LlmRole.COMPOSER_COMPOSE, config.COMPOSER_MODEL, 0.0, 4096, _Schema, True, None),
        (LlmRole.COMPOSER_REPAIR, config.COMPOSER_MODEL, 0.0, 4096, _Schema, True, None),
        (LlmRole.GATE, config.GATE_MODEL, 0.0, 4096, _Schema, True, None),
        (LlmRole.ANSWER, config.ANSWER_MODEL, 0.0, 4096, _Schema, True, None),
        (LlmRole.OVERRIDE_CLASSIFIER, config.OVERRIDE_CLASSIFIER_MODEL, 0.0, 400, _Schema, False, None),
    ]


# ===========================================================================
# 1. Default-OFF byte-identity
# ===========================================================================


@pytest.mark.parametrize("cfg", _role_configs(), ids=lambda c: c[0].value)
def test_default_off_byte_identity(cfg, spy_chat_anthropic):
    role, model_name, temperature, max_tokens, schema, include_raw, tools = cfg

    result = make_chat_model(
        role=role,
        model_name=model_name,
        temperature=temperature,
        max_tokens=max_tokens,
        structured_output=schema,
        include_raw=include_raw,
        tools=tools,
    )

    # Exactly one ChatAnthropic constructed.
    assert len(spy_chat_anthropic) == 1
    inst = spy_chat_anthropic[0]

    # Constructor kwargs equal anthropic_chat_kwargs for this site —
    # crucially this is where the 6 ex-bypassers now get temperature
    # correctly stripped for no-sampling models (KI-09).
    expected_kwargs = config.anthropic_chat_kwargs(
        model_name=model_name, temperature=temperature, max_tokens=max_tokens
    )
    assert inst.init_kwargs == expected_kwargs

    # Wrapper applied per the site.
    if schema is not None:
        assert inst.structured_call == (schema, include_raw)
        assert inst.bound_tools is None
    elif tools is not None:
        assert inst.bound_tools == tools
        assert inst.structured_call is None
    else:
        assert inst.structured_call is None
        assert inst.bound_tools is None
    assert result is inst


def test_no_sampling_model_strips_temperature(spy_chat_anthropic, monkeypatch):
    """A no-sampling model (Opus 4.8 / Fable) must get NO temperature key —
    the latent-400 fix the seam delivers for the ex-bypassers."""
    make_chat_model(
        role=LlmRole.DOMAIN_REACT,
        model_name="claude-opus-4-8",
        temperature=0.0,
        max_tokens=4096,
        tools=["t"],
    )
    inst = spy_chat_anthropic[0]
    assert "temperature" not in inst.init_kwargs
    assert inst.init_kwargs == {"model": "claude-opus-4-8", "max_tokens": 4096}


def test_sampling_model_keeps_temperature(spy_chat_anthropic):
    make_chat_model(
        role=LlmRole.GATE,
        model_name="claude-sonnet-4-6",
        temperature=0.0,
        max_tokens=4096,
        structured_output=_Schema,
        include_raw=True,
    )
    inst = spy_chat_anthropic[0]
    assert inst.init_kwargs["temperature"] == 0.0


def test_unknown_role_raises_typed():
    with pytest.raises(LlmFactoryError):
        make_chat_model(
            role="not_a_real_role",
            model_name="claude-sonnet-4-6",
            temperature=0.0,
            max_tokens=10,
        )


def test_structured_and_tools_mutually_exclusive(spy_chat_anthropic):
    with pytest.raises(LlmFactoryError):
        make_chat_model(
            role=LlmRole.DOMAIN_REACT,
            model_name="claude-sonnet-4-6",
            temperature=0.0,
            max_tokens=10,
            structured_output=_Schema,
            tools=["t"],
        )


# ===========================================================================
# Fixture-dir builders for the flag-ON tests
# ===========================================================================


def _write_fixture(root: Path, session: str, turn: int, fname: str, payload):
    d = root / session / str(turn)
    d.mkdir(parents=True, exist_ok=True)
    (d / fname).write_text(json.dumps(payload))


def _enable_replay(monkeypatch, root: Path, session="sess", turn=0):
    monkeypatch.setenv(llm_factory.REPLAY_FLAG_ENV, "1")
    monkeypatch.setenv(llm_factory.REPLAY_DIR_ENV, str(root))
    llm_factory.reset_replay_source()
    llm_factory.set_replay_context(session_id=session, turn_index=turn)


# ===========================================================================
# 2. Flag-ON no-API: replayed structured call end-to-end
# ===========================================================================


@pytest.mark.asyncio
async def test_replay_structured_no_api(tmp_path, monkeypatch):
    """composer_compose ainvoke replays the recorded ComposerLLMOutput and
    constructs NO ChatAnthropic."""
    import langchain_anthropic

    from orchestrator.open_dag.composer import ComposerLLMOutput

    monkeypatch.setattr(langchain_anthropic, "ChatAnthropic", _RaisingChatAnthropic)

    recorded = {
        "workflow_id": "wf_demo",
        "terminal_node_id": "leaf_a",
        "rationale": "replayed",
        "leaf_holes": [],
        "operator_nodes": [],
        "edges": [],
        "literal_bindings": [],
    }
    _write_fixture(tmp_path, "sess", 0, "composer_compose.0.json", recorded)
    _enable_replay(monkeypatch, tmp_path)

    model = make_chat_model(
        role=LlmRole.COMPOSER_COMPOSE,
        model_name=config.COMPOSER_MODEL,
        temperature=0.0,
        max_tokens=4096,
        structured_output=ComposerLLMOutput,
        include_raw=True,
    )
    result = await model.ainvoke(["irrelevant messages"])

    # include_raw=True envelope shape, exactly what the composer site reads.
    assert set(result.keys()) == {"raw", "parsed", "parsing_error"}
    assert result["parsing_error"] is None
    assert isinstance(result["parsed"], ComposerLLMOutput)
    assert result["parsed"].workflow_id == "wf_demo"
    assert result["parsed"].terminal_node_id == "leaf_a"


@pytest.mark.asyncio
async def test_replay_include_raw_false_returns_parsed(tmp_path, monkeypatch):
    """reference_resolver (include_raw=False) returns the parsed object
    directly, not the envelope."""
    import langchain_anthropic

    monkeypatch.setattr(langchain_anthropic, "ChatAnthropic", _RaisingChatAnthropic)
    _write_fixture(tmp_path, "sess", 0, "reference_resolver.0.json", {"value": "resolved"})
    _enable_replay(monkeypatch, tmp_path)

    model = make_chat_model(
        role=LlmRole.REFERENCE_RESOLVER,
        model_name=config.LLM_MODEL,
        temperature=0.0,
        max_tokens=256,
        structured_output=_Schema,
        include_raw=False,
    )
    result = await model.ainvoke(["x"])
    assert isinstance(result, _Schema)
    assert result.value == "resolved"


@pytest.mark.asyncio
async def test_replay_conductor_adapter(tmp_path, monkeypatch):
    """The conductor's per-pid ``02_compose_out.json`` is served via the
    adapter when the canonical ``composer_compose.0.json`` is absent."""
    import langchain_anthropic

    from orchestrator.open_dag.composer import ComposerLLMOutput

    monkeypatch.setattr(langchain_anthropic, "ChatAnthropic", _RaisingChatAnthropic)
    # Conductor layout: <root>/<pid>/02_compose_out.json (no turn subdir).
    (tmp_path / "pid7").mkdir(parents=True)
    (tmp_path / "pid7" / "02_compose_out.json").write_text(
        json.dumps({"workflow_id": "from_conductor", "terminal_node_id": "t"})
    )
    _enable_replay(monkeypatch, tmp_path, session="pid7", turn=0)

    model = make_chat_model(
        role=LlmRole.COMPOSER_COMPOSE,
        model_name=config.COMPOSER_MODEL,
        temperature=0.0,
        max_tokens=4096,
        structured_output=ComposerLLMOutput,
        include_raw=True,
    )
    result = await model.ainvoke(["x"])
    assert result["parsed"].workflow_id == "from_conductor"


# ===========================================================================
# 3. Replay-miss → LlmReplayError
# ===========================================================================


@pytest.mark.asyncio
async def test_replay_miss_raises_typed(tmp_path, monkeypatch):
    import langchain_anthropic

    from orchestrator.open_dag.composer import ComposerLLMOutput

    monkeypatch.setattr(langchain_anthropic, "ChatAnthropic", _RaisingChatAnthropic)
    _enable_replay(monkeypatch, tmp_path)  # empty dir → miss

    model = make_chat_model(
        role=LlmRole.COMPOSER_COMPOSE,
        model_name=config.COMPOSER_MODEL,
        temperature=0.0,
        max_tokens=4096,
        structured_output=ComposerLLMOutput,
        include_raw=True,
    )
    with pytest.raises(LlmReplayError):
        await model.ainvoke(["x"])


@pytest.mark.asyncio
async def test_replay_missing_dir_raises_typed(monkeypatch):
    monkeypatch.setenv(llm_factory.REPLAY_FLAG_ENV, "1")
    monkeypatch.setenv(llm_factory.REPLAY_DIR_ENV, "/nonexistent/replay/dir/xyz")
    llm_factory.reset_replay_source()
    llm_factory.set_replay_context(session_id="sess", turn_index=0)
    model = make_chat_model(
        role=LlmRole.COMPOSER_COMPOSE,
        model_name=config.COMPOSER_MODEL,
        temperature=0.0,
        max_tokens=4096,
        structured_output=_Schema,
        include_raw=True,
    )
    with pytest.raises(LlmReplayError):
        await model.ainvoke(["x"])


# ===========================================================================
# 4. Streaming role replay
# ===========================================================================


@pytest.mark.asyncio
async def test_replay_supervisor_synthesis_astream(tmp_path, monkeypatch):
    """supervisor_synthesis streams recorded tokens off the bare base."""
    import langchain_anthropic

    monkeypatch.setattr(langchain_anthropic, "ChatAnthropic", _RaisingChatAnthropic)
    _write_fixture(
        tmp_path, "sess", 0, "supervisor_synthesis.0.json",
        {"tokens": ["The ", "10Y ", "is ", "4.2%."]},
    )
    _enable_replay(monkeypatch, tmp_path)

    base = make_chat_model(
        role=LlmRole.SUPERVISOR_SYNTHESIS,
        model_name=config.LLM_MODEL,
        temperature=0.0,
        max_tokens=1024,
    )
    chunks = []
    async for chunk in base.astream(["x"]):
        chunks.append(chunk.content)
    assert "".join(chunks) == "The 10Y is 4.2%."


@pytest.mark.asyncio
async def test_replay_supervisor_route_off_shared_base(tmp_path, monkeypatch):
    """The supervisor's ONE base re-keys its structured wrapper to
    supervisor_route while .astream stays supervisor_synthesis."""
    import langchain_anthropic

    monkeypatch.setattr(langchain_anthropic, "ChatAnthropic", _RaisingChatAnthropic)
    _write_fixture(tmp_path, "sess", 0, "supervisor_route.0.json", {"value": "routed"})
    _enable_replay(monkeypatch, tmp_path)

    base = make_chat_model(
        role=LlmRole.SUPERVISOR_SYNTHESIS,
        model_name=config.LLM_MODEL,
        temperature=0.0,
        max_tokens=1024,
    )
    route_model = base.with_structured_output(_Schema, include_raw=True)
    result = await route_model.ainvoke(["x"])
    assert result["parsed"].value == "routed"


@pytest.mark.asyncio
async def test_replay_domain_react_tool_then_answer(tmp_path, monkeypatch):
    """domain_react (bind_tools) serves a recorded tool_call message, then a
    recorded final-answer message — the two ReAct ainvoke turns."""
    import langchain_anthropic

    monkeypatch.setattr(langchain_anthropic, "ChatAnthropic", _RaisingChatAnthropic)
    _write_fixture(
        tmp_path, "sess", 0, "domain_react.0.json",
        {
            "messages": [
                {
                    "content": "",
                    "tool_calls": [
                        {"name": "get_yield_tool", "args": {"tenor": "10Y"}, "id": "c1"}
                    ],
                },
                {"content": "The 10Y yield is 4.2%.", "tool_calls": []},
            ]
        },
    )
    _enable_replay(monkeypatch, tmp_path)

    model = make_chat_model(
        role=LlmRole.DOMAIN_REACT,
        model_name=config.LLM_MODEL,
        temperature=0.0,
        max_tokens=4096,
        tools=["get_yield_tool"],
    )
    # First ainvoke → tool_call message (drives the REAL tool to execute).
    first = await model.ainvoke(["x"])
    assert first.tool_calls
    assert first.tool_calls[0]["name"] == "get_yield_tool"
    assert first.tool_calls[0]["args"] == {"tenor": "10Y"}
    # Second ainvoke → final answer, no tool_calls.
    second = await model.ainvoke(["x"])
    assert second.content == "The 10Y yield is 4.2%."
    assert not second.tool_calls


@pytest.mark.asyncio
async def test_replay_per_role_call_index_increments(tmp_path, monkeypatch):
    """Two selector leaves within one turn read selector.0 then selector.1."""
    import langchain_anthropic

    monkeypatch.setattr(langchain_anthropic, "ChatAnthropic", _RaisingChatAnthropic)
    _write_fixture(tmp_path, "sess", 0, "selector.0.json", {"value": "leaf_a"})
    _write_fixture(tmp_path, "sess", 0, "selector.1.json", {"value": "leaf_b"})
    _enable_replay(monkeypatch, tmp_path)

    model = make_chat_model(
        role=LlmRole.SELECTOR,
        model_name=config.LLM_MODEL,
        temperature=0.0,
        max_tokens=4096,
        structured_output=_Schema,
        include_raw=True,
    )
    r0 = await model.ainvoke(["x"])
    r1 = await model.ainvoke(["x"])
    assert r0["parsed"].value == "leaf_a"
    assert r1["parsed"].value == "leaf_b"
