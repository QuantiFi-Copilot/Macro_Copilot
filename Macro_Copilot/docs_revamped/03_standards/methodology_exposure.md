# Methodology Exposure

> Every methodology choice a primitive makes is either **YAML-locked** (system constant; the user changes it by editing the config + restarting) or **exposed** (the user changes it per call via the Pydantic `Input` model). The decision is recorded INSIDE `config.yaml` next to the convention itself — one place, one truth. This standard defines the decision protocol, the YAML shape, the propagation chain, and the **standalone-module bridge pattern** that every proof-of-concept tool follows from now on.

**Version:** v1
**Last reviewed:** 2026-05-28
**Status:** load-bearing component contract. Changes require an ADR in [`../05_decisions/`](../05_decisions/).
**Operationalises principles:** [P5](../00_thesis/01_non_negotiables.md) (honest disclosure), [P8](../00_thesis/01_non_negotiables.md) (closed-family discipline), [P10](../00_thesis/01_non_negotiables.md) (single source of truth), [PR7](../02_components/primitive/README.md) (configuration offloading), [PR8](../02_components/primitive/README.md) (single central methodology surface), [PR11](../02_components/primitive/README.md) (honest refusal), [PR13](../02_components/primitive/README.md) (cross-config consistency).
**See also:** [`methodology_disclosure.md`](methodology_disclosure.md), [`lifecycle_checklist_template.md`](lifecycle_checklist_template.md), [`tool_lifecycle.md`](tool_lifecycle.md), [ADR 0014](../05_decisions/0014-frontend-module-architecture.md), [ADR 0015](../05_decisions/0015-tool-metadata-db-table.md).

---

## 1. Universal rule

> Every entry under `conventions:` in a tool's `config.yaml` carries an **`exposure:` sub-block** recording whether the convention is YAML-locked or user-overridable per call via the Pydantic `Input` model. The decision is binary. The rationale is required either way. The decision lives next to the convention it describes — never in a separate decision-matrix file, never derived from the absence of a record.

The `exposure:` block is the source of truth. Every downstream surface — the Pydantic `<Tool>Input` schema, the MCP wrapper signature, the manifest's `pm_overridable` field, the frontend `MODULE.defaultParams` / `MODULE.paramHints` / `MODULE.interpretationCards`, and the Library page's exposed-knob list — is a **derived mirror** of the exposure decisions, not a parallel record.

---

## 2. The exposure decision protocol

For each convention the decision is binary: **expose** or **keep YAML-locked**. Apply the following two criteria; both must be true for `expose: true` to be admissible.

### 2.1 Criterion A — Material output change

The convention's value materially changes the OUTPUT a desk user reads. *Materially* means: a desk user would interpret the same query against the same data with two different convention values as **two different answers**.

- ✅ `z_score_window_days` — changing 252 → 60 changes the z-score interpretation (tactical vs structural framing).
- ✅ `z_score_ddof` — sample vs population std changes the numerical value of the z-score.
- ❌ `yield_round_decimals` — display precision; does not change the underlying answer.

### 2.2 Criterion B — Relevant to the analytical mechanism

The convention sits inside the analytical / statistical relationship the tool computes — not a structural / data-hygiene / numerical-stability lock.

- ✅ `z_score_window_days` is inside the z-score definition itself.
- ➖ `ffill_limit_days` is a data-cleaning step upstream of the math; changing it changes which days enter the math, not how the math works on the included days. **Borderline** — record the rationale either way.
- ❌ `_LINKER_INSTRUMENT_TYPE` is a structural identity invariant; exposing it would let a caller bypass the no-proxy guard ([PR6](../02_components/primitive/README.md)).

### 2.3 The protocol

For each convention in `config.yaml`:

1. Apply Criterion A. If FALSE → `expose: false`, rationale: *"display / precision / numerical-stability — not interpretation-changing."*
2. Apply Criterion B. If FALSE → `expose: false`, rationale: *"<specific reason — structural identity, data-hygiene-only, code-level invariant, etc.>"*
3. Both TRUE → `expose: true` is admissible. Record the rationale + the Pydantic `Input` field name + type.

Borderline cases default to `expose: false` with a planned-extension note. **Promotion** from locked → exposed is a follow-up PR; **demotion** exposed → locked is a breaking change requiring schema migration, frontend update, and parity-fixture regeneration.

### 2.4 Relationship to PR8 (single central methodology surface)

[PR8](../02_components/primitive/README.md) caps the `Input` surface size for LLM tool-selection clarity. Exposure decisions land conventions into that surface; they must therefore cohere into a small, well-defined set.

| Pattern | Example | PR8-compliance |
|---|---|---|
| **Single-knob exposure** | `lookback_days` on simple level tools | ✅ default; one cohesive methodology surface |
| **Cohesive multi-knob exposure** | `n_components + change_frequency + tenors` for PCA | ✅ together they specify one model |
| **Independent multi-knob exposure** | N conventions each shifting a different output dimension | ❌ either split into sibling primitives ([PR2](../02_components/primitive/README.md)) or keep all but one YAML-locked |

The exposure protocol records the decision; PR8 caps how many `expose: true` decisions are admissible on one tool.

---

## 3. The `exposure:` YAML block shape

Every entry under `conventions:` in a tool's `config.yaml` carries an `exposure:` block alongside the existing `value / source / rationale / valid_range` fields.

### 3.1 Schema

```yaml
<convention_name>:
  value: <existing>
  source: <existing — from methodology_disclosure.md §2 registry>
  rationale: <existing>
  valid_range: <existing — for numerics only>
  exposure:                              # REQUIRED
    expose: true | false                 # the decision (binary)
    rationale: <one-paragraph string>    # REQUIRED whichever value `expose` carries
    decided_at: <YYYY-MM-DD>             # ISO date of the decision
    decided_in_pr: <"#<n>" | "n/a">      # PR number; "n/a" if pre-pilot legacy back-fill
    # The next four fields are REQUIRED ONLY when expose: true
    input_field: <Pydantic field name>   # e.g. "z_score_window_days"
    pydantic_type: <type expression>     # e.g. "int", "Optional[int]", "Literal['A','B']"
    default_source: yaml | input_explicit
    promoted_from_yaml_in_pr: <"#<n>" | "n/a">
```

### 3.2 Field semantics

| Field | When required | Meaning |
|---|---|---|
| `expose` | Always | `true` → convention surfaces as a Pydantic `Input` field; `false` → convention stays YAML-only. |
| `rationale` | Always | The decision justification. For `expose: false` cite which Criterion A/B failed and why. For `expose: true` cite the desk use cases that justify the override. |
| `decided_at` | Always | ISO date the decision was made / last re-affirmed. |
| `decided_in_pr` | Always | The PR that landed the decision (or `"n/a"` for pre-pilot legacy decisions back-filled during retroactive migration). |
| `input_field` | `expose: true` only | The exact Pydantic field name in `<Tool>Input`. By convention identical to the convention name. |
| `pydantic_type` | `expose: true` only | The Pydantic type string. Must respect `valid_range` via `Field(ge=, le=)` constraints. |
| `default_source` | `expose: true` only | `yaml` → the `Input` field defaults to the YAML value (typically via a `_bundled_default_<convention>()` helper that loads the bundled config — sentinel-resolved in `compute()`). `input_explicit` → the `Input` field is required (no YAML fall-through). |
| `promoted_from_yaml_in_pr` | `expose: true` only | The PR that promoted this convention from YAML-locked → exposed (or `"n/a"` if it was exposed from day-one). Audit trail. |

### 3.3 Examples

#### Convention that becomes exposed:

```yaml
z_score_window_days:
  value: 252
  source: industry_standard_1y_window
  rationale: >-
    Trading-day count for a 1-year rolling window.  Matches every
    sovereign rates tool and OIS rate_level; the cross-config lint
    enforces the match for the DEFAULT value.
  valid_range: [60, 1260]
  exposure:
    expose: true
    rationale: >-
      The z-score window is parameterised by definition; desk users
      tactically use 60d / 126d for short-horizon work and 504d for
      structural-regime work.  Cross-config lint still enforces the
      DEFAULT value alignment across rates level tools; per-call
      overrides are runtime behaviour, not config drift, and do not
      violate the lint.
    decided_at: "2026-05-28"
    decided_in_pr: "#<n>"
    input_field: z_score_window_days
    pydantic_type: int
    default_source: yaml
    promoted_from_yaml_in_pr: "#<n>"
```

#### Convention that stays YAML-locked:

```yaml
yield_round_decimals:
  value: 4
  source: legacy_default_pre_pilot
  rationale: >-
    Four decimals matches the legacy hardcoded default in
    shared.analytics.levels.compute_level_metrics...
  valid_range: [0, 8]
  exposure:
    expose: false
    rationale: >-
      Display precision; does not change the underlying answer.
      Criterion A (material output change) fails — rounding affects
      the displayed digits, not the interpretation of the level.
      Cross-tool lint enforces alignment across rates level tools.
    decided_at: "2026-05-28"
    decided_in_pr: "#<n>"
```

#### Convention deferred (borderline):

```yaml
ffill_limit_days:
  value: 5
  source: team_judgment_pending_review
  rationale: >-
    Bridge up to one trading week of missing data across holiday gaps...
  valid_range: [1, 10]
  exposure:
    expose: false
    rationale: >-
      Borderline.  Criterion B is ambiguous — ffill is a data-cleaning
      step upstream of the math, not a parameter of the math itself.
      Defaulting to YAML-locked per the protocol; revisit if a desk
      use case for per-call override emerges.  Path to promotion:
      add the field to Pydantic Input with default_source: yaml.
    decided_at: "2026-05-28"
    decided_in_pr: "#<n>"
```

---

## 4. The propagation chain

Once an `exposure:` decision is recorded, the same decision propagates mechanically through five downstream surfaces. **Each surface is a mirror — never edit one in isolation.**

```
config.yaml `exposure:` block           ← SOURCE OF TRUTH
        │
        ├─→ Pydantic <Tool>Input field             (if exposure.expose: true)
        │           │
        │           ├─→ MCP wrapper signature       (matches the Input)
        │           │
        │           └─→ backend ToolCard            (auto-derived from Pydantic
        │                                            schema; surfaced at
        │                                            /api/v1/tools/{name})
        │
        ├─→ manifest `pm_overridable` list          (derived; mirrors the set
        │                                            of expose: true conventions)
        │
        └─→ frontend MODULE.defaultParams / paramHints / interpretationCards
                    │
                    └─→ Library page exposed-knobs list   (derived from ToolCard)
```

### 4.1 Rules

1. **No surface is hand-edited independently of the YAML decision.** If a knob appears in `MODULE.paramHints` but does NOT appear in any convention's `exposure: true` block, that's a desync — fix by either promoting the YAML entry or removing the frontend hint.
2. **Adding an exposed knob requires** (a) update `config.yaml` `exposure:` block; (b) add the Pydantic Input field; (c) update MCP wrapper kwargs + docstring; (d) update manifest `pm_overridable`; (e) add a `TestConventionOverrides`-style test proving the override changes the output; (f) update the per-tool `LIFECYCLE_CHECKLIST.md` Stage 1A row. **All in the same PR.**
3. **Removing an exposed knob (demotion to YAML-locked)** is a breaking change — bumps the tool's wire-contract version, requires a schema migration, frontend update, parity-fixture regeneration. Update the `exposure:` block's `rationale` field with the new justification; keep the old `decided_in_pr` audit trail.
4. **Cross-config lint** ([PR13](../02_components/primitive/README.md)) checks DEFAULT values across tools sharing the convention name. Per-call overrides do not violate the lint. Exposure decisions therefore do not affect lint behaviour.

### 4.2 ToolCard derivation

The backend's `/api/v1/tools/{name}` endpoint returns a `ToolCard` containing `input_fields` auto-derived from the Pydantic Input class. When a convention is exposed:

- The Pydantic `Field`'s `default=` uses the YAML value (typically resolved via a `_bundled_default_<convention>()` helper that loads the bundled config — established pattern in `calculate_zscore_custom_tool`, `calculate_cpi_surprise_tool`).
- The Pydantic `Field`'s `description=` cites BOTH the YAML default AND the override semantics (e.g. *"Trading-day z-score window. Default 252 (mirrors sovereign yield_levels); override to 60/126/504 for tactical/structural alternatives."*).
- The Pydantic `Field`'s `ge=` / `le=` constraints mirror the YAML's `valid_range`.

The frontend's `inferFieldControl` helper in `modelRegistry.ts` then maps the field type + name pattern to a UI control (slider for `*_window_*` integers, enum for `Literal[...]`, etc.).

---

## 5. Standalone module pattern (the bridge contract)

> Every PROOF-OF-CONCEPT tool — and every tool brought into compliance from now on — ships as a **standalone component**: it owns its backend code, its bridge endpoint, its frontend module, and its surfaces. Generic shared shells (the `MODULE.typedView` closed-family enum of shared views; the `/api/v1/tools/{name}/run` generic execute route consumed from a typed Build canvas) are **legacy-compatible** but no longer the default for new modules.

### 5.1 The five-element bridge

Each standalone tool ships five tightly-coupled elements:

| # | Element | Path |
|---|---|---|
| 1 | Backend tool folder (`compute.py` + `config.yaml` + `schemas.py` + `__init__.py`) | `rates_agent/<domain>/tools/<tool>/` |
| 2 | MCP wrapper (LLM-facing) | `rates_agent/<domain>/mcp_server.py::<tool>_tool` |
| 3 | **Typed-detail HTTP endpoint (frontend-facing)** | `api/routes/rates/detail/<tool_kind>.py` — per-tool route, NOT the generic `/api/v1/tools/{name}/run` route |
| 4 | Frontend module folder | `UI/macro-copilot-dashboard-polished/src/modules/primitives/<tool_name>/` |
| 5 | Frontend surfaces (Build + Monitor + optional Ask) | `UI/macro-copilot-dashboard-polished/src/modules/primitives/<tool_name>/surfaces/` |

### 5.2 Why standalone

- **Containment** ([`../02_components/surface_contract.md`](../02_components/surface_contract.md) §1) — partial state of one tool cannot leak into shared infrastructure used by other tools.
- **Closed-family discipline** ([`closed_family_discipline.md`](closed_family_discipline.md)) — the `MODULE.typedView` 7-value closed family stops growing. Adding `'real_yield'` / `'breakeven'` / `'zcis'` etc. per-tool would have required N ADRs; standalone modules sidestep this entirely.
- **Per-tool sovereignty** — a tool's backend ⇄ frontend handshake is one folder pair + one endpoint + one schema. Changing the contract is one PR with one set of tests.

### 5.3 Migration policy

- **New tools (from the Phase-1 pilot forward):** standalone by default. `MODULE.typedView` is `null` (or omitted entirely); the module claims `custom_build_surface` and ships `surfaces/BuildSurface.tsx` as the **full canvas** (controls + output, no central typed-view routing).
- **Existing tools using shared `typedView`:** legacy-compatible; opportunistic migration per [`tool_lifecycle.md §6 Phase 3`](tool_lifecycle.md). The closed family of `typedView` values is FROZEN at its current 7 entries — no new values added.
- **Future:** if every active `typedView` consumer migrates to standalone, retire the `typedView` field and the central `TOOL_TO_VIEW` map.

### 5.4 The typed-detail endpoint

Standalone tools ship their OWN typed-detail HTTP route. The route:

- Mounts at `/api/v1/rates/detail/<tool_kind>` — per-tool, where `tool_kind` is a short route slug (e.g. `real_yield`, `breakeven`, `zcis`); NOT keyed by the full `tool_name`.
- Receives the same parameters the Pydantic Input expects (via query string for GETs — convention for read-only level / spread / breakeven tools; via body for POSTs when payloads are large).
- Returns the same `<Tool>Output.model_dump()` shape `/api/v1/tools/{name}/run` would return.
- Is consumed by the frontend module's `surfaces/BuildSurface.tsx` AND `surfaces/monitor/<Widget>.tsx`.
- Has its own integration test in `tests/api/routes/rates/detail/test_<tool_kind>.py`.

**Reusing `/api/v1/tools/{name}/run` is forbidden for standalone-tool frontend Build/Monitor surfaces.** That generic route remains the LLM-facing execution surface (called via the MCP wrapper boundary); the frontend's typed-detail consumption is a separate concern that demands its own typed bridge for reasons (a)–(c) in §5.2.

---

## 6. Reviewer checks

- [ ] Every convention in `config.yaml` has an `exposure:` block.
- [ ] `exposure.rationale` is a complete sentence; for `expose: false` cites Criterion A and/or B; for `expose: true` cites the desk use case.
- [ ] If `expose: true` — the matching Pydantic Input field exists; its `default=` mirrors the YAML; its `Field(ge=, le=)` mirrors the `valid_range`; its `description=` cites the override semantics.
- [ ] If `expose: true` — the MCP wrapper signature includes the new kwarg; the wrapper docstring explains the override.
- [ ] If `expose: true` — the manifest's `pm_overridable` list includes the convention name.
- [ ] If `expose: true` — a `TestConventionOverrides`-style test proves the override changes the output.
- [ ] Parity fixtures unchanged (overriding a convention is RUNTIME behaviour; parity captures DEFAULT behaviour). If a DEFAULT value changes, fixtures regenerate per [PR15](../02_components/primitive/README.md).
- [ ] Cross-config lint (`python -m shared.config.lint`) passes.
- [ ] The tool's `LIFECYCLE_CHECKLIST.md` (per [`lifecycle_checklist_template.md`](lifecycle_checklist_template.md)) Stage 1A row matches the YAML state.
- [ ] **Standalone bridge contract:** typed-detail endpoint exists; frontend module ships `surfaces/BuildSurface.tsx` (full canvas); `MODULE.typedView` is `null` / omitted for new modules.

---

## 7. Anti-patterns

- A convention with no `exposure:` block. **The decision was never made.**
- `exposure.rationale: "TBD"` / `"defaults are fine"` / empty string. **The decision was made but not justified.**
- A frontend `MODULE.paramHints` entry with no backing `exposure: true` convention. **Frontend hand-coded a knob the backend doesn't expose.**
- A Pydantic Input field that exists with no matching `exposure: true` convention. **Knob exists but the decision protocol was skipped.**
- A manifest `pm_overridable` list maintained by hand instead of derived from YAML. **Drift between manifest and YAML.**
- Reusing `/api/v1/tools/{name}/run` from a NEW standalone module's typed Build canvas. **Generic-shell shortcut; defeats the standalone contract.**
- Adding a new value to the `MODULE.typedView` closed family for a NEW module. **Family is frozen; new modules go standalone.**
- A `source: methodology_judgement_pending_review` tag with no `exposure:` block. **Convention was registered but the exposure decision was deferred — both halves of the protocol are required.**
- A `default_source: input_explicit` (no YAML fall-through) on a convention that the cross-config lint expects to enforce alignment for. **Lint can only enforce a default; an input-explicit field has no default to align.**

---

## 8. Adoption

- **Pilot tools (Phase 1 of the `revamp` branch):** `get_real_yield_level_tool` first, then `calculate_breakeven_inflation_simple_tool`. Both ship under this standard from day-one.
- **New primitives (Phase 2):** adopt from day-one.
- **Existing primitives (Phase 3):** opportunistic migration when touched. Mass back-fill is not scheduled.

A tool counts as **compliant with this standard** only when:

1. Every convention has a complete `exposure:` block (Criterion-A/B-justified, dated, PR-referenced).
2. Every `expose: true` has the matching Pydantic / MCP / manifest / test propagation in place.
3. The standalone bridge (§5) is wired OR the tool is explicitly legacy-typedView and recorded as such in the per-tool `LIFECYCLE_CHECKLIST.md` Stage 1D.

---

## 9. Links

- [P5, P8, P10 — non-negotiables](../00_thesis/01_non_negotiables.md)
- [PR7, PR8, PR11, PR13 — primitive principles](../02_components/primitive/README.md)
- [`methodology_disclosure.md`](methodology_disclosure.md) — the source-tag registry (PR12) every `source:` value cites
- [`closed_family_discipline.md`](closed_family_discipline.md) — why `typedView` is frozen
- [`typed_boundary_discipline.md`](typed_boundary_discipline.md) — frozen + extra-forbid Pydantic at every boundary
- [`tool_lifecycle.md`](tool_lifecycle.md) — the 7 axes; §2 axis 2 cites this standard for the exposure-decision protocol
- [`lifecycle_checklist_template.md`](lifecycle_checklist_template.md) — per-tool progress tracker; Stage 1A consumes this standard
- [ADR 0014](../05_decisions/0014-frontend-module-architecture.md) — the frontend module-architecture ADR (closed-family `typedView` context)
- [ADR 0015](../05_decisions/0015-tool-metadata-db-table.md) — DB metadata table architecture

---

## 10. Version log

| Version | Date | Change |
|---|---|---|
| v1 | 2026-05-28 | Initial standard.  Defines the exposure decision protocol (Criterion A + Criterion B), the `exposure:` YAML block shape, the propagation chain (config → Pydantic → MCP → manifest → frontend), and the standalone-module bridge pattern (every new module ships its own typed-detail endpoint + full BuildSurface, no shared typedView reuse).  Adopted by the Phase-1 pilot tool `get_real_yield_level_tool`. |
