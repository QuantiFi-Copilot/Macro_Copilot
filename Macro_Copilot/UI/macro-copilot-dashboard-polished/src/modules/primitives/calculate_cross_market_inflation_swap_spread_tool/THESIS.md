# THESIS — `calculate_cross_market_inflation_swap_spread_tool`

> First `inflation_swaps` tool brought to dual-view + standalone-bridge parity with the Phase-1 pilots.  Ships an extended Build view (full canvas for single-tool queries) AND a compact Build view (grid card for multi-tool query DAGs) — both REQUIRED per [`rendering_density.md`](../../../../../docs_revamped/03_standards/rendering_density.md) §1 — plus a Monitor tile.

**Version:** v3 (factory dual-view + Monitor implementation)
**Module spec:** [`module.ts`](module.ts)
**Backend artifact:** `calculate_cross_market_inflation_swap_spread_tool` (generic_runnable; standalone bridge via `/api/v1/rates/detail/cross-market-zcis`)
**Tier set:** `[generic_runnable, custom_build_surface, monitor_surface]`
**Category:** `cross_market_rv`
**Mockups:** [`./mockups/Compact.png`](./mockups/Compact.png) + [`./mockups/Extended.png`](./mockups/Extended.png) — committed alongside the module per the mockup-first workflow.

---

## 1. What surfaces does this module ship?

- **`generic_runnable`** — runtime-status tier; backend ships in `_PRIMITIVE_SPECS`.
- **`custom_build_surface`** — the dual-view contract:
  - **Extended Build view** ([`surfaces/BuildExtended.tsx`](surfaces/BuildExtended.tsx)) — full canvas mounted for single-tool queries.  Controls strip (two **Leg A / Leg B** dropdowns + tenor + lookback + field), top-right Z-score / Percentile / Index-Families cards, a bps-scale KPI strip (spread + changes + range + per-leg PERCENT level decomposition + observation count), spread-history chart with ±2σ z-score bands, stretch-context panel, methodology card (per-leg `inflation_index_family` / `index_lag` / `interpolation` / `underlying_index` + the wire's `index_family_caveat` + the `methodology_label` disclosure), lineage footer.
  - **Compact Build view** ([`surfaces/BuildCompact.tsx`](surfaces/BuildCompact.tsx)) — grid card mounted as a node body inside multi-tool query DAGs (e.g. *"compare USD-EUR vs USD-GBP vs EUR-GBP 5Y ZCIS"*).  Title `USD-EUR 5Y ZCIS` with subtitle `CPI-U vs HICPxT`, headline 3-KPI strip (SPREAD bps + 1D CHANGE bps + Z-SCORE 252D), mini-chart with z-score bands, footer caveat `CPI-U vs HICPxT · not fungible inflation measures` + per-leg flag chips, click-to-expand affordance.
- **`monitor_surface`** — Monitor bento tile ([`surfaces/monitor/CrossMarketZcisWidget.tsx`](surfaces/monitor/CrossMarketZcisWidget.tsx)) showing one cross-market ZCIS spread (leg_a × leg_b × tenor × lookback parameterised; defaults USD_ZCIS / EUR_ZCIS / 5Y / 252).  Cross-market ZCIS spreads are a canonical cross-CB inflation-divergence read per `surface_contract.md §3.4`.  Inherently compact per `rendering_density.md §8`.

---

## 2. What does the user read off each surface?

### Extended Build view

The full investigation canvas for ONE cross-market ZCIS spread.  A PM reads, in order: (a) the title row `USD-EUR 5Y ZCIS SPREAD` with the per-leg subtitle `USD_ZCIS_CPIU 5Y vs EUR_ZCIS_HICPxT 5Y` + the two flag chips + as-of date; (b) the top-right Z-Score / Percentile / Index-Families cards (the last carries the load-bearing index-family caveat — `CPI-U vs HICPxT (not fungible inflation measures)` — sourced from the wire's `current_metrics.index_family_caveat`); (c) the bps-scale KPI strip — current SPREAD (bps + percent subtext), 1d/5d/1m changes (bps), z-score, percentile, 252d high/low, observation count, AND the per-leg PERCENT levels (LEG A / LEG B) so the `spread = leg_a − leg_b` decomposition is auditable on the same screen; (d) the spread-history chart with z-score band overlays; (e) the stretch-context panel (`USD-EUR ZCIS spread is elevated wider than its trailing-year mean ... `); (f) the methodology card — construction formula, sign convention (`leg_a − leg_b`), field, alignment discipline (strict pandas inner-join, no synthetic spread points), per-leg `inflation_index_family` / `index_lag` / `interpolation` / `underlying_index`, the wire's `index_family_caveat`, the wire's full `methodology_label` disclosure; (g) the lineage footer.

### Compact Build view

The at-a-glance grid card.  A PM sees this in a multi-tool prompt.  They read THREE numbers + a sparkline: current SPREAD (bps, subtext in percent), 1-day change (bps, tone-coloured, subtext in percent), rolling 252d z-score (with regime caption — Normal / Elevated / Extreme).  The mini-chart shows the spread history with ±2σ / ±1.5σ z-score bands.  The footer carries the load-bearing one-line caveat — `CPI-U vs HICPxT · not fungible inflation measures` — sourced from `current_metrics.index_family_caveat` on the wire (NOT a TS literal), plus the per-leg flag chips (e.g. `🇺🇸 🇪🇺 · USD-EUR`).  The expand arrow opens the extended view in a modal.

### Monitor tile

One cross-market ZCIS spread, desk-glanceable: current spread (bps + percent subtext), 1-day change (bps, sign-coloured), 252d percentile, a high/low range strip with a current marker, AND the index-family caveat one-liner on a separate row (per `rendering_density.md §2.2` methodology MUST remain reachable in compact contexts; the Monitor is not a compact-Build but the same principle applies for honest disclosure).  Pair flags + z-score badge in the header.

---

## 3. Why these surfaces and not others?

**Why the dual-view contract** (both extended + compact REQUIRED): per [`rendering_density.md §1.1`](../../../../../docs_revamped/03_standards/rendering_density.md) multi-tool prompts are the default reality; cross-market ZCIS spreads are characteristically queried in batches (*"USD-EUR vs USD-GBP vs EUR-GBP at 5Y"* — 3 calls; *"USD-EUR ZCIS at 2Y/5Y/10Y/30Y"* — 4 calls); a tool that ships only an extended view falls back to a generic artifact-type card in those DAGs.  The standard OVERRIDES FM4 parsimony for the dual-view mandate.

**Why these three compact KPIs** (SPREAD bps + 1D CHANGE bps + Z-SCORE 252D): they're the desk-canonical "first three numbers" a PM reads off a cross-market ZCIS snapshot — *"where is the spread now / how much did it move today / is this stretched?"*.  Alternatives considered + rejected: SPREAD in percent (the bps form is the desk-canonical change unit and reads better in a dense card — percent shown as subtext); 252d percentile (already conveyed by the z-score regime + band overlay; surfaced in the extended view's KPI strip); per-leg levels (decomposition detail, surfaced in the extended view's KPI strip, not headline); 1m change (same reason — the daily move is the headline read).

**Why the index-family caveat is REQUIRED inline** (not hidden): misreading a cross-market ZCIS spread as a clean expected-inflation divergence is a genuine desk error — USD_ZCIS references US CPI-U, EUR_ZCIS references euro-area HICPxT, GBP_ZCIS references UK RPI, and these are structurally distinct inflation measures.  The spread therefore captures BOTH inflation-expectation differentials AND structural index-family differences.  Per `rendering_density.md §2.2` + §12 the methodology caveat must be reachable in the compact view; here it's the canonical one-liner in the footer (`CPI-U vs HICPxT · not fungible inflation measures`), sourced from the wire's `current_metrics.index_family_caveat` (single source of truth per `methodology_exposure.md §1`), with the full per-leg metadata + `methodology_label` disclosure surfacing in the extended view's methodology card.

**Why two Leg dropdowns** (not a single pair-picker): unlike a same-country breakeven (where the linker family uniquely determines the nominal counterparty), a cross-market ZCIS spread is genuinely two-degrees-of-freedom — USD/EUR, USD/GBP, EUR/GBP, and the reversed sign-conventions are all valid desk reads.  The schema layer enforces `leg_a != leg_b`; the controls UI snaps the other leg when the user picks a colliding family, so the user can never dispatch an invalid pair.

**Why a typed-detail endpoint** (`/api/v1/rates/detail/cross-market-zcis`): per [`methodology_exposure.md §5`](../../../../../docs_revamped/03_standards/methodology_exposure.md) every standalone module ships its own typed bridge; the generic `/run` route stays the LLM-facing surface.  This is the FIRST inflation_swaps standalone-bridge endpoint — the inflation_swaps roster of three sibling primitives (level / curve-spread / forward) will migrate to the same pattern.

---

## 4. What would change the design?

- **A new ZCIS curve family** (e.g. JPY_ZCIS, CAD_ZCIS) lands → add the entry to `FAMILY_REGISTRY` in [`surfaces/crossMarketZcisShared.ts`](surfaces/crossMarketZcisShared.ts) with its `marketShort` / `indexShort` / `flag`.  No shell changes.  The wire's `current_metrics.index_family_caveat` continues to name the actual resolved per-leg families; the static fallback `shortIndexCaveat()` is purely for the loading state.
- **A risk-premium-adjusted cross-market spread primitive** lands (would isolate the inflation-expectation differential from the index-family differential) → it ships as a SEPARATE module (different concept), not a knob on this one; the extended view could then offer a side-by-side "raw spread vs adjusted" framing.
- **The desk decides z-score conventions need exposure** at the Pydantic Input layer → promote `z_score_window_days` / `z_score_min_periods` / `z_score_ddof` from YAML-locked → exposed (per the `methodology_exposure.md §3` decision protocol); add Advanced controls in [`surfaces/BuildExtended.tsx`](surfaces/BuildExtended.tsx) mirroring `calculate_breakeven_inflation_simple_tool`'s pattern.
- **Absolute-bp z-band labels** (the mockup shows ±2σ as σ labels for cross-tool consistency).  Promoting absolute-bp labels is a shared-`MainChart` enhancement that would backport to every tool at once; tracked as a deferred consistency decision.

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
- **P5** (honest disclosure) — the index-family caveat surfaces in BOTH compact (one-liner footer, sourced from `current_metrics.index_family_caveat`) and extended (per-leg metadata + caveat + `methodology_label` disclosure) views.
- Per the **methodology-exposure standalone-bridge contract** (`methodology_exposure.md §5`): own typed-detail endpoint `/api/v1/rates/detail/cross-market-zcis`; own service helper `fetchDetailCrossMarketZcis`; own frontend type `CrossMarketInflationSwapSpreadOutput`.
- Per the **rendering-density dual-view contract** (`rendering_density.md §1`): both views ship + both wired in `module.ts.surfaces` + files at canonical paths.

---

## Version log

| Version | Date | Change |
|---|---|---|
| v3 | 2026-05-29 | Factory dual-view + Monitor implementation per `rendering_density.md`.  Shipped `surfaces/BuildExtended.tsx`, `surfaces/BuildCompact.tsx`, `surfaces/monitor/CrossMarketZcisWidget.tsx`, and the shared helper `surfaces/crossMarketZcisShared.ts`.  Tier set updated to `[generic_runnable, custom_build_surface, monitor_surface]`.  Standalone-bridge typed-detail endpoint shipped at `/api/v1/rates/detail/cross-market-zcis`.  Brought to parity with `calculate_breakeven_inflation_simple_tool` / `calculate_breakeven_butterfly_tool`.  First inflation_swaps tool under the dual-view contract. |
| v2 | 2026-05-26 | Stage 4f — rewrote the body to drop stale "Stage 3 scaffold" framing. |
| v1 | 2026-05-25 | Stage 3 scaffold — runtime tier only, no surfaces. |
