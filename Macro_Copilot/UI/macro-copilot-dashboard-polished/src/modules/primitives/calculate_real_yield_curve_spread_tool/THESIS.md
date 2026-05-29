# THESIS — `calculate_real_yield_curve_spread_tool`

> Phase-1 Stage-C module under the dual-view rendering-density contract.  Brought to full parity with `get_real_yield_level_tool`: ships an extended Build view (full canvas for single-tool queries) AND a compact Build view (grid card for multi-tool query DAGs) — both REQUIRED per [`rendering_density.md`](../../../../../docs_revamped/03_standards/rendering_density.md) §1 — plus a Monitor tile.

**Version:** v3 (Phase-1 Stage-C dual-view + Monitor implementation)
**Module spec:** [`module.ts`](module.ts)
**Backend artifact:** `calculate_real_yield_curve_spread_tool` (generic_runnable; standalone bridge via `/api/v1/rates/detail/real_yield_curve_spread`)
**Tier set:** `[generic_runnable, custom_build_surface, monitor_surface]`
**Category:** `curve_shape`
**Mockups:** [`./mockups/Compact.png`](./mockups/Compact.png) + [`./mockups/Extended.png`](./mockups/Extended.png) — committed alongside the module per the mockup-first workflow.

---

## 1. What surfaces does this module ship?

- **`generic_runnable`** — runtime-status tier; backend ships in `_PRIMITIVE_SPECS`.
- **`custom_build_surface`** — the Phase-1 **dual-view** contract:
  - **Extended Build view** ([`surfaces/BuildExtended.tsx`](surfaces/BuildExtended.tsx)) — full canvas mounted for single-tool queries.  Controls strip (curve-family + **separate Short Tenor / Long Tenor dropdowns** + lookback + field + Advanced z-score overrides), top-right Z-score / Percentile / signed-5-zone-Regime cards, a KPI strip (spread % + bps changes + range + the two endpoint real yields), spread-history chart with ±2σ z-score bands, stretch-context panel, methodology card, lineage footer.
  - **Compact Build view** ([`surfaces/BuildCompact.tsx`](surfaces/BuildCompact.tsx)) — grid card mounted as a node body inside multi-tool DAGs (e.g. *"compare TIPS 5s10s vs UK linker 5s10s real-yield curve"*).  Headline 3-KPI strip, mini-chart with z-score bands, the curve-shape caveat footer + curve-family chip, click-to-expand affordance.
- **`monitor_surface`** — Monitor bento tile ([`surfaces/monitor/RealYieldCurveSpreadWidget.tsx`](surfaces/monitor/RealYieldCurveSpreadWidget.tsx)) showing one real-yield curve spread (curve_family × short_tenor × long_tenor × lookback parameterised; defaults USD_TIPS / 5Y / 10Y / 252).  A real-yield 5s30s / 2s10s curve is a desk daily-glance item per `surface_contract.md §3.4`.  Inherently compact per `rendering_density.md §8`.

---

## 2. What does the user read off each surface?

### Extended Build view

The full investigation canvas for ONE real-yield curve spread.  A PM reads, in order: (a) the title row identifying curve_family + the `<short>s<long>s` pair + as-of date; (b) the top-right Z-Score / Percentile / **signed Curve-Regime slider** cards — the slider is a 5-zone bar (Extreme Down / Elevated Down / Normal / Elevated Up / Extreme Up) because the spread is a SIGNED measure (steepening vs flattening read differently); (c) the KPI strip — current spread (%), 1d/5d/1m changes (bps), z-score, percentile, 252d high/low (%), AND the short + long endpoint real yields so the decomposition is auditable; (d) the spread-history chart with z-score band overlays; (e) the stretch-context panel framed in curve-shape language; (f) the methodology card (construction formula, the *valid tenor pair: long > short* rule, field, z-model, units, resolved country/currency identity, country caveat); (g) the lineage footer.  The **Long Tenor dropdown is filtered to tenors strictly longer than the Short Tenor**, enforcing the curve-spread validity rule at the input layer.

### Compact Build view

The at-a-glance grid card.  A PM sees this in a multi-tool prompt.  They read THREE numbers + a sparkline: current spread (%), 1-day change (bps, tone-coloured), rolling 252d z-score (with regime caption).  The mini-chart shows the spread history with ±2σ / ±1.5σ z-score bands.  The footer carries the *"Real-rate curve shape. Long real yields vs short."* caveat + the curve-family chip (e.g. *"USD_TIPS 5s10s"*).  The expand arrow opens the extended view in a modal.

### Monitor tile

One real-yield curve spread, desk-glanceable: current spread (%), 1-day change (bps), 252d percentile, and a high/low range strip with a current marker.  Country flag + z-score badge in the header.

---

## 3. Why these surfaces and not others?

**Why the dual-view contract** (both extended + compact REQUIRED): per [`rendering_density.md §1.1`](../../../../../docs_revamped/03_standards/rendering_density.md) multi-tool prompts are the default reality.  Real-yield curve spreads are frequently compared cross-country and against the nominal curve, so the compact view earns its keep.  The standard OVERRIDES FM4 parsimony for the dual-view mandate.

**Why these three compact KPIs** (current spread + 1d change + z-score): the desk-canonical "first three numbers" for a curve-spread snapshot — *"where is the curve now / did it steepen or flatten today / is the shape stretched?"*.  Alternatives rejected: the two endpoint real yields (decomposition detail, surfaced in the extended KPI strip); 252d percentile (conveyed by the z-score regime + bands); the curve geometry (year fractions) — irrelevant to the headline read.

**Why the spread is in PERCENT but changes in BPS**: the real-yield curve spread is the difference of two PERCENT real yields, so its natural unit is PERCENT (same as the underlying) — NOT ×100 like a breakeven.  But the desk reads spread *moves* in bps, matching every other rate-change convention.  The wire types + the KPI strip honour this split.

**Why the signed 5-zone regime slider** (vs the 3-zone Normal/Elevated/Extreme used for an unsigned read): a curve spread can be stretched in EITHER direction — extreme steepening vs extreme inversion are different desk signals.  The shared `ZScoreRegimeSlider` renders the signed 5-zone bar; the curve-spread extended view is its canonical consumer (mockup ExtendedCurveSpread design).

**Why separate Short/Long tenor dropdowns with the long-filter**: a curve spread requires two distinct, ordered tenors.  Surfacing them as two dropdowns (vs a single pre-baked "5s10s" pick) lets the PM construct any valid pair on the curve; filtering the long-tenor options to strictly-longer tenors enforces the *long > short* validity rule at the input layer (the backend re-validates regardless).

**Why a typed-detail endpoint** (`/api/v1/rates/detail/real_yield_curve_spread`): per [`methodology_exposure.md §5`](../../../../../docs_revamped/03_standards/methodology_exposure.md) every standalone module ships its own typed bridge.

---

## 4. What would change the design?

- **A real-yield butterfly primitive** (2s5s10s real-yield) lands → ships as a SEPARATE module (three-leg concept), not a knob here; the extended view could cross-link to it.
- **A new linker market** lands → add the curve_family to `CURVE_OPTIONS` + the tenor set to `TENOR_OPTIONS_BY_CURVE` in [`surfaces/curveSpreadShared.ts`](surfaces/curveSpreadShared.ts); add the country caveat to the shared `countryCaveats.ts`.  No shell changes.
- **Date-window input mode** (`start_date` / `end_date` for event-aligned windows) lands in the backend → add the controls; the chart shell already accepts an arbitrary window.
- **The 5-zone regime slider is promoted to other signed-measure tools** (e.g. should `get_real_yield_level_tool` adopt it?) — a shared-infrastructure consistency decision; the `ZScoreRegimeSlider` already lives in the shared barrel so adoption is a one-line import per tool.

---

## 5. Which backend doctrine does this module operationalise?

- **FM1** (module identity) — folder name equals backend `tool_name`.
- **FM3** (surface-tier capability declaration) — claims `generic_runnable` + `custom_build_surface` + `monitor_surface`.
- **FM4** (tier-set parsimony) — overridden by `rendering_density.md §1.2` for the dual-view mandate; Monitor justified by `surface_contract.md §3.4`.
- **FM5** (display metadata) — PM-facing `displayName` / `category` / `oneLineSummary` (per `lifecycle_checklist_template.md` Stage 1C — no "analogue of X" framing).
- **FM7** (pure-spec assembly) — `module.ts` exports a pure value.
- **FM8** (surface-file contract) — `surfaces.buildExtended` + `surfaces.buildCompact` populated; files at canonical paths.
- **FM9** (routing-claim disclosure) — `typedView: null`.
- **FM10** (THESIS discipline) — this file.
- **FM11** (round-trip test) — [`__tests__/module.spec.ts`](__tests__/module.spec.ts) asserts the dual-view contract.
- **FM12** (loader presence) — module imported in [`src/modules/index.ts`](../../index.ts).
- Per the **methodology-exposure standalone-bridge contract** (`methodology_exposure.md §5`): own typed-detail endpoint `/api/v1/rates/detail/real_yield_curve_spread`; own service helper `fetchDetailRealYieldCurveSpread`; own frontend type `RealYieldCurveSpreadOutput`.
- Per the **rendering-density dual-view contract** (`rendering_density.md §1`): both views ship + both wired + files at canonical paths.

---

## Version log

| Version | Date | Change |
|---|---|---|
| v3 | 2026-05-28 | Phase-1 Stage-C dual-view + Monitor implementation per `rendering_density.md`.  Shipped `surfaces/BuildExtended.tsx` (separate short/long tenor dropdowns + long-tenor filter + signed 5-zone regime slider), `surfaces/BuildCompact.tsx`, `surfaces/monitor/RealYieldCurveSpreadWidget.tsx`, and the shared helper `surfaces/curveSpreadShared.ts`.  Tier set updated to `[generic_runnable, custom_build_surface, monitor_surface]`.  Standalone-bridge typed-detail endpoint shipped at `/api/v1/rates/detail/real_yield_curve_spread`.  Brought to parity with `get_real_yield_level_tool`. |
| v2 | 2026-05-26 | Stage 4f — rewrote the body to drop stale "Stage 3 scaffold" framing. |
| v1 | 2026-05-25 | Stage 3 scaffold — runtime tier only, no surfaces. |
