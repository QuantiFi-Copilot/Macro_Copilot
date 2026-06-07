# THESIS — `policy_futures_get_futures_butterfly_simple_tool`

> Dispatch-1 dual-view build under the rendering-density contract.  Ships an extended Build view (full canvas for single-tool queries) AND a compact Build view (grid card for multi-tool query DAGs) — both REQUIRED per [`rendering_density.md`](../../../../../docs_revamped/03_standards/rendering_density.md) §1 — plus a Monitor tile.

**Version:** v3 (Dispatch-1 dual-view + Monitor implementation)
**Module spec:** [`module.ts`](module.ts)
**Backend artifact:** `policy_futures_get_futures_butterfly_simple_tool` (generic_runnable; standalone bridge via `/api/v1/rates/detail/policy-futures-butterfly`)
**Tier set:** `[generic_runnable, custom_build_surface, monitor_surface]`
**Category:** `curvature`
**Mockups:** [`./mockups/Compact.png`](./mockups/Compact.png) + [`./mockups/Extended.png`](./mockups/Extended.png) — committed alongside the module per the mockup-first workflow.

---

## 1. What surfaces does this module ship?

- **`generic_runnable`** — runtime-status tier; backend ships this primitive in `_PRIMITIVE_SPECS` (per the catalog tool-19 audit: backend folder `rates_agent/policy_futures/tools/futures_butterfly_simple/` + MCP wrapper `get_futures_butterfly_simple_tool` at `rates_agent/policy_futures/mcp_server.py:795` + curated migration `database/migrations/2026-06-07_phase1_policy_futures_get_futures_butterfly_simple_curated.sql`).
- **`custom_build_surface`** — the Phase-1 **dual-view** contract:
  - **Extended Build view** ([`surfaces/BuildExtended.tsx`](surfaces/BuildExtended.tsx)) — full canvas mounted for single-tool queries.  Controls strip (single **Curve Family** dropdown + single **Triplet** dropdown + lookback + field), top-right Z-Score / Percentile / Strip-Context cards (the third names the resolved curve identity inline — e.g. *"SOFR · 1-2-3"* — plus the per-curve regime label), a bps-scale headline KPI strip plus a decomposition row (three per-leg implied rates in PERCENT with both master stems and current-front underlyings inline), butterfly-history chart with ±2σ z-score bands, stretch-context panel, methodology card sourced from `methodology_disclosure` on the wire + the construction / sign-convention / weighting context, lineage footer.
  - **Compact Build view** ([`surfaces/BuildCompact.tsx`](surfaces/BuildCompact.tsx)) — grid card mounted as a node body inside multi-tool query DAGs (e.g. *"compare SFR 1-2-3 vs SFR 2-3-4 fly"*).  Headline 3-KPI strip (FLY (BPS) / 1D CHANGE (BPS) — 1D change carries a percent-of-body subtext per the mockup / Z-SCORE (252D)), mini-chart with z-score bands, the 100-minus-rate caveat footer + curve-family chip, click-to-expand affordance.
- **`monitor_surface`** — Monitor bento tile ([`surfaces/monitor/PolicyFuturesButterflyWidget.tsx`](surfaces/monitor/PolicyFuturesButterflyWidget.tsx)) showing one STIR strip butterfly (curve_family × triplet × lookback parameterised; defaults SOFR_FUT / 1-2-3 / 252).  STIR strip butterflies are the desk-canonical front-end policy-curvature read; inherently compact per `rendering_density.md §8`.

---

## 2. What does the user read off each surface?

### Extended Build view

The full investigation canvas for ONE same-curve STIR butterfly.  A PM reads, in order:

1. The title row identifying the curve + triplet + flag (e.g. *"SFR 1-2-3 Butterfly · 🇺🇸"* with subtitle *"US Fed SOFR strip — body − 0.5 × (wing_short + wing_long), implied-rate axis"*) + the as-of date.
2. The top-right Z-Score / Percentile / Strip-Context cards (the last names the resolved curve identity — *"SOFR · 1-2-3"* — plus the regime caveat *"Compounded daily RFR (SOFR / SONIA) — disclosure-only label"* OR *"Unsecured 3M term IBOR (Euribor)"*).
3. The bps-scale headline KPI strip — current butterfly (bps, multiplied from the wire's PERCENT POINTS) with belly-cheap / belly-rich caption, 1d change (bps), z-score with regime+direction caption (Elevated Cheap / Extreme Rich / Neutral), percentile, 252d high/low (bps), observation_count.
4. The decomposition row — three per-leg implied rates (`implied_rate_pct_wing_short` / `implied_rate_pct_body` / `implied_rate_pct_wing_long` in %) each labelled with the strip-slot master stem (e.g. *"WING SHORT (SFR1)"*) and carrying the current-front underlying contract (e.g. *"SFRM26"*) inline as a subtext line — so the desk can audit `body − 0.5 × (wing_short + wing_long)` on the same screen AND see exactly which underlying contracts each strip slot resolves to as_of today.
5. The butterfly-history chart with z-score band overlays.
6. The stretch-context panel.
7. The methodology card (construction formula, sign convention, triplet with `butterfly_label`, curve family, underlying contracts, quote convention, short-rate regime, weighting choice, field, z-window, trailing range, window, observations, and the load-bearing **`methodology_disclosure`** flowing verbatim from the backend wire).
8. The lineage footer.

The single **Curve Family** dropdown picks the curve_family (single-curve primitive — distinct from `policy_futures_get_futures_cross_market_spread`, which crosses two families).  The single **Triplet** dropdown enforces the `wing_short < body < wing_long` invariant by only offering registered triplets per curve.

### Compact Build view

The at-a-glance grid card.  A PM sees this in a multi-tool prompt.  They read THREE numbers + a sparkline:

- **FLY (BPS)** — signed bps (butterfly_value_pct × 100) with Belly cheap / Belly rich / Flat caption — the canonical desk read.
- **1D CHANGE (BPS)** — bps, tone-coloured, with a `(±X.XX%)` subtext expressing the daily move as a percent of current body implied rate (mockup-faithful).
- **Z-SCORE (252D)** — signed z + regime+direction caption (Neutral / Elevated Cheap / Extreme Rich / etc.).

The mini-chart shows the butterfly history with ±2σ / ±1.5σ z-score bands.  The footer carries the *"Implied-rate bps. Underlying STIR futures quote inverse (100-minus-rate)."* caveat + the curve-family chip (e.g. *"🇺🇸 SOFR"*).  The expand arrow opens the extended view in a modal.

### Monitor tile

One STIR butterfly, desk-glanceable: current butterfly (bps with belly-cheap / belly-rich qualifier), 1-day change (bps), 252d percentile, and a high/low range strip with a current marker on the bps axis.  Country flag + z-score badge in the header.  The implied-rate / 100-minus-rate caveat surfaces as a single-line footer so a glance never misreads the unit / sign convention.

---

## 3. Why these surfaces and not others?

**Why the dual-view contract** (both extended + compact REQUIRED): per [`rendering_density.md §1.1`](../../../../../docs_revamped/03_standards/rendering_density.md) multi-tool prompts are the default reality; a tool that ships only an extended view falls back to a generic artifact-type card in multi-tool DAGs.  STIR butterflies are commonly compared across triplets (*"SFR 1-2-3 vs SFR 2-3-4"*) and across curve families (*"SFR 1-2-3 vs ER 1-2-3"*) so the compact view earns its keep immediately.  Standard OVERRIDES FM4 parsimony for the dual-view mandate.

**Why these three compact KPIs** (FLY (BPS) + 1D CHANGE (BPS) + Z-SCORE (252D)): they are the desk-canonical "first three numbers" a PM reads off a STIR butterfly snapshot — *"where is the curvature now / how much did it move today / is this stretched?"*.  Alternatives considered + rejected:

- **Body implied rate outright** — the curvature object IS the snapshot; surfacing the body rate instead would hide the construction.  The extended view's decomposition row carries the per-leg rates for desk audit.
- **252d percentile as a separate cell** — already conveyed by the z-score regime + band overlay; surfacing it twice would consume one of the three precious cells without adding read.
- **The two wing implied rates** — decomposition detail, surfaced in the extended view's decomposition row, not headline.
- **The raw 4-row mockup KPI deck (BUTTERFLY · 1D CHG · Z-SCORE · OBSERVATIONS) plus the 5-cell per-leg block** — would require extending BuildCompactShell with a `secondaryKpis` slot, touching 10+ shipped compact views.  Deferred per the Option-(c) precedent — see Mockup conformance section.

The 1D-change cell carries a `(±X.XX%)` subtext per the mockup so the desk reads the move both in absolute bps AND in proportion to the current body implied rate.

**Why a SINGLE Curve-Family dropdown** (not a leg pair): this primitive is single-curve by construction — the Pydantic Input takes one `curve_family` plus three `strip_position_*` fields, with the same-curve invariant baked in by the strip-slot resolver.  Distinct from `policy_futures_get_futures_cross_market_spread`, which crosses two families at a shared strip slot.  Surfacing a leg-pair UI would invite invalid input shapes; the single dropdown honours the actual contract.

**Why a SINGLE Triplet dropdown** (not three independent strip-position dropdowns): the schema's `_strip_positions_must_be_ordered` validator rejects any unordered or duplicate ordering at the input layer.  Surfacing three independent dropdowns would invite invalid orderings; the single dropdown of registered triplet presets (per curve) makes invalid orderings unreachable.

**Why the wire ships PERCENT POINTS** (and we multiply by 100 to render bps): the policy-futures sub-domain stays in PERCENT POINTS on implied-rate-derived objects (matches the `implied_rate_pct` / `spread_implied_rate_pct` siblings).  The sovereign / OIS butterfly's `_bps` suffix convention does NOT apply here.  The display layer multiplies the wire value by 100 to deliver the bps headline a desk reader expects for a 3-strip-slot curvature.  This conversion is centralised in `futuresButterflySimpleShared.ts:pctToBps` so the three surfaces cannot drift.

**Why the 100-minus-rate caveat is REQUIRED** (not hidden): misreading the wire as a price-axis number is a real desk error — the underlying STIR futures quote 100-minus-rate (`inverse_priced` = true for SFR / ER / SFI), so UP raw-price moves correspond to DOWN implied-rate moves.  The butterfly construction is on the IMPLIED-RATE axis (where the sign convention is unambiguous), so the wire is sign-aligned with the desk-recognised "body cheap" reading — but the load-bearing fact a reader must remember is that the underlying contracts are inverse-priced.  Per `rendering_density.md §2.2` + §12 the methodology caveat must be reachable in the compact view; here it's the canonical one-liner in the footer (*"Implied-rate bps. Underlying STIR futures quote inverse (100-minus-rate)."*).

**Why the methodology card sources the disclosure from the wire** (PR10 / P5 compliance): the policy-futures butterfly backend ships `methodology_disclosure` on every response (composed at compute() time, sourced from `config.yaml` + the per-curve regime label).  The methodology card's "Disclosure" row consumes that field verbatim so YAML edits + per-curve regime swaps flow to runtime.  Distinct from the OIS butterfly sibling where the wire does NOT yet carry the field (the OIS sub-domain hasn't caught up to PR10) — the policy_futures domain shipped it from day one per ADR 0013.

**Why no z-score override controls in the extended view**: the policy-futures butterfly Pydantic schema does NOT expose `z_score_window_days` / `z_score_min_periods` / `z_score_ddof` as input fields — they fall through to the YAML at every call site (consistent with the sovereign / linker / OIS / ZCIS butterfly siblings).  Surfacing them as controls would silently shadow the YAML; the extended view honours the actual contract instead.

**Why a typed-detail endpoint** (`/api/v1/rates/detail/policy-futures-butterfly`): per [`methodology_exposure.md §5`](../../../../../docs_revamped/03_standards/methodology_exposure.md) every standalone module ships its own typed bridge; the generic `/run` route stays the LLM-facing surface.

---

## 4. What would change the design?

- **A DV01-weighted / regression-fitted STIR butterfly primitive** lands → it ships as a SEPARATE module (different concept), not a knob on this one; the extended view could then offer a side-by-side "fixed 50-50 vs DV01 vs regression-fitted" framing.  (config.yaml's `planned_extensions` names this — currently the FIXED 50-50 weighting is locked because the policy_futures sub-domain does not yet ingest DV01 metadata for STIR contracts.)
- **A meeting-by-meeting policy-path butterfly primitive** lands → SEPARATE module.  The methodology disclosure on this primitive explicitly REFUSES that framing (per `config.yaml`) — `WIRP`-style per-meeting reads are a different object.
- **A new V1 policy-futures market lands** (e.g. AUD CASH RATE futures, CAD CORRA futures, JPY TONA futures) → update `CURVE_REGISTRY` in [`surfaces/futuresButterflySimpleShared.ts`](surfaces/futuresButterflySimpleShared.ts) + add registered triplets to `BUTTERFLY_TRIPLETS_BY_CURVE`.  No shell changes.
- **A backend-side `weekly_change_butterfly_value_pct` / `monthly_change_butterfly_value_pct`** lands → expose in the extended KPI strip (the wire is currently leaner than the linker / ZCIS butterfly siblings — no weekly / monthly change fields).
- **A persistent per-row decomposition** (per-leg implied rates per trade date) → surface from the bespoke `time_series` rows if the schema is extended; today the per-row shape only carries `butterfly_value_pct + z_score`.
- **The catalog promotes the wire from PERCENT POINTS to BPS** at the Pydantic schema layer → drop the `pctToBps` helper; the display layer reads the wire unchanged.

---

## 5. Which backend doctrine does this module operationalise?

- **FM1** (module identity) — folder name equals the frontend module identity (`policy_futures_get_futures_butterfly_simple_tool`); the MCP function inside `policy_futures/mcp_server.py` is the unprefixed `get_futures_butterfly_simple_tool` and is bridged through `KNOWN_TOOL_ALIASES` in [`src/lib/toolNames.ts`](../../../lib/toolNames.ts) per the policy_futures naming-divergence precedent.
- **FM3** (surface-tier capability declaration) — claims `generic_runnable` + `custom_build_surface` + `monitor_surface`.
- **FM4** (tier-set parsimony) — overridden by `rendering_density.md §1.2` for the dual-view mandate; Monitor is justified by `surface_contract.md §3.4` eligibility (desk-canonical front-end policy-curvature read).
- **FM5** (display metadata) — `displayName` / `category` / `oneLineSummary` are PM-facing (per `lifecycle_checklist_template.md` Stage 1C).
- **FM7** (pure-spec assembly) — `module.ts` exports a pure value.
- **FM8** (surface-file contract) — `surfaces.buildExtended` + `surfaces.buildCompact` populated; files at canonical paths.
- **FM9** (routing-claim disclosure) — `typedView: null`; this module owns its own full surfaces.
- **FM10** (THESIS discipline) — this file.
- **FM11** (round-trip test) — [`__tests__/module.spec.ts`](__tests__/module.spec.ts) asserts the dual-view contract.
- **FM12** (loader presence) — module imported in [`src/modules/index.ts`](../../index.ts).
- **PR10 / P5** (methodology label sourcing) — the methodology card's "Disclosure" row sources from `data.methodology_disclosure` directly (NOT a hardcoded TS literal).  All other methodology rows source from `data.current_metrics` + the request context.
- Per the **methodology-exposure standalone-bridge contract** (`methodology_exposure.md §5`): own typed-detail endpoint `/api/v1/rates/detail/policy-futures-butterfly`; own service helper `fetchDetailPolicyFuturesButterfly`; own frontend type `FuturesButterflySimpleOutput`.
- Per the **rendering-density dual-view contract** (`rendering_density.md §1`): both views ship + both wired in `module.ts.surfaces` + files at canonical paths.

---

## Mockup conformance

The **Compact mockup** shows `SFR · 1-2-3 · WHITES` identity row with 🇺🇸 flag + `STIR` status pill + a `SOFR Strip Butterfly (Implied Rate)` subtitle; a 3-cell mockup KPI deck (FLY (BPS) `+8.4 bps` with "Belly cheap" caption, 1D CHANGE `+1.9 bps` with `(+0.23%)` subtext, Z-SCORE (252D) `+1.84σ` with "Elevated Cheap" caption); a butterfly history chart with ±2σ / ±1.5σ / 0-line band overlays; the *"Implied-rate bps. Underlying STIR futures quote inverse (100-minus-rate)."* footer caveat; and the as-of timestamp + "View full analysis →" + "Fresh" badge in the footer.  The implementation reproduces all of these via the shared `BuildCompactShell` + the per-tool `compactKPIs` helper.  The percent-of-body subtext is reconstructed from `bps / (body_implied_rate × 100)` so the desk gets the equivalent percent move without a second tool call.

**Compact density step-down (Option (c) precedent — catalog design_guardrail #5):** the mockup shell-standard density matches the implementation exactly here (3 KPIs, single identity, sparkline, caveat, expand affordance).  No step-down was required — unlike the sibling `policy_futures_get_futures_price_level_tool` where the mockup carried 5+ KPIs that had to be deferred to the extended view, the butterfly mockup hews to the shell-standard 3-KPI density.  All headline elements (FLY / 1D CHANGE / Z-SCORE) match the mockup's primary deck verbatim.

The **Extended mockup** shows `SFR 1-2-3 OIS Butterfly` with 🇺🇸 flag + `STIR Strip (Implied Rate)` subtitle, top-right Z-Score `+1.84` (Elevated Cheap) / Percentile `84th` / Federal Reserve (FOMC) regime cards, a bps-scale headline KPI strip (BUTTERFLY `+8.4 bps` / 1D CHANGE `+1.84 bps` / 5D / 1M / Z-SCORE `+1.84` / PERCENTILE `84th` / WHITES-REDS / 252D HIGH `+14.2 bps` / 252D LOW `−11.5 bps` / OBSERVATIONS `365`), a per-leg DECOMPOSITION row with the three implied rates + master stems + current-front underlyings, the chart with z-bands + history strip + freshness indicator, the stretch-context panel, and the full methodology card.  The implementation reproduces this structure end-to-end via the shared `BuildExtendedShell` + the per-tool `extendedKPIs` + `decompositionKPIs` + `buildMethodologyRows` helpers.  The mockup's `5D` / `1M` change cells are NOT present (the backend Output does not carry weekly / monthly change fields — the wire is leaner than the linker / ZCIS butterfly siblings); their absence is documented in §4 "What would change the design" so a future Output extension can populate them cleanly.

---

## Version log

| Version | Date | Change |
|---|---|---|
| v3 | 2026-06-07 | Dispatch-1 dual-view + Monitor implementation per `rendering_density.md` + `methodology_exposure.md §5`.  Shipped `surfaces/BuildExtended.tsx`, `surfaces/BuildCompact.tsx`, `surfaces/monitor/PolicyFuturesButterflyWidget.tsx`, and the shared helper `surfaces/futuresButterflySimpleShared.ts`.  Tier set updated to `[generic_runnable, custom_build_surface, monitor_surface]`.  Standalone-bridge typed-detail endpoint shipped at `/api/v1/rates/detail/policy-futures-butterfly`.  Brought to parity with `calculate_ois_butterfly_tool` (the OIS-side single-curve 3-leg butterfly cousin) and `policy_futures_get_futures_price_level_tool` (the STIR price-level sibling — same naming-divergence + quote-convention treatment).  Backend ships butterfly + 1d change + range in PERCENT POINTS; display layer multiplies by 100 to render bps.  `methodology_disclosure` flows from the wire (PR10 / P5 compliant). |
| v2 | 2026-05-26 | Stage 4f — rewrote the body to drop stale "Stage 3 scaffold" framing and answer Q2–Q5 for real. |
| v1 | 2026-05-25 | Stage 3 scaffold — runtime tier only, no surfaces. |
