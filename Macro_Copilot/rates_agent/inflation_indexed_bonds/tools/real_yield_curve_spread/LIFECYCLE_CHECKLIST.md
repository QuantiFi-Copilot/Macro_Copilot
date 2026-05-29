# LIFECYCLE CHECKLIST — `calculate_real_yield_curve_spread_tool`

> Per-tool progress tracker.  See [`docs_revamped/03_standards/lifecycle_checklist_template.md`](../../../../docs_revamped/03_standards/lifecycle_checklist_template.md) for the standard.
> Roll-up row: [`docs_revamped/02_components/surface_contract.md`](../../../../docs_revamped/02_components/surface_contract.md) §10.

**Tool:** `calculate_real_yield_curve_spread_tool`
**Domain:** `inflation_indexed_bonds`
**Started:** 2026-05-28
**Current stage:** Stage 8 (pending source-material verification + Stage-3 DB apply)
**Compliance posture:** YES from day one (Stage-C Phase-1 pilot; brought to parity with `get_real_yield_level_tool`)

---

## Stage 1 — Backend specification  ☑ closed
- Closed in commit: (this PR — Stage B/C)
- Closed by: Stage-C implementation
- Date: 2026-05-28

### 1A. Convention exposure decisions (per [`methodology_exposure.md`](../../../../docs_revamped/03_standards/methodology_exposure.md))

| Convention | Decision | Rationale (one line) |
|---|---|---|
| `z_score_window_days` | ☑ expose | Criterion A+B pass; applies to the spread's own z + buffer. |
| `z_score_min_periods` | ☑ expose | Criterion A+B pass; scales with the window. |
| `z_score_ddof` | ☑ expose | Criterion A+B pass; sample vs population std. |
| `default_field_name` | ☑ expose | Via existing `field_name`; flows to both endpoints. |
| `z_score_buffer_multiplier` | ☑ keep-yaml | Fetch-window math. |
| `daily_change_offset_rows` | ☑ keep-yaml | Desk-language anchor wire-encoded. |
| `weekly_change_offset_rows` | ☑ keep-yaml | Same. |
| `monthly_change_offset_rows` | ☑ keep-yaml | Same. |
| `trailing_range_window_days` | ☑ keep-yaml | WIRE-FROZEN (252 embedded in high_252d_pct etc.). |
| `ffill_limit_days` | ☑ keep-yaml | Borderline data-hygiene inside composed level primitive. |
| `yield_round_decimals` | ☑ keep-yaml | Display precision. |
| `bps_round_decimals` | ☑ keep-yaml | Display precision. |
| `z_score_round_decimals` | ☑ keep-yaml | Display precision. |
| `window_years_round_decimals` | ☑ keep-yaml | Display precision (year-fraction fields). |
| `high_low_round_decimals` | ☑ keep-yaml | Display precision (threaded into the level primitive). |

### 1B. Pydantic Input + MCP wrapper
- ☑ `RealYieldCurveSpreadInput` carries the 3 z-score override fields
- ☑ MCP wrapper `calculate_real_yield_curve_spread_tool` signature matches (integer sentinels 0/0/-1)
- ☑ MCP wrapper docstring covers each new override (incl. the "applies to the spread's own z" note)

### 1C. Manifest mirror
- ☑ Manifest `pm_overridable` derived from the `expose: true` set
- ☑ Manifest `references` mirrors DB `theoretical_reference`
- ☑ Manifest `one_liner` rewritten PM-facing (dropped the engineer-facing "analogue of calculate_curve_spread")

### 1D. Standalone bridge contract
- ☑ `MODULE.typedView: null`
- ☑ Typed-detail endpoint planned at `/api/v1/rates/detail/real_yield_curve_spread` (delivered Stage 5)

---

## Stage 2 — Backend tests  ☑ closed
- Closed in commit: (this PR — Stage B/C) · Date: 2026-05-28

- ☑ `tests/test_real_yield_curve_spread_compute.py` passes (incl. `TestInputOverrides` + `TestExposureBlockContract`)
- ⊘ dedicated `_wiring.py` z-override coverage — wrapper requires the `mcp` package (env-dependent; same carve-out as `get_real_yield_level_tool`)
- ☑ `tests/test_real_yield_curve_spread_parity.py` passes (DEFAULT behaviour unchanged)
- ☑ `tests/test_real_yield_curve_spread_sql_validation.py` exists (live-DB CLI runner)
- ☑ `TestConventionOverrides` + `TestInputOverrides` cover every `expose: true` convention
- ☑ `python -m shared.config.lint` passes
- ☑ Parity fixtures NOT regenerated (no DEFAULT methodology value changed)

---

## Stage 3 — DB metadata  ⏸ migration written; live-DB apply pending
- ☑ Migration file `database/migrations/2026-05-28_phase1_calculate_real_yield_curve_spread_curated.sql` checked in (idempotent UPDATE + INSERT fallback)
- ☑ `theoretical_reference` curated in the migration
- ☑ `known_limitations` curated in the migration
- ☑ `desk_narrative` curated in the migration
- ☑ `output_field_units` JSONB defined (`{"time_series_spread": "percent", "time_series_zscore": "z_score"}`)
- ⏸ **DB apply pending** — live DB off-limits for automated application in this PR
- ⏸ `source_material_verified` — tracked in Stage 8

---

## Stage 4 — Frontend module spec  ☑ closed
- Closed in commit: (this PR — Stage B/C) · Date: 2026-05-28

- ☑ `module.ts` declares `tiers: [generic_runnable, custom_build_surface, monitor_surface]`
- ☑ `THESIS.md` Q1 enumerates extended + compact (+ monitor); Q2 per-surface read; Q3 justifies the compact metrics
- ☑ `MODULE.surfaces.buildExtended` populated
- ☑ `MODULE.surfaces.buildCompact` populated
- ☑ `__tests__/module.spec.ts` exercises `assertStandardModuleInvariants` + asserts both surface fields + files
- ☑ Loader entry present in `src/modules/index.ts` (alphabetical)
- ☑ `MODULE.defaultParams` mirror Stage 1A exposure decisions
- ☑ `MODULE.typedView` is `null`

---

## Stage 5 — Frontend bridge endpoint  ☑ closed
- Closed in commit: (this PR — Stage B/C) · Date: 2026-05-28

- ☑ Typed-detail route mounted at `/api/v1/rates/detail/real_yield_curve_spread`
- ☑ Route in `api/routes/rates/detail.py` (`real_yield_curve_spread_detail`)
- ☑ Route receives the same params the Pydantic Input expects (incl. 3 z-overrides)
- ☑ Route returns `RealYieldCurveSpreadOutput.model_dump()` shape (response_model)
- ⊘ Integration test in `tests/api/routes/rates/detail/test_real_yield_curve_spread.py` — deferred; `fastapi` not installed in the working env
- ☑ Frontend service helper `fetchDetailRealYieldCurveSpread` added to `src/services/ratesApi.ts`
- ☑ Frontend type `RealYieldCurveSpreadOutput` added to `src/types/rates.ts`

---

## Stage 6 — Frontend surfaces  ☑ closed
- Closed in commit: (this PR — Stage B/C) · Date: 2026-05-28

### 6A. Build dual-view
- ☑ `surfaces/BuildExtended.tsx` — full canvas (separate short/long tenor dropdowns with the long-tenor filtered to enforce long > short; signed 5-zone `ZScoreRegimeSlider` card; Advanced z-overrides)
- ☑ `surfaces/BuildCompact.tsx` — grid card; 3 headline KPIs + sparkline + curve-shape caveat + curve-family chip + expand affordance + tone cues; size-aware
- ☑ Compact view does NOT carry the controls strip
- ☑ Compact view does NOT mount its own modal (calls `onExpand`)

### 6B. Other surfaces
- ☑ `surfaces/monitor/RealYieldCurveSpreadWidget.tsx` — Monitor tile
- ☑ `MODULE.monitorWidgets[]` declares the widget (curve_family / short_tenor / long_tenor / lookback param fields)
- ⊘ `surfaces/AskCard.tsx` — `ask_surface` not claimed (FM4 parsimony)

### 6C. Tests + smoke
- ☑ `npm run test:modules` clean (round-trip + dual-view contract)
- ☑ `npm run test:build` clean
- ☑ `npm run typecheck` — no NEW errors in the new files
- ⏸ Manual smoke (single-tool extended; multi-tool DAG compact; expand-to-modal; live-DB run; Monitor add-widget) — requires a running UI + live API; deferred to a manual QA pass

---

## Stage 7 — Mirrors + contracts  ☑ closed
- Closed in commit: (this PR — Stage B/C) · Date: 2026-05-28

- ☑ `surface_contract.md` §4 row updated (build/monitor status)
- ☑ `surface_contract.md` §10 row reflects current axis statuses
- ☑ `surface_contract.md` §11 changelog appended
- ☑ Per-tool `README.md` created per [`tool_lifecycle.md §4`](../../../../docs_revamped/03_standards/tool_lifecycle.md)
- ☑ Manifest `validation_status` set to "Stages 1–7 implemented; Stage 3 DB apply + source-material verification pending"
- ☑ `graphify update .` run to refresh the knowledge graph

---

## Stage 8 — Closeout sign-off  ☐ open

- ⏸ **Source-material verification**
  - Assigned to: TBD
  - Target date: TBD
  - Source: Tuckman 4e Ch. 22 (Inflation-Indexed Bonds) + Ch. 5 (term structure) + per-country primary issuer documentation
  - Notes: PM to verify the real-yield-curve-shape framing + the long − short construction.
- ⏸ DB `source_material_verified` JSONB populated with `{verifier, date, source}` (blocked on the Stage-3 DB apply too)
- ☐ Manifest `validation_status` flipped to verified-state string
- ☐ Per-tool README developer section updated with sign-off note
- ☐ `surface_contract.md` §10 axis-3 status flipped from `in-progress` → `shipped`

---

## Version log

| Version | Date | Change | PR | Stages touched |
|---|---|---|---|---|
| v1 | 2026-05-28 | Initial checklist + full Stage 1–7 implementation (Stage-C Phase-1 pilot; parity with `get_real_yield_level_tool`).  Stage 3 migration written (not applied); Stage 8 pending. | #TBD | 1–7 |
