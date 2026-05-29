# THESIS — `calculate_real_yield_butterfly_tool`

> Round-1 dispatch under the dual-view rendering-density contract.  Brought to full parity with `calculate_breakeven_butterfly_tool` (the 3-leg fly sibling): ships an extended Build view (full canvas for single-tool queries) AND a compact Build view (grid card for multi-tool query DAGs) — both REQUIRED per [`rendering_density.md`](../../../../../docs_revamped/03_standards/rendering_density.md) §1 — plus a Monitor tile.

**Version:** v3 (Round-1 dual-view + Monitor implementation)
**Module spec:** [`module.ts`](module.ts)
**Backend artifact:** `calculate_real_yield_butterfly_tool` (generic_runnable; standalone bridge via `/api/v1/rates/detail/real-yield-butterfly`)
**Tier set:** `[generic_runnable, custom_build_surface, monitor_surface]`
**Category:** `cross_market_rv`
**Mockups:** [`./mockups/Compact.png`](./mockups/Compact.png) + [`./mockups/Extended.png`](./mockups/Extended.png) — committed alongside the module per the mockup-first workflow.

---

## 1. What surfaces does this module ship?

- **`generic_runnable`** — runtime-status tier; backend ships in `_PRIMITIVE_SPECS`.
- **`custom_build_surface`** — the Phase-1 **dual-view** contract:
  - **Extended Build view** ([`surfaces/BuildExtended.tsx`](surfaces/BuildExtended.tsx)) — full canvas mounted for single-tool queries.  Controls strip (single **Linker Curve** dropdown + single **Triplet** dropdown + lookback + field), top-right Z-score / Percentile / Linker-Curve cards (the third carries the `CPI-lag applies to linkers. Real yields = real rates.` honesty caveat inline), a bps-scale KPI strip plus a decomposition row (three endpoint real yields in PERCENT + two component wing spreads in BPS), butterfly-history chart with ±2σ z-score bands, stretch-context panel, methodology card sourced from the backend's `methodology_label`, lineage footer.
  - **Compact Build view** ([`surfaces/BuildCompact.tsx`](surfaces/BuildCompact.tsx)) — grid card mounted as a node body inside multi-tool query DAGs (e.g. *"compare US 5s10s30s real-yield fly vs UK 5s10s30s real-yield fly"*).  Headline 3-KPI strip (FLY / 1D CHANGE / Z-SCORE), mini-chart with z-score bands, the real-rates caveat footer + linker chip, click-to-expand affordance.
- **`monitor_surface`** — Monitor bento tile ([`surfaces/monitor/RealYieldButterflyWidget.tsx`](surfaces/monitor/RealYieldButterflyWidget.tsx)) showing one linker real-yield butterfly (curve × triplet × lookback parameterised; defaults US (USD_TIPS) / 5s10s30s / 252).  Real-yield butterflies are a desk-canonical curve-shape RV read on the linker side; inherently compact per `rendering_density.md §8`.

---

## 2. What does the user read off each surface?

### Extended Build view

The full investigation canvas for ONE linker real-yield butterfly.  A PM reads, in order: (a) the title row identifying the curve_family + triplet (e.g. *"USD_TIPS 5-10-30 REAL YIELD BUTTERFLY"*) + as-of date; (b) the top-right Z-Score / Percentile / Linker-Curve cards (the last carries the *"CPI-lag applies to linkers. Real yields = real rates."* caveat inline); (c) the bps-scale KPI strip — current butterfly (bps, converted from PERCENT wire shape) with belly-rich / belly-cheap caption, 1d/5d/1m changes (bps from backend), z-score with regime+direction caption, percentile, 252d high/low (bps), observation count; (d) the decomposition row — three endpoint real yields (`short_real_yield_pct`, `belly_real_yield_pct`, `long_real_yield_pct` in %) + two wing spreads (`belly − short`, `long − belly` in bps) so the desk can audit `belly_ry − 0.5 × (short_ry + long_ry)` on the same screen; (e) the butterfly-history chart with z-score band overlays; (f) the stretch-context panel; (g) the methodology card (construction formula, sign convention, tenors with year fractions, field, z-model, the honesty disclosure threaded from `current_metrics.methodology_label`, linker identity, country caveat); (h) the lineage footer.  The single **Linker Curve** dropdown picks the curve_family (single-curve primitive — no nominal counterparty); the single **Triplet** dropdown enforces strict short < belly < long ordering by only offering registered triplets per curve.

### Compact Build view

The at-a-glance grid card.  A PM sees this in a multi-tool prompt.  They read THREE numbers + a sparkline: FLY (bps, with Belly Rich / Belly Cheap caption — the canonical desk read), 1-day change (bps, tone-coloured, with a coarse ±σ subtext reconstructed from `|fly_bps / z|`), rolling 252d z-score (with regime+direction caption — Normal / Elevated Rich / Extreme Rich / etc).  The mini-chart shows the butterfly history with ±2σ / ±1.5σ z-score bands.  The footer carries the *"CPI-lag applies to linkers. Real yields = real rates."* caveat + the linker chip (e.g. *"🇺🇸 USD_TIPS · TIPS (single linker curve)"*).  The expand arrow opens the extended view in a modal.

### Monitor tile

One linker real-yield butterfly, desk-glanceable: current butterfly (bps with belly-rich / belly-cheap qualifier), 1-day change (bps), 252d percentile, and a high/low range strip with a current marker.  Country flag + z-score badge in the header.

---

## 3. Why these surfaces and not others?

**Why the dual-view contract** (both extended + compact REQUIRED): per [`rendering_density.md §1.1`](../../../../../docs_revamped/03_standards/rendering_density.md) multi-tool prompts are the default reality; a tool that ships only an extended view falls back to a generic artifact-type card in multi-tool DAGs.  Real-yield butterflies are commonly compared across linker markets (*"US 5s10s30s vs UK 5s10s30s real-yield fly"*) and across triplets (*"US 5s10s20s vs US 5s10s30s real-yield fly"*), so the compact view earns its keep immediately.  The standard OVERRIDES FM4 parsimony for the dual-view mandate.

**Why these three compact KPIs** (current butterfly + 1d change + z-score): they're the desk-canonical "first three numbers" a PM reads off a real-yield butterfly snapshot — *"where is the curvature now / how much did it move today / is this stretched?"*.  Alternatives considered + rejected: belly real yield outright (the curvature object IS the snapshot — surfacing the belly real-yield instead would hide the construction); 252d percentile (already conveyed by the z-score regime + band overlay); the two wing spreads (decomposition detail, surfaced in the extended view's decomposition row, not headline).  The 1D-change cell carries a coarse `(±Xσ)` subtext per the mockup so the desk reads the size of the move both in absolute bps AND in its own rolling-σ units.

**Why a SINGLE Linker-Curve dropdown** (not a nominal/linker pair): this primitive is single-curve by construction — the Pydantic Input takes one `curve_family` and three tenors, with no nominal counterparty.  Distinct from `calculate_breakeven_butterfly_tool`, which crosses a nominal + linker pair.  Surfacing a nominal dropdown would invite invalid input shapes; the single dropdown honours the actual contract.

**Why a SINGLE Triplet dropdown** (not three independent tenor dropdowns): the schema's `_short_belly_long_strictly_ordered` validator rejects inverted or duplicate triplets at the input layer (e.g. short='10Y', belly='5Y', long='2Y').  Surfacing three independent dropdowns would invite invalid orderings; the single dropdown of registered triplet presets (per curve) makes invalid orderings unreachable.  The USD_TIPS playbook ingests only 5Y / 10Y / 20Y / 30Y benchmarks (no 2Y) so the registered presets respect data availability.

**Why bps display when the wire ships PERCENT**: real yields on the wire are in PERCENT (same units as the underlying yield series).  The butterfly (`belly_ry − 0.5 × (short + long)`) inherits PERCENT.  But on the desk, curvature numbers are read in BPS — a butterfly of `−0.14%` reads as `−14 bp` to every PM looking at it.  The display layer therefore multiplies `current_butterfly_pct × 100` (and `high_252d_pct × 100`, `low_252d_pct × 100`, wing spreads × 100) for bps presentation.  Daily / weekly / monthly changes already arrive in bps from the backend.  This unit-handling decision is centralised in [`surfaces/realYieldButterflyShared.ts`](surfaces/realYieldButterflyShared.ts) (`pctToBps`); the decomposition's three endpoint real yields stay in `%` because that's the natural unit for a level (not a spread or curvature).

**Why the curvature-of-real-yields caveat is REQUIRED** (not hidden): misreading a real-yield butterfly as a curvature of inflation expectations or nominal rates is a genuine desk error.  Per `rendering_density.md §2.2` + §12 the methodology caveat must be reachable in the compact view; here it's the canonical one-liner in the footer ("CPI-lag applies to linkers. Real yields = real rates."), and the full honesty disclosure threads onto the wire via `current_metrics.methodology_label` (sourced from `config.yaml:methodology.what_it_does`, not hardcoded).  The methodology card additionally surfaces the linker identity (country/curve) and the linker-specific caveat from the shared `countryCaveats.ts` registry (e.g. the USD 20Y TIPS issuance discontinuation, the UK RPI→CPIH 2030 transition).

**Why no z-score override controls in the extended view**: unlike the spot real-yield level primitive, the real-yield butterfly Pydantic schema does NOT expose `z_score_window_days` / `z_score_min_periods` / `z_score_ddof` as input fields — they fall through to the YAML at every call site (consistent with the breakeven butterfly's design).  Surfacing them as controls would silently shadow the YAML; the extended view honours the actual contract instead.

**Why a typed-detail endpoint** (`/api/v1/rates/detail/real-yield-butterfly`): per [`methodology_exposure.md §5`](../../../../../docs_revamped/03_standards/methodology_exposure.md) every standalone module ships its own typed bridge; the generic `/run` route stays the LLM-facing surface.

---

## 4. What would change the design?

- **A duration-neutral / DV01-weighted real-yield butterfly primitive** lands → it ships as a SEPARATE module (different concept), not a knob on this one; the extended view could then offer a side-by-side "fixed-weight vs DV01-weight" framing.
- **A new linker market** (e.g. AUS, JPY) with a complete pillar grid lands → add the curve to `CURVE_REGISTRY` + `REAL_YIELD_BUTTERFLY_TRIPLETS_BY_CURVE` in [`surfaces/realYieldButterflyShared.ts`](surfaces/realYieldButterflyShared.ts); add the linker caveat to the shared `countryCaveats.ts` registry.  No shell changes.
- **A cross-country real-yield butterfly primitive** lands → it ships as a SEPARATE module; this module remains the single-curve object.
- **The trailing range window is promoted from 252 → configurable** (config.yaml `planned_extensions` already names this) → expose `trailing_range_window_days` on the schema, add an "Advanced" control on the extended view, and update the methodology row's "Trailing range" line.
- **Z-band absolute-value labelling** (the mockup shows σ labels) → the bands use σ labels today for cross-tool consistency with the spot real-yield level + breakeven butterfly modules.  Promoting absolute-bp labels is a shared-`MainChart` enhancement.

---

## 5. Which backend doctrine does this module operationalise?

- **FM1** (module identity) — folder name equals backend `tool_name`.
- **FM3** (surface-tier capability declaration) — claims `generic_runnable` + `custom_build_surface` + `monitor_surface`.
- **FM4** (tier-set parsimony) — overridden by `rendering_density.md §1.2` for the dual-view mandate; Monitor is justified by `surface_contract.md §3.4` eligibility (desk-canonical curve-shape RV read on the linker side).
- **FM5** (display metadata) — `displayName` / `category` / `oneLineSummary` are PM-facing (per `lifecycle_checklist_template.md` Stage 1C).
- **FM7** (pure-spec assembly) — `module.ts` exports a pure value.
- **FM8** (surface-file contract) — `surfaces.buildExtended` + `surfaces.buildCompact` populated; files at canonical paths.
- **FM9** (routing-claim disclosure) — `typedView: null`; this module owns its own full surfaces.
- **FM10** (THESIS discipline) — this file.
- **FM11** (round-trip test) — [`__tests__/module.spec.ts`](__tests__/module.spec.ts) asserts the dual-view contract.
- **FM12** (loader presence) — module imported in [`src/modules/index.ts`](../../index.ts).
- **PR10 / P5** (methodology label sourcing) — the methodology card sources the honesty disclosure from `current_metrics.methodology_label` (threaded from `config.yaml:methodology.what_it_does`), NOT a hardcoded TS literal.
- Per the **methodology-exposure standalone-bridge contract** (`methodology_exposure.md §5`): own typed-detail endpoint `/api/v1/rates/detail/real-yield-butterfly`; own service helper `fetchDetailRealYieldButterfly`; own frontend type `RealYieldButterflyOutput`.
- Per the **rendering-density dual-view contract** (`rendering_density.md §1`): both views ship + both wired in `module.ts.surfaces` + files at canonical paths.

---

## Mockup conformance

The Compact mockup shows `USD_TIPS 5-10-30 RY FLY` with `(single linker curve)` subtitle, the three KPIs (FLY `-14 bp` with "Belly Rich" caption, 1D CHANGE `-1.8 bp` with `(-0.12σ)` subtext, Z-SCORE `-2.07` with "Extreme Rich" caption), a butterfly history chart with ±2σ / ±1.5σ band overlays, the *"CPI-lag applies to TIPS. Real yields = real rates."* footer caveat, and the 🇺🇸 USD_TIPS linker chip.  The implementation reproduces all of these.  The single-character difference is the caveat text — the mockup spells "TIPS"; the implementation generalises to "linkers" because the caveat surfaces for all four linker markets (USD_TIPS / GBP_LINKER / EUR_FR_LINKER / CAD_RRB) without per-market specialisation.  The country-specific caveat (e.g. the TIPS 20Y discontinuation note) lives in the shared `countryCaveats.ts` registry and surfaces on the Extended view's methodology card.

The Extended mockup shows `USD_TIPS 5-10-30 REAL YIELD BUTTERFLY` with top-right Z-score / Percentile / Linker-Curve cards, a bps-scale KPI strip, a decomposition row, the chart with z-bands, the stretch-context panel, and the methodology card.  The implementation reproduces this structure end-to-end via the shared `BuildExtendedShell`.

---

## Version log

| Version | Date | Change |
|---|---|---|
| v3 | 2026-05-29 | Round-1 dual-view + Monitor implementation per `rendering_density.md`.  Shipped `surfaces/BuildExtended.tsx`, `surfaces/BuildCompact.tsx`, `surfaces/monitor/RealYieldButterflyWidget.tsx`, and the shared helper `surfaces/realYieldButterflyShared.ts`.  Tier set updated to `[generic_runnable, custom_build_surface, monitor_surface]`.  Standalone-bridge typed-detail endpoint shipped at `/api/v1/rates/detail/real-yield-butterfly`.  Brought to parity with `calculate_breakeven_butterfly_tool` (3-leg fly sibling).  PERCENT→BPS unit handling centralised in `realYieldButterflyShared.ts`. |
| v2 | 2026-05-26 | Stage 4f — rewrote the body to drop stale "Stage 3 scaffold" framing and answer Q2–Q5 for real. |
| v1 | 2026-05-25 | Stage 3 scaffold — runtime tier only, no surfaces. |
