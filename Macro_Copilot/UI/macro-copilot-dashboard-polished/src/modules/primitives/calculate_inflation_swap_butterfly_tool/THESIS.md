# THESIS — `calculate_inflation_swap_butterfly_tool`

> Round-1 dispatch under the dual-view rendering-density contract.  Brought to full parity with `calculate_real_yield_butterfly_tool` (single-curve 3-leg fly sibling) and the sibling `calculate_cross_market_inflation_swap_spread_tool` (CPI-family caveat threading pattern): ships an extended Build view (full canvas for single-tool queries) AND a compact Build view (grid card for multi-tool query DAGs) — both REQUIRED per [`rendering_density.md`](../../../../../docs_revamped/03_standards/rendering_density.md) §1 — plus a Monitor tile.

**Version:** v3 (Round-1 dual-view + Monitor implementation)
**Module spec:** [`module.ts`](module.ts)
**Backend artifact:** `calculate_inflation_swap_butterfly_tool` (generic_runnable; standalone bridge via `/api/v1/rates/detail/zcis-butterfly`)
**Tier set:** `[generic_runnable, custom_build_surface, monitor_surface]`
**Category:** `cross_market_rv`
**Mockups:** [`./mockups/Compact.png`](./mockups/Compact.png) + [`./mockups/Extended.png`](./mockups/Extended.png) — committed alongside the module per the mockup-first workflow.

---

## 1. What surfaces does this module ship?

- **`generic_runnable`** — runtime-status tier; backend ships in `_PRIMITIVE_SPECS`.
- **`custom_build_surface`** — the Phase-1 **dual-view** contract:
  - **Extended Build view** ([`surfaces/BuildExtended.tsx`](surfaces/BuildExtended.tsx)) — full canvas mounted for single-tool queries.  Controls strip (single **ZCIS Curve** dropdown + single **Triplet** dropdown + lookback + field), top-right Z-score / Percentile / Index-Family cards (the third names the CPI-family identity inline — e.g. *"USD · CPI-U"* + the `CPI-U vs HICPxT vs RPI are not fungible` caveat), a bps-scale KPI strip plus a decomposition row (three endpoint ZCIS rates in PERCENT + two component wing spreads in BPS), butterfly-history chart with ±2σ z-score bands, stretch-context panel, methodology card sourced from the backend's `methodology_label` + the per-leg index-family metadata (`inflation_index_family` / `index_lag` / `interpolation` / `underlying_index`), lineage footer.
  - **Compact Build view** ([`surfaces/BuildCompact.tsx`](surfaces/BuildCompact.tsx)) — grid card mounted as a node body inside multi-tool query DAGs (e.g. *"compare US 2-5-10 ZCIS fly vs UK 2-5-10 ZCIS fly"*).  Headline 3-KPI strip (FLY (BPS) / 1D CHANGE / Z-SCORE — both FLY and 1D-change carry a percent-of-belly subtext per the mockup), mini-chart with z-score bands, the CPI-family caveat footer + ZCIS-family chip, click-to-expand affordance.
- **`monitor_surface`** — Monitor bento tile ([`surfaces/monitor/InflationSwapButterflyWidget.tsx`](surfaces/monitor/InflationSwapButterflyWidget.tsx)) showing one ZCIS butterfly (curve × triplet × lookback parameterised; defaults USD_ZCIS / 2s5s10s / 252).  ZCIS butterflies are a desk-canonical curve-shape RV read on the inflation-swap side; inherently compact per `rendering_density.md §8`.

---

## 2. What does the user read off each surface?

### Extended Build view

The full investigation canvas for ONE same-curve ZCIS butterfly.  A PM reads, in order: (a) the title row identifying the curve_family + triplet (e.g. *"USD_ZCIS 2-5-10 BUTTERFLY · CPI-U"*) + as-of date; (b) the top-right Z-Score / Percentile / Index-Family cards (the last names the resolved inflation index family — e.g. *"USD · CPI-U"* — and carries the *"ZCIS = zero-coupon inflation swap. CPI-U vs HICPxT vs RPI are not fungible."* caveat inline); (c) the bps-scale KPI strip — current butterfly (bps directly from the backend) with belly-rich / belly-cheap caption, 1d/5d/1m changes (bps), z-score with regime+direction caption, percentile, 252d high/low (bps), observation count; (d) the decomposition row — three endpoint ZCIS rates (`short_zcis_rate_pct`, `belly_zcis_rate_pct`, `long_zcis_rate_pct` in %) + two wing spreads (`wing_short_bps`, `wing_long_bps` in bps) so the desk can audit `(belly_zcis − 0.5 × (short + long)) × 100` on the same screen; (e) the butterfly-history chart with z-score band overlays; (f) the stretch-context panel; (g) the methodology card (construction formula, sign convention, tenors with year fractions, field, z-model, the honesty disclosure threaded from `current_metrics.methodology_label`, the per-leg index family / lag / interpolation / underlying index, same-curve invariant); (h) the lineage footer.  The single **ZCIS Curve** dropdown picks the curve_family (single-curve primitive — no nominal counterparty, no cross-curve mix); the single **Triplet** dropdown enforces strict short < belly < long ordering by only offering registered triplets per curve.

### Compact Build view

The at-a-glance grid card.  A PM sees this in a multi-tool prompt.  They read THREE numbers + a sparkline: FLY (BPS) (signed bps directly from the backend, with Belly Rich / Belly Cheap caption — the canonical desk read — and a `(±X.XX%)` subtext expressing the fly as a percent of current belly ZCIS rate, mockup-faithful), 1-day change (bps, tone-coloured, with the same percent-of-belly subtext), rolling 252d z-score (with regime+direction caption — Neutral / Elevated Cheap / Extreme Rich / etc).  The mini-chart shows the butterfly history with ±2σ / ±1.5σ z-score bands.  The footer carries the *"ZCIS = zero-coupon inflation swap. CPI-U vs HICPxT vs RPI are not fungible."* caveat + the curve-family chip (e.g. *"🇺🇸 USD ZCIS · CPI-U"*).  The expand arrow opens the extended view in a modal.

### Monitor tile

One ZCIS butterfly, desk-glanceable: current butterfly (bps with belly-rich / belly-cheap qualifier), 1-day change (bps), 252d percentile, and a high/low range strip with a current marker.  Country flag + z-score badge in the header.  The CPI-family caveat surfaces as the footer line so a glance never misreads the curvature object.

---

## 3. Why these surfaces and not others?

**Why the dual-view contract** (both extended + compact REQUIRED): per [`rendering_density.md §1.1`](../../../../../docs_revamped/03_standards/rendering_density.md) multi-tool prompts are the default reality; a tool that ships only an extended view falls back to a generic artifact-type card in multi-tool DAGs.  ZCIS butterflies are commonly compared across inflation markets (*"US 2-5-10 ZCIS fly vs UK 2-5-10 ZCIS fly"*) and across triplets (*"US 2-5-10 vs US 5-10-30 ZCIS fly"*), so the compact view earns its keep immediately.  The standard OVERRIDES FM4 parsimony for the dual-view mandate.

**Why these three compact KPIs** (FLY (BPS) + 1D CHANGE + Z-SCORE (252D)): they are the desk-canonical "first three numbers" a PM reads off a ZCIS butterfly snapshot — *"where is the curvature now / how much did it move today / is this stretched?"*.  Alternatives considered + rejected: belly ZCIS rate outright (the curvature object IS the snapshot — surfacing the belly rate instead would hide the construction); 252d percentile (already conveyed by the z-score regime + band overlay); the two wing spreads (decomposition detail, surfaced in the extended view's decomposition row, not headline).  The FLY and 1D-change cells carry a `(±X.XX%)` subtext per the mockup so the desk reads the move both in absolute bps AND in proportion to the current belly ZCIS rate.

**Why a SINGLE ZCIS-Curve dropdown** (not a leg pair): this primitive is single-curve by construction — the Pydantic Input takes one `curve_family` and three tenors, with the same-curve invariant enforced at the input layer AND re-asserted at compute() against the resolved instrument_master metadata.  Distinct from `calculate_cross_market_inflation_swap_spread_tool`, which crosses two ZCIS families at a shared tenor.  Surfacing a leg-pair UI would invite invalid input shapes; the single dropdown honours the actual contract.

**Why a SINGLE Triplet dropdown** (not three independent tenor dropdowns): the schema's `_short_belly_long_strictly_ordered` validator rejects inverted or duplicate triplets at the input layer (e.g. short='10Y', belly='5Y', long='2Y').  Surfacing three independent dropdowns would invite invalid orderings; the single dropdown of registered triplet presets (per curve) makes invalid orderings unreachable.  All three ZCIS families share the ingested 1Y / 2Y / 3Y / 5Y / 10Y / 20Y / 30Y pillar grid per `playbooks/inflation_swaps.yml`, so the registered triplets are stable across curves.

**Why the wire already ships BPS (and we don't unit-convert)**: the inflation_swaps domain established the BPS convention for curve-shape views via `inflation_swap_curve_spread`; the butterfly inherits it.  The wire ships `current_butterfly_bps`, `daily_change_bps`, `high_252d_bps`, `wing_short_bps`, `wing_long_bps` all in BPS directly.  The display layer renders BPS unchanged.  Only the three per-leg endpoint rates (`short_zcis_rate_pct`, `belly_zcis_rate_pct`, `long_zcis_rate_pct`) stay in PERCENT — that's the natural unit for an inflation-swap rate level (not a spread or curvature).  This is a key difference from the linker `real_yield_butterfly` sibling, whose wire ships PERCENT and whose display layer multiplies by 100 for the BPS presentation.

**Why the CPI-family caveat is REQUIRED** (not hidden): misreading a USD ZCIS butterfly as a generic "inflation curvature" — fungible with a EUR_ZCIS or GBP_ZCIS butterfly — is a real desk error.  The three families reference DIFFERENT inflation indices (US CPI-U / Eurozone HICPxT / UK RPI), so a 2-5-10 fly on USD_ZCIS is a curvature of CPI-U term-structure expectations and ONLY that.  Per `rendering_density.md §2.2` + §12 the methodology caveat must be reachable in the compact view; here it's the canonical one-liner in the footer ("ZCIS = zero-coupon inflation swap. CPI-U vs HICPxT vs RPI are not fungible."), and the full honesty disclosure threads onto the wire via `current_metrics.methodology_label` (sourced from `config.yaml:methodology.what_it_does`, not hardcoded).  The methodology card additionally surfaces the resolved `inflation_index_family` / `index_lag` / `interpolation` / `underlying_index` from the wire so the desk can confirm the curve identity end-to-end.

**Why no z-score override controls in the extended view**: unlike the spot ZCIS-rate level primitive, the inflation_swap_butterfly Pydantic schema does NOT expose `z_score_window_days` / `z_score_min_periods` / `z_score_ddof` as input fields — they fall through to the YAML at every call site (consistent with the breakeven-butterfly and real-yield-butterfly siblings).  Surfacing them as controls would silently shadow the YAML; the extended view honours the actual contract instead.

**Why a typed-detail endpoint** (`/api/v1/rates/detail/zcis-butterfly`): per [`methodology_exposure.md §5`](../../../../../docs_revamped/03_standards/methodology_exposure.md) every standalone module ships its own typed bridge; the generic `/run` route stays the LLM-facing surface.

---

## 4. What would change the design?

- **A DV01-weighted / duration-neutral ZCIS butterfly primitive** lands → it ships as a SEPARATE module (different concept), not a knob on this one; the extended view could then offer a side-by-side "fixed-weight vs DV01-weight" framing.
- **A new ZCIS market** (e.g. JPY_ZCIS, CAD_ZCIS) with a complete pillar grid lands → add the curve to `FAMILY_REGISTRY` + `INFLATION_SWAP_BUTTERFLY_TRIPLETS_BY_CURVE` in [`surfaces/inflationSwapButterflyShared.ts`](surfaces/inflationSwapButterflyShared.ts).  No shell changes.
- **A cross-market ZCIS butterfly primitive** lands (e.g. USD_ZCIS_5Y − EUR_ZCIS_5Y triplet curvature) → it ships as a SEPARATE module; this module remains the single-curve object.
- **The trailing range window is promoted from 252 → configurable** (config.yaml `planned_extensions` already names this) → expose `trailing_range_window_days` on the schema, add an "Advanced" control on the extended view, and update the methodology row's "Trailing range" line.
- **The wire migrates butterfly output from BPS → PERCENT** (config.yaml plan for harmonising with the linker side) → flip the shared helpers to multiply by 100 for display.  This is one centralised edit in `inflationSwapButterflyShared.ts` (mirror the real-yield-butterfly `pctToBps` pattern).
- **The desk wants per-row historical decomposition** (short/belly/long levels per trade date) → surface from the bespoke `time_series` rows if the schema is extended; today the per-row shape only carries `butterfly_bps + z_score`.

---

## 5. Which backend doctrine does this module operationalise?

- **FM1** (module identity) — folder name equals backend `tool_name`.
- **FM3** (surface-tier capability declaration) — claims `generic_runnable` + `custom_build_surface` + `monitor_surface`.
- **FM4** (tier-set parsimony) — overridden by `rendering_density.md §1.2` for the dual-view mandate; Monitor is justified by `surface_contract.md §3.4` eligibility (desk-canonical curve-shape RV read on the inflation-swap side).
- **FM5** (display metadata) — `displayName` / `category` / `oneLineSummary` are PM-facing (per `lifecycle_checklist_template.md` Stage 1C).
- **FM7** (pure-spec assembly) — `module.ts` exports a pure value.
- **FM8** (surface-file contract) — `surfaces.buildExtended` + `surfaces.buildCompact` populated; files at canonical paths.
- **FM9** (routing-claim disclosure) — `typedView: null`; this module owns its own full surfaces.
- **FM10** (THESIS discipline) — this file.
- **FM11** (round-trip test) — [`__tests__/module.spec.ts`](__tests__/module.spec.ts) asserts the dual-view contract.
- **FM12** (loader presence) — module imported in [`src/modules/index.ts`](../../index.ts).
- **PR10 / P5** (methodology label sourcing) — the methodology card sources the honesty disclosure from `current_metrics.methodology_label` (threaded from `config.yaml:methodology.what_it_does`), NOT a hardcoded TS literal.
- Per the **methodology-exposure standalone-bridge contract** (`methodology_exposure.md §5`): own typed-detail endpoint `/api/v1/rates/detail/zcis-butterfly`; own service helper `fetchDetailInflationSwapButterfly`; own frontend type `InflationSwapButterflyOutput`.
- Per the **rendering-density dual-view contract** (`rendering_density.md §1`): both views ship + both wired in `module.ts.surfaces` + files at canonical paths.

---

## Mockup conformance

The **Compact mockup** shows `Inflation Swap Butterfly · SNAPSHOT` header with 🇺🇸 flag, `USD ZCIS · 2-5-10 FLY · CPI-U` identity + `USD_ZCIS_CPIU · Zero-Coupon Inflation Swap` subtitle, the three KPIs (FLY (BPS) `-3.2 bp` with `(-0.32%)` subtext + "Belly Rich" caption, 1D CHANGE `-1.7 bp` with `(-0.53%)` subtext, Z-SCORE (252D) `-0.86` with "Neutral" caption), a butterfly history chart with ±2σ / ±1.5σ band overlays, the *"ZCIS = zero-coupon inflation swap. CPI-U vs HICPxT vs RPI are not fungible."* footer caveat, and the `🇺🇸 USD ZCIS (CPI-U)` chip.  The implementation reproduces all of these via the shared `BuildCompactShell` + the per-tool `compactKPIs` helper.  The percent-of-belly subtext is reconstructed from `bps / (belly_zcis_rate_pct × 100)` so the desk gets the equivalent percent move without a second tool call.

The **Extended mockup** shows `USD ZCIS 2-5-10 BUTTERFLY · CPI-U` with top-right Z-score `-0.72` / Percentile `38th` cards, a bps-scale KPI strip (BUTTERFLY `-3.1 bp` / 1D `-1.2 bp` / 5D `-2.1 bp` / 1M `-8.2 bp` / Z-SCORE / PERCENTILE / 252D HIGH `+16.7` / LOW `-21.3` / OBS `365`), a `ZCIS 2-5-10 DECOMPOSITION` row, the chart with z-bands, the stretch-context panel, and the methodology card.  The implementation reproduces this structure end-to-end via the shared `BuildExtendedShell` + the per-tool `extendedKPIs` + `decompositionKPIs` + `buildMethodologyRows` helpers.

---

## Version log

| Version | Date | Change |
|---|---|---|
| v3 | 2026-05-29 | Round-1 dual-view + Monitor implementation per `rendering_density.md`.  Shipped `surfaces/BuildExtended.tsx`, `surfaces/BuildCompact.tsx`, `surfaces/monitor/InflationSwapButterflyWidget.tsx`, and the shared helper `surfaces/inflationSwapButterflyShared.ts`.  Tier set updated to `[generic_runnable, custom_build_surface, monitor_surface]`.  Standalone-bridge typed-detail endpoint shipped at `/api/v1/rates/detail/zcis-butterfly`.  Brought to parity with `calculate_real_yield_butterfly_tool` (single-curve 3-leg fly sibling) and the sibling `calculate_cross_market_inflation_swap_spread_tool` (CPI-family caveat threading).  Backend ships butterfly + period changes + range + wing spreads already in BPS — no unit conversion needed. |
| v2 | 2026-05-26 | Stage 4f — rewrote the body to drop stale "Stage 3 scaffold" framing and answer Q2–Q5 for real. |
| v1 | 2026-05-25 | Stage 3 scaffold — runtime tier only, no surfaces. |
