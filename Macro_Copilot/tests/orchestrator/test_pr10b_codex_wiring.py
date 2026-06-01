"""tests/orchestrator/test_pr10b_codex_wiring.py — PR-10B anchor tests.

Anchors for the PR-10B Codex corrective.  Each test pins one of the
wiring properties Codex flagged as broken in PR-10:

  F1 — open-DAG auto-routes after template lane.
  F2 — children receive primitive_resolver.
  F3 — default executor adapter wired by default.
  F4 — Composer.repair_sync satisfies the Protocol.
  F11 — selector callbacks are lazy.
  F12 — supervisor prompt no longer says "drop".
  F14 — LLM-authored rationale fields on Selector/Composer output.
  F15 — intent table merges card hints with baseline.
  F16 — PASS_DRYRUN distinct from PASS.
"""

from __future__ import annotations

import os

import pytest


# ============================================================================
# F1 — auto-route to open-DAG (static check)
# ============================================================================


def test_pr10b_f1_session_run_contains_open_dag_fallback():
    """The session's _run_turn calls run_open_dag as a fallback after
    the template lane.  Static check: the source mentions
    run_open_dag and the OPEN_DAG_PRE_ROUTER env var."""
    import inspect
    from orchestrator.session import CopilotSession

    src = inspect.getsource(CopilotSession._run_turn)
    assert "run_open_dag" in src, (
        "PR-10B F1: _run_turn must reference run_open_dag as the "
        "post-template fallback"
    )
    assert "OPEN_DAG_PRE_ROUTER" in src, (
        "PR-10B F1: opt-out env var OPEN_DAG_PRE_ROUTER must be "
        "respected so developers can pin to legacy lane"
    )


# ============================================================================
# F2 — children receive primitive_resolver (static check)
# ============================================================================


def test_pr10b_f2_session_passes_primitive_resolver_to_children():
    import inspect
    from orchestrator.session import CopilotSession

    src = inspect.getsource(CopilotSession.open)
    assert "primitive_resolver=rates_primitive_resolver" in src, (
        "PR-10B F2: DomainAgentSession ctor must receive "
        "primitive_resolver so fill_leaf works"
    )


# ============================================================================
# F3 — default executor wiring
# ============================================================================


def test_pr10b_f3_run_open_dag_signature_has_dry_run_flag():
    import inspect
    from orchestrator.session import CopilotSession

    sig = inspect.signature(CopilotSession.run_open_dag)
    assert "dry_run" in sig.parameters, (
        "PR-10B F3: run_open_dag must expose a dry_run kwarg for "
        "explicit dry-run opt-in; live execution is the default"
    )


def test_pr10b_f3_run_open_dag_imports_default_executor_when_none():
    import inspect
    from orchestrator.session import CopilotSession

    src = inspect.getsource(CopilotSession.run_open_dag)
    assert "build_default_executor_callback" in src, (
        "PR-10B F3: run_open_dag must default to the substrate's "
        "execute_workflow adapter when executor_callback is None"
    )


# ============================================================================
# F4 — Composer.repair_sync satisfies the Protocol
# ============================================================================


def test_pr10b_f4_composer_has_sync_repair_wrapper():
    from orchestrator.open_dag import Composer

    assert hasattr(Composer, "repair_sync"), (
        "PR-10B F4: Composer must expose repair_sync to satisfy the "
        "Assembler's sync ShapePatchProvider Protocol"
    )
    # Sync signature, not async.
    import inspect
    assert not inspect.iscoroutinefunction(Composer.repair_sync)


def test_pr10b_f4_pipeline_wires_repair_callbacks():
    """OpenDagPipeline.__init__ wires composer.repair_sync as
    shape_patch_provider AND constructs a leaf_rebinder closure."""
    import inspect
    from orchestrator.open_dag import OpenDagPipeline

    src = inspect.getsource(OpenDagPipeline.__init__)
    assert "shape_patch_provider" in src
    assert "leaf_rebinder" in src
    rebinder_src = inspect.getsource(OpenDagPipeline._build_sync_leaf_rebinder)
    assert "ThreadPoolExecutor" in rebinder_src, (
        "PR-10B F4: leaf rebinder must run async fill_leaf on a "
        "worker thread (the Assembler is called sync from inside "
        "the pipeline's async run())"
    )


# ============================================================================
# F11 — selector callbacks lazy
# ============================================================================


def test_pr10b_f11_run_open_dag_uses_lazy_selector_closures():
    import inspect
    from orchestrator.session import CopilotSession

    src = inspect.getsource(CopilotSession.run_open_dag)
    # The lazy pattern: closure captures domain + opens it on first call.
    assert "_make_lazy_selector_cb" in src or "lazily" in src.lower(), (
        "PR-10B F11: per-domain selector callbacks must be lazy "
        "closures, not pre-opened at registration time"
    )


# ============================================================================
# F12 — supervisor prompt drift fix
# ============================================================================


def test_pr10b_f12_supervisor_prompt_does_not_say_drop():
    from orchestrator.prompts import SUPERVISOR_SYSTEM_PROMPT

    # The old wording was "otherwise the code drops the entry" —
    # PR-10B replaces it with "PREFER" + "INCLUDE it with the
    # correct domain_hint" + "under-scoping evidence".
    assert "otherwise the code drops the entry" not in SUPERVISOR_SYSTEM_PROMPT, (
        "PR-10B F12: supervisor prompt must not say 'drops the entry'"
    )
    # And the new wording instructs the model to INCLUDE.
    assert (
        "INCLUDE it with the correct domain_hint" in SUPERVISOR_SYSTEM_PROMPT
        or "preserves out-of-routing" in SUPERVISOR_SYSTEM_PROMPT.lower()
    ), (
        "PR-10B F12: supervisor prompt must instruct the model to "
        "INCLUDE under-scope evidence"
    )


# ============================================================================
# F14 — LLM-authored rationale fields exist
# ============================================================================


def test_pr10b_f14_selector_llm_output_has_rationale_field():
    from orchestrator.selectors import SelectorLLMOutput

    fields = SelectorLLMOutput.model_fields
    assert "rationale" in fields, (
        "PR-10B F14: SelectorLLMOutput must expose an optional "
        "rationale field so the LLM can author the lingo-resolution "
        "rationale (not just have it derived)"
    )
    # Default empty string — backward compat.
    field = fields["rationale"]
    assert field.default == "" or field.default is None


def test_pr10b_f14_composer_llm_output_has_rationale_field():
    from orchestrator.open_dag.composer import ComposerLLMOutput

    fields = ComposerLLMOutput.model_fields
    assert "rationale" in fields, (
        "PR-10B F14: ComposerLLMOutput must expose an optional "
        "rationale field so the LLM can author the wiring rationale"
    )


def test_pr10b_f14_intent_chain_prefers_llm_rationale_when_present():
    """IntentChain.from_inputs reads BoundLeaf.rationale (LLM-authored)
    and uses it verbatim; falls back to derived helper when empty."""
    from orchestrator.contracts import (
        Domain, EconomicQuantity, IntentTag, RouteAction, RouteDecision,
    )
    from orchestrator.open_dag import (
        BoundLeaf, Frequency, GOLDEN_TRANSFORM_ROLLING_ZSCORE,
        GateVerdict, IntentChain,
    )
    from shared.artifacts.registry import ArtifactTypeName

    route = RouteDecision(
        action=RouteAction.SINGLE_DOMAIN,
        domains=[Domain.SOVEREIGN_BONDS],
        rationale="x",
        intent_tag=IntentTag.TRANSFORM,
        decomposition=[EconomicQuantity(
            name="x", nl_description="x",
            domain_hint=Domain.SOVEREIGN_BONDS,
        )],
    )
    leaf_with_rationale = BoundLeaf(
        leaf_id="leaf_input",
        domain="sovereign_bonds",
        mcp_tool_name="t",
        resolver_tool_key="t",
        params={},
        output_field="time_series",
        declared_output_artifact_type=ArtifactTypeName.SERIES,
        declared_frequency=Frequency.DAILY,
        declared_semantic_role="r",
        declared_output_meaning="m",
        fit_confidence=0.9,
        rationale="LLM_AUTHORED_RATIONALE_SENTINEL",
    )
    chain = IntentChain.from_inputs(
        user_prompt="p",
        route_decision=route,
        bound_leaves=[leaf_with_rationale],
        shape_or_workflow=GOLDEN_TRANSFORM_ROLLING_ZSCORE,
        gate_verdict=GateVerdict(status="PASS", reason="ok"),
    )
    # LLM-authored rationale carried VERBATIM.
    assert chain.selectors[0].rationale == "LLM_AUTHORED_RATIONALE_SENTINEL"

    # And when rationale is empty, the derived fallback kicks in.
    leaf_no_rationale = BoundLeaf(
        leaf_id="leaf_input",
        domain="sovereign_bonds",
        mcp_tool_name="t",
        resolver_tool_key="t",
        params={},
        output_field="time_series",
        declared_output_artifact_type=ArtifactTypeName.SERIES,
        declared_frequency=Frequency.DAILY,
        declared_semantic_role="r",
        declared_output_meaning="m",
        fit_confidence=0.9,
        # rationale="" — default empty.
    )
    chain2 = IntentChain.from_inputs(
        user_prompt="p",
        route_decision=route,
        bound_leaves=[leaf_no_rationale],
        shape_or_workflow=GOLDEN_TRANSFORM_ROLLING_ZSCORE,
        gate_verdict=GateVerdict(status="PASS", reason="ok"),
    )
    # Derived: includes the role + domain + confidence.
    assert "r" in chain2.selectors[0].rationale
    assert "sovereign_bonds" in chain2.selectors[0].rationale


# ============================================================================
# F15 — intent table merges card hints
# ============================================================================


def test_pr10b_f15_intent_section_reads_card_intent_hints():
    """Cards that declare ``intent_hints`` in YAML should automatically
    appear in the composer prompt's intent → operator table."""
    import inspect
    from orchestrator.open_dag.composer import _intent_section_for_prompt

    src = inspect.getsource(_intent_section_for_prompt)
    # The merger logic must read intent_hints from the card.
    assert "intent_hints" in src, (
        "PR-10B F15: _intent_section_for_prompt must read "
        "card.intent_hints to support registration-only growth on "
        "new operator families"
    )


def test_pr10b_f15_operator_card_has_intent_hints_field():
    from shared.workflow.operator_catalogue import OperatorCard

    fields = OperatorCard.model_fields
    assert "intent_hints" in fields, (
        "PR-10B F15: OperatorCard must carry intent_hints field for "
        "registration-only intent-table growth"
    )


# ============================================================================
# F16 — PASS_DRYRUN distinct from PASS
# ============================================================================


def test_pr10b_f16_pipeline_status_includes_pass_dryrun():
    """PipelineStatus literal includes PASS_DRYRUN."""
    from orchestrator.open_dag.pipeline import PipelineStatus
    import typing

    args = typing.get_args(PipelineStatus)
    assert "PASS_DRYRUN" in args, (
        f"PR-10B F16: PipelineStatus must include PASS_DRYRUN; "
        f"got {args}"
    )
    assert "PASS" in args


def test_pr10b_f16_pipeline_outcome_has_is_gate_pass_property():
    from orchestrator.open_dag import PipelineOutcome

    assert hasattr(PipelineOutcome, "is_gate_pass"), (
        "PR-10B F16: PipelineOutcome must expose is_gate_pass "
        "covering both PASS + PASS_DRYRUN"
    )


# ============================================================================
# PR-10C — fresh Codex audit corrective tests
# ============================================================================


def test_pr10c_f7_composer_exposes_last_compose_rationale():
    """PR-10C Codex F7: Composer must capture LLM-authored rationale
    from each compose call so the pipeline can thread it through to
    IntentChain.from_inputs (without changing Composer.compose's
    return signature)."""
    from orchestrator.open_dag import Composer

    assert hasattr(Composer, "last_compose_rationale"), (
        "PR-10C F7: Composer must expose last_compose_rationale "
        "property so the pipeline can thread the LLM-authored "
        "wiring rationale into IntentChain"
    )


def test_pr10c_f7_pipeline_threads_composer_rationale_to_intent_chain():
    """The pipeline must read composer.last_compose_rationale after
    compose() and pass it to IntentChain.from_inputs as
    composer_llm_rationale."""
    import inspect
    from orchestrator.open_dag import OpenDagPipeline

    src = inspect.getsource(OpenDagPipeline.run)
    assert "last_compose_rationale" in src, (
        "PR-10C F7: pipeline.run must read composer.last_compose_rationale"
    )
    assert "composer_llm_rationale" in src, (
        "PR-10C F7: pipeline.run must pass composer_llm_rationale to "
        "IntentChain.from_inputs"
    )


def test_pr10c_f4_scaling_proof_uses_git_rev_parse_show_toplevel():
    """PR-10C Codex F4: literal git-diff test must discover git root
    via `git rev-parse --show-toplevel` rather than assuming
    _REPO_ROOT contains .git."""
    import inspect
    from tests.eval import test_scaling_proofs as proofs_mod

    src = inspect.getsource(
        proofs_mod.TestProof1_RegistrationOnlyGrowth.test_invariant_files_via_literal_git_diff,
    )
    assert "rev-parse" in src and "--show-toplevel" in src, (
        "PR-10C F4: literal git-diff test must use "
        "`git rev-parse --show-toplevel` for git root discovery"
    )


def test_pr10c_f5_github_workflow_at_actual_git_root():
    """PR-10C Codex F5: .github/workflows/open_dag_lints.yml must
    live at the actual git root (parent of Macro_Copilot in split-
    checkout layouts), not under Macro_Copilot/."""
    import subprocess
    from pathlib import Path

    repo_root = Path("/Volumes/Sreeram/Macro/Macro_Copilot")
    try:
        top = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, cwd=str(repo_root),
            timeout=5,
        )
        if top.returncode != 0:
            import pytest
            pytest.skip("not in a git repo")
        git_root = Path(top.stdout.strip())
    except Exception:
        import pytest
        pytest.skip("git unavailable")
    wf = git_root / ".github" / "workflows" / "open_dag_lints.yml"
    assert wf.is_file(), (
        f"PR-10C F5: GitHub Actions workflow must live at git "
        f"root .github/workflows/, got {wf}"
    )
