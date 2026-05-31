"""tests/orchestrator/open_dag/test_composability_audit.py — PR-A3 corrective.

Covers the open-DAG composability audit (plan ``tmp/orchestration.md``
§2.5 + PR-3 acceptance criterion #4).

The acceptance test: running ``audit_resolver`` over the entire live
rates resolver MUST classify every primitive as one of
``BRIDGEABLE_SERIES``, ``BRIDGEABLE_PANEL``, or
``TERMINAL_ONLY_SNAPSHOT``.  ZERO primitives may be ``UNDECLARED`` —
that's the honesty check the plan demanded.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel

from shared.workflow import PrimitiveSpec
from orchestrator.open_dag.composability_audit import (
    Composability,
    CompositionAuditEntry,
    audit_resolver,
    by_classification,
    classify_primitive,
)


# ============================================================================
# CLASSIFIER UNIT TESTS — synthetic specs
# ============================================================================


class _In(BaseModel):
    pass


class _Out(BaseModel):
    pass


def _spec(
    name: str,
    artifact_type: str = "Series",
    units: dict | None = None,
) -> PrimitiveSpec:
    return PrimitiveSpec(
        tool_name=name,
        callable=lambda **kw: None,
        input_class=_In,
        output_class=_Out,
        config_path=Path("/dev/null"),
        output_artifact_type=artifact_type,
        output_field_units=units if units is not None else {},
    )


class TestClassifier:
    def test_series_with_units_is_bridgeable_series(self) -> None:
        entry = classify_primitive(
            _spec("s_with_units", "Series", {"time_series": "bps"}),
        )
        assert entry.classification == Composability.BRIDGEABLE_SERIES
        assert "Series" in entry.rationale

    def test_panel_is_bridgeable_panel(self) -> None:
        entry = classify_primitive(_spec("p", "Panel"))
        assert entry.classification == Composability.BRIDGEABLE_PANEL

    def test_series_without_units_is_terminal_only(self) -> None:
        entry = classify_primitive(_spec("s_empty", "Series", {}))
        assert entry.classification == Composability.TERMINAL_ONLY_SNAPSHOT
        assert "snapshot" in entry.rationale.lower()

    def test_unknown_artifact_type_is_undeclared(self) -> None:
        # PrimitiveSpec accepts any string for output_artifact_type at
        # construction; the classifier rejects it.
        entry = classify_primitive(
            _spec("u", "NotAnArtifactType", {"time_series": "bps"}),
        )
        assert entry.classification == Composability.UNDECLARED

    def test_unsupported_artifact_type_is_undeclared(self) -> None:
        # SeriesSet et al. are valid ArtifactTypeName members but no
        # registered primitive emits them today — the classifier
        # surfaces this as UNDECLARED so an extension is forced to
        # update both the bridge and the classifier in lock-step.
        entry = classify_primitive(_spec("u2", "SeriesSet"))
        assert entry.classification == Composability.UNDECLARED


# ============================================================================
# RESOLVER-WIDE AUDIT
# ============================================================================


class TestResolverAudit:
    def test_audit_returns_one_entry_per_tool(self) -> None:
        from rates_agent.workflows import (
            known_rates_primitives,
            rates_primitive_resolver,
        )

        names = known_rates_primitives()
        audit = audit_resolver(rates_primitive_resolver, names)
        assert set(audit.keys()) == set(names)

    def test_audit_no_primitive_is_undeclared(self) -> None:
        # THE ACCEPTANCE TEST.  Per plan ``tmp/orchestration.md`` PR-3
        # acceptance criterion #4: "every MCP primitive is classified
        # for open-DAG composability; ... UNDECLARED fails the PR."
        from rates_agent.workflows import (
            known_rates_primitives,
            rates_primitive_resolver,
        )

        audit = audit_resolver(
            rates_primitive_resolver, known_rates_primitives(),
        )
        undeclared = by_classification(audit, Composability.UNDECLARED)
        assert not undeclared, (
            f"{len(undeclared)} primitives are UNDECLARED — "
            "every registered primitive must classify as "
            "BRIDGEABLE_SERIES, BRIDGEABLE_PANEL, or "
            "TERMINAL_ONLY_SNAPSHOT.  Offenders:\n  "
            + "\n  ".join(
                f"{name}: {entry.rationale}"
                for name, entry in undeclared.items()
            )
        )

    def test_audit_has_at_least_one_panel_primitive(self) -> None:
        from rates_agent.workflows import (
            known_rates_primitives,
            rates_primitive_resolver,
        )

        audit = audit_resolver(
            rates_primitive_resolver, known_rates_primitives(),
        )
        panels = by_classification(audit, Composability.BRIDGEABLE_PANEL)
        assert panels, (
            "Expected at least one BRIDGEABLE_PANEL primitive "
            "(build_sovereign_yield_panel_tool et al.) but found none."
        )

    def test_audit_has_at_least_one_series_primitive(self) -> None:
        from rates_agent.workflows import (
            known_rates_primitives,
            rates_primitive_resolver,
        )

        audit = audit_resolver(
            rates_primitive_resolver, known_rates_primitives(),
        )
        series = by_classification(audit, Composability.BRIDGEABLE_SERIES)
        assert len(series) > 10, (
            "Expected many BRIDGEABLE_SERIES primitives in the rates "
            f"universe; got {len(series)}."
        )

    def test_audit_terminal_only_includes_known_scanners(self) -> None:
        # Spot-check: scanner-style primitives (returning ranked
        # results, not time series) must classify as
        # TERMINAL_ONLY_SNAPSHOT.  Names contain 'scan'.
        from rates_agent.workflows import (
            known_rates_primitives,
            rates_primitive_resolver,
        )

        audit = audit_resolver(
            rates_primitive_resolver, known_rates_primitives(),
        )
        scanners = {
            name: entry for name, entry in audit.items()
            if "scan" in name
        }
        for name, entry in scanners.items():
            assert entry.classification != Composability.BRIDGEABLE_SERIES, (
                f"Scanner primitive {name!r} unexpectedly classified "
                f"as BRIDGEABLE_SERIES.  Scanners should be "
                "TERMINAL_ONLY_SNAPSHOT (output_field_units empty) "
                "until they declare a typed bridge."
            )


# ============================================================================
# CLOSED-FAMILY DISCIPLINE
# ============================================================================


class TestClosedFamily:
    def test_composability_size_pinned(self) -> None:
        # Per P8: adding a new classification requires an ADR.
        assert len(Composability) == 4
