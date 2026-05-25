# ADR 0013 — `cross_sectional_screen` output shape: `SeriesSet` (Option A); `RankedResult` rejected for V1

**Status:** Accepted
**Date:** 2026-05-25
**Builds on:** ADR 0006 (inflation-domain agents — the closed-family-extension precedent the rejected Option B would have invoked), ADR 0012 (event-primitive placement — the most recent ADR; sets the format precedent and the citation pattern for "stay inside the existing closed family unless forced out"). Inherits the workflow-template contract codified in [`02_components/workflow_template/README.md`](../02_components/workflow_template/README.md) (WT1–WT16) and the artifact contract in [`02_components/artifact/README.md`](../02_components/artifact/README.md) (ART1–ART16).
**Operationalises principles:** P8 (closed-family discipline — `ARTIFACT_TYPE_NAMES` stays at six members; no `RankedResult` admission), P9 (asset-class-blind operator substrate — the screen's DAG runs unchanged on rates / FX / equity inputs via slot-substituted primitives), P10 (single source of truth — pins the output-shape decision so future cross-sectional templates do not re-litigate it), P11 (sibling-isolated agents — template lives under `rates_agent/workflows/`, never `shared/workflows/`), WT1 (archetype ownership — `cross_sectional_screen` was a reserved forward declaration since V1; this ADR is the admission of its first template), WT3 (asset-class-blind substrate — every member primitive is slot-substituted), WT5 (V1 one canonical template per archetype — sibling templates ship later as the universe grows beyond 4 members or the metric path grows beyond "snapshot at sentinel date"), ART2 (closed-family membership — `RankedResult` is explicitly not in `ARTIFACT_TYPE_NAMES`), ART5 (new shape, not new use case — the platform's published anti-pattern names `RankedResult` directly: *"that's `SeriesSet` with the rank values as the payload, until `RankedResult` is admitted"*).
**Scope:** Records the verdict on Phase 2.5's two-option question (`SeriesSet`-carried Panel screen vs new `RankedResult` artifact + `rank` operator) and admits the first `cross_sectional_screen` template. **No closed-family extension; no new operator; no new artifact type.** The first template is fixed-arity (N=4 members) per the operator-substrate constraints documented below.

---

## Context

`cross_sectional_screen` has been a reserved forward declaration in `WORKFLOW_ARCHETYPES` since V1 (`shared/workflow/template.py` line 85). The archetype's canonical question shape is: *"Across universe U, which instruments rank highest on metric M as of date D?"* — verbatim from the workflow-template runbook's archetype table.

Phase 2.5 of the primitive-expansion plan ([`tmp/primitive_expansion/phase2_5.md`](../../tmp/primitive_expansion/phase2_5.md)) posed the architectural question this ADR answers: does the existing closed family of artifact types carry a cross-sectional screen, or does it require a new `RankedResult` artifact + `rank` operator?

Two options are framed in the plan:

- **Option A.** Use existing artifacts (`Panel`, `Series` at sentinel date, `SeriesSet`) to carry the screen result. No closed-family extension. Lower architectural cost.
- **Option B.** Add a `RankedResult` artifact + a `rank` operator to the closed family. Cleaner output shape for ranked results, but requires a P8 closed-family ADR + artifact addition + bridge extension + executor union update + UI render support.

The plan's bottom line: *"Default to the cheapest architectural path; extend the closed family only if Panel-only verifiably can't carry the workflow."*

### What the operator substrate actually allows today

Three operator-substrate facts shape the verdict:

1. **No `rank` operator exists.** `OPERATOR_REGISTRY` (`shared/workflow/registry.py`) lists 12 operators: `align_series`, `select_from_series_set`, `series_arithmetic`, `threshold_events`, `event_windows`, `conditional_aggregate`, `apply_mask`, `rolling_regression`, `summarize_series`, `construct_trades`, `evaluate_trades`, `summarize_trades`. None of them produces a ranked output. Adding one would be an open-catalogue addition (`OPERATOR_REGISTRY` is not a closed family per [`03_standards/closed_family_discipline.md`](../03_standards/closed_family_discipline.md) §2) but still requires the operator to pass the OPR1–OPR16 admission gate — non-trivial scope and not in Stage 4.
2. **No per-column `Panel` transform operator exists.** `series_arithmetic` operates on `Series`, not `Panel`; `summarize_series` operates on `Series`, not `Panel`. The plan's literal topology (*"panel primitive → `series_arithmetic` to compute the cross-sectional metric per row → `zscore_custom` → some form of 'rank by z' → terminal artifact"*) is not buildable today: `series_arithmetic` cannot reduce a `Panel` cross-sectionally because the operator's contract is two-Series binary arithmetic, and `zscore_custom` is a primitive that operates on a single curve_family+tenor — not a `Panel`-shaped operator.
3. **Templates have fixed topology (WT7).** There is no fan-out / map-over-universe mechanism in V1 — the DAG node count and edge count are template-author-locked. A universe-of-N screen must hardcode N into the YAML or rely on a single primitive whose `*Input` carries the universe (e.g. `sovereign_yield_panel`'s `legs` list, max 20).

These three constraints together mean Option A's *cheapest* expression — a few primitive nodes, existing operators, no new closed-family member — is structurally bounded. It can express either:

- **A pure-Panel terminal** — single `sovereign_yield_panel`-style primitive whose `legs` slot carries the universe. Terminal artifact type: `Panel`. No metric / z-score / rank step inside the template. The "screen" is the Panel itself; the desk reads the recent rows and reasons visually.
- **A fixed-arity SeriesSet** — N parallel primitive branches (slot-substituted `tool_name` per WT3), each producing a `Series`; per-branch `summarize_series` at the sentinel date; terminal `align_series` emitting a `SeriesSet` keyed by caller-supplied member labels. The "screen" is the per-member summary at the sentinel.

Neither expresses *ranking* as a substrate artifact. Both expose the cross-sectional comparison surface (the row of values, the keyed map of values) and leave the *interpretation* (which member is most stretched? least?) to the desk reading the terminal artifact. Option B is what closes that interpretive gap structurally — at the cost of a P8 closed-family extension.

### The ART5 precedent

`02_components/artifact/README.md` ART5 (*"new shape, not new use case"*) lists `RankedResult` explicitly as an anti-pattern:

> *"We need a `RankedResult` artifact for cross-sectional rankings — that's `SeriesSet` with the rank values as the payload, until `RankedResult` is admitted."*

That precedent — codified in the artifact contract before this ADR — is the platform's existing answer to the cross-sectional question. This ADR is the workflow-layer record of that answer, not a re-derivation.

## Decision

### 1. Option A. The first `cross_sectional_screen` template emits a `SeriesSet` keyed by caller-supplied member labels.

The terminal node is an `align_series` operator whose input is the 4 per-member single-row summary `Series` (produced by 4 parallel `summarize_series` nodes), with `output_keys` slot-substituted from a `member_labels: list` slot. The resulting `SeriesSet` carries one `Series` per member — each a single-row `Series` at the sentinel date `1900-01-01` (the same sentinel `summarize_series` uses for its output) carrying the member's summary value with the member's units.

The desk reads the SeriesSet's four values and reasons about which member is most stretched. **Ranking is not a substrate output today.** The ADR explicitly defers that path to a future per-column-Panel operator addition (see "Deferred future paths" below).

### 2. The template is fixed-arity (N=4 members) per WT7 topology lock.

Templates have static topology. The first template hardcodes four parallel member branches; sibling templates of the same archetype can ship later for N=8 or N=16 (per WT5: *"the catalogue grows by adding sibling templates, not by adding switches inside one template"*). The N=4 choice matches the platform's common cross-country cross-section question (e.g. UST / BUND / GILT / JGB 10Y; or USD-SOFR / EUR-ESTR / GBP-SONIA / JPY-TONA 2Y).

A universe size > 20 will eventually require either (a) a new operator that consumes a `Panel` and emits a per-column summary `Panel`, OR (b) a primitive whose `*Input` already carries the universe (`sovereign_yield_panel`'s `max_length=20` is the existing pattern). Both are out of scope for Stage 4.

### 3. `ARTIFACT_TYPE_NAMES` is unchanged. `OPERATOR_REGISTRY` is unchanged.

This ADR makes no closed-family changes:

- `ARTIFACT_TYPE_NAMES` stays at six members (`Series`, `SeriesSet`, `EventSet`, `Panel`, `WindowedPanel`, `TradeSet`). No `RankedResult` is admitted.
- `OPERATOR_REGISTRY` stays at 12 operators. No `rank` operator is added. (A finance-blind method extension to one existing operator — adding the `"latest"` value to `SummarizeSeriesParams.statistic` per Codex F1 on PR #201 — is a within-operator open-catalogue change that does not affect the registry's entry count; the operator's existing default of `"mean"` is preserved so every existing caller stays byte-identical. The template's `statistic` slot defaults to `"latest"` to deliver an honest snapshot-at-as-of-date reading.)
- `WORKFLOW_ARCHETYPES` stays at five members — `cross_sectional_screen` was already in the tuple as a forward declaration; this ADR records the admission of its first template per WT2, not an enum extension.

### 4. The substrate's `WorkflowResult.terminal_artifact` union already carries `SeriesSet`.

Per [`shared/workflow/result.py`](../../shared/workflow/result.py) and the workflow-template README's "What this is not" boundary, the executor's terminal-artifact union is `Series | SeriesSet | EventSet | Panel | WindowedPanel`. `SeriesSet` is supported. The MCP envelope summarisation in `rates_agent/workflows/_runner.py` was extended in PR-A8 follow-up to include per-key VALUES (`values_by_key` for single-row SeriesSet, `latest_value_by_key` always) — without that, a terminal SeriesSet would have rendered to the user as 4 keys + zero numbers, structurally invisible to the desk (Codex F2 on PR #201). The extension is additive and backward-compatible.

### 5. Ranking interpretation is a UI / desk concern at V1; it is not a substrate artifact.

The platform's discretionary-PM contract (P5 honest disclosure, P12 Bloomberg accuracy boundary) accepts the desk reading a `SeriesSet`'s four values and forming a ranking judgment — this is exactly the kind of "show your work" surface the platform commits to. A substrate-level ranking would impose an opinionated ordering (descending z-score? absolute z-score? signed z-score?) that the metric's choice already determines; making it implicit at the artifact layer would hide the methodology choice the slot schema already declares explicitly.

## Alternatives considered

### (A2) Pure-Panel terminal — single `sovereign_yield_panel` primitive as the only node

**Rejected.** Decision 1 above admits a 9-node DAG whose substrate carries actual metric computation (per-member summary). A2's "single Panel-producing primitive → terminal Panel" is barely more than a primitive call wrapped in a workflow-template envelope — it expresses no analysis beyond what the primitive itself emits and gives no precedent for the *substrate composition* a real cross-sectional screen will require when sibling templates land.

A2 is also asset-class-narrowed by construction (it would have to literal-pin a Panel primitive like `sovereign_yield_panel`, whose universe is sovereign-bonds-only). The Decision-1 design uses *slot-substituted* per-member primitives (per WT3), so the substrate runs unchanged on OIS / inflation-swap / cross-asset members — exactly the asset-class-blindness WT3 + WT15 require.

A2 was the *minimal* Option A flavour; this ADR picks A1 (fixed-arity SeriesSet) as the *real* Option A.

### (A3) Add a new open-catalogue operator (`summarize_panel` or `per_column_zscore`) and build a Panel-primitive → new-operator → Panel-terminal template

**Rejected for Stage 4.** Adding a new operator is open-catalogue (not closed-family per [`03_standards/closed_family_discipline.md`](../03_standards/closed_family_discipline.md) §2), so it does not require a P8 ADR. But it does require the operator to pass the OPR1–OPR16 admission gate ([`02_components/operator/runbook.md`](../02_components/operator/runbook.md)), with a documented promotion rationale (OPR4 — typically *"used by 3+ templates"*, which a single demonstration template does not yet satisfy).

The right shape for A3 is: (1) ship the Stage-4 fixed-arity template (this ADR), (2) ship one or two more cross-sectional templates that each independently want a per-column-Panel reduction, (3) *then* promote the helper to a shared operator with three known callers. That sequencing matches the operator-promotion discipline. Pre-promoting an operator for a single demonstration template would invert OPR4.

Tracked as a roadmap item for the operator layer; not a Stage-4 deliverable.

### (B) Add `RankedResult` artifact + `rank` operator (closed-family extension)

**Rejected for V1.** ART5's precedent and the eight-site landing requirement (ART6) make this the most expensive change in the platform's component system. The trigger for opening it would be: a `SeriesSet`-carried screen produces an artifact whose shape the desk cannot read as a ranked result (PR4 interpretability test). The Option-A prototype in this ADR does *not* produce such a failure: a 4-member SeriesSet keyed by `["UST_10Y", "BUND_10Y", "GILT_10Y", "JGB_10Y"]` with z-score payloads is exactly the cross-sectional surface a sovereign-desk PM reads today.

Re-opening Option B would require:

1. An ADR ([`02_components/artifact/runbook.md`](../02_components/artifact/runbook.md) Path B) declaring the new shape, the rationale, and the producer-consumer pair.
2. The eight-site landing per ART6: Pydantic class in `shared/artifacts/types.py`; `ARTIFACT_TYPE_NAMES` tuple in `shared/workflow/registry.py`; discriminator `ArtifactTypeLiteral` in `state/schemas.py`; `artifact_type_name()` type-map; artifact-store codec in `state/artifact_store.py` (with a `_<type>_to_stored` / `_<type>_from_stored` pair); bridge or producer-operator in `shared/artifacts/adapters/`; consumer-operator (`rank`) with `OperatorSpec`; full ART13 test suite.
3. UI render support — a renderer that maps `RankedResult` → ranked-list view (currently absent).
4. The executor's `TerminalArtifact` union in `shared/workflow/result.py`.

Approximately 8 production sites + ≥3 test sites + a UI surface per Round-4-or-later open scope. Not justified by a single template that the existing closed family expresses cleanly.

### (B-deferred) Defer the cross_sectional_screen archetype entirely until Option B lands

**Rejected.** This archetype was reserved in `WORKFLOW_ARCHETYPES` since V1 explicitly to be opened by a first template — that's the workflow-template README's "forward declaration" pattern (WT2). Leaving it unimplemented while the substrate is capable of expressing it under Option A would accumulate taxonomy debt and contradict ART5's own published direction.

## Consequences

**Positive.**
- The `cross_sectional_screen` archetype opens with a working first template inside Stage 4 (Round 3) without any closed-family extension.
- The platform's closed families (`ARTIFACT_TYPE_NAMES`, `OPERATOR_REGISTRY`, `WORKFLOW_ARCHETYPES`) remain undisturbed. P8 is preserved at full strength.
- The asset-class-blindness chain (WT3 → ART3 → OPR6 → P9) is preserved by the slot-substituted member primitive pattern — the template's operator substrate runs unchanged on rates, FX, inflation, or any future agent's primitives.
- A future per-column-Panel operator (the A3 path) can ship as an open-catalogue addition without needing to revisit the artifact closed family. The decision deliberately leaves that lane open.
- The ART5 precedent's prescription is honoured — `SeriesSet` carries the cross-section, ranking is deferred, no parallel artifact-type system is introduced.

**Negative / accepted.**
- The first template is bounded to N=4 members. Sibling templates for N=8 / N=16 / per-Panel are downstream work.
- The terminal `SeriesSet`'s four values do not embed an explicit rank ordering. The desk reads the values and forms the ranking judgment. This is a documented limitation (P5) declared on the TemplateCard's `description`.
- A future cross-sectional analysis that genuinely cannot be expressed by `SeriesSet` (or by `Panel` with a future per-column operator) will reopen Option B. This ADR does not foreclose that path; it pins V1's default and names the trigger.
- The Phase-2.5 plan's literal step-2 topology (panel primitive → `series_arithmetic` cross-sectional reduction → `zscore_custom` → terminal) is *not* what this ADR's first template ships. That topology is not buildable with today's operator set; this ADR records the substrate-honest alternative.

## Verification

After this ADR lands and PR-A8 is merged:

1. `rates_agent/workflows/cross_sectional_screen/template.yaml` exists; `template.archetype == "cross_sectional_screen"`; `template.terminal_node_id == "screen"` and the `screen` node is an `align_series` operator emitting a `SeriesSet`.
2. `tests/test_workflow_cross_sectional_screen.py` covers all five WT15 layers; the topology-archetype-fit gate uses the allow-list `{"summarize_series", "align_series"}` (no `event_windows`, no `apply_mask`, no `rolling_regression`, no `evaluate_trades`).
3. `ARTIFACT_TYPE_NAMES` in `shared/workflow/registry.py` is unchanged (six members).
4. `OPERATOR_REGISTRY` in `shared/workflow/registry.py` is unchanged (12 operators).  `SummarizeSeriesParams.statistic` is extended from 5 to 6 closed-enum values (added `"latest"`) per Codex F1 on PR #201 — a within-operator method extension that is finance-blind, opt-in, and backward-compatible.  No platform-level closed family is changed.
5. `WORKFLOW_ARCHETYPES` in `shared/workflow/template.py` is unchanged (five members, with `cross_sectional_screen` now backed by a real template).
6. `rates_agent/workflows/mcp_server.py` and `api/routes/workflows/catalogue.py` both import `rates_agent.workflows.cross_sectional_screen` per WT16.

## Deferred future paths (named so a future ADR can re-open them without re-deriving the rationale)

- **A3 — per-column-Panel operator promotion.** Once two additional cross-sectional templates each independently want a per-column `Panel` reduction, promote a shared operator (e.g. `summarize_panel: Panel → Panel(single-row)`) per OPR4. The operator stays open-catalogue (no closed-family extension); ART2's enum is unchanged; the rank-by-value step is still a desk read of a `Panel`.
- **B — `RankedResult` admission.** Trigger: a cross-sectional analysis whose desk surface genuinely requires an embedded rank order at the artifact layer (e.g. an automated pairs-trading template whose downstream operator needs to know "the 3rd-most-stretched spread"). At that point, file a P8 ADR per ART4 + the eight-site landing per ART6.

Neither path is opened by this ADR. Both are documented here so a future contributor consulting this ADR knows the rationale before re-litigating.

## Version log

| Version | Date | Change |
|---|---|---|
| v1 | 2026-05-25 | Initial decision. Accepted — admits `cross_sectional_screen` as a fixed-arity (N=4) SeriesSet-terminal template under Option A. Rejects Option B (RankedResult + rank) and the open-catalogue operator-promotion shortcut (A3) for V1. |
