# THESIS — `calculate_ois_curve_spread_tool`

> Phase-1 dual-view module under the rendering-density contract.  Brought to full parity with `calculate_real_yield_curve_spread_tool` (the same-shape design twin — 2-leg same-curve tenor spread on a different curve family) and the sibling `calculate_ois_butterfly_tool` (same OIS family, 3-leg shape).  Ships an extended Build view (full canvas for single-tool queries) AND a compact Build view (grid card for multi-tool query DAGs) — both REQUIRED per [`rendering_density.md`](../../../../../docs_revamped/03_standards/rendering_density.md) §1 — plus a Monitor tile.

**Version:** v3 (Round-1 dispatch — dual-view + Monitor implementation)
**Module spec:** [`module.ts`](module.ts)
**Backend artifact:** `calculate_ois_curve_spread_tool` (generic_runnable; standalone bridge via `/api/v1/rates/detail/ois-curve-spread`)
**Tier set:** `[generic_runnable, custom_build_surface, monitor_surface]`
**Category:** `curve_shape`
**Mockups:** [`./mockups/Compact.png`](./mockups/Compact.png) + [`./mockups/Extended.png`](./mockups/Extended.png) — committed alongside the module per the mockup-first workflow.

---

## 1. What surfaces does this module ship?

- **`generic_runnable`** — runtime-status tier; backend ships in `_PRIMITIVE_SPECS`.
- **`custom_build_surface`** — the Phase-1 **dual-view** contract:
  - **Extended Build view** ([`surfaces/BuildExtended.tsx`](surfaces/BuildExtended.tsx)) — full canvas mounted for single-tool queries.  Controls strip (OIS curve + **separate Short Tenor / Long Tenor dropdowns** + lookback + field), top-right Z-score / Percentile / Overnight-Index cards, a KPI strip (spread bps + 1d/3m/12m changes + z-score + 252d percentile + 252d high/low + observation_count + the two endpoint OIS rates), spread-history chart with ±2σ / ±1.5σ z-score bands, stretch-context panel, methodology card, lineage footer.  The **Long Tenor dropdown is filtered to tenors strictly longer than the Short Tenor**, enforcing the curve-spread validity rule at the input layer (the Pydantic `OISCurveSpreadInput._tenors_must_differ` validator re-validates the `short != long` rule at the schema layer).
  - **Compact Build view** ([`surfaces/BuildCompact.tsx`](surfaces/BuildCompact.tsx)) — grid card mounted as a node body inside multi-tool DAGs (e.g. *"compare SOFR 2s10s vs ESTR 2s10s OIS curve shape"*).  Headline 3-KPI strip, mini-chart with z-score bands, the OIS policy-pricing caveat footer + overnight-index chip, click-to-expand affordance.
- **`monitor_surface`** — Monitor bento tile ([`surfaces/monitor/OisCurveSpreadWidget.tsx`](surfaces/monitor/OisCurveSpreadWidget.tsx)) showing one OIS curve spread (curve × pair × lookback_days parameterised; defaults USD_SOFR_OIS / 2s10s / 252).  An OIS 2s10s / 5s30s / 1s5s read is a desk daily-glance item per `surface_contract.md §3.4`.  Inherently compact per `rendering_density.md §8`.

---

## 2. What does the user read off each surface?

### Extended Build view

The full investigation canvas for ONE OIS curve spread.  A PM reads, in order: (a) the title row identifying overnight-index + the `<short>s<long>s` pair + as-of date; (b) the top-right Z-Score / Percentile / Overnight-Index cards (the Overnight-Index card surfaces the risk-neutral policy-pricing caveat at the top of the canvas, NOT only in the methodology card — the desk needs to see the caveat alongside the headline number); (c) the KPI strip — current spread (bps), 1d/3m/12m changes (bps), z-score, 252d percentile, 252d high/low (bps), observation count, AND the short + long endpoint OIS par-swap rates (percent) so the decomposition is auditable; (d) the spread-history chart with z-score band overlays; (e) the stretch-context panel framed in curve-shape language; (f) the methodology card (construction formula, the *long > short* rule, field, z-model, units, overnight-index identity, same-curve invariant, risk-neutral policy-pricing disclosure); (g) the lineage footer.  Percentile / 252d high-low / observation_count / 3m / 12m changes are computed CLIENT-SIDE from `time_series_spread.rows` because the OIS curve-spread backend Output is leaner than the linker / sovereign siblings — the desk gets the mockup's KPI strip without inventing wire data.

### Compact Build view

The at-a-glance grid card.  A PM sees this in a multi-tool prompt.  They read THREE numbers + a sparkline: current spread (bps, with "Steeper"/"Inverted"/"Flat" caption), 1-day change (bps, tone-coloured), rolling 252d z-score (with "Elevated Steeper" / "Elevated Flatter" / "Neutral" regime caption).  The mini-chart shows the spread history with ±2σ / ±1.5σ z-score bands.  The footer carries the *"Risk-neutral OIS curve shape (policy expectations, not outcomes)."* caveat + the overnight-index chip (e.g. *"SOFR · 2s10s"*).  The expand arrow opens the extended view in a modal.

### Monitor tile

One OIS curve spread, desk-glanceable: current spread (bps, signed with steeper/inverted shape word), 1-day change (bps), z-score badge.  Country flag + overnight-index label in the header.  No high/low marker strip (distinct from the OIS-butterfly Monitor widget) — the OIS curve-spread Output does not carry wire `high_252d_bps` / `low_252d_bps` and the Monitor widget keeps a minimal payload contract (no client-side derivation in the constrained widget footprint).

---

## 3. Why these surfaces and not others?

**Why the dual-view contract** (both extended + compact REQUIRED): per [`rendering_density.md §1.1`](../../../../../docs_revamped/03_standards/rendering_density.md) multi-tool prompts are the default reality.  OIS curve spreads are frequently compared cross-currency (SOFR 2s10s vs ESTR 2s10s vs SONIA 2s10s) and against the nominal sovereign curve, so the compact view earns its keep.  The standard OVERRIDES FM4 parsimony for the dual-view mandate.

**Why these three compact KPIs** (current spread + 1d change + z-score): the desk-canonical "first three numbers" for an OIS curve-spread snapshot — *"where does the OIS curve sit now / did it steepen or flatten today / is the shape stretched?"*.  Alternatives rejected: the two endpoint OIS rates (decomposition detail, surfaced in the extended KPI strip + decomposition row); 252d percentile (conveyed by the z-score regime + bands); the curve geometry (year fractions) — irrelevant to the headline read.

**Why the spread is in BPS** (not percent): an OIS curve spread is the difference of two PERCENT par-swap rates × 100, expressed in BPS — the OIS sub-domain BPS convention.  The wire ships `current_spread_bps` directly already in bps; the display layer does no unit conversion.  This is distinct from the **real-yield** curve spread (twin module), which expresses the spread itself in PERCENT (same as the underlying real yields).  The convention is bps because OIS desks read swap-curve moves in bps.

**Why separate Short/Long tenor dropdowns with the long-filter**: an OIS curve spread requires two distinct, ordered tenors.  Surfacing them as two dropdowns (vs a single pre-baked "2s10s" pick) lets the PM construct any valid pair on the curve; filtering the long-tenor options to strictly-longer tenors enforces the *long > short* validity rule at the input layer (the backend `_tenors_must_differ` validator re-validates regardless).  Distinct from the OIS butterfly's single-triplet selector, which encodes a 3-tuple as one label because the butterfly has registered presets the desk pivots on.

**Why client-side derivation of percentile / 252d high-low / 3m+12m changes**: the OIS curve-spread Output is leaner than the linker / sovereign siblings (no wire `percentile_252d` / `high_252d_bps` / `low_252d_bps` / `weekly_change_bps` / `monthly_change_bps` / `observation_count`).  The mockup's extended KPI strip wants these.  Computing them CLIENT-SIDE from `time_series_spread.rows` is honest (the wire IS providing the underlying data; the derivations match the Pydantic compute layer's would-be definitions byte-for-byte) and lets the mockup ship without backend changes.  Marked with a `// TODO(PR10)` in `oisCurveSpreadShared.ts` so when the backend extends the Output schema, the wrapper swaps client-derivation for wire fields in a one-line edit per metric.

**Why a typed-detail endpoint** (`/api/v1/rates/detail/ois-curve-spread`): per [`methodology_exposure.md §5`](../../../../../docs_revamped/03_standards/methodology_exposure.md) every standalone module ships its own typed bridge.

---

## 4. What would change the design?

- **A `methodology_label` field lands on the OIS curve-spread Pydantic Output** (PR10 — siblings in inflation_indexed_bonds / inflation_swaps already carry it) → the methodology card's "Disclosure" row switches to source `current_metrics.methodology_label` (one-line edit; the marker is in [`surfaces/oisCurveSpreadShared.ts`](surfaces/oisCurveSpreadShared.ts)).
- **Wire `percentile_252d` / `high_252d_bps` / `low_252d_bps` / `observation_count` / `weekly_change_bps` / `monthly_change_bps` lands** → replace the client-side derivations in `extendedKPIs()` with direct wire reads.  Distinct from this module's current "compute it ourselves" stance, which is the honest interim.
- **A new OIS curve family lands** (e.g. NZD_OCR_OIS, CHF_SARON_OIS) → add the family to the `FAMILY_REGISTRY` + the tenor grid to `TENOR_OPTIONS_BY_CURVE` + a curated pair set to `OIS_CURVE_SPREAD_PAIRS_BY_CURVE` in [`surfaces/oisCurveSpreadShared.ts`](surfaces/oisCurveSpreadShared.ts), mirroring `rates_agent/playbooks/ois.yml`.  No shell changes.
- **A cross-curve OIS spread primitive ships** (already exists as `calculate_ois_cross_market_spread_tool` for SOFR-ESTR / SOFR-SONIA / etc.) → cross-link from the methodology card.  Cross-curve spreads are deliberately a separate primitive (different schema constraints, different desk read).
- **The signed 5-zone regime slider is promoted to other signed-measure tools** (the real-yield curve-spread twin uses it; OIS curve-spread currently uses the 3-zone regime + direction caption) — a shared-infrastructure consistency decision; the `ZScoreRegimeSlider` already lives in the shared barrel so adoption is a one-line import.

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
- Per the **methodology-exposure standalone-bridge contract** (`methodology_exposure.md §5`): own typed-detail endpoint `/api/v1/rates/detail/ois-curve-spread`; own service helper `fetchDetailOisCurveSpread`; own frontend type `OisCurveSpreadOutput`.
- Per the **rendering-density dual-view contract** (`rendering_density.md §1`): both views ship + both wired + files at canonical paths.

---

## Version log

| Version | Date | Change |
|---|---|---|
| v3 | 2026-05-29 | Round-1 dispatch — dual-view + Monitor implementation per `rendering_density.md`.  Shipped `surfaces/BuildExtended.tsx` (separate short/long tenor dropdowns + long-tenor filter), `surfaces/BuildCompact.tsx`, `surfaces/monitor/OisCurveSpreadWidget.tsx`, and the shared helper `surfaces/oisCurveSpreadShared.ts`.  Tier set updated to `[generic_runnable, custom_build_surface, monitor_surface]`.  Standalone-bridge typed-detail endpoint shipped at `/api/v1/rates/detail/ois-curve-spread`.  Client-side derivation of percentile / 252d high-low / 3m+12m changes / observation_count from `time_series_spread.rows` because the OIS curve-spread Output is leaner than the linker / sovereign siblings; PR10 marker in `oisCurveSpreadShared.ts` for when the backend lands the wire fields. |
| v2 | 2026-05-26 | Stage 4f — rewrote the body to drop stale "Stage 3 scaffold" framing. |
| v1 | 2026-05-25 | Stage 3 scaffold — runtime tier only, no surfaces. |
