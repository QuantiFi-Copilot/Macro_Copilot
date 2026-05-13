"""tests/test_r5_backend_changes.py — Phase R5 backend changes.

Focused unit tests for the R5 backend deliverables:

  R5.1 — conditional_aggregate event-offset preview labels
  R5.5 — override-classifier heuristic patterns
  R5.6 — workspace_repo forkability guard

Each suite is isolated (no DB, no MCP, no LLM) so they run in
the standard pytest tree.  Integration tests for R5.2 (payload
endpoint) and R5.3 (supervisor persistence) require a live engine
+ object storage and live in the integration suite — see the
corresponding ``tests/integration/`` files for full plumbing.
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# R5.1 — event-offset preview labels
# ---------------------------------------------------------------------------


class TestEventOffsetPreview:
    """Conditional_aggregate emits a Series whose synthetic anchor index
    used to render as 1970-01-01 → 1970-01-06 in the UI preview.  R5.1
    detects the encoding on the lineage and substitutes "Day -5" / "Day
    +5" / "Day 0" labels in the preview path."""

    def test_format_event_offset_zero(self):
        from state.artifact_store import _format_event_offset
        assert _format_event_offset(0) == "Day 0"

    def test_format_event_offset_positive(self):
        from state.artifact_store import _format_event_offset
        assert _format_event_offset(3) == "Day +3"

    def test_format_event_offset_negative(self):
        from state.artifact_store import _format_event_offset
        assert _format_event_offset(-5) == "Day -5"

    def test_extract_preview_event_offset_encoding(self):
        """``_extract_preview`` sees ``index_encoding.kind == 'event_offset'``
        in the payload and surfaces "Day N" labels."""
        from state.artifact_store import _extract_preview

        inline_payload = {
            "artifact_type": "Series",
            "payload": {
                "index": ["1970-01-01T00:00:00", "1970-01-02T00:00:00"],
                "values": [0.0, -0.06],
                "index_encoding": {
                    "kind": "event_offset",
                    "anchor": "1970-01-01",
                    "offsets": [0, 1],
                },
            },
        }
        idx, vals = _extract_preview(inline_payload)
        assert idx == ["Day 0", "Day +1"]
        assert vals == [0.0, -0.06]

    def test_extract_preview_regular_series_unchanged(self):
        """Series without event-offset encoding still uses the raw
        ISO date index."""
        from state.artifact_store import _extract_preview

        inline_payload = {
            "artifact_type": "Series",
            "payload": {
                "index": ["2024-01-02", "2024-01-03"],
                "values": [50.4, 51.1],
            },
        }
        idx, vals = _extract_preview(inline_payload)
        assert idx == ["2024-01-02", "2024-01-03"]
        assert vals == [50.4, 51.1]

    def test_extract_preview_eventset_unchanged(self):
        """EventSet preview path is not affected by R5.1 — it reads
        ``mask_index`` not ``index_encoding``."""
        from state.artifact_store import _extract_preview

        inline_payload = {
            "artifact_type": "EventSet",
            "payload": {
                "mask_index": ["2024-01-02", "2024-01-03"],
                "mask_values": [True, False],
            },
        }
        idx, vals = _extract_preview(inline_payload)
        assert idx == ["2024-01-02", "2024-01-03"]
        assert vals == [1.0, 0.0]


# ---------------------------------------------------------------------------
# R5.5 — override classifier heuristics
# ---------------------------------------------------------------------------


class TestOverrideClassifier:
    """The heuristic classifier maps common parameter-change phrases to
    structured ProposedOverride objects."""

    def test_change_window_to_126(self):
        from orchestrator.override_classifier import classify_overrides
        out = classify_overrides("Change the z-score window to 126d.")
        assert len(out) == 1
        assert out[0].path == ("window_days",)
        assert out[0].value == 126
        assert out[0].value_label == "126d"

    def test_change_lookback_to_252(self):
        from orchestrator.override_classifier import classify_overrides
        out = classify_overrides("Change the lookback window to 252d.")
        assert len(out) == 1
        assert out[0].path == ("lookback_days",)
        assert out[0].value == 252

    def test_use_act_365(self):
        from orchestrator.override_classifier import classify_overrides
        out = classify_overrides("Use ACT/365 instead.")
        assert len(out) == 1
        assert out[0].path == ("day_count",)
        assert out[0].value == "ACT/365"
        assert out[0].value_label == "ACT/365"

    def test_switch_to_act_360(self):
        from orchestrator.override_classifier import classify_overrides
        out = classify_overrides("Switch to ACT/360 day-count.")
        assert any(o.value == "ACT/360" for o in out)

    def test_inline_window(self):
        from orchestrator.override_classifier import classify_overrides
        out = classify_overrides("Use a 22-day rolling window.")
        assert len(out) >= 1
        assert out[0].path == ("window_days",)
        assert out[0].value == 22

    def test_unrelated_message_returns_empty(self):
        from orchestrator.override_classifier import classify_overrides
        out = classify_overrides("What is the 2s10s spread today?")
        assert out == []

    def test_to_wire_shape(self):
        """The ProposedOverride.to_wire() output matches the snake_case
        shape the frontend expects on the WebSocket ``done`` event."""
        from orchestrator.override_classifier import classify_overrides
        out = classify_overrides("Change the window to 126d.")
        wire = out[0].to_wire()
        assert set(wire.keys()) == {
            "id",
            "path",
            "value",
            "value_label",
            "node_id",
            "previous_value",
            "rationale",
        }
        assert wire["path"] == ["window_days"]
        assert wire["value"] == 126


# ---------------------------------------------------------------------------
# R5.6 — forkability consistency guard
# ---------------------------------------------------------------------------


class TestForkabilityGuard:
    """``create_workspace`` raises when template_id is set but
    bound_slot_values is None, or vice versa.  Both NULL (legacy /
    supervisor-persisted) and both populated (template-derived) are
    accepted."""

    def test_template_id_without_slot_values_rejected(self):
        """Setting template_id without bound_slot_values is an
        inconsistent forkable state."""
        from state.workspace_repo import create_workspace

        with pytest.raises(ValueError, match="template_id and bound_slot_values"):
            # We never reach the SQL — the guard fires first.  Pass a
            # None connection so any post-guard code would crash with
            # AttributeError if the guard somehow lets the call through.
            create_workspace(
                "a" * 64,
                conn=None,  # type: ignore[arg-type]
                template_id="some_template",
                bound_slot_values=None,
            )

    def test_slot_values_without_template_id_rejected(self):
        from state.workspace_repo import create_workspace

        with pytest.raises(ValueError, match="template_id and bound_slot_values"):
            create_workspace(
                "a" * 64,
                conn=None,  # type: ignore[arg-type]
                template_id=None,
                bound_slot_values={"signal_threshold": 0.5},
            )


# ---------------------------------------------------------------------------
# R5.3 — supervisor persistence helper: shape-level assertions
# ---------------------------------------------------------------------------


class TestSupervisorPersistenceHelper:
    """The helper module loads cleanly and its public signature stays
    stable so future wiring is a mechanical change.  Integration tests
    (real DB + tool execution + artifact persistence) live in the
    integration suite once the tool_output plumbing into ChildResponse
    is in place."""

    def test_helper_importable(self):
        from orchestrator.supervisor_persistence import (
            persist_supervisor_workspace_from_tool_call,
        )
        assert callable(persist_supervisor_workspace_from_tool_call)

    def test_helper_returns_none_for_non_workspace_tool(self):
        """When the tool isn't workspace-eligible, the helper returns
        None without hitting the persistence pipeline."""
        from orchestrator.supervisor_persistence import (
            persist_supervisor_workspace_from_tool_call,
        )
        result = persist_supervisor_workspace_from_tool_call(
            tool_name="get_yield_levels_tool",  # not in workspace set
            params={},
            tool_output={},
            conn=None,  # type: ignore[arg-type]
            object_storage=None,
            primitive_resolver=lambda name: (_ for _ in ()).throw(
                AssertionError("resolver should not be called")
            ),
        )
        assert result is None
