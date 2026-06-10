# THESIS — `calculate_butterfly_tool`

> Migration dispatch — converted from the legacy typed-renderer pattern (`typedView: 'butterfly'` + `surfaces/ResultRenderer.tsx`) to the new dual-view + standalone-bridge contract.  Brought to full parity with `calculate_ois_butterfly_tool` (single-curve 3-leg fly sibling) and `calculate_real_yield_butterfly_tool`: ships an extended Build view (full canvas for single-tool queries) AND a compact Build view (grid card for multi-tool query DAGs) — both REQUIRED per [`rendering_density.md`](../../../../../docs_revamped/03_standards/rendering_density.md) §1.

**Version:** v3 (migration dispatch — dual-view + standalone-bridge under MIGRATION_RULES)
**Module spec:** [`module.ts`](module.ts)
**Backend artifact:** `calculate_butterfly_tool` (`manifest_typed_view` — NOT in `_PRIMITIVE_SPECS`; standalone bridge via `/api/v1/rates/detail/butterfly`)
**Tier set:** `[manifest_typed_view, custom_build_surface]` — PRESERVED from pre-migration (backend `_PRIMITIVE_SPECS` membership is unchanged; per MIGRATION_RULES §8 do NOT swap to `generic_runnable`)
**Backend sub-agent:** `sovereign_bonds` · **Category:** `curve_shape`
**Mockups:** [`./mockups/Compact.png`](./mockups/Compact.png) + [`./mockups/Extended.png`](./mockups/Extended.png) — committed alongside the module per the mockup-first workflow.

---

## 1. What surfaces does this module ship?

- **`manifest_typed_view`** — runtime-status tier.  Backend is in `_MANIFEST_ONLY_BUILD_TOOLS`, NOT in `_PRIMITIVE_SPECS`, so the workflow `/run` bridge cannot dispatch this tool.  Migration is PURELY about replacing the legacy typed-view renderer with the standalone dual-view; the runtime tier mirrors backend reality unchanged.
- **`custom_build_surface`** — the Phase-1 **dual-view** contract:
  - **Extended Build view** ([`surfaces/BuildExtended.tsx`](surfaces/BuildExtended.tsx)) — full canvas mounted for single-tool queries.  Controls strip (single **Sovereign Curve** dropdown + single **Triplet** dropdown + lookback + field), top-right Z-score / Percentile / Sovereign-Curve cards (the third names the sovereign identity inline — e.g. *"UST · US Treasuries"* + the `Sovereign fly; cross-check OIS fly for swap-spread component.` caveat), a bps-scale 9-cell KPI strip (BUTTERFLY / 1D / 5D / 1M / Z / PERCENTILE / 252D HIGH / 252D LOW / OBSERVATIONS) plus a decomposition row (three endpoint yields in PERCENT + two component wing spreads in BPS), butterfly-history chart with ±2σ z-score bands, stretch-context panel, methodology card sourced from the construction formula + the sign convention + the sovereign identity, lineage footer.  5D / 1M changes + observation count are re-derived client-side from `time_series_butterfly` (the wire is leaner than the linker / ZCIS butterfly siblings).
  - **Compact Build view** ([`surfaces/BuildCompact.tsx`](surfaces/BuildCompact.tsx)) — grid card mounted as a node body inside multi-tool query DAGs (e.g. *"compare UST 2s5s10s fly vs Bund 2s5s10s fly"*).  Headline 3-KPI strip (FLY (bps) / 1D CHANGE (bps) / Z-SCORE (252D) — 1D change carries a percent-of-belly subtext per the mockup), mini-chart with ±2σ / ±1.5σ z-score bands, the sovereign-vs-OIS caveat footer + sovereign-family chip, click-to-expand affordance.

---

## 2. What does the user read off each surface?

### Extended Build view

The full investigation canvas for ONE same-curve sovereign butterfly.  A PM reads, in order: (a) the title row identifying the sovereign + triplet (e.g. *"UST 2-5-10 BUTTERFLY · UST"*) + as-of date; (b) the top-right Z-Score / Percentile / Sovereign-Curve cards (the last names the resolved sovereign — e.g. *"UST · US Treasuries"* — and carries the *"Sovereign fly; cross-check OIS fly for swap-spread component."* caveat inline); (c) the bps-scale 9-cell KPI strip — current butterfly (bps directly from the backend) with belly-rich / belly-cheap caption, 1d change (bps), 5d change (bps, client-derived), 1m change (bps, client-derived), z-score with regime+direction caption, percentile, 252d high/low (bps), observation count; (d) the decomposition row — three endpoint yields (`short_tenor_yield`, `belly_tenor_yield`, `long_tenor_yield` in %) + two wing spreads (`wing_short_bps`, `wing_long_bps` in bps) so the desk can audit `(2 × belly − short − long) × 100` on the same screen; (e) the butterfly-history chart with z-score band overlays; (f) the stretch-context panel; (g) the methodology card (construction formula, sign convention, triplet with butterfly_label, field, z-model, the sovereign-vs-OIS caveat, sovereign identity, weighting choice, same-curve invariant); (h) the lineage footer.  The single **Sovereign Curve** dropdown picks the curve_family (single-curve primitive — no nominal counterparty); the single **Triplet** dropdown enforces all-three-distinct ordering by only offering registered triplets per curve.

### Compact Build view

The at-a-glance grid card.  A PM sees this in a multi-tool prompt.  They read THREE numbers + a sparkline: FLY (bps) (signed bps directly from the backend, with Belly Rich / Belly Cheap caption — the canonical desk read), 1-day change (bps, tone-coloured, with a `(±X.XX%)` subtext expressing the daily move as a percent of current belly yield, mockup-faithful), rolling 252d z-score (with regime+direction caption — Neutral / Elevated Cheap / Extreme Rich / etc).  The mini-chart shows the butterfly history with ±2σ / ±1.5σ z-score bands.  The footer carries the *"Sovereign fly; cross-check OIS fly for swap-spread component."* caveat + the sovereign chip (e.g. *"🇺🇸 UST"*).  The expand arrow opens the extended view in a modal.

---

## 3. Why these surfaces and not others?

**Why the dual-view contract** (both extended + compact REQUIRED): per [`rendering_density.md §1.1`](../../../../../docs_revamped/03_standards/rendering_density.md) multi-tool prompts are the default reality; a tool that ships only an extended view falls back to a generic artifact-type card in multi-tool DAGs.  Sovereign butterflies are commonly compared across sovereigns (*"UST 2s5s10s vs Bund 2s5s10s"*) and against the OIS butterfly on the same triplet (to isolate the swap-spread component), so the compact view earns its keep immediately.  The standard OVERRIDES FM4 parsimony for the dual-view mandate.

**Why these three compact KPIs** (FLY (bps) + 1D CHANGE (bps) + Z-SCORE (252D)): they are the desk-canonical "first three numbers" a PM reads off a sovereign butterfly snapshot — *"where is the curvature now / how much did it move today / is this stretched?"*.  Alternatives considered + rejected: belly yield outright (the curvature object IS the snapshot — surfacing the belly yield instead would hide the construction); 252d percentile (already conveyed by the z-score regime + band overlay); the two wing spreads (decomposition detail, surfaced in the extended view's decomposition row, not headline).  The 1D-change cell carries a `(±X.XX%)` subtext per the mockup so the desk reads the move both in absolute bps AND in proportion to the current belly yield.

**Why a SINGLE Sovereign-Curve dropdown** (not a leg pair): this primitive is single-curve by construction — the Pydantic Input takes one `curve_family` and three tenors, with the same-curve invariant enforced at the input layer (the cross-curve case composes on `calculate_cross_market_spread_tool`).  Surfacing a leg-pair UI would invite invalid input shapes; the single dropdown honours the actual contract.

**Why a SINGLE Triplet dropdown** (not three independent tenor dropdowns): the schema's `_tenors_must_all_differ` validator rejects duplicate or repeated triplets at the input layer.  Surfacing three independent dropdowns would invite invalid orderings; the single dropdown of registered triplet presets (per curve) makes invalid orderings unreachable.

**Why the wire already ships BPS (and we don't unit-convert)**: the sovereign sub-domain established the BPS convention for curve-shape views via `calculate_curve_spread` / `calculate_cross_market_spread`; the butterfly inherits it.  The wire ships `current_butterfly_bps`, `daily_change_bps`, `high_252d_bps`, `low_252d_bps`, `wing_short_bps`, `wing_long_bps` all in BPS directly.  The display layer renders BPS unchanged.  Only the three per-leg endpoint yields (`short_tenor_yield`, `belly_tenor_yield`, `long_tenor_yield`) stay in PERCENT — that's the natural unit for a sovereign yield level.

**Why 5D / 1M changes are client-derived** (not on the wire): the sovereign butterfly backend Output ships only `daily_change_bps`; weekly / monthly changes are NOT in the Pydantic schema.  Rather than inventing wire data, the BuildExtended view re-derives them client-side from `time_series_butterfly.rows` (same pattern as `calculate_curve_spread_tool`).  This honours the mockup's full KPI strip without growing the backend Output.

**Why the sovereign-vs-OIS caveat is REQUIRED** (not hidden): misreading a sovereign butterfly as a pure curvature signal is a real desk error.  Sovereign curves carry a sovereign-vs-OIS basis (FRA-OIS, asset-swap, repo specialness) that an OIS butterfly does not; reading the sovereign fly in isolation misses the swap-spread component of the move.  Per `rendering_density.md §2.2` + §12 the methodology caveat must be reachable in the compact view; here it's the canonical one-liner in the footer ("Sovereign fly; cross-check OIS fly for swap-spread component.").

**Why the methodology card sources the disclosure from a per-tool constant** (not the wire `methodology_label`): unlike the linker / ZCIS butterfly siblings, the sovereign butterfly backend Output does NOT currently carry a `current_metrics.methodology_label` field — the sovereign sub-domain hasn't caught up to the PR10 wire-honesty-disclosure convention yet.  The methodology card surfaces the canonical sovereign-vs-OIS caveat from `butterflyShared.ts` with an explicit `TODO(PR10)` marker; when the backend ships the field, the "Disclosure" row switches to consume the wire (one-line edit).  All OTHER methodology rows (construction formula, sign convention, weighting choice, same-curve invariant, z-window, trailing range, sovereign identity) source from the wire payload + the request context directly.

**Why no z-score override controls in the extended view**: the sovereign butterfly Pydantic schema does NOT expose `z_score_window_days` / `z_score_min_periods` / `z_score_ddof` as input fields — they fall through to the YAML at every call site (consistent with the curve_spread / OIS / linker butterfly siblings).  Surfacing them as controls would silently shadow the YAML; the extended view honours the actual contract instead.

**Why `manifest_typed_view` is preserved** (not swapped to `generic_runnable`): the backend's `_PRIMITIVE_SPECS` dict does NOT contain `calculate_butterfly_tool`; the tool is a `_MANIFEST_ONLY_BUILD_TOOLS` entry.  Per MIGRATION_RULES §4 step 6 + §8 anti-patterns the tier set must mirror backend reality.  The runtime tier reflects the dispatch reality; the dual-view + standalone-bridge IS the live surface, and `unsupportedReason.whatWorksNow` is updated to reflect that.

**Why a typed-detail endpoint** (`/api/v1/rates/detail/butterfly`): per [`methodology_exposure.md §5`](../../../../../docs_revamped/03_standards/methodology_exposure.md) every standalone module ships its own typed bridge; the generic `/run` route stays the LLM-facing surface.  The existing route + service helper + TS type are REUSED — no parallel central files added.

---

## 4. What would change the design?

- **The backend adds `calculate_butterfly_tool` to `_PRIMITIVE_SPECS`** → promote the runtime tier from `manifest_typed_view` → `generic_runnable`, drop the `unsupportedReason` block, no surface changes needed.
- **The backend ships `current_metrics.methodology_label`** on the sovereign butterfly Output (PR10 catch-up) → the methodology card's "Disclosure" row switches to source from the wire (one-line edit in `butterflyShared.ts:buildMethodologyRows`).
- **Desk demand for a Monitor bento tile** → claim a runtime Monitor tier and ship a widget mirroring the OIS butterfly's `OisButterflyWidget` shape.  The pre-migration module did NOT claim the Monitor capability tier and the design_guardrails for this migration explicitly forbid adding one; this triggers a separate (non-migration) sprint if/when desk demand justifies.
- **A DV01-weighted / duration-neutral sovereign butterfly primitive** lands → it ships as a SEPARATE module (different concept), not a knob on this one; the extended view could then offer a side-by-side "fixed-weight vs DV01-weight" framing.  (config.yaml's `planned_extensions` already names this — currently locked at the (-1, +2, -1) simple-butterfly weighting.)
- **The trailing range window is promoted from 252 → configurable** (config.yaml `planned_extensions` already names this) → expose `trailing_range_window_days` on the schema, add an "Advanced" control on the extended view, and update the methodology row's "Trailing range" line.

---

## 5. Which backend doctrine does this module operationalise?

- **FM1** (module identity) — folder name equals backend `tool_name`.
- **FM3** (surface-tier capability declaration) — claims `manifest_typed_view` (runtime) + `custom_build_surface` (capability).
- **FM4** (tier-set parsimony) — overridden by `rendering_density.md §1.2` for the dual-view mandate.
- **FM5** (display metadata) — `displayName` / `category` / `oneLineSummary` are PM-facing (per `lifecycle_checklist_template.md` Stage 1C).
- **FM6** (runtime-status disclosure) — `unsupportedReason` carries label + reason + whatWorksNow per the test infrastructure's invariant 8 (`src/modules/__test-utils.ts`:265).  The `whatWorksNow` copy is updated post-migration to reflect that the dual-view IS the live surface.
- **FM7** (pure-spec assembly) — `module.ts` exports a pure value.
- **FM8** (surface-file contract) — `surfaces.buildExtended` + `surfaces.buildCompact` populated; files at canonical paths.
- **FM9** (routing-claim disclosure) — `typedView: null`; this module owns its own full surfaces.
- **FM10** (THESIS discipline) — this file.
- **FM11** (round-trip test) — [`__tests__/module.spec.ts`](__tests__/module.spec.ts) asserts the dual-view contract.
- **FM12** (loader presence) — module imported in [`src/modules/index.ts`](../../index.ts).
- **MIGRATION_RULES §4** (transformation procedure) — applied verbatim: deleted `surfaces/ResultRenderer.tsx`, transformed `module.ts` (`typedView` → `null`, removed `workspaceLabel`, surfaces switched), overwrote `THESIS.md` + `__tests__/module.spec.ts`.
- **MIGRATION_RULES §6** (monitor widget identity rule) — N/A; no monitor widgets to preserve (empty array in catalog entry).
- **MIGRATION_RULES §8** (anti-patterns) — explicit compliance: `manifest_typed_view` preserved (backend membership unchanged); `unsupportedReason` preserved (required by test infrastructure for `manifest_typed_view`); central files (`src/types/rates.ts:ButterflyOutput`, `src/services/ratesApi.ts:fetchDetailButterfly`, `api/routes/rates/detail.py:/detail/butterfly`) REUSED with no parallel duplicates.
- **PR10 / P5** (methodology label sourcing) — the methodology card surfaces the wire-derivable rows (construction / sign convention / weighting / sovereign identity / z-window / trailing range) from `data.current_metrics` directly; the "Disclosure" row sources the canonical sovereign-vs-OIS caveat from the per-tool shared file with a `TODO(PR10)` marker for when the backend ships `methodology_label`.
- Per the **methodology-exposure standalone-bridge contract** (`methodology_exposure.md §5`): own typed-detail endpoint `/api/v1/rates/detail/butterfly`; own service helper `fetchDetailButterfly`; own frontend type `ButterflyOutput`.
- Per the **rendering-density dual-view contract** (`rendering_density.md §1`): both views ship + both wired in `module.ts.surfaces` + files at canonical paths.

---

## Mockup conformance

The **Compact mockup** shows `BUTTERFLY (SOVEREIGN) · SNAPSHOT` header with 🇺🇸 flag, `UST · 2-5-10 FLY` identity + `US Treasuries curve curvature` subtitle, the three KPIs (FLY (bps) `+8.4 bps` with "Belly Cheap" caption, 1D CHANGE `+1.9 bps` with `(+0.23%)` subtext, Z-SCORE (252D) `+1.84` with "Elevated Cheap" caption), a butterfly history chart with ±2σ / ±1.5σ band overlays, the *"Sovereign fly; cross-check OIS fly for swap-spread component."* footer caveat, and the `🇺🇸 UST` chip.  The implementation reproduces all of these via the shared `BuildCompactShell` + the per-tool `compactKPIs` helper.  The percent-of-belly subtext is reconstructed from `bps / (belly_tenor_yield × 100)` so the desk gets the equivalent percent move without a second tool call.

The **Extended mockup** shows `UST 2-5-10 BUTTERFLY` with top-right Z-score `+1.84` (Elevated Cheap) / Percentile `84th` cards, a bps-scale KPI strip (BUTTERFLY `+8.4 bps` / 1D `+1.9 bps` / 5D `-4.7 bps` / 1M `-19.0 bps` / Z-SCORE `+1.84` / PERCENTILE `84th` / 252D HIGH `+14.2` / 252D LOW `-11.5` / OBSERVATIONS `365`), a butterfly decomposition row, the chart with z-bands, the stretch-context panel, and the methodology card.  The implementation reproduces this structure end-to-end via the shared `BuildExtendedShell` + the per-tool `extendedKPIs` + `decompositionKPIs` + `buildMethodologyRows` helpers.  5D / 1M change + observation count are re-derived client-side from `time_series_butterfly` because the wire is leaner than the linker / ZCIS butterfly siblings.

---

## Version log

| Version | Date | Change |
|---|---|---|
| v3 | 2026-06-10 | Migration dispatch under MIGRATION_RULES.  Converted from legacy typed-renderer (`typedView: 'butterfly'` + `surfaces/ResultRenderer.tsx`) to dual-view + standalone-bridge.  Shipped `surfaces/BuildExtended.tsx`, `surfaces/BuildCompact.tsx`, `surfaces/butterflyShared.ts`.  Deleted `surfaces/ResultRenderer.tsx`.  Tier set PRESERVED at `[manifest_typed_view, custom_build_surface]` because backend `_PRIMITIVE_SPECS` membership is unchanged (per MIGRATION_RULES §8 anti-patterns).  `unsupportedReason.whatWorksNow` copy updated to reflect that the dual-view is the live surface.  Removed legacy `module.ts.typedView`, `module.ts.workspaceLabel`.  Central files (`ButterflyOutput`, `fetchDetailButterfly`, `/detail/butterfly`) REUSED; `ButterflyOutput` extended to include `time_series_butterfly` + `time_series_zscore` (Pydantic Output fields that were missing from the TS mirror). |
| v2 | 2026-05-26 | Stage 4e — rewrote Q2–Q5 to match the actual post-migration module state. |
| v1 | 2026-05-25 | Stage 3 scaffold — runtime tier only, no surfaces. |
