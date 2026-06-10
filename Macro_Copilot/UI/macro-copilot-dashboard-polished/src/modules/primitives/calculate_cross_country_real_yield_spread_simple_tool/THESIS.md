# THESIS — `calculate_cross_country_real_yield_spread_simple_tool`

> Second `inflation_indexed_bonds` CROSS-COUNTRY tool brought to dual-view + standalone-bridge parity with the Phase-1 pilots and the Batch-3 sibling `calculate_cross_country_breakeven_spread_simple_tool`.  Ships an extended Build view (full canvas for single-tool queries) AND a compact Build view (grid card for multi-tool query DAGs) — both REQUIRED per [`rendering_density.md`](../../../../../docs_revamped/03_standards/rendering_density.md) §1 — plus a Monitor tile.

**Version:** v3 (factory dual-view + Monitor implementation)
**Module spec:** [`module.ts`](module.ts)
**Backend artifact:** `calculate_cross_country_real_yield_spread_simple_tool` (generic_runnable; standalone bridge via `/api/v1/rates/detail/cross-country-real-yield-spread`)
**Tier set:** `[generic_runnable, custom_build_surface, monitor_surface]`
**Category:** `cross_market_rv`
**Mockups:** [`./mockups/Compact.png`](./mockups/Compact.png) + [`./mockups/Extended.png`](./mockups/Extended.png) — committed alongside the module per the mockup-first workflow.

---

## 1. What surfaces does this module ship?

- **`generic_runnable`** — runtime-status tier; backend ships in `_PRIMITIVE_SPECS`.
- **`custom_build_surface`** — the dual-view contract:
  - **Extended Build view** ([`surfaces/BuildExtended.tsx`](surfaces/BuildExtended.tsx)) — full canvas mounted for single-tool queries.  Controls strip (two **First curve / Second curve** linker dropdowns + tenor + lookback + field), top-right Z-score / Percentile / Index-Families cards, a mixed-unit KPI strip (SPREAD %, daily/weekly/monthly Δ bps, z-score, percentile, 252d high/low %, observation count, per-curve REAL-YIELD endpoint % decomposition), spread-history chart with ±2σ z-score bands on a PERCENT axis, stretch-context panel, methodology card (per-curve `indexLong` / resolved (country, currency) + the load-bearing index-family + market-structure caveats + the `methodology_label` disclosure), lineage footer.
  - **Compact Build view** ([`surfaces/BuildCompact.tsx`](surfaces/BuildCompact.tsx)) — grid card mounted as a node body inside multi-tool query DAGs (e.g. *"compare UK-US vs FR-US vs CA-US 10Y real-yield spread"*).  Title `UK-US 10Y RY` with subtitle `UK 10Y Real Yield (RPI) minus US 10Y Real Yield (CPI-U)`, headline 3-KPI strip (SPREAD % + 1D CHANGE bps + Z-SCORE 252D), mini-chart with z-score bands, footer caveat `RPI vs CPI-U · not fungible inflation measures` + per-country flag chips, click-to-expand affordance.
- **`monitor_surface`** — Monitor bento tile ([`surfaces/monitor/CrossCountryRealYieldSpreadWidget.tsx`](surfaces/monitor/CrossCountryRealYieldSpreadWidget.tsx)) showing one cross-country real-yield spread (first_curve_family × second_curve_family × tenor × lookback parameterised; defaults GBP_LINKER vs USD_TIPS / 10Y / 252).  Cross-country linker real-yield spreads are a desk-canonical cross-CB real-rate-divergence read per `surface_contract.md §3.4`.  Inherently compact per `rendering_density.md §8`.

---

## 2. What does the user read off each surface?

### Extended Build view

The full investigation canvas for ONE cross-country real-yield spread.  A PM reads, in order: (a) the title row `UK-US 10Y Real Yield` with the per-country subtitle `UK 10Y Real Yield (RPI) minus US 10Y Real Yield (CPI-U)` + the two flag chips (🇬🇧🇺🇸) + as-of date; (b) the top-right Z-Score / Percentile / Index-Families cards (the last carries the load-bearing index-family caveat — `RPI vs CPI-U · not fungible inflation measures` — derived from the per-curve linker metadata + corroborated by the wire's `methodology_label`); (c) the KPI strip — current SPREAD (%, signed), 1d/5d/1m changes (bps; per desk convention a percent-units spread's *changes* report in bps), z-score, percentile, 252d high/low (%), observation count, AND the per-curve REAL-YIELD endpoint decomposition (RY A / RY B, %) so the `spread = first_real_yield − second_real_yield` math is auditable on the same screen; (d) the spread-history chart in PERCENT units with z-score band overlays; (e) the stretch-context panel; (f) the methodology card — construction formula, sign convention (`first_curve − second_curve`), units note (PERCENT for the spread, BPS for changes), field, composition pattern (two `get_real_yield_level` calls per curve → inner-join → percent subtraction), per-curve resolved (country, currency) + inflation-index family long form, the load-bearing index-family + market-structure caveats, the wire's full `methodology_label` disclosure; (g) the lineage footer.

### Compact Build view

The at-a-glance grid card.  A PM sees this in a multi-tool prompt.  They read THREE numbers + a sparkline: current SPREAD (%, signed, primary emphasis — real yields are in PERCENT, so the spread stays in PERCENT), 1-day change (bps, tone-coloured), rolling 252d z-score (with regime caption — Normal / Elevated / Extreme).  The mini-chart shows the percent-units spread history with ±2σ / ±1.5σ z-score bands.  The footer carries the load-bearing one-line caveat — `RPI vs CPI-U · not fungible inflation measures` — derived from the wire's curve_family identifiers (the long-form `methodology_label` is surfaced in full on the Extended methodology card), plus the per-country flag chips (e.g. `🇬🇧 🇺🇸 · UK-US`).  The expand arrow opens the extended view in a modal.

### Monitor tile

One cross-country real-yield spread, desk-glanceable: current spread (%, signed), 1-day change (bps, sign-coloured), 252d percentile, a high/low range strip (% scale) with a current marker, AND the index-family caveat one-liner on a separate row (per `rendering_density.md §2.2` methodology MUST remain reachable in compact contexts).  Pair flags + z-score badge in the header — the z-badge's `title=` tooltip carries the full `methodology_label` prose for desk readers who want the formal disclosure inline.

---

## 3. Why these surfaces and not others?

**Why the dual-view contract** (both extended + compact REQUIRED): per [`rendering_density.md §1.1`](../../../../../docs_revamped/03_standards/rendering_density.md) multi-tool prompts are the default reality; cross-country real-yield spreads are characteristically queried in batches (*"UK-US vs FR-US vs CA-US at 10Y"* — 3 calls; *"UK-US RY at 5Y/10Y/30Y"* — 3 calls; *"UK-US RY vs UK-US BE at 10Y"* — 2 calls comparing the two cross-country sibling primitives); a tool that ships only an extended view falls back to a generic artifact-type card in those DAGs.  The standard OVERRIDES FM4 parsimony for the dual-view mandate.

**Why these three compact KPIs** (SPREAD % + 1D CHANGE bps + Z-SCORE 252D): they're the desk-canonical "first three numbers" a PM reads off a cross-country real-yield snapshot — *"where is the spread now / how much did it move today / is this stretched?"*.  Alternatives considered + rejected: SPREAD in BPS (real yields are quoted in PERCENT, so the percent form is the desk-canonical level unit and matches the wire's PERCENT-units `time_series_spread` — converting to bps would silently rescale a percent-units quantity); 252d percentile (already conveyed by the z-score regime + band overlay; surfaced in the extended view's KPI strip); per-curve real-yield levels (decomposition detail, surfaced in the extended view's KPI strip, not headline); 1m change (same reason — the daily move is the headline read).

**Why mixed units on the compact view** (SPREAD %, 1D CHANGE bps): this is the desk convention for real-yield work — *levels* in percent (because real yields are quoted in percent), *changes* in bps (because the desk reads "the spread moved 5 bps today" not "the spread moved 0.05%").  The wire's Pydantic Output reflects this: `current_spread_pct` / `high_252d_pct` / `low_252d_pct` are PERCENT; `daily_change_bps` / `weekly_change_bps` / `monthly_change_bps` are BPS.  The KPI tiles' `unit` strings (`%` vs `bp`) carry the disambiguation visibly — there is no silent conversion in either direction.

**Why the index-family + market-structure caveats are REQUIRED inline** (not hidden): misreading a cross-country real-yield spread as a clean real-rate differential is a genuine desk error — USD TIPS reference US CPI-U non-seasonally adjusted, UK GILT linkers reference RPI (legacy) / CPIH (newer), FR OAT / DE BUND / IT BTP linkers reference Eurozone HICPxT, Canadian RRBs reference Canada CPI, AND cross-country linker markets differ materially in benchmark availability at the same tenor pillar, issuance size, liquidity premium, and deflation-floor treatment.  The spread therefore captures BOTH real-rate divergence AND structural differences.  Per `rendering_density.md §2.2` + §12 the methodology caveat must be reachable in the compact view; here it's the canonical one-liner in the footer (`RPI vs CPI-U · not fungible inflation measures`), derived from the wire's curve_family identifiers; the full long-form `methodology_label` (the YAML-sourced wire-honesty disclosure) surfaces verbatim on the extended view's methodology card and via the Monitor widget's `title=` tooltip.

**Why two single-curve dropdowns** (not a packed-pair convention like the breakeven sibling): real-yield levels read off the linker curve ALONE — there is no nominal sovereign required to form a real-yield (distinct from a breakeven, which is nominal MINUS linker).  Each leg is therefore a SINGLE linker curve_family.  Surfacing the curve directly (e.g. `USD_TIPS`) as one select per leg makes the cross-country invariant unrepresentable as a same-curve mismatch (the snap-fallback in `handleControlChange` enforces `first_curve_family != second_curve_family`).  The single-curve convention propagates to the URL state, the Monitor widget params, and the shared helper's registry.

**Why a typed-detail endpoint** (`/api/v1/rates/detail/cross-country-real-yield-spread`): per [`methodology_exposure.md §5`](../../../../../docs_revamped/03_standards/methodology_exposure.md) every standalone module ships its own typed bridge; the generic `/run` route stays the LLM-facing surface.  This is the SECOND inflation_indexed_bonds CROSS-COUNTRY standalone-bridge endpoint (the sibling cross-country breakeven primitive shipped in Batch 3).

---

## 4. What would change the design?

- **A new linker curve_family lands** (e.g. JPY_LINKER, AUD_LINKER) → add the entry to `CURVE_REGISTRY` in [`surfaces/crossCountryRealYieldSpreadSimpleShared.ts`](surfaces/crossCountryRealYieldSpreadSimpleShared.ts) with its `countryShort` / `indexShort` / `indexLong` / `flag`.  No shell changes.  The wire's `current_metrics.methodology_label` continues to name the load-bearing caveat verbatim; the static `shortIndexCaveat()` helper consults the registry purely for the compact-view one-liner.
- **An inflation-swap-based cross-country real-rate primitive lands** (would replace bond-implied real yields with inflation-swap-implied real rates, decomposing more cleanly) → it ships as a SEPARATE module (different concept; the catalog entry's "simple" name explicitly denotes the bond-implied unadjusted variant), not a knob on this one.  The extended view could then offer a side-by-side "bond-implied real-yield spread vs swap-implied real-rate spread" framing if both modules ship.
- **A currency-hedged variant lands** (would FX-adjust each leg) → it ships as a SEPARATE module (different concept; the "simple" name explicitly denotes the raw-FX unadjusted variant), not a knob on this one.
- **The desk decides z-score conventions need exposure** at the Pydantic Input layer → promote `z_score_window_days` / `z_score_min_periods` / `z_score_ddof` from YAML-locked → exposed (per the `methodology_exposure.md §3` decision protocol); add Advanced controls in [`surfaces/BuildExtended.tsx`](surfaces/BuildExtended.tsx) mirroring `calculate_breakeven_inflation_simple_tool`'s pattern.
- **Per-leg structured index-family / market-structure metadata makes it onto the wire** (currently `methodology_label` carries the caveat as prose; the per-leg `inflation_index_family` / `linker_liquidity_class` fields are NOT currently on the wire for this primitive) → swap the static `CURVE_REGISTRY` lookup for the wire's structured per-leg metadata (single source of truth per `methodology_exposure.md §1`).  The static registry becomes a fallback only.

---

## 5. Which backend doctrine does this module operationalise?

- **FM1** (module identity) — folder name equals backend `tool_name`.
- **FM3** (surface-tier capability declaration) — claims `generic_runnable` + `custom_build_surface` + `monitor_surface`.
- **FM4** (tier-set parsimony) — overridden by `rendering_density.md §1.2` for the dual-view mandate; Monitor is justified by `surface_contract.md §3.4` eligibility.
- **FM5** (display metadata) — `displayName` / `category` / `oneLineSummary` are PM-facing (per `lifecycle_checklist_template.md` Stage 1C).
- **FM7** (pure-spec assembly) — `module.ts` exports a pure value.
- **FM8** (surface-file contract) — `surfaces.buildExtended` + `surfaces.buildCompact` populated; files at canonical paths.
- **FM9** (routing-claim disclosure) — `typedView: null`; this module owns its own full surfaces.
- **FM10** (THESIS discipline) — this file.
- **FM11** (round-trip test) — [`__tests__/module.spec.ts`](__tests__/module.spec.ts) asserts the dual-view contract.
- **FM12** (loader presence) — module imported in [`src/modules/index.ts`](../../index.ts).
- **P5** (honest disclosure) — the index-family + market-structure caveats surface in BOTH compact (one-liner footer, derived from the wire's curve_family identifiers) and extended (per-curve metadata + caveats + the wire's `methodology_label` verbatim) views.
- Per the **methodology-exposure standalone-bridge contract** (`methodology_exposure.md §5`): own typed-detail endpoint `/api/v1/rates/detail/cross-country-real-yield-spread`; own service helper `fetchDetailCrossCountryRealYieldSpread`; own frontend type `CrossCountryRealYieldSpreadSimpleOutput`.
- Per the **rendering-density dual-view contract** (`rendering_density.md §1`): both views ship + both wired in `module.ts.surfaces` + files at canonical paths.

---

## Mockup conformance

The Extended view's identity row, KPI strip ordering (`SPREAD % / 1D bp / 5D bp / 1M bp / Z-SCORE / PERCENTILE / 252D HIGH % / 252D LOW % / OBS / RY A / RY B`), top-right cards (Z-score / Percentile / Index Families), spread-history chart with ±2σ z-bands on a PERCENT axis, methodology card, and lineage footer all mirror `mockups/Extended.png`.  The Compact view's three headline KPIs (SPREAD % / 1D CHANGE bps / Z-SCORE 252D), mini-chart with z-bands, footer caveat line (`RPI vs CPI-U · not fungible inflation measures`), per-country flag chips, expand affordance, and tone cues (sign colouring + |z| ≥ 1.5 amber band) mirror `mockups/Compact.png`.  Per the Option (c) shell-density precedent recorded in the catalog `design_guardrails`, the compact density matches the shell-standard convention used by the just-shipped cross-country breakeven sibling.

The key shape delta from the breakeven sibling is intentional and surfaced visibly: the spread units are PERCENT (not BPS) because real yields are quoted in PERCENT, and the chart axis + headline KPI render with the `%` unit token (matching the mockup).  Daily / weekly / monthly *changes* remain BPS per desk convention, with the `bp` unit token disambiguating those tiles.

---

## Version log

| Version | Date | Change |
|---|---|---|
| v3 | 2026-06-10 | Factory dual-view + Monitor implementation per `rendering_density.md`.  Shipped `surfaces/BuildExtended.tsx`, `surfaces/BuildCompact.tsx`, `surfaces/monitor/CrossCountryRealYieldSpreadWidget.tsx`, and the shared helper `surfaces/crossCountryRealYieldSpreadSimpleShared.ts`.  Tier set updated to `[generic_runnable, custom_build_surface, monitor_surface]`.  Standalone-bridge typed-detail endpoint shipped at `/api/v1/rates/detail/cross-country-real-yield-spread`.  Brought to parity with `calculate_cross_country_breakeven_spread_simple_tool` (Batch 3).  Second inflation_indexed_bonds cross-country tool under the dual-view contract. |
| v2 | 2026-05-26 | Stage 4f — rewrote the body to drop stale "Stage 3 scaffold" framing. |
| v1 | 2026-05-25 | Stage 3 scaffold — runtime tier only, no surfaces. |
