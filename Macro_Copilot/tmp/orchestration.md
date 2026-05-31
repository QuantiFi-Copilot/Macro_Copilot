# Open-DAG Orchestration — PR Plan for the PoC

> **Status:** build-ready plan. To be audited by Codex before code lands.
> **Branch:** `revamp`.
> **Scope:** the *unconstrained* (open-DAG) orchestration lane that lets the LLM build a fresh typed DAG to answer any prompt expressible inside today's primitive + operator universe — without any one LLM ever seeing the full universe.
> **Out of scope, deliberately:** template-mode orchestration (untouched); backtest path (operators exist on disk but are unregistered — see §10); UI changes beyond the answer-rendering template.

---

## 0. Purpose

Macro Copilot is being built as a *digital macro analyst brain* — the bridge between quant/optimization systems and discretionary trader reasoning. The LLM is the economic-intuition brain; the primitives and operators are its deterministic math/finance instruments; a DAG is just the trader's mental sequence made auditable.

This document is the PR plan for the architecture that lets the LLM construct a typed DAG to answer any prompt within today's universe (58 primitives across 6 domains, 16 registered operators, 6 closed-family artifact types), while satisfying three scaling invariants:

1. **Registration-only growth.** Adding the Nth primitive / operator / domain requires only a registration entry — zero change to composer, validator, executor, or any LLM-facing static prompt.
2. **Per-decision context is bounded by local fan-out.** No LLM ever sees the full universe at once.
3. **Two trust boundaries.** Determinism lives *outside* the reasoning path — in validation and lineage — not by constraining the LLM's reasoning.

The PoC is built on today's universe. The architecture is what's being proved.

---

## 1. Settled rulings (do not re-litigate)

Cited from the project-owner handoff. Every PR below must respect these; reviewers may reject any PR that violates one.

| ID | Ruling |
|---|---|
| R1 | The LLM does ALL understanding, selection, composition. NO code-based "semantic filtering" that maps user words to tool names/tags. |
| R2 | Determinism is enforced OUTSIDE the LLM's reasoning path: typed primitives + operators, deterministic validator, content-addressed lineage. NOT by constraining the LLM's freedom inside any one decision. |
| R3 | Hierarchical LLM routing INCREASES end-to-end accuracy when each layer is well-scoped and well-fed. Flat "one LLM sees all 58 tools" is rejected. |
| R4 | Rich primitive/operator descriptions ARE the priors the LLM reasons over. They are not optional polish. |
| R5 | The "role vocabulary" is NOT a separate hand-curated enum. It is an emergent property of how thoroughly we describe each primitive and operator. |
| R6 | Templates are OUT OF SCOPE for this work. |
| R7 | No useless sub-agents. Careful single-pass thinking beats spurious fan-out. |
| R8 | Boundary B is hard-block. Never execute a low-confidence DAG. |
| R9 | Lineage hash = **reproducibility, NOT correctness.** L6's intent echo is what protects correctness — wrong understanding shows up before a wrong-confident number ever does. |
| R10 | P11 isolation (each domain agent sees only its own MCP tools) is enforced at the MCP-subprocess level and must remain enforced after this work. |
| R11 | P9 — operators stay finance-blind. No PR may put domain vocabulary inside `shared/operators/`. |
| R12 | P10 — single source of truth. Every piece of content lives in exactly one place, referenced from everywhere else. |

---

## 2. Confirmed catalogue

### 2.1 Operators (16, finance-blind, in `OPERATOR_REGISTRY` at [shared/workflow/registry.py:324](../shared/workflow/registry.py:324))

Grouped by purpose; wiring fact each one carries.

| Purpose | Operator(s) | Wiring fact the composer must know |
|---|---|---|
| Align indexes / frequencies | `align_series` | Outputs `SeriesSet`. To feed pair-stats (correlation et al.) the composer MUST add `select_from_series_set ×2` to extract two `Series`. |
| Unit conversion | `convert_units` | One `Series` → one `Series`, re-tagged. The standard "adapter" repair-loop will insert. |
| Pick named Series from bundle | `select_from_series_set` | `SeriesSet` → one `Series` named by `params.key`. |
| Combine / transform a Series | `series_arithmetic` | `add` / `subtract` / `multiply` / `divide` / `diff` / `pct_change`. Conditional arity: `right` required only for binary ops. Same-unit required for `add`/`subtract`/`divide(Series,Series)`. |
| Pair statistical relationship | `correlation`, `rolling_correlation`, `cointegration`, `rolling_regression` | First three HARD-require identical `DatetimeIndex` → `align_series → 2× select` upstream is required. `rolling_regression` inner-joins internally but the composer should still align upstream as convention. |
| Standardise / rank vs own history | `rolling_zscore`, `percentile_rank`, `rolling_statistic` | One `Series` → one `Series`. Output units: `Z_SCORE`, `PCT_RANK`, preserved. |
| Find events in a Series | `threshold_events` | `Series` → `EventSet` (boolean dates). |
| Mask a Series by event dates | `apply_mask` | `Series` + `EventSet` → `Series` (restricted to True dates). |
| Build per-event panel | `event_windows`, `conditional_aggregate` | `event_windows` makes `WindowedPanel` from `EventSet` + `Series`. `conditional_aggregate` collapses `WindowedPanel` to a per-offset `Series`. |
| Collapse Series to 1-row summary | `summarize_series` | One `Series` → one 1-row `Series` at a sentinel date. Feeds `series_arithmetic.subtract` for cross-regime compares. |

**Out of scope (exist on disk, NOT in registry):** `construct_trades`, `evaluate_trades`, `summarize_trades`. Registering them later IS the canonical scaling-proof test (see §10).

### 2.2 Primitives (58 total, across 6 domain MCP servers)

Each primitive has a rich PM-phrased docstring already (when-to-use / when-NOT-to-use / parameters / examples) inside its domain's MCP server file — these are the priors L2 selectors will reason over. NO rewrite needed; they exist.

| Domain | File | Count | Role groups represented |
|---|---|---|---|
| `sovereign_bonds` | [rates_agent/sovereign_bonds/mcp_server.py](../rates_agent/sovereign_bonds/mcp_server.py) | 17 | yield level, curve spread (incl. the canonical 2s10s), OTR history, OTR-OFR spread, cross-market spread, butterfly, curve-move classifier, scanner, custom z-score, rolling regression, beta-adjusted spread, half-life, PCA, yield-change attribution PCA, panel builder, breakeven (cross-domain hook), NFP surprise |
| `inflation_indexed_bonds` | [rates_agent/inflation_indexed_bonds/mcp_server.py](../rates_agent/inflation_indexed_bonds/mcp_server.py) | 11 | real yield level, simple breakeven, forward breakeven, breakeven curve spread, cross-country breakeven spread, real-yield curve spread, cross-country real-yield spread, real-yield butterfly, breakeven butterfly, scanner, linker panel builder |
| `ois` | [rates_agent/ois/mcp_server.py](../rates_agent/ois/mcp_server.py) | 9 | OIS rate level, OIS curve spread, OIS butterfly, OIS forward rate, cross-market OIS spread, swap spread, scanner, financing rate, WIRP meeting-by-meeting pricing |
| `inflation_swaps` | [rates_agent/inflation_swaps/mcp_server.py](../rates_agent/inflation_swaps/mcp_server.py) | 9 | ZCIS level, ZCIS curve spread, ZCIS forward, cross-market ZCIS spread, swap-breakeven basis, ZCIS butterfly, scanner, ZCIS panel builder, CPI surprise |
| `policy_futures` | [rates_agent/policy_futures/mcp_server.py](../rates_agent/policy_futures/mcp_server.py) | 9 | futures price level, volume/OI snapshot, calendar spread, simple butterfly, cross-market spread, strip snapshot, pack average, scanner, strip panel builder |
| `bond_futures` | [rates_agent/bond_futures/mcp_server.py](../rates_agent/bond_futures/mcp_server.py) | 3 | rolling-generic price level, volume/OI, scanner |

### 2.3 Closed-family artifact types (6, in [shared/artifacts/registry.py](../shared/artifacts/registry.py))

`Series`, `SeriesSet`, `EventSet`, `Panel`, `WindowedPanel`, `ScalarMetric`. Single source of truth via `ArtifactTypeName` enum. Every operator slot's `artifact_type` is one of these.

### 2.4 Name-collision audit (the design hazard)

The current global resolver at [rates_agent/workflows/__init__.py](../rates_agent/workflows/__init__.py) ALREADY disambiguates a collision: `get_futures_price_level_tool` exists in BOTH `policy_futures` AND `bond_futures` MCP servers (same visible tool name). The resolver convention is:

- `bond_futures` keeps the bare tool name → resolver key `"get_futures_price_level_tool"` ([line 1079](../rates_agent/workflows/__init__.py:1079))
- `policy_futures` gets a domain prefix → resolver key `"policy_futures_get_futures_price_level_tool"` ([line 1147](../rates_agent/workflows/__init__.py:1147))

This is ad-hoc and not enforced by any contract today. The PR plan respects it AND wraps it: `BoundLeaf` will carry BOTH `mcp_tool_name` (what the L2 selector saw, used for lineage/audit) AND `resolver_tool_key` (what the executor dispatches on, derived from `(domain, mcp_tool_name)` by a small adapter). The same adapter is the seam where, in a follow-on cleanup, the project can migrate to a uniform `<domain>::<tool>` convention with zero impact on L2 selectors.

---

## 3. The pipeline (end-to-end, corrected)

Eight layers. Code does deterministic plumbing; LLMs do all understanding, selection, composition.

| Layer | Owner | Sees | Emits |
|---|---|---|---|
| **L0 Intake** | code | raw prompt, session refs (`@last`, etc.) | normalised prompt |
| **L1 Router** | LLM (one call) | normalised prompt + one-line domain cards (the only menu that grows with the instrument universe) | extended `RouteDecision`: `action` (single/multi/clarify) + `domains` + `intent_tag` ∈ {lookup, relationship, regression, cointegration, transform, event_regime, scan, panel, basis} + `decomposition` (list of named economic quantities with `name`, `nl_description`, `domain_hint`) |
| **L3 Composer** | LLM (one call) | prompt + L1 `decomposition` + L1 `intent_tag` + **full** operator catalogue (16 cards) + 6 artifact-type names + 4–6 golden few-shots | `ShapeSpec`: a Workflow whose leaves are typed `LeafHole`s; each hole carries a `LeafRequest` |
| **L2 Selectors** | LLM, one per in-scope domain, P11-isolated via own MCP subprocess | only own-domain primitives (the rich docstrings already in place) + one `LeafRequest` at a time | `BoundLeaf` (or explicit refusal) — chosen primitive, params, declared output type + role/basis/frequency + `mcp_tool_name` + `resolver_tool_key` |
| **L4 Assembler + Validate (Boundary A)** | code | shape + all `BoundLeaf`s + `OPERATOR_REGISTRY` + global resolver | substitute → validate (structured `ValidationResult`) → classify errors by `owner_layer` → one bounded repair round (additive only) → refuse on exhaustion |
| **L4.5 Coverage Gate (Boundary B)** | LLM (one call) | **original prompt** + L1 decomposition (supplementary) + assembled DAG + bindings | pass / refuse / one precise clarification. Compares the original prompt to an English echo of the DAG. Hard-block: never executes a low-confidence DAG. |
| **L5 Execute** | code | gated DAG | typed artifacts + content-addressed lineage + **intent chain** (router decomposition, each selector's lingo-resolution rationale, composer's wiring rationale, gate verdict) |
| **L6 Answer** | LLM | typed artifacts + extracted facts + prompt + L1 decomposition | trader-lingo answer that **echoes "what I understood and built" BEFORE the number**, with per-leaf provenance |

### 3.1 The canonical query (type-correct shape)

User: *"correlation between US 2s10s and 5Y breakeven over the last 5 years."*

```
L1 decomposition:
  - {name: us_2s10s,      nl: "UST curve spread, 2Y minus 10Y", domain_hint: sovereign_bonds}
  - {name: us_5y_breakeven, nl: "USD breakeven at 5Y tenor",    domain_hint: inflation_indexed_bonds}
  intent_tag: relationship

L3 ShapeSpec:
  LeafHole A (Series, role=spread_level,   domain_hint=sovereign_bonds)         ─┐
  LeafHole B (Series, role=breakeven_level, domain_hint=inflation_indexed_bonds) ─┤
                                                                                  │
                                                          align_series ──────────┴──► SeriesSet
                                                                                       │
                                              select_from_series_set(key=A) ◄──────────┤
                                              select_from_series_set(key=B) ◄──────────┘
                                                          │             │
                                                          └──────┬──────┘
                                                              correlation
                                                                  │
                                                              ScalarMetric ──► L6 answer
```

L2 bindings:
- Hole A → `BoundLeaf(domain=sovereign_bonds, mcp_tool_name=calculate_curve_spread_tool, resolver_tool_key=calculate_curve_spread_tool, params={curve_family:UST, short_tenor:2Y, long_tenor:10Y, lookback_days:1825}, output_field=time_series, declared_output_artifact_type=Series, declared_semantic_role=spread_level, …)`
- Hole B → `BoundLeaf(domain=inflation_indexed_bonds, mcp_tool_name=calculate_breakeven_inflation_simple_tool, resolver_tool_key=calculate_breakeven_inflation_simple_tool, params={curve_family:USD_TIPS, tenor:5Y, lookback_days:1825}, …)`

L4 validates structurally (Boundary A — type + role compat). L4.5 confirms both decomposition pieces are present and the English echo matches the prompt (Boundary B). L5 executes. L6 echoes "pulled US 2s10s from sovereigns, 5Y breakeven from linkers, aligned them on common dates, computed the full-period correlation" before the number.

---

## 4. PR plan

### 4.1 Sequencing and dependency graph

```
PR-1 (ValidationResult)  ─────┬─►  PR-4 (Assembler) ─┐
                              │                       │
PR-3 (Hole/fill contracts) ───┴─►  PR-4 ─────────────┤
                                                      │
PR-2 (Operator cards) ─────────►  PR-7 (Composer) ◄──┘
                                                      │
PR-5 (L1 decomposition) ───────►  PR-7 ──────────────┤
                                  PR-8 (Boundary B) ◄┘
                                                      │
PR-3 ─────────────────────────►   PR-6 (L2 selectors)─┤
                                                      │
PR-9 (Lineage + answer) ◄─── cross-cutting ───────────┤
                                                      ▼
                                                   PR-10 (Wiring + eval + scaling proofs)
```

**Parallel-safe foundations:** PR-1, PR-2, PR-3, PR-5 can be developed in parallel.
**Sequential after foundations:** PR-4 (needs 1+3), PR-6 (needs 3), PR-7 (needs 2+3+4+5), PR-8 (needs 5).
**Cross-cutting late:** PR-9, PR-10.

### 4.2 PR-by-PR

Each PR section below states: scope, files added/changed, decisions enforced, tests, acceptance criteria, dependencies. Reviewers may reject any PR whose acceptance criteria are not all green.

---

### PR-1 — Structured `ValidationResult`

**Why first.** Without structured multi-error validation, the repair loop has no feedback protocol. Every downstream piece (Assembler PR-4, role-compat in Boundary A, error classification in Composer PR-7) depends on this spine. Operator-card content (PR-2) is the highest-value LLM prior, but it feeds only one consumer (the Composer); `ValidationResult` feeds the entire repair architecture. Order by what unblocks the most downstream substrate.

**Scope.**
- Refactor [shared/workflow/validate.py](../shared/workflow/validate.py) so it collects ALL errors in one pass and returns a frozen `ValidationResult`.
- Assign each of the existing 13 `raise WorkflowValidationError` sites a stable error code and an `owner_layer` tag.
- Preserve the existing first-error-raise behaviour as a thin wrapper (`validate_workflow_strict`) so the current executor caller continues to work unchanged.

**Files.**
- `shared/workflow/validate.py` — refactor.
- `shared/workflow/validation_result.py` — NEW (frozen Pydantic `ValidationResult`, `ValidationError`, `ErrorCode` enum, `OwnerLayer` enum).
- `tests/workflow/test_validation_result.py` — NEW.

**Error code taxonomy (closed enum, stable across the lifetime of the substrate).**

| Code | Owner layer | Meaning | Raise site in current `validate.py` |
|---|---|---|---|
| `E_UNKNOWN_OPERATOR` | L3_WIRING | `OperatorNode.operator_name` not in registry | line 112 |
| `E_UNKNOWN_SLOT` | L3_WIRING | edge targets a slot the operator doesn't have | line 131 |
| `E_EDGE_TARGETS_PRIMITIVE` | L3_WIRING | edge targets a `PrimitiveNode` (illegal in v1) | line 143 |
| `E_LITERAL_TARGETS_NON_OPERATOR` | L3_WIRING | `LiteralBinding` targets non-operator node | line 160 |
| `E_LITERAL_UNKNOWN_SLOT` | L3_WIRING | `LiteralBinding` targets unknown slot | line 168 |
| `E_LITERAL_SLOT_NO_SCALAR` | L3_WIRING | `LiteralBinding` targets slot with `accepts_scalar=False` | line 179 |
| `E_ARITY_VIOLATION` | L3_WIRING | per-operator `arity_validator` rejected | line 231 |
| `E_UNBOUND_REQUIRED_SLOT` | L3_WIRING | required slot has no edge AND no literal | line 259 |
| `E_TYPE_MISMATCH` | L3_WIRING (when upstream is operator) / L2_BINDING (when upstream is primitive) | source artifact type ≠ slot's expected type | line 323 |
| `E_UNKNOWN_OUTPUT_FIELD` | L2_BINDING | primitive's `output_field` not in resolver's declared set | line 381 |
| `E_UNIT_MISMATCH` | L3_WIRING | per-operator `unit_validator` rejected | line 442 |
| `E_DAG_CYCLE` | L3_WIRING | DAG contains a cycle | line 461 |
| `E_PRIMITIVE_RESOLVE_FAIL` | L2_BINDING | resolver could not resolve the tool name | line 476 |

**Decisions enforced.**
- `owner_layer` is the dispatch key for the repair loop's "who fixes this" question. PR-4 reads it; PR-7 (composer) and PR-6 (selectors) react to it.
- Error codes are a closed `Literal[...]` per P8 — extensions are deliberate PRs.

**Tests.**
- One test per error code: synthesize a minimal workflow that triggers exactly that code.
- Multi-error workflow: triggers ≥3 codes in one pass; assert all 3 surface in `ValidationResult.errors`.
- Legacy wrapper: `validate_workflow_strict(bad_workflow)` raises `WorkflowValidationError` on the first error (unchanged behaviour).
- Owner-layer dispatch test: each error's `owner_layer` matches the table above.

**Acceptance criteria.**
1. All 13 raise sites converted; no `raise WorkflowValidationError` remains in `validate.py` proper (now lives only inside the strict wrapper).
2. `ValidationResult.errors` is a tuple (not list — frozen Pydantic).
3. Existing executor caller in [shared/workflow/executor.py:58](../shared/workflow/executor.py:58) imports unchanged path; existing tests stay green.
4. New tests (one per code + multi-error + legacy wrapper) green.
5. No new dependency on any operator's `config.yaml` (this PR is structure-only; content lands in PR-2).

**Dependencies.** None. First PR.

---

### PR-2 — Operator cards (descriptions in `config.yaml` + L3 catalogue renderer)

**Scope.**
- Author rich operator cards in each of the 16 operators' `config.yaml` files, structured under a new `card:` block. Reference shape is [shared/operators/correlation/config.yaml](../shared/operators/correlation/config.yaml) (already has most of this).
- Build `shared/workflow/operator_catalogue.py` — a renderer that reads BOTH (a) each operator's `config.yaml` `card:` block and (b) the operator's slot/output structure from `OPERATOR_REGISTRY`, and produces one consolidated `OperatorCard` per operator. This is the artifact the Composer (PR-7) will see in its prompt.
- Add a `config_path: Path` field to `OperatorSpec` (mirroring `PrimitiveSpec.config_path`) so the renderer can find each operator's `config.yaml` without hardcoding paths.

**Per-operator `card:` block schema (added to each `config.yaml`):**

```yaml
card:
  one_line: >-
    Single-sentence summary the LLM uses to recognise this operator.
  when_to_use:
    - "Use when the user asks about X."
    - "Use when the prompt mentions Y."
  when_not_to_use:
    - "Do NOT use for Z — use [sibling_operator] instead."
  upstream_requirements:
    - "Inputs must already share a DatetimeIndex (use align_series + select_from_series_set ×2 upstream)."
  downstream_pattern:
    - "Output is ScalarMetric; feeds L6 answer directly."
  knobs_quick_ref:
    method: "pearson (default) | spearman | kendall (not yet implemented — see planned_extensions)"
    min_periods: "2 by default; raise for noisier domains."
  example_shapes:
    - description: "Full-sample correlation between two daily series."
      shape: "align_series([A, B]) -> select_from_series_set ×2 -> correlation"
  sibling_operators:
    - rolling_correlation:    "If the user asks for time-varying correlation (rolling window), pick rolling_correlation instead."
    - cointegration:          "If the user asks if a SPREAD is stationary (pairs trading), pick cointegration."
    - rolling_regression:     "If the user asks for hedge ratios / betas (one explanatory variable), pick rolling_regression."
```

**The `OperatorCard` (rendered, what the Composer's prompt sees):**

```python
class OperatorCard(BaseModel):
    operator_name: str
    one_line: str
    when_to_use: tuple[str, ...]
    when_not_to_use: tuple[str, ...]
    upstream_requirements: tuple[str, ...]
    downstream_pattern: tuple[str, ...]
    knobs_quick_ref: dict[str, str]
    example_shapes: tuple[ExampleShape, ...]
    sibling_operators: dict[str, str]
    input_slots: dict[str, SlotDescriptor]   # from registry — NOT duplicated in YAML
    output: OutputDescriptor                  # from registry — NOT duplicated in YAML
```

**Decisions enforced.**
- **P10 strict.** Description content lives in `config.yaml` ONLY. Slot/output structure lives in `OPERATOR_REGISTRY` ONLY. The renderer fuses them. Neither side duplicates the other's content.
- Token-budget cap: each card's full rendered prompt-form must be ≤ 600 tokens (enforced by a test). With 16 operators that puts the catalogue at ≤ 9.6K tokens — bounded and cheap to send to L3 every call.
- The four pair-stats operators (`correlation` / `rolling_correlation` / `cointegration` / `rolling_regression`) MUST cross-reference each other in their `sibling_operators` blocks — this is the most common Composer mistake and the cross-refs are the primary mitigation.

**Files.**
- `shared/operators/*/config.yaml` — add `card:` block to each of the 16 (some already have partial content).
- `shared/workflow/operator_catalogue.py` — NEW.
- `shared/workflow/registry.py` — add `config_path: Path` to `OperatorSpec` and populate at registration.
- `tests/workflow/test_operator_catalogue.py` — NEW.

**Tests.**
- Every registered operator has a non-empty `card:` block; loader green.
- Each card passes the schema validation (all fields populated, all `when_to_use` non-empty, etc.).
- Token-budget test: rendered card ≤ 600 tokens for each operator.
- Sibling cross-reference test: each of the four pair-stats operators names the other three in `sibling_operators`.
- Renderer determinism: same `OperatorSpec` + same `config.yaml` → byte-identical `OperatorCard.model_dump_json()` (so the L3 prompt is cacheable).

**Acceptance criteria.**
1. All 16 operators have rich `card:` blocks.
2. Catalogue renderer returns `dict[str, OperatorCard]` keyed by operator name.
3. P10: no description content is duplicated between `config.yaml` and `OPERATOR_REGISTRY`.
4. Each card ≤ 600 tokens; total catalogue ≤ 10K tokens.
5. No imports from `rates_agent/` (P11/P9 preserved).

**Dependencies.** None (parallel-safe with PR-1, PR-3, PR-5).

---

### PR-3 — Hole/fill contracts + resolver-safe primitive identity

**Scope.**
- New typed contracts that L3 emits and L2 fills.
- Extended `PrimitiveResolver` protocol with a `declare_output_type(tool_name, params)` method so a selector can declare a primitive's output type without executing it.
- Resolver-key adapter: a small `domain_to_resolver_key(domain, mcp_tool_name) -> str` function that encodes the current ad-hoc convention (bond_futures keeps bare name; policy_futures gets `policy_futures_` prefix; everything else uses bare name today). Centralised so a follow-on cleanup can migrate to uniform `<domain>::<tool>` with zero touches elsewhere.

**Files.**
- `shared/workflow/holes.py` — NEW. Contains:
  - `LeafHole(node_id, leaf_request)` — what appears in a `ShapeSpec` in place of a primitive
  - `LeafRequest(...)` — what the Composer declares per hole
  - `BoundLeaf(...)` — what each L2 selector returns
  - `ShapeSpec(...)` — a Workflow-with-holes (extends `Workflow` to allow `LeafHole` nodes)
- `shared/workflow/resolver_keys.py` — NEW. Contains `domain_to_resolver_key()` plus the centralised collision table.
- `shared/workflow/registry.py` — extend `PrimitiveResolver` protocol with `declare_output_type`.
- `rates_agent/workflows/__init__.py` — implement `declare_output_type` on the rates resolver (reads `PrimitiveSpec.output_artifact_type` — no execution).
- `tests/workflow/test_holes.py` — NEW.
- `tests/workflow/test_resolver_keys.py` — NEW.

**Contract schemas.**

```python
class LeafRequest(BaseModel):
    # Closed-substrate fields (HARD-validated by Boundary A):
    required_artifact_type: ArtifactTypeName          # one of the 6 closed-family types
    expected_units: Optional[str] = None              # one of TimeSeriesUnits values, or None for "any"
    expected_frequency: Optional[str] = None          # "daily" | "weekly" | "monthly" | None
    domain_hint: Domain                                # the L1-router's domain assignment

    # Free-form fields (SOFT signals — normalized-string compared, mismatches escalate to Boundary B):
    semantic_role: str                                 # e.g. "spread_level", "breakeven_level", "regime_event"
    requested_output_meaning: str                      # one-sentence English; the answer this leaf should provide
    nl_intent: str                                     # plain-English description of what to fetch

class BoundLeaf(BaseModel):
    leaf_id: str                                       # matches LeafHole.node_id
    domain: Domain                                     # echoed from request
    mcp_tool_name: str                                 # the visible tool name the L2 selector saw via MCP
    resolver_tool_key: str                             # derived via domain_to_resolver_key(domain, mcp_tool_name)
    params: dict[str, Any]                             # the primitive's input params
    output_field: str                                  # which time_series* field to lift to artifact

    # Closed-substrate declarations (HARD-validated against LeafRequest):
    declared_output_artifact_type: ArtifactTypeName
    declared_units: Optional[str] = None
    declared_frequency: Optional[str] = None

    # Free-form declarations (SOFT signals):
    declared_semantic_role: str
    declared_output_meaning: str

    fit_confidence: float                              # selector's self-assessment, [0,1]
    refusal: Optional[str] = None                      # if set, selector declined to bind; explains why

class LeafHole(BaseModel):                             # appears in ShapeSpec in place of PrimitiveNode
    node_id: str
    leaf_request: LeafRequest

class ShapeSpec(BaseModel):                            # what L3 emits
    workflow_id: str
    nodes: list[Union[LeafHole, OperatorNode]]         # NO PrimitiveNode at this stage
    edges: list[WorkflowEdge]
    literal_bindings: list[LiteralBinding]
    terminal_node_id: str
```

**Decisions enforced (the role-discriminant nuance Codex correctly pushed on).**

| Field family | Comparison rule | If mismatch |
|---|---|---|
| Closed-substrate (`required_artifact_type`, `expected_units`, `expected_frequency`) | Hard structured equality against the corresponding `declared_*` on `BoundLeaf` | `E_TYPE_MISMATCH` / `E_UNIT_MISMATCH` / `E_FREQUENCY_MISMATCH` raised by Boundary A; L4 dispatches repair to `owner_layer=L2_BINDING` |
| Free-form (`semantic_role`, `requested_output_meaning`) | Normalised string compare (lowercase, trim, collapse whitespace) → if equal, pass; if unequal, attach as a SOFT signal to the `ValidationResult` (new severity: `WARNING`); Boundary A does NOT fail on warnings | If warnings exist when Boundary A passes, Boundary B receives them as supplementary evidence and is more likely to refuse if the DAG/prompt echo doesn't match |

This split is the explicit answer to R5 (no curated role enum): the substrate gets hard structured checks only on the things that are GENUINELY closed (artifact_type is already an enum; units is `TimeSeriesUnits`; frequency is a small closed set). For everything else the LLMs author free-form English and the substrate does normalised-equality contradiction detection — strict enough to catch obvious contradictions, loose enough to not curate a vocabulary.

**Resolver-key adapter:**

```python
# shared/workflow/resolver_keys.py
_DOMAIN_PREFIXED: set[Domain] = {Domain.POLICY_FUTURES}
_BARE_NAME_DOMAINS: set[Domain] = {
    Domain.SOVEREIGN_BONDS, Domain.OIS, Domain.INFLATION_INDEXED_BONDS,
    Domain.INFLATION_SWAPS, Domain.BOND_FUTURES,
}

def domain_to_resolver_key(domain: Domain, mcp_tool_name: str) -> str:
    """Translate (domain, MCP-visible tool name) → the resolver key registered
    in rates_agent/workflows/__init__.py. Centralises the current ad-hoc
    convention (bond_futures keeps bare names; policy_futures gets a
    policy_futures_ prefix) so a future migration to uniform <domain>::<tool>
    is a one-file change."""
    if domain in _DOMAIN_PREFIXED:
        return f"{domain.value}_{mcp_tool_name}"
    return mcp_tool_name
```

**Tests.**
- Round-trip `LeafRequest` and `BoundLeaf` through JSON; assert frozen/immutable.
- `domain_to_resolver_key(POLICY_FUTURES, "get_futures_price_level_tool") == "policy_futures_get_futures_price_level_tool"`.
- `domain_to_resolver_key(BOND_FUTURES, "get_futures_price_level_tool") == "get_futures_price_level_tool"`.
- Resolver smoke test: both resolver keys resolve to DIFFERENT `PrimitiveSpec`s through `rates_agent.workflows`' resolver.
- `declare_output_type("calculate_curve_spread_tool", {...})` returns `"Series"` without executing the primitive (no DB call).

**Acceptance criteria.**
1. `ShapeSpec` / `LeafHole` / `LeafRequest` / `BoundLeaf` defined, frozen Pydantic.
2. `domain_to_resolver_key` covers all 6 domains; collision (`get_futures_price_level_tool`) explicitly tested both ways.
3. `PrimitiveResolver.declare_output_type` implemented in `rates_agent/workflows`; no DB engine touched.
4. No coupling between `shared/workflow/` and any `rates_agent/` package (P11/P9 preserved).

**Dependencies.** None (parallel-safe with PR-1, PR-2, PR-5).

---

### PR-4 — Assembler + bounded-repair controller

**Scope.**
- A pure-code module that takes a `ShapeSpec` + a list of `BoundLeaf`s, substitutes leaves into holes, and validates the resulting `Workflow` via PR-1's `ValidationResult`. On errors, dispatches one repair round (additive-only) by owner layer; on continued failure, refuses.

**Files.**
- `shared/workflow/assembler.py` — NEW. Contains `Assembler.assemble(shape, leaves) -> AssemblyResult`.
- `tests/workflow/test_assembler.py` — NEW.

**Repair-mutation rules (the discipline that prevents oscillation).**

| Mutation | Allowed? | Rationale |
|---|---|---|
| Insert adapter node (e.g. `convert_units`, `align_series`) between two existing nodes to fix a unit/frequency mismatch | ✅ | Additive; preserves intent. |
| Rebind a `BoundLeaf`'s `params` (e.g. change a `lookback_days` value) when the selector flagged a fixable mismatch | ✅ | Additive within the leaf's scope; selector is asked to re-bind. |
| Fix a slot wiring (re-route an edge to a different already-existing slot on the same operator) | ✅ | Additive within the shape. |
| Re-pick a different primitive for the same hole | ❌ | This is "re-bind, then re-shape, then re-bind" → oscillation. Refuse instead. |
| Re-shape the DAG topology (add new operators, remove operators, change pair-stats operator choice) | ❌ | Composer's shape is the contract. Re-shape = re-prompt the user, not retry. |
| Multi-round repair | ❌ | One repair round only. If still broken, refuse. |

**Repair-round protocol (one shot):**

1. `validate_workflow(assembled) -> ValidationResult`. If clean, return.
2. Partition `ValidationResult.errors` by `owner_layer`:
   - `ASSEMBLER` errors (e.g. resolver-key dispatch failure, missing leaf): the assembler retries the substitution mechanically. No LLM round-trip.
   - `L3_WIRING` errors: call back into the Composer (PR-7) with the full error list and the shape; Composer emits an **additive patch** (`AddAdapterNode` / `ReWireEdge`) only — never a new `ShapeSpec`.
   - `L2_BINDING` errors: call back into the relevant per-domain Selector (PR-6) with the full error list and the leaf's current `BoundLeaf`; Selector emits an updated `BoundLeaf` (or refuses).
3. Re-substitute, re-validate. If clean, return. If still broken, return `AssemblyResult(status=REFUSED, errors=…)`.

**Decisions enforced.**
- One repair round, additive only. No oscillation.
- Refusal is a first-class outcome; the L4.5 gate is the next gate after refusal — it can either escalate to user (one precise clarification) or terminate.
- The assembler NEVER invents bindings or fills missing data — it only orchestrates Composer/Selector retries on the LLM-authored content.

**Tests.**
- Clean assembly: canonical 2s10s × 5Y breakeven shape + correct leaves → `AssemblyResult(status=CLEAN)`.
- Adapter insertion: shape with unit mismatch → repair inserts `convert_units` → second validate green.
- Refusal on re-pick: shape where the only legal fix would require swapping a primitive → `AssemblyResult(status=REFUSED, errors=[…])`.
- Owner-layer dispatch: each error code in PR-1's taxonomy routes to the correct repair owner.
- Resolver-key handling: a `BoundLeaf` with mismatched `(domain, mcp_tool_name)` → `E_PRIMITIVE_RESOLVE_FAIL`, dispatched to `L2_BINDING`.

**Acceptance criteria.**
1. `Assembler.assemble(shape, leaves)` returns `AssemblyResult` with `status ∈ {CLEAN, REFUSED}` and full error/repair trace.
2. The trace lists every mutation applied in repair (auditable).
3. Tests cover each error code in PR-1's taxonomy at least once.
4. No new LLM call inside the assembler itself — it only invokes Composer/Selector callbacks supplied by the caller.

**Dependencies.** PR-1 (error codes), PR-3 (contracts). Parallel-safe after those land.

---

### PR-5 — L1 router decomposition extension

**Scope.**
- Extend the existing `RouteDecision` ([orchestrator/contracts.py](../orchestrator/contracts.py)) with `intent_tag` and `decomposition` fields.
- Update the supervisor's system prompt ([orchestrator/prompts.py:46](../orchestrator/prompts.py:46)) to emit the new fields.
- Keep the existing demote-to-clarify normalisation logic intact (`_normalise_route_decision` in [orchestrator/supervisor.py:251](../orchestrator/supervisor.py:251)).

**Files.**
- `orchestrator/contracts.py` — extend `RouteDecision` and add `IntentTag`, `EconomicQuantity`.
- `orchestrator/prompts.py` — update `SUPERVISOR_SYSTEM_PROMPT` to elicit `intent_tag` + `decomposition`; add few-shots for the canonical decomposition shape (e.g. "5y5y real yield" → forward(nominal_sovereign_yield, breakeven_from_linkers)).
- `orchestrator/supervisor.py` — extend `_normalise_route_decision` if needed for the new fields.
- `tests/orchestrator/test_l1_decomposition.py` — NEW.

**Schema additions.**

```python
class IntentTag(str, Enum):
    LOOKUP        = "lookup"
    RELATIONSHIP  = "relationship"
    REGRESSION    = "regression"
    COINTEGRATION = "cointegration"
    TRANSFORM     = "transform"
    EVENT_REGIME  = "event_regime"
    SCAN          = "scan"
    PANEL         = "panel"
    BASIS         = "basis"

class EconomicQuantity(BaseModel):
    name: str                       # short slug, e.g. "us_2s10s"
    nl_description: str             # one-sentence English meaning
    domain_hint: Domain

class RouteDecision(BaseModel):     # EXTENDED
    action: RouteAction
    domains: list[Domain]
    rationale: str
    clarification_question: Optional[str] = None
    adjustments: list[str] = []
    # NEW:
    intent_tag: Optional[IntentTag] = None
    decomposition: list[EconomicQuantity] = []
```

**Tests.**
- Canonical query → `intent_tag=relationship`, `decomposition=[us_2s10s (sovereign_bonds), us_5y_breakeven (inflation_indexed_bonds)]`.
- Composite-noun query ("5y5y real") → decomposition lists both legs (nominal + breakeven).
- Clarify path: no decomposition required when action is clarify.
- Eval set covering each `IntentTag`: at least one prompt per tag.

**Acceptance criteria.**
1. `RouteDecision` carries `intent_tag` + `decomposition`; backwards-compatible default `[]`.
2. Single-domain queries STILL produce decomposition (one entry) — this is the coverage oracle for the gate even in single-domain mode.
3. Existing supervisor tests stay green.

**Dependencies.** None (parallel-safe with PR-1, PR-2, PR-3).

---

### PR-6 — L2 selectors as hole-fillers

**Scope.**
- Add a `fill_leaf(request: LeafRequest) -> BoundLeaf` mode to `DomainAgentSession` ([orchestrator/domain_agent.py](../orchestrator/domain_agent.py)). Same MCP isolation. The selector calls primitives in "describe my output type" mode (no execution) — the primitive resolver's `declare_output_type` is the channel.
- New per-domain selector prompt that frames the task as "pick the primitive that satisfies this LeafRequest; refuse if no primitive fits." Existing rich primitive docstrings via MCP are the priors.
- Existing ReAct mode preserved — used for legacy template-less paths.

**Files.**
- `orchestrator/domain_agent.py` — add `fill_leaf` method (separate code path from `run`).
- `orchestrator/prompts.py` — new `SELECTOR_FILL_LEAF_SYSTEM_PROMPT` (one prompt template per domain via existing _DOMAIN_PROMPTS pattern, or a single template parameterised by domain).
- `tests/orchestrator/test_selectors_fill_leaf.py` — NEW.

**Selector contract.**

```python
class DomainAgentSession:
    async def fill_leaf(
        self,
        request: LeafRequest,
        timeout_s: float = 10.0,
    ) -> BoundLeaf:
        """Bind a single leaf or refuse. No primitive execution.
        Returns BoundLeaf with refusal set if no primitive fits the request."""
        ...
```

**Decisions enforced.**
- "Refuse rather than bind nearest." Explicit in prompt; few-shots include refusal cases.
- Selector returns a SINGLE BoundLeaf (or refusal). No multi-binding in this PR — composition is L3's job.
- Selector NEVER invents `resolver_tool_key` — it returns `mcp_tool_name` + `domain`, and the assembler derives the resolver key via PR-3's `domain_to_resolver_key`.

**Tests.**
- Canonical: sovereign selector receives `LeafRequest(role=spread_level, nl_intent="US 2s10s, last 5 years")` → returns `BoundLeaf(mcp_tool_name=calculate_curve_spread_tool, params={curve_family:UST, short_tenor:2Y, long_tenor:10Y, lookback_days≈1825}, declared_output_artifact_type=Series, …)`.
- Inflation-linked selector: `LeafRequest(role=breakeven_level, nl_intent="USD 5Y breakeven, last 5 years")` → returns `BoundLeaf(mcp_tool_name=calculate_breakeven_inflation_simple_tool, …)`.
- Refusal: `LeafRequest(role=spread_level, nl_intent="JPY OIS swap spread")` to sovereign selector → returns `BoundLeaf(refusal="JPY OIS is not in this domain; route to OIS specialist.")`.
- Output-type declaration accuracy: declared type matches what `declare_output_type` returns for the chosen tool.
- P11 holds: selector's MCP client only sees own-domain tools (assert via `_tool_names`).

**Acceptance criteria.**
1. `fill_leaf` works for every one of the 6 domains.
2. Refusal path tested in every domain.
3. Selector never executes a primitive in `fill_leaf` mode (verified by MCP-call inspector).
4. Existing `run` (ReAct) mode unaffected.

**Dependencies.** PR-3 (contracts).

---

### PR-7 — L3 Composer

**Scope.**
- The new heart of the open-DAG path. A structured-output LLM call that reads the prompt + L1 decomposition + full operator catalogue (16 cards from PR-2) + 6 artifact types + 4–6 golden few-shots, and emits a `ShapeSpec`.
- Wires into PR-4's repair loop: when the assembler raises L3_WIRING errors, the composer is called back with the full error list and emits an additive patch (NOT a new shape).

**Files.**
- `orchestrator/composer.py` — NEW. Contains `Composer.compose(prompt, decomposition, intent_tag) -> ShapeSpec` and `Composer.repair(shape, errors) -> Patch`.
- `orchestrator/prompts.py` — `COMPOSER_SYSTEM_PROMPT` and `COMPOSER_REPAIR_PROMPT`.
- `orchestrator/composer_golden_shapes.py` — NEW. Constant module with 4–6 reference shapes the prompt embeds as few-shots.
- `tests/orchestrator/test_composer.py` — NEW.

**Golden few-shots (the minimum set, baked into the prompt).**

| # | Intent | Shape |
|---|---|---|
| 1 | relationship (full-sample correlation) | `2 leaves → align_series → select ×2 → correlation → ScalarMetric` |
| 2 | relationship (rolling correlation) | `2 leaves → align_series → select ×2 → rolling_correlation → Series` |
| 3 | cointegration (pairs stationarity) | `2 leaves → align_series → select ×2 → cointegration → ScalarMetric` |
| 4 | regression (rolling beta) | `2 leaves → align_series → select ×2 → rolling_regression → SeriesSet` |
| 5 | event_regime (NFP-conditional yield move) | `1 leaf → threshold_events → event_windows(target=2nd leaf) → conditional_aggregate → Series` |
| 6 | transform (rolling z-score) | `1 leaf → rolling_zscore → Series` |

**Decisions enforced.**
- Composer NEVER picks a primitive. Every leaf is a `LeafHole` with a fully specified `LeafRequest`.
- Composer NEVER mutates a shape in repair. It only emits additive patches (`AddAdapter`, `ReWireEdge`) — the assembler applies them deterministically.
- Composer ALWAYS sees the full 16-operator catalogue. No shortlister.

**Tests.**
- Canonical query → shape matches golden #1 exactly (modulo node_ids).
- Each intent_tag → composer picks an operator from the right family (eval set per `IntentTag`).
- Repair: given an `E_UNIT_MISMATCH` error pointing at an edge, composer emits `AddAdapter(convert_units)` on that edge — not a new shape.
- Refusal-on-impossible: prompt names a quantity for which no operator family fits → composer emits a `LeafHole` whose `LeafRequest.nl_intent` flags the gap; assembler routes to Boundary B.
- Token-budget audit: composer's prompt input ≤ 25K tokens (catalogue + few-shots + L1 output + prompt).

**Acceptance criteria.**
1. Composer emits a structurally valid `ShapeSpec` for each canonical query in the eval set.
2. Repair callback emits only additive patches (assembler test enforces this).
3. Composer prompt contains no primitive names anywhere (grep test).
4. Composer prompt contains the full operator catalogue (assert all 16 names appear).

**Dependencies.** PR-2 (cards), PR-3 (contracts), PR-4 (assembler / repair loop), PR-5 (L1 decomposition).

---

### PR-8 — Boundary B (coverage + coherence gate)

**Scope.**
- A hard-block LLM call between assembly and execution. Compares the **original user prompt** against an English echo of the assembled DAG. Treats L1 decomposition as supplementary evidence (NOT the source of truth, per Codex's correction — if L1 dropped a domain, the decomposition is already wrong).
- Decision: pass / refuse / one precise clarification.

**Files.**
- `orchestrator/coverage_gate.py` — NEW.
- `orchestrator/prompts.py` — `COVERAGE_GATE_SYSTEM_PROMPT`.
- `shared/workflow/dag_echo.py` — NEW. Code-rendered English description of an assembled Workflow + bindings (no LLM); the gate uses this as deterministic input.
- `tests/orchestrator/test_coverage_gate.py` — NEW.

**Gate output contract.**

```python
class GateVerdict(BaseModel):
    status: Literal["PASS", "REFUSE", "CLARIFY"]
    reason: str                             # always populated, audit trail
    clarification_question: Optional[str]   # populated iff status == CLARIFY

    # SOFT inputs from Boundary A (PR-3): if any role-mismatch WARNINGs were
    # collected during validation, they're surfaced here so the gate can weigh
    # them against the prompt match.
    soft_warnings: list[str] = []
```

**Decisions enforced (per Codex's correction point 4).**
- The SOURCE OF TRUTH for the coverage check is the **original prompt**, not L1 decomposition.
- L1 decomposition is supplementary evidence — useful to the gate's reasoning, but if it contradicts the prompt, the prompt wins.
- Soft warnings from Boundary A (role/intent free-form mismatches) bias the gate toward refuse-or-clarify but do not force it.
- Clarification is preferred to outright refusal IFF the gap is fixable by user input (e.g. ambiguous tenor). Outright refusal is reserved for impossible-given-universe cases.

**Tests.**
- Under-scoped query (L1 dropped a domain → DAG is partial) → gate returns `REFUSE` or `CLARIFY`.
- Type-legal but role-mismatched DAG (composer asked for "return series", selector bound a "level series" and they declared mismatching free-form roles → Boundary A surfaced warnings) → gate returns `CLARIFY`.
- Coherent DAG matching prompt → `PASS`.
- Composite-noun ambiguity ("5y5y" without a market) → exactly ONE precise `clarification_question`, not free-form prose.
- Adversarial: a DAG whose English echo plausibly answers a *different* question → `REFUSE` with a specific reason.

**Acceptance criteria.**
1. Gate's prompt cites the original user prompt verbatim, decomposition as supplementary evidence.
2. Hard-block enforced: pipeline never executes when `status != PASS`.
3. Refusal/clarification reasons are always populated; observable in lineage.
4. Soft warnings from Boundary A surface in the gate's input.

**Dependencies.** PR-5 (L1 decomposition for supplementary evidence), PR-4 (assembled DAG to gate).

---

### PR-9 — Lineage intent chain + L6 answer template

**Scope.**
- Extend lineage to record the full intent chain alongside the compute chain.
- L6 answer template: intent echo → number → per-leaf provenance footer.

**Files.**
- `shared/artifacts/lineage.py` — add `IntentChain` record (router decomposition, selector lingo-resolution rationales, composer wiring rationale, gate verdict).
- `orchestrator/answer.py` — NEW or extend existing answer rendering with the intent-echo template.
- `orchestrator/prompts.py` — `ANSWER_SYSTEM_PROMPT` updated with the intent-first format.
- `tests/orchestrator/test_answer_intent_echo.py` — NEW.

**Answer template (the format L6 must follow):**

```
[1 paragraph — "Here is what I understood and built":]
  • Decomposed: <L1 decomposition in trader lingo>
  • Pulled: <each leaf + its primitive + domain, in trader lingo>
  • Wired: <the operator pipeline in trader lingo>

[1 paragraph — the answer in trader lingo, with the number:]
  <answer>

[Provenance footer — collapsible / small:]
  • Leaf A: <tool> · <domain> · <params>
  • Leaf B: <tool> · <domain> · <params>
  • Lineage hash: <head_hash>
```

**Decisions enforced.**
- Intent echo MUST come first. The lineage hash is in the provenance footer, NOT presented as a correctness seal (R9: reproducibility, not correctness).
- No PR-9 answer is generated for a refused/clarified gate verdict — the gate's clarification message goes to the user instead.

**Tests.**
- Canonical query → answer template populated with both intent and number; provenance footer has correct lineage hash.
- Smoke: answer template never displays the lineage hash without the intent echo.
- Lineage record contains `IntentChain` with all four sub-records (router/selectors/composer/gate).

**Acceptance criteria.**
1. Every answer in the new lane echoes intent before number.
2. Lineage record includes `IntentChain`.
3. PM-facing copy never refers to the lineage hash as proof of correctness.

**Dependencies.** None strict, but lands late (after PR-7 / PR-8 are functional end-to-end).

---

### PR-10 — End-to-end wiring + eval suite + scaling proofs

**Scope.**
- A new lane `orchestrator/open_dag_pipeline.py` that stitches L1 → L3 → L2 → L4 → L4.5 → L5 → L6 together.
- A pre-router in `CopilotSession` (very thin) that picks between the existing template path and the new open-DAG path. Simplest rule for PoC: if a template-router would match cleanly, use the template lane; otherwise route to open-DAG. (This pre-router is the only piece that *both* paths touch — kept deliberately tiny.)
- The full eval suite covering every operator family.
- The three scaling proofs (the actual gating deliverable).
- CI lints.

**Files.**
- `orchestrator/open_dag_pipeline.py` — NEW.
- `orchestrator/session.py` — small extension to route new lane.
- `tests/eval/test_open_dag_eval_matrix.py` — NEW.
- `tests/eval/test_scaling_proofs.py` — NEW.
- `tests/eval/synthetic_primitive_59.py` + `tests/eval/synthetic_operator_17.py` — NEW. Synthetic fixtures that simulate a 59th primitive and a 17th operator being registered.
- `scripts/ci_lint_domain_tool_count.py` — NEW. Fails when any domain MCP server registers > N primitives (force sub-domain split).
- `.github/workflows/` (or equivalent) — wire the lint into CI.

**Eval matrix (one canonical query per intent family — Codex's correction point 3).**

| Intent | Canonical query | Expected shape |
|---|---|---|
| LOOKUP | "Where is the US 10Y vs its 1-year range?" | `1 leaf (get_yield_levels_tool) → percentile_rank → Series` |
| RELATIONSHIP (full-sample) | "Correlation between US 2s10s and 5Y breakeven over 5y" | golden #1 |
| RELATIONSHIP (rolling) | "Rolling 1y correlation between SOFR 2s10s and UST 2s10s" | golden #2 |
| REGRESSION | "Rolling 1y beta of BTP-Bund to Bund 10Y yield" | golden #4 |
| COINTEGRATION | "Is the 5s30s curve stationary over the last 5 years?" | `1 leaf (curve_spread) → cointegration → ScalarMetric` (single-input variant) |
| TRANSFORM | "Z-score of the SOFR 5Y vs its 1y history" | golden #6 |
| EVENT_REGIME | "Average UST 10Y move 5 days after each NFP surprise > 50K" | `1 leaf (NFP surprise) → threshold_events → event_windows(target=UST 10Y) → conditional_aggregate → Series` |
| SCAN | "Show the 5 biggest dislocations in OIS curve spreads today" | `1 leaf (scan_ois_extremes_tool) → ScalarMetric/SeriesSet → L6` (scanner already returns ranked) |
| PANEL | "Build a panel of every UST curve spread today" | `1 leaf (build_sovereign_yield_panel_tool) → Panel` |
| BASIS | "Basis between USD 5Y linker breakeven and 5Y inflation swap" | `2 leaves (breakeven, ZCIS) → align_series → select ×2 → series_arithmetic.subtract → Series` |
| MESSY LINGO #1 | "twos tens vs five year breakeven correl, five years back" | shape == RELATIONSHIP canonical (proves lingo robustness) |
| MESSY LINGO #2 | "where are reds vs greens in SOFR strip" | shape uses policy_futures pack-average tool |
| MESSY LINGO #3 | "how rich is BTP-Bund vs 3-year history" | `1 leaf (cross_market_spread BTP-Bund) → percentile_rank → ScalarMetric` |
| ADVERSARIAL — under-scoped | "give me correlation" (no decomposable nouns) | gate returns CLARIFY with one question |
| ADVERSARIAL — role-mismatched | "correlate the LEVEL of UST 10Y with the CHANGE in 5Y" (would require diff on one side) | composer must insert `series_arithmetic.diff` on one leg OR gate refuses with reason |

The gating metric per the handoff is **shape and intent correctness on this matrix** — NOT execution success on the clean canonical query.

**Scaling proofs (the three from the handoff).**

| Proof | Test |
|---|---|
| Registration-only growth | Add a synthetic 59th primitive (`tests/eval/synthetic_primitive_59.py`) to one domain + a synthetic 17th operator (`tests/eval/synthetic_operator_17.py`) to the registry. Assert via `git diff` that `orchestrator/composer.py`, `orchestrator/coverage_gate.py`, `shared/workflow/validate.py`, `shared/workflow/executor.py`, `orchestrator/prompts.py:SUPERVISOR_SYSTEM_PROMPT`, and every OTHER domain's MCP server file are byte-for-byte unchanged. Then prove a fresh query using the new tools composes correctly. |
| Context-bound | Per query in the eval matrix, instrument the pipeline to record (a) each L2 selector's MCP-visible tool count = only own-domain tools; (b) the composer's prompt does NOT mention any primitive name (grep); (c) the composer's prompt token count is unchanged when the 59th primitive is registered (delta == 0 ± token-counter noise). |
| Two-boundary | The three adversarial entries in the eval matrix above. Plus: a contradictory free-form `semantic_role` between LeafRequest and BoundLeaf → Boundary A surfaces as WARNING → gate returns CLARIFY. |

**CI lints.**
- `scripts/ci_lint_domain_tool_count.py`: parse each domain MCP server, count `@mcp.tool()` decorators, fail if any domain exceeds `MAX_TOOLS_PER_DOMAIN` (TBD; suggest 25 for the PoC — forces a sub-domain split before fan-out gets unmanageable).
- A grep test that fails CI if any operator's `config.yaml` description content is duplicated as a Python string anywhere under `shared/operators/` or `shared/workflow/` (P10 enforcement).
- A grep test that fails CI if any file under `shared/operators/` imports from `rates_agent/` (P9 enforcement).

**Decisions enforced.**
- The PoC is GATED on the scaling proofs, NOT on the canonical query alone. A green canonical query without green scaling proofs is failure.
- Backtest is EXPLICITLY out of PoC scope; the trade operators are tracked for a follow-on registration-only growth PR that will reuse the same scaling-proof harness.

**Acceptance criteria.**
1. Every eval-matrix entry passes shape/intent correctness.
2. All three scaling proofs green.
3. CI lints integrated and enforced on PRs.
4. Pre-router cleanly separates lanes; existing template lane unaffected (existing template tests stay green).

**Dependencies.** Everything else (PR-1 through PR-9).

---

## 5. Eval matrix (full)

See PR-10 §4.2. Tabular form repeated here for top-of-file reference. The eval is the gating metric for the PoC.

| Intent family | Canonical query | Eval-pass means |
|---|---|---|
| LOOKUP | "Where is US 10Y vs 1y range?" | Composer emits `get_yield_levels → percentile_rank` shape |
| RELATIONSHIP (full) | "Corr US 2s10s × 5Y breakeven, 5y" | Composer emits `align → select×2 → correlation` |
| RELATIONSHIP (rolling) | "Rolling 1y corr SOFR 2s10s × UST 2s10s" | Composer emits `align → select×2 → rolling_correlation` |
| REGRESSION | "Rolling beta BTP-Bund to Bund 10Y" | Composer emits `align → select×2 → rolling_regression` |
| COINTEGRATION | "5s30s stationarity over 5y" | Composer emits `curve_spread → cointegration` |
| TRANSFORM | "Z-score SOFR 5Y vs 1y" | Composer emits `OIS_level → rolling_zscore` |
| EVENT_REGIME | "UST 10Y move +5d after NFP surprises > 50K" | Composer emits `NFP → threshold → event_windows → conditional_aggregate` |
| SCAN | "Top 5 OIS dislocations" | Composer emits `scan_ois_extremes` leaf |
| PANEL | "Panel of every UST curve spread" | Composer emits `build_sovereign_yield_panel` leaf |
| BASIS | "Basis: USD 5Y linker breakeven vs 5Y inflation swap" | Composer emits `align → select×2 → series_arithmetic.subtract` |
| MESSY LINGO ×3 | "twos tens", "reds vs greens", "rich BTP-Bund vs 3y" | Composer produces the SAME shapes as their formal-language counterparts |
| ADVERSARIAL ×3 | under-scoped / role-mismatched / composite ambiguity | Boundary A or B refuses/clarifies; no DAG executes |

---

## 6. Decisions committed (not asked)

| Question | Decision | Rationale |
|---|---|---|
| Where do operator descriptions live? | `config.yaml` ONLY; renderer fuses with registry slots | P10 — single source of truth. Avoids duplication Codex correctly called out. |
| Coverage gate disposition | Hard-block (per R8). Refuse OR one precise clarification — gate picks based on whether the gap is fixable by user input. | Hard-block is settled; the refuse-vs-clarify choice is a UX detail the gate is competent to make. |
| Repair budget | One round, additive-only mutations (insert adapter, fix params, fix slot wiring). No primitive swap. No DAG reshape. Refuse on exhaustion. | Prevents oscillation. Codex's "you can't keep retrying" intuition formalised. |
| Resolver collision handling | `BoundLeaf` carries `domain` + `mcp_tool_name` + `resolver_tool_key`. The assembler derives `resolver_tool_key` via `domain_to_resolver_key()` (centralised adapter at [shared/workflow/resolver_keys.py](shared/workflow/resolver_keys.py)). | Respects the current ad-hoc convention (verified in [rates_agent/workflows/__init__.py](../rates_agent/workflows/__init__.py)) without locking it in. A future cleanup to uniform `<domain>::<tool>` is a one-file change. |
| Role-discriminant strictness | Closed-substrate fields hard-checked; free-form fields normalised-string compared and escalated as soft warnings, NOT hard rejects. | Avoids the "curated role enum" trap (R5) while keeping mechanical contradiction detection. |
| Boundary B source of truth | The ORIGINAL user prompt. L1 decomposition is supplementary evidence only. | Per Codex's correction point 4: if L1 dropped a domain, the decomposition is corrupted — checking against it would miss the very failure mode the gate exists to catch. |
| Trade operators registration | Out of PoC scope. Will be a follow-on PR that reuses the registration-only growth harness — proving the architecture, not just the registration-only claim. | Per Codex's correction point 5: be honest about what the PoC proves vs what scales onto it. |
| Templates path | Untouched. Both lanes co-exist via a thin pre-router in `CopilotSession`. | R6: templates out of scope here. |
| Composer shortlister | NONE. Composer ALWAYS sees the full 16-operator catalogue. | R3 + the owner's settled reasoning on per-layer accuracy. |
| Selector multi-binding | NO. One `LeafRequest` → one `BoundLeaf` per call. Composition is L3's job. | Keeps selector behaviour scoped to one decision, max prior density per call. |

---

## 7. Explicit non-goals (what this PoC does NOT prove)

| Non-goal | Why explicit |
|---|---|
| Backtest end-to-end | The three trade operators (`construct_trades` / `evaluate_trades` / `summarize_trades`) are unregistered. The architecture supports them, but the PoC does not claim to compose them. Follow-on PR will register them and exercise the scaling-proof harness to prove registration-only growth on a non-trivial new operator family. |
| Auto-scanner (Goal 2 from the founding vision) | The PoC is Goal 1 only (user-prompted DAG construction). Auto-scanner is the next horizon and depends on Goal 1 working. |
| Sub-domain split inside any one MCP server | The CI lint will FAIL if a domain crosses the per-domain primitive cap, forcing a split — but the actual split work is its own follow-on PR (not in this plan). |
| Multi-asset (FX, equities) domains | The substrate is portable; the PoC only proves the 6 rates domains currently registered. Adding FX is a sibling MCP server + a domain card — registration-only growth proves this WILL work. |
| New artifact types | The 6 closed-family artifact types are the floor for the PoC. Adding a 7th is an ADR and a registry change — not in scope here. |

---

## 8. The single biggest risk and the mitigation

**Risk:** the Composer reliably picks the right operator family but produces type-invalid DAGs (e.g. omits the two `select_from_series_set` nodes between `align_series` and a pair-stats operator). This is exactly the mistake the previous agent (me) made when describing the canonical shape.

**Mitigation (layered):**
1. PR-2 operator cards for `align_series` include in `downstream_pattern`: "outputs `SeriesSet`; to feed a pair-stats operator (correlation / rolling_correlation / cointegration / rolling_regression), follow with `select_from_series_set ×2`."
2. PR-2 operator cards for the four pair-stats operators include in `upstream_requirements`: "left and right must be two `Series` with identical `DatetimeIndex`. Standard upstream: `align_series → select_from_series_set ×2`."
3. PR-7 golden few-shots include the **correct full shape** for each of relationship / rolling / cointegration / regression patterns — composer learns by example.
4. PR-1 validator catches the omission as `E_TYPE_MISMATCH` (operator slot expects `Series`, edge from `align_series` provides `SeriesSet`) — clean refusal with diagnostic.
5. PR-4 assembler treats `E_TYPE_MISMATCH` from a `SeriesSet → Series` boundary as a CANDIDATE for additive repair (insert two `select_from_series_set` nodes between `align_series` and the pair-stats operator). Repair adds the missing nodes; second validate green.

Defence in depth. Even if (1)–(3) fail to teach the composer, (4) catches and (5) repairs.

---

## 9. Where this differs from the previous "Claude plan" and why

Audited against Codex's two rounds of pushback (kept here for reviewer convenience):

| Change vs prior plan | Source | Why |
|---|---|---|
| Build order: `ValidationResult` first, operator cards second | Codex round 1 | `ValidationResult` is the spine; operator cards feed only L3. Unblocks more downstream substrate. |
| Canonical shape includes `select_from_series_set ×2` | Codex round 1 | The previous shape was type-invalid against `align_series` outputting `SeriesSet`. Verified in [registry.py:338](../shared/workflow/registry.py:338). |
| `BoundLeaf` carries `domain` + `mcp_tool_name` + `resolver_tool_key` | Codex round 1 + verified in [rates_agent/workflows/__init__.py:1079,1147](../rates_agent/workflows/__init__.py:1079) | Real resolver collision already exists; the current ad-hoc convention (bond_futures bare, policy_futures prefixed) is respected via a centralised adapter. |
| Role-discriminant: closed-substrate fields hard-checked; free-form fields normalised-string compared and escalated as SOFT warnings | Codex round 2 | Pure string equality on LLM free-form is brittle; pure structured enums violate R5 (no curated role vocab). The split solves both. |
| Resolver-key adapter (`shared/workflow/resolver_keys.py`) | Codex round 2 + code verification | Don't put `<domain>::<tool>` directly into `PrimitiveNode.tool_name` — that would require a substrate migration. Adapter respects current convention; future migration is a one-file change. |
| Eval matrix expanded across all 9 intent families + 3 messy-lingo + 3 adversarial | Codex round 2 | Previous matrix was relationship-heavy; risk of proving only one operator family. |
| Boundary B compares against ORIGINAL PROMPT, not L1 decomposition | Codex round 2 | If L1 dropped a domain, decomposition is corrupted; checking against it would miss the very failure the gate exists to catch. |
| Backtest explicitly listed as non-goal | Codex round 2 | The PoC must be honest about what it proves; backtest follows via the registration-only growth harness. |
| `OperatorSpec` gets only a `config_path` pointer, NOT description content | Codex round 2 | P10: description content lives in `config.yaml` only; renderer fuses both at L3-prompt-build time. No duplication. |

---

## 10. Open questions for the project owner (none)

This plan commits every decision the previous draft asked. There are no open questions. If a reviewer disagrees with any decision in §6, the path is: open an ADR, supersede the relevant row, update this document in the same PR.

---

## 11. Reviewer checklist (audit gate for the plan itself)

A reviewer (human or Codex) auditing this document should confirm:

- [ ] All 12 settled rulings in §1 are respected by every PR in §4.
- [ ] Catalogue in §2 matches the code (counts + per-domain role groups).
- [ ] §2.4 name-collision audit reflects the actual resolver convention.
- [ ] Canonical shape in §3.1 type-checks against `OPERATOR_REGISTRY` (specifically: `align_series → SeriesSet`, `select_from_series_set → Series`, `correlation` takes two `Series`).
- [ ] Each PR in §4.2 lists files, schemas, tests, acceptance criteria, dependencies.
- [ ] Error-code taxonomy in PR-1 has one entry per current `raise WorkflowValidationError` site.
- [ ] Repair-mutation rules in PR-4 explicitly disallow primitive swap and DAG reshape.
- [ ] Eval matrix in §5 covers every operator family at least once.
- [ ] Scaling proofs in PR-10 test all three handoff requirements (registration-only, context-bound, two-boundary).
- [ ] Non-goals in §7 are explicit.
- [ ] §9 traces every change vs the previous draft to its source (Codex round 1, Codex round 2, or code verification).

When all boxes are checked, the plan is approved and PR-1 can start.

---

*End of plan. ~5,200 words. Built to be audited.*
