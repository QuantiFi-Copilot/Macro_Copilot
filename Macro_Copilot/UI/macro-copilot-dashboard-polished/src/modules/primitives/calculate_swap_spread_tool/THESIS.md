# THESIS — `calculate_swap_spread_tool`

> Factory dispatch — dual-view + standalone-bridge module under the rendering-density contract.  Brought to full parity with `calculate_breakeven_inflation_simple_tool` (the Phase-1 single-spread BPS dual-view pilot shape) and the OIS-family siblings `calculate_ois_cross_market_spread_tool` / `calculate_ois_curve_spread_tool` (same risk-neutral OIS leg, same TS-side methodology disclosure marker pending PR10).  Ships an extended Build view (full canvas for single-tool queries) AND a compact Build view (grid card for multi-tool query DAGs) — both REQUIRED per [`rendering_density.md`](../../../../../docs_revamped/03_standards/rendering_density.md) §1 — plus a Monitor tile.

**Version:** v3 (factory dual-view + Monitor implementation)
**Module spec:** [`module.ts`](module.ts)
**Backend artifact:** `calculate_swap_spread_tool` (generic_runnable; standalone bridge via `/api/v1/rates/detail/swap-spread`)
**Tier set:** `[generic_runnable, custom_build_surface, monitor_surface]`
**Category:** `cross_market_rv`
**Mockups:** [`./mockups/Compact.png`](./mockups/Compact.png) + [`./mockups/Extended.png`](./mockups/Extended.png) — committed alongside the module per the mockup-first workflow.

---

## 1. What surfaces does this module ship?

- **`generic_runnable`** — runtime-status tier; backend ships in `_PRIMITIVE_SPECS`.
- **`custom_build_surface`** — the dual-view contract:
  - **Extended Build view** ([`surfaces/BuildExtended.tsx`](surfaces/BuildExtended.tsx)) — full canvas mounted for single-tool queries.  Controls strip (single **Sovereign Leg** dropdown + tenor + lookback + sovereign-field + OIS-field), top-right Z-score / Percentile / Sign-Convention cards, a bps-scale KPI strip (spread + 1d/5d/1m changes + range + per-leg PERCENT level decomposition (SOVEREIGN YIELD / OIS RATE) + observation count), spread-history chart with ±2σ z-score bands, stretch-context panel, methodology card (construction formula + sign convention + alignment discipline + currency-match invariant + the par-leg OIS approximation disclosure), lineage footer.
  - **Compact Build view** ([`surfaces/BuildCompact.tsx`](surfaces/BuildCompact.tsx)) — grid card mounted as a node body inside multi-tool query DAGs (e.g. *"compare UST 10Y vs BUND 10Y vs UK Gilt 10Y swap spread"*).  Title `UST-SOFR · 10Y` with subtitle `UST 10Y yield − SOFR 10Y OIS rate`, headline 3-KPI strip (SPREAD bps with sign-convention caption + 1D CHANGE bps + Z-SCORE 252D), mini-chart with z-score bands, footer caveat `Par-leg OIS approximation. NOT per-bond ASW.` + country-flag chip, click-to-expand affordance.
- **`monitor_surface`** — Monitor bento tile ([`surfaces/monitor/SwapSpreadWidget.tsx`](surfaces/monitor/SwapSpreadWidget.tsx)) showing one same-currency swap spread (pair × tenor × lookback parameterised; defaults UST / 10Y / 252).  Swap spreads are a canonical desk daily-glance read on cash-vs-swap RV per `surface_contract.md §3.4`.  Inherently compact per `rendering_density.md §8`.

---

## 2. What does the user read off each surface?

### Extended Build view

The full investigation canvas for ONE swap spread.  A PM reads, in order: (a) the title row `USD 10Y SWAP SPREAD` with the per-leg subtitle `UST 10Y yield − SOFR 10Y OIS rate` + the country flag + as-of date; (b) the top-right Z-Score / Percentile / Sign-Convention cards (the Sign-Convention card surfaces the par-leg OIS approximation caveat at the top of the canvas, NOT only in the methodology card — the desk needs the caveat alongside the headline number); (c) the bps-scale KPI strip — current SPREAD (bps + the sign caption: `Treasuries Cheap` / `Treasuries Rich` / `Parity`), 1d/5d/1m changes (bps), z-score, percentile, 252d high/low, observation count (computed CLIENT-SIDE from `time_series_spread.rows` because the swap-spread wire is leaner than the ZCIS / sovereign cross-market siblings — same shape as the OIS cross-market sibling), AND the per-leg PERCENT levels (SOVEREIGN YIELD / OIS RATE) so the `sovereign_yield − ois_rate` decomposition is auditable on the same screen; (d) the spread-history chart with z-score band overlays; (e) the stretch-context panel framed in cash-vs-swap rich/cheap language; (f) the methodology card — construction formula, sign convention (`sovereign − OIS` = treasuries cheap when positive), tenor, sovereign field (YLD_YTM_MID) + OIS field (PX_LAST) explicitly, alignment discipline (strict pandas inner-join, no synthetic spread points), currency-match invariant, units (BPS for the spread; PERCENT for the per-leg legs), cross-domain invariant, and the par-leg OIS approximation disclosure; (g) the lineage footer.  The single **Sovereign Leg** dropdown expands to both legs (a swap spread is a same-currency object, so the sovereign family uniquely determines the canonical OIS counterparty).

### Compact Build view

The at-a-glance grid card.  A PM sees this in a multi-tool prompt.  They read THREE numbers + a sparkline: current SPREAD (bps, primary emphasis with the sign-convention caption — e.g. `+24.6 bp · Treasuries Cheap`), 1-day change (bps, tone-coloured), rolling 252d z-score (with regime caption — `Normal` / `Elevated` / `Extreme` — e.g. `+1.63 · Elevated`).  The mini-chart shows the spread history with ±2σ / ±1.5σ z-score bands.  The footer carries the load-bearing one-line caveat — `Par-leg OIS approximation. NOT per-bond ASW.` — sourced from the per-tool registry (NOT a hardcoded TSX literal — single source of truth in [`surfaces/swapSpreadShared.ts`](surfaces/swapSpreadShared.ts) `SWAP_SPREAD_COMPACT_CAVEAT`, with the `// TODO(PR10)` marker for when the OIS sub-domain ships `methodology_label` on the wire — mirrors the sibling OIS curve_spread / cross-market / butterfly registries), plus the country-flag chip (e.g. `🇺🇸 · UST-SOFR · 10Y`).  The expand arrow opens the extended view in a modal.

### Monitor tile

One same-currency swap spread, desk-glanceable: current spread (bps + the sign-convention word — `Treasuries Cheap` / `Treasuries Rich`), 1-day change (bps, sign-coloured), 252d percentile, a high/low range strip with a current marker (the swap-spread wire DOES carry `high_252d_bps` / `low_252d_bps` — unlike the leaner OIS curve_spread Output, so the range strip ships), AND the par-leg OIS approximation caveat one-liner on a separate row (per `rendering_density.md §2.2` methodology MUST remain reachable in compact contexts).  Country flag + pair label + z-score badge in the header.

---

## Mockup conformance

The committed `mockups/Compact.png` shows a SECONDARY 6-KPI strip beneath the headline 3-KPI row (5D CHANGE, 1M CHANGE, 252D PCTL, 252D HIGH, 252D LOW, OBSERVATIONS) — making the compact card render six numbers in addition to the canonical three.  Per the **Option (c) precedent** established on Batch 1 (commit `fdac7d2`) + Batch 2 (commit `cbd5613`), the compact view ships with shell-standard 3-KPI density to preserve the cross-tool consistency of the multi-tool DAG grid.  The 6-KPI secondary strip is accepted as a mockup deviation: the full 11-cell strip (5D / 1M change, percentile, 252d high / low, observations, per-leg PERCENT levels) surfaces in the extended view's KPI strip, so the desk loses no information — the secondary strip just moves up the rendering ladder.  Documented here so the reviewer can see the deviation was deliberate.  Other mockup elements — title row format, sparkline shape, sign-convention caption, country-flag chip, footer caveat — match `Compact.png` 1:1.  `Extended.png` matches the extended view 1:1 (controls strip, top-right cards, full KPI strip, chart with z-score bands, stretch context, methodology card, lineage footer).

---

## 3. Why these surfaces and not others?

**Why the dual-view contract** (both extended + compact REQUIRED): per [`rendering_density.md §1.1`](../../../../../docs_revamped/03_standards/rendering_density.md) multi-tool prompts are the default reality; swap spreads are characteristically queried in batches (*"UST 10Y vs BUND 10Y vs Gilt 10Y swap spread"* — 3 calls; *"USD swap spread at 2Y/5Y/10Y/30Y"* — 4 calls); a tool that ships only an extended view falls back to a generic artifact-type card in those DAGs.  The standard OVERRIDES FM4 parsimony for the dual-view mandate.

**Why these three compact KPIs** (SPREAD bps with sign caption + 1D CHANGE bps + Z-SCORE 252D): they're the desk-canonical "first three numbers" a PM reads off a swap-spread snapshot — *"where does the cash-vs-swap RV sit now / how much did it move today / is this stretched?"*.  The sign caption (e.g. `Treasuries Cheap` at SPREAD > 0) is the read the desk actually wants — turning a signed bps number into a desk-language statement about which side of the RV is the cheap leg.  Alternatives considered + rejected: SPREAD in percent (the bps form is the desk-canonical change unit and reads better in a dense card); 252d percentile (already conveyed by the z-score regime + band overlay; surfaced in the extended view's KPI strip); per-leg yields (decomposition detail, surfaced in the extended view's KPI strip, not headline); 1m change (same reason — the daily move is the headline read).  The mockup's secondary 6-KPI strip is accepted as a deviation per the Mockup conformance subsection above.

**Why the par-leg OIS approximation caveat is REQUIRED inline** (not hidden): misreading the swap spread as the true present-value asset-swap-spread is a genuine desk error — the par-leg ASW (`(sovereign_yield − ois_rate) × 100` at matched tenor) is the desk quick-and-dirty, NOT the PV-equivalent spread that prices the bond as a floating-rate note.  Per `rendering_density.md §2.2` + §12 the methodology caveat must be reachable in the compact view; here it's the canonical one-liner in the footer (`Par-leg OIS approximation. NOT per-bond ASW.`), with the full disclosure surfacing in the extended view's methodology card (error magnitude vs true ASW — typically 1-3 bps for liquid sovereigns, growing to 10+ bps for off-the-run / high-coupon bonds).

**Why a single Sovereign-Leg dropdown** (not two): a swap spread is a same-currency object by construction; the backend schema layer rejects cross-currency pairings (e.g. UST vs ESTR) with a controlled error via the `CURVE_FAMILY_TO_CURRENCY` closed mapping.  Surfacing two independent dropdowns would invite invalid selections; the single dropdown enforces validity at the input layer.  Mirrors the breakeven_inflation_simple Phase-1 pilot pattern (a breakeven is a same-country object → single Country-Pair dropdown).

**Why client-side derivation of `observation_count`**: the swap-spread Output is leaner than the linker / sovereign / ZCIS cross-market siblings (no wire `observation_count` field — same shape as OIS curve_spread / cross_market_spread).  Computing it CLIENT-SIDE from `time_series_spread.rows` is honest (the wire IS providing the underlying data) and lets the mockup ship without backend changes.  Marked with the `// TODO(PR10)` marker in [`surfaces/swapSpreadShared.ts`](surfaces/swapSpreadShared.ts) so when the backend extends the Output schema, the wrapper swaps client-derivation for the wire field in a one-line edit.

**Why a typed-detail endpoint** (`/api/v1/rates/detail/swap-spread`): per [`methodology_exposure.md §5`](../../../../../docs_revamped/03_standards/methodology_exposure.md) every standalone module ships its own typed bridge; the generic `/run` route stays the LLM-facing surface.  The endpoint slug is distinct from the sibling sovereign `/detail/spread` (sovereign vs sovereign tenor spread) and OIS `/detail/ois-curve-spread` (OIS vs OIS tenor spread) — this primitive is the cross-domain sovereign-vs-OIS swap spread.

---

## 4. What would change the design?

- **A `methodology_label` field lands on the swap-spread Pydantic Output** (PR10 — siblings in inflation_indexed_bonds / inflation_swaps already carry it; the OIS sub-domain is the remaining PR10 backlog) → the methodology card's "Disclosure" row + the compact-view caveat row switch to source `current_metrics.methodology_label` (one-line edit; the marker is in [`surfaces/swapSpreadShared.ts`](surfaces/swapSpreadShared.ts) on `SWAP_SPREAD_COMPACT_CAVEAT`).
- **Wire `observation_count` lands** → replace the client-side derivation in `extendedKPIs()` with a direct wire read.  Distinct from this module's current "compute it ourselves" stance, which is the honest interim.
- **A new sovereign-vs-OIS currency pair lands** (e.g. AUD ACGB vs AUD_OIS, CAD CGB vs CAD_OIS) → add the pair to `PAIR_BY_SOVEREIGN` in [`surfaces/swapSpreadShared.ts`](surfaces/swapSpreadShared.ts) with its `currency` / `country` / `sovereignShort` / `oisShort` / `flag`; extend the backend `CURVE_FAMILY_TO_CURRENCY` closed mapping in lockstep.  No shell changes.
- **A present-value true-ASW primitive lands** (V2 work — would ingest bond-level metadata: coupon, accrued interest, day-count, dirty-price) → it ships as a SEPARATE module (different concept), not a knob on this one; the extended view could then offer a side-by-side "par-leg approx vs PV true-ASW" framing.
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
- **P5** (honest disclosure) — the par-leg OIS approximation caveat surfaces in BOTH compact (one-liner footer, sourced from the per-tool registry with the PR10 wire-honesty marker) and extended (full disclosure + sign convention + per-leg field-name identity + currency-match invariant) views.
- Per the **methodology-exposure standalone-bridge contract** (`methodology_exposure.md §5`): own typed-detail endpoint `/api/v1/rates/detail/swap-spread`; own service helper `fetchDetailSwapSpread`; own frontend type `SwapSpreadOutput`.
- Per the **rendering-density dual-view contract** (`rendering_density.md §1`): both views ship + both wired in `module.ts.surfaces` + files at canonical paths.

---

## Version log

| Version | Date | Change |
|---|---|---|
| v3 | 2026-06-08 | Factory dual-view + Monitor implementation per `rendering_density.md`.  Shipped `surfaces/BuildExtended.tsx`, `surfaces/BuildCompact.tsx`, `surfaces/monitor/SwapSpreadWidget.tsx`, and the shared helper `surfaces/swapSpreadShared.ts`.  Tier set updated to `[generic_runnable, custom_build_surface, monitor_surface]`.  Standalone-bridge typed-detail endpoint shipped at `/api/v1/rates/detail/swap-spread`.  Brought to parity with `calculate_breakeven_inflation_simple_tool` (single-spread BPS dual-view pilot) and OIS-family siblings.  Mockup conformance: shell-standard 3-KPI compact density per Option (c) precedent; mockup's 6-KPI secondary strip accepted as a deviation. |
| v2 | 2026-05-26 | Stage 4f — rewrote the body to drop stale "Stage 3 scaffold" framing. |
| v1 | 2026-05-25 | Stage 3 scaffold — runtime tier only, no surfaces. |
