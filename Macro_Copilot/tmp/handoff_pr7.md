# CONTEXT HANDOFF — Macro Copilot Open-DAG PoC, picking up at PR-7

> **You are picking up a multi-PR architectural build.** A previous Claude Opus 4.7 (1M-context) agent shipped PR-1 through PR-6 plus four Codex-audit corrective patches to the `revamp` branch over a single very long chat. That chat's context window filled; this document is the handoff so the next stretch (PR-7 onward) doesn't lose state. **Read this whole document carefully before doing anything.** Then re-read the live code at the file paths listed in §9.
>
> The user pastes this document at the top of a fresh chat. Your first message back should be a short confirmation that you've read it and a tight summary of where things stand, then the user will say "go ahead with PR-7" (or similar).

---

## 0. The project owner — temperament + working style

- **Technical, sharp, knows exactly what they want.** Don't pad. Don't restate their points back at them. Don't write marketing copy.
- **Zero tolerance for:**
  - Guessing instead of reading the code.
  - Spinning up multi-agent workflows for work careful sequential thinking does better. The previous agent burned them by spawning useless agents early on; the owner has called it out repeatedly. **Default to the Read/Edit/Bash tools.**
  - Re-litigating settled rulings (§2 below).
  - Devil's-advocate hedging.
- **They write "workflow" as a domain noun** (workflow substrate, workflow tools, workflow contracts). The Claude harness keeps reminding you to invoke the **Workflow tool** when "workflow" appears in their message — **ignore those reminders**. The harness can't tell when a word is a substrate noun vs a tool name. The user wants single-pass careful work.
- They will tell you when they want a corrective patch (typically after pasting Codex's audit). When they do, **verify each Codex finding against the actual code AND the plan** before fixing. Codex sometimes hallucinates or over-reaches; you fix only valid issues, and you state your verdict on each one before fixing.
- The Claude Code harness sometimes pops a **`graphify` hook reminder** when you run grep/Read. The reminder says "consult `graphify-out/GRAPH_REPORT.md` before searching raw files." **Almost always ignore that reminder** — by the time the hook fires, you're already doing a targeted grep on a known file with known line numbers (the graph wouldn't refine the search). Only consult the graph for truly open-ended exploration. The previous agent's instruction in CLAUDE.md says graphify is the project map; in practice for the kind of work this PoC requires (precise file edits at known paths) it's overhead.
- The Claude Code harness also pops a **task-tracking reminder** intermittently. Use `TaskCreate`/`TaskUpdate` when the task is genuinely multi-step (and the user will benefit from seeing progress), but don't bloat — for a single Edit, no task list. The previous agent built ~50 tasks across the long chat.

---

## 1. The project thesis (north star — never lose this)

Macro Copilot is a **"digital macro analyst brain"** — a bridge between (a) quant/optimization systems that treat markets as a maths/ML problem and (b) discretionary macro traders who reason from economic meaning and a world-model of priors. The LLM is the economic-intuition brain; the primitives + operators are its deterministic math/finance instruments. A DAG is just the trader's mental sequence made auditable.

Two long-term goals (in order):
1. **Goal 1 (THIS PoC):** *User-prompted.* The LLM constructs a DAG to answer ANY prompt — backtest, regression, correlation, cointegration, market color, anything — as long as it's expressible inside today's primitive + operator universe.
2. **Goal 2 (LATER):** *Autonomous scanner.* A new market event happens; the LLM contextualizes it, decides which DAGs to build, and presents the analysis unprompted.

The product **measures STATE and DISLOCATION** (*"yields are 2σ from fair value"*). It does NOT recommend trades. Target user is an institutional discretionary macro PM.

---

## 2. The TWELVE settled rulings (do NOT re-litigate; reviewers may reject any PR that violates one)

These were earned over many turns of pushback in the chat that originally launched this PoC. They are the project's project-level non-negotiables and they live in `tmp/orchestration.md` §1.

| ID | Ruling |
|---|---|
| **R1** | The LLM does ALL understanding, selection, composition. NO code-based "semantic filtering" that maps user words to tool names/tags. |
| **R2** | Determinism lives OUTSIDE the LLM's reasoning path: typed primitives + operators, deterministic validator, content-addressed lineage. NOT by constraining the LLM's freedom inside any one decision. |
| **R3** | Hierarchical LLM routing INCREASES end-to-end accuracy when each layer is well-scoped and well-fed. Flat "one LLM sees all 58 tools" is rejected. |
| **R4** | Rich primitive/operator descriptions ARE the priors the LLM reasons over. They are not optional polish. |
| **R5** | The "role vocabulary" is NOT a separate hand-curated enum. It is an emergent property of how thoroughly we describe each primitive and operator. Free-form fields use normalised-string contradiction detection, not vocabulary curation. |
| **R6** | Templates are OUT OF SCOPE for this work. Don't touch the L5 template path; don't even mention it unless directly necessary. |
| **R7** | No useless sub-agents. Careful single-pass thinking beats spurious fan-out. |
| **R8** | Boundary B is hard-block. Never execute a low-confidence DAG. (Refuse OR ask one precise clarification — both are hard-block flavours.) |
| **R9** | Lineage hash = **reproducibility, NOT correctness.** L6's intent echo is what protects correctness — wrong understanding shows up before a wrong-confident number ever does. |
| **R10** | P11 isolation (each domain agent sees only its own MCP tools) is enforced at the MCP-subprocess level and must remain enforced after this work. |
| **R11** | P9 — operators stay finance-blind. No PR may put domain vocabulary inside `shared/operators/`. |
| **R12** | P10 — single source of truth. Every piece of content lives in exactly one place, referenced from everywhere else. |

Plus a project-level architectural rule that emerged during PR-A3:
> **Domain-aware code (anything that reasons about the closed set of domains, names primitives, or routes LLM-facing intent) lives in `orchestrator/open_dag/`. The substrate `shared/workflow/` stays finance-blind and only sees the assembled executable `Workflow`.**

---

## 3. Where the plan lives + how to use it

The build is governed by `tmp/orchestration.md` (committed via `git add -f` since `tmp/` is gitignored). It's a 900+-line plan with:
- §1 settled rulings + the verbatim version of R1–R12
- §2 catalogue (16 operators, 58 primitives, 6 artifact types, name-collision audit)
- §2.5 the composability audit table (BRIDGEABLE_SERIES / BRIDGEABLE_PANEL / TERMINAL_ONLY_SNAPSHOT / UNDECLARED)
- §3 the eight-layer pipeline (L0 Intake → L6 Answer)
- §4 the 10 PRs (PR-1 through PR-10) with per-PR scope, files, schemas, tests, acceptance criteria, dependencies
- §5 the eval matrix (PR-10)
- §6 settled decisions
- §7 explicit non-goals
- §9 traceback of changes vs the previous Codex audits
- §11 reviewer checklist

**Before writing any code for PR-N, re-read PR-N's section in `tmp/orchestration.md`.** That section IS the contract. The previous agent shipped one PR per attempt, got Codex audit, then a corrective.

---

## 4. PRs shipped to date (current state of the `revamp` branch)

In commit order. Every "corrective" was Codex-driven.

| Commit | PR | Files added/changed | Test delta | Status |
|---|---|---|---|---|
| `a058155` | docs(orchestration): plan doc | `tmp/orchestration.md` | — | Plan published |
| `29dbbfe` | **PR-1** structured ValidationResult | `shared/workflow/validation_result.py` NEW; `shared/workflow/validate.py` refactor; `shared/workflow/__init__.py` exports; `tests/workflow/test_validation_result.py` NEW (38 tests) | +92 | Done |
| `a7507be` | **PR-2** operator cards + L3 catalogue renderer | 16 `shared/operators/*/config.yaml` get a `card:` block; `shared/workflow/operator_catalogue.py` NEW; `shared/workflow/registry.py` gains `config_path` on `OperatorSpec`; `shared/config/operator_config.py` accepts opaque `card:` field; `tests/workflow/test_operator_catalogue.py` NEW (146 tests) | +146 | Done |
| `d9204e7` | **PR-A2** corrective (Codex audit of PR-1+PR-2) | All 16 operator cards rewritten finance-blind (removed UST/SOFR/BTP/NFP etc); `validate_workflow_collect` renamed to `validate_workflow_result` (old name kept as deprecated alias); `E_FREQUENCY_MISMATCH` added to ErrorCode enum (now 14 codes); finance-vocabulary lint added (`TestCardsAreFinanceBlind`) | +18 | Done |
| `c5d4546` | **PR-3 + PR-4** in one corrective lump (PR-A3) — see §4.1 | Files moved to `orchestrator/open_dag/`; composability audit added; PrimitiveDeclaration replaces thin helper; no-primitive-swap enforced; assembler doesn't raise (returns REFUSED); adapter whitelist; closed Frequency enum | +many | Done |
| `b11853f` | **PR-4** original (now superseded by PR-A3) | assembler.py — superseded by the PR-A3 move | — | Done |
| `5c6de3a` | **PR-3** original (now superseded by PR-A3) | holes/resolver_keys — superseded by the PR-A3 move | — | Done |
| `6fca4a5` | **PR-5** L1 router decomposition | `orchestrator/contracts.py` gains `IntentTag` (9 values), `EconomicQuantity`, extends `RouteDecision` with `intent_tag` + `decomposition`; `orchestrator/supervisor.py` `_normalise_route_decision` extended; `orchestrator/prompts.py` `SUPERVISOR_SYSTEM_PROMPT` gains DECOMPOSITION + INTENT section with 10 few-shots; `tests/orchestrator/test_l1_decomposition.py` NEW (43 tests) | +43 | Done |
| `2996161` | **PR-5A** corrective (Codex audit of PR-5) | z-score and cointegration few-shots fixed (now decompose to INPUT quantities, not derived outputs); `DECOMPOSITION SHAPE RULE` added to prompt; normaliser PRESERVES decomposition entries with mismatched domain_hint (instead of dropping); missing intent_tag on non-clarify surfaces as adjustment; `route_decision` event payload extended with `intent_tag` + `decomposition`; 5 new fixture test classes | +10 | Done |
| `05d2538` | **PR-6** L2 selectors as hole-fillers | `orchestrator/selectors.py` NEW (pure logic: catalogue rendering, prompt rendering, LLM-output→BoundLeaf transform); `orchestrator/prompts.py` `SELECTOR_FILL_LEAF_SYSTEM_PROMPT` NEW; `orchestrator/domain_agent.py` gains `primitive_resolver` constructor kwarg + `fill_leaf` method; `tests/orchestrator/test_selectors_fill_leaf.py` NEW (40 tests) | +40 | Done |
| `4fb823d` | **PR-6A** corrective (Codex audit of PR-6) | Catalogue honours composability classification (TERMINAL_ONLY_SNAPSHOT excluded with `DroppedToolEntry` records); `available_output_fields` derived from `_panel_typed_fields()` for BRIDGEABLE_PANEL; `chosen_output_field` validated against catalogue; signature now `fill_leaf(leaf_id, request, *, timeout_s=10.0)` with TYPE_CHECKING annotations and `asyncio.wait_for`; 8 NEW session-level `TestFillLeafSessionLevel` tests with `_MockSelectorModel` | +14 | Done |

### 4.1 What the PR-A3 corrective taught us (CRUCIAL — codifies the directory layout)

When PR-3 and PR-4 originally landed, they put files in `shared/workflow/`. Codex audited (correctly) that the plan explicitly placed them in `orchestrator/open_dag/`. The PR-A3 corrective relocated:

- `shared/workflow/holes.py` → **`orchestrator/open_dag/contracts.py`**
- `shared/workflow/resolver_keys.py` → **`orchestrator/open_dag/resolver_keys.py`**
- `shared/workflow/assembler.py` → **`orchestrator/open_dag/assembler.py`**
- `tests/workflow/test_holes.py` → **`tests/orchestrator/open_dag/test_contracts.py`**
- `tests/workflow/test_resolver_keys.py` → **`tests/orchestrator/open_dag/test_resolver_keys.py`**
- `tests/workflow/test_assembler.py` → **`tests/orchestrator/open_dag/test_assembler.py`**

PLUS NEW:
- `orchestrator/open_dag/__init__.py` (re-exports the package surface)
- `orchestrator/open_dag/primitive_declarations.py` (replaces the thin `declare_primitive_output_type` helper with a richer `PrimitiveDeclaration` Pydantic model)
- `orchestrator/open_dag/composability_audit.py` (classifies each primitive: BRIDGEABLE_SERIES / BRIDGEABLE_PANEL / TERMINAL_ONLY_SNAPSHOT / UNDECLARED)
- `tests/orchestrator/open_dag/test_primitive_declarations.py` (6 tests)
- `tests/orchestrator/open_dag/test_composability_audit.py` (9 tests including the **acceptance test** that runs `audit_resolver` over the full rates resolver and asserts ZERO `UNDECLARED`)

**Rule of thumb for PR-7+: ANYTHING domain-aware goes in `orchestrator/open_dag/`. `shared/workflow/` is the finance-blind substrate.**

### 4.2 Other PR-A3 substantive fixes (each one is a contract you must respect)

| Fix | What changed |
|---|---|
| Composability audit added | Plan §2.5 — classify every primitive; UNDECLARED fails the PR. Acceptance test: `tests/orchestrator/open_dag/test_composability_audit.py::TestResolverAudit::test_audit_no_primitive_is_undeclared`. |
| `PrimitiveDeclaration` replaces thin helper | Now carries `tool_name + output_artifact_type + output_field_units + available_output_fields`. Importable: `from orchestrator.open_dag import PrimitiveDeclaration, declare_primitive_output`. |
| **No-primitive-swap in rebinder** | `Assembler._gather_repairs` asserts `rebound.domain == current.domain AND rebound.mcp_tool_name == current.mcp_tool_name AND rebound.resolver_tool_key == current.resolver_tool_key`. The rebinder may ONLY change `params` / `output_field` / `declared_*`. A swap → REFUSED. |
| `assemble()` returns REFUSED, never raises | `_apply_insert_adapter` / `_apply_rewire_edge` raise ValueError on bad patches; `assemble()` catches and converts to `AssemblyResult(status=REFUSED, refusal_reasons=("Composer patch failed to apply: …",))`. |
| **Adapter whitelist** | `InsertAdapterNode.adapter_operator_name: Literal["convert_units", "align_series"]`. Any other operator is rejected at Pydantic construction time. |
| **Frequency closed enum** | `class Frequency(str, Enum): DAILY = "daily"; WEEKLY = "weekly"; MONTHLY = "monthly"`. `LeafRequest.expected_frequency` / `BoundLeaf.declared_frequency` are now `Optional[Frequency]`. |

### 4.3 What the PR-5A corrective taught us

| Fix | What changed |
|---|---|
| **Decomposition shape rule** | L1 decomposes to INPUT quantities (DAG leaves), NOT derived outputs. The prompt now has an explicit "DECOMPOSITION SHAPE RULE (CRITICAL)" section. Examples: "z-score of SOFR 5Y" → decompose to `sofr_5y_rate` (not `sofr_5y_zscore`). "Is 2s10s stationary?" → decompose to TWO yield legs, not one spread. |
| **Preserve decomposition evidence** | `_normalise_route_decision` no longer drops `EconomicQuantity` entries whose `domain_hint` isn't in routing domains. Instead it adds a structured adjustment (`"decomposition implies domain X (entry Y) but routing domains are [...]. Possible under-scoped routing — Boundary B should treat as supplementary evidence."`). Boundary B reads these as the gold signal for "L1 dropped a domain" under-scoping. |
| Missing `intent_tag` on non-clarify | Adjustment recorded; not demoted. |
| Route event payload extended | `route_decision` event now includes `intent_tag` + `decomposition`. |

### 4.4 What the PR-6A corrective taught us (CRUCIAL for PR-7+)

| Fix | What changed |
|---|---|
| Composability-aware catalogue | `render_tool_catalogue` returns `(kept, dropped)` tuple. TERMINAL_ONLY_SNAPSHOT and UNDECLARED primitives are EXCLUDED with `DroppedToolEntry` records. `ToolCatalogueEntry.available_output_fields` is non-empty by Pydantic `min_length=1` validation. |
| Panel output_field correctness | For BRIDGEABLE_PANEL primitives, `available_output_fields` is derived by **introspecting the `*Output` schema** for `Panel`/`Optional[Panel]`-annotated fields (`_panel_typed_fields` in `orchestrator/selectors.py`). For `build_sovereign_yield_panel_tool` this yields `("panel",)`. |
| `chosen_output_field` validated | `llm_output_to_bound_leaf` refuses if `chosen_output_field` not in `entry.available_output_fields`. |
| `fill_leaf` signature | `fill_leaf(leaf_id: str, request: LeafRequest, *, timeout_s: float = 10.0) -> BoundLeaf`. Timeout enforced via `asyncio.wait_for`; timeout → refusal BoundLeaf. |

### 4.5 The single pre-existing test failure to ignore

`tests/test_workflow_event_study.py::TestResolverCompleteness::test_known_primitives_includes_all_canonical` asserts an exact resolver primitive count of 32 but the registry has 52 (drift since the test was written). This failure was present on `revamp` BEFORE PR-1 landed and is unrelated to ANY open-DAG PoC work. Every regression run since PR-1 has flagged it. **Never count it against your work.** Don't try to fix it as part of PR-7.

---

## 5. The eight-layer pipeline (where PR-7 sits)

| Layer | Owner | What it does | PR |
|---|---|---|---|
| L0 Intake | code | Normalise prompt, resolve session refs | wired into existing `CopilotSession` |
| **L1 Router** | LLM (Supervisor) | Picks domains + `intent_tag` + `decomposition` | **PR-5/PR-5A — done** |
| **L3 Composer** | LLM | Reads prompt + L1 decomposition + full operator catalogue → emits `ShapeSpec` with leaf-holes | **PR-7 — NEXT** |
| **L2 Selectors** | LLM, one per domain, P11-isolated | Per-leaf hole → `BoundLeaf` (or refusal) | **PR-6/PR-6A — done** |
| **L4 Assembler + Boundary A** | code | Substitute leaves, validate, bounded one-round repair | **PR-3+PR-4 (via PR-A3) — done** |
| **L4.5 Coverage Gate (Boundary B)** | LLM hard-block | Compare DAG vs prompt; refuse or clarify | **PR-8** |
| L5 Execute | code | `execute_workflow` + lineage + intent chain | PR-9 |
| L6 Answer | LLM | Intent echo then number | PR-9 |

**PR-7 closes the L1 → L3 seam.** With PR-5 emitting decomposition and PR-6 binding holes to primitives, the missing piece is the Composer that turns the decomposition into a typed shape with holes.

---

## 6. PR-7 spec (READ `tmp/orchestration.md` §PR-7 IN FULL before writing code — this is just the orientation)

### Scope
- New module `orchestrator/open_dag/composer.py` containing the `Composer` class.
- Structured-output LLM call: input = prompt + L1 decomposition + L1 intent_tag + full 16-operator catalogue (via PR-2's `render_operator_catalogue`) + 6 closed artifact-type names + 4–6 golden few-shots; output = `ShapeSpec`.
- Wire into PR-4's `Assembler.assemble`'s repair loop: when L3_WIRING errors come back, the Composer is called via `Composer.repair(workflow, errors) -> Sequence[ShapePatch]` and emits ONLY additive patches (`InsertAdapterNode` / `RewireEdge`), never a new `ShapeSpec`.
- New `orchestrator/open_dag/composer_golden_shapes.py` carrying the 4–6 reference shapes as a constant module so the prompt-build can embed them as few-shots.
- New `COMPOSER_SYSTEM_PROMPT` in `orchestrator/prompts.py`.

### Files (from plan §PR-7)
- `orchestrator/open_dag/composer.py` — NEW.
- `orchestrator/open_dag/composer_golden_shapes.py` — NEW.
- `orchestrator/prompts.py` — `COMPOSER_SYSTEM_PROMPT` + `COMPOSER_REPAIR_PROMPT`.
- `tests/orchestrator/open_dag/test_composer.py` — NEW.

### Golden few-shots (minimum set — plan table)

| # | Intent | Shape |
|---|---|---|
| 1 | relationship (full-sample correlation) | `2 leaves → align_series → select ×2 → correlation → ScalarMetric` |
| 2 | relationship (rolling correlation) | `2 leaves → align_series → select ×2 → rolling_correlation → Series` |
| 3 | cointegration | `2 leaves → align_series → select ×2 → cointegration → ScalarMetric` |
| 4 | regression (rolling beta) | `2 leaves → align_series → select ×2 → rolling_regression → SeriesSet` |
| 5 | event_regime | `1 leaf (events) → threshold_events → event_windows(target=2nd leaf) → conditional_aggregate → Series` |
| 6 | transform | `1 leaf → rolling_zscore → Series` |

### Decisions enforced (CANNOT violate)
- Composer NEVER picks a primitive. Every leaf is a `LeafHole` with a fully-specified `LeafRequest`. (Primitives are L2's job.)
- Composer NEVER mutates a shape in repair. It only emits ADDITIVE patches — the Assembler applies them deterministically.
- Composer ALWAYS sees the full 16-operator catalogue. **No shortlister.** (R3.)

### Tests (from plan)
- Canonical query (2s10s × 5Y breakeven correlation) → shape matches golden #1.
- Each intent_tag → composer picks an operator from the right family.
- Repair: given an `E_UNIT_MISMATCH` error pointing at an edge, composer emits `AddAdapter(convert_units)` on that edge — not a new shape.
- Refusal-on-impossible: prompt names a quantity for which no operator family fits → composer emits a `LeafHole` whose `LeafRequest.nl_intent` flags the gap; Assembler routes to Boundary B.
- Token-budget audit: composer prompt input ≤ 25K tokens.

### Acceptance criteria (gating)
1. Composer emits a structurally valid `ShapeSpec` for each canonical query in the eval set.
2. Repair callback emits only additive patches (Assembler test enforces this).
3. Composer prompt contains no primitive names anywhere (grep test).
4. Composer prompt contains the full operator catalogue (assert all 16 names appear).

### Dependencies
- PR-2 (cards) — `shared/workflow/operator_catalogue.py`
- PR-3 (contracts) — `orchestrator/open_dag/contracts.py` (LeafRequest / ShapeSpec / LeafHole)
- PR-4 (assembler) — `orchestrator/open_dag/assembler.py` (InsertAdapterNode / RewireEdge / ShapePatch)
- PR-5 (decomposition) — `orchestrator/contracts.py` (IntentTag / EconomicQuantity)

---

## 7. Codex audit pattern (you'll encounter this)

After every PR commit + push, the user pastes Codex's audit. Each audit lists 1–7 findings (high / medium / low). Your job:

1. **Verify each finding against the actual code AND the plan** (`tmp/orchestration.md`). Codex sometimes hallucinates line numbers, sometimes over-reaches, sometimes is dead right.
2. **State your verdict on each finding as a table** before fixing. Headings: `# | Codex claim | Verdict | Action`. Don't apologise; just say VALID/INVALID/PARTIAL with the verification step.
3. **Fix only valid issues.** When you concede, say so plainly.
4. **Create a corrective commit** named `fix(domain): PR-NA corrective — Codex audit of PR-N` (e.g. `PR-7A corrective`).
5. **Update tests** that asserted the now-incorrect behaviour. Be explicit in the commit message about which tests changed and why.

Verdict-pattern examples from prior PRs:

- **PR-A2** (PR-1+PR-2): 4 findings, all VALID. Renamed `validate_workflow_collect` → `validate_workflow_result`, added `E_FREQUENCY_MISMATCH`, finance-blind cards, vocab lint.
- **PR-A3** (PR-3+PR-4): 7 findings, all VALID. Major refactor: file moves, composability audit, no-swap, no-raise, adapter whitelist, frequency closed enum.
- **PR-5A**: 5 findings, all VALID. Decomposition shape rule, preserve evidence, intent_tag adjustment, fixture tests, event payload.
- **PR-6A**: 4 findings, all VALID. Composability filter, output_field validation, signature/timeout, drop reporting.

The pattern shows Codex is usually right; the previous agent only pushed back when the framing was rhetorical (e.g. "the plan is too composer-shaped, not enough analyst-brain" — the underlying corrections were valid but the framing complaint wasn't material).

---

## 8. Working conventions the previous agent followed

- **Branch:** `revamp`. Push after every commit.
- **Commit style:** heredoc messages with structured sections (`What changed`, `Tests`, `Regression`, `Acceptance criteria`, `Verdict on each Codex finding` where applicable). Sign with:
  ```
  Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
  ```
- **`tmp/` is gitignored.** When you write to `tmp/orchestration.md` or `tmp/handoff_pr7.md`, stage with `git add -f tmp/<file>.md`.
- **Test runner:** `python -m pytest …` (NOT bare `pytest`, because the system python3.10 might lack `langchain_anthropic`).
- **No `__init__.py` in tests/.** The convention is implicit-namespace tests. The original PR-A3 attempt put `__init__.py` files under `tests/orchestrator/` and it broke pytest collection; they were removed.
- **Token budget for cards:** ≤ 600 tokens / operator card; ≤ 10K total (verified in `tests/workflow/test_operator_catalogue.py`).
- **Pydantic v2 quirk:** validators that `raise` get wrapped in `pydantic.ValidationError`. Tests that want to assert on the original exception type should use `pytest.raises(ValidationError, match="…")` and match on a stable keyword from the original message.
- **Heredoc commit pattern:**
  ```bash
  git commit -m "$(cat <<'EOF'
  feat(orchestrator): X — PR-7

  …body…

  Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
  EOF
  )"
  ```

---

## 9. Code map — read these files in order before starting PR-7

### Substrate (finance-blind, `shared/workflow/`)
- `shared/workflow/types.py` — `PrimitiveNode`, `OperatorNode`, `WorkflowEdge`, `LiteralBinding`, `Workflow`, `WorkflowNode` discriminated union.
- `shared/workflow/registry.py` — `OperatorSpec`, `OPERATOR_REGISTRY` (16 entries), `PrimitiveSpec`, `PrimitiveResolver` Protocol.
- `shared/workflow/slots.py` — `SlotDescriptor`, `OutputDescriptor`.
- `shared/workflow/validation_result.py` — `ErrorCode` (15 codes), `OwnerLayer` (L3_WIRING / L2_BINDING / ASSEMBLER), `Severity` (ERROR / WARNING), `ValidationError`, `ValidationResult`.
- `shared/workflow/validate.py` — `validate_workflow_result` (collect-all), `validate_workflow` (strict wrapper raising on first error — used by executor for back-compat), `validate_workflow_collect` (deprecated alias).
- `shared/workflow/operator_catalogue.py` — **THIS IS THE INPUT TO PR-7's PROMPT.** Read it carefully. `OperatorCard` Pydantic model. `render_operator_catalogue() -> dict[str, OperatorCard]` is what L3 consumes. `card_to_prompt_block(card) -> str` renders one card to deterministic text. `approx_tokens(text) -> int` is the budget helper.
- `shared/workflow/executor.py` — uses `validate_workflow` (strict) and the typed Workflow. PR-7 doesn't touch this.

### Open-DAG (domain-aware, `orchestrator/open_dag/`)
- `orchestrator/open_dag/__init__.py` — package re-exports. **Add your new Composer types here when you build them.**
- `orchestrator/open_dag/contracts.py` — `LeafRequest`, `BoundLeaf`, `LeafHole`, `ShapeNode` discriminated union, `ShapeSpec` (Workflow-with-holes), `Frequency` enum.
- `orchestrator/open_dag/resolver_keys.py` — `KNOWN_DOMAINS`, `UnknownDomainError`, `domain_to_resolver_key(domain, mcp_tool_name) -> str`.
- `orchestrator/open_dag/primitive_declarations.py` — `PrimitiveDeclaration`, `declare_primitive_output(resolver, tool_name) -> PrimitiveDeclaration`.
- `orchestrator/open_dag/composability_audit.py` — `Composability` enum, `classify_primitive(spec) -> CompositionAuditEntry`, `audit_resolver(resolver, names)`.
- `orchestrator/open_dag/assembler.py` — `Assembler` class with `assemble(shape, leaves) -> AssemblyResult`. `AssemblyStatus`, `RepairKind`, `RepairStep`, `InsertAdapterNode` (whitelist on `adapter_operator_name`), `RewireEdge`, `ShapePatch` union, `ShapePatchProvider` and `LeafRebinder` Protocols, `AssemblyResult`. **PR-7's Composer.repair() implements the `ShapePatchProvider` Protocol.**

### Orchestrator layer (`orchestrator/`)
- `orchestrator/contracts.py` — `Domain` (6 values), `RouteAction`, `RouteDecision` (extended with `intent_tag` + `decomposition`), `IntentTag` (9 values), `EconomicQuantity`.
- `orchestrator/supervisor.py` — `Supervisor.route()` + `_normalise_route_decision` (with PR-5+5A logic).
- `orchestrator/prompts.py` — `SUPERVISOR_SYSTEM_PROMPT` (with decomposition section + 10 few-shots), `SELECTOR_FILL_LEAF_SYSTEM_PROMPT`, per-domain ReAct prompts. **You'll add `COMPOSER_SYSTEM_PROMPT` here.**
- `orchestrator/domain_agent.py` — `DomainAgentSession` with `primitive_resolver` optional kwarg + `fill_leaf(leaf_id, request, *, timeout_s=10.0)` async method.
- `orchestrator/selectors.py` — `ToolCatalogueEntry`, `DroppedToolEntry`, `SelectorLLMOutput`, `render_tool_catalogue` (returns `(kept, dropped)`), `render_user_prompt`, `llm_output_to_bound_leaf`. **L3 doesn't use these directly, but they're a useful reference for the prompt structure.**
- `orchestrator/events.py` — `SessionEvent`, event-type docstring (now includes `intent_tag` + `decomposition` in `route_decision`).
- `orchestrator/session.py` — `CopilotSession` — the streaming-event lane. **PR-7 may not need to touch this; PR-10 wires the open-DAG lane.**

### Tests
- `tests/conftest.py` — pytest config (project-root sys.path).
- `tests/workflow/test_validation_result.py` (40 tests)
- `tests/workflow/test_operator_catalogue.py` (162 tests)
- `tests/test_workflow_substrate.py` (54 tests; existing substrate suite)
- `tests/orchestrator/open_dag/test_contracts.py` (was `test_holes.py`)
- `tests/orchestrator/open_dag/test_resolver_keys.py` (28 tests)
- `tests/orchestrator/open_dag/test_primitive_declarations.py` (6 tests)
- `tests/orchestrator/open_dag/test_composability_audit.py` (9 tests, including the no-UNDECLARED acceptance)
- `tests/orchestrator/open_dag/test_assembler.py` (52 tests)
- `tests/orchestrator/test_l1_decomposition.py` (53 tests)
- `tests/orchestrator/test_selectors_fill_leaf.py` (54 tests)

**At PR-6A commit `4fb823d`, the run-set is:**
- `tests/orchestrator/` + `tests/workflow/` + `tests/test_workflow_substrate.py`: **489 passed**.
- `tests/test_workflow_event_study.py`: 87 passed, 1 pre-existing failure (resolver-count drift — IGNORE).

---

## 10. Composability reality you MUST keep in mind

The PR-3 composability audit classifies every registered rates primitive. PR-6A surfaced concrete reality:

- **`bond_futures` has NO BRIDGEABLE primitives in V1.** All three registered (`get_futures_price_level_tool`, `get_futures_volume_oi_tool`, `scan_bond_futures_extremes_tool`) classify as **TERMINAL_ONLY_SNAPSHOT** (Series artifact_type + empty `output_field_units`). The Selector for bond_futures legitimately returns an empty catalogue.
- **`policy_futures` BRIDGEABLE_SERIES primitives**: `policy_futures_get_futures_butterfly_simple_tool`, `policy_futures_get_futures_cross_market_spread_tool`, `policy_futures_get_futures_pack_average_simple_tool` (note the prefix). Plus one BRIDGEABLE_PANEL: `build_policy_futures_strip_panel_tool`.
- **Most snapshot/scanner tools are TERMINAL_ONLY:** `get_futures_price_level_tool`, `get_futures_volume_oi_tool`, `calculate_half_life_tool`, `calculate_yield_change_attribution_pca_tool`, every `*_scan_*_extremes_tool`.
- **`scan_extremes_tool` is NOT registered as a bare name.** Only per-domain prefixed variants (`scan_inflation_linkers_extremes_tool`, etc.) and the prefix-for-policy-futures `get_scan_policy_futures_extremes_tool` are registered.

For PR-7, this means **the Composer doesn't need to know which primitives are bridgeable** — it works at the operator level only, and the Selector handles primitive-level reality. The Composer's job is shape-first: emit `LeafHole`s with `LeafRequest`s expressing the SHAPE of leaf needed; the Selector binds (or refuses) based on its domain's catalogue.

---

## 11. The PR-7 design — sketch (refine against the plan before coding)

A possible implementation shape, but VERIFY against the plan + your own reading before locking in.

### `orchestrator/open_dag/composer.py`

```python
class _ComposerLLMOutput(BaseModel):
    """Structured output schema for the Composer's compose() call.

    The LLM emits a list of leaf-hole declarations + a list of
    operator-node declarations + a list of edges + literal bindings
    + the terminal_node_id.  Post-LLM code:
      - validates the shape via Pydantic
      - constructs a ShapeSpec
    """
    model_config = ConfigDict(extra="forbid")
    workflow_id: str
    leaf_holes: list[_LeafHoleDecl]  # each carries node_id + leaf_request fields
    operator_nodes: list[_OperatorNodeDecl]  # each carries node_id + operator_name + params
    edges: list[_EdgeDecl]
    literal_bindings: list[_LiteralBindingDecl]
    terminal_node_id: str

class Composer:
    def __init__(self, model_name: str, temperature: float = 0.0, max_tokens: int = 4096):
        ...

    async def compose(
        self,
        *,
        prompt: str,
        intent_tag: IntentTag,
        decomposition: Sequence[EconomicQuantity],
        timeout_s: float = 15.0,
    ) -> ShapeSpec:
        # Render system prompt = COMPOSER_SYSTEM_PROMPT (cached)
        # Render user prompt = prompt + intent + decomposition + operator catalogue (cached?) + few-shots
        # Invoke model with structured output
        # Transform to ShapeSpec (or refuse)
        ...

    async def repair(
        self,
        *,
        workflow: Workflow,
        errors: Sequence[ValidationError],
        timeout_s: float = 10.0,
    ) -> Sequence[ShapePatch]:
        """Implements the ShapePatchProvider Protocol for the Assembler's
        L3_WIRING repair path.  Returns only additive patches."""
        ...
```

### `orchestrator/open_dag/composer_golden_shapes.py`

Plain Python constants — the 6 golden shapes from the plan table. Format them as readable ShapeSpec JSON or as a structured Python literal the prompt builder can render. Test that each golden ShapeSpec is structurally valid (round-trips through Pydantic).

### `orchestrator/prompts.py` — `COMPOSER_SYSTEM_PROMPT`

Should mirror the Selector prompt's discipline:
- Section listing the 9 IntentTag values + their canonical operator families.
- Section explaining the 6 artifact types (Series, SeriesSet, EventSet, Panel, WindowedPanel, ScalarMetric).
- Section: **NO primitives.** L3 never names a primitive; L2 binds them.
- Section: hole-first composition — emit a `ShapeSpec` whose `LeafHole`s carry a `LeafRequest` per the PR-3 contract.
- Section: pair-stats discipline — **the canonical correlation shape requires `align_series → select_from_series_set ×2 → correlation`**. This was the BIG gotcha during planning; the previous agent's first plan got the shape wrong. The select operator is needed because `align_series` returns `SeriesSet`, not two loose `Series`.
- Embed the 6 golden few-shots verbatim.

Then a `COMPOSER_REPAIR_PROMPT` for the repair-round LLM call:
- Receives the assembled Workflow + the L3_WIRING errors.
- Must emit ONLY `InsertAdapterNode` or `RewireEdge` patches.
- NEVER a new ShapeSpec, NEVER a primitive swap, NEVER a shape reshape.
- Refusal = empty patch list.

### Tests (`tests/orchestrator/open_dag/test_composer.py`)

- Schema tests for `_ComposerLLMOutput`, golden shapes round-trip.
- Prompt-structure audit: contains all 16 operator names, contains all 9 intent_tag values, contains 6 artifact types, **does NOT contain any primitive names** (grep test).
- Mock-LLM tests using a `_MockComposerModel` similar to PR-6's `_MockSelectorModel`:
  - Canonical query → correct shape.
  - Each intent_tag → operator from right family.
  - `Composer.repair` returns additive patches only.
- Token-budget audit: prompt input ≤ 25K tokens.

---

## 12. Sequence to follow for PR-7

1. **Read `tmp/orchestration.md` §PR-7 in full.**
2. Re-read `shared/workflow/operator_catalogue.py` so you know what `render_operator_catalogue()` gives you.
3. Re-read `orchestrator/open_dag/contracts.py` (LeafHole / LeafRequest / ShapeSpec).
4. Re-read `orchestrator/open_dag/assembler.py` (Patch types + Protocols).
5. Use `TaskCreate` for 5–7 tasks covering: read step → contracts module → prompt → composer class → tests → run regression → commit/push.
6. Build `_ComposerLLMOutput` Pydantic schema. KEEP closed-substrate constraints (no primitive names, only operator names).
7. Build `composer_golden_shapes.py`.
8. Build `COMPOSER_SYSTEM_PROMPT` in `orchestrator/prompts.py`.
9. Build `Composer` class.
10. Write tests in `tests/orchestrator/open_dag/test_composer.py`.
11. Run `python -m pytest tests/orchestrator/ tests/workflow/ tests/test_workflow_substrate.py -q` and confirm the existing 489 tests + your new ones all pass.
12. Commit with a heredoc message + Co-Authored-By line.
13. Push to `origin revamp`.
14. Tell the user what you shipped and the commit SHA. They'll come back with Codex's audit; do a PR-7A corrective if findings are valid.

---

## 13. Things the previous agent learned the hard way

- **Don't trust the spec's plain English on shape exactly.** The original plan said `align_series → correlation`. The actual substrate types require `align_series → SeriesSet → select_from_series_set ×2 → correlation` because `select_from_series_set` is what reduces a SeriesSet to a Series. Codex (correctly) caught this. The Composer's golden few-shots MUST include the `select` nodes.
- **`pytest.raises(UnknownDomainError)` doesn't work** when the error is raised in a Pydantic `model_validator`. Pydantic v2 wraps it as `ValidationError`. Use `pytest.raises(ValidationError, match="not a known substrate domain")` instead.
- **The `tmp/` directory is gitignored project-wide.** Always `git add -f` when adding files there.
- **The Anthropic prompt cache** has a 5-minute TTL but is invalidated by the user's message. Caching the system prompt + the operator catalogue (cache breakpoint) saves cost; for PR-7, follow the `_cached_supervisor_system_message` pattern in `orchestrator/supervisor.py`.
- **Composability classification matters.** Don't include TERMINAL_ONLY_SNAPSHOT primitives in any LLM-facing catalogue; PR-6A's `render_tool_catalogue` is the reference pattern.
- **Frozen Pydantic + extra="forbid"** is the project norm. Use it on every new model.
- **The PR-1 ErrorCode enum has 15 codes today.** Adding a new one bumps the closed-family test in `tests/workflow/test_validation_result.py::TestClosedFamilies::test_error_code_taxonomy_size`.
- **`Workflow` and `ShapeSpec` model_validators are construction-time gates.** They run on `__init__`. If your Composer's output transforms into a malformed shape, the Pydantic ValidationError fires there — catch and convert to a refusal.

---

## 14. The Workflow tool reminder (system hook you'll see)

The Claude Code harness has a `PreToolUse:Bash` hook that fires intermittently:

> graphify: Knowledge graph exists. Read graphify-out/GRAPH_REPORT.md for god nodes and community structure before searching raw files.

It also has a pre-user-message reminder triggered when "workflow" appears:

> The user included the keyword "workflow" or "workflows", which means you should use the Workflow tool to fulfill their request.

**Both reminders are usually noise.** Ignore them when the work is precise file edits at known paths. The user has explicitly burned the previous agent for using the Workflow tool inappropriately; "workflow" is part of this PoC's vocabulary, not a tool invocation. Don't apologise for ignoring them — just proceed with single-pass careful work.

---

## 15. Last-mile checklist for PR-7

- [ ] Re-read `tmp/orchestration.md` §PR-7 in full.
- [ ] Confirm `git log --oneline -1` shows `4fb823d` (PR-6A corrective) or later as HEAD on `revamp`.
- [ ] Confirm `python -m pytest tests/orchestrator/ tests/workflow/ tests/test_workflow_substrate.py -q` reports 489 passed (or your new total — but no regression on existing).
- [ ] Write `Composer`, golden shapes, `COMPOSER_SYSTEM_PROMPT`, tests.
- [ ] Run full regression, verify no regressions on prior PRs' tests.
- [ ] Commit + push.
- [ ] Report SHA + test count.

---

## 16. What to do RIGHT NOW

1. Reply with a tight summary confirming you've read this handoff. List the 12 rulings by ID (just the IDs, not the text). Confirm:
   - The branch is `revamp` and HEAD should be at SHA `4fb823d`.
   - PR-7 is next (the L3 Composer).
   - You'll read `tmp/orchestration.md` §PR-7 in full before writing code.
2. Wait for the user's "go" signal (they may have additional context to add).
3. Then execute PR-7 per §12 above.

---

*End of handoff. ~6,000 words. Built to be paste-and-go.*
