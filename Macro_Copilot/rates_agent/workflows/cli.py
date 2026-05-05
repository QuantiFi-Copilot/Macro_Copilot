"""rates_agent/workflows/cli.py — CLI for testing the workflow-template
routing + execution layer end-to-end against a real LLM.

This is the developer-facing surface for the gauntlet walkthrough
(Phase D in the PR 9 plan).  Three subcommands:

  - ``route <prompt>``   LLM routes the prompt to a template; prints
                         the WorkflowRouteDecision JSON.  No execution.
                         Use this first to spot-check routing quality
                         on a handful of prompts before exercising
                         execution.

  - ``run <prompt>``     LLM routes; if the action is ROUTE, the bound
                         workflow executes against synthetic DB
                         fetchers (default) or live TimescaleDB
                         (``--use-real-db``).  Prints the routing
                         decision AND the execution envelope.

  - ``catalogue``        Print the live template catalogue (the same
                         text the router sees in its system prompt).
                         Useful when authoring new prompts.

Configuration / .env discipline
-------------------------------
This CLI reuses ``orchestrator.config`` for ``.env`` loading +
``ANTHROPIC_API_KEY`` validation.  Importing the config module
triggers ``load_dotenv`` against the project root's ``.env`` file,
matching the existing CLI in ``orchestrator/graph.py``.  When the
LLM-using subcommands run (``route`` / ``run``), ``main`` calls
``orchestrator.config.validate()`` to fail-fast with a clear
diagnostic if ``ANTHROPIC_API_KEY`` is missing — rather than letting
a deep langchain stack trace surface.

The CLI's ``--model`` flag defaults to ``orchestrator.config.LLM_MODEL``
so the workflow router and the existing supervisor see the same
model unless the developer overrides explicitly.

Synthetic vs real DB
--------------------
``run`` defaults to synthetic DB fetchers
(``tests._workflow_synthetic_fetchers``) so a developer typing prompts
doesn't need a live TimescaleDB to see whether the LLM bound the
right slots.  ``--use-real-db`` flips the engine to a live SQLAlchemy
engine (subject to env-var DB credentials being set).

Note on synthetic-fetcher coverage
----------------------------------
The synthetic fetchers cover the canonical Q1 (event_study) and Q2
(regime_conditioned_relationship) bindings ONLY.  A prompt the LLM
routes to event_study with a NON-canonical primitive (e.g. a
sovereign curve_spread instead of swap_spread) will NOT have a
matching synthetic fetcher and the workflow will fail at execute
time with a database error.  This is a feature, not a bug — it
surfaces "your prompt routes to a template whose primitives we
haven't synthesized for in the eval harness."  Use ``--use-real-db``
for those cases.

Entry point
-----------
``python -m rates_agent.workflows.cli route "<prompt>"``
``python -m rates_agent.workflows.cli run "<prompt>"``
``python -m rates_agent.workflows.cli catalogue``
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any, Optional

# Bootstrap: project root on sys.path so ``python -m
# rates_agent.workflows.cli`` resolves cleanly regardless of cwd.
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Importing ``orchestrator.config`` triggers ``load_dotenv`` against
# the project root's .env file (matches the existing
# ``orchestrator.graph`` CLI's bootstrap discipline).  Must run BEFORE
# any LLM-binding import so ``ANTHROPIC_API_KEY`` is in os.environ by
# the time langchain-anthropic constructs its client.
from orchestrator import config as _orchestrator_config  # noqa: F401, E402

# Importing each template package triggers ``register_template`` via
# its __init__.py.  Required so ``list_workflows()`` /
# ``WorkflowRouter`` see every template.
import rates_agent.workflows.event_study  # noqa: F401, E402
import rates_agent.workflows.regime_conditioned_relationship  # noqa: F401, E402

from orchestrator.workflow_contracts import (  # noqa: E402
    WorkflowExecutionResult,
    WorkflowRouteAction,
    WorkflowRouteDecision,
)
from orchestrator.workflow_prompts import render_catalogue  # noqa: E402
from orchestrator.workflow_router import WorkflowRouter  # noqa: E402
from rates_agent.workflows._runner import run_template  # noqa: E402

logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("rates_agent.workflows.cli")


# ---------------------------------------------------------------------------
# Default LLM model — sourced from orchestrator/config.py so the CLI,
# the existing Supervisor, and any future session integration share
# one model knob (settable via env var ``LLM_MODEL``).
# ---------------------------------------------------------------------------
_DEFAULT_LLM_MODEL = _orchestrator_config.LLM_MODEL


# ===========================================================================
# CLI HELPERS
# ===========================================================================


def _print_decision(decision: WorkflowRouteDecision) -> None:
    """Pretty-print a routing decision in JSON shape (matches the
    contract ``WorkflowRouteDecision`` Pydantic dump)."""
    payload = decision.model_dump(mode="json")
    # Convert the action enum to its value for readability.
    print(json.dumps(payload, indent=2, default=str))


def _print_execution(result: WorkflowExecutionResult) -> None:
    """Pretty-print a full execution result — routing decision +
    execution envelope.  Two distinct sections so the developer can
    see routing quality independently of execution success."""
    print("=" * 72)
    print("ROUTE DECISION")
    print("=" * 72)
    _print_decision(result.route)
    print()
    print("=" * 72)
    print("EXECUTION")
    print("=" * 72)
    if result.execution is None:
        print("(no execution — action was CLARIFY or OUT_OF_SCOPE)")
        return
    # Truncate the envelope for terminal readability.  Full envelope
    # is in the structured output for piping / scripting.
    print(json.dumps(result.execution, indent=2, default=str))


# ===========================================================================
# CLI SUBCOMMANDS
# ===========================================================================


async def _cmd_route(prompt: str, model: str) -> int:
    """Route-only: LLM decides which template + slot bindings; no
    execution."""
    router = WorkflowRouter(model_name=model)
    decision = await router.route(prompt)
    _print_decision(decision)
    return 0 if decision.action == WorkflowRouteAction.ROUTE else 0
    # Always exit 0 — the CLI is a developer tool; non-ROUTE decisions
    # are valid outputs (CLARIFY / OUT_OF_SCOPE), not errors.


async def _cmd_run(prompt: str, model: str, use_real_db: bool) -> int:
    """Route + execute.  Default uses synthetic DB fetchers (one set
    of patches for Q1, another for Q2 — chosen by the routed
    template_id).  ``--use-real-db`` flips to a live engine."""
    router = WorkflowRouter(model_name=model)

    if use_real_db:
        # Real engine path: defer to the runner's default which
        # initialises a live SQLAlchemy engine via
        # ``database.database.get_db_engine``.
        result = await router.execute(prompt)
        _print_execution(result)
        return 0

    # Synthetic-fetcher path — choose the right patch context based
    # on the routed template_id, after routing succeeds.  We can't
    # know which template the LLM picks until after route(), so we
    # do route + bind here in two stages: route first, THEN enter the
    # appropriate patch context, THEN execute.
    decision = await router.route(prompt)

    if decision.action != WorkflowRouteAction.ROUTE:
        # Print the route only — there's nothing to execute.
        result = WorkflowExecutionResult(route=decision, execution=None)
        _print_execution(result)
        return 0

    template_id = decision.template_id or ""
    patch_ctx = _patches_for_template(template_id)
    if patch_ctx is None:
        # No synthetic-fetcher coverage for this template.  Print a
        # diagnostic and skip execution.
        result = WorkflowExecutionResult(
            route=decision,
            execution={
                "ok": False,
                "template_id": template_id,
                "error": (
                    f"No synthetic-fetcher coverage for template "
                    f"{template_id!r}.  Re-run with --use-real-db "
                    "to execute against a live TimescaleDB."
                ),
            },
        )
        _print_execution(result)
        return 0

    with patch_ctx:
        envelope = run_template(template_id, decision.slot_values, engine=None)
    result = WorkflowExecutionResult(route=decision, execution=envelope)
    _print_execution(result)
    return 0


def _patches_for_template(template_id: str):
    """Return a context-manager that installs the right synthetic
    DB-fetcher patches for the named template, or ``None`` if no
    coverage exists.

    Synthetic-fetcher coverage for V1:
      - ``event_study``                        → Q1 canonical patches
      - ``regime_conditioned_relationship``    → Q2 canonical patches

    Adding a new template's synthetic coverage = extend
    ``tests._workflow_synthetic_fetchers`` and add a branch here.
    """
    # Lazy-import: keep ``cli`` importable in environments where
    # ``tests/`` isn't on sys.path (e.g. minimal deployment installs).
    try:
        from tests._workflow_synthetic_fetchers import (
            q1_canonical_fetchers_context,
            q2_canonical_fetchers_context,
        )
    except ImportError as exc:
        logger.warning(
            "synthetic fetchers unavailable (tests/ not importable): %s",
            exc,
        )
        return None
    if template_id == "event_study":
        return q1_canonical_fetchers_context()
    if template_id == "regime_conditioned_relationship":
        return q2_canonical_fetchers_context()
    return None


def _cmd_catalogue() -> int:
    """Print the live template catalogue text — exactly what the
    workflow router embeds into its system prompt."""
    print(render_catalogue())
    return 0


# ===========================================================================
# ARGPARSE
# ===========================================================================


def build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rates_agent.workflows.cli",
        description=(
            "Workflow-template routing + execution CLI.  Use ``route`` "
            "to test LLM routing on a prompt without execution; ``run`` "
            "to route AND execute the bound workflow; ``catalogue`` to "
            "print the live template catalogue."
        ),
    )
    parser.add_argument(
        "--model",
        default=_DEFAULT_LLM_MODEL,
        help=(
            f"Anthropic model id for the router LLM "
            f"(default: {_DEFAULT_LLM_MODEL})."
        ),
    )

    subparsers = parser.add_subparsers(
        dest="command", required=True,
        title="subcommands",
    )

    p_route = subparsers.add_parser(
        "route",
        help="Route a prompt to a template — no execution.",
    )
    p_route.add_argument("prompt", type=str, help="The user prompt to route.")

    p_run = subparsers.add_parser(
        "run",
        help="Route + execute the bound workflow.",
    )
    p_run.add_argument("prompt", type=str, help="The user prompt to route and execute.")
    p_run.add_argument(
        "--use-real-db",
        action="store_true",
        help=(
            "Hit live TimescaleDB instead of the default synthetic "
            "fetchers.  Requires DB env-vars to be set."
        ),
    )

    subparsers.add_parser(
        "catalogue",
        help="Print the live template catalogue text.",
    )

    return parser


def main(argv: Optional[list] = None) -> int:
    parser = build_argparser()
    args = parser.parse_args(argv)

    # ``catalogue`` doesn't call the LLM; the other two do.  Validate
    # ``ANTHROPIC_API_KEY`` (and any other required env vars) BEFORE
    # constructing the WorkflowRouter so the failure mode is a clear
    # diagnostic, not a deep langchain stack trace.  Mirrors the
    # ``orchestrator.graph`` CLI's discipline.
    if args.command in ("route", "run"):
        _orchestrator_config.validate()

    if args.command == "catalogue":
        return _cmd_catalogue()
    if args.command == "route":
        return asyncio.run(_cmd_route(args.prompt, args.model))
    if args.command == "run":
        return asyncio.run(
            _cmd_run(args.prompt, args.model, args.use_real_db),
        )
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
