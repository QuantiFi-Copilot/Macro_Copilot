# Operator

> The contract every operator in the Macro Copilot platform must satisfy — what makes something an operator at all, what makes it *standard*, and the design principles that govern when to build one (and when to compose existing ones instead). **Finance-blind by design** — the operator layer carries no instrument knowledge, no asset-class enums, no domain references; operators are the structural transformations that domain primitives feed into and that workflow templates compose.

**Version:** v1.1
**Last reviewed:** 2026-05-17
**Status:** load-bearing component contract. Changes require an ADR in [`../../05_decisions/`](../../05_decisions/).
**Operationalises principles:** P1 (future-proofed), P3 (consistency by contract), P4 (determinism), P6 (no silent failure), P8 (closed-family discipline — operators consume and emit only closed-family artifact types), **P9 (finance-blind operator boundary — this contract is the operational expression of P9 at the operator layer)**, P10 (single source of truth).
**See also:** [`runbook.md`](runbook.md) — the procedure for adding a new operator.

---

## What this folder is

The contract every operator must satisfy. Operators are the L3 finance-blind structural transformation layer: they consume typed artifacts produced by primitives or by other operators, apply a single structural method (alignment, masking, windowing, aggregation, ranking, arithmetic, fan-out), and emit a typed artifact downstream. They are the *only* place in the platform where pure structural compute lives.

This document is organised as **principles, not archetypes** — same as the [primitive contract](../primitive/README.md). The patterns you see in current code (~12 operators across half a dozen method families) are *emergent* from the principles; new method families will arrive without restructuring the architecture. The 16 numbered principles (**OPR1–OPR16**) below are the operator-specific analog of the primitive's PR-numbers, grouped in the same four bins: definitional (OPR1–OPR3, *is this actually an operator?*) → admission (OPR4–OPR6, *should this be built, and as a shared operator?*) → standardness (OPR7–OPR11, *what does "standard operator" mean operationally?*) → operational (OPR12–OPR16, *the build conventions every standard operator follows*).

A note on relationship to the primitive contract: many primitive principles have an operator analog, but **the mappings are not identical** — operators differ from primitives in four load-bearing ways:

1. Operators are **finance-blind** (the single most important rule). Primitives carry domain knowledge; operators forbid it.
2. Operators are **pure functions over typed artifacts** — no `engine`, no DB, no I/O. Primitives read from L1; operators only read what's already been read.
3. Operators **extend** lineage rather than **originate** it. Primitives start a lineage chain; operators add their `OperatorStep` to an existing one.
4. Operators have a **promotion rule** — a new operator is only admitted to the shared catalog when it is reused across ≥3 workflow archetypes OR is algebraically foundational to the operator algebra. Primitives have no equivalent (every primitive is a primitive from day one).

Each principle below cites the primitive analog (if any) and names where the two diverge.

## What an operator *is* — the universal contract

Every operator is a folder under `shared/operators/<operator_name>/` containing exactly four files:

```
shared/operators/<operator_name>/
  __init__.py     # public-API re-exports (CONFIG_PATH, <operator>, <Operator>Params, <Operator>Error)
  config.yaml     # operator meta + defaults + methodology
  schemas.py      # Pydantic <Operator>Params
  operator.py     # def <operator_name>(<artifact inputs>, params=None, config=None) -> <Output Artifact>
```

Same number of files as the primitive shape, with one file substituted:

- **`operator.py` instead of `compute.py`.** The naming difference reflects what the file *is*: an operator's compute step *is* the operator. There is no separate fetch step because operators don't read from L1 — that's the primitive's job.

Two parameter layers feed every operator call:

| Layer | Lives in | Per-call or constant? | Who controls? |
|---|---|---|---|
| **Params** | `<Operator>Params` Pydantic model | per-call | The workflow template (or test caller) |
| **Defaults** | `config.yaml` `defaults:` block | system constants | YAML-locked in V1; not overridable by LLM (operators are not LLM-routed; workflow templates choose them) |

Note the naming difference from primitive: operators have `defaults:` blocks (not `conventions:`), and `<Operator>Params` (not `<Tool>Input`). The semantics are analogous — `defaults` are the convention-style declarations every operator carries — but the chosen vocabulary is operator-specific because the consumers are different: primitives are LLM-routed via MCP, so the input is the LLM's "input"; operators are workflow-routed, so the input is the workflow's "params."

The `operator()` signature is canonical, no exceptions:

```python
def <operator_name>(
    <artifact_input_1>: <ArtifactType>,
    <artifact_input_2>: Optional[<ArtifactType>] = None,
    ...,
    params: <Operator>Params | None = None,
    config: OperatorConfig | None = None,
) -> <OutputArtifactType>:
    if config is None:
        config = load_operator_config(CONFIG_PATH)
    if params is None:
        params = <Operator>Params()  # uses Pydantic defaults
    # ... validate structural metadata compatibility (frequency, units, missingness) ...
    # ... apply the structural transformation ...
    # ... extend lineage with OperatorStep ...
    return <output_artifact_with_extended_lineage>
```

**No `engine` parameter.** Operators do not talk to the database. They are deterministic functions over typed artifacts.

**Inputs and outputs are typed artifacts**, drawn from the *current* closed family: `Series`, `SeriesSet`, `EventSet`, `Panel`, `WindowedPanel`, `TradeSet`. (Two additional shapes — `ScalarMetric` and `RankedResult` — are mentioned in earlier design notes but are **not** yet in the executable closed family. `ScalarMetric` is explicitly deferred to a later phase; `RankedResult` is not yet registered. New operators must use one of the six currently-supported types.) The artifact type contract is owned by `02_components/artifact/` (forthcoming) and is closed-family by P8 — adding a new artifact type is an ADR-gated decision.

**Errors raise typed exceptions** (e.g., `AlignSeriesError`, `ThresholdEventsError`), subclasses of `ValueError`. Operators never return `{"error": ...}` envelopes — that envelope conversion happens at the transport boundary (MCP server, workflow executor's exception handler). The operator layer is deep inside the workflow stack; raising is correct here.

## What an operator is *not*

Five boundary statements that prevent the most common misclassifications:

- **Not finance-aware.** An operator does not know what an instrument is, what a curve_family means, what a tenor represents, or what asset class its inputs come from. If your candidate operator's signature contains `curve_family`, `tenor`, `bond`, `swap`, `OIS`, `Treasury`, or any other finance concept, it is not an operator — it is a primitive or a workflow template component.
- **Not a data reader.** Operators have no `engine` parameter, no SQL, no network call, no file I/O (except reading their bundled `config.yaml` at startup). Primitives read from L1; operators only transform what primitives produced.
- **Not a workflow.** A single operator is one structural transformation. Multi-step analyses are workflow templates that compose operators and primitives (L4/L5), not operators with multi-stage internal logic. If your candidate "operator" is really a sequence of three operators glued together, it is a workflow template.
- **Not a UI / explanation step.** Operators emit typed artifacts; they do not produce prose, formatted text, or rendered output. Anything that produces user-facing text belongs at the UI / orchestrator layer.
- **Not lineage-originating.** Operators continue a lineage chain by appending an `OperatorStep`. They do not start one. Lineage origination is the primitive's job (via `PrimitiveStep`).

## How to use this document

For your first read: scan the **Quick index** below, then read every principle's **Rule** line. That alone gives you the whole spec. The **Why**, **Verify**, and **Anti-patterns** sections are reference material for when a question or a PR turns on a specific principle.

For ongoing work: do not re-read this file from top to bottom. Look up the specific principle by ID when it comes up. Cite by ID (`OPR6`, `OPR9`) in commit messages, PR comments, and code review — same discipline as the P-numbers from [`../../00_thesis/01_non_negotiables.md`](../../00_thesis/01_non_negotiables.md) and the PR-numbers from [`../primitive/README.md`](../primitive/README.md). The three namespaces are deliberately separate: P-numbers are platform-wide; PR-numbers are primitive-specific; OPR-numbers are operator-specific.

## Quick index — the OPR-numbers

| ID | Group | Principle | One-line rule |
|---|---|---|---|
| **OPR1** | I | Structural-method ownership | An operator owns one structural method family (alignment, masking, windowing, aggregation, ranking, arithmetic, fan-out). It never owns a finance concept. |
| **OPR2** | I | One operator, one method family | An operator expresses one cohesive structural method family with its consequential variants exposed as parameters. Multiple unrelated method families split into multiple operators. |
| **OPR3** | I | Shared-folder residence | An operator lives at `shared/operators/<operator_name>/`. Never under any agent. The shared folder *is* the finance-blind residence. |
| **OPR4** | II | Parsimony + promotion rule | Do not build a new operator if existing operators compose to the same effect. A new operator is admitted to the shared layer only when (a) the composability argument fails on accuracy / efficiency / interpretability / provenance / workflow-template clarity grounds, AND (b) the candidate is reused across ≥3 workflow archetypes OR is algebraically foundational to the operator set. |
| **OPR5** | II | Concept novelty | A new operator is a genuinely new structural method family or a genuinely new variant inside an existing family, not a one-off transform that exists for a single workflow. |
| **OPR6** | II | Asset-class / domain-blind contract | The operator does not know the asset class of its inputs. Generic trading concepts (trade, holding window, P&L) are allowed; asset-class-specific concepts (curve_family, tenor, FX pair, equity sector) are forbidden. The operator must run unchanged on rates, FX, equities — its code does not change when the asset class does. |
| **OPR7** | III | Defaults offloading | Every methodology default lives in `config.yaml`'s `defaults:` block; mathematical and structural invariants stay in code; no hidden module-level constants. |
| **OPR8** | III | Bounded parameter surface with explicit variants | `<Operator>Params` exposes every consequential method variant the caller can pick (aggregator, threshold rule, window shape, alignment policy, ranking direction). No hidden hardcoded choices the caller cannot inspect or override. |
| **OPR9** | III | Typed artifact I/O over the closed family | Inputs and outputs are drawn from the closed-family artifact set (`Series`, `SeriesSet`, `EventSet`, `Panel`, `WindowedPanel`, `TradeSet`). No naked pandas / NumPy, no dicts, no prose. |
| **OPR10** | III | Lineage extension | Every operator extends the artifact's lineage chain with an `OperatorStep` carrying its name, version, parameters, and policy choices. Operators do not originate lineage; they continue it. |
| **OPR11** | III | Structural-metadata enforcement and honest refusal | Operators enforce structural metadata compatibility (frequency, units, missingness policy, index type) by default. Mismatches raise typed exceptions (`<Operator>Error`). Callers may opt into mixed inputs explicitly; the choice is recorded in lineage. |
| **OPR12** | IV | Source-tagged defaults | Every `Convention` / `Default.source` references a registered tag. The operator-default taxonomy currently includes `operator_v1_default`, `derived_from_window`, `team_judgment_pending_review`. New tags require a one-line registry entry. |
| **OPR13** | IV | Typed exceptions, not envelopes | Operators raise typed `<Operator>Error` exceptions on failure. The envelope conversion (`{"error": "..."}`) happens at the transport boundary, never inside the operator. |
| **OPR14** | IV | Pure function, no I/O | No DB access, no network, no filesystem except the bundled `config.yaml`. Deterministic on inputs. No wall-clock time, no unfixed random seeds. |
| **OPR15** | IV | Arity declared in the registry; list slots are first-class | Every operator's arity is declared in `OperatorSpec.input_slots`. List-typed slots (`"List[Series]"`) are first-class and validated by the substrate. No implicit fan-out via `Union[T, List[T]]` branching inside the operator. |
| **OPR16** | IV | Test pattern | Every operator ships with a unit test against synthetic typed-artifact inputs (covers happy path + edge cases + structural-metadata mismatches), and at least one workflow-integration test through a real workflow template that consumes it. No SQL validation (no DB). |

---

## Group I — Definitional: is this actually an operator?

The first question. If the answer to any of OPR1–OPR3 is "no, this is not an operator," the conversation stops here and the proposal goes to a different shape (a primitive, a workflow template, a UI helper, or a `shared/analytics/` helper).

### OPR1 — Structural-method ownership

**Rule.** An operator owns one *structural method family*. Examples of families currently in code: alignment (`align_series`), masking (`apply_mask`), thresholding (`threshold_events`), windowing (`event_windows`), aggregation (`conditional_aggregate`, `summarize_series`, `summarize_trades`), arithmetic (`series_arithmetic`), selection (`select_from_series_set`), construction (`construct_trades`), evaluation (`evaluate_trades`). New operators add to this list of structural method families; they do not own finance concepts.

**Why.** This is what makes operators reusable across asset classes. An operator named `align_series` runs on rate yields, FX spot rates, equity prices, and temperature time series unchanged — because alignment is a structural operation, not a finance operation. The moment an operator carries a finance concept, the substrate's cross-asset claim collapses (per [P9](../../00_thesis/01_non_negotiables.md)) and the operator becomes a hidden primitive.

**Verify.**
- The operator's `tool.name` and `methodology.what_it_does` describe a *structural method*, not a finance concept.
- The operator's name, signature, config, and docstrings do not mention: instrument, asset class, curve_family, tenor, sovereign, bond, swap, OIS, Treasury, equity, currency, yield, spread, rate, or any other finance term except as documentation of what the artifact's units happen to be in *this* call (which is data, not code).
- A reviewer can describe the operator in one sentence using only structural terms: *"`align_series` combines N indexed Series onto a common index under a join policy."*

**Anti-patterns.**
- `calculate_swap_spread` — knows finance (sovereign yield vs OIS rate). Correct home: a cross-domain primitive (already shipped as such).
- `find_curve_inversions` — domain-specific curve semantics. Correct home: a primitive or workflow template.
- `regress_breakeven_on_oil` — workflow logic. Correct home: a workflow template.
- `format_event_study_report` — UI / explanation. Correct home: UI / orchestrator layer.
- `conditional_mean` as a standalone operator — too narrow a variant of the broader `conditional_aggregate` family. Either generalise (expose `aggregator: Literal["mean", "median", ...]`) or fold into the existing operator.

**Relates to.** OPR analog of [PR1](../primitive/README.md#pr1--concept-ownership-not-instrument-ownership), but the axis is different: primitives own finance concepts (parameterised by instrument); operators own structural methods (parameterised by metadata). [P9](../../00_thesis/01_non_negotiables.md) is the platform-level principle this operationalises.

### OPR2 — One operator, one method family

**Rule.** An operator expresses one cohesive structural method family. *Inside* that family, consequential method variants are exposed as caller-controlled parameters (e.g., `align_series` exposes `join_policy: Literal["inner", "outer"]` and `fill_policy: Literal["raw", "ffill"]` because both are legitimate variants of alignment). *Across* unrelated method families, split into multiple operators.

**Why.** Tool-selection clarity for workflow-template authors (the operator-layer analog of LLM tool-selection clarity for primitives) and reviewer sanity. An operator that does two structural things is two operators in a trench coat; templates can't tell which "mode" they're invoking, the contract becomes ambiguous, and downstream consumers can't reason about what they're calling.

**Verify.**
- The operator's `methodology.what_it_does` describes *one* method family in one paragraph.
- There is no `mode: Literal["align", "threshold"]` parameter that switches between *different* method families.
- The output artifact type is consistent across all parameter values (an operator that returns `SeriesSet` for some inputs and `EventSet` for others is doing two operators' work).

**Anti-patterns.**
- An operator named `transform_series` that does alignment, thresholding, *or* arithmetic depending on a mode flag. Three operators, not one.
- An operator that switches its output artifact type based on a param value.
- An operator whose `what_it_does` requires the word "or" to describe what it produces.

**Exceptions.** None.

**Relates to.** OPR analog of [PR2](../primitive/README.md#pr2--one-primitive-one-concept). For operators, the "one concept" maps to "one structural method family with explicit variants" — variants inside the family are required (not forbidden), as long as each is a legitimate methodology choice within the family, not a different family.

### OPR3 — Shared-folder residence

**Rule.** Every operator lives at `shared/operators/<operator_name>/`. Operators are never placed under an agent's package, never under `shared/analytics/`, never under a domain-specific subdirectory of any kind. The `shared/operators/` folder *is* the finance-blind residence.

**Why.** This is the structural enforcement of OPR6 (finance-blind contract). Per [P11](../../00_thesis/01_non_negotiables.md), agents are isolated; per [P9](../../00_thesis/01_non_negotiables.md), operators are finance-blind. The two together force the conclusion: an operator under any agent's folder would either be domain-coupled (violating P9) or wrongly placed (violating P11). The shared folder is the only correct home.

The contrast with primitives is sharp:

- Primitives live under `<agent>/<sub_agent>/tools/<tool_name>/` because they own a desk concept, which has conventional ownership.
- Operators live under `shared/operators/<operator_name>/` because they own a structural method, which has no conventional owner.

**Verify.**
- The operator's folder path begins with `shared/operators/`.
- The operator's `operator.py`, `schemas.py`, and `config.yaml` do not import from any agent's package. A `grep` for `from rates_agent`, `from fx_agent`, etc., inside any operator file returns zero matches.
- The operator's `shared/analytics/` imports (if any) are limited to truly finance-blind helpers — `shared/analytics/stats.py` (yes), `shared/analytics/rates_fetch.py` (no, that's domain-coupled).

**Anti-patterns.**
- An "operator" placed under `rates_agent/operators/` or any agent-scoped folder.
- An operator that imports from any agent's tools or schemas.
- A "shared but rates-only" operator. There is no such thing; either it's finance-blind (and lives in `shared/operators/`) or it's a primitive (and lives in an agent).

**Exceptions.** None.

**Relates to.** OPR analog of [PR3](../primitive/README.md#pr3--domain-residence-by-conventional-ownership), but the rule inverts: primitives live where the *desk concept* conventionally lives; operators have *no* domain, so they live in the shared root by definition. The two rules together preserve [P9](../../00_thesis/01_non_negotiables.md) and [P11](../../00_thesis/01_non_negotiables.md).

---

## Group II — Admission: should this be built, and as a shared operator?

OPR1–OPR3 said the candidate *is* an operator. Group II asks whether it *should* exist as a shared admission, and what bar it has to clear.

### OPR4 — Parsimony + the promotion rule

**Rule.** Do not build a new operator if existing operators compose to the same effect. The default action when a workflow needs a transformation that maps to a chain of `align_series → threshold_events → event_windows → conditional_aggregate` is **compose**, not **build a new operator**.

A new operator is admitted to the shared catalog **only when both of these hold**:

1. The composability argument fails on at least one of these grounds:
   - **Accuracy.** The composition accumulates error that a direct operator avoids.
   - **Efficiency.** The composition would require many intermediate artifact materialisations; a direct operator does it in one pass.
   - **Interpretability.** The composition produces intermediate artifacts whose *meaning* a workflow-template reviewer cannot follow; the direct operator produces a coherent end-to-end transform.
   - **Provenance.** The composition produces a lineage chain too noisy to audit; the direct operator carries a single coherent `OperatorStep`.
   - **Workflow-template clarity.** The composition requires every template that uses it to repeat the same 4-node sub-DAG; a single operator makes the templates simpler and the operator's role auditable.

2. The candidate satisfies the **promotion rule** — at least one of:
   - It is **reused across ≥3 distinct workflow archetypes** (event_study, regime_conditioned_relationship, attribution_decomposition, cross_sectional_screen, backtest, etc.), or
   - It is **algebraically foundational to the operator set** — meaning no composition of existing operators reproduces its effect cleanly, and the reviewer can name what the operator gives the operator algebra that the existing set does not.

Promotion is reviewed against the existing operator catalog by a reviewer; it is **not self-declared by the author**.

**Why.** Two compounding effects:

1. Every operator added to `shared/operators/` is a maintenance, test, and review burden that compounds across every workflow that touches it.
2. Operators are the *building blocks* templates compose. A bloated operator catalog makes templates harder to read (more operators to choose between) and the reviewer's job harder (more shapes to remember). The catalog must stay small enough that a human can hold the full operator vocabulary in their head — that's the bar.

The promotion rule exists because the operator layer is *the* place where parsimony has compounding leverage: a primitive used by one workflow is fine; an operator used by one workflow is debt. Without the rule, the operator folder becomes a wastebasket.

The five-criterion admission gate (the same five we use for primitives in PR4) and the promotion rule are **both required** — passing one without the other is not enough.

**Verify.**

The PR description must answer three questions explicitly:

1. **Composability check.** What composition of existing operators was considered? Why is it materially worse against ≥1 of the five criteria?
2. **Promotion check.** Which ≥3 workflow archetypes will use this operator? Name them. Alternatively, name the algebraic foundation the operator adds that no composition produces cleanly.
3. **Non-overlap check.** Run the proposed operator's `name` and `methodology.what_it_does` past every existing operator's `name` and `what_it_does`. Is there an existing operator whose scope could plausibly cover this? If yes, the right answer is usually to *extend the existing operator with a new variant* (e.g., add a new `method_family` enum value, or a new `aggregator` value), not add a sibling.

**Anti-patterns.**
- "We need it for one workflow." Build it as workflow-local code; promote later if a second workflow needs it.
- "It's faster than the composition" — without measuring how much faster, and whether the difference matters at template-execution rates.
- Building `aggregate_with_mean`, `aggregate_with_median`, `aggregate_with_max` as three operators. One operator (`conditional_aggregate`) with an `aggregator` variant.
- Building two operators whose `methodology.what_it_does` are near-paraphrases of each other.
- Speculative additions ("we might need this someday"). Build when a real third workflow archetype is queued.

**Exceptions.** Two narrow ones:

1. **Bootstrap exception (ADR-backed).** New asset-class or new workflow-archetype work sometimes needs a new operator before the third archetype consumer exists — the operator can't reach three consumers if no consumer can start until the operator exists. For these cases, the operator may be admitted with a single (planned) consumer **provided an ADR explicitly records the bootstrap status and names the expected ≥3-archetype timeline**. The bootstrap status is revisited at the next phase boundary; if the third consumer hasn't materialised, the operator is reviewed for either promotion (consumer #3 lands) or demotion to workflow-local code.
2. **Algebraic foundation.** As stated in the promotion rule itself — an operator that is algebraically foundational to the operator algebra does not require ≥3 archetype reuse, but it does require reviewer confirmation that no composition of existing operators reproduces its effect cleanly.

A workflow may also need workflow-local helper code that is not a shared operator; that's fine — but it lives inside the workflow template's package, not in `shared/operators/`.

**Relates to.** OPR analog of [PR4](../primitive/README.md#pr4--parsimony-the-composability-check--llm-tool-selection-clarity), but with the **promotion rule** baked in — operators have a tighter admission bar than primitives because every operator added is debt amortised across the whole template catalog. The five admission criteria substitute "workflow-template clarity" for "LLM tool-selection clarity" (operators are not LLM-routed directly; they are template-routed).

### OPR5 — Concept novelty

**Rule.** A new operator is a *genuinely new structural method family* or a *genuinely new variant inside an existing family*. It is not:

- a new finance use case for an existing structural method (use the existing operator with the new artifact type as input),
- a new universe member of an existing operator (universe expansion is a primitive concern, not an operator concern),
- a new workflow step that happens to be composable (workflow steps are template components, not operators).

**Why.** The most common reason an operator proposal is mis-classified as new is that the contributor sees a *new analytical use case* and reaches for *a new operator*, when the right answer is to use an existing operator with the new artifact type or a new parameter value.

**Verify.**
- The PR description names the structural method family (one of the existing families, or a genuinely new one with a one-paragraph justification).
- The reviewer can name an existing operator whose `method_family + variants` *could not* cover the new use case under any parameter value.

**Anti-patterns.**
- A PR titled `feat: add align_series for cross-currency-basis workflows` (the existing `align_series` is already finance-blind and handles any artifact type).
- A PR that adds a new operator whose `methodology.what_it_does` is "same as `<existing_operator>` but for `<new_use_case>`."
- A PR whose only diff from an existing operator is a different default value of a YAML default.

**Exceptions.** None.

**Relates to.** OPR analog of [PR5](../primitive/README.md#pr5--concept-novelty). For operators the bar is even tighter because the universe-coverage / region-coverage anti-pattern doesn't apply (operators have no universe) — concept novelty becomes pure structural novelty.

### OPR6 — Asset-class / domain-blind contract

**Rule.** Operators do not know the **asset class** of their inputs. Generic, structural finance concepts that apply uniformly across asset classes (e.g., *trade*, *holding window*, *P&L*, *price panel*, *event date*) are allowed when they're part of the operator's structural method family. **Asset-class-specific concepts** (curve_family, tenor, sovereign vs OIS, FX pair convention, equity sector, credit issuer rating) are forbidden in the operator's signature, code, config, or imports.

The decision test: *"Would this operator's code change if I swap rates inputs for FX inputs or equity inputs?"* If yes (the operator branches on asset class, or its math depends on asset-class-specific conventions), it is not an operator — it is a primitive. If no (the operator treats every asset class identically through the typed-artifact metadata), it satisfies OPR6.

**Why.** This is the platform-level operationalisation of [P9](../../00_thesis/01_non_negotiables.md) at the operator layer, refined for the reality that some structural transforms (trade lifecycle, P&L accounting, position-level windowing) inherently use trading-shaped vocabulary without being asset-class-specific. The substrate's cross-asset claim — that the same operator catalog works on rates today, FX tomorrow, equities later — depends on the asset-class boundary, not on banning every word that sounds financial.

A *trade* is structurally the same shape in rates, FX, equities, and commodities; an operator that constructs trades from an event set is asset-class-blind because its code does not change when the asset class changes. A *yield curve* is structurally specific to rates; an operator that operates on yield curves is asset-class-specific and belongs in the primitive layer.

**Verify.**
- The operator's signature contains no asset-class-specific parameters (`curve_family`, `tenor`, `currency_pair`, `equity_sector`, `bond_issuer`). Generic trading parameters (`holding_period`, `leg_spec`, `notional`) are allowed if the operator is in a trade-lifecycle family.
- The operator's code does not branch on asset class identity. (Branching on structural metadata — *units*, *frequency*, *missingness policy* — is fine; that's the structural-metadata enforcement OPR11 prescribes.)
- The operator's `config.yaml` `defaults:` block contains no asset-class-specific values. (Generic structural defaults like `business_day_convention: act/365` *do* appear in current operator configs with `source: industry_standard_*` tags — those are allowed when documented as cross-asset conventions, not rates-specific.)
- The operator's imports include no agent packages: zero matches for `from rates_agent`, `from fx_agent`, etc.
- The operator's test suite includes at least one test where inputs are explicitly *not* rates data (synthetic random walks for non-trade operators; non-rates synthetic trades for trade-lifecycle operators) — and the operator produces structurally-correct output without any rates-specific assumptions.

**Anti-patterns.**
- An operator whose `<Operator>Params` includes `curve_family`, `tenor`, `instrument_type`, or any asset-class enum.
- An operator that branches on asset-class-shaped fields (e.g., `if input.units == BPS: ...` is suspect — BPS is rates-specific).
- An operator that imports anything from `rates_agent/`, `fx_agent/`, or any future agent's package.
- An operator that hardcodes a rates-specific default (e.g., `default_window_days: 252` *tagged* `source: industry_standard_us_treasury_window`). The 252-day default is fine when tagged as the generic `industry_standard_252_business_days` (which it currently is across the catalog); the rates-specific tagging would be the violation.
- An operator whose tests only use synthetic rates data, with no cross-asset smoke test.

**Acceptable** (not violations):
- A trade-lifecycle operator using `TradeSet`, `LegSpec`, `holding_period`, `P&L`, `financing_assumption`. These are generic trading concepts that apply across all asset classes; the operator does not know whether the trades are in rates, FX, equities, or commodities.
- A `default.source` tag that names an industry context (e.g., `industry_standard_sovereign_repo_usd_money_market`). The tag documents *provenance of the value*; it does not make the operator branch on asset class.
- Generic structural conventions (`act/365` day count, business-day calendars) used as defaults across the catalog.

**Exceptions.** None on the asset-class-blindness rule. Operators that need asset-class-specific behaviour are not operators; they are primitives.

**Relates to.** [P9](../../00_thesis/01_non_negotiables.md) is the platform-level principle; OPR6 is its operator-layer enforcement, refined to permit generic trading vocabulary where the trade-lifecycle operators legitimately need it. The complementary primitive rule is [PR3](../primitive/README.md#pr3--domain-residence-by-conventional-ownership) (primitives live where the desk concept conventionally lives — *because* primitives are the place asset-class-specific finance knowledge belongs).

---

## Group III — Standardness: what does "standard operator" mean operationally?

OPR1–OPR6 said the candidate *is* an admissible operator. Group III defines what it means for that operator to be *standard*. Same five-rule structure as the primitive's PR7–PR11, with operator-specific framings.

### OPR7 — Defaults offloading + the design-locked-constant allowance

**Rule.** Every *user-choosable methodology default* — join policies, fill policies, fill limits, threshold rules, aggregator defaults, ranking defaults — lives in `config.yaml`'s `defaults:` block with a full `{value, source, rationale, valid_values?}` block. Mathematical invariants — "all input series must have the same index type", "weights must sum to 1" — stay in code as Pydantic `@model_validator` validators or runtime structural-metadata checks.

**Hidden methodology constants in `operator.py` are forbidden, with one explicitly-bounded exception** described below.

#### Design-locked constants — the allowed exception

Some operators carry **design-locked constants** that are deliberately *not* user-configurable: they are part of the operator's contract shape, not a methodology choice. Examples in live code:

- `summarize_series/operator.py::SUMMARY_SENTINEL_DATE` — the fixed anchor date a 1-row summary is stamped at, so that two summaries can feed into `series_arithmetic.subtract` for cross-regime comparisons. Making this configurable would silently break downstream composition.
- `conditional_aggregate/operator.py::_OFFSET_ANCHOR` (a fixed Unix epoch reference for offset arithmetic) and `::_FIXED_DDOF` (degrees-of-freedom convention locked at 1).
- Standard `_OPERATOR_NAME`, `_OPERATOR_VERSION`, `_CONFIG_PATH` module-level constants every operator carries (these are identity / structural, not methodology).

These constants are allowed in `operator.py` provided **all four** of the following hold:

1. **Documented as design-locked** in the operator's docstring or methodology block (`methodology.what_it_does` or `methodology.planned_extensions`), with a one-line rationale for why it is *not* a YAML default.
2. **Stamped into lineage** when the constant materially affects the output — e.g., the sentinel date the summary is anchored at appears in `OperatorStep.params` so the artifact is replayable.
3. **Migration-scoped** — the docstring names the future change that would unlock configurability (e.g., *"`SUMMARY_SENTINEL_DATE` is locked at `1970-01-01` until series_arithmetic supports caller-supplied anchor alignment; planned in v1.2"*).
4. **Reviewable as a design lock, not a hidden methodology choice** — the constant is not a knob the user would *want* to vary; it's a structural pin the operator's contract depends on.

Everything else — user-choosable methodology — goes in YAML, no exceptions.

**Why.** A reviewer reading the YAML must be able to enumerate every methodology choice the operator makes. Truly-user-choosable defaults belong in YAML. Design locks — values that are part of the operator's contract shape and would break composition if they varied silently — belong in code *with documentation*. Pretending these are YAML defaults would mislead callers into thinking they could change them.

**Verify.**
- Every numeric constant in `operator.py` is either a mathematical truth (`1.0`, `100`), a documented design-locked constant (per the four conditions above), or read from `config.default_value("...")`.
- Every string default is YAML-sourced or design-locked-documented.
- The `config.yaml`'s `defaults:` block lists every user-choosable methodology choice with a non-empty `source`, non-empty `rationale`, and `valid_values` for enum-typed defaults. (Numeric defaults' range constraints belong in `<Operator>Params` field declarations, not in YAML — `OperatorDefault` has `extra="forbid"` and does not support `valid_range`.)
- Every design-locked constant is named in the operator's docstring with a one-line rationale and a migration pointer.

**Anti-patterns.**
- `_FFILL_LIMIT_DAYS = 5` at module level *without* documentation. This is hidden methodology; move to YAML.
- Magic numbers in operator body (`if abs(value) < 1.0:`) — the 1.0 should be in YAML.
- Methodology choices in code comments rather than `config.yaml` (`# inner join is the default`) — the YAML should declare it.
- A `valid_range` field in `config.yaml`'s `defaults:` block — `OperatorDefault` rejects this with `extra="forbid"`. Use `<Operator>Params` field constraints (Pydantic `Field(..., ge=..., le=...)`) for numeric ranges.
- A "design-locked constant" that is actually a methodology choice the user would reasonably want to vary — that's hidden methodology disguised, not a design lock.

**Exceptions.** Design-locked constants per the four conditions above. Every other methodology choice is YAML-locked.

**Relates to.** OPR analog of [PR7](../primitive/README.md#pr7--configuration-offloading), with the operator-specific design-lock allowance recognising that some operators have contract-shape constants that *must not* be user-configurable.

### OPR8 — Bounded parameter surface with explicit variants

**Rule.** `<Operator>Params` exposes every consequential method variant the caller can pick. *"Consequential"* means: the variant changes the operator's output materially. Examples in current code:

| Operator | Exposed consequential variants |
|---|---|
| `align_series` | `join_policy`, `fill_policy`, `fill_limit`, `require_matching_frequency`, `require_matching_missingness`, `output_keys` |
| `threshold_events` | `threshold_rule` (one-sided ≥, two-sided absolute, etc.), comparator, threshold value source |
| `event_windows` | `window_before`, `window_after`, alignment policy (event-aligned, calendar-aligned) |
| `conditional_aggregate` | `aggregator` (mean, median, max, min, count, sum), `min_observations` |
| `series_arithmetic` | `operation` (add, subtract, multiply, divide), unit-compatibility policy |

Hidden hardcoded choices that the caller cannot inspect or override are prohibited.

**Why.** This is the operator-specific analog of [PR8](../primitive/README.md#pr8--single-central-methodology-surface-the-central-knob), but with a different shape: primitives expose *one cohesive central methodology surface*; operators expose the *consequential variants of one structural method family*. The differences:

- Primitives are LLM-routed; their input surface must be minimal (one slot for the LLM to fill).
- Operators are workflow-template-routed; their parameter surface can be richer because the template author is choosing them deliberately, not extracting them from natural language.

But the underlying discipline is the same: every methodology choice the operator makes is *either* a caller-controlled parameter *or* a YAML-locked default; nothing is hidden in code.

**Verify.**
- `<Operator>Params` has typed fields for every variant the operator supports.
- Every variant has either a `Literal[...]` type or a `valid_values` constraint via the YAML's `defaults:` block.
- A reviewer can list every methodology choice the operator makes by reading `<Operator>Params` + `config.yaml` together — no surprises lurking in `operator.py`.

**Anti-patterns.**
- An operator with a hidden `_HARDCODED_TOLERANCE = 1e-9` in `operator.py` that materially affects the output.
- An operator whose "method family" has only one variant exposed when the doctrine clearly expects multiple (e.g., a `conditional_aggregate` that only supports `mean`).
- A parameter named `mode` whose values switch between *different method families* (PR2 violation; should be different operators).

**Exceptions.** None. Operators that genuinely have only one variant of their method family are correctly modeled as a single-variant family; the single variant is still declared explicitly.

**Relates to.** OPR analog of [PR8](../primitive/README.md#pr8--single-central-methodology-surface-the-central-knob) for operators. The framing differs because operators are template-routed, not LLM-routed.

### OPR9 — Typed artifact I/O over the closed family

**Rule.** Inputs and outputs are drawn from the closed-family artifact set: `Series`, `SeriesSet`, `EventSet`, `Panel`, `WindowedPanel`, `TradeSet`. No naked `pd.DataFrame`, no `np.ndarray`, no `dict`, no prose. Each artifact carries:

- a typed payload (the actual data)
- structural metadata (index type, units, frequency, missingness policy)
- a lineage chain (PrimitiveStep, OperatorStep)

The operator reads structural metadata to enforce compatibility (OPR11) and to make method choices that depend on the structural shape (e.g., daily vs weekly resampling).

Outputs are either **composable** (valid inputs to downstream operators) or **terminal** (valid end-state results for workflow templates / UI without further transformation). A single operator may produce either kind, but it does not produce prose, dicts, or ad-hoc objects.

**Why.** Typed artifacts are what make operators compositional. A `pd.DataFrame` in vs `pd.DataFrame` out is type-safe in Python but semantically opaque — the operator could be doing anything to it, and the downstream operator has no way to enforce structural correctness. Typed artifacts surface unit mismatches, frequency mismatches, missingness-policy mismatches, and index incompatibilities at the operator boundary, where they can be caught and refused (OPR11).

The artifact closed family is governed by [P8](../../00_thesis/01_non_negotiables.md) and owned by [`02_components/artifact/`](../artifact/) (forthcoming). Adding a new artifact type is an ADR-gated decision.

**Verify.**
- The operator's signature types every artifact input as one of the closed-family types.
- The operator's return type is one of the closed-family types.
- The operator's code does not "pass through" raw pandas / numpy objects in the public interface; pandas / numpy may be used internally but the boundary is typed.

**Anti-patterns.**
- An operator that takes `pd.DataFrame` as input. The right approach is to lift the DataFrame to a `Series` or `Panel` via a primitive's adapter first.
- An operator that returns a `dict` with raw numbers and prose annotations. Use a closed-family artifact type (most naturally a single-row `Series` or a `Panel` with one row of summary statistics) or refactor the prose annotation into the UI layer.
- An operator that introduces a new artifact type silently (e.g., a `CustomXResult` class). Adding artifact types is a P8 decision.

**Exceptions.** None.

**Relates to.** OPR analog of [PR10](../primitive/README.md#pr10--provenance-reachability) (provenance reachability) at the I/O layer, plus [P8](../../00_thesis/01_non_negotiables.md) (closed-family discipline). For primitives, output shape can vary (snapshot+TS, Panel, statistical fit, categorical); for operators, output must be one of the closed-family artifacts.

### OPR10 — Lineage extension via `OperatorStep.build` + `Lineage.append`

**Rule.** Every operator extends its primary input artifact's lineage chain by appending an `OperatorStep` constructed via `OperatorStep.build(...)`. The canonical pattern is:

```python
from shared.artifacts.lineage import OperatorStep

new_step = OperatorStep.build(
    name=_OPERATOR_NAME,              # e.g. "align_series"
    version=_OPERATOR_VERSION,        # e.g. "1.0.0"
    params=params.model_dump(),       # the <Operator>Params instance, serialised
    input_hashes=(<input_lineage_hashes>,),
    auxiliary_lineages=(<non_primary_input_lineages>,),  # optional, see below
)
new_lineage = primary_input.lineage.append(new_step)
```

The `OperatorStep` model has exactly these fields (per `shared/artifacts/lineage.py`): `kind="operator"`, `name`, `version`, `params`, `input_hashes`, `auxiliary_lineages`, and a computed `hash`. The model has `extra="forbid"` — adding fields like `policy_choices` will fail at construction time. **Runtime policy decisions (e.g., the actual `join_policy` after defaults resolution, the actual `fill_limit` resolved from `None`) belong inside `params`, not in a separate field.**

The `auxiliary_lineages` tuple is for **N-ary operators** whose right-hand or auxiliary inputs have their own provenance chains (e.g., `series_arithmetic` takes two `Series`; the second's lineage embeds into the step rather than living as a separate top-level chain). For unary operators, `auxiliary_lineages` is empty.

The output artifact's `lineage` is constructed by appending the step to the *primary* input's lineage; auxiliary input chains live inside the step. Operators do not *originate* lineage; they continue it.

**Why.** Lineage is what makes [P4](../../00_thesis/01_non_negotiables.md) (determinism and replayability) work at the workflow layer. A workflow that takes 12 nodes to produce its terminal artifact has 12 lineage steps; the user reading the terminal artifact's methodology card can see every transformation that touched the data. If one operator drops a step (doesn't extend lineage), the chain is broken and replay is impossible for everything downstream.

`OperatorStep.build` (vs. direct construction) is the right entry point because it computes the canonical step hash deterministically over `kind + name + version + params + input_hashes`. Direct `OperatorStep(...)` would require the caller to supply the `hash` field, which is the wrong responsibility — the hash recipe lives in the class.

**Verify.**
- The operator's output artifact's lineage is constructed via `primary_input.lineage.append(OperatorStep.build(...))`.
- The `OperatorStep.build(...)` call passes `name`, `version`, `params` (a serialised dict), and `input_hashes` (a tuple of upstream lineage hashes). `auxiliary_lineages` is passed for N-ary operators only.
- Runtime-resolved policy decisions are reflected *inside* the `params` dict, not as separate fields on `OperatorStep` (the model rejects them).
- A test asserts: input artifact's lineage length is N; output artifact's lineage length is N+1; the new step's `name` matches the operator.
- Composition of two operators through the workflow executor produces a lineage chain of length N+2.

**Anti-patterns.**
- An operator that returns its output without modifying lineage (lineage chain stays at length N).
- An operator that *replaces* the input lineage rather than appending (loses upstream provenance).
- Calling `OperatorStep(...)` directly instead of `OperatorStep.build(...)` (forces the caller to compute the hash; the hash recipe should live in the model).
- Calling `lineage.extend(...)` — that method does not exist on `Lineage`. The method is `append`.
- Adding a `policy_choices=...` kwarg to `OperatorStep.build` — the field does not exist and the model rejects unknown fields. Policy choices go inside `params`.
- An `OperatorStep` whose `params` field is empty or missing the runtime-resolved decisions — the lineage can't reproduce the operator's behaviour without them.

**Exceptions.** None.

**Relates to.** [P4](../../00_thesis/01_non_negotiables.md), OPR analog of [PR10](../primitive/README.md#pr10--provenance-reachability) (primitives originate via `PrimitiveStep`; operators extend via `OperatorStep`), [P5](../../00_thesis/01_non_negotiables.md) (provenance is the disclosure substrate).

### OPR11 — Structural-metadata enforcement and honest refusal

**Rule.** Operators enforce structural metadata compatibility by default. The default is **strict**: if the operator's inputs disagree on `frequency`, `units`, `missingness_policy`, or `index_type` in a way that would silently change the result, the operator raises a typed `<Operator>Error` exception. Callers may opt into mixed inputs explicitly via `<Operator>Params` flags (e.g., `align_series.require_matching_frequency=False`); the flag value flows through to the `params` dict on the resulting `OperatorStep`, so downstream consumers can see in the lineage record which compatibility check was relaxed.

**Why.** Structural metadata is the operator layer's safety net. Silently aligning a daily series with a weekly one, silently adding a basis-point series to a percentage-point series, silently aggregating a ffilled series with a raw series — these are the kinds of failure modes the typed-artifact framework exists to catch. If operators don't enforce, the framework is decorative. Strict by default + explicit opt-in is the discipline that surfaces compatibility decisions at the workflow-template layer where they're visible to the reviewer, instead of silently inside `operator.py` where they're invisible.

**Why typed exceptions, not envelopes:** operators are deep inside the workflow execution stack. Returning `{"error": "..."}` from an operator would force every workflow executor to branch on dict-shape — fragile. Raising a typed exception lets the executor catch at the right level (workflow boundary) and convert to the envelope for the user-facing layer. The transport-boundary envelope conversion happens once at the workflow / MCP / API layer; operators are below that.

**Verify.**
- Every `<Operator>Params` exposes flags for the structural-metadata compatibility checks (`require_matching_X`); defaults are `True` (strict).
- The operator validates input metadata on entry and raises `<Operator>Error` with a clear message on mismatch (e.g., *"AlignSeriesError: input frequencies disagree: ['daily', 'weekly']; pass require_matching_frequency=False to opt in"*).
- When a `require_matching_X=False` flag is set, the operator proceeds with the mixed inputs and the relaxed flag value appears inside the output's `OperatorStep.params`.
- A test exercises each strict-default case (mismatch → exception) and each opt-in case (mismatch tolerated → policy recorded).

**Anti-patterns.**
- An operator that silently proceeds with mixed-frequency inputs (no exception, no flag, no policy record).
- An operator whose `<Operator>Error` message is generic (`"input validation failed"`); it must name the specific mismatch.
- An operator that returns `{"error": "..."}` instead of raising.
- A `require_matching_X` flag whose default is `False` (strict-by-default is the rule).

**Exceptions.** None.

**Relates to.** OPR analog of [PR11](../primitive/README.md#pr11--honest-refusal), but with two differences: operators always raise (no envelope path); the refusal is *structural* (metadata mismatch), not *methodological* (unbuilt method).

---

## Group IV — Operational: the build conventions every standard operator follows

OPR7–OPR11 define what *standard operator* means. OPR12–OPR16 are the operational conventions that keep the operator catalog coherent.

### OPR12 — Source-tagged defaults

**Rule.** Every `default.source` in every operator's `config.yaml` is a non-empty, defensible tag that documents where the default came from. The set of tags actually in use across the operator catalog today (observed across `shared/operators/*/config.yaml`):

- **`operator_v1_default`** — a choice made by the operator's V1 author as a sensible structural default, awaiting cross-template validation.
- **`methodology_judgement_pending_review`** — a default reflecting team judgment, not yet validated externally. (Note the operator-side spelling differs from the primitive-side `team_judgment_pending_review`; both are debt tags meant to be driven down.)
- **`industry_standard_252_business_days`** — generic 1-year rolling window convention, applicable across markets.
- **`industry_standard_sovereign_repo_usd_money_market`** — industry-context citation. The tag documents *where the value originated*, not what the operator branches on — operators stay asset-class-blind per OPR6 even when their default values cite an industry context.
- **`derived_from_window`** — a default mechanically derived from another default.
- **`rolling_regression_primitive_v1`** — default inherited from a sibling primitive's V1 calibration.

New tags should be added by editing live configs and noting them in the version log; a formal tag registry with lint enforcement is a planned tightening (today the lint validates `OperatorDefault` schema shape but does not enforce a tag whitelist).

Vague tags (`default`, `standard`, `convention`, `tbd`, `fixme`) are auto-reject.

**Why.** Same as [PR12](../primitive/README.md#pr12--registered-methodology-sources) for primitives: every default value must have a defensible origin documented at the value. The operator-side and primitive-side tag sets overlap partially (both use `derived_from_window`; the "pending review" tag has slightly different spelling on each side); convergence to a unified registry is a known cleanup.

**Verify.**
- Every `default.source` is a non-empty string drawn from the tags in use across the operator catalog, or a new tag introduced with a one-line rationale in the PR description.
- No vague tags.
- The `source` field documents value provenance; it does *not* imply the operator branches on the citation. (An operator with `source: industry_standard_sovereign_repo_usd_money_market` is still asset-class-blind per OPR6 as long as its code doesn't branch on the citation.)

**Anti-patterns.**
- `source: "default"`, `source: "v1"`, `source: "team_choice"` — vague tags.
- A new source tag that overlaps an existing one with a slightly different name (drift). Reuse the existing tag.

**Exceptions.** None.

**Relates to.** OPR analog of [PR12](../primitive/README.md#pr12--registered-methodology-sources). The operator-side tag enforcement is currently lighter than primitive-side (no formal registry); when the registry lands and is lint-enforced, both layers will share it.

### OPR13 — Typed exceptions, not envelopes

**Rule.** Operators raise typed `<Operator>Error` exceptions on user-facing failures. They never return `{"error": "..."}` envelopes from inside the operator. The envelope conversion happens at the transport boundary — the workflow executor's exception handler, or the MCP server wrapper around the workflow — never inside `operator.py`.

`<Operator>Error` is conventionally a subclass of `ValueError` (e.g., `class AlignSeriesError(ValueError)`) so existing `except ValueError:` blocks in the rest of the codebase continue to work.

**Why.** Operators are pure functions deep inside the workflow stack. Returning a dict envelope from inside one would force every workflow executor to type-check the output of every operator (`is it a SeriesSet or is it an error dict?`), which is fragile and against the typed-artifact contract (OPR9). Raising a typed exception lets the executor catch once at the workflow boundary, convert to the user-facing envelope shape per [P6](../../00_thesis/01_non_negotiables.md)'s transport-layer rule, and surface the error cleanly.

This is the *opposite* discipline from primitives' PR11, where envelopes are sometimes acceptable (when the surrounding pattern expects them, e.g., `financing_rate`). The difference: primitives sit at the MCP boundary; operators sit deep inside the workflow stack.

**Verify.**
- The operator defines `<Operator>Error(ValueError)`.
- The operator's `operator.py` does not contain `return {"error": ...}` anywhere.
- The operator raises with a clear, specific message naming the failure mode and any applicable opt-in flag.

**Anti-patterns.**
- `return {"error": "alignment failed"}` from inside an operator.
- `raise ValueError("error")` (use the typed `<Operator>Error` subclass with a specific message).
- Bare `raise Exception(...)` (always raise the typed subclass).
- Catching exceptions inside the operator and converting to a dict envelope — that conversion happens at the transport boundary, not here.

**Exceptions.** None.

**Relates to.** [P6](../../00_thesis/01_non_negotiables.md) at the operator-vs-transport-layer split. Note the deliberate divergence from [PR11](../primitive/README.md#pr11--honest-refusal), which allows envelopes for primitives. The two layers have different correct refusal mechanisms.

### OPR14 — Pure function, no I/O

**Rule.** Operators are pure functions. They have no DB access, no network calls, no filesystem reads or writes (except reading the bundled `config.yaml` at module load), no wall-clock time, no unfixed random seeds, no global state mutation.

`operator(inputs, params, config) → output` is deterministic: the same inputs + params + config always produce the same output (and the same lineage hash).

**Why.** Determinism at the operator layer is what makes [P4](../../00_thesis/01_non_negotiables.md) (replayability) work for workflows. A workflow that runs through 12 operators is replayable iff each operator is deterministic. Any I/O at the operator layer breaks the chain: the operator's output now depends on something the lineage chain doesn't capture, and the audit story collapses.

The contrast with primitives is structural: primitives *must* read from the DB (that's the L2-from-L1 step); operators *must not* (they sit above L1; the data has already been read).

**Verify.**
- The operator's signature has no `engine` parameter, no DB connection, no path arguments (except indirectly via the bundled config).
- A `grep` for `engine`, `connection`, `requests.`, `urllib`, `open(`, `datetime.now`, `time.time`, `random.` inside the operator's code is empty — or all matches are in the bundled-config load path, which runs once at import.
- A test exercises the operator twice with identical inputs and asserts byte-identical outputs (`output_1.lineage.head_hash == output_2.lineage.head_hash`).

**Anti-patterns.**
- An operator that hits a database to look up a value during execution.
- An operator that uses `datetime.now()` or `time.time()` anywhere in its compute path.
- An operator that uses `np.random.randn()` or `random.choice()` without a fixed seed.
- An operator that mutates global state (`shared.cache[key] = value`).
- An operator that reads a non-bundled-config file from disk.

**Exceptions.** None.

**Relates to.** [P4](../../00_thesis/01_non_negotiables.md), OPR analog of the primitive's compute-purity discipline.

### OPR15 — Arity declared in the registry; list slots are first-class

**Rule.** Every operator's arity is declared in its `OperatorSpec.input_slots` entry in `shared/workflow/registry.py::OPERATOR_REGISTRY`. The substrate supports three slot shapes today:

1. **Single-artifact slots**: `input_slots={"series": "Series", "mask": "EventSet"}` — exactly one artifact per slot.
2. **List-of-artifact slots** (first-class): `input_slots={"series_list": "List[Series]"}` — the slot takes a variable-length list of artifacts of the named type. The workflow validator handles list-aggregation across multiple edges into the same slot. `align_series`'s `series_list` slot is the canonical example.
3. **Mixed-arity** with optional slots: declared in `OperatorSpec.accepts_scalar_input` and validated by per-operator `arity_validator` lambdas (e.g., `evaluate_trades` has an optional `financing_rate_panel` slot whose requirement is conditional on a params value).

A list-typed slot is not "implicit fan-out" — it is an explicit, registry-declared arity choice. The substrate validator knows about it; workflow templates can bind multiple edges into a single list slot; the operator's signature can take `List[ArtifactType]` directly. There is no separate `map` meta-operator today (and none is needed for current use cases).

A future `map` meta-operator may be added if a use case arises where an operator needs to be wrapped point-wise over a list without being designed as a list-slot operator from the start. That would be an OPR4 admission decision in its own right.

**Why.** Type-algebra clarity. A template author reading `OperatorSpec.input_slots` can tell at a glance whether a slot is single-artifact, list-of-artifact, or optional. The substrate validator enforces the declared shape at workflow-validation time, before any execution.

**Verify.**
- The operator's arity is declared in `OperatorSpec.input_slots` with explicit types: `"Series"`, `"List[Series]"`, `"EventSet"`, etc.
- The operator's Python signature mirrors the registry declaration (e.g., `series_list: List[Series]` for a `"List[Series]"` slot).
- The operator does not introduce implicit list handling inside its body that the registry hasn't declared (e.g., accepting `Union[Series, List[Series]]` and branching internally is a violation — the registry should pick one shape).

**Anti-patterns.**
- An operator with a `Union[T, List[T]]` input that branches internally instead of declaring the shape once in the registry.
- A new "operator" whose only job is to fan out an existing operator over a list — the existing operator's slot should be list-typed if list input is a legitimate use; otherwise wait for a real fan-out use case before adding a `map` meta-operator.
- An operator whose registry declaration disagrees with its Python signature (e.g., registry says `"Series"` but the function takes `List[Series]`).

**Exceptions.** None. Arity is what the registry declares.

**Relates to.** The existing operator-architecture doctrine's fan-out section codified differently in v1 (referenced a `map` meta-operator); this principle aligns to what the substrate actually supports today (list-typed slots are first-class; `map` is future work).

### OPR16 — Test pattern

**Rule.** Every operator ships with two test layers, with a bootstrap allowance on the second:

1. **`tests/test_<operator>.py`** (or equivalent unit-test file) — synthetic typed-artifact inputs exercising the happy path, every parameter variant, and every structural-metadata mismatch (each `require_matching_X=False` opt-in flag). Plus **registry / validator tests** confirming the operator is correctly registered in `OPERATOR_REGISTRY` and that the validator accepts representative slot bindings. *This layer is mandatory from day one — no bootstrap allowance.*
2. **At least one workflow-integration test** through a real workflow template that consumes the operator — verifies the operator integrates with the workflow executor, the lineage chain extends correctly, and the output artifact flows into the next node. *This layer is mandatory **once a real workflow consumer exists**; for OPR4 bootstrap-exception operators that ship before any consumer, the workflow integration test lands with the first consumer's PR.*

**No SQL validation tests.** Operators don't talk to the DB; there's no independent SQL baseline. The workflow-integration test is the operator's analog of the primitive's SQL parity check.

**Why.** Operators are the building blocks workflow templates compose. Unit + registry tests catch local bugs and registry-misregistration; workflow integration catches composition bugs (the operator works on its own but fails to chain correctly). The bootstrap allowance on the second layer recognises the chicken-and-egg of OPR4: a brand-new operator may legitimately ship before any consumer exists, with the integration test arriving alongside the first consumer.

**Verify.**
- The operator has a unit-test file covering the happy path, every documented variant, and every structural-mismatch exception case.
- Registry / validator tests confirm `OPERATOR_REGISTRY` includes the operator with correct `input_slots`, `output_type`, and `params_class`.
- A workflow template in `<agent>/workflows/` references the operator and has an integration test **OR** an ADR records bootstrap status and names the timeline for the first consumer (per OPR4's bootstrap exception).
- The unit test exercises cross-asset robustness (or asset-class-blind robustness in OPR6's revised sense): at least one test case uses inputs that aren't rates-shaped (random walks for non-trade operators; non-rates synthetic trades for trade-lifecycle operators).

**Anti-patterns.**
- An operator with unit tests only on rates-shaped data, no cross-asset coverage.
- An operator without any workflow-integration test *and* no ADR-recorded bootstrap exception explaining when the integration test will arrive.
- A unit test that mocks structural metadata away instead of constructing real typed artifacts.
- A bootstrap-exception operator whose integration test never arrives — at the next phase-boundary review, the operator must either gain the integration test (consumer landed) or be demoted to workflow-local code.

**Exceptions.** Bootstrap exception on the workflow-integration test, as described — ADR-recorded with a timeline. The unit + registry tests have no exception; they ship from day one.

**Relates to.** OPR analog of [PR16](../primitive/README.md#pr16--test-triplet). The "triplet" reduces to a "doublet" for operators because there's no SQL-parity layer; the bootstrap allowance on the integration test is unique to operators (primitives have no equivalent because every primitive has a clear consumer by definition — it serves a desk concept).

---

## Method-family taxonomy

Operators do not have buckets (the 1A / 1B / 2 axis used for primitives doesn't apply — operators have no methodology-depth gradient). They have a **method-family taxonomy** instead, declared as a `Literal[...]` enum at `shared/config/operator_config.py::OperatorMethodFamily`. The current registered families (in their canonical YAML-value form):

| `method_family` value | What the family does | Current operators in this family |
|---|---|---|
| `alignment` | Combine N indexed artifacts onto a common index | `align_series` |
| `arithmetic` | Compose typed series via add / subtract / multiply / divide with unit compatibility | `series_arithmetic` |
| `masking` | Apply a boolean mask / event-driven selection to an indexed artifact | `apply_mask`, `threshold_events` (events are masks over time) |
| `windowing` | Extract data windows around event timestamps | `event_windows` |
| `aggregation` | Reduce a windowed, panel, or series artifact into a summary | `conditional_aggregate`, `summarize_series` |
| `ranking` | Rank or sort across a collection artifact | (no current operator; reserved for cross-sectional ranking when it ships) |
| `mapping` | Project / transform / select across a collection artifact | `select_from_series_set` |
| `trade_construction` | `EventSet → TradeSet` | `construct_trades` |
| `trade_evaluation` | `TradeSet + price Panel → P&L Panel` | `evaluate_trades` |
| `trade_summary` | `P&L Panel → summary Panel` | `summarize_trades` |

The trio of `trade_*` families exists because the backtest archetype required them; they are *asset-class-blind* trade-lifecycle structural transforms (the operator doesn't know if the trades are in rates or FX or equities). The non-trade families are pure structural transforms.

Adding a new family is a Literal-enum extension — it requires editing `OperatorMethodFamily` in `shared/config/operator_config.py` plus an ADR. This makes it both an OPR4 (admission) and an OPR5 (concept novelty) decision under the same five-criterion + promotion-rule bar as adding a new operator. Adding a new *variant* inside an existing family (e.g., a new `aggregator` value in `conditional_aggregate`) is a much smaller change — usually a YAML addition + a code branch + a test, no new operator needed.

## Non-standard operators (coming soon)

Some operators may legitimately not satisfy OPR7–OPR11 — e.g., operators wrapping a third-party library whose internals are opaque, operators carrying model state that's research-output rather than configuration. A non-standard operator category, its admission tests, and its workflow-composition rules are a planned extension. **Until that section opens, every operator that ships is held to OPR7–OPR11.** Contributors who believe their operator genuinely cannot meet the standard contract should surface the case rather than ship a non-standard operator under a standard label.

---

## Worked examples — how the principles manifest in real operators

### Alignment family — `align_series`

The canonical example of every operator principle in one place. Consumes `List[Series]`, produces `SeriesSet`.

- **OPR1 + OPR6**: Pure structural alignment. Code makes no finance references; runs identically on yields, FX rates, equity prices, or temperatures.
- **OPR3**: Lives at `shared/operators/align_series/`.
- **OPR7 + OPR8**: `defaults:` block declares `join_policy`, `fill_policy`, `fill_limit`, `require_matching_frequency`, `require_matching_missingness`, each with `source`, `rationale`, `valid_values`. `AlignSeriesParams` exposes every variant.
- **OPR9**: Input is `List[Series]` (structurally a collection of typed Series artifacts); output is `SeriesSet`. Closed-family in and out.
- **OPR10**: Output's `lineage` is constructed via `series_list[0].lineage.append(OperatorStep.build(name="align_series", version="1.0.0", params=<flattened AlignSeriesParams>, input_hashes=(<each input's head_hash>,)))`. Auxiliary inputs' lineages embed into the step rather than appearing as separate top-level chains.
- **OPR11**: Strict-by-default frequency/missingness matching; `require_matching_X=False` opts in to mixed inputs and records the choice.
- **OPR13**: Raises `AlignSeriesError(ValueError)` on mismatch.
- **OPR14**: Pure function; no I/O.

### Thresholding family — `threshold_events`

Consumes `Series`, produces `EventSet`. Same principles; output type *changes* across the operator boundary, which is the operator-algebra signal that this is structural transformation, not just filtering.

- **OPR1**: Owns the thresholding method family with explicit variants (one-sided, two-sided, absolute, signed).
- **OPR9**: Series → EventSet. The closed-family artifact types are designed to encode this kind of structural change (a time series of values becomes a set of events at specific timestamps).

### Aggregation family — `conditional_aggregate`

Consumes a `WindowedPanel` (whose contents already encode the events as window groupings), produces a `Series` (per-event aggregated values keyed by event date). The `aggregator` variant (`mean`, `median`, `max`, `min`, etc.) is the central choice; ancillary parameters like `min_observations` round out the surface.

- **OPR8**: All consequential variants exposed as `<Operator>Params` fields.
- The example most clearly demonstrates *why* an aggregator can't be a separate operator per aggregator value — the family is the operator; the values are its variants.

### Backtest operator chain — `construct_trades → evaluate_trades → summarize_trades`

The canonical composition example. Three operators chain through `EventSet → TradeSet → Panel → Panel`. Each operator handles one trade-lifecycle family:

- `construct_trades` (family: `trade_construction`): `EventSet → TradeSet`.
- `evaluate_trades` (family: `trade_evaluation`): `TradeSet + price Panel → per-trade P&L Panel`.
- `summarize_trades` (family: `trade_summary`): `P&L Panel → single-row summary Panel`.

Each operator is **asset-class-blind**: `construct_trades` doesn't know whether the trade is a rates trade, an FX trade, or an equity trade — only that the input is an `EventSet` and the output is a `TradeSet`. The workflow template (`backtest`) is what *composes* these into a finance-meaningful analysis specific to an asset class. This is the architecture working as designed: structural trade-lifecycle methods stay in the operator layer, asset-class-specific reasoning stays in the workflow template that consumes them. (Per OPR6 as revised, the operator layer is asset-class-blind, not finance-vocabulary-empty — see the OPR6 record for the distinction.)

### Arithmetic family — `series_arithmetic`

Consumes two `Series`, produces one `Series`. The `operation` variant (`add`, `subtract`, `multiply`, `divide`) is the central choice; unit compatibility is enforced (OPR11).

- **OPR11 is especially load-bearing here**: silently adding a basis-point series to a percent series would produce a meaningless number. Strict unit-compatibility enforcement is what catches this at the operator boundary.

### Single-output summary — `summarize_series`

Consumes a `Series`, produces a single-row `Series` anchored at a hard-coded sentinel date (`SUMMARY_SENTINEL_DATE`). The single-row output stays inside the closed family (`Series` is the canonical type) while still being a *terminal-style summary* — a workflow template can use the value directly, or feed it into another operator like `series_arithmetic` to compare two summaries across regimes. The sentinel date is a wire-format / design-lock constant whose presence is documented in lineage and surfaced in the operator's methodology card (see OPR7's "design-locked constants" allowance).

---

## Anti-patterns (catalogue-wide)

Auto-reject in review:

- **An operator with a finance-aware parameter** (`curve_family`, `tenor`, `instrument_type`, etc.). OPR6 violation. Refactor to operate on artifact metadata, not finance concepts.
- **An operator placed under any agent's package.** OPR3 violation.
- **An operator that imports from any agent's package.** OPR3 + OPR6 violation.
- **An operator with hidden methodology constants in `operator.py`.** OPR7 violation.
- **An operator returning a `dict` or `pd.DataFrame` from its public interface.** OPR9 violation.
- **An operator that doesn't extend lineage.** OPR10 violation.
- **An operator that silently tolerates structural-metadata mismatches.** OPR11 violation.
- **An operator returning `{"error": "..."}` instead of raising.** OPR13 violation.
- **An operator with `datetime.now()` or `np.random` (unseeded) in its compute path.** OPR14 violation.
- **An operator with internal fan-out (`for x in xs: ...`) where `xs` is not a first-class collection artifact.** OPR15 violation.
- **A new operator built when an existing one could be extended with a new variant.** OPR4 + OPR5 violation.
- **A new operator that's used by only one workflow.** OPR4 promotion-rule violation; keep it as workflow-local code.
- **An operator whose `methodology.what_it_does` uses the word "or" to describe two unrelated method families.** OPR2 violation.

## Open questions and known gaps

1. **The `map` meta-operator.** OPR15 references a `map` meta-operator as the canonical fan-out site. None is registered today; fan-out happens via collection-typed inputs. When the first true fan-out use case arrives, the `map` meta-operator will be added with explicit semantics for how it wraps base operators.
2. **Non-standard operator category** (the OPR7–OPR11 escape hatch). Tracked as a planned extension; until it opens, every operator is held to standard.
3. **Closed-family artifact set may grow.** The current set (`Series, SeriesSet, EventSet, Panel, WindowedPanel, TradeSet`) covers all current operators. Two earlier design-note types — `ScalarMetric` (explicitly deferred) and `RankedResult` (not registered) — are not in the executable closed family today; operators that would naturally return one of them use a single-row `Series` or a `Panel` instead. New artifact types are ADR-gated decisions per [P8](../../00_thesis/01_non_negotiables.md); the operator contract will reference whatever the artifact contract declares.
4. **Workflow-integration test discoverability.** OPR16 requires a workflow-integration test, but the operator-to-workflow mapping is not yet machine-discoverable. A registry that names which workflows consume each operator (for impact-analysis when an operator changes) is a planned tool addition.
5. **`team_judgment_pending_review` count.** Operators share this tag with primitives. The platform's stated goal is to drive the count down by validating defaults against external references; the operator contribution to that count should be tracked separately from the primitive contribution.
6. **Promotion-rule audit trail.** OPR4's promotion rule is reviewed at admission time, but there's no ongoing audit that confirms an operator still meets the rule (e.g., what if archetypes get deprecated and an operator drops below the ≥3 threshold?). Promotion-status as a periodic review is a planned hygiene practice.

## Changing an operator principle

Same discipline as the primitive principles:

1. Open an ADR in [`../../05_decisions/`](../../05_decisions/) describing the proposed change.
2. Land the ADR and the contract change in the same PR.
3. Bump the version of this file.

This contract is intentionally stable. New operators compound onto it; existing operators are audited against it; workflow templates rely on it. Slow change is the right shape.

## Citation cheat sheet

| Use | Pattern |
|---|---|
| In a commit message | `feat(threshold_events): expose two-sided rule variant per OPR8` |
| In a PR review comment | `This violates OPR6 — the operator imports from rates_agent and branches on curve_family.` |
| In a code comment (rare) | `# OPR11: strict-by-default; opt in via require_matching_frequency=False` |
| In a runbook step | `Step 4 — verify OPR9 (typed I/O) and OPR10 (lineage extension).` |
| In an ADR | `This decision relaxes OPR4's promotion rule for the first FX-agent operator pass.` |

## Version log

| Version | Date | Change | ADR |
|---|---|---|---|
| v1.1 | 2026-05-17 | Twelve pre-canonical factual corrections, each re-verified against live code: (a) **Folder shape** corrected from "exactly three files" to "exactly four files" — `__init__.py`, `config.yaml`, `schemas.py`, `operator.py` — matching the live shape; the difference from primitives is `operator.py` vs `compute.py`, not the count. (b) **Closed artifact family** corrected: live family is `Series, SeriesSet, EventSet, Panel, WindowedPanel, TradeSet`. `ScalarMetric` is explicitly deferred per `shared/artifacts/types.py`; `RankedResult` is not registered. (c) **Worked example outputs** corrected: `conditional_aggregate` → `Series` (not Panel/ScalarMetric); `summarize_series` → `Series` (not ScalarMetric); `summarize_trades` → `Panel` (not ScalarMetric). All verified against `OPERATOR_REGISTRY`. (d) **Method-family taxonomy** rewritten to use the actual `OperatorMethodFamily` Literal values from `shared/config/operator_config.py`: `alignment, arithmetic, masking, windowing, aggregation, ranking, mapping, trade_construction, trade_evaluation, trade_summary`. Replaced incorrect entries (`thresholding`, `selection`, `construction`, `evaluation`). (e) **OPR6 reframed** from absolute "no finance vocabulary" to **asset-class/domain-blind** — generic trading concepts (trade, holding window, P&L) are allowed when they're part of the structural method family; asset-class-specific concepts (curve_family, tenor, FX pair) are forbidden. Reconciles with live `construct_trades` / `evaluate_trades` / `summarize_trades` and `TradeSet`. (f) **OPR7** added a **design-locked-constant allowance** with four explicit conditions (documented, stamped into lineage, migration-scoped, genuinely structural-not-methodology) — reconciles with live `SUMMARY_SENTINEL_DATE`, `_OFFSET_ANCHOR`, `_FIXED_DDOF` in the catalog. (g) **OPR10** rewritten around the actual lineage API: `OperatorStep.build(name=..., version=..., params=..., input_hashes=..., auxiliary_lineages=...)` + `Lineage.append(step)`. Removed the non-existent `policy_choices` field; runtime decisions go inside `params`. Removed the non-existent `lineage.extend(...)` method. (h) **OPR12** source-tag taxonomy aligned with tags actually in use across the operator catalog (`industry_standard_252_business_days`, `industry_standard_sovereign_repo_usd_money_market`, `methodology_judgement_pending_review`, `rolling_regression_primitive_v1`, etc.) and softened the enforcement claim — lint validates schema shape but doesn't enforce a tag registry today. (i) **OPR15** rewritten around the substrate's actual support: `OperatorSpec.input_slots` declares arity, list-typed slots (`"List[Series]"`) are first-class as in `align_series`. The `map` meta-operator is future work, not a current dispatch site. (j) **OPR4 + OPR16** added **bootstrap exception** with ADR backing for new asset-class / new archetype work where ≥3-consumer reuse can't be reached before the operator exists. (k) **Runbook Step 3** YAML template removed `valid_range` (operator `OperatorDefault` has `extra="forbid"` and only accepts `value`/`source`/`rationale`/`valid_values`); numeric range constraints belong in `<Operator>Params` Pydantic field constraints. (l) **Runbook Step 5** params resolution recipe rewritten to do explicit field-by-field via `config.default_value("<field>")`, not a comprehension over `model_fields` (which fails on per-call fields without YAML defaults). | (pending) |
| v1 | 2026-05-17 | Initial operator contract. Replaced by v1.1 the same day after a factual-review pass against the source code. | — |
