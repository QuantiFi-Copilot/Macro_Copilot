"""orchestrator.open_dag.executed_dag — PR-11A.

Typed carriers for what an L5 executor produces.  Replaces the prior
``(Lineage, executed_summary: str)`` tuple that ``default_executor.py``
returned and ``OpenDagPipeline`` consumed: that tuple discarded the
full ``WorkflowResult`` (including ``node_artifacts`` keyed by node_id)
and the actual terminal-artifact value, both of which the session
layer needs to persist the run as a slug-routed workspace and emit a
``workflow_result`` event the frontend can render.

Two shapes:

  - ``TerminalArtifactSummary`` — just enough of the terminal artifact
    for the L6 prose + the frontend workflow_result card to render
    WITHOUT a second artifact-store fetch.  ScalarMetric carries its
    ``metric_key`` + ``value`` (Codex correction: the frontend card
    must show the real correlation number, not a hash).
  - ``ExecutedDag`` — the substrate ``WorkflowResult`` (carrying every
    node's artifact), the lineage extracted from the terminal, the
    topology summary string the L6 lane already consumes, and the
    terminal summary.  ``PipelineOutcome`` carries this on PASS so
    ``CopilotSession`` can persist + emit without reaching back into
    pipeline state.

Both are Pydantic ``BaseModel`` (frozen, extra='forbid') so they
compose naturally with the existing ``PipelineOutcome`` shape and
stay JSON-friendly for tests + telemetry.

Why Pydantic (not @dataclass)
=============================

The plan calls these "dataclasses" colloquially, but PipelineOutcome is
already a frozen Pydantic BaseModel and embedding raw dataclasses inside
breaks the existing model's frozen + extra='forbid' guarantees.  Pydantic
gives us the same immutability + validation surface and lets the existing
PipelineOutcome carry an ``executed_dag`` field without an
``arbitrary_types_allowed`` carve-out.

Finance-blindness
=================

This module sits in ``orchestrator/open_dag/`` and only imports from
``shared.artifacts.types`` (Lineage + the closed-family artifact
wrappers).  No ``rates_agent/`` imports.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from shared.artifacts.lineage import Lineage
from shared.artifacts.types import (
    EventSet,
    Panel,
    ScalarMetric,
    Series,
    SeriesSet,
    WindowedPanel,
)
from shared.workflow.result import WorkflowResult


# ============================================================================
# TERMINAL ARTIFACT SUMMARY
# ============================================================================


class TerminalArtifactSummary(BaseModel):
    """Just enough of the terminal artifact for L6 prose + the frontend
    ``workflow_result`` card to render WITHOUT a second artifact-store
    fetch.

    Fields
    ------
    artifact_type :
        One of the closed-family artifact-type names
        (``"Series"`` / ``"SeriesSet"`` / ``"EventSet"`` / ``"Panel"``
        / ``"WindowedPanel"`` / ``"ScalarMetric"``).
    units :
        Optional unit tag.  For Series / ScalarMetric / WindowedPanel
        the artifact carries a single ``units`` field; for SeriesSet /
        Panel the artifact carries per-key/per-column units and this
        summary intentionally elides them (callers wanting the full
        per-column map should fetch the artifact payload).
    metric_key :
        ScalarMetric only — the operator-supplied identifier
        (e.g. ``"correlation_coefficient"``).  ``None`` for every
        other artifact type.
    value :
        ScalarMetric only — the finite float value.  ``None`` for
        every other artifact type.  Finite at construction (the
        underlying ``ScalarMetric`` rejects ``±Inf``/``NaN`` per
        ART11).
    row_count :
        Length hint for Series / SeriesSet / Panel; ``None`` for
        ScalarMetric and ``None`` when the payload is empty for the
        other types.
    head_hash :
        ``terminal_artifact.lineage.head_hash`` — content-addressed
        reference the frontend can carry through to artifact-payload
        fetches.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    artifact_type: str = Field(..., min_length=1)
    units: Optional[str] = None
    metric_key: Optional[str] = None
    value: Optional[float] = None
    row_count: Optional[int] = None
    head_hash: str = Field(..., min_length=1)

    @classmethod
    def from_terminal_artifact(
        cls,
        artifact: Any,
    ) -> "TerminalArtifactSummary":
        """Extract the closed-family summary from a typed terminal
        artifact.

        Dispatches on ``isinstance`` against the closed family.  An
        artifact whose runtime type isn't in the family raises
        ``ValueError`` (would have been caught at the artifact-store
        boundary upstream — defensive guard here so a future closed-
        family extension makes the gap loud, not silent).
        """
        head_hash = artifact.lineage.head_hash
        if isinstance(artifact, ScalarMetric):
            return cls(
                artifact_type="ScalarMetric",
                units=artifact.units.value,
                metric_key=artifact.metric_key,
                value=float(artifact.value),
                row_count=None,
                head_hash=head_hash,
            )
        if isinstance(artifact, Series):
            return cls(
                artifact_type="Series",
                units=artifact.units.value,
                row_count=int(len(artifact.payload)),
                head_hash=head_hash,
            )
        if isinstance(artifact, SeriesSet):
            return cls(
                artifact_type="SeriesSet",
                units=None,  # per-key map elided; see docstring
                row_count=int(len(artifact.common_index)),
                head_hash=head_hash,
            )
        if isinstance(artifact, EventSet):
            return cls(
                artifact_type="EventSet",
                units=None,
                row_count=int(len(artifact.mask)),
                head_hash=head_hash,
            )
        if isinstance(artifact, Panel):
            return cls(
                artifact_type="Panel",
                units=None,  # per-column map elided; see docstring
                row_count=int(len(artifact.payload)),
                head_hash=head_hash,
            )
        if isinstance(artifact, WindowedPanel):
            return cls(
                artifact_type="WindowedPanel",
                units=artifact.units.value,
                row_count=int(artifact.payload.shape[0]),
                head_hash=head_hash,
            )
        raise ValueError(
            "TerminalArtifactSummary: unknown terminal artifact type "
            f"{type(artifact).__name__!r}.  The closed family is "
            "{Series, SeriesSet, EventSet, Panel, WindowedPanel, "
            "ScalarMetric}; extending it requires an ART4/ART16 ADR "
            "+ updates to this dispatch and the frontend artifact-"
            "widget registry."
        )

    def to_executed_summary(self) -> str:
        """Render a value-bearing one-line summary the L6 LLM consumes.

        PR-11A.A.2 contract: the AnswerRenderer's ``executed_summary``
        param feeds the LLM the actual number / artifact to report.
        Pre-PR-11 the pipeline passed only the topological lineage
        string (``"workflow X: p1 -> p2 -> ..."``); the LLM had no
        way to know the actual correlation value for a ScalarMetric
        terminal.

        Format (stable; tests assert on it):
          - ScalarMetric : ``"ScalarMetric(<metric_key>=<value> <units>)"``
            — e.g. ``"ScalarMetric(correlation_coefficient=-0.342 RATIO)"``
          - Series       : ``"Series<<units>>(n=<row_count>)"`` —
            e.g. ``"Series<PERCENT>(n=1257)"``
          - SeriesSet    : ``"SeriesSet(n=<row_count>)"``
          - EventSet     : ``"EventSet(n=<row_count>)"``
          - Panel        : ``"Panel(n=<row_count>)"``
          - WindowedPanel: ``"WindowedPanel<<units>>(n_events=<row_count>)"``

        The format is INTENTIONALLY compact — the L6 LLM authors the
        prose; this helper just hands it the structured value.
        """
        if self.artifact_type == "ScalarMetric":
            # The load-bearing case: a single finite scalar the L6 LLM
            # MUST be able to quote in its prose.
            value_repr = (
                f"{self.value:.4g}"
                if self.value is not None
                else "n/a"
            )
            units_repr = f" {self.units}" if self.units else ""
            return (
                f"ScalarMetric({self.metric_key}="
                f"{value_repr}{units_repr})"
            )
        if self.artifact_type == "WindowedPanel":
            units_repr = f"<{self.units}>" if self.units else ""
            return f"WindowedPanel{units_repr}(n_events={self.row_count or 0})"
        if self.artifact_type == "Series":
            units_repr = f"<{self.units}>" if self.units else ""
            return f"Series{units_repr}(n={self.row_count or 0})"
        # SeriesSet / EventSet / Panel — no single-units summary at
        # this layer (the artifact carries per-key/per-column units).
        return f"{self.artifact_type}(n={self.row_count or 0})"


# ============================================================================
# EXECUTED DAG
# ============================================================================


class ExecutedDag(BaseModel):
    """The full L5 executor outcome.

    Carries the substrate ``WorkflowResult`` (with every node's
    artifact keyed by ``node_id``), the lineage extracted from the
    terminal artifact (cached for callers that only need it), the
    topological lineage summary string the L6 lane consumes, and
    the structured terminal summary the session layer threads into
    the ``workflow_result`` event.

    Returned by ``execute_workflow_async`` and carried in
    ``PipelineOutcome.executed_dag`` on PASS.

    Fields
    ------
    workflow_result :
        The substrate's full ``WorkflowResult`` — ``node_artifacts``
        keyed by ``node_id`` matches ``workflow.nodes`` 1:1 per the
        executor contract.  Needed by
        ``state.dag_repo.persist_dag_from_workflow_result`` (the
        caller-owned persistence helper the template lane already
        uses).  Held as ``Any`` to keep this module finance-blind
        and avoid an import-order cycle with ``shared.workflow.*``.
    lineage :
        Cached extract from ``workflow_result.terminal_artifact.
        lineage``.  Convenience field — every typed terminal
        artifact carries one (every closed-family wrapper has a
        ``.lineage`` field per ART9).
    workflow_lineage_summary :
        Human-readable topologically-sorted "p1 → p2 → align →
        correlation" string the L6 ``AnswerRenderer`` consumes.
        Mirrors ``WorkflowResult.workflow_lineage_summary``.
    terminal_summary :
        The structured summary the frontend workflow_result card
        renders (carries ScalarMetric.value when relevant).
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        arbitrary_types_allowed=True,
    )

    workflow_result: WorkflowResult
    lineage: Lineage
    workflow_lineage_summary: str = Field(..., min_length=1)
    terminal_summary: TerminalArtifactSummary

    @property
    def executed_summary(self) -> str:
        """The value-bearing ``executed_summary`` string the L6
        ``AnswerRenderer`` consumes.

        Combines the structured terminal summary (which carries the
        ScalarMetric value / Series row count / etc.) with the
        topological lineage summary (the "p1 -> align -> correlation"
        string) so the L6 LLM sees BOTH the actual number AND the
        compute path that produced it.

        Codex correction (PR-11 follow-up): the pre-PR-11 pipeline
        passed ONLY ``workflow_lineage_summary`` as ``executed_summary``,
        so the AnswerRenderer's LLM had no way to quote the
        correlation coefficient in its prose.  ``terminal_summary``
        carries the value; this property is the glue that hands it to
        the renderer's existing string-typed contract WITHOUT changing
        the AnswerRenderer's signature.

        Format (stable):
          ``"<terminal_summary.to_executed_summary()> · <workflow_lineage_summary>"``

        Examples:
          - ScalarMetric : ``"ScalarMetric(correlation_coefficient=-0.342 RATIO) · workflow X: p1 -> p2 -> align -> correlation"``
          - Series       : ``"Series<PERCENT>(n=1257) · workflow X: p1 -> ..."``
        """
        return (
            f"{self.terminal_summary.to_executed_summary()} "
            f"· {self.workflow_lineage_summary}"
        )

    @classmethod
    def from_workflow_result(cls, result: WorkflowResult) -> "ExecutedDag":
        """Build the bundled ExecutedDag from a successful
        ``WorkflowResult``.

        Raises ``ValueError`` when the terminal artifact has no
        ``.lineage`` (should not happen for any registered closed-
        family wrapper, but we surface it loudly rather than crash
        downstream)."""
        terminal = result.terminal_artifact
        lineage = getattr(terminal, "lineage", None)
        if lineage is None:
            raise ValueError(
                "ExecutedDag.from_workflow_result: terminal artifact "
                f"{type(terminal).__name__!r} has no .lineage; every "
                "closed-family artifact wrapper must carry one (ART9)."
            )
        return cls(
            workflow_result=result,
            lineage=lineage,
            workflow_lineage_summary=result.workflow_lineage_summary,
            terminal_summary=TerminalArtifactSummary.from_terminal_artifact(
                terminal,
            ),
        )


__all__ = [
    "TerminalArtifactSummary",
    "ExecutedDag",
]
