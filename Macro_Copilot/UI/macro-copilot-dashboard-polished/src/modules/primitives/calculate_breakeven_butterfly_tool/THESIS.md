# THESIS — `calculate_breakeven_butterfly_tool`

> Round-1 dispatch under the dual-view rendering-density contract.  Brought to full parity with `calculate_breakeven_inflation_simple_tool`: ships an extended Build view (full canvas for single-tool queries) AND a compact Build view (grid card for multi-tool query DAGs) — both REQUIRED per [`rendering_density.md`](../../../../../docs_revamped/03_standards/rendering_density.md) §1 — plus a Monitor tile.

**Version:** v3 (Round-1 dual-view + Monitor implementation)
**Module spec:** [`module.ts`](module.ts)
**Backend artifact:** `calculate_breakeven_butterfly_tool` (generic_runnable; standalone bridge via `/api/v1/rates/detail/breakeven-butterfly`)
**Tier set:** `[generic_runnable, custom_build_surface, monitor_surface]`
**Category:** `cross_market_rv`
**Mockups:** [`./mockups/Compact.png`](./mockups/Compact.png) + [`./mockups/Extended.png`](./mockups/Extended.png) — committed alongside the module per the mockup-first workflow.

---

## 1. What surfaces does this module ship?

- **`generic_runnable`** — runtime-status tier; backend ships in `_PRIMITIVE_SPECS`.
- **`custom_build_surface`** — the Phase-1 **dual-view** contract:
  - **Extended Build view** ([`surfaces/BuildExtended.tsx`](surfaces/BuildExtended.tsx)) — full canvas mounted for single-tool queries.  Controls strip (single **Country Pair** dropdown + single **Triplet** dropdown + lookback + field), top-right Z-score / Percentile / Country-Pair cards (the third carries the `Inflation compensation × 3 legs. Risk premia & liquidity premia embedded at every tenor.` honesty caveat inline), a bps-scale KPI strip plus a decomposition row (three endpoint breakevens + two component wing spreads), butterfly-history chart with ±2σ z-score bands, stretch-context panel, methodology card sourced from the backend's `methodology_label`, lineage footer.
  - **Compact Build view** ([`surfaces/BuildCompact.tsx`](surfaces/BuildCompact.tsx)) — grid card mounted as a node body inside multi-tool query DAGs (e.g. *"compare US 5s10s30s breakeven fly vs UK 5s10s30s breakeven fly"*).  Headline 3-KPI strip (FLY / 1D CHANGE / Z-SCORE), mini-chart with z-score bands, the inflation-compensation-curvature caveat footer + country-pair chip, click-to-expand affordance.
- **`monitor_surface`** — Monitor bento tile ([`surfaces/monitor/BreakevenButterflyWidget.tsx`](surfaces/monitor/BreakevenButterflyWidget.tsx)) showing one same-country breakeven butterfly (pair × triplet × lookback parameterised; defaults US (UST/TIPS) / 5s10s30s / 252).  Breakeven butterflies are a desk-canonical curve-shape RV read; inherently compact per `rendering_density.md §8`.

---

## 2. What does the user read off each surface?

### Extended Build view

The full investigation canvas for ONE breakeven butterfly.  A PM reads, in order: (a) the title row identifying the country pair + triplet (e.g. *"US 5s10s30s BREAKEVEN FLY"*) + as-of date; (b) the top-right Z-Score / Percentile / Country-Pair cards (the last carries the *"Inflation compensation × 3 legs. Risk premia & liquidity premia embedded at every tenor."* caveat inline); (c) the bps-scale KPI strip — current butterfly (bps) with belly-rich / belly-cheap caption, 1d/5d/1m changes (bps), z-score with regime+direction caption, percentile, 252d high/low, observation count; (d) the decomposition row — three endpoint breakevens (`short_be`, `belly_be`, `long_be` in bps) + two wing spreads (`belly − short`, `long − belly` in bps) so the desk can audit `belly_breakeven_bps − 0.5 × (short_breakeven_bps + long_breakeven_bps)` on the same screen; (e) the butterfly-history chart with z-score band overlays; (f) the stretch-context panel; (g) the methodology card (construction formula, sign convention, tenors with year fractions, field, z-model, the honesty disclosure threaded from `current_metrics.methodology_label`, same-country pair, linker caveat); (h) the lineage footer.  The single **Country Pair** dropdown expands to both nominal + linker legs (a butterfly is a same-country object); the single **Triplet** dropdown enforces strict short < belly < long ordering by only offering registered triplets.

### Compact Build view

The at-a-glance grid card.  A PM sees this in a multi-tool prompt.  They read THREE numbers + a sparkline: current butterfly (bps, with Belly Rich / Belly Cheap caption), 1-day change (bps, tone-coloured), rolling 252d z-score (with regime+direction caption — Normal / Elevated Rich / Extreme Rich / etc).  The mini-chart shows the butterfly history with ±2σ / ±1.5σ z-score bands.  The footer carries the *"Inflation compensation × 3 legs. Risk premia & liquidity premia embedded at every tenor."* caveat + the country-pair chip (e.g. *"US · UST/TIPS"*).  The expand arrow opens the extended view in a modal.

### Monitor tile

One same-country breakeven butterfly, desk-glanceable: current butterfly (bps with belly-rich / belly-cheap qualifier), 1-day change (bps), 252d percentile, and a high/low range strip with a current marker.  Country flag + z-score badge in the header.

---

## 3. Why these surfaces and not others?

**Why the dual-view contract** (both extended + compact REQUIRED): per [`rendering_density.md §1.1`](../../../../../docs_revamped/03_standards/rendering_density.md) multi-tool prompts are the default reality; a tool that ships only an extended view falls back to a generic artifact-type card in multi-tool DAGs.  Breakeven butterflies are commonly compared across countries (*"US 2s5s10s vs UK 2s5s10s breakeven fly"*) and across triplets (*"US 2s5s10s vs US 5s10s30s breakeven fly"*), so the compact view earns its keep immediately.  The standard OVERRIDES FM4 parsimony for the dual-view mandate.

**Why these three compact KPIs** (current butterfly + 1d change + z-score): they're the desk-canonical "first three numbers" a PM reads off a breakeven butterfly snapshot — *"where is the curvature now / how much did it move today / is this stretched?"*.  Alternatives considered + rejected: belly breakeven outright (the curvature object IS the snapshot — surfacing the belly breakeven instead would hide the construction); 252d percentile (already conveyed by the z-score regime + band overlay); the two wing spreads (decomposition detail, surfaced in the extended view's decomposition row, not headline).

**Why a SINGLE Country-Pair dropdown** (not two): each leg of each endpoint of the butterfly is a same-country object by construction; the compute layer's `_enforce_same_country_invariant` helper refuses cross-country pairs with a controlled error.  Surfacing two independent dropdowns would invite invalid selections; the single dropdown enforces validity at the input layer.

**Why a SINGLE Triplet dropdown** (not three independent tenor dropdowns): the schema's `_short_belly_long_strictly_ordered` validator rejects inverted or duplicate triplets at the input layer (e.g. short='10Y', belly='5Y', long='2Y').  Surfacing three independent dropdowns would invite invalid orderings; the single dropdown of registered triplet presets (per pair) makes invalid orderings unreachable.

**Why the inflation-compensation-curvature caveat is REQUIRED inline** (not hidden): misreading a bond-implied breakeven butterfly as a clean expected-inflation-curvature forecast is a genuine desk error — each endpoint breakeven carries an inflation risk premium + relative liquidity premium, and the butterfly inherits all three at every endpoint.  Per `rendering_density.md §2.2` + §12 the methodology caveat must be reachable in the compact view; here it's the canonical one-liner in the footer, and the full honesty disclosure threads onto the wire via `current_metrics.methodology_label` (sourced from `config.yaml:methodology.what_it_does`, not hardcoded).

**Why no z-score override controls in the extended view**: unlike the spot breakeven primitive, the breakeven butterfly Pydantic schema does NOT expose `z_score_window_days` / `z_score_min_periods` / `z_score_ddof` as input fields — they fall through to the YAML at every call site.  Surfacing them as controls would silently shadow the YAML; the extended view honours the actual contract instead.

**Why a typed-detail endpoint** (`/api/v1/rates/detail/breakeven-butterfly`): per [`methodology_exposure.md §5`](../../../../../docs_revamped/03_standards/methodology_exposure.md) every standalone module ships its own typed bridge; the generic `/run` route stays the LLM-facing surface.

---

## 4. What would change the design?

- **A duration-neutral / DV01-weighted breakeven butterfly primitive** lands → it ships as a SEPARATE module (different concept), not a knob on this one; the extended view could then offer a side-by-side "fixed-weight vs DV01-weight" framing.
- **A new linker market** (e.g. AUS, JPY) with a matching nominal curve lands → add the pair to `PAIR_BY_LINKER` + `BREAKEVEN_BUTTERFLY_TRIPLETS_BY_PAIR` in [`surfaces/breakevenButterflyShared.ts`](surfaces/breakevenButterflyShared.ts); add the linker caveat to the shared `countryCaveats.ts` registry.  No shell changes.
- **An inflation-risk-premium- / liquidity-premium-adjusted breakeven butterfly primitive** lands → it ships as a SEPARATE module (different concept); the extended view could then offer a "compensation vs expectations curvature" comparison.
- **A cross-country breakeven butterfly primitive** lands (`config.yaml.planned_extensions` already names this) → it ships as a SEPARATE module; this module remains the same-country object.
- **Z-band absolute-value labelling** (the breakeven mockup shows σ labels) → the bands use σ labels today for cross-tool consistency with the spot breakeven module.  Promoting absolute-bp labels is a shared-`MainChart` enhancement.

---

## 5. Which backend doctrine does this module operationalise?

- **FM1** (module identity) — folder name equals backend `tool_name`.
- **FM3** (surface-tier capability declaration) — claims `generic_runnable` + `custom_build_surface` + `monitor_surface`.
- **FM4** (tier-set parsimony) — overridden by `rendering_density.md §1.2` for the dual-view mandate; Monitor is justified by `surface_contract.md §3.4` eligibility (desk-canonical curve-shape RV read).
- **FM5** (display metadata) — `displayName` / `category` / `oneLineSummary` are PM-facing (per `lifecycle_checklist_template.md` Stage 1C).
- **FM7** (pure-spec assembly) — `module.ts` exports a pure value.
- **FM8** (surface-file contract) — `surfaces.buildExtended` + `surfaces.buildCompact` populated; files at canonical paths.
- **FM9** (routing-claim disclosure) — `typedView: null`; this module owns its own full surfaces.
- **FM10** (THESIS discipline) — this file.
- **FM11** (round-trip test) — [`__tests__/module.spec.ts`](__tests__/module.spec.ts) asserts the dual-view contract.
- **FM12** (loader presence) — module imported in [`src/modules/index.ts`](../../index.ts).
- **PR10 / P5** (methodology label sourcing) — the methodology card sources the honesty disclosure from `current_metrics.methodology_label` (threaded from `config.yaml:methodology.what_it_does`), NOT a hardcoded TS literal.
- Per the **methodology-exposure standalone-bridge contract** (`methodology_exposure.md §5`): own typed-detail endpoint `/api/v1/rates/detail/breakeven-butterfly`; own service helper `fetchDetailBreakevenButterfly`; own frontend type `BreakevenButterflyOutput`.
- Per the **rendering-density dual-view contract** (`rendering_density.md §1`): both views ship + both wired in `module.ts.surfaces` + files at canonical paths.

---

## Mockup conformance

The mockup PNGs in `mockups/` show a `2s5s10s` default for the US (UST + USD_TIPS) pair.  The implementation defaults USD_TIPS to **5Y / 10Y / 30Y** instead, because the USD_TIPS playbook ingests only 5Y / 10Y / 20Y / 30Y benchmarks — there is no 2Y TIPS series in the substrate.  Defaulting to 2s5s10s would yield a controlled `{"error": ...}` envelope from `compute()`, not a renderable result.

The deviation is data-driven, not stylistic: extending it would require adding a 2Y TIPS benchmark to the USD_TIPS playbook (see `rates_agent/playbooks/inflation_indexed_bonds.yml`) and back-filling the historical series.  Until that ingestion lands, the implementation honours the underlying data over the mockup.  The other three linker pairs (`GBP_LINKER`, `EUR_FR_LINKER`, `CAD_RRB`) default to the triplet that IS implementable for each pair, matching the mockup intent where possible.

The Compact view's identity chip therefore shows the *implementable* triplet for the chosen pair (e.g. `UST · 5Y · 10Y · 30Y` for the US default, `GBP_LINKER · 2Y · 5Y · 10Y` for the UK default) instead of the mockup's literal `UST · USD_TIPS · 2Y · 5Y · 10Y`.  This is the intended Compact behaviour; the mockup snapshot precedes the data-availability check.

---

## Version log

| Version | Date | Change |
|---|---|---|
| v3 | 2026-05-29 | Round-1 dual-view + Monitor implementation per `rendering_density.md`.  Shipped `surfaces/BuildExtended.tsx`, `surfaces/BuildCompact.tsx`, `surfaces/monitor/BreakevenButterflyWidget.tsx`, and the shared helper `surfaces/breakevenButterflyShared.ts`.  Tier set updated to `[generic_runnable, custom_build_surface, monitor_surface]`.  Standalone-bridge typed-detail endpoint shipped at `/api/v1/rates/detail/breakeven-butterfly`.  Brought to parity with `calculate_breakeven_inflation_simple_tool`. |
| v2 | 2026-05-26 | Stage 4f — rewrote the body to drop stale "Stage 3 scaffold" framing and answer Q2–Q5 for real. |
| v1 | 2026-05-25 | Stage 3 scaffold — runtime tier only, no surfaces. |
