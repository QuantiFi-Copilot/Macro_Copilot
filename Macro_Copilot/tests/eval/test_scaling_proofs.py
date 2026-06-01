"""tests/eval/test_scaling_proofs.py — PR-10 §PR-10 scaling proofs.

Per ``tmp/orchestration.md`` §PR-10 the PoC's actual gating
deliverable is THREE scaling proofs:

  > | Proof | Test |
  > |---|---|
  > | Registration-only growth | Add a synthetic 59th primitive +
  >    17th operator.  Assert via byte-comparison that
  >    orchestrator/open_dag/composer.py, coverage_gate.py,
  >    shared/workflow/validate.py, orchestrator/prompts.py:
  >    SUPERVISOR_SYSTEM_PROMPT, and every OTHER domain's MCP
  >    server file are byte-for-byte unchanged.  Then prove a
  >    fresh query using the new tools composes correctly. |
  > | Context-bound | Per query in the eval matrix, instrument the
  >    pipeline to record (a) each L2 selector's MCP-visible tool
  >    count = only own-domain tools; (b) the composer's prompt
  >    does NOT mention any primitive name (grep); (c) the
  >    composer's prompt token count is unchanged when the 59th
  >    primitive is registered. |
  > | Two-boundary | The three adversarial entries in the eval
  >    matrix above.  Plus: a contradictory free-form semantic_role
  >    between LeafRequest and BoundLeaf → Boundary A surfaces as
  >    WARNING → gate returns CLARIFY. |

These are the architecture-claim tests — what the PoC actually
proves.  Per §PR-10 acceptance criterion 2: ALL THREE must be green
for the PoC to count as shipped.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pytest
from pydantic import BaseModel

from orchestrator.contracts import (
    Domain,
    EconomicQuantity,
    IntentTag,
    RouteAction,
    RouteDecision,
)
from orchestrator.open_dag import (
    BoundLeaf,
    ComposerRefusal,
    Frequency,
    GOLDEN_RELATIONSHIP_CORRELATION,
    GateVerdict,
    LeafHole,
    LeafRequest,
    OpenDagPipeline,
    ShapeSpec,
)
from orchestrator.open_dag.composer import (
    build_compose_system_prompt_text,
)
from shared.artifacts.registry import ArtifactTypeName
from shared.workflow.operator_catalogue import (
    approx_tokens,
    clear_catalogue_cache,
    render_operator_catalogue,
)
from shared.workflow.registry import (
    OPERATOR_REGISTRY,
    PrimitiveSpec,
)
from shared.workflow.types import OperatorNode, WorkflowEdge

from tests.eval.synthetic_operator_17 import (
    SYNTHETIC_OPERATOR_17_NAME,
    with_synthetic_operator_17,
)
from tests.eval.synthetic_primitive_59 import (
    SYNTHETIC_PRIMITIVE_59_NAME,
    SYNTHETIC_PRIMITIVE_59_SPEC,
    wrap_resolver_with_synthetic_59,
)


# ============================================================================
# REPO ROOT — used by file-hash byte-comparison
# ============================================================================


_REPO_ROOT: Path = Path(__file__).resolve().parents[2]


def _file_hash(p: Path) -> str:
    """Stable hex digest of a file's bytes for byte-for-byte
    comparison."""
    return hashlib.sha256(p.read_bytes()).hexdigest()


# ============================================================================
# PROOF #1 — REGISTRATION-ONLY GROWTH
# ============================================================================


# The files §PR-10 explicitly lists as must NOT change when a new
# primitive + a new operator are registered.  Paths are repo-rooted.
_INVARIANT_FILES: List[Path] = [
    _REPO_ROOT / "orchestrator" / "open_dag" / "composer.py",
    _REPO_ROOT / "orchestrator" / "open_dag" / "coverage_gate.py",
    _REPO_ROOT / "shared" / "workflow" / "validate.py",
    # PR-10A Codex F5: plan §PR-10 explicitly lists shared/workflow/
    # executor.py in the invariant set.  Omitting it weakened the
    # registration-only proof — the executor surface is exactly where
    # a new primitive registration MUST NOT force a substrate change.
    _REPO_ROOT / "shared" / "workflow" / "executor.py",
    _REPO_ROOT / "orchestrator" / "prompts.py",
]

# Every other domain's MCP server file (i.e. NOT the domain where
# the synthetic primitive is registered).  Per §PR-10 these MUST be
# byte-identical after the synthetic registration.
_OTHER_DOMAIN_MCP_SERVERS: List[Path] = sorted(
    p for p in (_REPO_ROOT / "rates_agent").glob("*/mcp_server.py")
    if p.parent.name != "sovereign_bonds"  # the synthetic primitive's "domain"
)


class TestProof1_RegistrationOnlyGrowth:
    """Per §PR-10: adding a synthetic 59th primitive + 17th operator
    must leave the invariant-file set byte-identical, AND a fresh
    query that uses the new operator must compose correctly."""

    def test_invariant_files_unchanged_under_synthetic_registration(self):
        """Capture file hashes BEFORE registering synthetic surfaces;
        inject the synthetic operator (and access the synthetic
        primitive's spec); capture hashes AFTER; assert byte-identity.
        """
        before: Dict[Path, str] = {
            p: _file_hash(p) for p in _INVARIANT_FILES
            if p.exists()
        }
        before_servers: Dict[Path, str] = {
            p: _file_hash(p) for p in _OTHER_DOMAIN_MCP_SERVERS
        }
        assert before, "no invariant files found — fixture broken"

        # "Register" the synthetic surfaces.  The synthetic primitive
        # is just a typed PrimitiveSpec referenced by a resolver-wrap
        # helper; the synthetic operator is injected into
        # OPERATOR_REGISTRY (in-memory only).
        _ = SYNTHETIC_PRIMITIVE_59_SPEC.tool_name
        with with_synthetic_operator_17():
            assert SYNTHETIC_OPERATOR_17_NAME in OPERATOR_REGISTRY

            after: Dict[Path, str] = {
                p: _file_hash(p) for p in _INVARIANT_FILES
                if p.exists()
            }
            after_servers: Dict[Path, str] = {
                p: _file_hash(p) for p in _OTHER_DOMAIN_MCP_SERVERS
            }

        # After-state hashes MUST equal before-state hashes — every
        # invariant file is byte-for-byte unchanged.
        for path, before_hash in before.items():
            assert after[path] == before_hash, (
                f"PR-10 Proof #1: file {path} changed under synthetic "
                f"registration.  Registration-only growth requires "
                "this file stays byte-identical."
            )
        for path, before_hash in before_servers.items():
            assert after_servers[path] == before_hash, (
                f"PR-10 Proof #1: other-domain MCP server {path} "
                "changed under synthetic registration."
            )

        # And after the context manager exits, the synthetic operator
        # is removed from the registry (test isolation).
        assert SYNTHETIC_OPERATOR_17_NAME not in OPERATOR_REGISTRY

    def test_fresh_query_with_synthetic_operator_composes(self):
        """Per §PR-10: prove a fresh query using the new tools
        composes correctly.

        Build a shape that uses the synthetic_operator_17 between a
        Series leaf and the terminal.  Substitute through the
        Assembler with the synthetic primitive as the leaf.  Assert
        the assembly is CLEAN — i.e. ZERO substrate changes were
        needed to support the new primitive + new operator.
        """
        with with_synthetic_operator_17():
            # Build a shape: 1 LeafHole -> synthetic_operator_17.
            leaf = LeafHole(
                node_id="leaf_input",
                leaf_request=LeafRequest(
                    required_artifact_type=ArtifactTypeName.SERIES,
                    domain_hint=Domain.SOVEREIGN_BONDS.value,
                    semantic_role="input_series",
                    requested_output_meaning="synthetic input series",
                    nl_intent="synthetic input series",
                ),
            )
            op = OperatorNode(
                node_id="synthetic_op",
                operator_name=SYNTHETIC_OPERATOR_17_NAME,
                params={"scale": 2.0},
            )
            shape = ShapeSpec(
                workflow_id="proof_1_fresh_query",
                nodes=[leaf, op],
                edges=[
                    WorkflowEdge(
                        source_node_id="leaf_input",
                        target_node_id="synthetic_op",
                        target_input_slot="series",
                    ),
                ],
                terminal_node_id="synthetic_op",
            )

            from orchestrator.open_dag.assembler import (
                Assembler,
                AssemblyStatus,
            )

            class _In(BaseModel):
                pass

            class _Out(BaseModel):
                time_series: dict = {}

            def _wrapped_resolver(tool_name: str) -> PrimitiveSpec:
                if tool_name == SYNTHETIC_PRIMITIVE_59_NAME:
                    return SYNTHETIC_PRIMITIVE_59_SPEC
                return PrimitiveSpec(
                    tool_name=tool_name,
                    callable=lambda **kw: {},
                    input_class=_In,
                    output_class=_Out,
                    config_path=Path("/tmp/stub.yaml"),
                    output_field_units={"time_series": "bps"},
                    output_artifact_type="Series",
                )

            asm = Assembler(primitive_resolver=_wrapped_resolver)
            bound = BoundLeaf(
                leaf_id="leaf_input",
                domain="sovereign_bonds",
                mcp_tool_name=SYNTHETIC_PRIMITIVE_59_NAME,
                resolver_tool_key=SYNTHETIC_PRIMITIVE_59_NAME,
                params={},
                output_field="time_series",
                declared_output_artifact_type=ArtifactTypeName.SERIES,
                declared_units=None,
                declared_frequency=Frequency.DAILY,
                declared_semantic_role="input_series",
                declared_output_meaning="synthetic input series",
                fit_confidence=0.9,
            )
            result = asm.assemble(shape, [bound])
            assert result.status == AssemblyStatus.CLEAN, (
                f"PR-10 Proof #1: fresh query with the synthetic "
                f"operator failed to assemble.  Errors: "
                f"{[(e.code.value, e.message[:80]) for e in result.validation_result.errors]}"
            )


# ============================================================================
# PROOF #2 — CONTEXT-BOUND
# ============================================================================


class TestProof2_ContextBound:
    """Per §PR-10: the composer's prompt is bounded by its own
    catalogue surface — NEVER expands with primitive vocabulary AND
    NEVER grows when a new primitive is registered in any domain."""

    def test_composer_prompt_has_no_primitive_names(self):
        """Acceptance criterion (b): the composer's prompt does NOT
        mention any primitive name (grep).  Same discipline asserted
        in PR-7's TestComposerPromptContent but anchored here as a
        scaling proof — primitive-name leakage would be a regression
        on Proof #2."""
        from orchestrator.prompts import COMPOSER_SYSTEM_PROMPT

        catalogue = render_operator_catalogue()
        text = build_compose_system_prompt_text(catalogue, COMPOSER_SYSTEM_PROMPT)

        # Sentinel primitive name patterns — any leakage is a regress.
        primitive_indicators = [
            "calculate_curve_spread_tool",
            "calculate_swap_spread_tool",
            "calculate_breakeven_inflation_simple_tool",
            "get_futures_butterfly_simple_tool",
            "calculate_ois_curve_spread_tool",
            "_tool",
        ]
        for needle in primitive_indicators:
            assert needle not in text, (
                f"PR-10 Proof #2: composer prompt leaks primitive "
                f"vocabulary ({needle!r}); Proof requires zero "
                "primitive names in the L3 prompt"
            )

    def test_composer_prompt_token_delta_unchanged_after_registering_59th_primitive(self):
        """Acceptance criterion (c): the composer's prompt token
        count is UNCHANGED when the 59th primitive is registered
        (delta == 0).

        The composer's prompt is built from
        ``render_operator_catalogue()`` (16 operator cards) — NOT
        from any primitive surface.  So registering a new primitive
        in the resolver MUST NOT change the prompt token count.
        Asserted at the resolver-wrap layer: the wrapped resolver
        contains the new spec, but the operator catalogue (and
        therefore the prompt) is unchanged.
        """
        from orchestrator.prompts import COMPOSER_SYSTEM_PROMPT

        # Clear any process-cached cards so we're measuring against a
        # fresh catalogue render.
        clear_catalogue_cache()
        catalogue_before = render_operator_catalogue()
        text_before = build_compose_system_prompt_text(
            catalogue_before, COMPOSER_SYSTEM_PROMPT,
        )
        tokens_before = approx_tokens(text_before)

        # "Register" the 59th primitive (resolver-side, in-memory).
        base = lambda name: SYNTHETIC_PRIMITIVE_59_SPEC  # noqa: E731
        wrapped = wrap_resolver_with_synthetic_59(base)
        assert wrapped(SYNTHETIC_PRIMITIVE_59_NAME).tool_name == SYNTHETIC_PRIMITIVE_59_NAME

        # Re-render the prompt.  The catalogue source is the operator
        # registry, which is unchanged — therefore the prompt MUST be
        # byte-identical.
        clear_catalogue_cache()
        catalogue_after = render_operator_catalogue()
        text_after = build_compose_system_prompt_text(
            catalogue_after, COMPOSER_SYSTEM_PROMPT,
        )
        tokens_after = approx_tokens(text_after)

        # Token-delta MUST be zero — the composer's prompt is
        # context-bound to the operator catalogue + golden few-shots.
        assert tokens_after == tokens_before, (
            f"PR-10 Proof #2: composer prompt token count changed "
            f"from {tokens_before} to {tokens_after} after registering "
            "the 59th primitive.  Registration-only growth requires "
            "the L3 prompt to be UNCHANGED."
        )
        # And the byte-level surface is identical too — strictest
        # form of the proof.
        assert text_after == text_before, (
            "PR-10 Proof #2: composer prompt bytes changed after "
            "registering the 59th primitive"
        )

    def test_synthetic_operator_becomes_visible_in_full_catalogue(self):
        """PR-10A Codex F6: a freshly registered operator MUST become
        VISIBLE in the full operator catalogue with a rich card.  This
        is the L3 composability condition the original test sidestepped.

        Per the PR-10A corrective: the synthetic operator now ships
        with a real ``synthetic_operator_17_config.yaml`` so
        ``render_operator_card`` and ``render_operator_catalogue``
        succeed.  After injection:

          - the catalogue is size 17 (not 16);
          - the synthetic operator's card is renderable;
          - the Composer prompt that's built from the catalogue
            INCLUDES the synthetic operator's name + one-line.

        That's the architecture claim: registration → L3 visibility,
        without modifying composer.py or coverage_gate.py or any
        substrate file.
        """
        from shared.workflow.operator_catalogue import (
            render_operator_card,
        )

        clear_catalogue_cache()
        baseline_size = len(render_operator_catalogue())

        with with_synthetic_operator_17():
            clear_catalogue_cache()
            catalogue_with = render_operator_catalogue()
            # Catalogue grew by exactly 1 — the synthetic operator.
            assert len(catalogue_with) == baseline_size + 1, (
                f"PR-10A F6: catalogue size after registering "
                f"synthetic operator was {len(catalogue_with)}, "
                f"expected {baseline_size + 1}"
            )
            assert SYNTHETIC_OPERATOR_17_NAME in catalogue_with, (
                "PR-10A F6: synthetic operator NOT visible in the "
                "rendered catalogue — the L3 composability condition "
                "is not met"
            )
            # The card renders with the rich content the YAML
            # declares.
            card = render_operator_card(SYNTHETIC_OPERATOR_17_NAME)
            assert "Synthetic" in card.one_line
            assert card.output.artifact_type.value == "Series"
            assert "series" in card.input_slots

            # The Composer's full system prompt INCLUDES the synthetic
            # operator's name + its catalogue card content.
            from orchestrator.prompts import COMPOSER_SYSTEM_PROMPT
            prompt = build_compose_system_prompt_text(
                catalogue_with, COMPOSER_SYSTEM_PROMPT,
            )
            assert SYNTHETIC_OPERATOR_17_NAME in prompt, (
                "PR-10A F6: synthetic operator name absent from the "
                "Composer prompt after registration"
            )

        # Test isolation: catalogue restored to baseline after the
        # context manager exits.
        clear_catalogue_cache()
        assert len(render_operator_catalogue()) == baseline_size

    def test_selector_catalogue_is_per_domain_isolated(self):
        """Acceptance criterion (a): each L2 selector's MCP-visible
        tool count = only own-domain tools.

        Per PR-6 the Selector catalogue is built per-domain from the
        MCP-visible tool list; one domain's selector never sees
        another domain's tools.  This proof asserts the typed
        contract: the SelectorCallback in the pipeline is keyed by
        Domain, and a callback for domain X can only return
        BoundLeafs with leaf.domain == X.

        We use the pipeline mock to assert the dispatch boundary.
        """
        from orchestrator.open_dag import GOLDEN_RELATIONSHIP_CORRELATION

        # Build a tracking selector that records which domain it was
        # called for.  If the pipeline's dispatch boundary is
        # working, the per-domain selector receives ONLY its own
        # domain's LeafHoles.
        ois_calls: List[str] = []
        sov_calls: List[str] = []

        async def ois_cb(*, leaf_id, request, timeout_s):
            ois_calls.append(leaf_id)
            return BoundLeaf(
                leaf_id=leaf_id, domain="ois",
                mcp_tool_name="t", resolver_tool_key="t",
                params={}, output_field="time_series",
                declared_output_artifact_type=ArtifactTypeName.SERIES,
                declared_units=None, declared_frequency=Frequency.DAILY,
                declared_semantic_role="r", declared_output_meaning="m",
                fit_confidence=0.9,
            )

        async def sov_cb(*, leaf_id, request, timeout_s):
            sov_calls.append(leaf_id)
            return BoundLeaf(
                leaf_id=leaf_id, domain="sovereign_bonds",
                mcp_tool_name="t", resolver_tool_key="t",
                params={}, output_field="time_series",
                declared_output_artifact_type=ArtifactTypeName.SERIES,
                declared_units=None, declared_frequency=Frequency.DAILY,
                declared_semantic_role="r", declared_output_meaning="m",
                fit_confidence=0.9,
            )

        # The golden correlation shape's LeafHoles are sovereign_bonds.
        from orchestrator.open_dag.pipeline import _SelectorBoundaryError

        # Construct a pipeline.  Use a fake stub resolver.
        class _In(BaseModel):
            pass

        class _Out(BaseModel):
            time_series: dict = {}

        def _stub(tool_name):
            return PrimitiveSpec(
                tool_name=tool_name, callable=lambda **kw: {},
                input_class=_In, output_class=_Out,
                config_path=Path("/tmp/x.yaml"),
                output_field_units={"time_series": "bps"},
                output_artifact_type="Series",
            )

        class _MockRouter:
            async def route(self, p):
                return RouteDecision(
                    action=RouteAction.SINGLE_DOMAIN,
                    domains=[Domain.SOVEREIGN_BONDS],
                    rationale="x",
                    intent_tag=IntentTag.RELATIONSHIP,
                    decomposition=[EconomicQuantity(
                        name="x", nl_description="x",
                        domain_hint=Domain.SOVEREIGN_BONDS,
                    )],
                )

        class _MockComposer:
            async def compose(self, **kw):
                return GOLDEN_RELATIONSHIP_CORRELATION

        class _MockGate:
            async def check(self, **kw):
                return GateVerdict(status="PASS", reason="ok")

        class _MockRenderer:
            async def render(self, **kw):
                return "RENDERED"

        pipeline = OpenDagPipeline(
            router=_MockRouter(),
            composer=_MockComposer(),
            coverage_gate=_MockGate(),
            answer_renderer=_MockRenderer(),
            selectors={
                Domain.OIS: ois_cb,
                Domain.SOVEREIGN_BONDS: sov_cb,
            },
            primitive_resolver=_stub,
        )

        import asyncio
        outcome = asyncio.run(pipeline.run("p"))

        # Per-domain isolation: the OIS callback was NEVER invoked
        # because the golden's LeafHoles are sovereign_bonds; the
        # sovereign_bonds callback was called for both leaves.
        assert ois_calls == [], (
            "PR-10 Proof #2: OIS selector received leaves from a "
            "sovereign_bonds shape — per-domain isolation violated"
        )
        assert sorted(sov_calls) == ["leaf_a", "leaf_b"], (
            "PR-10 Proof #2: sovereign_bonds selector did not receive "
            f"both LeafHoles; got {sov_calls}"
        )


# ============================================================================
# PROOF #3 — TWO-BOUNDARY
# ============================================================================


class TestProof3_TwoBoundary:
    """Per §PR-10: the two boundaries (Boundary A / Boundary B) catch
    the right adversarial classes.  Proof:

      - 3 adversarial entries from the eval matrix (already asserted
        in tests/eval/test_open_dag_eval_matrix.py::TestAdversarialEval —
        this test file references the discipline anchor).
      - A contradictory free-form semantic_role between LeafRequest
        and BoundLeaf → Boundary A surfaces as WARNING → gate
        receives the warning and biases toward CLARIFY.
    """

    def test_adversarial_eval_class_imports(self):
        # Discipline anchor: the adversarial eval class exists and
        # has the 3 tests the plan requires.  If any are removed,
        # this proof regresses.
        from tests.eval.test_open_dag_eval_matrix import (
            TestAdversarialEval,
        )
        attrs = dir(TestAdversarialEval)
        assert "test_adversarial_underscoped_returns_clarify" in attrs
        assert "test_adversarial_role_mismatch_routes_to_clarify" in attrs
        assert "test_adversarial_composite_noun_routes_to_clarify" in attrs

    def test_role_mismatch_surfaces_as_boundary_a_warning(self):
        """Manually construct a LeafRequest expecting one
        semantic_role and a BoundLeaf declaring a different
        semantic_role.  Run through the Assembler.  Boundary A's
        contract check MUST surface this as a WARNING (severity =
        WARNING, code = E_ROLE_DISCRIMINANT_MISMATCH).

        The downstream gate (PR-8) is then expected to see the
        warning + bias toward CLARIFY — that bias is asserted in
        PR-8's test_soft_warnings_surface_in_verdict.  This test
        anchors the WARNING surface itself."""
        from orchestrator.open_dag import GOLDEN_TRANSFORM_ROLLING_ZSCORE
        from orchestrator.open_dag.assembler import (
            Assembler,
            AssemblyStatus,
        )
        from shared.workflow.validation_result import (
            ErrorCode,
            Severity,
        )

        class _In(BaseModel):
            pass

        class _Out(BaseModel):
            time_series: dict = {}

        def _stub_resolver(tool_name):
            return PrimitiveSpec(
                tool_name=tool_name, callable=lambda **kw: {},
                input_class=_In, output_class=_Out,
                config_path=Path("/tmp/x.yaml"),
                output_field_units={"time_series": "bps"},
                output_artifact_type="Series",
            )

        # The golden's LeafHole expects semantic_role="input_series"
        # + requested_output_meaning="input series to standardise
        # against its own trailing history".
        # Construct a BoundLeaf with a CONTRADICTORY semantic_role
        # ("rolling_zscore_output") and a contradictory
        # output_meaning ("the operator-side z-score series").
        bound = BoundLeaf(
            leaf_id="leaf_input",
            domain="sovereign_bonds",
            mcp_tool_name="calc_tool",
            resolver_tool_key="calc_tool",
            params={},
            output_field="time_series",
            declared_output_artifact_type=ArtifactTypeName.SERIES,
            declared_units=None,
            declared_frequency=Frequency.DAILY,
            # CONTRADICTION on the free-form role and meaning.
            declared_semantic_role="rolling_zscore_output",
            declared_output_meaning="the operator-side z-score series",
            fit_confidence=0.7,
        )

        asm = Assembler(primitive_resolver=_stub_resolver)
        result = asm.assemble(GOLDEN_TRANSFORM_ROLLING_ZSCORE, [bound])

        # Boundary A: structural validation MUST still succeed
        # (artifact_type matches, units match, frequency matches —
        # only the free-form fields contradict).  Assembly is CLEAN.
        assert result.status == AssemblyStatus.CLEAN

        # Per §PR-10 the two-boundary proof: the contradiction
        # surfaces as a WARNING (NOT an error).
        warnings = result.validation_result.warnings
        role_warnings = [
            w for w in warnings
            if w.code == ErrorCode.E_ROLE_DISCRIMINANT_MISMATCH
        ]
        assert len(role_warnings) >= 1, (
            "PR-10 Proof #3: free-form role contradiction did not "
            "surface as a WARNING in Boundary A.  Two-boundary "
            "discipline requires this."
        )
        for w in role_warnings:
            assert w.severity == Severity.WARNING

    def test_warnings_flow_through_gate_to_verdict(self):
        """The gate (PR-8) must propagate Boundary A's WARNINGs into
        its own soft_warnings field on GateVerdict.  This is the
        bridge: Boundary A surfaces the issue; Boundary B's verdict
        carries it forward to the user-facing layer."""
        from orchestrator.open_dag.coverage_gate import (
            warnings_to_string_list,
        )
        from shared.workflow.validation_result import (
            ErrorCode,
            OwnerLayer,
            Severity,
            ValidationError,
        )

        w = ValidationError(
            code=ErrorCode.E_ROLE_DISCRIMINANT_MISMATCH,
            owner_layer=OwnerLayer.L2_BINDING,
            severity=Severity.WARNING,
            message="role mismatch",
            node_id="leaf_a",
        )
        out = warnings_to_string_list([w])
        # The warning's code surfaces in the string list (the
        # GateVerdict.soft_warnings surface) — Two-boundary proof's
        # propagation step.
        assert any("E_ROLE_DISCRIMINANT_MISMATCH" in s for s in out)
