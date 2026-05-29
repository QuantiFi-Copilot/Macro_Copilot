# Lifecycle Checklist (per tool)

> Every primitive (and every operator / workflow analog) ships with a per-tool `LIFECYCLE_CHECKLIST.md` file in its own folder. The checklist is the **granular, check-markable record** of the tool's progress through the 7-axis lifecycle — the assembly-line for closeout. The roll-up summary lives in [`../02_components/surface_contract.md`](../02_components/surface_contract.md) §10; the per-stage granular truth lives in this file, with the tool.

**Version:** v1
**Last reviewed:** 2026-05-28
**Status:** load-bearing. Required for every new tool from the Phase-1 pilot forward.
**Operationalises principles:** [P10](../00_thesis/01_non_negotiables.md) (single source of truth), [P11](../00_thesis/01_non_negotiables.md) (domain isolation / containment).
**See also:** [`tool_lifecycle.md`](tool_lifecycle.md) (the 7 axes), [`methodology_exposure.md`](methodology_exposure.md) (the exposure-decision protocol consumed in Stage 1A), [`../02_components/surface_contract.md`](../02_components/surface_contract.md) §10 (the roll-up summary view), [ADR 0015](../05_decisions/0015-tool-metadata-db-table.md) (`source_material_verified` JSONB shape).

---

## 1. Purpose

Three problems this standard solves:

1. **Reproducibility across chat sessions and engineers.** A new contributor — human or AI agent in a fresh chat — opening the tool folder reads the checklist and knows exactly which stages are done, which are pending, who owns the open items, and what the acceptance gate for the next stage is. No tribal knowledge required.

2. **Factory-pipeline discipline.** Each lifecycle stage has explicit acceptance gates. A stage cannot be marked complete until every check inside it is closed. Promotion through stages is mechanical, not negotiable.

3. **Surface-contract symmetry.** The roll-up row in `surface_contract.md §10` is a **summary view** over the per-tool checklist. The checklist is the source of truth for *"is this stage really done?"*. The summary row mirrors it.

---

## 2. File location + naming

```
rates_agent/<domain>/tools/<tool>/LIFECYCLE_CHECKLIST.md         # primitive
shared/operators/<operator>/LIFECYCLE_CHECKLIST.md               # operator analog
rates_agent/workflows/<workflow>/LIFECYCLE_CHECKLIST.md          # workflow analog
```

The file lives WITH the artifact it describes — never in a central spreadsheet, never in a shared tracker. Containment per [PR1](../02_components/primitive/README.md) / [FM2](../02_components/frontend_module/README.md).

---

## 3. Status conventions

Every item in the checklist carries one of four status markers:

| Marker | Meaning |
|---|---|
| `☐` | **Open** — not started, or work-in-progress |
| `☑` | **Done** — completed; meets the stage's acceptance gate |
| `⏸` | **Pending** — deliberately paused with a known reason + assignee + (optional) target date |
| `⊘` | **Not applicable** — explicitly does NOT apply to this tool, with a one-line reason |

Markers are character-level (Unicode); they render in any Markdown viewer including GitHub. CI does not parse them today; they are reviewer signals.

**No item is ever left without a marker.** A bullet without a marker is auto-reject in review (decision was not made; intent is ambiguous).

---

## 4. The 8-stage assembly line

Each tool's lifecycle proceeds through 8 stages. A stage's checks are check-marked individually; the stage gate at the end of each stage promotes the tool to the next stage. **A stage cannot be marked complete unless every check inside it is `☑` or explicitly `⊘`.**

| Stage | Theme | Gate criterion |
|---|---|---|
| **Stage 1 — Backend specification** | Conventions, exposure decisions, Pydantic, MCP wrapper, manifest mirror, standalone bridge contract | Every convention in `config.yaml` has an `exposure:` block per [`methodology_exposure.md`](methodology_exposure.md); Pydantic Input + MCP wrapper + manifest `pm_overridable` mirror them; standalone-bridge contract claimed (or legacy carve-out recorded). |
| **Stage 2 — Backend tests** | Compute / wiring / parity / SQL-validation triplet + override tests | All four test files exist + pass; parity fixtures captured; SQL validator green against live DB; `TestConventionOverrides` covers every `expose: true` convention. |
| **Stage 3 — DB metadata** | `macro_data.tool_metadata` row populated | `theoretical_reference` + `known_limitations` + `desk_narrative` populated; `source_material_verified` is either `verified` (DB JSONB populated) or **tracked as `pending`** in Stage 8 with assignee. |
| **Stage 4 — Frontend module spec** | `module.ts` + THESIS + loader + tests | Tier set declared; THESIS Q1–Q5 answered against the actual tier set; both `MODULE.surfaces.buildExtended` AND `MODULE.surfaces.buildCompact` declared per [`rendering_density.md`](rendering_density.md) (no opt-in); loader entry in `src/modules/index.ts` (alphabetical position); `__tests__/module.spec.ts` calls `assertStandardModuleInvariants` and passes; `MODULE.typedView` is `null` (or omitted) for standalone modules. |
| **Stage 5 — Frontend bridge endpoint** | Typed-detail HTTP route + service helper + frontend type | Per [`methodology_exposure.md §5.4`](methodology_exposure.md); endpoint mounted at `/api/v1/rates/detail/<tool_kind>`; integration test green; service helper + frontend type wired.  Both compact and extended Build views consume the SAME typed-detail endpoint (the compact view just renders less of the payload). |
| **Stage 6 — Frontend surfaces** | Build (extended + compact, both REQUIRED) + Monitor (+ optional Ask) | Both `surfaces/BuildExtended.tsx` AND `surfaces/BuildCompact.tsx` ship per [`rendering_density.md`](rendering_density.md); compact view includes the click-to-expand affordance + methodology disclosure (compact form) + tone cues; Monitor + Ask surfaces ship if their tiers are claimed; module tests + build tests + cross-side parity all green; manual smoke clean for BOTH single-tool dispatch (extended) AND multi-tool dispatch (compact + expand-to-modal). |
| **Stage 7 — Mirrors + contracts** | `surface_contract.md`, per-tool README, manifest, graph | §4 row updated for new build/monitor status; §10 row reflects current axis statuses; §11 changelog appended; per-tool `README.md` created per [`tool_lifecycle.md §4`](tool_lifecycle.md); manifest mirror current; `graphify update .` run. |
| **Stage 8 — Closeout sign-off** | Human source-material verification + final review | `source_material_verified` JSONB populated; manifest `validation_status` flipped from "Not validated yet" → verified-state string; reviewer signs the sign-off block. |

### 4.1 Stage promotion rules

- **Stages 1 → 2 → 3 are sequential and ALL backend.** No frontend work begins until Stage 3 is complete.
- **Stages 4 → 5 → 6 are sequential and ALL frontend.** They begin after Stage 3 closes.
- **Stage 7** mirrors the post-Stage-6 state into docs / contracts / README. May land in the same PR as Stage 6.
- **Stage 8** is the only stage that can stay open across multiple PRs (waiting on human verification). The tool's `surface_contract.md §10` axis-3 row remains `in-progress` until Stage 8 closes.

### 4.2 Partial / multi-PR splits

A tool may legitimately span multiple PRs. Recommended decomposition:

- **Rails PR** — landing the per-tool `LIFECYCLE_CHECKLIST.md` itself + any pre-Stage-1 scaffolding.
- **Backend PR** — Stages 1 → 2 → 3 in one shot.
- **Frontend PR** — Stages 4 → 5 → 6 in one shot.
- **Closeout PR** — Stage 7 + Stage 8.

Each PR check-marks its stage's items in the checklist + appends to the version log at the bottom of the per-tool checklist file. The checklist is the persistent state across PRs.

---

## 5. Source-material verification: `pending` treatment

[ADR 0015](../05_decisions/0015-tool-metadata-db-table.md) defines `macro_data.tool_metadata.source_material_verified` as a JSONB carrying `{verifier, date, source}`. The DB shape recognises two end-states:

- **`NULL`** = no record (never been considered).
- **Populated `{verifier, date, source}`** = verified.

Phase-1 reality includes a **third state: deliberately pending**. The DB does NOT model this — keeping the DB JSONB shape clean per ADR 0015. The intermediate state lives in the per-tool `LIFECYCLE_CHECKLIST.md` Stage 8 row, marked `⏸`:

```markdown
### Stage 8 — Closeout sign-off

- ⏸ **Source-material verification: pending**
  - Assigned to: <name or "TBD">
  - Target date: <YYYY-MM-DD or "TBD">
  - Source to verify against: <e.g. "Tuckman 4e Ch. 22; primary issuer documentation per country">
  - Notes: <e.g. "PM has the textbook; will review week of YYYY-MM-DD.">
```

When verification completes:

1. Replace `⏸` with `☑` on the row.
2. Apply the DB UPDATE populating `source_material_verified` with `{verifier, date, source}`.
3. Flip the manifest's `validation_status` field to the verified-state string.
4. Update the per-tool README's developer section with the sign-off note.
5. Update `surface_contract.md §10` axis-3 status from `in-progress` → `shipped`.

A tool MAY ship to production with `pending` source-material verification — Stages 1–7 close the user-functional contract; Stage 8 closes the institutional-defensibility contract. Both are tracked; only Stage 8 unblocks the `§10` axis-3 `shipped` status.

---

## 6. Sign-off + commit traceability

Each stage's gate is signed off by recording, at the stage header, the closing commit + reviewer:

```markdown
## Stage 3 — DB metadata  ☑ closed
- Closed in commit: <short SHA>
- Closed by: <name>
- Date: <YYYY-MM-DD>
```

Re-opening a stage (e.g. a downstream PR breaks an item) re-flips its marker to `☐` and appends a row to the version log at the bottom of the per-tool checklist with the re-open rationale.

---

## 7. Relationship to `surface_contract.md` §10

The roll-up summary in `surface_contract.md §10` maintains ONE row per tool with ONE status per axis (4-value enum: `shipped` / `in-progress` / `not-started` / `n/a`). The per-tool `LIFECYCLE_CHECKLIST.md` is the granular truth. Mapping:

| `§10` axis status | Maps to per-tool checklist state |
|---|---|
| `shipped` | Stage(s) containing this axis are closed (gate met) |
| `in-progress` | Stage(s) containing this axis are open or partially closed |
| `not-started` | Stage(s) containing this axis are untouched |
| `n/a` | The axis is marked `⊘` (not applicable) in the per-tool checklist with a recorded reason |

The roll-up is updated in the same PR that closes the underlying stage. **Drift between the per-tool checklist and the §10 roll-up is a PR-gate violation.**

### 7.1 7-axis to 8-stage mapping (reference)

| Lifecycle axis (per `tool_lifecycle.md §2`) | Owning stage(s) |
|---|---|
| Axis 1 — Theoretical reference | Stage 3 (DB write) + Stage 8 (human verification) |
| Axis 2 — Methodology | Stage 1 (exposure decisions + YAML state) |
| Axis 3 — Three-way testing | Stage 2 (pytest + SQL) + Stage 8 (source-material verification) |
| Axis 4 — Input/output contracts | Stage 1 (Pydantic) + Stage 2 (tests pin them) |
| Axis 5 — Known limitations | Stage 3 (DB write) |
| Axis 6 — Frontend surfaces | Stages 4 → 5 → 6 |
| Axis 7 — Desk narrative + Library copy | Stage 3 (DB write) + Stage 7 (README mirror) |

---

## 8. Reviewer checks

- [ ] Every new tool PR contains a `LIFECYCLE_CHECKLIST.md` in the tool folder before any other code lands.
- [ ] The checklist follows the canonical template in §10 (Appendix) below — section names and stage names are NOT renamed or reordered.
- [ ] **Stage gates respected:** a stage cannot be promoted (next stage's work cannot land) until the previous stage's items are all `☑` or `⊘` (with reasons recorded).
- [ ] Every `⊘` carries a one-line reason. `⊘` without reason is auto-reject.
- [ ] Every `⏸` carries an assignee + target date (or explicit "TBD" with a written note in the same row).
- [ ] The roll-up in `surface_contract.md §10` is updated in the same PR that closes the underlying stage.
- [ ] Sign-off blocks at the head of each closed stage record commit + reviewer + date.
- [ ] Version log at the bottom of the per-tool checklist is appended (never rewritten).
- [ ] Renames / reorders of stages or sub-items are auto-reject (standardisation depends on identical structure across tools).

---

## 9. Anti-patterns

- A tool folder with NO `LIFECYCLE_CHECKLIST.md`. Either a new tool that skipped the gate, or a legacy tool not yet migrated — both need closing.
- A renamed / reordered stage. **Standardisation depends on identical section structure across tools.**
- `⊘` items with no reason ("not applicable" with no explanation — usually means the contributor wasn't sure).
- `⏸` items with no assignee. Pending = somebody owes; no owner = it's actually `☐`.
- Stage 2+ items checked while Stage 1 has open items. **Out-of-order promotion.**
- The roll-up in `surface_contract.md §10` flipped to `shipped` while the per-tool checklist's matching stage has open items. **Drift.**
- A `pending` source-material verification with no DB JSONB write planned. **Verification was deferred indefinitely without a path to closure.**
- Stage-header sign-off block missing commit + reviewer + date. **Gate was closed but the audit trail is absent.**

---

## 10. Appendix — the canonical per-tool template

Copy verbatim into `rates_agent/<domain>/tools/<tool>/LIFECYCLE_CHECKLIST.md`. Replace `<TOOL_NAME>` and per-tool fields. Do NOT rename stages, sections, or sub-items.

```markdown
# LIFECYCLE CHECKLIST — `<TOOL_NAME>`

> Per-tool progress tracker.  See [`docs_revamped/03_standards/lifecycle_checklist_template.md`](../../../../docs_revamped/03_standards/lifecycle_checklist_template.md) for the standard.
> Roll-up row: [`docs_revamped/02_components/surface_contract.md`](../../../../docs_revamped/02_components/surface_contract.md) §10.

**Tool:** `<TOOL_NAME>`
**Domain:** `<sub_agent>`
**Started:** YYYY-MM-DD
**Current stage:** Stage <N>
**Compliance posture:** YES from day one  |  Legacy + opportunistically migrated

---

## Stage 1 — Backend specification  ☐ open

### 1A. Convention exposure decisions (per [`methodology_exposure.md`](../../../../docs_revamped/03_standards/methodology_exposure.md))

One row per convention in `config.yaml`. Update the `exposure:` block in YAML when the row flips to `☑`.

| Convention | Decision | Rationale (one line) |
|---|---|---|
| `<convention_1>` | `☐ expose / keep-yaml — TBD` | TBD |
| `<convention_2>` | `☐` | TBD |
| ... | ... | ... |

### 1B. Pydantic Input + MCP wrapper

- ☐ Pydantic `<Tool>Input` updated to reflect every `expose: true` convention (per 1A)
- ☐ MCP wrapper signature in `<domain>/mcp_server.py` matches the Input
- ☐ MCP wrapper docstring covers each new override

### 1C. Manifest mirror

- ☐ Manifest `pm_overridable` list derived from `expose: true` set
- ☐ Manifest `references` mirrors DB `theoretical_reference` (post Stage-3)
- ☐ Manifest `one_liner` is **PM-facing prose**, NOT engineer-facing description.  The `one_liner` is the text the Library card surfaces to the user; it should answer *"what does this tell me as a desk user?"* in 1-2 sentences without internal jargon (no "analogue of X", no method-name references, no "z-score override-tunable per call" implementation talk).  Technical detail belongs in the per-tool `README.md` and the DB `tool_metadata.desk_narrative` field.  Reviewer rejects PRs whose `one_liner` reads like a commit message or class docstring.

### 1D. Standalone bridge contract (per [`methodology_exposure.md §5`](../../../../docs_revamped/03_standards/methodology_exposure.md))

- ☐ Tool does NOT reuse shared `typedView` (or explicit legacy carve-out noted with reason)
- ☐ Typed-detail endpoint planned at `/api/v1/rates/detail/<tool_kind>` (delivered in Stage 5)

**Stage gate:** every check in 1A–1D is `☑` or `⊘`.

---

## Stage 2 — Backend tests  ☐ open

- ☐ `tests/test_<tool>_compute.py` exists + passes
- ☐ `tests/test_<tool>_wiring.py` exists + passes
- ☐ `tests/test_<tool>_parity.py` exists + passes
- ☐ `tests/fixtures/<tool>_v1/` carries representative fixtures
- ☐ `tests/test_<tool>_sql_validation.py` exists + green against live DB
- ☐ `TestConventionOverrides`-style coverage for every `expose: true` convention (per Stage 1A)
- ☐ `python -m shared.config.lint` passes
- ☐ Parity fixtures regenerated IF Stage 1 changed methodology DEFAULT values

**Stage gate:** every check is `☑`.

---

## Stage 3 — DB metadata  ☐ open

- ☐ `macro_data.tool_metadata` row exists (mechanical fields via `populate_tool_metadata.py`)
- ☐ `theoretical_reference` populated per the metadata package
- ☐ `known_limitations` populated
- ☐ `desk_narrative` populated
- ☐ `output_field_units` JSONB matches the Pydantic Output structure
- ☐ Migration file `database/migrations/YYYY-MM-DD_phase1_<tool>_curated.sql` checked in
- ☐ `source_material_verified` either populated (Stage 8 done) OR explicit `⏸` row in Stage 8 with assignee + target

**Stage gate:** every check is `☑` (Stage 8 status `⏸` is acceptable as long as the row is populated).

---

## Stage 4 — Frontend module spec  ☐ open

- ☐ `src/modules/primitives/<tool_name>/module.ts` declares final tier set
- ☐ `THESIS.md` Q1 enumerates BOTH "extended Build view" and "compact Build view" as shipped surfaces; Q2 describes what the PM reads off EACH view; Q3 justifies the compact view's curated headline metrics
- ☐ `MODULE.surfaces.buildExtended` populated (per [`rendering_density.md §5`](../../../../docs_revamped/03_standards/rendering_density.md) — REQUIRED for every new primitive; no opt-in)
- ☐ `MODULE.surfaces.buildCompact` populated (per [`rendering_density.md §5`](../../../../docs_revamped/03_standards/rendering_density.md) — REQUIRED for every new primitive; no opt-in)
- ☐ `__tests__/module.spec.ts` exercises `assertStandardModuleInvariants` and asserts presence of BOTH surface fields + BOTH surface files
- ☐ Loader entry added in `src/modules/index.ts` (alphabetical position)
- ☐ `MODULE.defaultParams` / `paramHints` / `interpretationCards` mirror Stage 1A exposure decisions
- ☐ `MODULE.typedView` is `null` (or omitted) per the standalone pattern (or legacy carve-out per Stage 1D)

**Stage gate:** every check is `☑`.

---

## Stage 5 — Frontend bridge endpoint  ☐ open

- ☐ Typed-detail route mounted at `/api/v1/rates/detail/<tool_kind>` per [`methodology_exposure.md §5.4`](../../../../docs_revamped/03_standards/methodology_exposure.md)
- ☐ Route file in `api/routes/rates/detail/<tool_kind>.py`
- ☐ Route receives the same params the Pydantic Input expects
- ☐ Route returns `<Tool>Output.model_dump()` shape
- ☐ Integration test in `tests/api/routes/rates/detail/test_<tool_kind>.py` green
- ☐ Frontend service helper (e.g. `fetchDetail<ToolKind>`) added to `src/services/ratesApi.ts`
- ☐ Frontend type definition added to `src/types/rates.ts`

**Stage gate:** every check is `☑`.

---

## Stage 6 — Frontend surfaces  ☐ open

### 6A. Build dual-view (per [`rendering_density.md`](../../../../docs_revamped/03_standards/rendering_density.md))

- ☐ `surfaces/BuildExtended.tsx` — full canvas (controls + output + methodology + provenance); standalone (no central typed-view routing); mounted by `VirtualPrimitiveCanvas` for single-tool queries AND by the click-to-expand modal infrastructure when invoked from a compact card
- ☐ `surfaces/BuildCompact.tsx` — grid card (~400×280px target at `size='small'`); REQUIRED content per `rendering_density.md §2.2`: tool identity chip + headline metric(s) + sparkline + methodology disclosure (compact form: tooltip / icon / one-line caveat) + expand affordance calling `onExpand` prop + tone cues for sign/extremity; size-aware (`'small' | 'medium'` prop); mounted as a node body inside multi-tool DAG visualizations
- ☐ Compact view does NOT carry the controls strip (editing happens via the expand path)
- ☐ Compact view does NOT mount its own modal (the `openExtendedView` shared infrastructure handles modal mounting + breadcrumb)

### 6B. Other surfaces (if their tiers are claimed)

- ☐ `surfaces/monitor/<WidgetName>.tsx` — Monitor tile (if `monitor_surface` claimed)
- ☐ `MODULE.monitorWidgets[]` declares per-widget metadata inline
- ☐ `surfaces/AskCard.tsx` (if `ask_surface` claimed)

### 6C. Tests + smoke

- ☐ `npm run test:modules -- src/modules/primitives/<tool_name>` clean
- ☐ `npm run test:build` clean
- ☐ `python tools/check_module_parity.py` clean
- ☐ Manual smoke: Library → Open in Build → renders the **extended** Build view (single-tool dispatch)
- ☐ Manual smoke: Ask handoff for a multi-tool query containing this tool → renders the **compact** Build view inside the DAG visualization (multi-tool dispatch)
- ☐ Manual smoke: click the expand affordance on the compact card → opens the extended view in a modal/drawer with breadcrumb back to the DAG
- ☐ Manual smoke: Build → Run against live DB on both views — extended produces the full canvas data, compact produces the headline data
- ☐ Manual smoke (if monitor): catalog modal → add widget → renders live

**Stage gate:** every check is `☑`.

---

## Stage 7 — Mirrors + contracts  ☐ open

- ☐ `docs_revamped/02_components/surface_contract.md §4` row updated (build_archetype, build_status, monitor_status)
- ☐ `docs_revamped/02_components/surface_contract.md §10.3` row reflects current axis statuses
- ☐ `docs_revamped/02_components/surface_contract.md §11` changelog appended
- ☐ Per-tool `README.md` created per [`tool_lifecycle.md §4`](../../../../docs_revamped/03_standards/tool_lifecycle.md)
- ☐ Manifest `validation_status` updated (after Stage 8) OR set to `"Stage 1–7 complete; source-material verification pending"`
- ☐ `graphify update .` run to refresh the knowledge graph

**Stage gate:** every check is `☑`.

---

## Stage 8 — Closeout sign-off  ☐ open

- ☐ / ⏸ **Source-material verification**
  - Assigned to: TBD
  - Target date: TBD
  - Source: <e.g. "Tuckman 4e Ch. 22 + per-country primary issuer docs">
  - Notes: TBD
- ☐ DB `source_material_verified` JSONB populated with `{verifier, date, source}`
- ☐ Manifest `validation_status` flipped to verified-state string
- ☐ Per-tool README developer section updated with sign-off note
- ☐ `surface_contract.md §10` axis-3 status flipped from `in-progress` → `shipped`

**Stage gate:** every check is `☑`.

---

## Version log

| Version | Date | Change | PR | Stages touched |
|---|---|---|---|---|
| v1 | YYYY-MM-DD | Initial checklist created. | #TBD | Rails |
```

---

## 11. Links

- [`tool_lifecycle.md`](tool_lifecycle.md) — the 7 axes; §3 references this standard
- [`methodology_exposure.md`](methodology_exposure.md) — the exposure-decision protocol consumed in Stage 1A
- [`../02_components/surface_contract.md`](../02_components/surface_contract.md) — the roll-up §10 view
- [`../02_components/primitive/README.md`](../02_components/primitive/README.md) — PR1–PR16 (the contract Stages 1–2 enforce)
- [`../02_components/frontend_module/README.md`](../02_components/frontend_module/README.md) — FM1–FM12 (Stage 4 contract)
- [ADR 0015](../05_decisions/0015-tool-metadata-db-table.md) — `source_material_verified` JSONB shape (Stage 3 + Stage 8)

---

## 12. Version log (this standard, not the per-tool checklists)

| Version | Date | Change |
|---|---|---|
| v1 | 2026-05-28 | Initial standard.  Defined the 8-stage assembly line (Backend spec → Backend tests → DB metadata → Frontend module → Frontend bridge endpoint → Frontend surfaces → Mirrors → Closeout sign-off), the 4-status convention (☐ ☑ ⏸ ⊘), the `pending` treatment for source-material verification (DB stays NULL; checklist tracks intent), the 7-axis ↔ 8-stage mapping, and the canonical per-tool template in §10.  Per `revamp` Phase-1 pilot adoption. |
| v1.1 | 2026-05-28 | Added explicit Stage 1C reviewer check: manifest `one_liner` MUST be PM-facing prose, not engineer-facing.  Surfaced after the `get_real_yield_level_tool` Phase-1 pilot landed with a technical one_liner that read like a commit message ("Linker analogue of get_yield_levels...") — the Library card showed it verbatim to users.  Rewriting the one_liner is a 10-min fix; preventing the pattern from recurring is what the new reviewer check delivers.  Per-tool DB `tool_metadata.desk_narrative` field stays the source of the long-form narrative; `one_liner` is the short PM-facing tagline.  No structural change to the 8-stage assembly line or the status convention. |
