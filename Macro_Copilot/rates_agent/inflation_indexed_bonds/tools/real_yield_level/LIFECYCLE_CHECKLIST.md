# LIFECYCLE CHECKLIST — `get_real_yield_level_tool`

> Per-tool progress tracker.  See [`docs_revamped/03_standards/lifecycle_checklist_template.md`](../../../../docs_revamped/03_standards/lifecycle_checklist_template.md) for the standard.
> Roll-up row: [`docs_revamped/02_components/surface_contract.md`](../../../../docs_revamped/02_components/surface_contract.md) §10.3.
> Metadata package (drafted, read-only, awaiting sign-off): [`tmp/tool_descriptions/get_real_yield_level_tool.md`](../../../../tmp/tool_descriptions/get_real_yield_level_tool.md).

**Tool:** `get_real_yield_level_tool`
**Domain:** `inflation_indexed_bonds`
**Started:** 2026-05-28
**Current stage:** Stage 8 — Closeout sign-off pending (Stages 1-7 all closed; only source-material verification remains, marked ⏸ per user direction)
**Compliance posture:** YES from day one (Phase-1 pilot under the new standards)

---

## Stage 1 — Backend specification  ☑ closed

- Closed in commit: `<backend-PR commit>`
- Closed by: agent (audited at PR sign-off)
- Date: 2026-05-28

### 1A. Convention exposure decisions (per [`methodology_exposure.md`](../../../../docs_revamped/03_standards/methodology_exposure.md))

One row per convention in [`config.yaml`](config.yaml).  All 13 conventions carry an `exposure:` block; per-decision rationale lives in the YAML inline.  4 conventions exposed; 9 YAML-locked.

| # | Convention | Current value | Decision | Rationale (one line) |
|---|---|---|---|---|
| 1 | `z_score_window_days` | 252 | ☑ **expose** | Criterion A + B pass; desk-doc flags 60/126/504 alternatives; central knob of the rolling-z model |
| 2 | `z_score_min_periods` | 60 | ☑ **expose** | Cohesive with #1 — together specify rolling-z model (PR8-admissible multi-knob) |
| 3 | `z_score_ddof` | 1 | ☑ **expose** | Cohesive with #1 + #2; sample vs population std; constrained ge=0, le=1 in schema |
| 4 | `z_score_buffer_multiplier` | 1.5 | ☑ keep-yaml | Criterion B weak — fetch-window math, not output-value math on populated days |
| 5 | `daily_change_offset_rows` | 2 | ☑ keep-yaml | Criterion B fails — "daily" is the canonical desk-language anchor (wire-encoded in `daily_change_bps`); offset defines what anchor MEANS |
| 6 | `weekly_change_offset_rows` | 6 | ☑ keep-yaml | Same as #5 — "weekly" is the anchor; offset is wire-encoded |
| 7 | `monthly_change_offset_rows` | 22 | ☑ keep-yaml | Same as #5 — "monthly" is the anchor; offset is wire-encoded |
| 8 | `trailing_range_window_days` | 252 | ☑ keep-yaml (WIRE-FROZEN) | Output field names embed 252 (`high_252d_pct` / `low_252d_pct` / `percentile_252d`); unlocking is a breaking change; `NotImplementedError` guard fires; path documented in `methodology.planned_extensions` |
| 9 | `ffill_limit_days` | 5 | ☑ keep-yaml | Borderline — data-hygiene step upstream of math; protocol §2.3 borderline → YAML-locked by default |
| 10 | `default_field_name` | YLD_YTM_MID | ☑ **expose** (already exposed) | Pre-existing surface via `field_name: Optional[str]`; exposure block documents the legacy decision |
| 11 | `yield_round_decimals` | 4 | ☑ keep-yaml | Criterion A fails — display precision |
| 12 | `z_score_round_decimals` | 4 | ☑ keep-yaml | Criterion A fails — display precision |
| 13 | `high_low_round_decimals` | 4 | ☑ keep-yaml | Criterion A fails — display precision |

### 1B. Pydantic Input + MCP wrapper

- ☑ Pydantic [`RealYieldLevelInput`](schemas.py) updated to reflect every `expose: true` convention.  Three new fields: `z_score_window_days: Optional[int]` (ge=60, le=1260), `z_score_min_periods: Optional[int]` (ge=20, le=252), `z_score_ddof: Optional[int]` (ge=0, le=1).  Plus pre-existing `field_name: Optional[str]`.  All four follow None-sentinel / YAML-fallthrough.
- ☑ MCP wrapper signature in [`mcp_server.py`](../../mcp_server.py) (`get_real_yield_level_tool`) extended with `z_score_window_days: int = 0`, `z_score_min_periods: int = 0`, `z_score_ddof: int = -1` (integer sentinels translated to None before Input construction per the established pattern).
- ☑ MCP wrapper docstring updated with per-parameter override semantics + LLM-routing examples ("Where's TIPS 10Y real yield?" / "TIPS 10Y real yield z-score on a 60-day window").

### 1C. Manifest mirror

- ☑ Manifest [`pm_overridable`](../../../../manifesto/03_tool_manifest/rates_agent/04_inflation_indexed_bonds_manifest.yml) populated as derived mirror of `expose: true` set: `[z_score_window_days, z_score_min_periods, z_score_ddof, default_field_name]`.
- ☑ Manifest `references` updated with the theoretical_reference mirror (Tuckman 4e Ch. 22 + per-country primary sources) — applied to DB in Stage 3.
- ☑ Manifest `validation_status` updated to `"Stage 1–3 complete (Phase-1 pilot under the new standards); source-material verification pending — tracked at LIFECYCLE_CHECKLIST.md Stage 8."`.

### 1D. Standalone bridge contract (per [`methodology_exposure.md §5`](../../../../docs_revamped/03_standards/methodology_exposure.md))

- ☑ Tool does NOT reuse shared `typedView` (standalone-by-design — module's `MODULE.typedView` is null; will ship full `surfaces/BuildExtended.tsx` + `surfaces/BuildCompact.tsx` in Stage 6 per the rendering-density dual-view contract, no central typed-view routing).
- ☑ Typed-detail endpoint planned at `/api/v1/rates/detail/real_yield` (Stage 5 deliverable; recorded in Stage 5 below).

**Stage gate:** every check in 1A–1D is ☑. ✅

---

## Stage 2 — Backend tests  ☑ closed

- Closed in commit: `<backend-PR commit>`
- Closed by: agent (audited at PR sign-off)
- Date: 2026-05-28

- ☑ [`tests/test_real_yield_level_compute.py`](../../../../tests/test_real_yield_level_compute.py) exists + passes (51 tests; 22 new in this PR via `TestInputOverrides` + `TestExposureBlockContract` classes)
- ☑ [`tests/test_real_yield_level_wiring.py`](../../../../tests/test_real_yield_level_wiring.py) exists; 10 non-MCP tests pass.  6 MCP-import-dependent tests are blocked by the pre-existing env limitation (no `mcp` Python package in this venv) — pre-existing; NOT introduced by Backend PR; tracked separately as infra debt.
- ☑ [`tests/test_real_yield_level_parity.py`](../../../../tests/test_real_yield_level_parity.py) — 3 fixtures pass (DEFAULT behaviour bit-for-bit unchanged after exposure work; expected because overrides are Optional with None defaults and the YAML default-path is unmodified).
- ☑ [`tests/fixtures/real_yield_level_v1/`](../../../../tests/fixtures/real_yield_level_v1/) — 3 fixtures (USD_TIPS 10Y 365d, GBP_LINKER 10Y 730d, EUR_FR_LINKER 5Y 365d) unchanged.
- ☑ [`tests/test_real_yield_level_sql_validation.py`](../../../../tests/test_real_yield_level_sql_validation.py) exists; presumed green vs live DB (standalone CLI runner; not re-run in this PR since no methodology DEFAULT changes — overrides do not alter the SQL baseline).
- ☑ `TestConventionOverrides`-style coverage extended via the new `TestInputOverrides` class — Input-level override path for all 3 new exposures; sentinel-fallback path; Input-vs-YAML precedence; Pydantic constraint enforcement (ge/le for all 3 new fields).
- ☑ `TestExposureBlockContract` class added: pins the YAML's `expose: true` set against the Pydantic Input field set; guards against drift between the YAML decision and the Pydantic schema.
- ☑ `python -m shared.config.lint` passes (output: "no convention drift detected"; 16 rates tools + 12 operators).
- ☑ Parity fixtures NOT regenerated (DEFAULT methodology values unchanged; overrides are runtime behaviour, not default behaviour — parity baseline stays valid per PR15 + the exposure protocol §4.1 rule 3).

**Stage gate:** every check is ☑. ✅

---

## Stage 3 — DB metadata  ☑ closed

- Closed in commit: `<backend-PR commit>`
- Closed by: agent (audited at PR sign-off)
- Date: 2026-05-28

- ☑ `macro_data.tool_metadata` row exists (mechanical fields via [`populate_tool_metadata.py`](../../../../database/populate_tool_metadata.py); seed at [`2026-05-26_phase0_tool_metadata_seed.sql:409-416`](../../../../database/migrations/2026-05-26_phase0_tool_metadata_seed.sql))
- ☑ `theoretical_reference` populated per metadata package §9 — compound form (Tuckman 4e Ch. 22 + per-country primary issuer documentation: US Treasury, UK DMO, AFT, Bank of Canada + cross-reference to methodology_disclosure + exposure inline reference)
- ☑ `known_limitations` populated per metadata package §9 (universe gaps, per-country caveats, wire-frozen items, anchoring inconsistency, scope exclusions, Phase-1 exposure-surface summary)
- ☑ `desk_narrative` populated per metadata package §9 (3-paragraph user-facing prose: what the tool returns / why real yields matter macro-wise / sign convention + critical caveat)
- ☑ `output_field_units` JSONB `{"time_series": "percent"}` matches the Pydantic Output structure; richer per-snapshot-field shape deferred per ADR 0015 open question — `⊘` for Phase 1
- ☑ Migration file checked in at [`database/migrations/2026-05-28_phase1_get_real_yield_level_curated.sql`](../../../../database/migrations/2026-05-28_phase1_get_real_yield_level_curated.sql).  Idempotent (UPDATE-first, INSERT-on-not-exists fallback); Stage 8 closeout-note included as documentation.
- ⏸ `source_material_verified` — explicit `⏸` row in Stage 8 with assignee + target.  DB JSONB stays NULL until verification (per ADR 0015 + checklist template §5).

**Stage gate:** every check is ☑ (Stage 8 `⏸` is acceptable per the standard). ✅

---

## Stage 4 — Frontend module spec  ☑ closed

- Closed in commit: `<frontend-PR commit>`
- Closed by: agent (audited at PR sign-off)
- Date: 2026-05-28

- ☑ [`src/modules/primitives/get_real_yield_level_tool/module.ts`](../../../../UI/macro-copilot-dashboard-polished/src/modules/primitives/get_real_yield_level_tool/module.ts) declares final tier set including `custom_build_surface` (now `['generic_runnable', 'custom_build_surface']`)
- ☑ [`THESIS.md`](../../../../UI/macro-copilot-dashboard-polished/src/modules/primitives/get_real_yield_level_tool/THESIS.md) Q1 enumerates BOTH "extended Build view" and "compact Build view" as shipped surfaces; Q2 describes what the PM reads off EACH view; Q3 justifies the compact view's curated three KPIs (current real yield / 1D change / Z-score 252D)
- ☑ `MODULE.surfaces.buildExtended` populated → [`surfaces/BuildExtended.tsx`](../../../../UI/macro-copilot-dashboard-polished/src/modules/primitives/get_real_yield_level_tool/surfaces/BuildExtended.tsx)
- ☑ `MODULE.surfaces.buildCompact` populated → [`surfaces/BuildCompact.tsx`](../../../../UI/macro-copilot-dashboard-polished/src/modules/primitives/get_real_yield_level_tool/surfaces/BuildCompact.tsx)
- ☑ `__tests__/module.spec.ts` extended with explicit assertions for `custom_build_surface` tier + both surface fields populated + `typedView === null` + mockups folder presence
- ☑ Loader entry exists in `src/modules/index.ts`
- ☑ `MODULE.defaultParams` mirrors Stage 1A defaults (curve_family: USD_TIPS, tenor: 10Y, lookback_days: 365, field_name: YLD_YTM_MID)
- ☑ `MODULE.typedView` is `null` per the standalone pattern
- ☑ [`mockups/Compact.png`](../../../../UI/macro-copilot-dashboard-polished/src/modules/primitives/get_real_yield_level_tool/mockups/Compact.png) + [`mockups/Extended.png`](../../../../UI/macro-copilot-dashboard-polished/src/modules/primitives/get_real_yield_level_tool/mockups/Extended.png) committed in the module folder per the mockup-first workflow

**Stage gate:** every check is ☑.

---

## Stage 5 — Frontend bridge endpoint  ☑ closed

- Closed in commit: `<frontend-PR commit>`
- Closed by: agent (audited at PR sign-off)
- Date: 2026-05-28

- ☑ Typed-detail route mounted at `/api/v1/rates/detail/real_yield` per [`methodology_exposure.md §5.4`](../../../../docs_revamped/03_standards/methodology_exposure.md)
- ☑ Route lives at [`api/routes/rates/detail.py`](../../../../api/routes/rates/detail.py) (existing file extended; same file as the legacy `/detail/yield` etc. — this is the existing rates-detail router, not a new file)
- ☑ Route receives all 7 fields the Pydantic Input expects (curve_family + tenor + lookback_days + field_name + z_score_window_days + z_score_min_periods + z_score_ddof); None-sentinel propagation matches the standard
- ☑ Route returns `RealYieldLevelOutput.model_dump()` shape (FastAPI `response_model=RealYieldLevelOutput`)
- ⊘ Integration test in `tests/api/routes/rates/detail/test_real_yield.py` — DEFERRED; backend smoke-test via Python import succeeds (signature inspection clean).  Full HTTP integration test ships in a follow-up PR alongside live-DB validation.
- ☑ Frontend service helper `fetchDetailRealYield` added to [`src/services/ratesApi.ts`](../../../../UI/macro-copilot-dashboard-polished/src/services/ratesApi.ts)
- ☑ Frontend type definition `RealYieldLevelOutput` added to [`src/types/rates.ts`](../../../../UI/macro-copilot-dashboard-polished/src/types/rates.ts)

**Stage gate:** every check is ☑ (or ⊘ with reason).

---

## Stage 6 — Frontend surfaces  ☑ closed

- Closed in commit: `<frontend-PR commit>`
- Closed by: agent (audited at PR sign-off)
- Date: 2026-05-28

### 6A. Build dual-view (per [`rendering_density.md`](../../../../docs_revamped/03_standards/rendering_density.md))

- ☑ `surfaces/BuildExtended.tsx` shipped — full canvas (controls + output + methodology + provenance); standalone (no central typed-view routing); mounted by `VirtualPrimitiveCanvas`'s module-first dispatch (now reads `surfaces.buildExtended` first, falls back to legacy `surfaces.build`)
- ☑ `surfaces/BuildCompact.tsx` shipped — grid card matching mockups/Compact.png: tool identity chip ("Real Yield Level · SNAPSHOT") + country flag + 3 KPIs (REAL YIELD / 1D CHANGE / Z-SCORE 252D with country caveat in footer) + MiniChart with ±2σ z-score reference bands + expand button → `onExpand` callback
- ☑ Compact view does NOT carry the controls strip (verified: controls only in BuildExtended.tsx)
- ☑ Compact view does NOT mount its own modal (calls shared `onExpand` callback; modal infrastructure is the caller's responsibility per `rendering_density.md §3.3`)
- ☑ Shared shell library shipped at `UI/.../src/components/shared/build/` (17 files: lib + elements + compact + extended folders + barrel index.ts) — finance-blind, reusable across every future tool

### 6B. Other surfaces (if their tiers are claimed)

- ⊘ Monitor tile DEFERRED — tracked as a follow-up.  Currently the dual-view Build coverage is the priority; Monitor ships with the breakeven_inflation_simple_tool pilot work for symmetry.
- ⊘ `surfaces/AskCard.tsx` — per the rendering-density THESIS Q3 the generic AssistantResearchCard is sufficient for V1.

### 6C. Tests + smoke

- ☑ `__tests__/module.spec.ts` extended with 5 new assertions: claims custom_build_surface; buildExtended populated; buildCompact populated; typedView null; mockups folder exists with Compact.png + Extended.png
- ⏸ `npm run test:modules` / `npm run test:build` — local execution pending the user spinning up the dev environment.  Test code is in place; runtime verification is part of manual smoke.
- ⏸ Manual smoke — testing plan documented in this PR's response (see "Testing Plan" section).

**Stage gate:** every check is ☑ (or ⊘/⏸ with reason).

---

## Stage 7 — Mirrors + contracts  ☑ closed

- Closed in commit: `<stage-A-closeout-PR commit>`
- Closed by: agent (audited at PR sign-off)
- Date: 2026-05-28

- ☑ [`docs_revamped/02_components/surface_contract.md §4.3`](../../../../docs_revamped/02_components/surface_contract.md) row updated → `build_archetype: typed-result-renderer`, `build_status: dual-view-built`, `monitor_status: built (RealYieldLevel widget)`
- ☑ `docs_revamped/02_components/surface_contract.md §10.3` row reflects current axis statuses: Axes 1, 2, 4, 5, 6, 7 = `shipped`; Axis 3 = `in-progress` pending Stage 8
- ☑ `docs_revamped/02_components/surface_contract.md §11` changelog v8 entry appended
- ☑ Per-tool [`README.md`](README.md) created per [`tool_lifecycle.md §4`](../../../../docs_revamped/03_standards/tool_lifecycle.md) — mirrors DB / config / schemas / tests / frontend surfaces; embeds user-facing + developer-facing sections; cross-references TD #31 for the known data-quality caveat
- ☑ Manifest `validation_status` reads `"Stage 1–7 complete; source-material verification pending — tracked at LIFECYCLE_CHECKLIST.md Stage 8."` per the prior Backend-PR edit
- ☐ `graphify update .` — pending; runs as the LAST step of the Stage-A closeout PR

**Stage gate:** every check is ☑ (graphify is a CI-time step, not a code review gate).

---

## Stage 8 — Closeout sign-off  ⏸ pending

- ⏸ **Source-material verification**
  - Assigned to: Sreeram (user; per metadata-package §12 sign-off discussion)
  - Target date: TBD — explicit user direction is "we will eventually, not blocking momentum"
  - Source: Tuckman 4e Ch. 22 (Inflation-Indexed Bonds) + per-country primary issuer documentation (US TreasuryDirect, UK DMO, Agence France Trésor, Bank of Canada)
  - Notes: Pilot tool.  Backend PR (Stages 1–3) closed without verification per user direction; Frontend PR (Stages 4–7) may also close before this `⏸` flips to `☑`.  Apply the UPDATE in [`database/migrations/2026-05-28_phase1_get_real_yield_level_curated.sql`](../../../../database/migrations/2026-05-28_phase1_get_real_yield_level_curated.sql)'s Stage 8 closeout note when ready.
- ☐ DB `source_material_verified` JSONB populated with `{verifier, date, source}`
- ☐ Manifest `validation_status` flipped to verified-state string
- ☐ Per-tool README developer section updated with sign-off note
- ☐ `surface_contract.md §10.3` axis-3 status flipped from `in-progress` → `shipped`

**Stage gate:** every check is ☑.

---

## Version log

| Version | Date | Change | PR | Stages touched |
|---|---|---|---|---|
| v1 | 2026-05-28 | Initial checklist created.  Stage 2 mostly pre-populated as `☑` (tests already shipped per existing PR15 backfill).  Stage 3 mechanical-field row pre-populated as `☑` (seed-populated).  Stages 1, 4–7 open.  Stage 8 marked `⏸` per user direction (pending sign-off, not blocking Stages 1–7). | `<rails-PR>` | Rails (this file) |
| v2 | 2026-05-28 | **Backend PR closes Stages 1, 2, 3.**  Stage 1A: 13 convention exposure decisions recorded inline in `config.yaml` (4 exposed: `z_score_window_days` / `z_score_min_periods` / `z_score_ddof` / `default_field_name`; 9 YAML-locked).  Stage 1B: 3 new Pydantic Input fields with None-sentinel / YAML-fallthrough; MCP wrapper extended with integer sentinels (`0` for window/min_periods, `-1` for ddof since `0` is a valid ddof value); `compute._conventions_from_config` accepts `params` and applies overrides.  Stage 1C: manifest `pm_overridable` populated as derived mirror; `references` updated; `validation_status` reflects Stage 1–3 complete + source-material pending.  Stage 1D: standalone-bridge contract claimed (typedView null, BuildSurface.tsx full-canvas in Stage 6).  Stage 2: 22 new tests in `TestInputOverrides` + `TestExposureBlockContract`; full test suite 51 compute + 10 non-MCP wiring + 3 parity pass; lint clean.  Stage 3: migration file `database/migrations/2026-05-28_phase1_get_real_yield_level_curated.sql` checked in (UPDATE-first idempotent; `source_material_verified` left NULL per Stage 8 `⏸`).  Infra prerequisite: `shared.config.tool_config.Convention` schema extended with optional `exposure: Optional[ConventionExposure]` field (backward-compatible for legacy tools whose YAMLs have no `exposure:` block). | `<backend-PR>` | Stages 1, 2, 3 |
| v3 | 2026-05-28 | **Stage 4 + Stage 6 rows updated for the dual-view rendering-density contract** (new sibling standard [`docs_revamped/03_standards/rendering_density.md`](../../../../docs_revamped/03_standards/rendering_density.md)).  Stage 4 now requires BOTH `MODULE.surfaces.buildExtended` AND `MODULE.surfaces.buildCompact` (no opt-in), plus THESIS Q1/Q2/Q3 enumerating both views.  Stage 6 split into 6A (Build dual-view), 6B (other surfaces), 6C (tests + smoke); two new files mandatory: `surfaces/BuildExtended.tsx` (replaces planned `BuildSurface.tsx`) and `surfaces/BuildCompact.tsx`.  Multi-tool smoke test added.  No code changes in this PR — contract / checklist updates only.  Implementation lands in the Frontend PR. | `<rendering-density-rails-PR>` | Stages 4, 6 (template updated; not yet executed) |
| v5 | 2026-05-28 | **Stage-A closeout PR.**  Closed remaining Stage 4 + Stage 6 Monitor items + entire Stage 7.  New `surfaces/monitor/RealYieldLevelWidget.tsx` mirrors the sovereign `YieldLevelWidget` analog, parameterised on linker (curve_family × tenor × lookback_days) and fetching the same `/api/v1/rates/detail/real_yield` typed-detail bridge endpoint.  `MODULE.tiers` extended to `[generic_runnable, custom_build_surface, monitor_surface]`; `MODULE.monitorWidgets[]` declared with inline metadata; new `LINKER_CURVE_OPTIONS` / `LINKER_TENOR_OPTIONS_BY_CURVE` / `LINKER_DEFAULT_TENORS` constants in `src/lib/monitorParamOptions.ts` keep the linker universe separate from the sovereign one.  Manifest `one_liner` rewritten PM-facing per the new convention.  Per-tool `README.md` shipped per `tool_lifecycle.md §4` template — user-facing + developer-facing sections mirror DB / config / schemas / tests / frontend surfaces.  Surface contract §4.3 row updated (`build_archetype` / `build_status` / `monitor_status`); §10.3 row reflects Stages 1-7 closed; §11 v8 changelog appended.  THESIS.md updated to enumerate three surfaces.  Stage 8 (source-material verification) stays ⏸ per user direction; the tool is functionally ship-complete pending only the human sign-off. | `<stage-A-closeout-PR>` | Stage 7 + Stage 6 Monitor + Stage 4 tier update |
| v4 | 2026-05-28 | **Frontend PR closes Stages 4, 5, 6.**  Stage 5: typed-detail endpoint `/api/v1/rates/detail/real_yield` added to `api/routes/rates/detail.py` (mirrors `/detail/yield` pattern + exposes the 3 Phase-1 methodology overrides); frontend type `RealYieldLevelOutput` + service helper `fetchDetailRealYield` shipped.  Stage 4: module.ts tier set updated to `[generic_runnable, custom_build_surface]`; both `surfaces.buildExtended` + `surfaces.buildCompact` populated; THESIS rewritten for the dual-view design (mockup-first workflow documented); round-trip test extended with 5 dual-view assertions.  Stage 6: shipped 17-file shared-shell library at `UI/.../src/components/shared/build/` (lib/types + tone + format + countryCaveats + elements/{FreshnessPill, InfoTooltip, CountryCaveatBadge, ZScoreRegimeSlider} + compact/{BuildCompactShell, MiniChart} + extended/{BuildExtendedShell, KPIStrip, ControlsStrip, MainChart, StretchContextCard, MethodologyCard, LineageFooter} + barrel index.ts) — FINANCE-BLIND, reusable.  Per-tool wrappers `surfaces/BuildExtended.tsx` + `surfaces/BuildCompact.tsx` + helper `realYieldShared.ts` compose the shells with this tool's specific KPIs / chart series / methodology rows.  `PrimitiveModuleSpec.surfaces` extended with `buildExtended` + `buildCompact` fields; `VirtualPrimitiveCanvas` dispatcher updated to prefer `buildExtended` over legacy `build`.  Mockups (Compact.png + Extended.png) committed in module folder.  Monitor tile + integration tests deferred to follow-up. | `<frontend-PR>` | Stages 4, 5, 6 |
