# THESIS — `calculate_inflation_swap_curve_spread_tool`

> Round-1 dispatch module under the dual-view rendering-density contract.  Brought to full parity with the `calculate_breakeven_curve_spread_tool` shape-twin: ships an extended Build view (full canvas for single-tool queries) AND a compact Build view (grid card for multi-tool query DAGs) — both REQUIRED per [`rendering_density.md`](../../../../../docs_revamped/03_standards/rendering_density.md) §1 — plus a Monitor tile.

**Version:** v3 (Round-1 dual-view + Monitor implementation)
**Module spec:** [`module.ts`](module.ts)
**Backend artifact:** `calculate_inflation_swap_curve_spread_tool` (generic_runnable; standalone bridge via `/api/v1/rates/detail/inflation-swap-curve-spread`)
**Tier set:** `[generic_runnable, custom_build_surface, monitor_surface]`
**Category:** `cross_market_rv`
**Mockups:** [`./mockups/Compact.png`](./mockups/Compact.png) + [`./mockups/Extended.png`](./mockups/Extended.png) — committed alongside the module per the mockup-first workflow (depict the EUR 2s10s ZCIS spread on the HICPxT curve).

---

## 1. What surfaces does this module ship?

- **`generic_runnable`** — runtime-status tier; backend ships in `_PRIMITIVE_SPECS`.
- **`custom_build_surface`** — the dual-view contract:
  - **Extended Build view** ([`surfaces/BuildExtended.tsx`](surfaces/BuildExtended.tsx)) — full canvas mounted for single-tool queries.  Controls strip (ZCIS curve + separate Short Tenor / Long Tenor dropdowns + lookback + field), top-right Z-score / Percentile / signed-5-zone-Regime cards, KPI strip (spread bps + bps period changes + z + percentile + 252d range + the two endpoint ZCIS rates for the decomposition), spread-history chart with ±2σ z-score bands, stretch-context panel, methodology card (carrying the load-bearing reference-index / lag / interpolation triple shared by both legs), lineage footer.
  - **Compact Build view** ([`surfaces/BuildCompact.tsx`](surfaces/BuildCompact.tsx)) — grid card mounted as a node body inside multi-tool DAGs (e.g. *"compare US 5s10s ZCIS vs UK 5s10s ZCIS"*).  Headline 3-KPI strip (spread bps + 1d change + z-score), mini-chart with ±2σ/±1.5σ z-score bands, the OTC-ZCIS caveat footer + curve identity chip, click-to-expand affordance.
- **`monitor_surface`** — Monitor bento tile ([`surfaces/monitor/InflationSwapCurveSpreadWidget.tsx`](surfaces/monitor/InflationSwapCurveSpreadWidget.tsx)) showing one same-curve ZCIS tenor spread (curve_family × short_tenor × long_tenor × lookback parameterised; defaults EUR_ZCIS / 2Y / 10Y / 252).  Same-curve ZCIS spreads are a desk daily-glance read per `surface_contract.md §3.4`.  Inherently compact per `rendering_density.md §8`.

---

## 2. What does the user read off each surface?

### Extended Build view

The full investigation canvas for ONE same-curve ZCIS tenor spread.  A PM reads, in order: (a) the title row identifying `<country> <short>s<long>s ZCIS SPREAD` + as-of date + reference-index chip (e.g. `EUR · HICPxT`); (b) the top-right Z-Score / Percentile / **signed Curve-Regime slider** cards — the slider is a 5-zone bar (Extreme Down / Elevated Down / Normal / Elevated Up / Extreme Up) because the spread is a SIGNED measure (upward sloping vs inverted read differently); (c) the KPI strip — current spread (bps), 1d/5d/1m changes (bps), z-score, percentile, 252d high/low (bps), observations count, AND the short + long endpoint ZCIS rates (percent) so the spread decomposition is auditable; (d) the spread-history chart with z-score band overlays; (e) the stretch-context panel framed in ZCIS-implied-forward-inflation curve-shape language; (f) the methodology card (construction formula `(long_zcis_pct − short_zcis_pct) × 100`, the *valid tenor pair: long > short* rule, field, the load-bearing `inflation_index_family` / `index_lag` / `interpolation` / `underlying_index` triple shared by both legs, z-model, units, curve identity); (g) the lineage footer.  The **Long Tenor dropdown is filtered to tenors strictly longer than the Short Tenor**, enforcing the curve-spread validity rule at the input layer.

### Compact Build view

The at-a-glance grid card.  A PM sees this in a multi-tool prompt.  They read THREE numbers + a sparkline: current spread (bps, with the upward-sloping/inverted caption), 1-day change (bps, tone-coloured, with the σ-magnitude subtext), rolling 252d z-score (with regime caption).  The mini-chart shows the spread history with ±2σ / ±1.5σ z-score bands.  The footer carries the *"OTC inflation swap curve; basis to bond BE is a separate primitive."* caveat + the ZCIS curve identity chip (e.g. *"EUR · HICPxT"*).  The expand arrow opens the extended view in a modal.

### Monitor tile

One same-curve ZCIS tenor spread, desk-glanceable: current spread (bps with upward-sloping/inverted caption), 1-day change (bps), 252d percentile, and a high/low range strip with a current marker.  Curve identity + z-score badge in the header.

---

## 3. Why these surfaces and not others?

**Why the dual-view contract** (both extended + compact REQUIRED): per [`rendering_density.md §1.1`](../../../../../docs_revamped/03_standards/rendering_density.md) multi-tool prompts are the default reality.  ZCIS curve spreads are frequently compared cross-curve (US 5s10s ZCIS vs UK 5s10s ZCIS) and against the matching bond-implied breakeven curve spread (the basis between the two IS the canonical desk read), so the compact view earns its keep.  The standard OVERRIDES FM4 parsimony for the dual-view mandate.

**Why these three compact KPIs** (current spread + 1d change + z-score): the desk-canonical "first three numbers" for a forward-inflation curve-spread snapshot — *"where is the ZCIS curve now / did it steepen or flatten today / is the shape stretched?"*.  Alternatives rejected: the two endpoint ZCIS rates (decomposition detail, surfaced in the extended KPI strip); 252d percentile (conveyed by the z-score regime + bands); the curve geometry (year fractions) — irrelevant to the headline read.

**Why the signed 5-zone regime slider** (vs the 3-zone Normal/Elevated/Extreme used for an unsigned read): a curve spread can be stretched in EITHER direction — extreme steepening vs extreme inversion are different desk signals.  The shared `ZScoreRegimeSlider` renders the signed 5-zone bar; the ZCIS-curve-spread extended view consumes it for the same reason its shape-twin `calculate_breakeven_curve_spread_tool` does.

**Why separate Short/Long tenor dropdowns with the long-filter**: a curve spread requires two distinct, ordered tenors.  Surfacing them as two dropdowns (vs a single pre-baked "2s10s" pick) lets the PM construct any valid pair on the chosen ZCIS curve; filtering the long-tenor options to strictly-longer tenors enforces the *long > short* validity rule at the input layer (the backend re-validates regardless via the Pydantic `_short_must_precede_long` validator).

**Why a single "ZCIS Curve" dropdown** (vs separate inflation-index + lag controls): a same-curve ZCIS spread is structurally a same-curve object — the `curve_family` alone uniquely picks the `inflation_index_family` / `index_lag` / `interpolation` triple shared by both legs (the same-curve invariant the backend enforces).  Surfacing those as separate controls would invite cross-index combinations the backend would reject with a controlled error envelope; the input layer collapses them into a single canonical choice instead.  The selected curve's reference-index identity is then surfaced on the extended methodology card so the desk can still read it.

**Why bps units throughout** (vs the percent units of the endpoint ZCIS rates): the spread is reported per backend convention as `(long_zcis_pct − short_zcis_pct) × 100` in bps; the desk reads curve spreads as a bps difference, so the wire / KPI strip / chart all stay in bps — no display-layer ×100 / ÷100 conversions.  The endpoint decomposition row in the extended view surfaces the underlying ZCIS rates in percent for auditability.

**Why a typed-detail endpoint** (`/api/v1/rates/detail/inflation-swap-curve-spread`): per [`methodology_exposure.md §5`](../../../../../docs_revamped/03_standards/methodology_exposure.md) every standalone module ships its own typed bridge.

---

## 4. What would change the design?

- **A cross-curve ZCIS spread primitive** (USD ZCIS 5Y vs EUR ZCIS 5Y, same-tenor across curves) already exists as a SEPARATE primitive (`calculate_cross_market_inflation_swap_spread_tool`); the extended view could cross-link to it.
- **A swap-breakeven basis primitive** (ZCIS rate minus same-tenor linker bond-implied breakeven) → SEPARATE primitive (`calculate_swap_breakeven_basis_simple_tool`, already shipped).
- **A new ZCIS curve family** (e.g. JPY_ZCIS) lands → add the curve to `ZCIS_CURVE_SPREAD_CURVE_OPTIONS` + the tenor set to `ZCIS_CURVE_SPREAD_TENORS_BY_CURVE` in [`surfaces/inflationSwapCurveSpreadShared.ts`](surfaces/inflationSwapCurveSpreadShared.ts).  No shell changes.
- **Date-window input mode** (`start_date` / `end_date` for event-aligned windows) lands in the backend → add the controls; the chart shell already accepts an arbitrary window.
- **The 5-zone regime slider promoted to other signed-measure tools** — a shared-infrastructure consistency decision; the `ZScoreRegimeSlider` already lives in the shared barrel so adoption is a one-line import per tool.

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
- Per the **methodology-exposure standalone-bridge contract** (`methodology_exposure.md §5`): own typed-detail endpoint `/api/v1/rates/detail/inflation-swap-curve-spread`; own service helper `fetchDetailInflationSwapCurveSpread`; own frontend type `InflationSwapCurveSpreadOutput`; `methodology_label` threaded from `current_metrics.methodology_label` (PR10 / P5 — NOT a hardcoded TS literal).
- Per the **rendering-density dual-view contract** (`rendering_density.md §1`): both views ship + both wired + files at canonical paths.
- Per **BUILD_GUIDE.md Stages 4 → 5 → 6**: Stage 4 module spec + Stage 5 standalone bridge endpoint + Stage 6 dual-view + Monitor + Mockups.

---

## Version log

| Version | Date | Change |
|---|---|---|
| v3 | 2026-06-07 | Round-1 dual-view + Monitor implementation per `rendering_density.md`.  Shipped `surfaces/BuildExtended.tsx` (single ZCIS-curve dropdown + separate short/long tenor dropdowns + long-tenor filter + signed 5-zone regime slider), `surfaces/BuildCompact.tsx`, `surfaces/monitor/InflationSwapCurveSpreadWidget.tsx`, and the shared helper `surfaces/inflationSwapCurveSpreadShared.ts`.  Tier set updated to `[generic_runnable, custom_build_surface, monitor_surface]`.  Standalone-bridge typed-detail endpoint shipped at `/api/v1/rates/detail/inflation-swap-curve-spread`.  Brought to parity with `calculate_breakeven_curve_spread_tool`. |
| v2 | 2026-05-26 | Stage 4f — rewrote the body to drop stale "Stage 3 scaffold" framing. |
| v1 | 2026-05-25 | Stage 3 scaffold — runtime tier only, no surfaces. |
