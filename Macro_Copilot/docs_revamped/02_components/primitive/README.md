# Primitive

> The contract every primitive in the Macro Copilot platform must satisfy — what makes something a primitive at all, what makes it *standard*, and the design principles that govern when to build one (and when not to). **Agent-agnostic by design**: the principles hold whether the agent is rates today or FX, credit, equities, commodities, options tomorrow.

**Version:** v1.1
**Last reviewed:** 2026-05-17
**Status:** load-bearing component contract. Changes require an ADR in [`../../05_decisions/`](../../05_decisions/).
**Operationalises principles:** P1 (future-proofed), P2 (accuracy), P3 (consistency by contract), P4 (determinism), P5 (honest disclosure), P6 (no silent failure), P7 (vendor SDK isolation), P9 (finance-blind operator boundary — *primitives are the finance-aware layer; this contract enforces what that means*), P10 (single source of truth), P11 (domain isolation), P12 (Bloomberg Accuracy Boundary).
**See also:** [`runbook.md`](runbook.md) — the procedure for adding a new primitive.

---

## What this folder is

This is the contract every primitive must satisfy. Primitives are the L2 finance-aware computational layer: they read vendor data from the L1 substrate, compute a finance metric, and return a typed result with full methodology provenance. They are the only place in the platform where domain-specific math lives.

This document is organised as **principles, not archetypes.** The shapes you see in current code (level-window primitives, multi-leg spreads, statistical decompositions, etc.) are *emergent patterns* of how the principles manifest against current data and current desk concepts — not enumerative categories. At 100 primitives the patterns will multiply; the principles will not. A new primitive contributor (human or agent) reads the principles, applies them to their specific concept, and the *shape* falls out as a consequence.

The 16 numbered principles (**PR1–PR16**) below are organised in the order they bind a contributor: definitional (PR1–PR3, *is this actually a primitive?*) → admission (PR4–PR6, *should this be built at all?*) → standardness (PR7–PR11, *what does "standard" mean operationally?*) → operational (PR12–PR16, *the build conventions every standard primitive follows*).

## What a primitive *is* — the universal contract

Every primitive is a folder under `<agent>/<domain>/tools/<tool_name>/` containing exactly four files:

```
<agent>/<domain>/tools/<tool_name>/
  __init__.py     # public-API re-exports (CONFIG_PATH, calculate_<tool>, schema classes)
  config.yaml     # tool meta + conventions (system constants) + methodology
  schemas.py      # Pydantic <Tool>Input, <Tool>CurrentMetrics, <Tool>TimeSeriesRow, <Tool>Output
  compute.py      # def calculate_<tool>(engine, params, config=None) -> dict
```

This shape is invariant across every primitive in the platform regardless of bucket, archetype, agent, or domain. **Different primitives differ in what fills the files, not in the file structure itself.**

Two parameter layers feed every `compute()` call:

| Layer | Lives in | Per-query or constant? | Who controls? |
|---|---|---|---|
| **Inputs** | `<Tool>Input` Pydantic model | per-query | LLM / API caller |
| **Conventions** | `config.yaml` `conventions:` block | system constants | YAML-locked in V1; never LLM-overridable |

The `compute()` signature is canonical, no exceptions:

```python
def calculate_<tool>(
    engine: Engine,
    params: <Tool>Input,
    config: ToolConfig | None = None,
) -> dict:
    if config is None:
        config = load_tool_config(CONFIG_PATH)
    z_window = config.convention_value("z_score_window_days")
    # ... every convention read explicitly via config.convention_value(...) ...
```

Output is a typed Python dict whose schema is declared as `<Tool>Output`.

There are **two distinct output-shape questions** to keep separate:

1. **Primitive output shapes** (what the dict actually carries). Today the catalog has four observed shapes: snapshot + `TimeSeries` (the vast majority of primitives), multi-series outputs that fit a `Panel`, statistical-fit outputs (loadings + scores + diagnostics, no single `TimeSeries`), and categorical outputs (an enum classification plus supporting numeric evidence). A primitive can return any shape its concept naturally requires.

2. **Workflow-bridge artifact shapes** (which output shapes the workflow executor can lift into typed artifacts for composition with operators). The bridge (`shared/artifacts/adapters/` + `shared/workflow/executor.py`) **today dispatches only two shapes**: `Series` (from a primitive's canonical `TimeSeries` payload) and `Panel` (multi-series outputs declared via `output_artifact_type = "Panel"`). Anything else — statistical fits, categorical outputs — is a valid primitive standalone but is **not bridge-composable** in v1. Workflow-composition support for additional shapes is a tracked extension.

This distinction matters: a primitive that returns a categorical output (e.g., `curve_move_classifier`) is a valid primitive that satisfies every principle in this document, but it cannot feed an operator chain through the workflow executor today. It can be called directly via the MCP server and consumed by downstream prose synthesis; it cannot be the source node of a workflow DAG. New primitives whose output is neither `Series` nor `Panel` must be designed knowing this constraint and must explicitly state in the PR description whether workflow-composability is required (in which case the output shape needs to fit one of the two bridge-supported shapes) or not (in which case the primitive is standalone-only until bridge support is extended).

## What a primitive is *not*

Boundary statements that prevent the most common misclassifications:

- **Not an instrument lookup.** A primitive owns a *concept* across a family of instruments, not one specific instrument (PR1). `calculate_curve_spread` is a primitive; `get_ust_2s10s` is not.
- **Not a workflow.** A primitive is one cohesive computation. Multi-step analyses are workflow templates that compose primitives and operators (L4/L5), not single primitives with multi-stage internal logic.
- **Not an operator.** Operators (L3) are finance-blind structural transforms over typed artifacts (P9). Primitives carry the domain knowledge — instrument names, vendor mnemonics, curve families, methodology choices. The two layers are deliberately separate.
- **Not a black box.** Every methodology choice is reachable from `config.yaml`, output provenance, or both. Primitives that cannot satisfy this fall under the *Non-standard primitives* section below — which is currently a placeholder for a path that has not yet been opened.

## How to use this document

For your first read: scan the **Quick index** below, then read every principle's **Rule** line. That alone gives you the whole spec. The **Why**, **Verify**, and **Anti-patterns** sections are reference material for when a question or a PR turns on a specific principle.

For ongoing work: do not re-read this file from top to bottom. Look up the specific principle by ID when it comes up. Cite by ID (`PR8`, `PR9`) in commit messages, PR comments, and code review, exactly the way you cite the P-numbers from [`../../00_thesis/01_non_negotiables.md`](../../00_thesis/01_non_negotiables.md). The two namespaces are deliberately separate: P-numbers are platform-wide; PR-numbers are primitive-specific.

## Quick index — the PR-numbers

| ID | Group | Principle | One-line rule |
|---|---|---|---|
| **PR1** | I | Concept ownership, not instrument ownership | A primitive owns a desk concept across a family, never a single instrument instance. |
| **PR2** | I | One primitive, one concept | A primitive expresses one cohesive desk concept; multi-concept branches split into multiple primitives. |
| **PR3** | I | Domain residence by conventional ownership | A primitive lives in the agent/domain that conventionally owns its concept on a real desk, even if it reads data across domains. |
| **PR4** | II | Parsimony (composability + LLM tool-selection clarity) | Do not build a new primitive if existing primitives + operators in the same sub-agent compose to the same output, unless there is a defensible accuracy / efficiency / interpretability / provenance / LLM-clarity advantage. |
| **PR5** | II | Concept novelty | A new primitive is a genuinely new concept, not a new instrument, new region, new family, or new universe member of an existing concept. |
| **PR6** | II | Metadata sufficiency, no proxies | A primitive cannot ship if its desk-recognized definition requires metadata the substrate does not have. The answer is *defer*, not *proxy*. |
| **PR7** | III | Configuration offloading | Every methodology default lives in `config.yaml`; mathematical invariants stay in code; no hidden module-level methodology constants. |
| **PR8** | III | Single-knob discipline | `<Tool>Input` exposes the per-query parameters plus exactly *one* central methodological choice. Everything else is YAML-locked. |
| **PR9** | III | Composition inheritance (strict) | If primitive B composes primitive A, B's config must expose *every* methodology choice A makes — pin if necessary, with rationale — for B to remain standard. *(Strict for now; will relax to "material to B's output" after a catalog-wide review.)* |
| **PR10** | III | Provenance echo | Every primitive's output carries enough metadata to reproduce the methodology — which conventions, which upstream primitives, which version of each. |
| **PR11** | III | Honest refusal | Multi-method primitives that have not built every method raise `NotImplementedError` with `planned_extensions` reference; never silently fall back. |
| **PR12** | IV | Registered methodology sources | Every `Convention.source` references a registered tag from `03_standards/methodology_disclosure.md` (forthcoming). New tags require a one-line registry entry. |
| **PR13** | IV | Cross-config consistency | The same convention key has the same value across every tool that uses it; CI lint (`python -m shared.config.lint`) enforces. |
| **PR14** | IV | Wire-format honesty | Output field names that embed methodologically-load-bearing parameters (window lengths, anchor choices) are frozen. Changing the parameter requires a schema migration. |
| **PR15** | IV | Parity-fixture discipline | Every primitive ships with a parity fixture; any convention change requires fixture regeneration in the same PR. |
| **PR16** | IV | Test triplet | Every primitive ships with `compute` + `wiring` + `sql_validation` tests; SQL validation independently reproduces core math against the real DB; both layers required before merge. |

---

## Group I — Definitional: is this actually a primitive?

The first question. If the answer to any of PR1–PR3 is "no, this is not a primitive," the conversation stops here and the proposal goes to a different shape (an instrument lookup, a workflow, an operator, or a non-component change).

### PR1 — Concept ownership, not instrument ownership

**Rule.** A primitive owns a *desk-recognized finance concept* across a family of instruments, parameterized by the choice of instruments. It never owns a single instrument instance.

**Why.** Scale. Without this rule, the catalog explodes from 100 primitives (one per concept) to 10,000+ (one per instrument-concept pair). Every workflow that wants to apply a concept to a new instrument would require a new primitive PR. Worse, the LLM router's catalog becomes unmanageable — the embedding-retrieval haystack grows linearly with instruments and noise dominates.

**Verify.**
- `<Tool>Input` exposes the instrument-selection parameters as inputs (`curve_family`, `tenor`, `pair`, `ticker_list`, `symbol`, etc.), not as hard-coded values inside `compute.py`.
- The primitive's `tool.name` in `config.yaml` is concept-named (`calculate_curve_spread`), not instrument-named (`get_ust_2s10s`).
- The same primitive works against multiple instances of its family without code change. Test it with at least two distinct instrument selections in the compute test.

**Anti-patterns.**
- `get_italy_10y_yield_tool`, `get_bund_10y_tool`, `get_ty1_price_tool`, `calculate_ust_2s10s_spread`. Each of these names a single instrument; each should be a general primitive (`get_yield_levels`, `calculate_curve_spread`) parameterized by instrument selection.
- A primitive whose `compute()` body has hardcoded `if curve_family == "UST": ... elif curve_family == "DE_BUND": ...` branches that change *what the primitive returns*. (Branches that change *how* — methodology — are also wrong for a different reason; see PR2 and PR7.)
- A primitive that exists only because "we wanted this one specific number on the dashboard."

**Exceptions.** None.

**Relates to.** PR2 (one concept), PR5 (concept novelty), P3 (consistency by contract — every primitive looks like every primitive), P11 (per-agent ownership of primitives).

### PR2 — One primitive, one concept

**Rule.** A primitive expresses one cohesive desk concept. If a candidate primitive needs an internal branch where the branches represent *different desk concepts* (not different methodologies for the same concept), split into multiple primitives.

**Why.** Tool-selection clarity for the LLM (see PR4) and reviewer sanity. A primitive that does two things is two primitives in a trench-coat; the LLM can't tell which "mode" the user wanted, the contract becomes ambiguous, and downstream consumers can't reason about what they're calling. The platform's scale claim depends on each primitive being a single clean unit of meaning.

**Verify.**
- The primitive's `methodology.what_it_does` block in `config.yaml` describes *one* thing in one paragraph, not "this tool does X, or alternatively Y."
- There is no `method: Literal["A", "B"]` parameter where A and B compute *different concepts*. (A `method` parameter that selects between *different methodologies for the same concept* — e.g., `financing_rate`'s `overnight_index_proxy` vs `constant_rate` — is fine. The test is whether a desk would consider the two methods to produce values that mean the same thing, just computed differently.)
- The output schema is consistent across all inputs; you do not return `{"yield_pct": ...}` for some inputs and `{"spread_bps": ...}` for others.

**Anti-patterns.**
- A primitive named `analyze_curve` that returns yields when given one tenor, spreads when given two, butterflies when given three. Three primitives, not one.
- A primitive that switches its output schema based on an input flag.
- A primitive whose `methodology.what_it_does` requires the word "or" to describe what it produces.

**Exceptions.** None.

**Relates to.** PR4 (overlapping primitives confuse the router), PR11 (the `method` enum pattern — different methodologies for *one* concept is allowed; different concepts is not).

### PR3 — Domain residence by conventional ownership

**Rule.** A primitive lives in the agent and sub-agent that *conventionally* owns its concept on a real institutional desk. This holds even when the primitive reads input data from across domain boundaries.

**Why.** Domain isolation (P11) is enforced at the *tool-catalog* layer, not the *data-source* layer. A swap-spread is conventionally an OIS-desk concept, so `swap_spread` lives in `rates_agent/ois/tools/`, even though it reads sovereign yields too — because an OIS trader is the one who asks about swap spreads, the OIS agent's MCP server is the one that should expose it, and the OIS agent's prompt should know it. Putting it under `sovereign_bonds/` because "it reads sovereign data" would force the rates supervisor to route correctly across two domains for a single-domain question.

Cross-domain *data fetching* goes through `shared/analytics/rates_fetch.py:fetch_cross_domain_pair` (or analogous helpers in other agents). The primitive code stays in one domain; the data crossing is a substrate-level concern.

**Verify.**
- The primitive folder is under the agent / sub-agent the *user* would expect to own the concept, not the agent that happens to own the most input data.
- If you cannot answer *"which desk role would ask for this primitive by name?"* with one specific desk, the concept may not be primitive-ready (see PR1, PR2).
- When the primitive reads data from another domain, the read goes through a `shared/analytics/` helper, not a direct import from the other agent's package.
- A `grep` for `from <other_agent>` inside this primitive's `compute.py` is empty.

**Anti-patterns.**
- A primitive moved to the sub-agent that happens to own the most input data, not the desk concept. (`asset_swap_spread` placed under `sovereign_bonds/` because it reads bond yields, when the asset-swap concept is an OIS-desk concept.)
- A primitive that imports directly from another agent's tools or schemas (e.g., `from rates_agent.sovereign_bonds.tools.curve_spread import ...` inside `rates_agent/ois/tools/...`).
- "We'll put it under `shared/` because it's cross-domain." There is no `shared/tools/`; primitives live under agents.

**Exceptions.** None for *new* primitives. Existing primitives whose placement was made under different rules can stay; relocation is a P3+P11 closed-family-style decision (file an ADR).

**Relates to.** P11 (domain isolation), PR1, P9 (operators are domain-blind; primitives carry domain).

---

## Group II — Admission: should this be built at all?

PR1–PR3 said the candidate *is* a primitive. Group II asks whether it *should* exist. This is where the platform's scale claim is defended PR by PR.

### PR4 — Parsimony (the composability check + LLM tool-selection clarity)

**Rule.** Do not build a new primitive if its output can be produced by composing existing primitives in the same sub-agent with the existing operator catalog. The default action when a desk concept maps to a chain `primitive_A → operator_X → primitive_B → operator_Y → result` is **compose**, not **build a new primitive**.

A new primitive is admitted only when at least one of these five advantages is defensibly true *and stated in the PR description*:

1. **Accuracy.** The composition accumulates floating-point or methodology error that a direct computation avoids. The primitive's result is bit-stable in a way the composition is not.
2. **Efficiency.** The composition would require many round-trips (DB reads, intermediate artifact persistence, repeated rolling-window calculations); a direct primitive is materially faster in a way the desk would notice.
3. **Interpretability.** The composition produces an artifact whose *shape* the desk doesn't recognise (an intermediate `Series` no one trades); the direct primitive produces a *desk-recognized number* (e.g., "BTP-Bund 10Y spread z-score" is one thing the desk asks for; a 4-node DAG that happens to compute it isn't).
4. **Provenance.** The composition loses upstream methodology context across the artifact boundary; the direct primitive carries explicit provenance about every choice.
5. **LLM tool-selection clarity.** The composition would force the LLM router to assemble a multi-step DAG correctly *every time* the desk asks for what is conceptually a single thing. The router's accuracy degrades as the assembly complexity grows. A single primitive named after the desk concept removes that assembly burden entirely.

**Why.** Every primitive added has compounding cost: maintenance burden, doc burden, parity-fixture burden, methodology-attestation burden, embedding-retrieval haystack growth, and — most consequentially — **LLM tool-selection ambiguity**. The router has to pick one or more primitives for a user's query; its accuracy is bounded by how distinct the tool catalog is. Two primitives with overlapping scopes are a routing nightmare: the LLM may pick A when B was right, pick both and present confused output, or pick neither and refuse a query that should have worked.

Without PR4 the catalog grows linearly with desk concepts. With PR4 it grows logarithmically with desk concepts because compositions absorb the variation. This is the principle that keeps the catalog at 100 tools instead of 1000.

**Verify.**

The PR description must answer two questions explicitly:

1. **The composability check.** Can this be produced by existing primitives in the same sub-agent + the existing operator catalog? If yes, list the composition (`tool_A → align_series → tool_B → threshold_events → ...`) and argue why composition is *materially worse* than a new primitive against at least one of the five criteria above.
2. **The non-overlap check.** Run the proposed `tool.name` and `methodology.what_it_does` past every existing primitive's `name` + `one_liner`. Is there a primitive whose scope could plausibly cover this new tool's use case? If yes, the right answer is usually to *extend the existing primitive* (via a new central knob value, or new universe coverage) rather than add a sibling.

**Anti-patterns.**
- "It's faster than the composition" — without measuring how much faster, and whether the difference matters at desk-relevant query rates.
- "The DAG is uglier" — without arguing how the LLM would mis-route the multi-step DAG (or how a desk user would mis-interpret the intermediate output).
- Building `calculate_X_for_universe_Y` when `calculate_X` already exists and could take a universe parameter.
- Building two primitives whose `one_liner` would be a near-paraphrase of each other ("z-score of curve spread" and "rolling standardised spread metric"). One of these is wrong by default.
- "Maybe someone will want this someday" — speculative additions fail PR4 unconditionally. Build when a real workflow needs it.
- The most insidious failure: building a primitive that is technically distinct from every existing one but whose LLM-routing scope *overlaps* one of them. The router will misroute under load.

**Exceptions.** None for *standard* primitives. Non-standard primitives (when that section opens) have their own admission rules.

**Relates to.** PR2 (overlapping scope is two-primitives-in-one), PR5 (concept novelty is the first cousin), AC8 (this is an AC8 trigger — if the parsimony argument is unclear, ask the human before merging).

### PR5 — Concept novelty

**Rule.** A new primitive is a *genuinely new desk concept*. It is not:

- a new universe member of an existing primitive (more tickers in the playbook does not need a new primitive);
- a new curve family on an existing concept (the existing primitive should take `curve_family` as an input);
- a new region or country (a `country` parameter on the existing primitive, not a new sibling);
- a new instrument example (`yield_levels` works for any instrument family that lives in `instrument_master`);
- a new workflow step (workflows compose; they don't promote chains into primitives).

**Why.** The most common reason a primitive proposal is mis-classified as new is that the contributor sees a *new use case* and reaches for a *new primitive*, when the right answer is a new input value on an existing one. Reaching for a new primitive when the existing one already covers the concept is the most common single source of catalog bloat.

**Verify.**
- The PR description names the specific desk concept being introduced (one sentence).
- The reviewer can name an existing primitive whose `tool.name + one_liner` *could not* cover the new use case under any input.
- The new primitive's `methodology.what_it_does` describes *a different thing* from any existing primitive's `methodology.what_it_does` — not "the same thing but for X."

**Anti-patterns.**
- A PR titled `feat: add curve_spread for German Bunds` (the existing `calculate_curve_spread` takes `curve_family` as input).
- A PR that adds a new tool whose `methodology.what_it_does` is *"same as `<existing_tool>` but for `<new_universe>`."*
- A PR whose only diff from an existing tool is a different default value of a YAML convention.

**Exceptions.** None. If a concept is genuinely new, this principle does not bind; if it is not, no exception applies.

**Relates to.** PR4 (overlapping concept is also a parsimony failure), PR1 (instrument-specific is one form of concept inflation).

### PR6 — Metadata sufficiency, no proxies

**Rule.** A primitive cannot ship if its real desk-recognized definition requires metadata the substrate doesn't have. The answer when metadata is missing is to **defer the primitive**, not to ship a proxy.

**Why.** This is the operational expression of P2 (accuracy) and P12 (Bloomberg Accuracy Boundary) at the primitive layer. A proxy — a 1:1 raw spread when the real concept is DV01-weighted, a generic real-yield series when the real concept needs CPI seasonals, a constant assumption where a real input would be required — is *not* a V1 of the primitive. It is a different thing under the wrong name, and it ships under the same desk-recognized label as the real concept. A PM consuming the proxy on the wrong assumption gets a wrong number with no warning. The trust cost compounds.

**Verify.**
- The PR description names every metadata field the real desk concept requires (vendor field name, derived field, reference field).
- For each, the field is either (a) already present in `instrument_master` or `market_data_daily`, or (b) ingested by an existing playbook, or (c) shippable in the same PR via a playbook extension.
- If any required metadata is missing and cannot land in the same PR, the PR is closed; the primitive is added to the planned-but-blocked register with the data dependency named.

**Anti-patterns.**
- "We'll use a constant 0.5 assumption for now and improve it in v2."
- "We'll proxy the missing field with the closest available one."
- A primitive that ships under the desk-recognized name with a `methodology.assumptions` block that admits the proxy in fine print.
- A primitive whose tests pass because the synthetic fixture provides the proxy data, while real-DB data would not.

**Exceptions.** One narrow case: a primitive may be shipped with a documented `analyst_override` knob that exposes the missing data as a user-supplied input (with no default), per the same exception in P12. This requires explicit opt-in by the caller, full P5 disclosure on the methodology card, and is not the same as shipping a proxy under defaults.

**Relates to.** P2 (accuracy), P6 (no silent failure — the documented refusal is the alternative to the proxy), P12 (Bloomberg Accuracy Boundary), AC7 (refuse rather than invent).

---

## Group III — Standardness: what does "standard" mean operationally?

The doctrine's existing definition of *standard* is correct but abstract: *"every methodology choice it depends on — including upstream dependency choices — is explicit, inspectable, reproducible, and reachable from config and/or output provenance, so that the same data plus the same methodology state yields the same answer."* PR7–PR11 operationalise it as five concrete, testable rules. A primitive is standard if and only if all five hold.

### PR7 — Configuration offloading

**Rule.** Every methodology default — windows, ddof, fill limits, rounding precision, default field names, thresholds — lives in `config.yaml` with a full `{value, source, rationale, valid_range}` block. Mathematical invariants — "short_tenor ≠ long_tenor", "end_date > start_date", "tenors must be sorted ascending" — stay in code as Pydantic `@model_validator(mode="after")` validators. **No hidden module-level methodology constants in `compute.py`.**

**Why.** This is the primary operational definition of "standard." A reviewer reading the YAML must be able to enumerate every methodology choice the primitive makes. If a window length, a fill limit, or a threshold lives inside `compute.py`, the methodology is hidden from anyone who isn't reading the Python — which means it isn't standard.

**Verify.**
- Every numeric constant in `compute.py` is either (a) a mathematical truth (`100` to convert percent to bps, `1.0` for unit factor), or (b) read from `config.convention_value("...")`.
- Every string default (`field_name`, `method`, etc.) is YAML-sourced; no `DEFAULT_FIELD = "YLD_YTM_MID"` at module level.
- The `config.yaml`'s `conventions:` block lists every methodology choice with a non-empty `source`, non-empty `rationale`, and `valid_range` for numerics.
- The convention validator in `shared.config.tool_config` accepts the YAML without errors.
- A reviewer can read the YAML alone (without `compute.py`) and produce a complete list of the methodology choices the primitive makes.

**Anti-patterns.**
- `_Z_SCORE_WINDOW_DAYS = 252` as a module-level constant in `compute.py`.
- Magic numbers in compute body (`if abs(spread_change) < 1.0:`) — the 1.0 should be in YAML.
- Methodology choices in code comments rather than `config.yaml` (`# rolling 252-day window`).
- A `defaults` dict at the top of `compute.py` that duplicates what should be in YAML.

**Exceptions.** None.

**Relates to.** P5 (honest disclosure — YAML conventions are what the methodology card surfaces), P10 (single source of truth — YAML is THE source for conventions).

### PR8 — Single central methodology surface (the "central knob")

**Rule.** `<Tool>Input` exposes the per-query parameters that select the instrument(s) being computed against (curve_family, tenor, ticker, date range, etc.) — plus *one cohesive central methodology surface*: the bounded set of parameters that together define what the primitive IS. For most primitives this is a single field (a window length, a lookback period). For statistical-fit primitives it can be a small bounded set of model-spec inputs (e.g., `n_components` + `change_frequency` + `tenors` for `pca_yield_curve` together define the PCA fit). Every methodology choice *outside* this central surface is YAML-locked and not LLM-overridable in V1.

The central surface varies by primitive. Examples from current code (and matching the canonical A13 table in the existing tool-architecture doctrine):

| Primitive | Central methodology surface (LLM-exposed) | YAML-locked methodology |
|---|---|---|
| `curve_spread` | `lookback_days` (single field; display window) | z-score window, ddof, ffill, rounding |
| `yield_levels` | `lookback_days` (single field; display window) | z-score window, ddof, ffill, rounding |
| `butterfly` | `lookback_days` (single field; display window) | z-score window, ddof, ffill, weights (50-50 locked), rounding |
| `zscore_custom` | `z_score_window_days` (single field; the window itself) | min_periods, ddof, buffer, ffill |
| `rolling_regression` | `regression_window_days` + `target_spec` + `regressor_specs` (cohesive surface — together they specify the regression) | min_periods, add_constant, solver, ffill, rounding |
| `pca_yield_curve` | `n_components` + `change_frequency` + `tenors` (cohesive surface — together they specify the PCA fit) | sign_anchor, min_observations, rounding |

**Why.** Bounded, well-typed LLM input space. The LLM router has a limited number of slots it can populate per call; every additional slot is a chance for it to mis-fill. By keeping the central methodology surface small and cohesive — and YAML-locking everything else — the input surface stays narrow and the LLM's job is well-defined. The *meaning* of the primitive stays crisp: the central surface is what defines what the primitive is computing, not how it computes it.

"Cohesive" is the load-bearing word. Three knobs that *together* specify one model (PCA's n_components + change_frequency + tenors) is one surface. Three knobs that each independently change a different aspect of the output (window + ddof + min_periods all as inputs) is three surfaces — and the second two should be in YAML.

**Verify.**
- `<Tool>Input` has the instrument-selection parameters plus a small, cohesive central methodology surface (single field for most primitives; 2-3 fields max for fit-based primitives where the fields together specify the model).
- A reviewer can describe the central surface in one sentence: *"The central surface of `pca_yield_curve` is the PCA fit spec — n_components + change_frequency + tenors choose what model you fit; everything else is YAML-locked."*
- No `<Tool>Input` field is a methodology choice that the YAML already pins. (If both `<Tool>Input.window_days` and `conventions.window_days` exist, the LLM and the YAML are contesting the same value; this is wrong.)
- Convention-ish parameters like `ddof`, `min_periods`, `solver`, `add_constant`, `rounding_decimals` are in YAML, not in `<Tool>Input`. These are *how* the primitive computes, not *what* it computes.

**Anti-patterns.**
- A primitive whose `<Tool>Input` has independent methodology knobs that do not cohere into one model spec (`window_days`, `ddof`, `min_periods` all as inputs). The latter two are YAML decisions.
- A primitive whose `<Tool>Input` exposes `default_field_name` as an override of itself. Field-name overrides via `field_name: Optional[str] = None` are an instrument-selection choice (which field of the instrument), not a methodology choice — that's fine. But a methodology parameter masquerading as an instrument selector is not.
- A primitive where the central surface doesn't actually define what the tool *is* — e.g., exposing `rounding_decimals` as the LLM-controllable methodology field.
- A "central surface" that's a grab-bag of unrelated knobs each independently shifting the output. The surface must be cohesive: knobs that *together* specify the computation.

**Exceptions.** None. Primitives that genuinely need multiple independent methodology knobs are doing more than one thing (PR2 violation) and should split.

**Relates to.** P11 (LLM tool-selection clarity), PR2 (multi-concept tools have multiple independent central surfaces naturally; split them), the canonical A13 table in `docs/architecture/tool_architecture.md` (preserved here, repos uses the same definition).

### PR9 — Composition inheritance (strict)

**Rule.** When primitive B composes primitive A — calls A's `compute()` at runtime, or consumes A's output as input — B inherits the *standard* property only if **B's config exposes every methodology choice A makes**. B may pin specific values of A's knobs if the desk concept B represents requires those specific values, but the pinning must be **explicit in B's `config.yaml`** with rationale.

> *(This is the strict form of PR9 for now. After a catalog-wide review of existing and forthcoming composition primitives, we will relax to "every methodology choice A makes that is **material to B's output**" — letting B remain standard without surfacing A's purely-cosmetic conventions like rounding precision. The strict form binds today because we do not yet have the body of evidence to draw the materiality line confidently; until that review, exposing more rather than less is the safer default.)*

**Why.** The failure mode this rule prevents: B silently calls A with A's defaults; A's defaults change in a future PR; B's output changes silently; no one knows why. PR9 makes composition primitives *auditable*. Without it, the "standard" property does not transit composition boundaries — and the platform's audit story collapses the moment a composition primitive ships under that label.

**Grandfathered debt.** The existing `yield_change_attribution_pca` predates this rule and does not satisfy strict PR9 today — it calls `calculate_pca_yield_curve(engine=engine, params=pca_input)` without an explicit upstream `config=` argument (relying on auto-load of pca_yield_curve's bundled config), and its own `config.yaml` does not surface or pin every PCA convention (e.g., `sign_anchor`, `degenerate_variance_share_threshold`, `factor_round_decimals`). This is **documented debt to be remediated in the same catalog-wide review** that will relax PR9 to "material to B's output." Until that review:

- **New composition primitives must satisfy strict PR9 from day one** (they do not get grandfathered status — the platform learned from the existing example).
- The existing `yield_change_attribution_pca` continues to ship; the debt is tracked under Open Questions item 1.
- Reviewers seeing a *new* composition primitive that looks like `yield_change_attribution_pca` must reject it under strict PR9 even though the existing primitive looks the same shape.

**Verify (for new composition primitives).**
- For each upstream primitive B calls, every convention in A's `config.yaml` is either:
  - Surfaced as an input on B (the LLM or the caller can set it), or
  - Pinned in B's `config.yaml` with an explicit rationale ("`pca_yield_curve.sign_anchor` is pinned to `level` here because attribution requires comparable signs across days").
- B's `compute()` calls A with an explicit `config=` argument constructed from B's own configuration, not relying on A's auto-load fallback.
- B's output echoes A's chosen methodology in provenance fields, not only in a lineage chain. The fields surface to the user; the user can read off "this attribution was computed against a PCA fit with `n_components=3`, `change_frequency=daily`, `sign_anchor=level`."
- B's tests cover the case where A's pinned conventions are altered (the test should *fail* if the pinning is broken; it is the test's job to enforce the pinning).

**Anti-patterns.**
- B's `config.yaml` is silent on A's conventions; A's defaults flow through implicitly.
- B's output schema names the upstream primitive but does not echo its methodology choices.
- B has an inline call to A's `compute(...)` with no explicit `config=` argument — the auto-load of A's config means B is silently inheriting whatever A's current defaults are.
- "We're inheriting A's defaults so we stay consistent" — without surfacing the inheritance in B's config or output.

**Exceptions.** None for *standard* composition primitives. A composition primitive that cannot satisfy PR9 is not standard; it falls under the (future) non-standard category.

**Relates to.** P4 (determinism — composition without provenance breaks replay), P5 (honest disclosure), P10 (single source of truth — A's methodology has one definition; B must not silently fork it).

### PR10 — Provenance reachability

**Rule.** Every methodology choice a primitive depends on must be **reachable** from at least one of three places: the primitive's `config.yaml`, the artifact lineage chain, or the primitive's output payload. The methodology card a user sees on a workspace must be reconstructable from one of these without inventing information. *Which* of the three carries provenance depends on the primitive class:

| Primitive class | Where provenance MUST surface | Why |
|---|---|---|
| **Simple Bucket 1A** (`yield_levels`, `curve_spread`, `butterfly`, etc.) where every methodology choice is in YAML and the bundle is content-hashed onto the artifact | `config.yaml` + artifact lineage chain (`tool_config_hash`) + methodology card pulled from `config.yaml` | The config is the single source of truth for methodology; the artifact's `tool_config_hash` pins the version. The output payload does not need a separate provenance block because the methodology card is reconstructable from the pinned config alone. |
| **Composition primitives** (those affected by PR9) | Output payload MUST surface upstream methodology choices as explicit fields | The upstream primitive's config is a separate file with a separate hash; without explicit echo in the output, a user reading B has to chase down which version of A produced the result. |
| **Pasted-input primitives** (where the caller provides upstream model state, e.g., pasted PCA loadings) | Output payload MUST echo the pasted provenance (fit window, sign anchor, observation count, etc.) | The "upstream" lives in the caller's prompt, not in any registered config. Without explicit echo, the methodology trail dies at the API boundary. |
| **Statistical-fit primitives** (model state is itself an output) | Output payload MUST surface the fit parameters (n_components used, observations used, convergence diagnostics, sign-anchor outcome) | The fit is the answer; the user cannot interpret the answer without seeing the model state explicitly. |
| **Non-obvious methodology** (any primitive that uses a proxy, default, or approximation the desk user might not assume) | Output payload SHOULD surface a one-line methodology note (e.g., `methodology_note: "financing computed via overnight_index_proxy method"`) | P5 requires hidden assumptions to be visible at the surface, not buried in config. |

The doctrine's wording is exact: *"reachable from config and/or output provenance, so that the same data plus the same methodology state yields the same answer."* PR10 is the operational expression of *reachable* — the methodology card must be reconstructable from somewhere a user can see without leaving the workspace.

**Why.** Lineage in the artifact-store is the *replay* substrate (P4). Provenance in either the config or the output (per the class above) is what makes the methodology *human-readable* to the desk user at the moment they're looking at the number (P5). The catalog has multiple legitimate paths to "reachable"; forcing every primitive to add a provenance block to its output would mass-reject most Bucket 1A primitives without making them more auditable.

**Verify.**
- For Bucket 1A standard primitives: the `config.yaml` carries every methodology choice (PR7); the artifact's `tool_config_hash` references it; the methodology card surfaces those values via the config. No provenance block in the output required.
- For composition primitives: the output schema includes a provenance block that names the upstream primitive(s) and the upstream conventions used in this specific run.
- For pasted-input primitives: the output echoes the pasted upstream provenance the caller supplied.
- For statistical-fit primitives: the output surfaces the fit parameters and diagnostics that the desk needs to interpret the result.
- For any primitive that uses a proxy or approximation: the methodology card has a one-line `methodology_note` (or equivalent field) at the surface, not buried in config comments.

**Anti-patterns.**
- A composition primitive returning a number with no upstream-methodology echo. *(PR10 + PR9 violation.)*
- A primitive using a proxy method (e.g., `overnight_index_proxy` for repo financing) without a visible methodology note in the output. *(PR10 + P5 violation.)*
- "The user can look up the config" *as a substitute for surfacing non-obvious methodology* — for obvious YAML-locked conventions on simple primitives, yes, the config is sufficient; for non-obvious choices, the surface must say so.

**Exceptions.** None on the *reachability* requirement. The choice of *where* provenance surfaces (config vs. output payload) depends on the primitive class per the table above.

**Relates to.** P4 (lineage is the replay substrate), P5 (honest disclosure of methodology), PR9 (composition primitives have a stricter provenance requirement than simple primitives).

### PR11 — Honest refusal

**Rule.** A primitive that supports multiple methods via an enum (`method: Literal["A", "B", "C", "D"]`) and has only built methods A and B must refuse cleanly when called with C or D. It must never silently fall back to A. The refusal must (a) be detected before any partial computation runs, (b) carry an explicit message that names the unbuilt method, and (c) point the caller at `methodology.planned_extensions` in `config.yaml`.

Two refusal mechanisms are acceptable today, both honest, both used in the current catalog:

1. **`raise NotImplementedError(...)`** with a clear message — appropriate when the unbuilt path is an *engineering* gap (the code structure exists but the implementation has not been written).
2. **`return {"error": "..."}` (controlled error envelope)** — appropriate when the unbuilt path is a *data/methodology* gap that the surrounding repo pattern surfaces as a structured error to the MCP boundary, where the wrapper converts to the transport envelope per P6. `financing_rate` uses this pattern today for `term_repo_curve` and `gc_special_blend`: the message names the missing data (*"real term-repo data is not yet ingested"*), points at `methodology.planned_extensions`, and never invokes the implemented methods as a fallback.

What is *not* acceptable: a bare `pass`, a silent return of `None`, a fallback to a different method, or a generic error message ("something went wrong"). The refusal must be loud, named, and actionable — whichever mechanism is used.

The Pydantic schema must enforce method-specific parameter requirements via a `@model_validator(mode="after")` so the *validation* error (a separate failure mode from method-not-built) fires before `compute()` runs.

**Why.** P6 (no silent failure) at the primitive layer. A primitive that silently falls back to A when the user asked for C teaches the LLM that "C works" — which propagates to misuse across many subsequent prompts. Both refusal mechanisms (raise + envelope) satisfy P6 *equally* as long as the refusal is loud, specific, and actionable. The choice between them is contextual: raise is cleaner inside the compute layer; the envelope is cleaner when the surrounding repo pattern already routes the result through an error-envelope-aware wrapper at the MCP boundary.

**Verify.**
- The `<Tool>Input.method` field is typed as `Literal[...]` with every supported value declared.
- Methods not yet built either raise `NotImplementedError("<method> is not yet supported; see methodology.planned_extensions")` immediately on compute entry, **or** return `{"error": "method=<method> is declared in the closed enum but V1 raises NotImplementedError — <one-line reason>. See config.yaml methodology.planned_extensions."}` — and never proceed to the implemented method dispatch.
- The Pydantic schema has a `@model_validator(mode="after")` that enforces "exactly one method's required params supplied" so the validation error fires before `compute()` runs.
- The `methodology.planned_extensions` block in `config.yaml` lists each unbuilt method with a brief rationale (what data or work blocks it).
- A test exercises each unbuilt method and asserts the refusal mechanism the primitive uses (raise or envelope) with the expected message shape.

**Anti-patterns.**
- An unbuilt method silently falling back to a default method.
- A `NotImplementedError` or error envelope with no message ("raise NotImplementedError" by itself, or `return {"error": ""}`).
- An unbuilt method with no entry in `methodology.planned_extensions` — the user gets a refusal with no path forward.
- Mixing the two mechanisms inside one primitive (some unbuilt methods raise, others return envelope) — pick one for the whole primitive and stay consistent so callers don't have to branch.

**Exceptions.** None. The choice between raise and envelope is a stylistic decision, not an exception to the rule.

**Relates to.** P6 (no silent failure), AC7 (refuse rather than invent), P1 (documented refusal surfaces are the right way to ship an incomplete contract honestly).

---

## Group IV — Operational: the build conventions every standard primitive follows

PR7–PR11 define what *standard* means. PR12–PR16 are the operational conventions that keep the catalog coherent — testable in CI, the floor every primitive sits on.

### PR12 — Registered methodology sources

**Rule.** Every `Convention.source` in every `config.yaml` references a registered tag from the methodology source registry. New tags require a one-line registry entry before they can be used. Vague tags (`default`, `standard`, `bloomberg`, `convention`, `tbd`, `fixme`) are auto-reject.

**Why.** P5 (honest disclosure) requires every methodology choice to name its origin. A vague source tag (`source: "default"`) is no source at all — it documents that the choice was made without saying *why*. The registry forces every default value to have a defensible origin; the source tag is the named claim.

**Verify.**
- Every `Convention.source` value in every `config.yaml` matches an entry in the methodology-source registry. (The registry will live in `03_standards/methodology_disclosure.md`, forthcoming; for the migration period, see the existing canonical list at `docs/architecture/methodology_sources.md`.)
- CI lint flags unrecognised source tags. (Today the validator only enforces "non-empty string"; strict enum enforcement is a planned tightening — until then, the rule is enforced socially by code review.)
- No source tag is one of the auto-reject vague forms.

**Anti-patterns.**
- `source: "default"`, `source: "standard"`, `source: "convention"`, `source: "bloomberg"` (vague — name the specific Bloomberg field convention), `source: "tbd"`, `source: "fixme"`, `source: "change_me"`.
- A new source tag introduced in a `config.yaml` without a corresponding registry entry in the same PR.
- `source: "team_judgment_pending_review"` on something that has a real external citation — this tag is *debt* (it tracks how many conventions are awaiting external validation), not a free pass to skip the source search.

**Exceptions.** None.

**Relates to.** P5, P10.

### PR13 — Cross-config consistency

**Rule.** The same convention key has the same value across every tool that uses it. `z_score_window_days = 252` in `curve_spread` must be `252` in `butterfly`, `yield_levels`, `cross_market_spread`, every other tool that reads it. Enforced by `python -m shared.config.lint` in CI; drift is a build failure.

**Why.** P3 (consistency by contract) at the methodology layer. Two tools that compute different versions of the same z-score produce comparable outputs that aren't actually comparable. The whole catalog's analytical coherence depends on this rule.

**Verify.**
- `python -m shared.config.lint` exits clean.
- A new primitive uses the same value for shared convention keys as the existing primitives that use those keys.
- When a deliberate divergence is needed (rare), the divergence is justified in the PR description and the consistency rule is *changed* (the lint config updated to allow the divergence) rather than silently violated.

**Anti-patterns.**
- A new primitive that sets `z_score_window_days: 250` because "the data is short for this case." Either change every tool to 250 (with parity fixtures regenerated everywhere), or use 252 with a documented `min_periods` accommodation.
- Suppressing the lint error rather than fixing the divergence.

**Exceptions.** The lint compares the same convention *name* across every tool that declares it — it has no concept of "groupings" today. If two tools both declare `ffill_limit_days` they must use the same value. Where two tools genuinely need different values for the same convention concept (e.g., 5 days for daily price series, 1 day for monthly CPI prints), the right answer today is to **name them differently** (`daily_ffill_limit_days`, `monthly_ffill_limit_days`) so the lint does not bucket them together. A future lint extension that supports explicit per-context groupings would let the same name carry different values across declared groups; until that extension lands, naming is the disambiguator.

**Relates to.** P3, P10.

### PR14 — Wire-format honesty

**Rule.** Output field names that embed methodologically-load-bearing parameters (window lengths, anchor choices, method names) are frozen. The field name encodes the methodology that produced the value, so a downstream consumer reading the name knows what they're consuming. Changing the underlying parameter requires a schema migration (rename the field) + frontend update + parity-fixture regeneration.

**Why.** Field names are part of the contract. A field named `high_252d_bps` communicates *"the high of the trailing 252 trading days, in basis points."* Reusing that field name to mean *"the high of a configurable trailing window"* — where the window value changes silently when the YAML is edited — would be a silent contract break for every downstream consumer that reads that field.

**Verify.**
- Output field names that contain a number (`high_252d_bps`, `percentile_252d`) are frozen against changes to the corresponding YAML value; `compute()` raises `NotImplementedError` if the YAML value is changed.
- The `methodology.planned_extensions` block in the relevant primitive's `config.yaml` documents the path to configurability (rename the field via a schema migration, update the frontend, regenerate fixtures).
- New primitives that need configurable windows from the start use methodology-agnostic field names (`high_window_bps` with a sibling `trailing_window_days` field) rather than embedding the window in the field name.

**Anti-patterns.**
- A primitive that changes the meaning of an existing field name in a follow-on PR.
- A primitive that uses methodology-encoded field names (`*_252d`) without the corresponding `NotImplementedError` guard, allowing the YAML value to change silently and the field name to lie.

**Exceptions.** None.

**Relates to.** P3, P4, P5.

### PR15 — Parity-fixture discipline

**Rule.** **Every *new* primitive ships with a parity fixture** in `tests/fixtures/<tool>_v1/` that captures real production output against known inputs. Any change to a `conventions:` value requires fixture regeneration in the *same PR* as the convention change, with a one-paragraph rationale in the PR description.

**Current catalog debt.** Only `tests/fixtures/curve_spread_v1/` exists today; the other 19 existing primitives ship without parity fixtures. Closing this gap is tracked under *Open Questions*. The PR15 rule binds **new primitives from day one**; the existing gap is being remediated incrementally, not by blocking unrelated PRs.

**Why.** Parity fixtures are the regression-detection layer for methodology changes. Without them, a YAML edit can change every output across the catalog and the tests pass because the synthetic mocks don't exercise the convention. With them, every methodology change forces a regenerated diff that the reviewer must inspect.

**Verify (for new primitives).**
- A `tests/fixtures/<tool>_v1/` directory exists with captured raw rows, captured expected output, and a `capture` provenance block (captured_at, database_name, as_of_date, raw_rows_count, raw_rows_sha256).
- The corresponding parity test (typically `test_<tool>_parity.py` or part of the SQL validation file) replays the captured rows through a mocked fetcher, asserts the raw-rows hash matches (tamper detection), and asserts byte-equal output within `1e-9` absolute tolerance.
- Any `conventions:` change is accompanied by a regenerated fixture in the same PR.

**Anti-patterns.**
- A *new* primitive without a parity fixture.
- A `conventions:` change without a regenerated fixture ("we'll regenerate later"). This binds even for primitives that did not historically have a fixture: the convention-change PR is when the fixture is created.
- A regenerated fixture without rationale in the PR description.
- Fixture regenerated to make a failing test pass, with no methodology change justifying it.

**Exceptions.** Primitives with non-deterministic outputs (model fits with stochastic initialisation) may use a *tolerance-based* parity test instead of byte-equal — but the tolerance must be in the test file with a stated rationale.

**Relates to.** P2 (accuracy), P4 (determinism — the parity fixture is the determinism check at the primitive layer).

### PR16 — Test triplet

**Rule.** **Every *new* primitive ships with three test files:**

1. **`tests/test_<tool>_compute.py`** — schema, wiring, config-load, synthetic math correctness. Verifies the primitive runs end-to-end against in-memory fixtures with the bundled config.
2. **`tests/test_<tool>_wiring.py`** — empty-string / None sentinel patterns, controlled error envelopes, the field-name fallback path (None → YAML default).
3. **`tests/test_<tool>_sql_validation.py`** — read-only independent SQL reproduction of the core math against the real DB, in the repo's container/dev environment. Excluded from default pytest collection where the SQL runner is a standalone script.

All three are required before merge for new primitives. Offline tests alone do not satisfy the contract.

**Current catalog debt.** 13 of 20 existing primitives have SQL validation tests today; 7 do not (mostly statistical-fit primitives like `pca_yield_curve`, `rolling_regression`, `half_life`, `zscore_custom`, `beta_adjusted_spread`, `yield_change_attribution_pca` where SQL parity is structurally hard — see the exception clause below). For those, the test contract is met by independently-implemented Python references serving as the parity baseline (per the exception). The PR16 rule binds **new primitives from day one**; the existing gap is acknowledged and remediated when those primitives are next touched.

**Why.** This is the operational test contract behind P2 (accuracy). The compute test verifies the math; the wiring test verifies the seams between Pydantic, config, and external callers; the SQL validation independently reproduces the calculation against real data and catches discrepancies a synthetic mock would never reveal. Each layer catches a different class of bug.

**Verify.**
- All three files exist for the new primitive.
- The compute test fails when `config.yaml` is corrupted or missing.
- The wiring test fails when `field_name: ""` is not handled per the field-name sentinel pattern.
- The SQL validator runs in the container environment, uses only `SELECT` queries (no `INSERT`/`UPDATE`/`DELETE`/`ALTER`), and independently reproduces the core math (not just smoke-tests that the tool returns a value).

**Anti-patterns.**
- Only one or two of the three files.
- A SQL validator that doesn't reproduce the math (just checks the response shape).
- A SQL validator that uses mutating queries.
- Test files exist but pytest skips them silently.

**Exceptions.** Primitives whose core math has no SQL-expressible parity (e.g., statistical fits where the calculation is algorithmic, not aggregational) may substitute an independently-implemented Python reference (e.g., `numpy.linalg.svd` directly, while compute uses a wrapped helper) as the parity baseline. The substitution is documented in the test file.

**Relates to.** P2, P4, P6.

---

## Bucket classification — the methodology-depth axis

The platform defines three buckets, established in the original `tool_architecture.md` doctrine and preserved here. Buckets describe **methodology depth**, not file structure. The four-file shape and every PR-number above apply identically across buckets; only the content inside the files differs.

| Bucket | Definition | Examples in current code |
|---|---|---|
| **1A** | Strict deterministic arithmetic on stored market data; no user-facing model inference; conventions YAML-locked. | `yield_levels`, `curve_spread`, `butterfly`, `cross_market_spread`, `swap_spread`, all OIS arithmetic tools |
| **1B** | Statistical model fits (rolling OLS, PCA, OU / AR(1)) where the user picks a window or factor count but the solver + conventions are YAML-locked; bit-stable on the same input. | `pca_yield_curve`, `rolling_regression`, `half_life`, `beta_adjusted_spread`, `zscore_custom`, `yield_change_attribution_pca` |
| **2** | Open-ended / scenario-generating / model-state tools where the load-bearing output is a fitted model state rather than a value. | None shipped yet. Forthcoming: HMM regime classifiers, term-premium decompositions, dynamic Nelson-Siegel fits, etc. |

The bucket-to-PR mapping is uniform: every primitive regardless of bucket must satisfy PR1–PR16. Bucket 2 introduces one additional convention not yet captured here — *the model state itself is a first-class output, persisted via the artifact store, and the primitive's output is the fit, not just a current snapshot.* This is a content extension, not a structural one. The PR-numbers above apply; when the first Bucket 2 primitive ships, the doc will gain a short subsection that names the model-state output pattern.

**Bucket classification is independent of standardness (PR7–PR11).** A Bucket 1A tool can be non-standard if its methodology is hidden; a Bucket 2 tool can be standard if every model-spec choice is in YAML and every fit parameter is echoed in provenance. Bucket says *how deep is the methodology*; the standardness principles say *regardless of depth, what must hold*.

## Tool category — desk-recognized vs quant-standard

Independent of bucket and orthogonal to standardness, every primitive declares one of two categories in its `config.yaml` `tool.category` field. Both qualify as *standard* if PR7–PR11 hold; the category communicates **how much methodology preface the user needs** to interpret the output.

- **`desk_invariant_primitive`** — a trader on any major desk recognises the tool's name and knows what its inputs and outputs are without a methodology preface. Examples: `curve_spread` ("2s10s"), `yield_levels` ("where's UST 10Y"), `butterfly` ("2s5s10s fly"), `swap_spread` ("BTP-Bund asset swap"). Configuration is calibration, not interpretation.
- **`quant_standard_analytic`** — a textbook quant primitive whose interpretation is universal but whose configuration must be specified before use. A trader recognises the concept; a methodology preface is needed before consuming the output. Examples: `pca_yield_curve` (lookback, change frequency, n_components), `rolling_regression` (window, regressor selection), `yield_change_attribution_pca` (depends on PCA loadings).

The default is `desk_invariant_primitive`; any primitive whose interpretation requires preface must declare `quant_standard_analytic` explicitly. Picking the category by convenience rather than honestly is a PR7-flavoured failure (the chosen category controls how downstream UI surfaces the methodology card).

## Non-standard primitives (coming soon)

The principles above (PR7–PR11) define what *standard* means. There are categories of primitive that legitimately cannot satisfy the standard contract — research-output models where parameters are research deliverables rather than knobs, third-party valuation models whose internals are black-box, single-PM bespoke tools that exist for one workflow rather than the catalog, and so on.

A non-standard category, its admission tests, its UI surfacing rules, and the discipline for how non-standard primitives may (or may not) be consumed by standard ones is a planned extension of this contract. **Until that section opens, every primitive that ships is held to PR7–PR11.** Contributors who believe their primitive genuinely cannot meet the standard contract should pause and surface the case to the human owners rather than ship a non-standard primitive under a standard label.

---

## Worked examples — how the principles manifest in actual primitives

The shapes you see in current code are emergent patterns of how the principles apply against current concepts and current data. Eight examples below — each names which PR-numbers governed its design choices.

### Single-instrument level + window — `yield_levels`

The simplest shape. One instrument selector (`curve_family`, `tenor`), one central knob (`lookback_days` for display), full snapshot + canonical `TimeSeries` output.

- **PR1** holds: the primitive takes any (curve_family, tenor) pair across the sovereign universe.
- **PR2, PR5**: one concept (current yield level + rolling stats), genuinely distinct from `curve_spread` (which is between two tenors).
- **PR7, PR8**: every methodology choice (z-score window, ffill limit, rounding) is YAML-locked; `lookback_days` is the central knob, controlling *display window only*, not the rolling-stat window.
- **PR16**: ships with `test_yield_levels_compute.py`, `test_yield_levels_wiring.py`, `test_yield_levels_sql_validation.py`.

### Multi-leg spread — `curve_spread`, `butterfly`

Two or three legs of the same curve, spread arithmetic plus rolling z-score. The single most common shape in the catalog.

- **PR1, PR5**: `curve_spread` is the concept "spread between two tenors on a curve" — works on UST 2s10s, Bund 5s30s, JGB 10s20s — any tenor pair on any sovereign curve.
- **PR2**: one concept (a spread); the related concept of a 3-leg butterfly is a *different* primitive (`butterfly`), not a flag on `curve_spread`.
- **PR4**: the parsimony argument for `butterfly` (vs composing `curve_spread`s with an operator) was the **interpretability** and **LLM tool-selection clarity** criteria — a desk asks for "the 2s5s10s fly" as a single concept, and the LLM should route to one tool, not assemble a DAG.
- **PR14**: butterfly's output has `high_252d_bps`, `percentile_252d_bps` — methodology-encoded field names guarded by `NotImplementedError` if the underlying window is changed.

### Statistical decomposition (Bucket 1B) — `pca_yield_curve`

A statistical model fit where the *user's central knob defines the model itself*: `n_components`, `change_frequency`, `tenors` are inputs because choosing them is choosing what model is being fit.

- **PR8**: the central knob is the model-spec choice. Unlike level/spread primitives where the knob is a display window, here the knob defines the underlying calculation.
- **PR7**: structural choices (`sign_anchor = "level"`, `add_constant`, the SVD solver) are YAML-locked and guarded by `NotImplementedError` if a caller tries to vary them.
- **PR11** *is not used* (this is single-method), but the structural locks are conceptually similar — explicit refusal rather than silent fallback.
- The methodology preface need explains the category — `quant_standard_analytic`, not `desk_invariant_primitive`.

### Composition primitive — `yield_change_attribution_pca`

The canonical example of composition. The primitive attributes a yield change across PCA components; it either fits the PCA inline or accepts caller-supplied loadings.

- **PR9 (strict) — partial satisfaction; documented debt.** The `<Tool>Input` includes a `loadings_source` discriminator. When `loadings_source="fit_inline"`, the *core* PCA spec parameters (`pca_lookback_days`, `n_components`, `change_frequency`, `tenors`) are surfaced as inputs — the user can override the structural model choices. When `loadings_source="pasted"`, the caller supplies the upstream provenance and the primitive validates it. **However**, the primitive does *not* yet satisfy the strict form of PR9: at [`compute.py:475`](../../rates_agent/sovereign_bonds/tools/yield_change_attribution_pca/compute.py) it calls `calculate_pca_yield_curve(engine=engine, params=pca_input)` without an explicit upstream `config=` argument (relying on pca_yield_curve's auto-load), and its `config.yaml` does not surface or pin every PCA convention (e.g., `sign_anchor`, `degenerate_variance_share_threshold`, `factor_round_decimals`). This is documented debt; the catalog-wide review will either tighten this primitive to strict PR9 or relax PR9 to "material to B's output" — whichever the review concludes.
- **PR10**: the output does echo the upstream choices that *are* surfaced (loadings_source, fit window, sign anchor used, variance shares per component, degenerate-component flags). The fields the primitive does not control today are not echoed.
- This is the pattern any *new* composition primitive must follow to remain standard — but it must close the gap this primitive has by passing explicit upstream config and surfacing every upstream convention.

### Panel-producing primitive — `sovereign_yield_panel`

Output shape differs from the snapshot+TimeSeries norm. Input is a *list* of leg specs; output is a `Panel` artifact (wide-format multi-series), not a single annotated series.

- **PR1, PR5**: one concept (a panel of sovereign yields across a user-specified leg set), distinct from `yield_levels` (one instrument at a time).
- **PR4**: the parsimony argument was *efficiency* — composing `yield_levels` over N legs and aligning the results would be N round-trips plus an alignment operator. The direct primitive does it in one query.
- **Output-shape implication**: downstream consumers must handle a panel, not a single series. The primitive declares this in its output schema.

### Categorical-output primitive — `curve_move_classifier`

Output is a classification enum (BULL_STEEPENER, BEAR_FLATTENER, PARALLEL_SHIFT, TWIST) plus supporting numeric evidence. Downstream consumers must branch on the enum.

- **PR2**: one concept (the curve-move regime label), not the spread itself (which is a separate primitive).
- **PR7**: the decision thresholds (`parallel_threshold_bps`, `move_threshold_bps`) live in YAML; the classification logic in code is straight conditionals on those thresholds.
- **Output-shape implication**: downstream operators consuming this output need to branch on a string, not interpolate a number. This is a real constraint on workflows that compose `curve_move_classifier` with downstream operators.

### Multi-method / partial-implementation primitive — `financing_rate`

The canonical example of PR11 using the *error-envelope* refusal mechanism. Accepts a `method` enum with four values (`constant_rate`, `overnight_index_proxy`, `term_repo_curve`, `gc_special_blend`). Only two are implemented in V1.

- **PR11 — envelope mechanism.** The two unimplemented methods (`term_repo_curve`, `gc_special_blend`) return a controlled error envelope: `return {"error": "method=term_repo_curve is declared in the closed-enum but V1 raises NotImplementedError — real term-repo data is not yet ingested. See config.yaml methodology.planned_extensions."}` (see [`compute.py:77`](../../rates_agent/ois/tools/financing_rate/compute.py)). The envelope satisfies PR11 because the refusal is loud (an error envelope, not a silent fallback), specific (names the missing data), and actionable (points at `planned_extensions`). The implemented methods are never invoked as a fallback. The wrapper at the MCP boundary converts the envelope into the transport-layer error per P6's transport-boundary rule.
- **PR2**: the four methods are different *methodologies for the same concept* (financing-rate computation), not different concepts. This is the test that keeps it a single primitive rather than four.
- The Pydantic `@model_validator` enforces "exactly one method's required params supplied" before `compute()` runs, so the validation error fires at the API boundary, not deep inside compute.

A *new* multi-method primitive may use either the `raise NotImplementedError(...)` mechanism or the envelope mechanism (per PR11), but must pick one and stay consistent.

### Cross-domain primitive — `swap_spread`

Lives in `rates_agent/ois/tools/`, computes `(sovereign_yield - ois_rate) × 100` in bps. The canonical example of PR3.

- **PR3**: asset-swap-spread is conventionally an OIS-desk concept (OIS traders ask "what's the BTP-Bund swap spread?"), so the primitive lives in OIS even though it reads sovereign yields. The OIS agent's MCP server exposes it; the OIS supervisor knows to route to it.
- The cross-domain data fetching goes through `shared/analytics/rates_fetch.py:fetch_cross_domain_pair`, not a direct `from rates_agent.sovereign_bonds...` import.
- A `grep` for `from rates_agent.sovereign_bonds` inside `rates_agent/ois/tools/swap_spread/compute.py` is empty.

---

## Anti-patterns (catalogue-wide)

Auto-reject in review:

- **Instrument-named primitives** (`get_ust_2s10s`, `calculate_bund_5y_zscore`). Violates PR1.
- **Multi-concept primitives** (`analyze_curve` that returns yields or spreads or butterflies depending on input shape). Violates PR2.
- **A primitive whose `compute()` imports from another agent's package.** Violates P11 and PR3.
- **A primitive whose `methodology.what_it_does` uses the word "or"** to describe what it computes. Likely PR2 violation.
- **A primitive that ships under a desk-recognized name with a proxy assumption.** Violates PR6 and P12.
- **A primitive whose central knob is `rounding_decimals`.** Violates PR8.
- **A composition primitive whose config is silent on upstream methodology.** Violates PR9.
- **An output that returns a number with no methodology trail.** Violates PR10 and P5.
- **An unbuilt method that silently falls back.** Violates PR11 and P6.
- **A `Convention.source: "default"`.** Violates PR12.
- **A new primitive whose PR description does not address PR4 (parsimony) and PR5 (concept novelty) explicitly.** The reviewer should bounce the PR until those questions are answered.
- **A primitive that ships without a parity fixture or without the test triplet.** Violates PR15 and PR16.
- **An LLM-routing-overlap with an existing primitive** ("we have z-score-of-spread and rolling-standardised-spread doing nearly the same thing"). Violates PR4's tool-selection clarity criterion.

## Open questions and known gaps

These are documented gaps to address as the catalog grows.

1. **PR9 strictness review.** Strict for now. A catalog-wide review of every composition primitive — once we have more than one — will draw the materiality line and relax PR9 to "every methodology choice that is material to B's output." Until that review, the strict form binds.
2. **Non-standard primitive category.** The escape hatch for genuinely opinionated tools is not yet open. Until it is, primitives that cannot meet PR7–PR11 are deferred, not relabelled.
3. **Bucket 2 conventions.** The first Bucket 2 primitive will introduce the *model-state artifact* output pattern; this contract will need a short subsection naming the pattern when that primitive ships.
4. **Strict source-tag enforcement.** PR12 today is enforced socially (code review + lint catches obvious vagueness). The planned tightening makes `Convention.source` a strict enum validated by `ToolConfig`.
5. **Field-name configurability migration path** (PR14). Several existing primitives have methodology-encoded field names (`high_252d_bps` etc.) guarded by `NotImplementedError`. The migration to configurable windows is documented in each primitive's `methodology.planned_extensions` but has not been planned as a catalog-wide effort.
6. **LLM-routing overlap detection.** PR4 names the criterion but the operational check is manual today (the reviewer compares one_liners). An embedding-similarity check against the existing catalog could be added to CI to flag near-duplicates automatically.
7. **Cross-config consistency groupings.** PR13's lint allows legitimate divergences via explicit grouping. New groupings should require an ADR; today the lint config is edited directly.

## Citation cheat sheet

| Use | Pattern |
|---|---|
| In a commit message | `feat(yield_levels): expose lookback_days as central knob per PR8` |
| In a PR review comment | `This violates PR4 — the same output can be produced by composing curve_spread + threshold_events.` |
| In a code comment (rare) | `# PR11: term_repo_curve method blocked on repo-rate data ingestion` |
| In a runbook step | `Step 4 — verify PR7 (no methodology constants in compute.py) and PR9 (composition inheritance).` |
| In an ADR | `This decision relaxes PR9 from "every methodology choice" to "every material methodology choice" per the catalog-wide review.` |
| In a refusal message | `(internal: refusal under PR11 — method not yet implemented; see methodology.planned_extensions.)` |

## Changing a primitive principle

Same discipline as the platform-wide principles in [`../../00_thesis/01_non_negotiables.md`](../../00_thesis/01_non_negotiables.md):

1. Open an ADR in [`../../05_decisions/`](../../05_decisions/) describing the proposed change and its consequences for existing primitives.
2. Land the ADR and the contract change in the same PR.
3. Bump the version of this file. The version log records every change. Pre-canonical revisions (before the first ADR adoption) are recorded in the log without a corresponding ADR id.

This contract is intentionally stable. New primitives compound onto it; existing primitives are inspected against it; the LLM router relies on it. Slow change is the right shape.

## Version log

| Version | Date | Change | ADR |
|---|---|---|---|
| v1.1 | 2026-05-17 | Pre-canonical factual corrections, each re-verified against the cited source files: (a) **PR8** reworded from "exactly one central methodological choice" to "one cohesive central methodology surface" — matches the existing A13 doctrine and reconciles with `pca_yield_curve` / `rolling_regression` which expose a cohesive 2-3 field surface, not a single field. (b) **What a primitive IS** now separates *primitive output shapes* (four exist in code: snapshot+TS, Panel, statistical-fit, categorical) from *workflow-bridge artifact shapes* (only Series and Panel are bridge-composable in v1; categorical primitives are valid standalone but not bridge-composable). (c) **PR9** adds explicit "Grandfathered debt" clause naming `yield_change_attribution_pca`'s gap (calls upstream without explicit `config=`, doesn't surface every PCA convention); strict PR9 binds new composition primitives, not existing ones until the catalog-wide review. (d) **PR10** restructured around *reachability* not *always-in-output*: Bucket 1A primitives with YAML-locked conventions satisfy PR10 via config+lineage+methodology card; composition / pasted-input / statistical-fit / non-obvious-methodology primitives must surface provenance in the output payload. (e) **PR11** allows both refusal mechanisms (`raise NotImplementedError` and `return {"error": ...}` envelope); names `financing_rate`'s envelope pattern as a valid satisfaction. (f) **PR13** removes the "explicit groupings" claim — the live lint compares globally by convention name; the right disambiguation today is naming. (g) **PR15 + PR16** marked as binding for new primitives with explicit "Current catalog debt" notes — only `curve_spread` has a v1 fixture; 7 of 20 primitives lack a SQL validation test (those are statistical-fit primitives where SQL parity is structurally hard). (h) **Worked examples** updated: `yield_change_attribution_pca` flagged as PR9-debt; `financing_rate` reframed as PR11-via-envelope example. (i) **Paths corrected**: `07_decisions/` → `05_decisions/`; `04_standards/methodology_disclosure.md` → `03_standards/methodology_disclosure.md` (the docs tree was renamed during the revamp; this file referenced the old paths). | (pending) |
| v1 | 2026-05-17 | Initial primitive contract. Replaced by v1.1 the same day after a factual-review pass against the source code. | — |
