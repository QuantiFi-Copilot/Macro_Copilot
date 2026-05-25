# Workflow Template

> The contract every workflow template in the Macro Copilot platform must satisfy — what makes something a valid template at all, what makes a particular template well-formed, and the principles that govern when (and how) a new template (or a new archetype) may be added. **Asset-class-blind by construction** — workflow templates compose finance-blind operators around finance-aware primitives via slot bindings; the *template's substrate* (nodes + edges + operators) is asset-class-blind, while the asset-class specificity lives inside the slot-bound primitive choices and the human-readable naming.

**Version:** v1.1
**Last reviewed:** 2026-05-18
**Status:** load-bearing component contract. Changes require an ADR in [`../../05_decisions/`](../../05_decisions/).
**Operationalises principles:** P1 (built right, not as a placeholder), P3 (consistency by contract — every template has the same shape), P4 (determinism — bound templates are content-addressed by the lineage of their terminal artifact), P5 (honest disclosure — methodology lives in YAML on the card, not in code), **P8 (closed-family discipline — the *archetype* family is closed; the *template* catalogue is open within that family)**, P9 (asset-class-blind substrate — the operator DAG never branches on asset class), P10 (single source of truth — `WORKFLOW_ARCHETYPES` and `SlotDeclaration.type` are the only valid enumerations), P11 (sibling-isolated agent packages — templates live under `<agent>/workflows/`, never under `shared/`).
**See also:** [`runbook.md`](runbook.md) — the procedure for adding a new template (or a new archetype, which is the heavier closed-family extension).

---

## What this folder is

The contract every workflow template must satisfy. Templates are the **declarative DAG specification** between user intent and the operator/primitive substrate: the supervisor's LLM step routes a user prompt to a template, the user (or LLM) fills the template's slots, the substrate binds + validates + executes, and the terminal artifact is the user-facing deliverable. Templates are *where the platform's analyses live* — every chart, every backtest, every event study a user sees is produced by a template.

The workflow-template layer is **structurally different** from the primitive, operator, and artifact layers:

- **Primitives and operators are *folders* of compute code**; artifacts are *types in a closed family*. Each is one instance / one entry in a registry.
- **Workflow templates are *YAML declarations* of node-DAGs over the operator + primitive substrate.** Each template lives in `<agent>/workflows/<template>/` as a `template.yaml` plus an `__init__.py` registration hook. The YAML is the contract; the `__init__.py` is the wiring; the substrate (`shared/workflow/`) does the parsing, validation, registration, binding, and execution.
- **The archetype family is closed; the template catalogue is open within it.** There are exactly five archetypes today (`event_study`, `regime_conditioned_relationship`, `attribution_decomposition`, `cross_sectional_screen`, `backtest`); adding a sixth is a P8-gated closed-family extension. Adding a new template *within* an existing archetype is an open extension (a sibling YAML in the same agent's workflow folder).

This document is organised as **principles** (same as primitive / operator / artifact), grouped in four bins:

- **Definitional (WT1–WT3)** — *is this actually a workflow template?*
- **Admission (WT4–WT6)** — *should this template (or archetype) exist at all?*
- **Well-formedness (WT7–WT12)** — *what makes a particular template valid?*
- **Operational (WT13–WT16)** — *the build conventions every template follows.*

A note on relationship to the primitive / operator / artifact contracts:

| Principle | Primitive (PR) | Operator (OPR) | Artifact (ART) | Workflow Template (WT) |
|---|---|---|---|---|
| Concept ownership | PR1 (finance concept) | OPR1 (structural method family) | ART1 (one structural shape) | WT1 (one workflow archetype — one canonical question shape) |
| Closed-family / residence | PR8 (Convention enum) | OPR3 (shared/operators/) | ART2 (closed family in `ARTIFACT_TYPE_NAMES`) | WT2 (closed `WORKFLOW_ARCHETYPES` enum, P11-isolated under `<agent>/workflows/`) |
| Parsimony / admission | PR4 (composability + LLM clarity) | OPR4 (promotion rule) | ART4 (ADR-gated extension) | WT4 (archetype extension is ADR-gated); WT5 (V1: one canonical template per archetype; variants ship as sibling templates) |
| Methodology surface | PR7 (config.yaml) | OPR7 (config.yaml + design-lock allowance) | N/A (artifacts have no methodology) | WT8 (slot schema is the ONLY caller-visible methodology surface; everything else is YAML-locked topology) |
| Determinism / replay | PR4 / PR15 (parity fixture) | OPR14 (pure function) | ART10 (content-addressed via `lineage.head_hash`) | WT12 (bound templates execute deterministically; the terminal artifact's `head_hash` is the workflow's replay key) |
| Provenance | PR10 (reachability) | OPR10 (lineage extension) | ART9 (mandatory lineage chain) | WT11 (every edge's typed artifact-hand-off propagates lineage automatically through the substrate; templates declare topology, not lineage) |
| Honest refusal | PR11 (raise or envelope) | OPR13 (raise only) | ART11 (validators raise at construction) | WT12 (bind-time validation raises `SlotBindingError`; runtime errors raise `WorkflowExecutionError`; no silent fallback) |
| Test pattern | PR16 (compute + wiring + SQL parity) | OPR16 (unit + integration) | ART13 (round-trip + validator + lineage) | WT15 (structural-validity + slot-binding-rejection + real-data E2E + **mandatory instrument-agnostic test** + topology-archetype-fit gate) |
| Asset-class blindness | implicit (primitives carry domain) | OPR6 (asset-class-blind operators) | ART3 (asset-class-blind types) | WT3 (asset-class-blind substrate; asset-class specificity lives only in slot-bound primitive choices) |

Each principle below cites its primitive / operator / artifact analog where one exists and names where workflow-template behaviour diverges.

## What a workflow template *is* — the universal contract

Every workflow template is **a `WorkflowTemplate` (frozen Pydantic) loaded from a `template.yaml` file**, living at `<agent>/workflows/<template>/template.yaml`, and registered into a process-wide catalogue at import time. The schema (declared in [`shared/workflow/template.py`](../../../shared/workflow/template.py)) carries these load-bearing parts:

```python
class WorkflowTemplate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    template_id: str                            # unique catalogue key
    archetype: WorkflowArchetype                # one of 5 closed values
    description: str                            # one-line for the card
    slot_schema: List[SlotDeclaration]          # caller-fillable surface
    nodes: List[WorkflowNodeTemplate]           # the DAG vertices (≥1)
    edges: List[WorkflowEdge]                   # typed artifact hand-offs
    literal_bindings: List[LiteralBindingTemplate]  # constants bound to slots
    terminal_node_id: str                       # which node's output is terminal
    archetype_signature: List[str]              # LLM-match cues
    slot_constraints: List[SlotConstraint] = []  # cross-slot validation

    def bind(self, slot_values: Dict[str, Any]) -> "Workflow":
        # Validates slot values, evaluates cross-slot constraints,
        # substitutes {$slot: name} references, and constructs a
        # concrete Workflow ready for execution.
        ...
```

The YAML carries the same structural shape; the loader (`load_workflow_template(path)` in [`shared/workflow/template_loader.py`](../../../shared/workflow/template_loader.py)) parses + validates + caches by absolute path. The template's `__init__.py` calls `register_template(template)` at import time so the substrate's catalogue (`_REGISTRY` in [`shared/workflow/template_registry.py`](../../../shared/workflow/template_registry.py)) holds every known template.

The closed archetype family today is exactly five values, declared in [`shared/workflow/template.py`](../../../shared/workflow/template.py):

```python
WORKFLOW_ARCHETYPES: tuple[str, ...] = (
    "event_study",
    "regime_conditioned_relationship",
    "attribution_decomposition",
    "cross_sectional_screen",
    "backtest",
)
```

Three of these have live templates today (`event_study`, `regime_conditioned_relationship`, `backtest`); two (`attribution_decomposition`, `cross_sectional_screen`) are in the enum but have no template yet. The enum membership does not require an immediate template — it expresses that the platform's archetype taxonomy has reserved that slot.

The closed slot-type taxonomy is exactly six values:

```python
SlotDeclaration.type ∈ {"str", "int", "float", "bool", "dict", "list"}
```

The closed slot-constraint family today has exactly one variant: `RelativeOrderConstraint`. Future variants (`mutual_exclusion`, `conditional_required`, `set_membership`, `regex_match`) are reserved per the constraint-union design but not yet implemented.

A bound `WorkflowTemplate` produces a `Workflow` (substrate-level concrete DAG, [`shared/workflow/types.py`](../../../shared/workflow/types.py)). The executor (`execute_workflow(workflow)` in [`shared/workflow/executor.py`](../../../shared/workflow/executor.py)) runs the DAG in topological order, holds every intermediate artifact in an in-memory `node_artifacts` dict, and returns a `WorkflowResult` whose `terminal_artifact` is the user-facing deliverable plus the full intermediate set keyed by `node_id`. Persistence of artifacts to the artifact store (if any) happens at a higher layer than the executor itself; the executor's contract is in-memory only.

## What a workflow template is *not*

Six boundary statements that prevent common misclassifications:

- **Not a free-form DAG built by the LLM.** Topology — node IDs, operator names, edge structure — is template-author-locked. The LLM's *only* control surface is filling declared slots (per WT7 + WT8). A workflow built by stringing operators together at runtime is not a template; it is the **open-graph composition** path, which is a separate (and explicitly more dangerous) execution mode the substrate supports but which the closed catalogue does not include.
- **Not a script.** A template does not run code at parse time. It declares structure; the executor runs it. No template's `template.yaml` may reference Python callables or import side-effects.
- **Not a primitive.** Primitives are finance-aware compute (rates_agent/primitives/...). Templates compose primitives via node entries, but they do not own compute. A "small workflow" with one primitive node and one operator is still a template, not a primitive — what matters is the DAG declaration, not the size.
- **Not an operator.** Operators are finance-blind structural transformations. Templates compose operators; they are not operators themselves. A template never appears in `OPERATOR_REGISTRY`.
- **Not a UI component.** Templates produce artifacts; the UI renders artifacts. The template's terminal artifact type (a member of the substrate's `TerminalArtifact` union — today `Series`, `SeriesSet`, `EventSet`, `Panel`, or `WindowedPanel` per [`shared/workflow/result.py`](../../../shared/workflow/result.py)) is what the UI binds against — the template itself has no rendering logic. `TradeSet` is not currently in the executor's terminal-artifact union; a template ending at `construct_trades` would need to thread a downstream operator (e.g. `evaluate_trades` → `summarize_trades`) before the terminal can be returned.
- **Not optional.** Every workflow served to a user runs through some template (or through the open-graph path, which is gated separately). The template catalogue *is* the platform's analysis catalogue.

## How to use this document

For your first read: scan the **Quick index** below, then read every principle's **Rule** line. The **Why**, **Verify**, and **Anti-patterns** sections are reference material for when a question or a PR turns on a specific principle.

For ongoing work: do not re-read this file from top to bottom. Look up the specific principle by ID when it comes up. Cite by ID (`WT3`, `WT7`) in commit messages, PR comments, and code review — same discipline as the P-numbers from [`../../00_thesis/01_non_negotiables.md`](../../00_thesis/01_non_negotiables.md), the PR-numbers from [`../primitive/README.md`](../primitive/README.md), the OPR-numbers from [`../operator/README.md`](../operator/README.md), and the ART-numbers from [`../artifact/README.md`](../artifact/README.md). The five namespaces are deliberately separate: P-numbers are platform-wide; PR / OPR / ART / WT are component-specific.

## Quick index — the WT-numbers

| ID | Group | Principle | One-line rule |
|---|---|---|---|
| **WT1** | I | Archetype ownership | A template owns exactly one workflow archetype — one canonical question shape. The archetype is the template's identity, not a tag. |
| **WT2** | I | Closed archetype family | The valid set of archetypes is exactly `WORKFLOW_ARCHETYPES` (5 entries today). Adding a new archetype is an ADR-gated closed-family extension; adding a new template within an existing archetype is an open extension. |
| **WT3** | I | Asset-class-blind substrate | The template's nodes-and-edges substrate (operators + DAG topology) is asset-class-blind. Asset-class specificity lives only in slot-bound primitive choices, human-readable naming, and the template's natural-language description — never in topology or operator selection. |
| **WT4** | II | Archetype extension is ADR-gated | Adding a new archetype expands `WORKFLOW_ARCHETYPES`, the closed-family enum. It requires an ADR, a demonstration template landing in the same PR (or explicit prerequisite chain), an updated `known_archetypes()` test, and the archetype-signature taxonomy decision. |
| **WT5** | II | One canonical template per archetype (V1 discipline) | At V1, each registered archetype has at most one canonical template; variants (different aggregators, window sizes, regime classifiers) ship as **sibling templates with the same archetype value**, not as branching logic inside a single template. The catalogue grows by sibling templates, not by template-internal switches. |
| **WT6** | II | Template admission requires reachability + producer-consumer pair | A new template is admitted only when (a) it owns a clear archetype (WT1), (b) every operator / primitive its nodes reference exists in the substrate, (c) every edge's source-output type matches the target-slot's declared type (WT11), and (d) the terminal artifact has a real consumer (UI surface or downstream workflow). |
| **WT7** | III | Topology lock | Node IDs, operator names, edge `source_node_id` / `target_node_id` / `target_input_slot`, and the DAG shape are **literal YAML values** — never slot-substitutable. Only node `params` values, `PrimitiveNodeTemplate.tool_name`, `PrimitiveNodeTemplate.output_field`, and `LiteralBindingTemplate.value` may be `{$slot: name}` references. |
| **WT8** | III | Slot schema is the only caller-visible surface | The declared `slot_schema` is the **complete and exclusive** list of caller-tunable knobs. Anything not in the slot schema is YAML-locked methodology (the template author's choice, not the caller's). Hidden hardcoded methodology that ought to be a slot is forbidden. |
| **WT9** | III | Closed slot type taxonomy | `SlotDeclaration.type` is one of `"str" / "int" / "float" / "bool" / "dict" / "list"`. Adding a seventh type is an ADR-gated closed-family extension. No parallel slot-type system. |
| **WT10** | III | Terminal node + reachable terminal artifact | `terminal_node_id` references a real node in the DAG; the node's output is the workflow's terminal artifact. The terminal artifact's type (a closed-family `ArtifactTypeLiteral`) is derived from the terminal node's operator's `output_type` (or `"Series"` for primitive-terminal templates) and surfaces on the `TemplateCard`. |
| **WT11** | III | Typed edges + closed-family artifact hand-offs | Every edge declares `target_input_slot` matching a real `OperatorSpec.input_slots` entry; the validator confirms the source node's output artifact type is compatible with the target slot's declared type. List-shaped slots (`"List[Series]"`) aggregate multiple edges with the same `(target_node_id, target_input_slot)` pair into a single list at execution time. |
| **WT12** | III | Bind-time loud, runtime loud — no silent fallback | Missing slot, unknown slot, type mismatch, or constraint violation raises `SlotBindingError` at `bind()` *before* a `Workflow` is constructed. Primitive/operator runtime failures raise `WorkflowExecutionError` wrapped with workflow + node context. No silent defaults; no swallowed exceptions; no envelopes. |
| **WT13** | IV | Methodology disclosure via the TemplateCard | Every template surfaces a `TemplateCard` (derived automatically via `card_for_template(template)`) with `archetype`, `description`, `slot_schema`, `terminal_artifact_type`, `primitives_used`, `operators_used`, `node_count`, `edge_count`, `archetype_signature`. The card is the LLM-facing and (eventually) UI-facing methodology surface. Nothing about the template's structure is hidden from the card. |
| **WT14** | IV | Archetype signature for LLM selection | Every user-facing template declares 4–10 `archetype_signature` cues — short structural phrases (≤120 chars each) the LLM matches against user prompts during template selection. The substrate permits empty / single-cue signatures (loader-permissive); the 4–10 range is a review-gate norm. Empty signatures are operationally disqualifying for the user-facing catalogue. |
| **WT15** | IV | Test pattern | Every template ships with: structural-validity tests (template loads, registers, passes validators), slot-binding-rejection tests (missing/unknown/wrong-type/constraint-violation each raise `SlotBindingError`), real-data E2E test with the agent's primitive resolver, **mandatory instrument-agnostic test** (same template runs unchanged against a finance-blind synthetic resolver), and a topology-archetype-fit gate (operators used are appropriate for the declared archetype). |
| **WT16** | IV | Registration discipline — auto-register at import, idempotent | Every template's `<agent>/workflows/<template>/__init__.py` calls `register_template(template)` on module import. Registration is idempotent (re-registering identical content is a no-op); re-registration with different content raises. Tests use `clear_template_registry()` between runs to isolate state. |

---

## Group I — Definitional: is this actually a workflow template?

### WT1 — Archetype ownership

**Rule.** A template owns exactly one workflow archetype — one canonical question shape. The archetype is the template's *identity*, not just a tag: every node, every edge, every slot exists to answer one structurally consistent kind of question (e.g. *"what is the conditional forward move when an event fires?"* → `event_study`; *"how does the relationship between X and Y differ across regimes of Z?"* → `regime_conditioned_relationship`; *"what does this signal-driven trading rule earn?"* → `backtest`).

**Why.** [P3](../../00_thesis/01_non_negotiables.md) (consistency by contract) at the workflow layer + [P8](../../00_thesis/01_non_negotiables.md) (closed-family discipline). The archetype is what makes a template's purpose intelligible — to the LLM (for routing), to the user (for review), to the substrate (for validation), and to the team (for ownership). A template without a clear archetype is a workflow that does *something* but not anything in particular; a template that owns more than one archetype is two templates compressed badly into one.

If you find yourself wanting to add an `if`-branch in a template's logic — "if `mode == 'regime'`, do A; if `mode == 'event_study'`, do B" — you have two templates trying to share a YAML. Split them.

**Verify.**
- The template's `archetype` field is exactly one value from `WORKFLOW_ARCHETYPES`.
- The template's `description` and `archetype_signature` cues are internally consistent with the archetype — they describe one shape of question, not several.
- A reviewer can read `template.yaml` and answer *"what one kind of analysis does this produce?"* in one sentence without using "or".

**Anti-patterns.**
- A template whose `archetype_signature` cues span two archetypes ("backtest a trade" and "do an event study on the same signal").
- A template with a `mode` slot that switches the topology between archetypes ("`mode: backtest`" branches one way; "`mode: event_study`" branches another). Two templates.
- A template called `general_analysis` or `flexible_workflow`. Not an archetype.

**Exceptions.** None at the archetype level. The substrate's open-graph composition path exists for genuinely one-off analyses; templates are for the closed catalogue.

**Relates to.** Analog of [PR1](../primitive/README.md#pr1--concept-ownership-not-instrument-ownership) (primitives own one finance concept) and [OPR1](../operator/README.md#opr1--structural-method-ownership) (operators own one structural method family). Workflow templates own one archetype.

### WT2 — Closed archetype family

**Rule.** The valid set of workflow archetypes is exactly the tuple `WORKFLOW_ARCHETYPES` in [`shared/workflow/template.py`](../../../shared/workflow/template.py). Today (v1):

```python
WORKFLOW_ARCHETYPES: tuple[str, ...] = (
    "event_study",
    "regime_conditioned_relationship",
    "attribution_decomposition",
    "cross_sectional_screen",
    "backtest",
)
```

Templates live under `<agent>/workflows/<template>/` per [P11](../../00_thesis/01_non_negotiables.md) (sibling-isolated agents). The archetype family is **closed and shared**; the template catalogue is **open and agent-scoped** within that family.

**Why.** [P8](../../00_thesis/01_non_negotiables.md) (closed-family discipline) at the archetype level. The archetype set is what the LLM router, the substrate validator, the template card, the catalogue navigator, and the eval / gauntlet machinery all read from. Letting it grow casually breaks each downstream consumer. Letting templates proliferate *within* an existing archetype is healthy (different variants of an event study, different regime classifiers); letting *archetypes* proliferate is what breaks the platform's analysis taxonomy.

Two archetypes (`attribution_decomposition`, `cross_sectional_screen`) are in the enum but unimplemented. Their presence in the enum is a forward declaration: the taxonomy reserves those slots; templates landing in those archetypes inherit the rest of this contract automatically. Removal of an archetype from the enum (vs. addition) is also an ADR-gated closed-family change.

**Verify.**
- Every `template.yaml`'s `archetype` field is a member of `WORKFLOW_ARCHETYPES`.
- A template's `archetype` value is referenced consistently across the YAML (no drift between `template_id` and `archetype`).
- The substrate's `known_archetypes()` returns exactly the closed tuple — never a subset, never a superset.

**Anti-patterns.**
- A template with an `archetype` value not in `WORKFLOW_ARCHETYPES`. Loader rejects at parse time.
- Two templates with the same `template_id` but different content. Registry rejects at registration.
- Bypassing the registry by holding template instances directly in agent code.

**Exceptions.** None.

**Relates to.** [P8](../../00_thesis/01_non_negotiables.md), [P11](../../00_thesis/01_non_negotiables.md). Operator analog is [OPR3](../operator/README.md#opr3--shared-residence) (operators live at `shared/operators/`); template analog: templates live at `<agent>/workflows/`, not `shared/workflows/`, because each template is asset-class-aware in its slot-bound primitive choices even though the substrate it composes is asset-class-blind.

### WT3 — Asset-class-blind operator topology; agent-scoped literal primitives are a documented tradeoff

**Rule.** A template's **operator substrate** — the operator names in the DAG, the edge structure, the artifact types flowing between operators, the validator's type-compatibility checks — is **asset-class-blind**. Asset-class specificity in a template is permitted only in:

1. **Primitive `tool_name` values (literal *or* `{$slot: ...}`).** A `PrimitiveNodeTemplate.tool_name` may be either a `{$slot: ...}` reference (the asset-class-blind path — the *selected* primitive at bind time is asset-class-specific, but the template structure is not) **or** a literal primitive name (the agent-scoped path — the template's analysis genuinely requires a specific primitive of that agent, and the template is by construction agent-scoped). The first path is preferred for templates whose analysis is structurally cross-asset; the second is an accepted tradeoff for templates whose analysis is intrinsically tied to a specific primitive (e.g., `backtest` hardcodes `build_sovereign_yield_panel_tool` and `compute_financing_rate_tool` because the analysis requires a sovereign-yield price panel and a financing-rate panel — these are not abstractions the template can defer to bind-time choice).
2. **Human-readable naming + description.** The template's natural-language description, slot descriptions, and `archetype_signature` cues may use asset-class vocabulary (rates terminology in `rates_agent/workflows/`, FX terminology in a future `fx_agent/workflows/`, etc.). That is desk-readable language, not type-level reasoning.
3. **Folder placement under the agent.** A template at `rates_agent/workflows/event_study/` is rates-scoped by its agent's primitive resolver; a future `fx_agent/workflows/event_study/` would be FX-scoped by the FX resolver. The *operator substrate* is identical; the *resolver context* differs.

Within the YAML, the operator DAG and edge structure must work unchanged for any asset class even when some primitive nodes are literal.

**Why.** [P9](../../00_thesis/01_non_negotiables.md) (asset-class-blind operator substrate) at the workflow layer. Templates are how that substrate becomes a user-facing analysis; if templates branched on asset class at the *operator* layer, the platform's cross-asset claim would collapse at the highest layer.

The distinction between "operator substrate" and "primitive nodes" is load-bearing. Operators are finance-blind by contract (OPR6); their composition is what makes a template's analysis structurally meaningful for any asset class. Primitives are finance-aware by contract (PR1); a template that binds a literal primitive name is an explicit, reviewable choice to scope the template to that primitive's domain — the template's *operator substrate* is still finance-blind, but the *template as a whole* is agent-scoped.

WT15's mandatory instrument-agnostic test verifies the **operator substrate's** asset-class-blindness: the test runs the same template against a synthetic primitive resolver. For templates whose `tool_name` fields are all slot-substituted, the test runs end-to-end with synthetic primitives wired in by name. For templates with literal primitives (e.g. `backtest`), the synthetic resolver patches each literal primitive's data-fetcher so the substrate runs unchanged with synthetic data — the operator chain still proves asset-class-blind even though the primitive selection is fixed.

**Verify.**
- No operator in the DAG branches on asset class (already enforced at the operator layer by OPR6).
- No edge's `target_input_slot` is asset-class-specific (operator slot names are structural, e.g. `series_list`, `events`, `target`, `panel`, never `rates_panel`).
- Every literal `tool_name` in the template is documented in the YAML comments with the rationale for not slot-substituting it (the analysis intrinsically requires this primitive).
- The template's operator substrate is structurally meaningful when re-bound — either via slot substitution OR via fetcher patches on the literal primitives — to produce non-rates synthetic data (the instrument-agnostic test from WT15 proves this).

**Anti-patterns.**
- Hardcoded asset-class concepts in operator names within the template (`operator_name: align_rates_series` — there is no such operator; the right thing is `align_series`).
- A `node_id` that embeds asset-class vocabulary in a way that suggests the substrate cares (`node_id: rates_curve_event` vs the asset-class-blind `node_id: events`).
- A template that imports from another agent's package (`from fx_agent.primitives import ...`). Templates compose primitives via the resolver indirection, never via direct import (P11 violation).
- A literal `tool_name` *without* a rationale comment in the YAML. If the choice not to slot-substitute is deliberate, document it; otherwise convert to `{$slot: ...}`.

**Exceptions.** Asset-class vocabulary is permitted (and expected) in `description`, slot `description` fields, `archetype_signature` cues, and the canonical-V1-binding comment block at the top of `template.yaml`. Those surfaces are for human + LLM understanding, not for substrate dispatch.

**Relates to.** [P9](../../00_thesis/01_non_negotiables.md), [P11](../../00_thesis/01_non_negotiables.md), [OPR6](../operator/README.md#opr6--asset-class--domain-blind-contract), [ART3](../artifact/README.md#art3--asset-class-blind-types). The asset-class-blindness chain runs from operators (OPR6) and artifact types (ART3) up through templates (WT3); a violation at the operator layer breaks the chain. A literal primitive node in a template does not break the chain — it is a documented agent-scoping decision.

---

## Group II — Admission: should this template (or archetype) exist at all?

### WT4 — Archetype extension is ADR-gated

**Rule.** Adding a new archetype expands `WORKFLOW_ARCHETYPES` (the closed-family enum). It requires:

1. **An ADR** in [`../../05_decisions/`](../../05_decisions/) describing the new archetype (the canonical question shape it owns), why no existing archetype covers it, the template-card surface it introduces, and the first template landing in this archetype.
2. **Simultaneous updates** to: the `WorkflowArchetype` `Literal` and the `WORKFLOW_ARCHETYPES` tuple in `shared/workflow/template.py`; the `known_archetypes()` test (the closed enum's parity test); the LLM router's archetype-signature taxonomy (if separate).
3. **A demonstration template** landing in the same PR (or explicit prerequisite chain) — a real `template.yaml` under `<agent>/workflows/<new_archetype>/` that owns the new archetype value (WT1), follows the full template contract (WT7–WT16), and ships with the full test set (WT15). An archetype admitted without a template is forward-declaration debt (cf. `attribution_decomposition`, `cross_sectional_screen` today); admitting a new such forward declaration is permitted but must be deliberate, not accidental.

**Why.** [P8](../../00_thesis/01_non_negotiables.md) is most acute at the archetype layer because every router decision, every catalogue page, every eval gauntlet reads from `WORKFLOW_ARCHETYPES`. A casual addition fragments the taxonomy faster than the LLM can be retrained to route to it. The ADR + simultaneous-landing rule is what keeps the taxonomy disciplined under change.

The discipline here is *much tighter* than the operator's OPR4 promotion rule. A new operator may legitimately ship as a workflow-local helper that gets promoted later. A new archetype may not. The archetype set is the platform's analysis vocabulary; growing it is a strategic decision, not a tactical one.

**Verify.**
- The ADR exists in `05_decisions/` and names the archetype's question shape, the affected sites, and the first template (if any).
- The PR updates the `Literal`, the `WORKFLOW_ARCHETYPES` tuple, the parity test, and either lands a demonstration template or explicitly documents the forward-declaration status.
- The first template (if landing alongside) ships with full test coverage per WT15.

**Anti-patterns.**
- A PR adding an archetype value to the `Literal` without updating the tuple, or vice versa.
- A PR adding an archetype "for a one-off analysis" — that is the open-graph path, not a new archetype.
- A new archetype whose canonical question shape overlaps an existing one ("`event_study_with_regimes`" — the right thing is to add a regime-aware variant template within the existing `event_study` archetype, or to recognize this is actually `regime_conditioned_relationship`).
- A new archetype admitted without a template AND without forward-declaration intent — accumulates dead taxonomy.

**Exceptions.** None.

**Relates to.** [P8](../../00_thesis/01_non_negotiables.md) (closed-family discipline). Analog of [ART4](../artifact/README.md#art4--closed-family-extension-is-adr-gated) for artifact types — archetype extension is the workflow-layer equivalent, with comparable strictness.

### WT5 — One canonical template per archetype (V1 discipline)

**Rule.** At v1, each registered archetype has **at most one canonical template**. Variants — different aggregators, different window sizes, different regime classifiers, different financing methods — ship as **sibling templates with the same archetype value**, each with its own `template_id`, its own `template.yaml`, and its own test suite. Templates do not branch internally on a "mode" or "variant" slot to express alternative analyses.

The catalogue grows by **adding sibling templates**, not by **adding switches inside one template**.

**Why.** [PR4](../primitive/README.md#pr4--parsimony-the-composability-check--llm-tool-selection-clarity) (parsimony + LLM tool-selection clarity) at the workflow layer. The LLM picks templates by matching prompt structure against `archetype_signature` cues (WT14). A template that switches internally between two analyses has signatures that span both; the LLM cannot route cleanly. Sibling templates with disjoint signatures route cleanly.

The "sibling template" pattern also makes versioning + retirement clean: deprecating a variant means removing one template, not surgically excising a code branch.

**V1 sunset.** This discipline is the V1 catalogue's starting position. As the catalogue grows (Phase 2+), multiple templates per archetype will become the norm, and **the rule that survives is that each template still owns one canonical question shape** — i.e. each sibling template has its own crisp archetype-signature footprint, not just its own `template_id`. "One canonical template per archetype" is the v1 simplification; "each template owns one question shape" is the principle that outlives v1.

**Verify.**
- A template's `slot_schema` does not contain a `mode` / `variant` / `analysis_type` slot whose value structurally rewires the analysis.
- The template's `archetype_signature` cues describe one analysis shape (WT1).
- When a contributor proposes "a slot to switch between A and B", a reviewer asks: *"would A and B route to different prompts? if yes, they are two templates."*

**Anti-patterns.**
- A `slot_schema` entry like `aggregator: Literal["mean", "median", "max"]` is **fine** (variant of the same analysis, downstream operator handles the difference cleanly). A `slot_schema` entry like `mode: Literal["event_study", "regime"]` is **not** (two analyses pretending to be one).
- A template whose internal logic uses operator-specific params to switch between fundamentally different structural shapes (e.g., dispatching to `evaluate_trades` in one mode and `event_windows` in another).
- A "framework template" meant to be subclassed or extended at runtime. Templates are leaf catalogue entries, not abstractions.

**Exceptions.** None at v1. The Phase 2+ extension — multiple templates per archetype — is opened by a roadmap ADR, not by a per-template carve-out.

**Relates to.** [PR4](../primitive/README.md#pr4--parsimony-the-composability-check--llm-tool-selection-clarity), [OPR4](../operator/README.md#opr4--parsimony--the-promotion-rule), WT1 (archetype ownership), WT14 (archetype-signature selection).

### WT6 — Template admission requires reachability + producer-consumer pair

**Rule.** A new template is admitted only when:

1. **It owns a clear archetype (WT1).** The `archetype` field is set; the cues, description, and topology are consistent with that archetype.
2. **Every node it references exists in the substrate.** Every operator name in the `nodes` list resolves to an entry in `OPERATOR_REGISTRY` — checked by `validate_workflow(workflow)`. Every primitive tool name (whether literal or slot-substituted at bind time) is reachable via the agent's primitive resolver — checked by `validate_workflow(workflow, primitive_resolver=<agent>_primitive_resolver)`. The loader (`load_workflow_template`) only parses YAML into `WorkflowTemplate`; primitive / operator resolvability is checked at validate time, **after binding**, when the resolver is supplied.
3. **Every edge's source-output type matches the target-slot's declared type (WT11).** The substrate's `validate_workflow()` runs before any execution and rejects type-incompatible compositions.
4. **The terminal artifact has a real consumer.** Either the UI surface renders this template's `terminal_artifact_type`, or a downstream workflow consumes it, or the terminal is a workspace artifact users can inspect. A template whose output is never read is dead catalogue.

**Why.** Templates are catalogue entries. Each one is a commitment that the substrate + agent + UI all support this analysis end-to-end. Admitting a template that doesn't validate, doesn't run, or doesn't render is admitting debt — the catalogue accumulates entries that look real but fail in production.

**Verify.**
- `load_workflow_template(path)` succeeds without warnings.
- `template.bind(canonical_slot_values)` succeeds and produces a `Workflow` that passes `validate_workflow()`.
- The instrument-agnostic E2E test (WT15) executes end-to-end and produces a typed terminal artifact.
- The terminal `artifact_type_literal` appears in the UI's render-supported list (or is documented as a workspace-only artifact).

**Anti-patterns.**
- A template that references a primitive the agent's resolver does not have (`tool_name: calculate_some_future_tool` where no such tool exists).
- A template whose edges produce an artifact type the downstream slot does not accept (e.g., source emits `Panel`, target slot expects `Series`). Validator rejects.
- A template whose `terminal_node_id` references a non-existent node, or a node with no inbound edges (orphan terminal).
- A template registered "for future use" — its consumer hasn't shipped yet, so it's dead catalogue.

**Exceptions.** Templates may legitimately ship slightly ahead of UI render support when the terminal artifact is a workspace-visible type (e.g., `Panel`); the rendering side then follows as a separate PR. That is acceptable iff the template's terminal artifact is at least workspace-inspectable in the meantime — admission of an *invisible* output is still rejected.

**Relates to.** WT1, WT11, WT15. The reachability test is the workflow-layer analog of [ART4](../artifact/README.md#art4--closed-family-extension-is-adr-gated)'s producer-consumer requirement for new artifact types.

---

## Group III — Well-formedness: what makes a particular template valid?

### WT7 — Topology lock

**Rule.** A template's topology is **literal YAML**. The following fields are never `{$slot: ...}` substitutable:

- `template_id`, `archetype`, `description`
- Every `node_id` in `nodes`
- Every `OperatorNodeTemplate.operator_name` (operators are template-author choices, NOT caller choices)
- Every `WorkflowEdge`'s `source_node_id`, `target_node_id`, `target_input_slot`
- `terminal_node_id`
- The structural shape of the DAG (number of nodes, set of edges)

The **only** fields permitted to carry `{$slot: name}` references are:

- `OperatorNodeTemplate.params` dict *values* (not keys)
- `PrimitiveNodeTemplate.tool_name` (entire field; allows instrument-agnostic primitive selection)
- `PrimitiveNodeTemplate.output_field` (entire field)
- `PrimitiveNodeTemplate.params` dict *values*
- `LiteralBindingTemplate.value`

The substitution mechanism is exact-match recursive walk: a value that is literally `{"$slot": "name"}` is replaced by the bound slot's value; any other dict is treated as data and walked into. Dotted paths (`{"$slot": "signal_spec.curve_family"}`) are not supported — nested structures go through `type: dict` slots whose value the primitive's `*Input` validates.

**Why.** Topology lock is what makes a template a *catalogue entry* rather than a *runtime composition*. The LLM (and the user) need to be able to read the template card and know exactly what nodes will execute and in what order. A template whose topology shifts at bind time is unpredictable: the same `template_id` can produce different DAGs on different binds, which means the catalogue page is wrong, the lineage is unstable, and reviewers cannot audit it.

The substitution allowance for primitive `tool_name` is specifically what makes the workflow substrate **asset-class-blind** at the template layer (WT3) — the same template runs on rates, FX, equities by binding different resolver-known primitives. Operator names are not slot-substitutable because operators define the DAG's structural meaning; substituting an operator at bind time would substitute the analysis.

**Verify.**
- A `grep` for `\$slot:` inside the template.yaml's `operator_name`, `node_id`, `source_node_id`, `target_node_id`, `target_input_slot`, `terminal_node_id` fields returns zero matches.
- The substrate loader's recursive-walk substitution is the only mechanism for slot insertion; no template parses YAML differently.
- A bound template's `Workflow.nodes` list has the same length, same `node_id` set, and same operator-or-primitive shape as the source `WorkflowTemplate.nodes` — only `params` / `tool_name` / `output_field` differ.

**Anti-patterns.**
- `operator_name: {$slot: chosen_operator}` — substitutes the analysis, not a parameter. Reject.
- A `node_id` whose value is a slot reference. Topology breaks.
- A second YAML key inside a node entry (`params.extra_node`) intended to dynamically add nodes. The schema rejects via `extra="forbid"`; even if it didn't, this is dynamic topology and the template contract forbids it.
- A `target_input_slot` value computed at runtime. Edges are static.

**Exceptions.** None. The open-graph composition path exists for genuinely dynamic DAGs and is governed separately.

**Relates to.** [P3](../../00_thesis/01_non_negotiables.md) (consistency by contract), WT8 (slot schema as only knob surface).

### WT8 — Slot schema is the only caller-visible surface

**Rule.** The `slot_schema` is the **complete and exclusive** list of caller-tunable knobs the template exposes. Two corollaries follow:

1. **Anything that should be caller-tunable must be a slot.** Hidden hardcoded methodology in node `params` that ought to be a slot is forbidden — if the right window size differs by user prompt, "window size" is a slot, not a constant inside `params`.
2. **Anything not in the slot schema is YAML-locked methodology.** The template author has decided this is *not* a caller knob: it is a methodology choice intrinsic to this template's archetype.

This is the workflow-layer analog of [OPR7](../operator/README.md#opr7--methodology-disclosure-via-configyaml-with-design-locked-constant-allowance) (operators surface methodology via `config.yaml`) and [PR7](../primitive/README.md#pr7--methodology-via-configyaml-with-strict-vendor-scope) (primitives surface methodology via `config.yaml`). The difference is that workflow templates have *two* methodology surfaces:

- **Caller-tunable**: the slot schema. Caller picks values per bind.
- **Author-locked**: every other `params` value in the YAML. Caller cannot influence; the template's authors have committed to this choice as part of the template's identity.

Both are visible — the slot schema appears on the template card; the author-locked YAML is the template itself, available to any reviewer.

**Why.** Templates are catalogue entries; reviewers + reviewers' reviewers need to know what the caller can and cannot influence. Hidden tunables masquerading as constants is the classic source of "the LLM didn't realize it could change X" complaints — and the equally classic source of "the LLM changed X and broke the analysis". Both failure modes go away when the surface is explicit and complete.

**Verify.**
- For each `params` value in `template.yaml` that is *not* a `{$slot: ...}` reference and is *not* a constant the archetype requires: ask *"should the caller be able to override this?"* If yes, it must become a slot. If no, document why in a YAML comment.
- The template card's `slot_schema` (echoed from the `WorkflowTemplate`) lists every caller-tunable knob; nothing else is caller-tunable.
- The PR description for a new template (or an edit to an existing template) lists the slot schema explicitly.

**Anti-patterns.**
- A node with `params: {window_days: 252}` where `window_days` should be a slot. The number is a methodology constant the author chose; if it might change per call, it is a slot, not a constant.
- A slot that the template's logic ignores (declared but never substituted). Lint should reject.
- A constant in `template.yaml` whose value differs between live templates of the same archetype "because we changed our minds". That is a sibling-template situation (WT5), not a fork in the source.

**Exceptions.** Numeric / categorical constants that are intrinsic to the archetype (e.g. the threshold `rule` for `threshold_events` is template-author-locked at `"abs_above"` for a "spike-driven" event study, but could be a slot for a flexible "any-direction" event study — same archetype, different template). The decision lives in the template's authors' hands.

**Relates to.** [OPR7](../operator/README.md#opr7--methodology-disclosure-via-configyaml-with-design-locked-constant-allowance), [PR7](../primitive/README.md#pr7--methodology-via-configyaml-with-strict-vendor-scope), [P5](../../00_thesis/01_non_negotiables.md) (honest disclosure).

### WT9 — Closed slot type taxonomy

**Rule.** `SlotDeclaration.type` is one of:

```python
Literal["str", "int", "float", "bool", "dict", "list"]
```

Six types, full stop. The substrate validates slot values against this type at bind time. Adding a seventh slot type (`tuple`, `set`, `datetime`, `Path`, etc.) is an ADR-gated closed-family extension: the loader, the validator, the card, the lint, the LLM router each branch on the slot-type enum, so a new type touches every consumer.

**Why.** [P8](../../00_thesis/01_non_negotiables.md) at the slot-type layer. The closed taxonomy is what lets the substrate validate bindings uniformly. A parallel type system invented per-template would defeat the slot-binding contract.

The `dict` and `list` types are the catch-alls for compound values: a `SeriesSpec` (compound primitive input) goes in a `type: dict` slot whose value the primitive's `*Input` validates; a universe of curve families goes in a `type: list` slot the cross-sectional-screen template enumerates. Adding `tuple` or `set` is unnecessary at v1 — `list` covers the use case and the primitive's `*Input` enforces the deeper semantics (e.g. ordered vs unordered).

**Verify.**
- Every `slot_schema[].type` value is one of the six closed values.
- The Pydantic schema's `Literal[...]` rejects unknown values at load time.
- No template defines its own slot-type system or wraps the closed taxonomy in custom validation logic.

**Anti-patterns.**
- `type: tuple` or `type: timestamp` in a template's slot schema. Loader rejects.
- A "string with allowed values" slot — that is a `type: str` slot with `valid_values: [...]` (NOT currently in the schema; see open-questions below), or an enum-style check inside the consuming primitive's `*Input`. The slot type stays `str`.
- A custom decoder for a slot value at bind time. The substrate handles types uniformly; per-template decoders are forbidden.

**Exceptions.** None.

**Relates to.** [P8](../../00_thesis/01_non_negotiables.md), [ART12](../artifact/README.md#art12--closed-enums-for-structural-metadata). The slot-type taxonomy is a closed family in the same sense as `TimeSeriesUnits` or `MissingnessPolicy`.

### WT10 — Terminal node + reachable terminal artifact

**Rule.** Every template declares exactly one `terminal_node_id`, a string identifying a real node in the DAG. The terminal node's output artifact is the workflow's terminal artifact — the user-facing deliverable.

The terminal artifact's type (a closed-family `ArtifactTypeLiteral`) is derived automatically by `card_for_template(template)` (see [`shared/workflow/template_card.py`](../../../shared/workflow/template_card.py)):

- If the terminal node is a `PrimitiveNodeTemplate`, the card hardcodes the terminal artifact type as `"Series"`. (The bridge invariant the card is keyed off — primitive-terminal templates have always used the Series bridge in v1; Panel-terminal templates terminate at an operator that emits `Panel`.)
- If the terminal node is an `OperatorNodeTemplate`, the terminal artifact type is the operator's declared `OperatorSpec.output_type`.

The terminal artifact type surfaces on the `TemplateCard` (WT13) so the LLM, the UI, and the catalogue all know what kind of result the template produces.

**Substrate-enforced invariant (loader-time, today):** `Workflow.__init__` checks that `terminal_node_id` matches a `node_id` in the `nodes` list (see [`shared/workflow/types.py`](../../../shared/workflow/types.py)). A typo or dangling reference raises `ValueError` at construction.

**Review-gate invariants (not loader-enforced today; reviewer must check):**

1. **The terminal node has at least one inbound edge** unless it is a `PrimitiveNodeTemplate` (which has no inbound edges by contract). A template with an isolated "future use" operator-typed terminal is a deferred-implementation gap, not a runtime crash — the executor will raise an unrelated error when it gets to that node — but the reviewer catches it first.
2. **The terminal node is reachable from the DAG's roots.** Equivalent to (1) for connected DAGs; named separately because future multi-terminal-candidate templates may make the distinction matter.

The substrate's `validate_workflow()` (see [`shared/workflow/validate.py`](../../../shared/workflow/validate.py)) does check cycle-freeness, operator existence, slot completeness, type compatibility, and primitive resolvability (when a `primitive_resolver` is supplied). It does not currently enforce terminal-reachability; that check is a planned substrate addition tracked in the open-questions list below. Until then, the review gate is the enforcement mechanism.

**Why.** A template without a clear terminal artifact is a workflow that does something but produces nothing. The terminal-node discipline is what makes the workflow's *output contract* explicit: the UI and the LLM both bind against the terminal artifact's type, so any ambiguity here breaks both.

The derivation rule (terminal type from terminal operator's `output_type`) is what keeps templates honest under operator changes — if an operator's `output_type` changes, every template whose terminal is that operator changes too, in lockstep.

**Verify.**
- `terminal_node_id` matches a `node_id` in the `nodes` list (substrate-enforced).
- The terminal node is reachable from at least one root in the DAG (review gate).
- `card_for_template(template).terminal_artifact_type` is a member of `ARTIFACT_TYPE_NAMES` (or `"Series"` for primitive-terminal templates).
- If the terminal is an operator, `card_for_template(template).terminal_artifact_type == OPERATOR_REGISTRY[operator_name].output_type`.
- The terminal artifact type is a member of the executor's `TerminalArtifact` union (`Series`, `SeriesSet`, `EventSet`, `Panel`, `WindowedPanel`). A template whose terminal type is outside that union (e.g. `TradeSet` from a `construct_trades` terminal) will not deserialize into the substrate's `WorkflowResult` and must thread a downstream operator first.

**Anti-patterns.**
- A template with `terminal_node_id: summarize` but no node with `node_id: summarize`. Loader rejects.
- A template whose terminal node is unreachable (an isolated "future use" node attached to nothing). Review gate rejects; runtime fails later if it slips through.
- A template whose terminal operator emits `TradeSet`. `WorkflowResult.terminal_artifact`'s union excludes `TradeSet`; thread an evaluator/summariser downstream.
- A template with two terminal nodes (the schema only allows one; in practice this would be two analyses bundled into one template — see WT1).

**Exceptions.** None.

**Relates to.** [ART2](../artifact/README.md#art2--closed-family-membership) (closed artifact family — the terminal type must be a member), [OPR9](../operator/README.md#opr9--typed-io-no-naked-pandas) (operators declare typed output).

### WT11 — Typed edges + closed-family artifact hand-offs

**Rule.** Every `WorkflowEdge` declares:

```python
WorkflowEdge(
    source_node_id: str,        # the producing node
    target_node_id: str,        # the consuming node
    target_input_slot: str,     # named slot on the target operator's OperatorSpec
)
```

The substrate validator (`validate_workflow()` in [`shared/workflow/validate.py`](../../../shared/workflow/validate.py)) enforces:

1. **`source_node_id` and `target_node_id` exist** in the `nodes` list.
2. **`target_input_slot` matches an entry in `OPERATOR_REGISTRY[target.operator_name].input_slots`** (or is in the operator's `accepts_scalar_input` set if a literal binding is wired to it).
3. **The source node's output artifact type is compatible with the target slot's declared type.** Compatibility is exact for scalar slots (`"Series"` matches `"Series"`); list-shaped slots (`"List[Series]"`) accept multiple edges from `Series`-typed sources, aggregating them into a list at execution time.

List-shaped slots are how the substrate expresses N-ary operator inputs (the canonical example is `align_series(series_list=[a, b, c])` — three edges into one `series_list: "List[Series]"` slot).

**Why.** Type-algebra integrity at the workflow layer. The substrate's static guarantee — that an operator declaring `input_slots={"series_list": "List[Series]"}` actually receives a list of `Series` artifacts — depends on every edge being validated at bind/validate time, before any node runs. A single mismatched edge at the workflow layer can only be detected at runtime (when the operator receives the wrong shape and raises), which makes the failure far from the cause; the validator's pre-flight check moves the failure to the bind site where it is fixable.

The list-aggregation rule (multiple edges to the same `(target_node_id, target_input_slot)` aggregate into a list) is what makes `align_series` expressible declaratively — without it, every list-arity operator would need its own slot-per-input.

**Verify.**
- Every edge's `target_input_slot` is a key in the target operator's `OPERATOR_REGISTRY` entry's `input_slots` dict, OR is in the operator's `accepts_scalar_input` set if a literal binding fills it.
- Every edge's source output type satisfies the target slot's declared type (per `OperatorSpec.input_slots`).
- Multiple edges sharing `(target_node_id, target_input_slot)` only occur where the target slot's type is `"List[<type>]"`.

**Anti-patterns.**
- An edge whose `target_input_slot` is not in `OperatorSpec.input_slots`. Validator rejects.
- An edge that hand-offs an `EventSet` into a slot declared as `Series`. Type-incompatible.
- Two edges into a scalar slot (without `accepts_scalar_input` being set). Cardinality violation.
- A literal binding into a slot that is also fed by an edge. Cardinality violation (the validator allows one source per scalar slot, edge XOR literal).

**Exceptions.** Operators with optional slots (e.g., `evaluate_trades`'s `financing_rate_panel` is optional and conditional on `financing_assumption` param) follow the operator's `arity_validator` hook, not a separate workflow rule.

**Relates to.** [OPR15](../operator/README.md#opr15--explicit-arity-list-typed-slots-are-first-class) (operator arity), [ART2](../artifact/README.md#art2--closed-family-membership) (closed artifact family).

### WT12 — Bind-time loud, runtime loud — no silent fallback

**Rule.** Failure modes raise typed exceptions at the right layer; there is no silent fallback at any layer.

| When | What | Exception | Where raised |
|---|---|---|---|
| At `load_workflow_template(path)` | Malformed YAML, schema validation failure | `WorkflowTemplateError` (subclasses `Exception`) | [`shared/workflow/template_loader.py`](../../../shared/workflow/template_loader.py) |
| At `register_template(template)` | Re-registering different content with the same `template_id` | `TemplateRegistryError` (subclasses `Exception`) | [`shared/workflow/template_registry.py`](../../../shared/workflow/template_registry.py) |
| At `Workflow(...)` construction | `terminal_node_id` not in `nodes` | `ValueError` | [`shared/workflow/types.py`](../../../shared/workflow/types.py) |
| At `template.bind(slot_values)` | Missing required slot | `SlotBindingError` (subclasses `ValueError`) | [`shared/workflow/template.py`](../../../shared/workflow/template.py) |
| At `template.bind(slot_values)` | Unknown slot in `slot_values` | `SlotBindingError` | `WorkflowTemplate.bind` |
| At `template.bind(slot_values)` | Type mismatch (e.g. slot declared `int`, value is `str`) | `SlotBindingError` | `WorkflowTemplate.bind` |
| At `template.bind(slot_values)` | Cross-slot constraint violated (`RelativeOrderConstraint`) | `SlotBindingError` | constraint's `.evaluate(...)` |
| At `validate_workflow(workflow)` | Topology cycle | `WorkflowValidationError` (subclasses `ValueError`) | [`shared/workflow/validate.py`](../../../shared/workflow/validate.py) |
| At `validate_workflow(workflow)` | Unknown operator name | `WorkflowValidationError` | substrate validator |
| At `validate_workflow(workflow)` | Edge target-slot mismatch | `WorkflowValidationError` | substrate validator |
| At `validate_workflow(workflow, primitive_resolver=...)` | Unknown primitive `tool_name` (when resolver supplied) | `WorkflowValidationError` | substrate validator |
| At `execute_workflow(workflow)` | Primitive runtime failure | `WorkflowExecutionError` (subclasses `RuntimeError`; wraps the primitive's exception with node + workflow context) | [`shared/workflow/executor.py`](../../../shared/workflow/executor.py) |
| At `execute_workflow(workflow)` | Operator runtime failure | `WorkflowExecutionError` (wraps the operator's `<Operator>Error`) | `shared/workflow/executor.py` |
| At `execute_workflow(workflow)` | Output not in closed family | `WorkflowExecutionError` | substrate executor |

The exception hierarchy is **not uniform** — different layers raise different base classes:

- **Schema / loader / registry errors** subclass `Exception` directly (`WorkflowTemplateError`, `TemplateRegistryError`). Catch with `except Exception` or by class name.
- **Bind-time and validation errors** subclass `ValueError` (`SlotBindingError`, `WorkflowValidationError`). Catch with `except ValueError` for combined handling or by class name for precision.
- **Runtime executor errors** subclass `RuntimeError` (`WorkflowExecutionError`). Catch by class name; do *not* catch with `except ValueError` (it won't match).

The non-uniformity is intentional: bind-time errors are *programmer / caller errors* (Pythonic `ValueError`), runtime errors are *runtime conditions outside the caller's input* (`RuntimeError`), and loader/registry errors are *substrate-layer concerns* (`Exception`, deliberately broad). None of these is silently swallowed; none is replaced by a default value; none is converted into a `{"error": "..."}` envelope. The caller (supervisor, eval harness, CI) is responsible for deciding how to surface the error; the substrate's job is to raise.

Note: terminal-node *reachability* (no inbound edges, unreachable from roots) is **not** currently raised by the substrate validator — see WT10. That gap is a review-gate norm, not a runtime exception. A reachability-validator addition is tracked in the open-questions list below.

**Why.** [P6](../../00_thesis/01_non_negotiables.md) (no silent failure) at every workflow-layer boundary. A silently-swallowed slot-binding error becomes a workflow that runs on default values and produces an artifact the caller didn't ask for — the LLM thinks the analysis succeeded when it actually used the wrong inputs. A silently-swallowed runtime error becomes a partial DAG result the caller cannot distinguish from a genuine answer. Both modes destroy the platform's trustworthiness; both are categorically refused.

Bind-time errors come first because they are deterministic and cheap. Runtime errors come second because they require the executor to have spent work. Validators in between catch structural problems before execution wastes cycles.

**Verify.**
- Every template's slot-binding-rejection test (WT15) covers each of the four bind-time failure modes.
- The substrate's E2E tests exercise validator rejection (cycle, unknown operator, etc.) and runtime rejection (primitive raises, operator raises).
- No template's `__init__.py` or YAML attempts to catch / suppress these exceptions.

**Anti-patterns.**
- A template's slot with `default: <reasonable_default>` for a slot that should be required. The default masks "caller didn't pass a value" — a silent fallback by another name.
- An agent's wrapper around `template.bind` that catches `SlotBindingError` and substitutes defaults. The supervisor decides UX, but it does so by raising at the LLM layer, not by hiding from the substrate.
- An executor wrapper that retries on `WorkflowExecutionError` without surfacing the cause. Retries belong in the orchestrator with explicit policy, not as silent fallback.

**Exceptions.** None.

**Relates to.** [P6](../../00_thesis/01_non_negotiables.md), [OPR13](../operator/README.md#opr13--honest-refusal-via-typed-error), [PR11](../primitive/README.md#pr11--honest-refusal-raise-or-envelope-strict).

---

## Group IV — Operational

### WT13 — Methodology disclosure via the TemplateCard

**Rule.** Every template surfaces a `TemplateCard` derived automatically by `card_for_template(template)` ([`shared/workflow/template_card.py`](../../../shared/workflow/template_card.py)). The card carries:

| Field | Source | Purpose |
|---|---|---|
| `template_id` | echo of `template.template_id` | catalogue key |
| `archetype` | echo of `template.archetype` | routing taxonomy |
| `description` | echo of `template.description` | human + LLM-readable summary |
| `slot_schema` | echo of `template.slot_schema` | caller-tunable surface (WT8) |
| `terminal_artifact_type` | derived from terminal node's operator's `output_type`, or `"Series"`/`"Panel"` for primitive-terminal templates | UI binding (WT10) |
| `primitives_used` | walked from `nodes`; literal tool names sorted; slot-substitutable primitives recorded as `<via $slot:NAME>` | provenance / capability disclosure |
| `operators_used` | walked from `nodes`; always concrete (operators are topology-locked, WT7) | capability disclosure |
| `node_count`, `edge_count` | derived | diagnostic |
| `archetype_signature` | echo of `template.archetype_signature` | LLM matching cues (WT14) |

The card is the **single uniform surface** every template exposes to the LLM, the UI, and any future MCP / REST catalogue. The substrate guarantees uniform shape across all templates regardless of size, agent, or archetype.

**Why.** [P5](../../00_thesis/01_non_negotiables.md) (honest disclosure) at the catalogue layer. The LLM cannot reason about a template it cannot see; the user cannot trust a template whose methodology is hidden; the team cannot audit a template whose `primitives_used` they don't know. The card is the rendered, machine-readable methodology surface that every consumer reads.

The "always concrete operators, slot-substitutable primitives" asymmetry is deliberate: operators define the analysis (WT7 topology lock — operators are template-author choices); primitives are the asset-class-specific compute the template uses (WT3 asset-class-blindness — primitives are slot-bound choices the LLM picks per asset class).

**Verify.**
- The card's `primitives_used` for a slot-substitutable primitive contains the `<via $slot:NAME>` marker, not a guessed primitive name.
- The card's `operators_used` for every operator in the DAG is a literal operator name (no `<via $slot:...>`).
- The card's `terminal_artifact_type` is a member of `ARTIFACT_TYPE_NAMES`.
- Round-trip: `card_for_template(template)` produces the same card on two identical loads (no nondeterminism).

**Anti-patterns.**
- A template that defines its own `card` shape outside `TemplateCard`. The substrate's card is the only valid shape.
- A template that omits `archetype_signature` cues and ships them in `description` instead. The card splits them deliberately so the LLM router can use the cues for matching.
- A template-specific override of `card_for_template`. Cards are derived uniformly.

**Exceptions.** None.

**Relates to.** [P5](../../00_thesis/01_non_negotiables.md), WT14 (archetype signatures), [PR8](../primitive/README.md#pr8--single-central-methodology-surface-the-central-knob) (primitive cards have an analogous role).

### WT14 — Archetype signature for LLM selection

**Rule.** Every template declares an `archetype_signature` — a list of short cues (≤120 chars each, non-empty strings) the LLM router matches against user prompts during template selection.

**Substrate-enforced (today):** Cues that are present must be non-empty strings of ≤120 chars (validated by `SlotDeclaration`-style Pydantic checks on the template schema). The substrate **permits** an empty `archetype_signature` list (`default_factory=list` on the field) and **permits** a single-cue signature — substrate tests in [`tests/test_workflow_template_system.py`](../../../tests/test_workflow_template_system.py) explicitly cover the "default empty signature is legal" and "single cue round-trips" cases.

**Review-gate norm (this contract, enforced at PR review):** every template that ships user-facing declares **4–10 cues**. Below four is insufficient coverage of natural-language variation desk analysts use; above ten dilutes match precision. The 4–10 range is a review-time discipline imposed by this contract on top of the substrate's permissive schema, not a loader-enforced rule.

Cues are **author-defined natural-language patterns in desk vocabulary** — phrases a desk analyst would actually use when describing the analysis. They are *not*:

- Free-form prose paragraphs (cues are scannable, not skimmable).
- Marketing copy ("our flagship analysis").
- Schema-style declarations ("input: time series; output: number").

An empty cue list is **operationally disqualifying** at the user-facing surface: the template is unselectable by the LLM router because there are no cues to match. Templates with empty signatures that still load are useful for testing or substrate-only execution but are not eligible for the user-facing catalogue (WT16).

**Why.** Templates are catalogue entries the LLM picks from. The matching mechanism is signature-string proximity (not embedding similarity, not exact match) — so the cues need to *cover the natural-language space of the analysis*. Four cues capture roughly the variation desk analysts use in framing a question; ten cues exhaustively cover unusual phrasings without diluting the match. The 120-char limit keeps each cue scannable in catalogue tooling and short enough to be a structural phrase, not a sentence.

The cues live on the template (not in a central router config) so the router stays uniform across all templates and so deprecating / editing a template's cues is a local change.

**Verify.**
- Every template's `archetype_signature` is a list of non-empty strings, each ≤120 chars (substrate-enforced).
- A user-facing template's signature has 4–10 cues (review gate; not loader-enforced).
- The cues are in desk vocabulary (rates vocabulary for rates_agent templates, etc.) — readable by an analyst, not just by the substrate.
- The cues disambiguate this template from any other sibling templates within the same archetype (WT5).

**Anti-patterns.**
- A template with one cue. Insufficient coverage; the router will miss valid prompts.
- A template whose cues overlap with another template's cues in the same archetype. The router will route ambiguously.
- A template whose cues are repeats of `description` rather than structural phrases the LLM matches.
- A cue >120 chars. Loader rejects.

**Exceptions.** None at v1. The Phase 2+ alternative (embedding-based template selection) may replace signature matching, in which case this rule's enforcement mechanism changes — but the discipline "the template is responsible for declaring how it expects to be selected" survives.

**Relates to.** [P5](../../00_thesis/01_non_negotiables.md), WT13 (the card surfaces these cues).

### WT15 — Test pattern

**Rule.** Every template ships with **five** test layers. The first four mirror the operator / artifact patterns; the fifth (instrument-agnostic) is workflow-specific and load-bearing.

1. **Structural validity** — the template loads (`load_workflow_template(path)` succeeds), registers (`register()` is idempotent), and the schema validators all pass. Node + edge counts match the documented shape. The `archetype_signature` cues meet WT14's constraints (count, length, non-empty).
2. **Slot-binding rejection** — for each of the four bind-time failure modes from WT12 (missing required slot, unknown slot, type mismatch, cross-slot constraint violation), one test that asserts `SlotBindingError` is raised with a specific message.
3. **Real-data E2E** — the template, bound with canonical V1 inputs, executed via the agent's real primitive resolver against a real (or near-real synthetic) database, produces a typed terminal artifact whose lineage spans every node in the DAG.
4. **Mandatory instrument-agnostic test** — the same template, executed unchanged against a finance-blind synthetic data source, produces a structurally correct terminal artifact. The pattern depends on whether the template's `tool_name` fields are slot-substituted or literal (see WT3). For slot-substituted templates, build a local `PrimitiveResolver` from synthetic `PrimitiveSpec` entries (the pattern in [`tests/test_workflow_event_study.py`](../../../tests/test_workflow_event_study.py)'s `synthetic_resolver` fixture). For literal-primitive templates, use the agent's real resolver with [`tests/_workflow_synthetic_fetchers.py`](../../../tests/_workflow_synthetic_fetchers.py)'s `patch_all_synthetic_fetchers()` context — the file provides DB-fetcher patches, not a resolver. Either way, the test proves WT3 (asset-class-blindness): the template's **operator substrate** (operators + DAG topology + edge structure) runs on any indexed numeric data, not just rates.
5. **Topology-archetype-fit gate** — assertions that the template uses only operators that belong to its archetype's structural family. For example, an `event_study` template uses `event_windows` and `conditional_aggregate`; a `regime_conditioned_relationship` template uses `apply_mask` and `rolling_regression`; a `backtest` template uses `construct_trades` and `evaluate_trades`. This gate is what prevents a template from silently drifting into a wrong-archetype shape over time as operators are added.

The card-content test (WT13) is often folded into structural validity (it verifies `card_for_template(template)` produces expected `primitives_used` markers, etc.). It can also live as a separate test if the template's card surface is large.

**Why.** Each layer catches a different class of bug. Structural validity catches loader / schema regressions. Slot-binding rejection catches bind-time silent-fallback regressions (WT12). Real-data E2E catches integration regressions across the operator + primitive substrate. The instrument-agnostic test specifically catches asset-class-overfitting regressions — without it, future contributors will start binding rates-specific assumptions into operator params, and the substrate's asset-class-blindness claim quietly breaks. The topology-archetype-fit gate catches *archetype drift* — a template that started as an event_study and slowly turned into a regime study via incremental edits is the failure mode this gate forecloses.

**Verify.**
- A `tests/test_workflow_<template_id>.py` file exists; it covers all five layers.
- The instrument-agnostic test is **not optional** — its presence is what the WT3-WT15 chain rests on. A template's PR review checklist (in [`runbook.md`](runbook.md)) is rejected without it.
- The topology-archetype-fit gate names the expected operator set explicitly (allow-list) rather than denying specific operators (deny-list).

**Anti-patterns.**
- A template that ships with E2E coverage only (no slot-rejection layer). Silent-fallback regressions go unnoticed.
- A template whose instrument-agnostic test uses an agent-specific resolver instead of `_workflow_synthetic_fetchers`. The asset-class claim is not actually tested.
- A topology-archetype-fit gate that uses a deny-list ("does not include `apply_mask`"). Deny-lists drift; allow-lists encode the archetype's structural identity explicitly.
- A template whose tests are inside `<agent>/workflows/<template>/test_*.py` instead of `tests/test_workflow_<template>.py`. The platform's test discovery convention puts workflow tests at the top of the test tree.

**Exceptions.** None.

**Relates to.** [PR16](../primitive/README.md#pr16--test-triplet), [OPR16](../operator/README.md#opr16--test-pattern), [ART13](../artifact/README.md#art13--test-pattern), WT3, WT12, WT14.

### WT16 — Registration discipline — auto-register at import, idempotent

**Rule.** Every template's `<agent>/workflows/<template>/__init__.py` calls `register_template(template)` on module import. The call pattern is:

```python
# rates_agent/workflows/<template>/__init__.py

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
    """Load + return the <template> template.  Cached by absolute
    path via shared.workflow.load_workflow_template."""
    return load_workflow_template(<TEMPLATE>_TEMPLATE_PATH)


def register() -> None:
    """Register the <template> template with the substrate's
    process-wide registry.  Idempotent."""
    template = load_<template>_template()
    register_template(template)


register()
```

Registration is **idempotent**: re-registering an identical `WorkflowTemplate` (same `template_id` + same content) is a no-op. **Re-registering different content with the same `template_id` raises** `TemplateRegistryError` — the registry refuses to silently overwrite, because two templates with the same ID would be a catalogue collision.

Tests use `clear_template_registry()` between runs to isolate state (`tests/conftest.py` typically wires this).

**Which templates get into the user-facing catalogue is a separate decision from registration.** A template's `__init__.py` registers it with the substrate (so the executor can dispatch it, so tests can exercise it), but whether the LLM router, the chat session, the CLI, the REST catalogue, and the LLM router's desk-phrasing taxonomy *see* it is controlled by which user-facing entry-point modules import its package. **Five entry points** are load-bearing today:

- [`rates_agent/workflows/mcp_server.py`](../../../rates_agent/workflows/mcp_server.py) — the MCP server that exposes templates to the LLM via stdio. Explicit imports + a matching `@mcp.tool()` wrapper per template.
- [`api/routes/workflows/catalogue.py`](../../../api/routes/workflows/catalogue.py) — the REST catalogue endpoint (`GET /workflows`). Explicit imports.
- [`orchestrator/session.py`](../../../orchestrator/session.py) — the chat session's `WorkflowRouter` bootstrap. Without this import the per-session router cannot see the template.
- [`rates_agent/workflows/cli.py`](../../../rates_agent/workflows/cli.py) — the CLI entry point. Without this import the CLI cannot dispatch the template.
- [`orchestrator/workflow_prompts.py`](../../../orchestrator/workflow_prompts.py) — the LLM router's `CANONICAL DESK PHRASING THE LLM SHOULD RECOGNIZE` taxonomy. Without an entry here the router's prompt does not teach the LLM the desk-cue → template-id mapping for the new template; the substrate's `archetype_signature` cues alone are not always sufficient for routing.

All five surfaces today import `event_study`, `regime_conditioned_relationship`, and `cross_sectional_screen`; `backtest` is intentionally excluded from each of them (the `mcp_server.py` comment block explains why — the V1 backtest is a yield-change distribution missing the data prerequisites for true economic P&L). The agent's `workflows/__init__.py` is **not** an auto-import barrel; it does not enumerate every template.

A template that ships without being imported by any of the five surfaces is still tested + executable directly (the tests in `tests/test_workflow_<template>.py` import the package directly to register it), but is invisible to whichever surface was missed. That gap between "in repo + tested" and "registered in user-facing catalogue" is a deliberate gating mechanism, not an oversight — and the runbook's [Step 5](runbook.md#step-5--wire-the-template-into-the-user-facing-surfaces) is the procedural source of truth for the five-site wiring.

The v1.1 README + runbook named only the first two surfaces (`mcp_server.py` + `catalogue.py`). That was incomplete — three additional surfaces had the same explicit-import requirement and were not enumerated. The omission surfaced during the Stage-4 Round-3 review of PR #201 (Codex F3): `cross_sectional_screen` shipped registered in two of the five surfaces and invisible in the other three. The v1.2 enumeration above is canonical going forward.

**Why.** Templates are catalogue entries; the catalogue is populated at import time. Each template's self-registration (the `__init__.py::register()` pattern) is what guarantees uniformity at the substrate level — every template registers the same way, idempotently. The user-facing surfaces' explicit imports are what give the catalogue's owners a control surface for staging templates (in repo, tested, but not yet user-visible).

Idempotency is what makes this safe under repeated imports (which happen in tests, in REPL workflows, in hot-reload-style development). The "same ID, different content rejects" rule is what prevents silent catalogue corruption when two PRs both edit the same template_id without coordination.

**Verify.**
- Every template's `__init__.py` follows the four-symbol pattern (`<TEMPLATE>_TEMPLATE_PATH`, `load_<template>_template()`, `register()`, top-level `register()` call).
- `register_template(same_template_twice)` is a no-op.
- `register_template(different_template_same_id)` raises `TemplateRegistryError`.
- If the template should be user-facing, the PR adds an import in **all five** surfaces: `rates_agent/workflows/mcp_server.py` (MCP/LLM routing + `@mcp.tool()` wrapper), `api/routes/workflows/catalogue.py` (REST exposure), `orchestrator/session.py` (chat session WorkflowRouter), `rates_agent/workflows/cli.py` (CLI dispatch), and `orchestrator/workflow_prompts.py` (LLM router desk-phrasing → template_id mapping). If the template is intentionally not user-facing (e.g. paused like `backtest`), the rationale is documented in a comment at the relevant entry-point.

**Anti-patterns.**
- A template registered manually from the agent's `__init__.py` instead of self-registering. Couples the agent to the template detail.
- A template registered lazily (only on first call to `get_template`). The catalogue is incomplete at agent-boot; the LLM router can't see the template.
- A template registered via a side-effect of importing `template.yaml` directly. The YAML is data; only the `__init__.py` is allowed to call `register_template`.
- A template marked user-facing in the PR description but not actually imported by all five user-facing surfaces. The user-facing claim is unverified — and partial wiring is worse than none because the failure modes differ per missing surface (works via MCP but not via chat session; visible in REST catalogue but never picked by the LLM; etc.).
- A template imported by some user-facing surfaces but not others, without a documented rationale for the asymmetry. The catalogue's curation is a documented decision, not a default; partial wiring is always intentional or it is a bug.

**Exceptions.** None.

**Relates to.** [P3](../../00_thesis/01_non_negotiables.md) (consistency by contract), [P11](../../00_thesis/01_non_negotiables.md) (sibling-isolated agents — registration is the only cross-package coupling, and it is uniform across all agents).

---

## The current catalogue

For reference, the templates that exist today, organised by archetype. **"In repo + tested"** means the template folder exists, registers with the substrate when its package is imported, and ships with the WT15 test suite. **"In user-facing catalogue"** means **all five** user-facing surfaces (MCP, REST catalogue, chat-session WorkflowRouter, CLI, LLM-router desk-phrasing taxonomy — see WT16) explicitly import the package — making it routable by the LLM, visible to API callers, and dispatchable from the CLI. The two columns can differ deliberately (cf. WT16).

| Archetype | Template | Terminal artifact type | In repo + tested | In user-facing catalogue | Notes |
|---|---|---|---|---|---|
| `event_study` | `event_study` (`rates_agent/workflows/event_study/`) | `Series` | ✅ E2E + instrument-agnostic + topology-fit | ✅ MCP + REST | live |
| `regime_conditioned_relationship` | `regime_conditioned_relationship` (`rates_agent/workflows/regime_conditioned_relationship/`) | `Series` | ✅ E2E + instrument-agnostic + topology-fit + cross-slot constraint | ✅ MCP + REST | live |
| `backtest` | `backtest` (`rates_agent/workflows/backtest/`) | `Panel` (summary metrics) | ✅ E2E + parity + synthetic | ❌ paused | V1 backtest is a yield-change distribution; missing data prerequisites for true economic P&L (MOD_DUR_MID, CPI-U NSA + seasonal factors, OTR history, true O/N OIS, bid/ask). Rationale + re-enable instructions in `mcp_server.py`'s comment block. |
| `attribution_decomposition` | — | — | — | — | Archetype reserved; no template (WT2 forward declaration) |
| `cross_sectional_screen` | — | — | — | — | Archetype reserved; no template (WT2 forward declaration) |

**Three templates in repo (two user-facing), five archetypes, one agent.** The catalogue is deliberately small at v1; the principles in this document govern the catalogue *as it grows*, which is the rest of this year's roadmap. The "in repo / in catalogue" split is itself the discipline — a paused template stays exercised by tests while the data prerequisites land, without leaking an incomplete analysis to the LLM router.

## Non-standard templates (coming soon)

Some templates may legitimately not satisfy every well-formedness rule — e.g. research-only templates exercising a Phase-2+ feature behind a flag, "shadow" templates running in parallel to a production template for A/B comparison, exploration templates the LLM cannot route to. A non-standard template category, its admission tests, and its registry-isolation are a planned extension. **Until that section opens, every template that ships is held to WT7–WT16.**

## Anti-patterns (catalogue-wide)

Auto-reject in review:

- **A template under `shared/workflows/` instead of `<agent>/workflows/`.** WT2 + [P11](../../00_thesis/01_non_negotiables.md) violation. Templates live next to the agent that owns them; the substrate (`shared/workflow/`) is finance-blind.
- **A `template.yaml` with `archetype` not in `WORKFLOW_ARCHETYPES`.** WT2 violation; loader rejects.
- **A template whose topology has `{$slot: ...}` in `node_id`, `operator_name`, `source_node_id`, `target_node_id`, `target_input_slot`, or `terminal_node_id`.** WT7 violation; substitutes the analysis.
- **A template with a hardcoded methodology constant in `params` that should be a slot.** WT8 violation; the caller should be able to override.
- **A template with no `archetype_signature` cues.** WT14 operational violation; the template is unselectable.
- **A template registered manually outside `__init__.py::register()`.** WT16 violation.
- **A template whose tests omit the instrument-agnostic layer.** WT15 violation; the asset-class claim is not actually tested.
- **A template that switches between two archetypes via a `mode` slot.** WT1 + WT5 violation; two templates packed badly into one.
- **A template with two terminal nodes.** Schema rejects; conceptually two analyses bundled.
- **A template that imports from another agent's package.** WT3 + [P11](../../00_thesis/01_non_negotiables.md) violation; templates compose via slot-bound primitives and the resolver indirection, never via cross-agent imports.

## Open questions and known gaps

1. **Slot `valid_values` / `valid_range` constraints.** The current `SlotDeclaration` schema accepts `name`, `type`, `required`, `description`, `default`. Templates today encode "allowed values for an enum-shaped slot" inside the consuming primitive's `*Input` Pydantic schema (the bind-time check still raises, just from inside the primitive). A future extension that adds `valid_values: List[Any]` and `valid_range: (min, max)` to `SlotDeclaration` would move that check upstream to bind time — making the failure earlier and the slot's surface richer on the template card. This is a planned closed-family extension (WT9 namespace).
2. **Cross-slot constraint kinds.** Today's only `SlotConstraint` variant is `RelativeOrderConstraint`. The substrate's discriminated-union design reserves `mutual_exclusion`, `conditional_required`, `set_membership`, `regex_match`; each is an ADR-gated addition when a template needs it.
3. **Multiple templates per archetype.** V1 ships at most one template per archetype (WT5 simplification). The roadmap admits multiple templates per archetype starting Phase 2; the contract change is small — `WT5` softens to "each template owns one canonical question shape" and the catalogue's archetype landing pages become indexes rather than single-template targets.
4. **Open-graph composition vs templates.** The substrate supports both closed templates (this contract) and open-graph LLM composition (separate execution mode). The boundary between the two — when a user prompt routes to a template vs to open composition — is a router-level decision documented separately. Within this contract, open-graph workflows are explicitly *not* templates.
5. **Per-template card layout (`default_card_layout`).** Some templates may want to specify how their terminal artifact should be rendered (sparkline + table, two-panel comparison, etc.). A `default_card_layout` field on `WorkflowTemplate` is a planned extension; until then, the UI binds against `terminal_artifact_type` and chooses a default layout per type.
6. **Template versioning + migration.** Templates today have no `version` field separate from `template_id`. The roadmap considers adding `version` so a v2 of an existing template can ship as `template_id="<id>", version="2.0.0"` while v1 stays callable for replay. The contract change is small but affects lineage stability — replay should always reproduce the version that originally executed.

## Changing a workflow-template principle

Same discipline as the primitive / operator / artifact principles:

1. Open an ADR in [`../../05_decisions/`](../../05_decisions/) describing the proposed change.
2. Land the ADR and the contract change in the same PR.
3. Bump the version of this file.

Workflow-template-principle changes are higher-stakes than per-template changes because they affect every downstream template, the LLM router, and the catalogue UI simultaneously. Expect multiple reviewers, including a substrate owner.

## Citation cheat sheet

| Use | Pattern |
|---|---|
| In a commit message | `feat(workflows): add cross_sectional_screen template per WT4` |
| In a PR review comment | `This violates WT7 — the template substitutes operator_name from a slot.` |
| In a code comment (rare) | `# WT3: substrate is asset-class-blind; do not bind rates-specific assumptions here.` |
| In a runbook step | `Step 3 — declare the slot schema per WT8 (every caller knob must be a slot).` |
| In an ADR | `This decision extends WORKFLOW_ARCHETYPES per WT4 to admit the <archetype_name> archetype.` |

## Version log

| Version | Date | Change | ADR |
|---|---|---|---|
| v1.2 | 2026-05-25 | **WT16 wiring widened from two to five user-facing surfaces.** The v1.1 README + runbook claimed user-facing visibility was controlled by exactly two entry points (`rates_agent/workflows/mcp_server.py` + `api/routes/workflows/catalogue.py`). In practice three additional surfaces have the same explicit-import requirement: `orchestrator/session.py`'s chat-session WorkflowRouter bootstrap; `rates_agent/workflows/cli.py`'s CLI dispatch; `orchestrator/workflow_prompts.py`'s `CANONICAL DESK PHRASING THE LLM SHOULD RECOGNIZE` taxonomy.  The gap surfaced during the Stage-4 Round-3 review of PR #201 (Codex F3): the in-progress `cross_sectional_screen` template shipped registered in 5a + 5b only and was invisible to chat / CLI / LLM-router-prompt.  PR-A8's fix commit added the three missing imports + the desk-phrasing cues block; this contract revision pins the five-surface enumeration so future templates do not repeat the omission.  See the runbook's [Step 5](runbook.md#step-5--wire-the-template-into-the-user-facing-surfaces) for the per-surface mechanics and code-pattern examples.  WT16's Verify + Anti-patterns bullets rewritten accordingly.  The current-catalogue table is intentionally NOT updated in this revision — it reflects the `build`-tracked state and will pick up `cross_sectional_screen` automatically when PR-A8 lands. | (pending) |
| v1.1 | 2026-05-18 | Pre-canonical corrections after a factual-review pass against the live workflow substrate: (a) **WT3 softened** — distinguished "asset-class-blind operator topology" from "agent-scoped literal primitive nodes"; literal `PrimitiveNodeTemplate.tool_name` values are an accepted tradeoff for templates whose analysis is intrinsically tied to specific primitives (e.g. `backtest` hardcodes `build_sovereign_yield_panel_tool` + `compute_financing_rate_tool`); the operator substrate is still asset-class-blind; new anti-pattern requires rationale comments in YAML for literal `tool_name`. (b) **Universal-contract paragraph** — removed the incorrect "executor persists intermediate artifacts" claim; the executor holds `node_artifacts` in-memory and returns them in `WorkflowResult`; persistence (if any) is a higher-layer concern. (c) **Terminal artifact union** (WT10 + "What this is not") — removed `TradeSet` from the terminal-type discussion (`WorkflowResult.TerminalArtifact` excludes it); a template ending at `construct_trades` must thread a downstream operator first. Corrected the primitive-terminal type derivation from "Series or Panel" to "hardcoded Series" per `template_card.py`. (d) **WT10 reachability invariants** — split into substrate-enforced ("`terminal_node_id` in `node_ids`", checked by `Workflow.__init__`) and review-gate (inbound edge + root-reachability, NOT currently checked by `validate_workflow()`); added an open-questions entry for the validator addition. (e) **WT12 exception hierarchy** — corrected the wrong claim that "all four exception classes subclass `ValueError`". Real hierarchy: `WorkflowTemplateError` / `TemplateRegistryError` subclass `Exception`; `SlotBindingError` / `WorkflowValidationError` subclass `ValueError`; `WorkflowExecutionError` subclasses `RuntimeError`. New table also covers loader/registry errors and clarifies that terminal-reachability is not a runtime exception today. (f) **WT14 cue-count** — separated substrate-enforced (≤120 chars, non-empty strings; empty list + single cue are explicitly permitted by `tests/test_workflow_template_system.py`) from review-gate (4–10 cues for user-facing templates). The 4–10 range is this contract's discipline, not a loader rule. (g) **WT16 registration discipline** — rewritten to match repo reality: the agent's `workflows/__init__.py` is NOT an auto-import barrel; user-facing visibility is controlled by explicit imports in `rates_agent/workflows/mcp_server.py` and `api/routes/workflows/catalogue.py`; "in repo + tested" and "in user-facing catalogue" are deliberately separable states. (h) **Admission criterion 2 (WT6)** — primitive resolvability is checked at `validate_workflow(..., primitive_resolver=...)` time, NOT at loader time (the loader only parses YAML). (i) **Current catalogue table** — split into two visibility columns ("in repo + tested" vs "in user-facing catalogue"); marked `backtest` as paused with the `mcp_server.py` rationale (V1 backtest is a yield-change distribution missing data prerequisites for true economic P&L). | (pending) |
| v1 | 2026-05-17 | Initial workflow-template contract. Sixteen principles (WT1–WT16) organised in four groups: definitional (WT1–WT3 — archetype ownership, closed archetype family, asset-class-blind substrate), admission (WT4–WT6 — ADR-gated archetype extension, V1 one-template-per-archetype discipline, reachability + producer-consumer pair), well-formedness (WT7–WT12), operational (WT13–WT16). Superseded by v1.1 the next day after a factual-review pass against the live substrate. | — |
