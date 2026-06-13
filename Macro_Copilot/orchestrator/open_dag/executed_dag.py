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

from typing import Any, Dict, Optional

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


def _p_value_from_lineage(lineage: "Lineage") -> Optional[float]:
    """Read a diagnostic operator's recorded p-value from the head
    lineage step, if any.

    Campaign FM-2 — granger_causality / stationarity_adf / ljung_box /
    normality_test / cointegration emit a TEST STATISTIC as the
    ScalarMetric value and record ``p_value`` in their OperatorStep
    params.  Mirrors ``companion_dispersion_from_lineage``'s
    operator-generic read: any head step that recorded a finite
    ``p_value`` param surfaces it; everything else returns None.
    """
    try:
        steps = getattr(lineage, "steps", None) or ()
        if not steps:
            return None
        head = steps[-1]
        params = getattr(head, "params", None) or {}
        raw = params.get("p_value")
        if raw is None:
            return None
        value = float(raw)
        if value != value or value in (float("inf"), float("-inf")):
            return None
        return value
    except Exception:  # pragma: no cover — defensive
        return None


# Campaign n01/n02 — bound on the per-key last-value map surfaced for
# keyed terminals (SeriesSet / Panel).  10 covers the desk's named-set
# asks (G7 = 7, G10 curves = 10) without letting a wide panel bloat the
# L6 prompt; wider sets surface the first 10 in key order + a
# truncation marker.
_KEY_LAST_VALUES_CAP = 10


def _key_last_values(
    series_by_key: "Dict[str, Any]",
) -> "tuple[Optional[Dict[str, float]], bool]":
    """Per-key LAST FINITE value for a keyed terminal, bounded.

    Campaign n01/n02 — a SeriesSet terminal summarized as only
    ``SeriesSet(n=933)`` leaves a ranking ask with no number to name a
    winner with; the honest L6 answer must then defer ("the ranking
    rides the artifact").  Surfacing each key's last finite value
    grounds the comparative read.  Keys with empty / all-NaN payloads
    are skipped (honest absence).  Returns ``(None, False)`` when
    nothing is finite.
    """
    out: Dict[str, float] = {}
    truncated = False
    try:
        for key, payload in series_by_key.items():
            if len(out) >= _KEY_LAST_VALUES_CAP:
                truncated = True
                break
            try:
                cleaned = payload.dropna()
                if len(cleaned) > 0:
                    out[str(key)] = float(cleaned.iloc[-1])
            except Exception:  # pragma: no cover — defensive per key
                continue
    except Exception:  # pragma: no cover — defensive
        return None, False
    return (out or None), truncated


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
    # GAP G02/T6 — companion dispersion.  ScalarMetric only, and only
    # when the producing operator recorded a dispersion in its head
    # lineage step (summarize_series: statistic=mean, dispersion=std).
    # The user who asked "mean AND std" gets both numbers in the L6
    # prose + the workflow_result card instead of a qualitative
    # description of the std.  None for every other artifact type and
    # for summaries that computed no dispersion.
    dispersion_key: Optional[str] = None
    dispersion_value: Optional[float] = None
    # Campaign FM-1 — Series terminals previously summarized as
    # ``Series<PERCENT>(n=1257)``: NO value, NO dates.  The L6 LLM had
    # nothing to quote and improvised (fabricated "trading at its
    # mean", unfilled "[value from ...]" templates, narration outside
    # the fetched window).  These fields ground the prose: the LAST
    # FINITE observation (the "current" read), the FIRST date, and the
    # LAST date bound the narratable span.  Series only; None elsewhere.
    last_value: Optional[float] = None
    last_date: Optional[str] = None
    first_date: Optional[str] = None
    finite_count: Optional[int] = None
    # Campaign c04 — with only ``last=`` the L6 LLM fabricated in-window
    # TREND claims ("drifted higher over the period") it could not know.
    # The first finite observation gives it both endpoints, so direction
    # statements are grounded by comparison instead of invented.
    first_value: Optional[float] = None
    # Campaign n01/n02 — SeriesSet/Panel terminals summarized as bare
    # ``SeriesSet(n=933)``: a ranking ask ("which market is most
    # stretched?") then has NO number to name a winner with, so the L6
    # prose must either defer to the artifact or fabricate ranks (the
    # pre-fix run invented "Italy 7/7").  The per-key LAST finite
    # values ground the comparative read.  Keyed terminals only; None
    # elsewhere.  Bounded at _KEY_LAST_VALUES_CAP entries (insertion
    # order) — a wide panel must not bloat the prompt.
    key_last_values: Optional[Dict[str, float]] = None
    key_last_values_truncated: bool = False
    # Campaign FM-2 — diagnostic operators (granger_causality,
    # stationarity_adf, ljung_box, normality_test, cointegration)
    # emit a TEST STATISTIC as the ScalarMetric value and record the
    # p-value in the head lineage step.  Without it the L6 LLM has
    # misread F statistics as p-values ("F=0.93 → p ~93%"; true
    # p≈0.46).  Surfaced so the prose can quote both, correctly
    # labelled.  None when the head step recorded no p_value.
    p_value: Optional[float] = None

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
            # GAP G02/T6 — a summary operator records its companion
            # dispersion in the head lineage step; surface it so the
            # L6 prose + the workflow_result card can quote BOTH
            # numbers ("mean 9.2 bps, std 59.4 bps").
            from shared.artifacts.lineage import (
                companion_dispersion_from_lineage,
            )

            dispersion_key, dispersion_value = (
                companion_dispersion_from_lineage(artifact.lineage)
            )
            return cls(
                artifact_type="ScalarMetric",
                units=artifact.units.value,
                metric_key=artifact.metric_key,
                value=float(artifact.value),
                row_count=None,
                head_hash=head_hash,
                dispersion_key=dispersion_key,
                dispersion_value=dispersion_value,
                p_value=_p_value_from_lineage(artifact.lineage),
            )
        if isinstance(artifact, Series):
            # Campaign FM-1 — ground the L6 prose: last finite value +
            # the fetched date span.  Pure reads of the payload; an
            # empty / all-NaN payload leaves the fields None (honest
            # absence — the renderer then has nothing to quote and must
            # say so rather than improvise).
            last_value: Optional[float] = None
            last_date: Optional[str] = None
            first_date: Optional[str] = None
            finite_count: Optional[int] = None
            first_value: Optional[float] = None
            try:
                payload = artifact.payload
                if len(payload) > 0:
                    first_date = str(payload.index[0])[:10]
                    cleaned = payload.dropna()
                    finite_count = int(len(cleaned))
                    if finite_count > 0:
                        last_value = float(cleaned.iloc[-1])
                        last_date = str(cleaned.index[-1])[:10]
                        first_value = float(cleaned.iloc[0])
            except Exception:  # pragma: no cover — defensive
                pass
            return cls(
                artifact_type="Series",
                units=artifact.units.value,
                row_count=int(len(artifact.payload)),
                head_hash=head_hash,
                last_value=last_value,
                last_date=last_date,
                first_date=first_date,
                finite_count=finite_count,
                first_value=first_value,
            )
        if isinstance(artifact, SeriesSet):
            key_last, truncated = _key_last_values(
                {k: s for k, s in artifact.series_by_key.items()}
            )
            return cls(
                artifact_type="SeriesSet",
                units=None,  # per-key map elided; see docstring
                row_count=int(len(artifact.common_index)),
                head_hash=head_hash,
                key_last_values=key_last,
                key_last_values_truncated=truncated,
            )
        if isinstance(artifact, EventSet):
            return cls(
                artifact_type="EventSet",
                units=None,
                row_count=int(len(artifact.mask)),
                head_hash=head_hash,
            )
        if isinstance(artifact, Panel):
            key_last, truncated = _key_last_values(
                {str(c): artifact.payload[c] for c in artifact.payload.columns}
            )
            return cls(
                artifact_type="Panel",
                units=None,  # per-column map elided; see docstring
                row_count=int(len(artifact.payload)),
                head_hash=head_hash,
                key_last_values=key_last,
                key_last_values_truncated=truncated,
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
            # GAP G02/T6 — when the summary operator recorded a
            # companion dispersion, hand the L6 LLM BOTH numbers in the
            # same units so "mean and std" prose can quote both.  The
            # no-dispersion format stays byte-identical (stable; tests
            # assert on it).
            if self.dispersion_key is not None and self.dispersion_value is not None:
                base = (
                    f"ScalarMetric({self.metric_key}="
                    f"{value_repr}{units_repr}; "
                    f"{self.dispersion_key}="
                    f"{self.dispersion_value:.4g}{units_repr})"
                )
            else:
                base = (
                    f"ScalarMetric({self.metric_key}="
                    f"{value_repr}{units_repr})"
                )
            # Campaign FM-2 — diagnostic operators' ScalarMetric is a
            # TEST STATISTIC; the decision number is the p-value in
            # lineage.  Hand the L6 LLM both, explicitly labelled, so
            # an F statistic is never misread as a probability.
            if self.p_value is not None:
                base = (
                    f"{base[:-1]}; p_value={self.p_value:.4g} "
                    "[the test statistic above is NOT a probability])"
                )
            return base
        if self.artifact_type == "WindowedPanel":
            units_repr = f"<{self.units}>" if self.units else ""
            return f"WindowedPanel{units_repr}(n_events={self.row_count or 0})"
        if self.artifact_type == "Series":
            units_repr = f"<{self.units}>" if self.units else ""
            # Campaign FM-1 — value-bearing Series summary.  The L6
            # prose can now quote the latest observation and MUST NOT
            # narrate outside the [first_date, last_date] span.  The
            # value-less legacy format renders only when the payload
            # was empty / all-NaN (honest absence).
            if self.last_value is not None:
                span = (
                    f"; span={self.first_date}→{self.last_date}"
                    if self.first_date and self.last_date
                    else ""
                )
                finite = (
                    f", finite={self.finite_count}"
                    if self.finite_count is not None
                    else ""
                )
                # Campaign c04 — both endpoints when available, so the
                # prose can state direction from comparison only.
                first = (
                    f"first={self.first_value:.4g}, "
                    if self.first_value is not None
                    else ""
                )
                return (
                    f"Series{units_repr}(n={self.row_count or 0}{finite}; "
                    f"{first}last={self.last_value:.4g} on "
                    f"{self.last_date}{span})"
                )
            return f"Series{units_repr}(n={self.row_count or 0})"
        # SeriesSet / EventSet / Panel — no single-units summary at
        # this layer (the artifact carries per-key/per-column units).
        # Campaign n01/n02 — keyed terminals surface per-key last
        # finite values so comparative/ranking prose has numbers to
        # name a winner with instead of deferring or fabricating.
        if self.key_last_values:
            pairs = ", ".join(
                f"{k}={v:.4g}" for k, v in self.key_last_values.items()
            )
            more = ", …" if self.key_last_values_truncated else ""
            return (
                f"{self.artifact_type}(n={self.row_count or 0}; "
                f"last per key: {pairs}{more})"
            )
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
