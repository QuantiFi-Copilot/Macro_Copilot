"""orchestrator.open_dag.default_executor — PR-10A Codex F2 / PR-11A.

A default ``ExecutorCallback`` for ``OpenDagPipeline`` that wraps the
substrate's ``shared.workflow.execute_workflow`` and:

  - invokes it synchronously inside an async wrapper (the substrate's
    executor is sync),
  - extracts the terminal artifact's ``Lineage`` (the
    ``WorkflowResult.terminal_artifact.lineage`` field, which every
    typed artifact wrapper carries),
  - builds the structured ``TerminalArtifactSummary`` the session
    layer threads into the ``workflow_result`` event (PR-11A; replaces
    the prior summary-string-only return),
  - bundles all of the above into ``ExecutedDag`` so the pipeline can
    carry the full ``WorkflowResult`` through to ``CopilotSession``
    for persistence (the template lane's
    ``persist_dag_from_workflow_result`` helper needs ``node_artifacts``
    keyed by ``node_id``; the prior ``(Lineage, str)`` tuple discarded
    that and blocked open-DAG persistence — Codex correction).

Per PR-10A Codex F2's correction (still load-bearing):

  > PR-10's pipeline makes execution an optional callback.  Default
  > the pipeline to use shared.workflow.execute_workflow when no
  > callback is supplied so end-to-end runs are the production
  > shape, not the test shape.

This module ships the BRIDGE between the open-DAG pipeline's
``ExecutorCallback`` Protocol and the substrate's
``execute_workflow``.  Callers can either:

  - use ``build_default_executor_callback(engine, resolver)`` to get
    a ready-made callback, OR
  - call the lower-level helpers
    (``execute_workflow_async`` + ``executed_summary_from_result`` +
    ``terminal_summary_from_result``) directly when more control is
    needed.

Both paths are exercised in the open-DAG end-to-end tests.

PR-11A change
=============

The return type of ``execute_workflow_async`` and the
``ExecutorCallback`` Protocol it satisfies evolved from
``Optional[Tuple[Lineage, str]]`` to ``Optional[ExecutedDag]``.  The
new shape carries the full ``WorkflowResult`` + the structured
``TerminalArtifactSummary`` so the session layer can persist the run
+ emit a slug-bearing ``workflow_result`` event.  The old summary
string (the topological "p1 → p2 → ..." label) is still produced
by ``executed_summary_from_result`` — that helper is unchanged and
``ExecutedDag.workflow_lineage_summary`` carries the same value.

Finance-blindness
=================

This module sits in ``orchestrator/open_dag/``.  It imports from
``shared.workflow.executor`` (the substrate), from
``shared.artifacts.lineage`` (the substrate's Lineage type), and from
``orchestrator.open_dag.executed_dag`` (the in-package typed carrier).
It does NOT import from ``rates_agent/`` — the primitive resolver is
passed in by the caller.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional, Sequence

from orchestrator.open_dag.executed_dag import (
    ExecutedDag,
    TerminalArtifactSummary,
)
from shared.workflow.executor import execute_workflow
from shared.workflow.registry import PrimitiveResolver
from shared.workflow.types import Workflow

logger = logging.getLogger(__name__)


# ============================================================================
# PUBLIC ENTRY POINT
# ============================================================================


def build_default_executor_callback(
    *,
    engine: Any,
    primitive_resolver: PrimitiveResolver,
):
    """Return a callable suitable for
    ``OpenDagPipeline(executor_callback=...)`` that delegates to
    ``shared.workflow.execute_workflow``.

    Parameters
    ----------
    engine :
        SQLAlchemy engine (or None for resolver-mocked tests).  Passed
        verbatim to ``execute_workflow``.
    primitive_resolver :
        The substrate's ``PrimitiveResolver`` — typically the
        agent layer's ``rates_primitive_resolver``.

    Returns
    -------
    An async callable that accepts ``(workflow, bound_leaves)`` and
    returns an ``ExecutedDag`` (carrying the full ``WorkflowResult`` +
    cached lineage + structured ``TerminalArtifactSummary``) OR
    ``None`` on execution failure.  Failure conversion to None is
    intentional: the pipeline's executor-callback contract treats
    None as "execution failed; mark RunLineage compute-incomplete".
    """

    async def callback(
        workflow: Workflow,
        bound_leaves: Sequence[Any],
    ) -> Optional[ExecutedDag]:
        return await execute_workflow_async(
            workflow=workflow,
            engine=engine,
            primitive_resolver=primitive_resolver,
        )

    return callback


async def execute_workflow_async(
    *,
    workflow: Workflow,
    engine: Any,
    primitive_resolver: PrimitiveResolver,
) -> Optional[ExecutedDag]:
    """Run the substrate executor inside an async wrapper.

    The substrate's ``execute_workflow`` is synchronous.  This helper
    runs it in the default executor pool so the open-DAG pipeline's
    async run loop is not blocked.

    Returns the ``ExecutedDag`` bundle on success.
    Returns ``None`` on any exception — the pipeline's
    executor-callback contract treats None as "execution failed".
    Exception details are logged (not propagated) so the pipeline can
    surface a structured failure verdict.
    """
    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(
            None,
            lambda: execute_workflow(
                workflow,
                engine=engine,
                primitive_resolver=primitive_resolver,
            ),
        )
    except Exception:
        logger.exception(
            "default executor: execute_workflow raised",
        )
        return None

    try:
        return ExecutedDag.from_workflow_result(result)
    except Exception:
        logger.exception(
            "default executor: building ExecutedDag from result raised; "
            "treating as execute failure"
        )
        return None


def executed_summary_from_result(result: Any) -> str:
    """Render a short English summary of the executor's output the
    L6 AnswerRenderer consumes as ``executed_summary``.

    Uses the executor's own ``workflow_lineage_summary`` (the
    topologically-sorted node sequence) plus a one-line description
    of the terminal artifact type.  The summary is INTENTIONALLY
    terse: the L6 LLM authors the PM-facing prose, not this
    function.

    Format (stable):
      ``"<terminal_artifact_type>: <workflow_lineage_summary>"``

    Tests use this string verbatim; deterministic.

    PR-11A note
    -----------
    This helper is now ALSO the source for
    ``ExecutedDag.workflow_lineage_summary``.  Kept as a free function
    so legacy callers + tests that constructed the string directly
    still work unchanged.
    """
    terminal = getattr(result, "terminal_artifact", None)
    type_label = (
        type(terminal).__name__ if terminal is not None else "Unknown"
    )
    summary = getattr(result, "workflow_lineage_summary", "")
    return f"{type_label}: {summary}"


def terminal_summary_from_result(result: Any) -> TerminalArtifactSummary:
    """Build the structured ``TerminalArtifactSummary`` for a
    ``WorkflowResult``.

    Thin wrapper around
    ``TerminalArtifactSummary.from_terminal_artifact`` that pulls the
    terminal off the result for callers that want the structured
    summary without going through ``ExecutedDag``.

    Raises ``ValueError`` when the terminal artifact's runtime type
    isn't in the closed family (see TerminalArtifactSummary for
    rationale).
    """
    terminal = getattr(result, "terminal_artifact", None)
    if terminal is None:
        raise ValueError(
            "terminal_summary_from_result: result.terminal_artifact "
            "is None; cannot build TerminalArtifactSummary."
        )
    return TerminalArtifactSummary.from_terminal_artifact(terminal)


__all__ = [
    "build_default_executor_callback",
    "execute_workflow_async",
    "executed_summary_from_result",
    "terminal_summary_from_result",
]
