"""tests/state/test_reference_resolver.py — unit tests for the
NL-to-structured reference resolver.

Phase 0 PR 8.

These tests EXERCISE the resolver's parsing + sanitisation layer
without making a real LLM call.  We patch the underlying
``ChatAnthropic`` model so the test can hand back a synthesized
``ReferenceResolution`` or simulate an error.

What we test:

  - Sanitisation drops hallucinated names (names the resolver
    returned that aren't in the visible-names allowlist).
  - Sanitisation drops save_as values that violate the safe-name
    pattern.
  - Timeout / exception in the LLM call -> empty resolution
    (no exception propagated to the caller).
  - Output that is not a ``ReferenceResolution`` instance -> empty
    resolution (defensive fallback).

No Postgres is required for this test file; the resolver does not
touch the DB.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest


_PROJECT_ROOT = str(Path(__file__).resolve().parents[2])
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


from orchestrator.reference_resolver import (  # noqa: E402
    ReferenceResolution,
    ReferenceResolver,
    _sanitize,
)


# ============================================================================
# _sanitize — pure-function tests
# ============================================================================


class TestSanitize:
    def test_drops_hallucinated_reference(self):
        raw = ReferenceResolution(
            save_as=None,
            referenced_names=["tips_2y_v1", "never_was_bound"],
        )
        cleaned = _sanitize(raw, visible_names=["tips_2y_v1"])
        assert cleaned.referenced_names == ["tips_2y_v1"]
        assert cleaned.save_as is None

    def test_drops_malformed_save_as(self):
        raw = ReferenceResolution(
            save_as="bad name with spaces",
            referenced_names=[],
        )
        cleaned = _sanitize(raw, visible_names=[])
        assert cleaned.save_as is None

    def test_drops_save_as_with_special_chars(self):
        raw = ReferenceResolution(
            save_as="bad;DROP",
            referenced_names=[],
        )
        cleaned = _sanitize(raw, visible_names=[])
        assert cleaned.save_as is None

    def test_keeps_valid_save_as(self):
        raw = ReferenceResolution(
            save_as="my_artifact_v3",
            referenced_names=[],
        )
        cleaned = _sanitize(raw, visible_names=[])
        assert cleaned.save_as == "my_artifact_v3"

    def test_preserves_order_of_valid_names(self):
        raw = ReferenceResolution(
            save_as=None,
            referenced_names=["c", "a", "b"],
        )
        cleaned = _sanitize(
            raw, visible_names=["a", "b", "c"],
        )
        # Order is the LLM's order; sanitisation is a filter, not a sort.
        assert cleaned.referenced_names == ["c", "a", "b"]

    def test_empty_input_returns_empty(self):
        raw = ReferenceResolution(save_as=None, referenced_names=[])
        cleaned = _sanitize(raw, visible_names=[])
        assert cleaned.referenced_names == []
        assert cleaned.save_as is None


# ============================================================================
# ReferenceResolver — stub-based tests
# ============================================================================


class _StubStructuredModel:
    """Replaces a ``with_structured_output`` model.  ``ainvoke``
    returns whatever the test configured (or raises)."""

    def __init__(self, *, response=None, raises=None, hang_seconds=None):
        self._response = response
        self._raises = raises
        self._hang_seconds = hang_seconds

    async def ainvoke(self, messages):  # noqa: ARG002
        if self._hang_seconds is not None:
            await asyncio.sleep(self._hang_seconds)
        if self._raises is not None:
            raise self._raises
        return self._response


@pytest.fixture
def resolver_factory(monkeypatch):
    """Build a ReferenceResolver with its structured model stubbed.

    ``ChatAnthropic`` and the langchain_core ``SystemMessage`` are
    imported lazily inside ``ReferenceResolver.__init__``.  We stub
    them via ``sys.modules`` so the lazy import resolves to fakes
    that never touch the network or require an API key.
    """
    import types

    def _make(*, response=None, raises=None, hang_seconds=None):
        class _FakeChat:
            def __init__(self, *args, **kwargs):
                pass

            def with_structured_output(self, *args, **kwargs):
                return _StubStructuredModel(
                    response=response,
                    raises=raises,
                    hang_seconds=hang_seconds,
                )

        class _FakeSystemMessage:
            def __init__(self, content):
                self.content = content

        class _FakeHumanMessage:
            def __init__(self, content):
                self.content = content

        fake_la = types.ModuleType("langchain_anthropic")
        fake_la.ChatAnthropic = _FakeChat
        monkeypatch.setitem(sys.modules, "langchain_anthropic", fake_la)

        # Ensure langchain_core.messages provides the names the
        # resolver imports.  If a real langchain_core is already in
        # sys.modules (e.g. via another dependency), we still want
        # the test's fakes used — set both messages submodule and
        # the parent module's `messages` attribute defensively.
        fake_lcm = types.ModuleType("langchain_core.messages")
        fake_lcm.SystemMessage = _FakeSystemMessage
        fake_lcm.HumanMessage = _FakeHumanMessage
        fake_lc = types.ModuleType("langchain_core")
        fake_lc.messages = fake_lcm
        monkeypatch.setitem(sys.modules, "langchain_core", fake_lc)
        monkeypatch.setitem(
            sys.modules, "langchain_core.messages", fake_lcm,
        )

        return ReferenceResolver(model_name="dummy")

    return _make


# Explicit asyncio marker: the repo-root pytest.ini sets
# ``asyncio_mode = auto``, but container runs invoke pytest from the
# Macro_Copilot directory (/app), where that ini is not on the config-
# discovery path — pytest-asyncio then runs in strict mode and skips
# unmarked async tests.  The explicit marker works in both modes.
@pytest.mark.asyncio
class TestResolverCall:
    async def test_returns_resolved_structure(self, resolver_factory):
        resp = ReferenceResolution(
            save_as="my_alias",
            referenced_names=["tips_2y_v1"],
        )
        resolver = resolver_factory(response=resp)
        out = await resolver.resolve(
            "save as my_alias, compare with tips_2y_v1",
            visible_names=["tips_2y_v1"],
        )
        assert out.save_as == "my_alias"
        assert out.referenced_names == ["tips_2y_v1"]

    async def test_hallucinated_names_dropped(self, resolver_factory):
        resp = ReferenceResolution(
            save_as=None,
            referenced_names=["fabricated"],
        )
        resolver = resolver_factory(response=resp)
        out = await resolver.resolve(
            "what's fabricated?",
            visible_names=["tips_2y_v1"],
        )
        assert out.referenced_names == []

    async def test_llm_exception_returns_empty(self, resolver_factory):
        resolver = resolver_factory(
            raises=RuntimeError("network down")
        )
        out = await resolver.resolve(
            "hi", visible_names=[],
        )
        assert out.save_as is None
        assert out.referenced_names == []

    async def test_unexpected_type_returns_empty(self, resolver_factory):
        resolver = resolver_factory(response={"not": "the right type"})
        out = await resolver.resolve(
            "hi", visible_names=[],
        )
        assert out.save_as is None
        assert out.referenced_names == []

    async def test_timeout_returns_empty(self, resolver_factory):
        # hang_seconds > timeout means ainvoke never returns in time.
        resolver = resolver_factory(hang_seconds=5.0)
        out = await resolver.resolve(
            "hi", visible_names=[], timeout_seconds=0.05,
        )
        assert out.save_as is None
        assert out.referenced_names == []
