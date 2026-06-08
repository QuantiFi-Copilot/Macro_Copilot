# THESIS — `calculate_ois_cross_market_spread_tool`

> Factory dispatch — dual-view + standalone-bridge module under the rendering-density contract.  Brought to full parity with `calculate_cross_market_inflation_swap_spread_tool` (the same-shape design twin — two-curve same-tenor cross-market spread on a different curve family) and the OIS-family siblings `calculate_ois_curve_spread_tool` / `calculate_ois_butterfly_tool` / `calculate_ois_forward_rate_tool` / `get_ois_rate_level_tool` (same closed curve_family enum, same risk-neutral policy-pricing caveat).  Ships an extended Build view (full canvas for single-tool queries) AND a compact Build view (grid card for multi-tool query DAGs) — both REQUIRED per [`rendering_density.md`](../../../../../docs_revamped/03_standards/rendering_density.md) §1 — plus a Monitor tile.

**Version:** v3 (factory dual-view + Monitor implementation)
**Module spec:** [`module.ts`](module.ts)
**Backend artifact:** `calculate_ois_cross_market_spread_tool` (generic_runnable; standalone bridge via `/api/v1/rates/detail/ois-cross-market-spread`)
**Tier set:** `[generic_runnable, custom_build_surface, monitor_surface]`
**Category:** `cross_market_rv`
**Mockups:** [`./mockups/Compact.png`](./mockups/Compact.png) + [`./mockups/Extended.png`](./mockups/Extended.png) — committed alongside the module per the mockup-first workflow.

---

## 1. What surfaces does this module ship?

- **`generic_runnable`** — runtime-status tier; backend ships in `_PRIMITIVE_SPECS`.
- **`custom_build_surface`** — the dual-view contract:
  - **Extended Build view** ([`surfaces/BuildExtended.tsx`](surfaces/BuildExtended.tsx)) — full canvas mounted for single-tool queries.  Controls strip (two **Leg A / Leg B** OIS-curve dropdowns + tenor + lookback + field), top-right Z-score / Percentile / Policy-Regime cards, a bps-scale KPI strip (spread + 1d/5d/1m changes + range + per-leg PERCENT level decomposition + observation count), spread-history chart with ±2σ z-score bands, stretch-context panel, methodology card (per-leg overnight-index identity + sign convention + alignment discipline + the risk-neutral-policy-pricing disclosure), lineage footer.
  - **Compact Build view** ([`surfaces/BuildCompact.tsx`](surfaces/BuildCompact.tsx)) — grid card mounted as a node body inside multi-tool query DAGs (e.g. *"compare USD-EUR vs USD-GBP vs EUR-GBP 2Y OIS"*).  Title `SOFR-ESTR · 2Y` with subtitle `Fed vs ECB policy-rate differential`, headline 3-KPI strip (SPREAD bps with central-bank-premium caption + 1D CHANGE bps + Z-SCORE 252D), mini-chart with z-score bands, footer caveat `Cross-central-bank policy divergence. Risk-neutral OIS pricing.` + per-leg flag chips, click-to-expand affordance.
- **`monitor_surface`** — Monitor bento tile ([`surfaces/monitor/CrossMarketOisWidget.tsx`](surfaces/monitor/CrossMarketOisWidget.tsx)) showing one cross-market OIS spread (curve_family_1 × curve_family_2 × tenor × lookback parameterised; defaults USD_SOFR_OIS / EUR_ESTR_OIS / 2Y / 252).  Cross-market OIS spreads are a canonical desk daily-glance item per `surface_contract.md §3.4`.  Inherently compact per `rendering_density.md §8`.

---

## 2. What does the user read off each surface?

### Extended Build view

The full investigation canvas for ONE cross-market OIS spread.  A PM reads, in order: (a) the title row `SOFR-ESTR 2Y OIS SPREAD` with the per-leg subtitle `USD_SOFR_OIS 2Y vs EUR_ESTR_OIS 2Y · Fed vs ECB policy-rate differential` + the two flag chips + as-of date; (b) the top-right Z-Score / Percentile / Policy-Regime cards (the Policy-Regime card surfaces the risk-neutral policy-pricing caveat at the top of the canvas, NOT only in the methodology card — the desk needs the caveat alongside the headline number); (c) the bps-scale KPI strip — current SPREAD (bps + the central-bank-premium word: `Fed Premium` / `ECB Premium` / `Parity`), 1d/5d/1m changes (bps), z-score, percentile, 252d high/low, observation count (computed CLIENT-SIDE from `time_series_spread.rows` because the OIS wire is leaner than the ZCIS / sovereign cross-market siblings), AND the per-leg PERCENT levels (LEG A / LEG B endpoint par-swap rates) so the `curve_family_1 − curve_family_2` decomposition is auditable on the same screen; (d) the spread-history chart with z-score band overlays; (e) the stretch-context panel framed in central-bank-divergence language; (f) the methodology card — construction formula, sign convention (`curve_family_1 − curve_family_2` = leg-A premium), field, alignment discipline (strict pandas inner-join, no synthetic spread points), per-leg overnight-index identity (SOFR / ESTR / SONIA / TONA / AONIA / CORRA), units (BPS for the spread; PERCENT for the per-leg rates), cross-curve invariant, and the risk-neutral-policy-pricing disclosure; (g) the lineage footer.

### Compact Build view

The at-a-glance grid card.  A PM sees this in a multi-tool prompt.  They read THREE numbers + a sparkline: current SPREAD (bps, primary emphasis with the central-bank-premium caption — e.g. `+175 bp · Fed Premium`), 1-day change (bps, tone-coloured, subtext in percent — e.g. `+4.5 bp (+2.64%)`), rolling 252d z-score (with regime caption — `Normal` / `Elevated` / `Extreme` — e.g. `+1.62 · Elevated`).  The mini-chart shows the spread history with ±2σ / ±1.5σ z-score bands.  The footer carries the load-bearing one-line caveat — `Cross-central-bank policy divergence. Risk-neutral OIS pricing.` — sourced from the per-tool registry (NOT a hardcoded TSX literal — single source of truth in [`surfaces/crossMarketOisShared.ts`](surfaces/crossMarketOisShared.ts) `OIS_CROSS_MARKET_COMPACT_CAVEAT`, with the `// TODO(PR10)` marker for when the OIS sub-domain ships `methodology_label` on the wire — mirrors the sibling OIS curve_spread / butterfly registries), plus the per-leg flag chips (e.g. `🇺🇸 🇪🇺 · USD-EUR`).  The expand arrow opens the extended view in a modal.

### Monitor tile

One cross-market OIS spread, desk-glanceable: current spread (bps + the central-bank-premium word — `Fed Premium` / `ECB Premium`), 1-day change (bps, sign-coloured), 252d percentile, a high/low range strip with a current marker (the OIS cross-market wire DOES carry `high_252d_bps` / `low_252d_bps` — unlike the leaner OIS curve_spread Output, so the range strip ships), AND the policy-path-divergence caveat one-liner on a separate row (per `rendering_density.md §2.2` methodology MUST remain reachable in compact contexts).  Pair flags + overnight-index pair label + z-score badge in the header.

---

## 3. Why these surfaces and not others?

**Why the dual-view contract** (both extended + compact REQUIRED): per [`rendering_density.md §1.1`](../../../../../docs_revamped/03_standards/rendering_density.md) multi-tool prompts are the default reality; cross-market OIS spreads are characteristically queried in batches (*"USD-EUR vs USD-GBP vs EUR-GBP at 2Y"* — 3 calls; *"USD-EUR OIS at 2Y/5Y/10Y/30Y"* — 4 calls); a tool that ships only an extended view falls back to a generic artifact-type card in those DAGs.  The standard OVERRIDES FM4 parsimony for the dual-view mandate.

**Why these three compact KPIs** (SPREAD bps + central-bank-premium caption + 1D CHANGE bps + Z-SCORE 252D): they're the desk-canonical "first three numbers" a PM reads off a cross-market OIS snapshot — *"where does the policy-rate differential sit now / how much did it move today / is this stretched?"*.  The central-bank-premium caption (e.g. `Fed Premium` at SOFR>ESTR) is the read the desk actually wants — turning a signed bps number into a desk-language statement about WHICH central bank is pricing more hawkish.  Alternatives considered + rejected: SPREAD in percent (the bps form is the desk-canonical change unit and reads better in a dense card — percent shown as 1d-change subtext); 252d percentile (already conveyed by the z-score regime + band overlay; surfaced in the extended view's KPI strip); per-leg rates (decomposition detail, surfaced in the extended view's KPI strip, not headline); 1m change (same reason — the daily move is the headline read).

**Why the policy-path-divergence caveat is REQUIRED inline** (not hidden): misreading a cross-market OIS spread as a clean rate-arbitrage object is a desk error — USD_SOFR_OIS references SOFR (Fed reaction function), EUR_ESTR_OIS references ESTR (ECB reaction function), and each curve prices its OWN central bank's expected policy path under the risk-neutral measure.  The spread therefore captures RELATIVE central-bank policy stance, NOT a tradable cross-currency arbitrage on the cash leg.  Per `rendering_density.md §2.2` + §12 the methodology caveat must be reachable in the compact view; here it's the canonical one-liner in the footer (`Cross-central-bank policy divergence. Risk-neutral OIS pricing.`), with the full per-leg overnight-index metadata + the risk-neutral-policy-pricing disclosure surfacing in the extended view's methodology card.

**Why two Leg dropdowns** (not a single pair-picker): unlike a same-curve OIS tenor spread (where the curve_family uniquely determines the overnight-index identity), a cross-market OIS spread is genuinely two-degrees-of-freedom — USD/EUR, USD/GBP, EUR/GBP, AUD/CAD, and the reversed sign-conventions are all valid desk reads.  The schema layer enforces `curve_family_1 != curve_family_2`; the controls UI snaps the other leg when the user picks a colliding family, so the user can never dispatch an invalid pair.

**Why client-side derivation of `observation_count`**: the OIS cross-market spread Output is leaner than the linker / sovereign / ZCIS cross-market siblings (no wire `observation_count` field — mirrors the OIS curve_spread + butterfly Output shape).  Computing it CLIENT-SIDE from `time_series_spread.rows` is honest (the wire IS providing the underlying data) and lets the mockup ship without backend changes.  Marked with the `// TODO(PR10)` marker in [`surfaces/crossMarketOisShared.ts`](surfaces/crossMarketOisShared.ts) so when the backend extends the Output schema, the wrapper swaps client-derivation for the wire field in a one-line edit.

**Why a typed-detail endpoint** (`/api/v1/rates/detail/ois-cross-market-spread`): per [`methodology_exposure.md §5`](../../../../../docs_revamped/03_standards/methodology_exposure.md) every standalone module ships its own typed bridge; the generic `/run` route stays the LLM-facing surface.  This is the FIFTH OIS-family standalone-bridge endpoint (after rate_level / curve_spread / butterfly / forward_rate) — the OIS roster is now complete under the dual-view + Monitor contract.

---

## 4. What would change the design?

- **A `methodology_label` field lands on the OIS cross-market spread Pydantic Output** (PR10 — siblings in inflation_indexed_bonds / inflation_swaps already carry it; the OIS sub-domain is the remaining PR10 backlog) → the methodology card's "Disclosure" row + the compact-view caveat row switch to source `current_metrics.methodology_label` (one-line edit; the marker is in [`surfaces/crossMarketOisShared.ts`](surfaces/crossMarketOisShared.ts) on `OIS_CROSS_MARKET_COMPACT_CAVEAT`).
- **Wire `observation_count` lands** → replace the client-side derivation in `extendedKPIs()` with a direct wire read.  Distinct from this module's current "compute it ourselves" stance, which is the honest interim.
- **A new OIS curve family lands** (e.g. NZD_OCR_OIS, CHF_SARON_OIS) → add the family to `FAMILY_REGISTRY` in [`surfaces/crossMarketOisShared.ts`](surfaces/crossMarketOisShared.ts) with its `marketShort` / `indexShort` / `centralBank` / `flag`.  No shell changes.
- **A basis-adjusted cross-market spread primitive** lands (would isolate the policy-stance differential from cross-currency basis-swap noise) → it ships as a SEPARATE module (different concept), not a knob on this one; the extended view could then offer a side-by-side "raw spread vs basis-adjusted" framing.
- **The desk decides z-score conventions need exposure** at the Pydantic Input layer → promote `z_score_window_days` / `z_score_min_periods` / `z_score_ddof` from YAML-locked → exposed (per the `methodology_exposure.md §3` decision protocol); add Advanced controls in [`surfaces/BuildExtended.tsx`](surfaces/BuildExtended.tsx) mirroring `calculate_breakeven_inflation_simple_tool`'s pattern.

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
- **P5** (honest disclosure) — the policy-path-divergence caveat surfaces in BOTH compact (one-liner footer, sourced from the per-tool registry with the PR10 wire-honesty marker) and extended (per-leg overnight-index metadata + central-bank-pair caption + risk-neutral disclosure) views.
- Per the **methodology-exposure standalone-bridge contract** (`methodology_exposure.md §5`): own typed-detail endpoint `/api/v1/rates/detail/ois-cross-market-spread`; own service helper `fetchDetailOisCrossMarketSpread`; own frontend type `OisCrossMarketSpreadOutput`.
- Per the **rendering-density dual-view contract** (`rendering_density.md §1`): both views ship + both wired in `module.ts.surfaces` + files at canonical paths.

---

## Version log

| Version | Date | Change |
|---|---|---|
| v3 | 2026-06-07 | Factory dual-view + Monitor implementation per `rendering_density.md`.  Shipped `surfaces/BuildExtended.tsx`, `surfaces/BuildCompact.tsx`, `surfaces/monitor/CrossMarketOisWidget.tsx`, and the shared helper `surfaces/crossMarketOisShared.ts`.  Tier set updated to `[generic_runnable, custom_build_surface, monitor_surface]`.  Standalone-bridge typed-detail endpoint shipped at `/api/v1/rates/detail/ois-cross-market-spread`.  Brought to parity with `calculate_cross_market_inflation_swap_spread_tool` (ZCIS shape twin) and the OIS-family siblings.  Fifth OIS-family standalone-bridge endpoint; completes the OIS sub-domain under the dual-view + Monitor contract. |
| v2 | 2026-05-26 | Stage 4f — rewrote the body to drop stale "Stage 3 scaffold" framing. |
| v1 | 2026-05-25 | Stage 3 scaffold — runtime tier only, no surfaces. |
