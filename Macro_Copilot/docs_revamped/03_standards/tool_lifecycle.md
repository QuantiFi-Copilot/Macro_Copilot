# Tool Lifecycle Standard

> The end-to-end standard for shipping a primitive tool in Macro_Copilot.  Every tool — backend Python + config + tests + frontend surfaces + library copy + status tracking — follows this lifecycle.  Workflows extend the standard with caveats (§5).

**Version:** v1 (Phase 0 — initial drafting)
**Status:** draft
**Branch discipline:** all Phase 0+ work lives on the `revamp` branch.  `build` stays untouched until `revamp` is ready to merge back.
**See also:** [`../02_components/surface_contract.md`](../02_components/surface_contract.md) (cross-tool status registry), [`../05_decisions/0015-tool-metadata-db-table.md`](../05_decisions/0015-tool-metadata-db-table.md) (DB metadata architecture).

---

## 0. Purpose

A primitive tool is "fully shipped" only when it is complete across **all seven axes** below.  The lifecycle standard is the canonical answer to "what does done mean?".

Two consumers of this doc:

1. **Per-tool work** — pick a tool, walk the seven axes, ship each one inside that tool's folders, update the row in `surface_contract.md`.
2. **Framework work** — when the standard itself needs to evolve (new axis, new field), update this doc + the contract registry shape.

The standard's **organising principle** is: every tool truth lives in **exactly one source-of-truth location**, with all other surfaces (UI, docs, lineage system) reading from it.  The DB holds the static, queried-often facts (units, theoretical reference, narrative).  The config YAML holds the user-overridable methodology.  The Python script holds the executable logic.  Documentation mirrors the DB + config for human readability — never the inverse.

---

## 1. THE LIFECYCLE PRINCIPLE (load-bearing)

> A primitive's methodology is **a user choice, not a developer lock**.  The whole point of the config-YAML + Python-script split is that the user dials in their preferred conventions (z-score window, day-count, threshold rule, etc.) at run time.  V1 of any tool ships ONE methodology; V2+ ships N methodologies the user picks between.  There is no separate "methodology version log" — the user always reconstructs any prior methodology by picking the config values they want.

Consequences:
- Tool config schemas are designed for **multiple methodologies from day one**, even if only one is wired up in V1.
- Convention changes don't require deprecation policies — they require a new config option.
- The lineage system records WHICH conventions a given output was produced under; replay-with-original reconstructs deterministically.

---

## 2. The 7 axes of a fully-shipped tool

| # | Axis | Source of truth | Owner |
|---|---|---|---|
| 1 | **Theoretical reference** — institutional textbook / paper / standard (e.g. Tuckman 4e Ch. 5 §5.3, Fabozzi Vol. III Ch. 12) | DB `tool_metadata.theoretical_reference` | Per-tool engineer |
| 2 | **Methodology** — conventions block (z-score window, day-count, threshold rule, etc.); each entry carries `value`, `source`, `rationale`, and an `exposure:` block per [`methodology_exposure.md`](methodology_exposure.md) recording whether the convention is YAML-locked or user-overridable via the Pydantic `Input` model | `rates_agent/<domain>/tools/<tool>/config.yaml` | Per-tool engineer |
| 3 | **Three-way testing** — pytest (compute, wiring, parity) + SQL ground-truth (DB-vs-Python) + source-material verification (human-judgment sign-off) | `tests/test_<tool>_*.py` + `tests/fixtures/<tool>_v1/` + DB `tool_metadata.source_material_verified` (status flag) | Per-tool engineer |
| 4 | **Input / output contracts** — Pydantic `<Tool>Input` / `<Tool>Output` classes; output field units in DB; cross-tool composability documented in the README | `rates_agent/<domain>/tools/<tool>/schemas.py` + DB `tool_metadata.output_field_units` | Per-tool engineer |
| 5 | **Known limitations** — what this tool can NOT do; edge cases that have been encountered; documented dependencies that, if missing, break behaviour | DB `tool_metadata.known_limitations` | Per-tool engineer |
| 6 | **Frontend surfaces** — every surface the tool ships per the surface contract (Monitor / Ask / Build / Library / parameter overrides); the Build surface is dual-view (extended + compact) per [`rendering_density.md`](rendering_density.md), with both views MANDATORY for every new primitive | `src/modules/primitives/<tool_name>/` + `surface_contract.md` row | Per-tool engineer |
| 7 | **User-facing copy** — the Library-page description + macro narrative ("what macro question does this answer · why a PM cares") | DB `tool_metadata.desk_narrative` + `manifesto/03_tool_manifest/<domain>_manifest.yml` mirror | Per-tool engineer |

**Items explicitly NOT in the standard (deferred / out of scope):**

- Methodology version log — superseded by §1 (user-config architecture).
- Performance budgets — Python + Postgres are fast enough for V1; revisit post-product.
- Sample / golden fixtures for onboarding — nice-to-have, not core; revisit V2.
- Deprecation / breaking-change policy — V2 hardening; prove functionality first.
- Failure-mode forecasting (speculative) — document only KNOWN limitations.

---

## 3. Where each tool truth lives

| Truth | Live location | Why there |
|---|---|---|
| **Static, queried often** (units, theoretical reference, desk narrative, known limitations) | DB `macro_data.tool_metadata` table | Single source; queried by frontend, backend operators, lineage system, library page.  Robust against YAML drift. |
| **Configurable methodology** (z-score windows, thresholds, day-count rules, etc.) | `rates_agent/<domain>/tools/<tool>/config.yaml` | Executable: loaded by Python at `compute()` time.  User-overridable in V2+. |
| **Executable logic** | `rates_agent/<domain>/tools/<tool>/*.py` | The tool itself. |
| **Input / output schema** | `rates_agent/<domain>/tools/<tool>/schemas.py` | Pydantic classes; LLM-facing.  Generated docstrings flow to the Library. |
| **Tests** (compute, wiring, parity, SQL ground-truth) | `tests/test_<tool>_*.py` + `tests/fixtures/<tool>_v1/` | Per-tool naming convention; runnable via pytest. |
| **Frontend module spec** | `src/modules/primitives/<tool_name>/module.ts` | Module-first dispatch architecture; per surface contract §1 containment principle. |
| **Frontend surface code** | `src/modules/primitives/<tool_name>/surfaces/` | Same: module-folder containment. |
| **Per-tool README** (human-readable consolidation, user + developer sections) | `rates_agent/<domain>/tools/<tool>/README.md` | Onboarding entry point; mirrors DB + config + schema. |
| **Cross-tool catalog entry** | `manifesto/03_tool_manifest/<domain>_manifest.yml` | Library page data source.  Lighter than the per-tool README; gets the desk-narrative field mirror. |
| **Cross-tool status registry** | `docs_revamped/02_components/surface_contract.md` | Tracks lifecycle status per tool across all 7 axes (roll-up summary view; 4-value enum per axis). |
| **Per-tool lifecycle progress** | `rates_agent/<domain>/tools/<tool>/LIFECYCLE_CHECKLIST.md` (or the operator / workflow analog) | Per-tool granular check-markable record across the 8 lifecycle stages per [`lifecycle_checklist_template.md`](lifecycle_checklist_template.md).  Authoritative for *"is this stage really done?"*; the cross-tool registry above is a derived summary view. |
| **Convention exposure decisions** | `rates_agent/<domain>/tools/<tool>/config.yaml` `conventions.<name>.exposure:` block | The decision (YAML-locked vs LLM/UI-exposed), rationale, and propagation metadata for every methodology convention, per [`methodology_exposure.md`](methodology_exposure.md).  Manifest's `pm_overridable`, the Pydantic `Input` surface, the MCP wrapper kwargs, and the frontend `MODULE.paramHints` are all derived mirrors of these blocks. |

---

## 4. The per-tool README template

Each tool ships `rates_agent/<domain>/tools/<tool>/README.md` with **explicit user-facing and developer-facing sections**:

```markdown
# <tool_name>

> One-line summary.

## For desk users

### What this tool tells you
[Plain-English: "this tool computes X about Y.  Use it when you want to know Z."]

### When to use it
[Concrete macro questions the tool answers; sample interpretation of typical outputs.]

### How to read the output
[Units; sign conventions; ranges typical values fall into; what extreme values indicate.]

### Known limitations
[Quote from DB tool_metadata.known_limitations.]

---

## For developers

### Theoretical reference
[Quote from DB tool_metadata.theoretical_reference + brief excerpt if relevant.]

### Methodology
[Link to config.yaml + summary of conventions block.  Cite the `value`, `source`, `rationale` triples.]

### Input contract
[Pydantic class link + field summary.]

### Output contract
[Pydantic class link + field summary + units per field (mirror of DB tool_metadata.output_field_units).]

### Testing
- Unit / compute tests: `tests/test_<tool>_compute.py`
- Wiring tests: `tests/test_<tool>_wiring.py`
- Parity fixture: `tests/fixtures/<tool>_v1/`
- SQL ground-truth: `tests/test_<tool>_sql_validation.py`
- Source-material verification: see DB tool_metadata.source_material_verified

### Frontend surfaces
- Module folder: `src/modules/primitives/<tool_name>/`
- Surface contract row: `docs_revamped/02_components/surface_contract.md` §4
```

The README is **not** the source of truth for any field — it always mirrors the DB / config / schemas.  Treat it as the human-readable consolidation, not the authoritative store.

---

## 5. Workflow caveats

Workflows (`event_study`, `regime_conditioned_relationship`, `backtest`) follow the same standard with these adjustments:

- **§1 methodology principle** holds — workflows have a template-level config (`rates_agent/workflows/<workflow>/template.yaml`) that exposes "central analysis knobs" as slots.  The wiring topology is template-locked.
- **§2 axis 4 (input/output contracts)** — workflow slot schema is the input contract; the terminal node's artifact type is the output contract.
- **§2 axis 6 (frontend surfaces)** — workflows render via specialised dashboards (`src/components/build/results/dashboards/<Workflow>Dashboard.tsx`) plus a workflow-level row in surface contract §5.
- **DAG-node role schema** — each workflow declares which template node IDs map to which dashboard "roles" (Setup, Events, Windows, Aggregate, Output for event_study; etc.).  Currently hardcoded in `src/components/build/results/lib/resolveWorkflowArtifacts.ts`.  Phase 1+ work standardises this declaration.

Workflows are **out of scope for the pilot pair** — primitives only.  Workflow standardisation happens after the primitive standard proves out.

---

## 6. Pilot strategy

| Phase | Scope | Branch | Exit criterion |
|---|---|---|---|
| **Phase 0** (this PR) | Spec doc + DB infrastructure + extended contract registry.  Zero per-tool work. | `revamp` | Lifecycle doc, ADR 0015, contract registry extension, DB table + migration + population script (mechanical fields only) all in place.  No production code touched. |
| **Phase 1** | Pilot 2 primitives end-to-end across all 7 axes. | `revamp` (sub-branches OK) | Both pilot tools' rows in the contract show all 7 axes green.  Standard surveyed against pilots; any gap triggers spec update. |
| **Phase 2** | Apply standard to every new tool going forward. | `revamp` → eventual merge back to `build` | Standard stable; new-tool PRs follow the framework by default. |
| **Phase 3** | Opportunistic retroactive migration. | per-tool sub-branches off `revamp` | Tools brought up to standard when touched for any reason.  No mass migration pass. |

**Pilot pair (Phase 1):**

1. `get_real_yield_level_tool` — direct analog of the already-polished `get_yield_levels_tool`.  Tests the standard for a typical single-series tool with the lowest risk.
2. `calculate_breakeven_inflation_simple_tool` — methodologically richer (derived calculation: linker yield − nominal yield with day-count alignment).  Tests the standard for a derived primitive.  Already has `tests/test_breakeven_inflation_simple_sql_validation.py`.

Workflows deferred to a later phase.

**Pilot exit criterion:** the next tool we attempt requires NO change to this standard.  When that happens, the pilot is done.

---

## 7. PR gates (added to surface contract §9)

In addition to the existing 4 gates, a tool-touching PR must:

1. **Update the affected tool's contract row** across the 7-axis status fields.
2. **Tier claims must match delivery** AND lifecycle axis status must match delivery — claiming `source_material_verified` requires a human sign-off note in the per-tool README's developer section.
3. **Static metadata edits go through the DB** — never edit the YAML mirror without updating the DB row in the same PR (and vice versa).
4. **Methodology changes (config.yaml edits) require:** (a) the parity fixture regenerated, (b) a rationale in the PR message, (c) the README's "For developers" section updated.

---

## 8. Change log

| Version | Date | Change |
|---|---|---|
| v1 | 2026-05-26 | Initial drafting (Phase 0).  Defined the 7 axes (theoretical reference, methodology, three-way testing, input/output contracts, known limitations, frontend surfaces, user-facing copy).  Codified the §1 methodology principle (user-config architecture).  Pinned pilot pair to 2 primitives (`get_real_yield_level_tool` + `calculate_breakeven_inflation_simple_tool`).  Workflows scoped out of pilot.  Cross-references to ADR 0015 (DB metadata table) and surface contract registry. |
| v1.1 | 2026-05-28 | **Rails for Phase-1 pilot.**  Linked §2 axis 2 (Methodology) + §3 (truth-location table) to two new sibling standards: [`methodology_exposure.md`](methodology_exposure.md) (per-convention `exposure:` block decision protocol + the standalone-module bridge contract — typed-detail endpoint per tool, no shared `typedView` reuse for new modules) and [`lifecycle_checklist_template.md`](lifecycle_checklist_template.md) (8-stage assembly-line per-tool `LIFECYCLE_CHECKLIST.md` template, ☐/☑/⏸/⊘ status convention, source-material-verification `pending`-state treatment).  No content edits to §1, §2 axes 1/3-7, §4-7.  The 7-axis lifecycle is unchanged; the new standards specify HOW axes 1, 2, 3, 5, 7 are operationalised at the granular check level. |
| v1.2 | 2026-05-28 | **Dual-view rendering-density mandate.**  §2 axis 6 (Frontend surfaces) cross-references the new [`rendering_density.md`](rendering_density.md) standard: the Build surface is dual-view (extended + compact) and BOTH are mandatory for every new primitive — no opt-in.  Multi-tool prompts dispatch to compact views inside a DAG visualization with click-to-expand affordances; single-tool prompts mount the extended view directly.  Workflow templates are explicitly out of scope (future workflow contract).  No content edits to axes 1, 2, 3, 4, 5, 7. |
