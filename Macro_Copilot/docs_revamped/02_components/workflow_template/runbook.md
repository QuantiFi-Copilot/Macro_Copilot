# Runbook — How to add a new workflow template (or a new archetype)

> Step-by-step procedure for two distinct admissions: (a) adding a **new template** to an existing archetype (the common case), and (b) **extending the closed archetype family** (the rare, ADR-gated case). Both procedures land a `template.yaml`, an `__init__.py`, and a five-layer test suite; the archetype-extension procedure additionally edits the substrate's closed enum and ships a parity-test update.

**Version:** v1.1
**Last reviewed:** 2026-05-18
**Audience:** any contributor (human or AI agent) introducing a new template.
**Prerequisite reading:** [`README.md`](README.md) — the workflow-template contract (WT1–WT16). Read it once before starting; refer back when a step says *"per principle WT<N>."*
**Operationalises principles:** P1 (built right, not as a placeholder), P3 (every template follows the same shape), P9 (asset-class-blind substrate — the *defining* constraint at the workflow layer), P11 (sibling-isolated agents), and workflow-template-specific WT1–WT16 throughout.
**AC class:** Adding a new template is **Load-bearing** per [`../../00_thesis/02_ai_agent_development_contract.md`](../../00_thesis/02_ai_agent_development_contract.md). Adding a new archetype is **Load-bearing+** (the heaviest workflow-layer change). Run the full self-check; do not skip the gate; for archetype extension, expect multiple reviewers.

---

## When to use this runbook

### Path A — New template inside an existing archetype (common)

- **Introducing a new canonical analysis** whose question shape falls under one of the five existing archetypes (`event_study`, `regime_conditioned_relationship`, `attribution_decomposition`, `cross_sectional_screen`, `backtest`).
- **Introducing a sibling template** to an existing template within the same archetype (different aggregator, different window size, different regime classifier, different financing method) — per WT5's "variants ship as sibling templates" discipline.
- **Promoting a research-only template to the production catalogue** once it has passed the full test set and the supervisor's router taxonomy.

### Path B — New archetype + first template (rare, ADR-gated)

- **Introducing a structurally new analysis shape** the existing five archetypes cannot express. This is heavier: it expands `WORKFLOW_ARCHETYPES` (a closed-family enum, P8), updates the substrate's parity test, AND lands a demonstration template.

## When NOT to use this runbook

- **Tweaking an existing template's slot defaults or descriptions.** That is an in-place edit to the existing `template.yaml` plus a test update. Bump the template's version (when versioning lands; today no version field). No new template.
- **Adding a new operator the existing templates need.** That is an operator-runbook task ([`../operator/runbook.md`](../operator/runbook.md)). New operators land first, then templates that consume them.
- **Adding a new primitive the new template needs.** Primitive-runbook ([`../primitive/runbook.md`](../primitive/runbook.md)). Same ordering: primitives first, then the template that calls them.
- **Building a workflow-local helper composition that runs once.** That is the substrate's open-graph composition path, not a template. Templates are catalogue entries with documented archetypes, signatures, and tests.
- **Building a UI component or rendering layer for an existing template's output.** That belongs in the orchestrator / UI layer (operationalised in [`../workspace/`](../workspace/) (forthcoming)), not in `<agent>/workflows/`.

The dividing line: a new template introduces a *new canonical analysis the catalogue should expose* that the LLM router can route to (Path A), or a *new analysis shape the existing archetypes cannot express* (Path B). Local helpers, runtime compositions, and UI surfaces do not.

---

## Pre-flight check — seven decisions BEFORE writing any code

A new template PR is hard to roll back once landed: the LLM router learns to use the new template; users start expecting the analysis; deprecating it later means cleaning up router cues, catalogue references, and any persisted workflows that reference its `template_id`. Seven decisions to make and document in the PR description before any YAML is drafted. **If any of these is uncertain, stop and ask the human (AC8).**

### 1. Archetype identification (WT1, WT2) — which closed archetype does this belong to?

Write the template's purpose as a single sentence in desk vocabulary. Match it against the five archetypes:

| Archetype | Canonical question shape |
|---|---|
| `event_study` | *"When event E fires on signal X, what is the conditional forward move in target Y over window W?"* |
| `regime_conditioned_relationship` | *"How does the relationship between X and Y differ across regimes of Z over period P?"* |
| `attribution_decomposition` | *"What components contribute to the move in X over period P?"* (no live template yet) |
| `cross_sectional_screen` | *"Across universe U, which instruments rank highest on metric M as of date D?"* (no live template yet) |
| `backtest` | *"What does the signal-driven trading rule (long X / short Y when Z fires, hold for N) earn?"* |

If your one-sentence purpose fits one of these exactly, this is **Path A** (new template in an existing archetype). If your purpose does not fit any of these — the question shape is structurally new — this is **Path B** (new archetype). If your purpose fits two archetypes ("event study + regime conditioning"), the right answer is almost certainly *two templates*, not one (WT5).

### 2. Sibling-template check (WT5) — does an existing template in the same archetype already do this?

If your archetype already has a live template (`event_study`, `regime_conditioned_relationship`, `backtest`), inspect that template's YAML. Specifically check:

- Could the existing template's slot schema accommodate your use case (by binding a different signal primitive, a different aggregator, a different threshold rule)?
- Are you introducing a structurally different topology (a new node, a new edge pattern, a fundamentally different DAG shape), or just a different *value* on an existing knob?

**If "different value on an existing knob"** → use the existing template; bind the slot differently. No new template needed.

**If "structurally different topology"** → you need a sibling template under the same archetype. Confirm: the new template's `archetype_signature` cues are *disjoint* from the existing template's cues (the LLM router needs to distinguish), and the topology genuinely cannot be expressed as a slot extension to the existing template.

If you find yourself wanting to add an `if`-branch in the existing template, you have two templates trying to share a YAML. Split them — write a new sibling.

### 3. Asset-class-blindness commitment (WT3, WT15) — can this run on temperature data?

Rewrite the template's structural topology — its operator DAG and edge structure — as if the inputs were temperature time series, equity prices, or random walks. Does the topology still make sense? If yes, the template is asset-class-blind at the substrate level (correct).

If the topology only makes sense for rates (e.g., one operator's choice depends on a curve_family value), you have rates-specific assumptions leaking into the substrate. Refactor so the asset-class specificity lives in slot-bound primitive choices (per WT3), then the template is reusable for FX, equities, or any future asset class.

The mandatory instrument-agnostic test (WT15 layer 4) is what enforces this at PR review time. If you cannot articulate how the test would pass against a synthetic non-rates resolver, the template is not ready.

### 4. Slot schema design (WT8) — what is genuinely caller-tunable?

List every value in your planned `template.yaml` that the caller (LLM + user prompt) should be able to influence:

- **Signal selection** (`signal_tool_name`, `signal_params`) — almost always slots.
- **Target instrument** (`target_curve_family`, `target_tenor`) — almost always slots.
- **Window / threshold / aggregator** — slots when the analysis is parameterised, locked when the archetype prescribes a single value.
- **Date range** — almost always slots.
- **Financing assumption** (backtest-specific) — slot for the value, but the *operator* that handles financing is topology-locked.

For each value: ask *"would the LLM router benefit from being able to set this differently across prompts?"* If yes, it is a slot. If no (it is intrinsic to the template's identity / archetype shape), it is YAML-locked.

A reasonable v1 template has 4–15 slots. Fewer than 4 suggests the template is over-locked (consider sibling templates); more than 15 suggests slots are leaking topology choices (consider whether some "slots" are actually two templates).

### 5. Topology design (WT7) — what is the DAG?

Sketch the DAG on paper (or in the PR description). Number each node, name each edge, declare each operator. Mark which nodes are primitives (fetch data from L1) and which are operators (compose typed artifacts). Then verify:

- Every operator referenced exists in `OPERATOR_REGISTRY`.
- Every primitive's tool name is reachable via the agent's `primitive_resolver` (or is a `{$slot: tool_name}` reference that will be filled at bind time, per WT3).
- Every edge's `target_input_slot` matches a real `OperatorSpec.input_slots` entry.
- Every edge's source-output type is compatible with the target slot's declared type (WT11).
- The DAG is acyclic.
- The terminal node is reachable from at least one root (WT10).

If any of these fails, the template will not pass `validate_workflow()`. Fix the design before writing YAML.

### 6. Slot constraints (optional; WT12) — are there cross-slot rules?

If your template has slot relationships that the substrate should enforce at bind time (e.g., `high_threshold` must be `>=` `low_threshold` for a regime template), declare a `slot_constraints` entry. The only v1 constraint variant is `RelativeOrderConstraint`; future variants (`mutual_exclusion`, etc.) require closed-family extension.

For each constraint, write:

- The slot names involved.
- The operator (`gt`, `gte`, `lt`, `lte`).
- A `rationale` string that explains *why* the constraint exists — surfaced in the bind-time error message so callers know how to fix a rejected binding.

If no cross-slot rules apply, omit the `slot_constraints` field entirely.

### 7. Archetype signature design (WT14) — what cues will the LLM match?

Draft 4–10 short cues (≤120 chars each) the LLM router will match against user prompts. The cues should:

- Use desk vocabulary specific to your archetype (avoid generic "do an analysis" phrasing).
- Cover the natural-language variation desk analysts use when asking this question.
- Be **disjoint** from any other live template's cues in the same archetype (sibling templates need clean routing).
- Be structural phrases, not full sentences or marketing copy.

Examples from live templates:

- Event study: `"over the last N years, when X widens by Y standard deviations, what is the average M-day forward move in Z?"`, `"event study on signal X with target Y"`, `"conditional forward move when signal exceeds threshold"`.
- Backtest: `"backtest buying X when signal exceeds threshold"`, `"what if I went long X and short Y every time Z spikes"`, `"long-short trade triggered by a signal threshold"`.

If you cannot draft four distinct cues that route cleanly against existing templates' cues, the template's positioning in the catalogue is unclear; either narrow the scope (sibling template) or reconsider the archetype assignment.

---

## Path A — Add a new template inside an existing archetype

### Step 1 — Write the pre-flight answers in the PR description

Before drafting any YAML, the PR description must explicitly answer the seven pre-flight questions above. This is the reviewer's first read. If the answers are missing or hand-waved, the PR bounces before any YAML is reviewed. **Per AC2 (cite, never paraphrase) and AC6 (cite operationalised principles in commits), cite by WT-number throughout.**

The PR description must also explicitly name:

- The existing sibling templates (if any) in the same archetype, and how this template's `archetype_signature` cues are disjoint from theirs.
- The operators + primitives the template will use (and confirm each exists).
- The terminal artifact type.

### Step 2 — Place the template folder

```
<agent>/workflows/<template_id>/
  __init__.py
  template.yaml
```

Naming conventions:

- `<template_id>` is lowercase snake_case, names the analysis (`event_study`, `regime_conditioned_relationship`, `backtest`, `breakeven_event_study`, `pairs_screen`). Template IDs are catalogue keys — they appear in every persisted workflow's lineage, every catalogue page, every test. Choose carefully.
- `<agent>` is the agent that owns the template's primitive resolver. Today: `rates_agent`. A template that mixes rates + FX primitives is forbidden (P11 + WT3 violation); it belongs as two parallel templates under each agent.
- The folder lives at `<agent>/workflows/<template_id>/` (per WT2 + P11). **Never** under `shared/workflows/`.

### Step 3 — Draft `template.yaml`

Eight top-level fields, in this conventional order. (Mandatory fields are marked; the rest may be omitted for templates that legitimately do not need them.)

```yaml
# =============================================================================
# <template_id> — <one-line description of the analysis>
# =============================================================================
# <multi-line description: the canonical question shape this template answers,
#  the canonical V1 binding (a concrete example slot value set), the DAG
#  topology in ASCII art, and any methodology choices that are template-author-
#  locked (per WT8) with their rationale.>
# =============================================================================

template_id: <template_id>                    # MANDATORY; unique catalogue key
archetype: <one of WORKFLOW_ARCHETYPES>       # MANDATORY; per WT1, WT2
description: >-
  <one-line human-readable summary of what the analysis produces; surfaced
  on the TemplateCard per WT13>               # MANDATORY

archetype_signature:                          # MANDATORY (per WT14;
  - "<cue 1 ≤120 chars>"                      # operationally, 4–10 cues)
  - "<cue 2>"
  - "<cue 3>"
  - "<cue 4>"
  # ... up to 10

slot_schema:                                  # MANDATORY (per WT8; may be
  - name: <slot_name>                         # an empty list for fully
    type: <str|int|float|bool|dict|list>     # locked templates, but most
    required: <true|false>                    # templates have at least
    description: >-                           # one slot)
      <one-line description; surfaced on the TemplateCard>
    default: <only when required: false>

slot_constraints:                             # OPTIONAL; per WT12
  - kind: relative_order
    higher: <slot_name>
    lower: <slot_name>
    operator: <gt|gte|lt|lte>
    rationale: >-
      <one-paragraph reason; surfaced in the bind-time error message>

nodes:                                        # MANDATORY (≥1 node)

  # Primitive node — fetches data via the agent's primitive resolver
  - kind: primitive
    node_id: <node_id>
    tool_name: {$slot: <slot_name>}           # or a literal tool name
    output_field: {$slot: <slot_name>}        # or a literal field name
    params:
      <param>: {$slot: <slot_name>}           # or literal value

  # Operator node — composes typed artifacts via shared.operators
  - kind: operator
    node_id: <node_id>
    operator_name: <operator_name>             # ALWAYS literal (WT7)
    params:
      <param>: {$slot: <slot_name>}           # or literal value

edges:                                        # MANDATORY (may be empty for
  - source_node_id: <upstream_node_id>        # primitive-only templates)
    target_node_id: <downstream_node_id>
    target_input_slot: <slot_name_on_target_operator>

literal_bindings: []                          # MANDATORY (may be empty)
                                              # for binding scalar
                                              # constants to operator
                                              # slots that accept_scalar_input

terminal_node_id: <node_id>                   # MANDATORY; per WT10
```

**Per WT7**: every `node_id`, `operator_name`, `source_node_id`, `target_node_id`, `target_input_slot`, and `terminal_node_id` is a literal YAML value. Only node `params` *values*, `PrimitiveNodeTemplate.tool_name`, `PrimitiveNodeTemplate.output_field`, and `LiteralBindingTemplate.value` may be `{$slot: ...}` references.

**Per WT8**: every value in `params` that the caller should be able to override is a slot reference. Hidden hardcoded methodology is forbidden.

**Per WT3**: the operator DAG and edge structure are asset-class-blind. Asset-class specificity lives in the slot-bound `tool_name` selections, in the human-readable `description` / `archetype_signature` cues, and nowhere else.

**Per WT14**: 4–10 archetype-signature cues, each ≤120 chars, non-empty, in desk vocabulary, disjoint from any sibling template's cues.

### Step 4 — Draft `__init__.py`

The canonical four-symbol shape (per WT16):

```python
"""<agent>.workflows.<template_id> — <one-line description>.

Loads ``template.yaml`` and registers the resulting
``WorkflowTemplate`` with the substrate's process-wide template
registry on import.  Importing this module is the entry point for
making the <template_id> template available to the executor /
template-selection LLM step / template-card catalogue.

Public surface
--------------
- ``<TEMPLATE>_TEMPLATE_PATH`` — absolute Path to template.yaml.
- ``load_<template>_template()`` — load + return the
  WorkflowTemplate (cached via the substrate's loader).
- ``register()`` — register the template with the substrate's
  registry.  Idempotent (re-registering identical content is a
  no-op).  Auto-invoked on module import.
"""

from __future__ import annotations

from pathlib import Path

from shared.workflow import (
    WorkflowTemplate,
    load_workflow_template,
    register_template,
)


<TEMPLATE>_TEMPLATE_PATH: Path = (
    Path(__file__).resolve().parent / "template.yaml"
)


def load_<template>_template() -> WorkflowTemplate:
    """Load + return the <template_id> template.  Cached by absolute
    path via shared.workflow.load_workflow_template."""
    return load_workflow_template(<TEMPLATE>_TEMPLATE_PATH)


def register() -> None:
    """Register the <template_id> template with the substrate's
    process-wide registry.  Idempotent."""
    template = load_<template>_template()
    register_template(template)


register()
```

**Per WT16**: the top-level `register()` call is what makes the template appear in the catalogue at agent boot. The function is idempotent (safe under repeated imports); re-registering different content with the same `template_id` raises.

### Step 5 — Wire the template into the user-facing surface(s)

The template's own `__init__.py` self-registers with the substrate at import (per WT16), but **whether the user-facing router, the REST catalogue, the chat session, the CLI, and the LLM-router's desk-phrasing taxonomy see it is a separate decision**. The agent's `<agent>/workflows/__init__.py` is NOT an auto-import barrel; it does not enumerate every template. Instead, **five user-facing surfaces** control visibility, and each one imports templates explicitly. Skipping any one of them is a real wiring gap that the substrate cannot catch — the template will be invisible to whichever surface was omitted.

The five surfaces, in the order a new template should reach them:

#### 5a. MCP server — `rates_agent/workflows/mcp_server.py`

The stdio-MCP entry point that exposes workflow tools to the orchestrator. Without this import, the LLM cannot invoke the template via MCP.

```python
# rates_agent/workflows/mcp_server.py

# Side-effect imports trigger each template's register() and make
# it routable by the LLM.  Explicit, not auto-discovered.
import rates_agent.workflows.cross_sectional_screen  # noqa: F401, E402
import rates_agent.workflows.event_study  # noqa: F401, E402
import rates_agent.workflows.regime_conditioned_relationship  # noqa: F401, E402
import rates_agent.workflows.<new_template_id>  # noqa: F401, E402     # ← added

# Templates can also be intentionally NOT imported here (paused).
# Example: backtest is paused until V2 data prerequisites land:
#   #   import rates_agent.workflows.backtest  # noqa: F401, E402
# When pausing a template, document the rationale in a comment.
```

Add a matching `@mcp.tool()` wrapper for the template at the bottom of the file — one thin function per template, mirroring the existing `event_study_workflow` / `regime_conditioned_relationship_workflow` shape.

#### 5b. REST catalogue — `api/routes/workflows/catalogue.py`

The REST-API catalogue endpoint that exposes `GET /workflows`, `GET /workflows/{template_id}`. Without this import, API callers cannot see the template.

```python
# api/routes/workflows/catalogue.py

import rates_agent.workflows.cross_sectional_screen  # noqa: F401
import rates_agent.workflows.event_study  # noqa: F401
import rates_agent.workflows.regime_conditioned_relationship  # noqa: F401
import rates_agent.workflows.<new_template_id>  # noqa: F401              # ← added
```

#### 5c. Chat session — `orchestrator/session.py`

The chat session's `WorkflowRouter` setup. Without this import, the LLM-driven chat path will not route to the template — even though the substrate has it registered, the chat session's per-session re-import path does not pick it up.

```python
# orchestrator/session.py — inside the session constructor's
# workflow-router bootstrap block (around line 280):

try:
    import rates_agent.workflows.cross_sectional_screen  # noqa: F401
    import rates_agent.workflows.event_study  # noqa: F401
    import rates_agent.workflows.regime_conditioned_relationship  # noqa: F401
    import rates_agent.workflows.<new_template_id>  # noqa: F401         # ← added
except Exception as exc:
    logger.warning(
        "[%s] failed to import workflow templates; workflow "
        "routing disabled for this session: %s",
        self.thread_id, exc,
    )
```

#### 5d. CLI — `rates_agent/workflows/cli.py`

The CLI entry point. Without this import, CLI invocations of the template will fail.

```python
# rates_agent/workflows/cli.py

import rates_agent.workflows.cross_sectional_screen  # noqa: F401, E402
import rates_agent.workflows.event_study  # noqa: F401, E402
import rates_agent.workflows.regime_conditioned_relationship  # noqa: F401, E402
import rates_agent.workflows.<new_template_id>  # noqa: F401, E402        # ← added
```

#### 5e. LLM-router desk-phrasing taxonomy — `orchestrator/workflow_prompts.py`

The "CANONICAL DESK PHRASING THE LLM SHOULD RECOGNIZE" section maps desk vocabulary (the same cues the template declares in `archetype_signature`) to template IDs. Without an entry here, the LLM router has no learned hint that a prompt matching the new template's cues should route to it — the substrate's `archetype_signature` cues alone are not always sufficient because the router prompt teaches the LLM the canonical-cue → template-id mapping explicitly.

```python
# orchestrator/workflow_prompts.py — inside CANONICAL DESK PHRASING:

- <New-template> cues: "<cue 1>", "<cue 2>", ...

These cues map to template_ids ``event_study``, \
``regime_conditioned_relationship``, ``cross_sectional_screen``, \
and ``<new_template_id>`` respectively.
```

Use the same cues you declared in the template's `archetype_signature` (per WT14) — copy them verbatim so the two sources stay in lockstep. If the prompt and the YAML drift, the router gets confused.

#### What each surface does and does NOT cover

| Surface | Covers | If omitted |
|---|---|---|
| **5a** `mcp_server.py` | MCP `@mcp.tool()` invocation; stdio LLM routing | LLM cannot call the workflow via MCP at all |
| **5b** `catalogue.py` | REST `GET /workflows[/{id}]`; UI catalogue | UI catalogue + REST callers do not see the template |
| **5c** `session.py` | Chat session's per-session `WorkflowRouter` | Chat router will not learn the template exists this session |
| **5d** `cli.py` | CLI invocation | CLI cannot dispatch the template |
| **5e** `workflow_prompts.py` | LLM-router's desk-vocabulary → template_id mapping | LLM may not recognise desk phrasing routes to this template |

For a new template that should be user-facing from day one, add the import to **all five** surfaces. For a template that is genuinely paused (data prerequisites missing, archetype not yet user-ready, etc.), leave the user-facing imports out *and document the reason* in a comment block at the relevant entry point (cf. the backtest comment in `mcp_server.py`). The template is still substrate-registered (so the executor can dispatch it and tests can exercise it), but it does not reach the user-facing surfaces.

If you skip the user-facing imports for a template that should be user-visible, the template is invisible to whichever surface was missed. Common failure modes the five-surface checklist catches:

- "It works via MCP but not via the chat session" → 5c was missed.
- "The catalogue UI shows it but the LLM never picks it" → 5e was missed.
- "Tests pass but the CLI throws `KeyError: unknown template`" → 5d was missed.
- "It worked locally but the REST catalogue is empty" → 5b was missed.

The CI suite (`tests/test_workflow_router.py`, `tests/test_workflow_template_system.py`) catches some — but not all — of these gaps. The five-surface checklist is the procedural guarantee.

> **Historical note.** The v1.1 runbook (and the v1.1 README's WT16) named only the first two surfaces (`mcp_server.py` + `catalogue.py`). That was incomplete — three additional surfaces had the same explicit-import pattern but were not enumerated. The omission surfaced during the Stage-4 Round-3 review of PR #201 (Codex F3): `cross_sectional_screen` shipped registered in two of the five surfaces and invisible in the other three. The v1.2 enumeration above is canonical going forward.

### Step 6 — Write the test suite (per WT15)

Five test layers, all required. Place in `tests/test_workflow_<template_id>.py`:

#### 6a. Structural validity

```python
def test_<template_id>_loads_and_registers():
    from <agent>.workflows.<template_id> import (
        load_<template>_template,
        register,
    )

    template = load_<template>_template()

    # Structural shape
    assert template.template_id == "<template_id>"
    assert template.archetype == "<archetype>"
    assert template.terminal_node_id in {n.node_id for n in template.nodes}
    assert len(template.nodes) >= 1
    assert len(template.archetype_signature) >= 4

    # Archetype signature cue constraints (WT14)
    for cue in template.archetype_signature:
        assert 0 < len(cue) <= 120
        assert isinstance(cue, str)

    # Idempotent registration (WT16)
    register()
    register()  # no-op on second call
```

#### 6b. Slot-binding rejection (one test per failure mode, per WT12)

```python
def test_<template_id>_rejects_missing_required_slot():
    template = load_<template>_template()
    incomplete_values = {<all required slots minus one>}
    with pytest.raises(SlotBindingError, match="<specific message>"):
        template.bind(incomplete_values)


def test_<template_id>_rejects_unknown_slot():
    template = load_<template>_template()
    bad_values = {<all valid slots>, "bogus_slot": "x"}
    with pytest.raises(SlotBindingError, match="unknown slot"):
        template.bind(bad_values)


def test_<template_id>_rejects_wrong_type():
    template = load_<template>_template()
    bad_values = {<all valid slots, except one with wrong type>}
    with pytest.raises(SlotBindingError, match="type"):
        template.bind(bad_values)


def test_<template_id>_rejects_constraint_violation():    # only if the template declares slot_constraints
    template = load_<template>_template()
    bad_values = {<all valid slots, except one that violates a constraint>}
    with pytest.raises(SlotBindingError, match="<rationale fragment>"):
        template.bind(bad_values)
```

#### 6c. Real-data E2E

```python
def test_<template_id>_e2e_with_real_resolver(real_db_engine):
    from <agent>.workflows import <agent>_primitive_resolver
    from shared.workflow import execute_workflow

    template = load_<template>_template()
    workflow = template.bind(<canonical_v1_slot_values>)
    result = execute_workflow(
        workflow,
        engine=real_db_engine,
        primitive_resolver=<agent>_primitive_resolver,
    )

    # Terminal artifact shape
    assert artifact_type_name(result.terminal_artifact) == "<expected_type>"

    # Every node produced an intermediate artifact (executor cache check).
    # Note: do NOT assert
    #   len(result.terminal_artifact.lineage.steps) >= len(template.nodes)
    # That invariant only holds for purely linear DAGs. For branched
    # DAGs, auxiliary branches embed via OperatorStep.auxiliary_lineages
    # (OPR10) rather than as top-level steps in the primary chain.
    expected_node_ids = {n.node_id for n in template.nodes}
    assert set(result.node_artifacts.keys()) == expected_node_ids

    # Workflow's human-readable summary names every node (executor
    # builds this from the topologically-sorted node sequence).
    for nid in expected_node_ids:
        assert nid in result.workflow_lineage_summary
```

For most templates, `real_db_engine` is a fixture that connects to a test-scoped Postgres with synthetic but realistic time-series data. See `tests/conftest.py` for the fixture pattern.

#### 6d. Mandatory instrument-agnostic test (WT3 enforcement)

This test proves the **operator substrate** (operator DAG + edge structure) is asset-class-blind (WT3). There is no shared `synthetic_primitive_resolver` in the codebase; each template's test file defines what it needs. Two patterns, picked based on whether the template's `tool_name` fields are slot-substituted (preferred) or literal (the backtest-style tradeoff documented in WT3):

**Pattern A — slot-substituted `tool_name` templates (`event_study`, `regime_conditioned_relationship`, …).** Build a local finance-blind resolver out of one or more synthetic `PrimitiveSpec` entries (hand-crafted callable + `*Input` + `*Output` Pydantic classes + a stub `config.yaml`); bind the template's slots to the synthetic tool names; execute. Pattern shown in [`tests/test_workflow_event_study.py`](../../../tests/test_workflow_event_study.py)'s `synthetic_resolver` fixture and `_synthetic_signal_callable` / `_synthetic_target_callable` helpers.

```python
@pytest.fixture
def synthetic_resolver(tmp_path) -> PrimitiveResolver:
    # Build PrimitiveSpec entries for the synthetic tools this
    # template needs.  Each callable accepts (engine, params, config)
    # and returns a dict the bridge can lift to a Series/Panel artifact.
    signal_spec = PrimitiveSpec(
        tool_name="synthetic_signal_tool",
        callable=_synthetic_signal_callable,
        # ...input/output classes, config_path, output_field_units...
    )
    target_spec = PrimitiveSpec(...)
    specs = {s.tool_name: s for s in [signal_spec, target_spec]}
    return lambda name: specs[name]


def test_<template_id>_runs_against_synthetic_resolver(synthetic_resolver):
    template = load_<template>_template()
    workflow = template.bind({
        "signal_tool_name": "synthetic_signal_tool",
        "target_tool_name": "synthetic_target_tool",
        # ... other slot values bound to synthetic-friendly inputs ...
    })
    result = execute_workflow(
        workflow, engine=None, primitive_resolver=synthetic_resolver,
    )
    assert artifact_type_name(result.terminal_artifact) == "<expected_type>"
```

**Pattern B — literal-primitive templates (`backtest`-style).** The template's `tool_name` fields are not slot-substituted because the analysis requires specific primitives (per WT3). Use the real agent resolver, but patch each primitive's DB-fetcher with `tests._workflow_synthetic_fetchers.patch_all_synthetic_fetchers()` (or the narrower `q1_canonical_fetchers_context()` / `q2_canonical_fetchers_context()`) so the substrate runs end-to-end on synthetic data:

```python
from tests._workflow_synthetic_fetchers import patch_all_synthetic_fetchers


def test_<template_id>_runs_against_synthetic_fetchers():
    template = load_<template>_template()
    workflow = template.bind(<canonical_slot_values>)

    patches = patch_all_synthetic_fetchers()
    for p in patches:
        p.start()
    try:
        result = execute_workflow(
            workflow, engine=None,
            primitive_resolver=<agent>_primitive_resolver,
        )
    finally:
        for p in patches:
            p.stop()

    assert artifact_type_name(result.terminal_artifact) == "<expected_type>"
```

In both patterns, the data flowing through the operator chain is synthetic (random walks / cosine waves / temperature-shaped values — nothing rates-specific). If the operator chain runs to completion and produces a structurally correct terminal artifact, the template's *operator substrate* is asset-class-blind. If it fails because an operator received unexpected data shape, the template has asset-class assumptions leaking into operator params (refactor) or into operator selection (refactor).

#### 6e. Topology-archetype-fit gate

```python
def test_<template_id>_uses_only_archetype_appropriate_operators():
    """Prevent archetype drift: a template whose archetype is X
    should use only the operators that belong to archetype X's
    structural family.  Allow-list, not deny-list, per WT15."""
    template = load_<template>_template()
    operators_used = {
        n.operator_name for n in template.nodes if n.kind == "operator"
    }
    # The allow-list for this archetype; document each choice.
    allowed = {
        # e.g. event_study: align_series, threshold_events,
        #      event_windows, conditional_aggregate,
        #      summarize_series, series_arithmetic
        "<operator_1>", "<operator_2>", ...
    }
    assert operators_used <= allowed, (
        f"{operators_used - allowed} not in archetype's allow-list; "
        f"if they belong, update the allow-list with rationale; "
        f"if not, the template is drifting into a different archetype."
    )
```

The allow-list is the load-bearing part of this test. Updating it requires a reviewer to confirm the new operator is consistent with the archetype's structural identity; this is the gate that catches archetype drift.

### Step 7 — Run CI and substrate validation

```bash
# Template loads, schema validates, registers
pytest tests/test_workflow_<template_id>.py -v

# Substrate-wide regression
pytest tests/test_workflow_template_system.py tests/test_workflow_substrate.py -v

# Full template registry stays consistent
pytest tests/ -v
```

The `test_workflow_template_system.py` suite typically asserts every template folder under `<agent>/workflows/` is registered after agent boot — if the new template's folder exists but the agent's `__init__.py` does not import it, this test fails.

---

## Path B — Add a new archetype + first template (ADR-gated)

When the template you want to add does not fit any of the five existing archetypes' question shapes, you are extending the closed `WORKFLOW_ARCHETYPES` family. This is heavier: it expands the substrate's closed enum and requires multi-reviewer sign-off.

### Step B1 — File the ADR

Create `../../05_decisions/<NNNN>_admit_<archetype_name>_workflow_archetype.md`. The ADR is mandatory under WT4; no PR drafts code before this file exists.

The ADR must answer:

- **Archetype shape** — what is the canonical question shape this archetype owns (one paragraph, desk vocabulary)?
- **Why existing archetypes don't cover it (WT4, WT5)** — name each of the five existing archetypes and explain why its question shape is structurally inadequate.
- **First template's design** — the template that will land alongside (template_id, slot schema sketch, DAG sketch, terminal artifact type).
- **Substrate diff** — the lines that change in `shared/workflow/template.py` (the `Literal` and the tuple), in the parity test (`tests/test_workflow_template_system.py`'s `known_archetypes()` check), and in any LLM-router taxonomy reference.
- **Future-template space** — anticipated sibling templates that might land in this archetype later (so reviewers can judge whether the archetype is a true generalisation or a one-off masquerading as a generalisation).
- **Reservation vs implementation** — whether this admission lands a first template (preferred) or is a forward declaration (like `attribution_decomposition` today; permitted but must be deliberate).

### Step B2 — Extend the closed family in the substrate

In [`shared/workflow/template.py`](../../../shared/workflow/template.py):

```python
WorkflowArchetype = Literal[
    "event_study",
    "regime_conditioned_relationship",
    "attribution_decomposition",
    "cross_sectional_screen",
    "backtest",
    "<new_archetype>",            # ← added per ADR-NNNN
]


WORKFLOW_ARCHETYPES: tuple[str, ...] = (
    "event_study",
    "regime_conditioned_relationship",
    "attribution_decomposition",
    "cross_sectional_screen",
    "backtest",
    "<new_archetype>",            # ← added per ADR-NNNN
)
```

Both the `Literal` and the tuple are updated together. The substrate's `known_archetypes()` reads from the tuple; the loader validates `template.archetype` against the `Literal`. The two staying in sync is the closed-family discipline.

### Step B3 — Update the parity test

In `tests/test_workflow_template_system.py` (or wherever `known_archetypes()` is asserted), update the expected tuple:

```python
def test_known_archetypes_matches_closed_family():
    expected = (
        "event_study",
        "regime_conditioned_relationship",
        "attribution_decomposition",
        "cross_sectional_screen",
        "backtest",
        "<new_archetype>",        # ← added
    )
    assert tuple(known_archetypes()) == expected
```

This parity test is what catches an enum/tuple drift between the `Literal` and the tuple. Failing this test in CI means the substrate is in an inconsistent state — both must be updated, not one.

### Step B4 — Land the first template (or document forward-declaration)

**Strongly preferred:** land a real template in the same PR following Path A above. The template's `archetype` value is the new value. The full Path A procedure applies (steps 1–7).

**Permitted but deliberate:** land the enum extension as a forward declaration, with no template yet. The ADR must explicitly state this is a forward declaration and name the expected first-template PR (with a planned timeline). Existing forward declarations (`attribution_decomposition`, `cross_sectional_screen`) are precedents.

A forward declaration accumulates taxonomy debt; multiple unimplemented archetypes degrade the catalogue's signal-to-noise for the LLM router. The bar for forward declaration should be high.

### Step B5 — Update the LLM router's archetype-signature taxonomy

If the supervisor's LLM router has a separate taxonomy reference for routing (today this lives implicitly in the `archetype_signature` cues per template; future iterations may have a central router config), update it to recognise the new archetype.

### Step B6 — Multi-reviewer sign-off

Archetype extension touches every downstream consumer (router, catalogue UI, eval gauntlet, replay tooling). The PR requires at least two reviewers, including a substrate owner. The ADR's framing is what those reviewers evaluate first; the code follows.

---

## Automation scope — what the build-bot is and is not allowed to do

Workflow templates are **not currently bot-eligible.** The automation bot at `tmp/automation/primitive_automation/` is scoped to primitives (specifically, Bucket 1A standard primitives — Archetype A / B in level / spread shape). Operators, artifacts, and workflow templates are explicitly outside the bot's scope.

This is deliberate. Template authorship requires:

- **Archetype identification** (WT1) — judgment about which existing archetype the analysis fits, or whether a new archetype is warranted.
- **Sibling-template disambiguation** (WT5) — judgment about whether the proposed template overlaps an existing sibling.
- **Slot schema design** (WT8) — judgment about which knobs the LLM router and the user should be able to influence.
- **Topology design** (WT7) — judgment about the operator DAG that expresses the analysis.
- **Archetype-signature cue design** (WT14) — judgment about how the LLM should route this template.

These are not pattern-matching tasks. The right scope for automation at the template layer is scaffold generation (boilerplate `__init__.py`, test-skeleton creation) once the design decisions are made, not the design decisions themselves. The scope-expansion path is documented in [`../primitive/runbook.md`](../primitive/runbook.md)'s automation section.

---

## Common pitfalls

Things reviewers see repeatedly:

- **Topology drift via slot substitution.** Putting `{$slot: ...}` in `operator_name`, `node_id`, `source_node_id`, `target_node_id`, `target_input_slot`, or `terminal_node_id`. WT7 violation; substitutes the analysis rather than a parameter.
- **Hidden methodology in `params`.** A constant in `params` that should be a slot. WT8 violation; the caller cannot override.
- **A `mode` slot switching between archetypes.** WT1 + WT5 violation; two templates compressed badly into one.
- **Asset-class assumptions in operator params.** A template whose substrate only makes sense for rates. WT3 + WT15 violation; the instrument-agnostic test fails.
- **Missing instrument-agnostic test.** The asset-class-blindness claim is not actually tested. WT15 violation; PR auto-reject.
- **Empty `archetype_signature`.** The template is unselectable. WT14 violation.
- **Cues that overlap a sibling template's cues.** The LLM router routes ambiguously. WT5 + WT14 violation.
- **Template registered manually outside `__init__.py::register()`.** WT16 violation; couples the agent to template detail.
- **Template's folder under `shared/workflows/` instead of `<agent>/workflows/`.** WT2 + P11 violation.
- **Two terminal nodes** (schema rejects). Conceptually two analyses bundled.
- **A slot's `default` provided when `required: true`.** Schema rejects (validator at construction).
- **A `default` for a slot that should be required**, masking "caller didn't pass a value" as a silent fallback. WT12 violation.
- **Topology-archetype-fit test that uses a deny-list.** Allow-lists encode the archetype's structural identity; deny-lists drift as new operators land. WT15 violation.
- **An edge whose `target_input_slot` is not a key in the target operator's `OperatorSpec.input_slots`.** Validator rejects; usually a typo or stale operator spec.

## PR review checklist

The reviewer signs off when each item is met. Cite the matching WT-number; do not paraphrase (AC2). For Path B (archetype extension), additionally use a multi-reviewer sign-off.

### Definitional (WT1–WT3)

- [ ] **WT1.** Template owns exactly one archetype; the description and cues describe one canonical question shape, not several.
- [ ] **WT2.** `archetype` is a member of `WORKFLOW_ARCHETYPES`; template lives under `<agent>/workflows/<template_id>/`.
- [ ] **WT3.** Substrate (operator DAG, edges, operator selection) is asset-class-blind. The temperature-data test passes (covered by WT15 layer 4).

### Admission (WT4–WT6)

- [ ] **WT4.** If this PR extends `WORKFLOW_ARCHETYPES`: ADR filed; `Literal` + tuple + parity test all updated; first template lands or forward-declaration is explicit.
- [ ] **WT5.** Sibling templates in the same archetype identified; this template's `archetype_signature` cues are disjoint from theirs.
- [ ] **WT6.** Every operator + primitive the template references exists in the substrate; the terminal artifact has a real consumer (UI surface or downstream workflow).

### Well-formedness (WT7–WT12)

- [ ] **WT7.** No `{$slot: ...}` in `node_id`, `operator_name`, `source_node_id`, `target_node_id`, `target_input_slot`, or `terminal_node_id`. Substitution allowed only on node `params` *values*, `PrimitiveNodeTemplate.tool_name`, `PrimitiveNodeTemplate.output_field`, and `LiteralBindingTemplate.value`.
- [ ] **WT8.** Slot schema covers every caller-tunable knob; no hidden hardcoded methodology in `params` that ought to be a slot.
- [ ] **WT9.** Every `slot_schema[].type` is one of `"str", "int", "float", "bool", "dict", "list"`.
- [ ] **WT10.** `terminal_node_id` references a real node reachable from at least one root; the terminal artifact type is a member of `ARTIFACT_TYPE_NAMES`.
- [ ] **WT11.** Every edge's `target_input_slot` matches a real `OperatorSpec.input_slots` entry; source-output types are compatible with target-slot types.
- [ ] **WT12.** Slot-binding-rejection tests cover every failure mode; the executor's runtime errors propagate as `WorkflowExecutionError`; no silent fallbacks.

### Operational (WT13–WT16)

- [ ] **WT13.** `card_for_template(template)` produces correct `primitives_used` (with `<via $slot:NAME>` markers where applicable), `operators_used` (concrete names), and `terminal_artifact_type`.
- [ ] **WT14.** 4–10 archetype-signature cues, each ≤120 chars, non-empty, in desk vocabulary, disjoint from sibling templates.
- [ ] **WT15.** Test suite covers all five layers: structural validity, slot-binding rejection, real-data E2E, **mandatory instrument-agnostic E2E**, topology-archetype-fit gate (allow-list).
- [ ] **WT16.** `<agent>/workflows/<template_id>/__init__.py` follows the canonical four-symbol pattern; top-level `register()` call; idempotent.

### Universal items

- [ ] **Step 5 — five user-facing surfaces (per WT16).** For a user-facing template, every one of the five surfaces below imports the new template package explicitly. For a paused template, all five surfaces are deliberately skipped *and* the rationale is documented in a comment at the relevant entry point.
  - [ ] **5a `rates_agent/workflows/mcp_server.py`** — side-effect import + matching `@mcp.tool()` wrapper.
  - [ ] **5b `api/routes/workflows/catalogue.py`** — side-effect import.
  - [ ] **5c `orchestrator/session.py`** — side-effect import in the chat session's workflow-router bootstrap block.
  - [ ] **5d `rates_agent/workflows/cli.py`** — side-effect import.
  - [ ] **5e `orchestrator/workflow_prompts.py`** — desk-phrasing cues block + `template_id` reference in `CANONICAL DESK PHRASING THE LLM SHOULD RECOGNIZE`.
- [ ] **AC2 / AC6.** Commit message ends with `Operationalises: P3, P9, P11; WT1, WT3, WT7, WT8, WT11, WT15, WT16; AC1, AC3, AC5, AC6.` (adjust IDs to whichever apply). For archetype extension also cite WT2, WT4.
- [ ] **AC8.** Any uncertainty about archetype identification, sibling-template disambiguation, or asset-class-blindness was raised with a human reviewer before YAML was drafted, not after.

## Version log

| Version | Date | Change | ADR |
|---|---|---|---|
| v1.2 | 2026-05-25 | Step 5 rewritten to enumerate **five** user-facing surfaces (was: two).  The v1.1 runbook named only `rates_agent/workflows/mcp_server.py` + `api/routes/workflows/catalogue.py`; three additional surfaces have the same explicit-import requirement and were silently missed by templates that followed the v1.1 procedure to the letter: (5c) `orchestrator/session.py`'s chat-session WorkflowRouter bootstrap; (5d) `rates_agent/workflows/cli.py`'s CLI entry point; (5e) `orchestrator/workflow_prompts.py`'s `CANONICAL DESK PHRASING THE LLM SHOULD RECOGNIZE` taxonomy.  The gap surfaced during the Stage-4 Round-3 review of PR #201 (Codex F3): the new `cross_sectional_screen` template shipped registered in 5a + 5b only and was invisible to chat / CLI / LLM-router-prompt.  PR-A8 added the three missing imports + cues block; this runbook revision pins the procedural guarantee so future templates do not repeat the omission.  Also added: a per-surface table summarising "what each covers / what breaks if omitted"; an explicit historical-note callout pinning the v1.1 → v1.2 motivation; PR review checklist's "Universal items" rewritten as a five-item sub-checklist (was: a single "agent's workflows/__init__.py" line that the runbook's own Step 5 explicitly contradicted). | (pending) |
| v1.1 | 2026-05-18 | Pre-canonical corrections aligned with the README v1.1 revisions: (a) Step 5 rewritten — replaced the incorrect "wire into agent's `workflows/__init__.py`" pattern with the real two-surface explicit-import pattern (`rates_agent/workflows/mcp_server.py` for MCP/LLM routing, `api/routes/workflows/catalogue.py` for REST exposure); documented that the agent's `workflows/__init__.py` is NOT an auto-import barrel. Added paused-template example with the `backtest` comment-block pattern. (b) Step 6c E2E assertion — replaced the wrong `len(result.terminal_artifact.lineage.steps) >= len(template.nodes)` invariant (which only holds for linear DAGs; branched DAGs embed auxiliary lineages via `OperatorStep.auxiliary_lineages`) with the real assertion pattern from `tests/test_workflow_event_study.py` (assert every `node_id` is in `result.node_artifacts` and in `result.workflow_lineage_summary`). (c) Step 6d instrument-agnostic test — corrected the wrong import (`tests._workflow_synthetic_fetchers.synthetic_primitive_resolver` does not exist; that file provides fetcher patches, not a resolver). Documented both real patterns: Pattern A (slot-substituted `tool_name` templates) builds a local `PrimitiveResolver` from synthetic `PrimitiveSpec` entries; Pattern B (literal-primitive templates like `backtest`) uses the agent's real resolver with `patch_all_synthetic_fetchers()` context. Superseded the next week by v1.2 after the five-surface gap surfaced. | (pending) |
| v1 | 2026-05-17 | Initial runbook for adding a new workflow template (Path A) and for extending the closed `WORKFLOW_ARCHETYPES` family (Path B). Seven pre-flight decisions, seven-step Path-A procedure, six-step Path-B procedure. Superseded by v1.1 the next day after a factual-review pass against the live workflow substrate. | — |
