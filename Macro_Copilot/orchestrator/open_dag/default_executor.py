"""orchestrator.open_dag.default_executor — PR-10A Codex F2.

A default ``ExecutorCallback`` for ``OpenDagPipeline`` that wraps the
substrate's ``shared.workflow.execute_workflow`` and:

  - invokes it synchronously inside an async wrapper (the substrate's
    executor is sync),
  - extracts the terminal artifact's ``Lineage`` (the
    ``WorkflowResult.terminal_artifact.lineage`` field, which every
    typed artifact wrapper carries),
  - renders a short ``executed_summary`` string the L6 AnswerRenderer
    consumes.

Per PR-10A Codex F2's correction:

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
    (``execute_workflow_async`` + ``executed_summary_from_result``)
    directly when more control is needed.

Both paths are exercised in the PR-10A end-to-end test.

Finance-blindness
=================

This module sits in ``orchestrator/open_dag/``.  It imports from
``shared.workflow.executor`` (the substrate) and from
``shared.artifacts.lineage`` (the substrate's Lineage type).  It
does NOT import from ``rates_agent/`` — the primitive resolver is
passed in by the caller.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional, Sequence, Tuple

from shared.artifacts.lineage import Lineage
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
    returns either a ``(Lineage, executed_summary_str)`` tuple OR
    ``None`` on execution failure.  Failure conversion to None is
    intentional: the pipeline's executor-callback contract treats
    None as "execution failed; mark RunLineage compute-incomplete".
    """

    async def callback(
        workflow: Workflow,
        bound_leaves: Sequence[Any],
    ) -> Optional[Tuple[Lineage, str]]:
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
) -> Optional[Tuple[Lineage, str]]:
    """Run the substrate executor inside an async wrapper.

    The substrate's ``execute_workflow`` is synchronous.  This helper
    runs it in the default executor pool so the open-DAG pipeline's
    async run loop is not blocked.

    Returns the ``(Lineage, executed_summary)`` tuple on success.
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

    lineage = _lineage_from_workflow_result(result)
    if lineage is None:
        logger.warning(
            "default executor: terminal artifact has no .lineage; "
            "falling back to None"
        )
        return None
    summary = executed_summary_from_result(result)
    return (lineage, summary)


def _lineage_from_workflow_result(result: Any) -> Optional[Lineage]:
    """Extract the substrate's ``Lineage`` from a ``WorkflowResult``.

    Every typed artifact wrapper carries a ``.lineage`` field (see
    ``shared/artifacts/types.py``).  This helper reaches into the
    terminal artifact to pull it out; returns None when the artifact
    doesn't expose one (defensive — should not happen for any
    registered closed-family wrapper).
    """
    terminal = getattr(result, "terminal_artifact", None)
    if terminal is None:
        return None
    return getattr(terminal, "lineage", None)


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
    """
    terminal = getattr(result, "terminal_artifact", None)
    type_label = (
        type(terminal).__name__ if terminal is not None else "Unknown"
    )
    summary = getattr(result, "workflow_lineage_summary", "")
    return f"{type_label}: {summary}"


__all__ = [
    "build_default_executor_callback",
    "execute_workflow_async",
    "executed_summary_from_result",
]
