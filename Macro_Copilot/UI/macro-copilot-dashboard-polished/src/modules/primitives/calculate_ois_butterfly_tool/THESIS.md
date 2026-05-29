# THESIS — `calculate_ois_butterfly_tool`

> Round-1 dispatch under the dual-view rendering-density contract.  Brought to full parity with `calculate_inflation_swap_butterfly_tool` (single-curve 3-leg fly sibling) and `calculate_real_yield_butterfly_tool`: ships an extended Build view (full canvas for single-tool queries) AND a compact Build view (grid card for multi-tool query DAGs) — both REQUIRED per [`rendering_density.md`](../../../../../docs_revamped/03_standards/rendering_density.md) §1 — plus a Monitor tile.

**Version:** v3 (Round-1 dual-view + Monitor implementation)
**Module spec:** [`module.ts`](module.ts)
**Backend artifact:** `calculate_ois_butterfly_tool` (generic_runnable; standalone bridge via `/api/v1/rates/detail/ois-butterfly`)
**Tier set:** `[generic_runnable, custom_build_surface, monitor_surface]`
**Category:** `cross_market_rv`
**Mockups:** [`./mockups/Compact.png`](./mockups/Compact.png) + [`./mockups/Extended.png`](./mockups/Extended.png) — committed alongside the module per the mockup-first workflow.

---

## 1. What surfaces does this module ship?

- **`generic_runnable`** — runtime-status tier; backend ships in `_PRIMITIVE_SPECS`.
- **`custom_build_surface`** — the Phase-1 **dual-view** contract:
  - **Extended Build view** ([`surfaces/BuildExtended.tsx`](surfaces/BuildExtended.tsx)) — full canvas mounted for single-tool queries.  Controls strip (single **OIS Curve** dropdown + single **Triplet** dropdown + lookback + field), top-right Z-score / Percentile / Overnight-Index cards (the third names the overnight-index identity inline — e.g. *"USD · SOFR"* + the `Risk-neutral policy pricing (policy path, not outcomes).` caveat), a bps-scale KPI strip plus a decomposition row (three endpoint OIS rates in PERCENT + two component wing spreads in BPS), butterfly-history chart with ±2σ z-score bands, stretch-context panel, methodology card sourced from the construction formula + the sign convention + the curve identity, lineage footer.
  - **Compact Build view** ([`surfaces/BuildCompact.tsx`](surfaces/BuildCompact.tsx)) — grid card mounted as a node body inside multi-tool query DAGs (e.g. *"compare US 2-5-10 OIS fly vs UK 2-5-10 OIS fly"*).  Headline 3-KPI strip (FLY (bps) / 1D CHANGE / Z-SCORE — 1D change carries a percent-of-belly subtext per the mockup), mini-chart with z-score bands, the risk-neutral-policy-pricing caveat footer + OIS-family chip, click-to-expand affordance.
- **`monitor_surface`** — Monitor bento tile ([`surfaces/monitor/OisButterflyWidget.tsx`](surfaces/monitor/OisButterflyWidget.tsx)) showing one OIS butterfly (curve × triplet × lookback parameterised; defaults USD_SOFR_OIS / 2s5s10s / 252).  OIS butterflies are the desk-canonical curve-shape RV read on the front-end policy-expectations curve; inherently compact per `rendering_density.md §8`.

---

## 2. What does the user read off each surface?

### Extended Build view

The full investigation canvas for ONE same-curve OIS butterfly.  A PM reads, in order: (a) the title row identifying the overnight index + triplet (e.g. *"SOFR 2-5-10 OIS BUTTERFLY · USD"*) + as-of date; (b) the top-right Z-Score / Percentile / Overnight-Index cards (the last names the resolved overnight index — e.g. *"USD · SOFR"* — and carries the *"Risk-neutral policy pricing (policy path, not outcomes)."* caveat inline); (c) the bps-scale KPI strip — current butterfly (bps directly from the backend) with belly-rich / belly-cheap caption, 1d change (bps), z-score with regime+direction caption, percentile, 252d high/low (bps).  NB the OIS butterfly Output is leaner than the linker / ZCIS butterfly siblings — no weekly / monthly change fields, no observation_count — so the strip surfaces only what the wire ships; (d) the decomposition row — three endpoint OIS rates (`short_tenor_rate`, `belly_tenor_rate`, `long_tenor_rate` in %) + two wing spreads (`wing_short_bps`, `wing_long_bps` in bps) so the desk can audit `(2 × belly − short − long) × 100` on the same screen; (e) the butterfly-history chart with z-score band overlays; (f) the stretch-context panel; (g) the methodology card (construction formula, sign convention, triplet with butterfly_label, field, z-model, the risk-neutral-policy-pricing caveat, overnight-index identity, weighting choice, same-curve invariant); (h) the lineage footer.  The single **OIS Curve** dropdown picks the curve_family (single-curve primitive — no nominal counterparty, no cross-curve mix); the single **Triplet** dropdown enforces all-three-distinct ordering by only offering registered triplets per curve.

### Compact Build view

The at-a-glance grid card.  A PM sees this in a multi-tool prompt.  They read THREE numbers + a sparkline: FLY (bps) (signed bps directly from the backend, with Belly Rich / Belly Cheap caption — the canonical desk read), 1-day change (bps, tone-coloured, with a `(±X.XX%)` subtext expressing the daily move as a percent of current belly OIS rate, mockup-faithful), rolling 252d z-score (with regime+direction caption — Neutral / Elevated Cheap / Extreme Rich / etc).  The mini-chart shows the butterfly history with ±2σ / ±1.5σ z-score bands.  The footer carries the *"Risk-neutral policy pricing (policy path, not outcomes)."* caveat + the overnight-index chip (e.g. *"🇺🇸 SOFR (USD)"*).  The expand arrow opens the extended view in a modal.

### Monitor tile

One OIS butterfly, desk-glanceable: current butterfly (bps with belly-rich / belly-cheap qualifier), 1-day change (bps), 252d percentile, and a high/low range strip with a current marker.  Country flag + z-score badge in the header.  The risk-neutral-policy-pricing caveat surfaces as the footer line so a glance never misreads the curvature object.

---

## 3. Why these surfaces and not others?

**Why the dual-view contract** (both extended + compact REQUIRED): per [`rendering_density.md §1.1`](../../../../../docs_revamped/03_standards/rendering_density.md) multi-tool prompts are the default reality; a tool that ships only an extended view falls back to a generic artifact-type card in multi-tool DAGs.  OIS butterflies are commonly compared across overnight-index markets (*"US 2-5-10 OIS fly vs UK 2-5-10 OIS fly"*) and across triplets (*"US 2-5-10 vs US 5-10-30 OIS fly"*), so the compact view earns its keep immediately.  The standard OVERRIDES FM4 parsimony for the dual-view mandate.

**Why these three compact KPIs** (FLY (bps) + 1D CHANGE + Z-SCORE (252D)): they are the desk-canonical "first three numbers" a PM reads off an OIS butterfly snapshot — *"where is the curvature now / how much did it move today / is this stretched?"*.  Alternatives considered + rejected: belly OIS rate outright (the curvature object IS the snapshot — surfacing the belly rate instead would hide the construction); 252d percentile (already conveyed by the z-score regime + band overlay); the two wing spreads (decomposition detail, surfaced in the extended view's decomposition row, not headline).  The 1D-change cell carries a `(±X.XX%)` subtext per the mockup so the desk reads the move both in absolute bps AND in proportion to the current belly OIS rate.

**Why a SINGLE OIS-Curve dropdown** (not a leg pair): this primitive is single-curve by construction — the Pydantic Input takes one `curve_family` (closed enum sourced from `rates_agent/playbooks/ois.yml`) and three tenors, with the same-curve invariant enforced at the input layer.  Distinct from `calculate_ois_cross_market_spread`, which crosses two OIS families at a shared tenor.  Surfacing a leg-pair UI would invite invalid input shapes; the single dropdown honours the actual contract.

**Why a SINGLE Triplet dropdown** (not three independent tenor dropdowns): the schema's `_tenors_must_all_differ` validator rejects duplicate or repeated triplets at the input layer.  Surfacing three independent dropdowns would invite invalid orderings; the single dropdown of registered triplet presets (per curve) makes invalid orderings unreachable.

**Why the wire already ships BPS (and we don't unit-convert)**: the OIS sub-domain established the BPS convention for curve-shape views via `calculate_ois_curve_spread` / `calculate_ois_cross_market_spread`; the butterfly inherits it.  The wire ships `current_butterfly_bps`, `daily_change_bps`, `high_252d_bps`, `wing_short_bps`, `wing_long_bps` all in BPS directly.  The display layer renders BPS unchanged.  Only the three per-leg endpoint rates (`short_tenor_rate`, `belly_tenor_rate`, `long_tenor_rate`) stay in PERCENT — that's the natural unit for an OIS par-swap rate level (not a spread or curvature).

**Why the risk-neutral-policy-pricing caveat is REQUIRED** (not hidden): misreading an OIS butterfly as a *forecast* of central-bank policy outcomes is a real desk error.  OIS curves price the EXPECTED POLICY PATH under the risk-neutral measure — they encode how the market is positioning, not what central banks will decide.  Per `rendering_density.md §2.2` + §12 the methodology caveat must be reachable in the compact view; here it's the canonical one-liner in the footer ("Risk-neutral policy pricing (policy path, not outcomes).").

**Why the methodology card sources the disclosure from a per-tool constant** (not the wire `methodology_label`): unlike the linker / ZCIS butterfly siblings, the OIS butterfly backend Output does NOT currently carry a `current_metrics.methodology_label` field — the OIS sub-domain hasn't caught up to the PR10 wire-honesty-disclosure convention yet.  The methodology card surfaces the canonical risk-neutral-policy-pricing caveat from `oisButterflyShared.ts` with an explicit `TODO(PR10)` marker; when the backend ships the field, the "Disclosure" row switches to consume the wire (one-line edit).  All OTHER methodology rows (construction formula, sign convention, weighting choice, same-curve invariant, z-window, trailing range, overnight-index identity) source from the wire payload + the request context directly.

**Why no z-score override controls in the extended view**: the OIS butterfly Pydantic schema does NOT expose `z_score_window_days` / `z_score_min_periods` / `z_score_ddof` as input fields — they fall through to the YAML at every call site (consistent with the sovereign / breakeven / real-yield / ZCIS butterfly siblings).  Surfacing them as controls would silently shadow the YAML; the extended view honours the actual contract instead.

**Why a typed-detail endpoint** (`/api/v1/rates/detail/ois-butterfly`): per [`methodology_exposure.md §5`](../../../../../docs_revamped/03_standards/methodology_exposure.md) every standalone module ships its own typed bridge; the generic `/run` route stays the LLM-facing surface.

---

## 4. What would change the design?

- **A DV01-weighted / duration-neutral OIS butterfly primitive** lands → it ships as a SEPARATE module (different concept), not a knob on this one; the extended view could then offer a side-by-side "fixed-weight vs DV01-weight" framing.  (config.yaml's `planned_extensions` already names this — currently locked at the (-1, +2, -1) simple-butterfly weighting because the repo does not yet ingest DV01 metadata for OIS pillars.)
- **A PCA-weighted OIS butterfly primitive** lands → same separate-primitive treatment.
- **The backend ships `current_metrics.methodology_label`** on the OIS butterfly Output (PR10 catch-up) → the methodology card's "Disclosure" row switches to source from the wire (one-line edit in `oisButterflyShared.ts:buildMethodologyRows`).
- **A new OIS market lands** with full overnight-index name promotion (JPY_TONA_OIS / AUD_AONIA_OIS / CAD_CORRA_OIS) — currently `JPY_OIS` / `AUD_OIS` / `CAD_OIS` per the playbook's actual labels; honest disclosure documented under config.yaml's `planned_extensions`.  When the longer suffixes land, update `FAMILY_REGISTRY` in [`surfaces/oisButterflyShared.ts`](surfaces/oisButterflyShared.ts).  No shell changes.
- **The trailing range window is promoted from 252 → configurable** (config.yaml `planned_extensions` already names this) → expose `trailing_range_window_days` on the schema, add an "Advanced" control on the extended view, and update the methodology row's "Trailing range" line.
- **The desk wants per-row historical decomposition** (short/belly/long levels per trade date) → surface from the bespoke `time_series` rows if the schema is extended; today the per-row shape only carries `butterfly_bps + z_score`.

---

## 5. Which backend doctrine does this module operationalise?

- **FM1** (module identity) — folder name equals backend `tool_name`.
- **FM3** (surface-tier capability declaration) — claims `generic_runnable` + `custom_build_surface` + `monitor_surface`.
- **FM4** (tier-set parsimony) — overridden by `rendering_density.md §1.2` for the dual-view mandate; Monitor is justified by `surface_contract.md §3.4` eligibility (desk-canonical curve-shape RV read on the policy-expectations curve).
- **FM5** (display metadata) — `displayName` / `category` / `oneLineSummary` are PM-facing (per `lifecycle_checklist_template.md` Stage 1C).
- **FM7** (pure-spec assembly) — `module.ts` exports a pure value.
- **FM8** (surface-file contract) — `surfaces.buildExtended` + `surfaces.buildCompact` populated; files at canonical paths.
- **FM9** (routing-claim disclosure) — `typedView: null`; this module owns its own full surfaces.
- **FM10** (THESIS discipline) — this file.
- **FM11** (round-trip test) — [`__tests__/module.spec.ts`](__tests__/module.spec.ts) asserts the dual-view contract.
- **FM12** (loader presence) — module imported in [`src/modules/index.ts`](../../index.ts).
- **PR10 / P5** (methodology label sourcing) — the methodology card surfaces the wire-derivable rows (construction / sign convention / weighting / curve identity / z-window / trailing range) from `data.current_metrics` directly; the "Disclosure" row sources the canonical risk-neutral-policy-pricing caveat from the per-tool shared file with a `TODO(PR10)` marker for when the backend ships `methodology_label` (today the OIS butterfly Output lacks the field — the OIS sub-domain hasn't caught up to PR10 yet, distinct from the linker / inflation-swap siblings).
- Per the **methodology-exposure standalone-bridge contract** (`methodology_exposure.md §5`): own typed-detail endpoint `/api/v1/rates/detail/ois-butterfly`; own service helper `fetchDetailOisButterfly`; own frontend type `OisButterflyOutput`.
- Per the **rendering-density dual-view contract** (`rendering_density.md §1`): both views ship + both wired in `module.ts.surfaces` + files at canonical paths.

---

## Mockup conformance

The **Compact mockup** shows `OIS Butterfly · SNAPSHOT` header with 🇺🇸 flag, `SOFR · 2-5-10 OIS FLY` identity + `USD SOFR OIS Curve Curvature` subtitle, the three KPIs (FLY (bps) `+8.4 bps` with "Belly Cheap" caption, 1D CHANGE `+1.9 bps` with `(+0.23%)` subtext, Z-SCORE (252D) `+1.84` with "Elevated Cheap" caption), a butterfly history chart with ±2σ / ±1.5σ band overlays, the *"Risk-neutral policy pricing (policy path, not outcomes)."* footer caveat, and the `🇺🇸 SOFR (USD)` chip.  The implementation reproduces all of these via the shared `BuildCompactShell` + the per-tool `compactKPIs` helper.  The percent-of-belly subtext is reconstructed from `bps / (belly_tenor_rate × 100)` so the desk gets the equivalent percent move without a second tool call.

The **Extended mockup** shows `SOFR 2-5-10 OIS BUTTERFLY · USD` with top-right Z-score `+1.84` (Elevated Cheap) / Percentile `84th` cards, a bps-scale KPI strip (BUTTERFLY `+8.4 bps` / 1D `+1.9 bps` / Z-SCORE `+1.84` / PERCENTILE `84th` / 252D HIGH / 252D LOW), an OIS decomposition row, the chart with z-bands, the stretch-context panel, and the methodology card.  The implementation reproduces this structure end-to-end via the shared `BuildExtendedShell` + the per-tool `extendedKPIs` + `decompositionKPIs` + `buildMethodologyRows` helpers.

---

## Version log

| Version | Date | Change |
|---|---|---|
| v3 | 2026-05-29 | Round-1 dual-view + Monitor implementation per `rendering_density.md`.  Shipped `surfaces/BuildExtended.tsx`, `surfaces/BuildCompact.tsx`, `surfaces/monitor/OisButterflyWidget.tsx`, and the shared helper `surfaces/oisButterflyShared.ts`.  Tier set updated to `[generic_runnable, custom_build_surface, monitor_surface]`.  Standalone-bridge typed-detail endpoint shipped at `/api/v1/rates/detail/ois-butterfly`.  Brought to parity with `calculate_inflation_swap_butterfly_tool` (single-curve 3-leg fly sibling) and `calculate_real_yield_butterfly_tool`.  Backend ships butterfly + 1d change + range + wing spreads already in BPS — no unit conversion needed.  NB the OIS butterfly Output is leaner than the linker / ZCIS butterfly siblings (no weekly / monthly change fields, no observation_count, no methodology_label); the methodology card surfaces the canonical risk-neutral-policy-pricing caveat from the per-tool shared file with a `TODO(PR10)` marker for when the backend catches up. |
| v2 | 2026-05-26 | Stage 4f — rewrote the body to drop stale "Stage 3 scaffold" framing and answer Q2–Q5 for real. |
| v1 | 2026-05-25 | Stage 3 scaffold — runtime tier only, no surfaces. |
