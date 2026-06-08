# THESIS — `policy_futures_get_futures_calendar_spread_tool`

> Dispatch-1 dual-view build under the rendering-density contract.  Ships an extended Build view (full canvas for single-tool queries) AND a compact Build view (grid card for multi-tool query DAGs) — both REQUIRED per [`rendering_density.md`](../../../../../docs_revamped/03_standards/rendering_density.md) §1 — plus a Monitor tile.

**Version:** v3 (Dispatch-1 dual-view + Monitor implementation)
**Module spec:** [`module.ts`](module.ts)
**Backend artifact:** `policy_futures_get_futures_calendar_spread_tool` (generic_runnable; standalone bridge via `/api/v1/rates/detail/policy-futures-calendar`)
**Tier set:** `[generic_runnable, custom_build_surface, monitor_surface]`
**Category:** `curve_shape`
**Mockups:** [`./mockups/Compact.png`](./mockups/Compact.png) + [`./mockups/Extended.png`](./mockups/Extended.png) — committed alongside the module per the mockup-first workflow.

---

## 1. What surfaces does this module ship?

- **`generic_runnable`** — runtime-status tier; backend ships this primitive in `_PRIMITIVE_SPECS` (per the catalog tool-20 audit: backend folder `rates_agent/policy_futures/tools/futures_calendar_spread/` + MCP wrapper `get_futures_calendar_spread_tool` at `rates_agent/policy_futures/mcp_server.py:577` + curated migration `database/migrations/2026-06-07_phase1_policy_futures_get_futures_calendar_spread_curated.sql`).
- **`custom_build_surface`** — the Phase-1 **dual-view** contract:
  - **Extended Build view** ([`surfaces/BuildExtended.tsx`](surfaces/BuildExtended.tsx)) — full canvas mounted for single-tool queries.  Controls strip (single **Curve Family** dropdown + single **Pair** dropdown + lookback + field), top-right Z-Score / Percentile / Strip-Context cards (the third names the resolved curve identity inline — e.g. *"SOFR · 1-3"* — plus the per-curve regime label), a bps-scale headline KPI strip plus a decomposition row (two per-leg labels carrying both the strip-slot master stems and the current-front underlying contracts inline), calendar-spread history chart with ±2σ z-score bands, stretch-context panel, methodology card sourced from `methodology_disclosure` on the wire + the construction / sign-convention / quote-convention / regime context, lineage footer.
  - **Compact Build view** ([`surfaces/BuildCompact.tsx`](surfaces/BuildCompact.tsx)) — grid card mounted as a node body inside multi-tool query DAGs (e.g. *"compare SFR 1-3 calendar vs SFR 1-4 calendar"*).  Headline 3-KPI strip (SPREAD (BPS) / 1D CHANGE (BPS) / Z-SCORE (252D) — each with sign-convention captions per the mockup), mini-chart with ±2σ z-score bands, the back-minus-front + inverse-pricing caveat footer + curve-family chip, click-to-expand affordance.
- **`monitor_surface`** — Monitor bento tile ([`surfaces/monitor/PolicyFuturesCalendarSpreadWidget.tsx`](surfaces/monitor/PolicyFuturesCalendarSpreadWidget.tsx)) showing one STIR calendar spread (curve_family × pair × lookback parameterised; defaults SOFR_FUT / 1-3 / 252).  STIR calendar spreads are the desk-canonical front-end policy-path slope read; inherently compact per `rendering_density.md §8`.

---

## 2. What does the user read off each surface?

### Extended Build view

The full investigation canvas for ONE same-curve STIR calendar spread.  A PM reads, in order:

1. The title row identifying the curve + pair + flag (e.g. *"SFR 1-3 Calendar Spread · 🇺🇸"* with subtitle *"US Fed SOFR strip — back-leg minus front-leg implied rate (bps display)"*) + the as-of date.
2. The top-right Z-Score / Percentile / Strip-Context cards (the last names the resolved curve identity — *"SOFR · 1-3"* — plus the regime caveat *"Compounded daily RFR (SOFR / SONIA) — disclosure-only label"* OR *"Unsecured 3M term IBOR (Euribor)"*).
3. The bps-scale headline KPI strip — current spread (bps, sign-flipped + multiplied from the wire's FRONT − BACK PERCENT POINTS to a desk-canonical BACK − FRONT bps display) with steeper/flatter caption, 1d change (bps), z-score (display-flipped) with regime + direction caption (Elevated Steeper / Extreme Flatter / Neutral), percentile (display-flipped — "78th" = 78th percentile of the back-minus-front display series), 252d high/low (bps), observation_count.
4. The decomposition row — two per-leg labels carrying both the master-stem strip slots (e.g. *"SHORT LEG (SFR1)"* / *"LONG LEG (SFR2)"*) AND the current-front underlying contracts (e.g. *"SFRM26"* / *"SFRU26"*) inline — so the desk can see exactly which underlying contracts each strip slot resolves to as_of today.
5. The calendar-spread history chart with z-score band overlays.
6. The stretch-context panel.
7. The methodology card (construction formula on both wire and display axes, sign convention, pair with `spread_label`, curve family, underlying contracts, quote convention with the inverse-pricing rule verbatim, short-rate regime, field, z-window, trailing range, window, observations, and the load-bearing **`methodology_disclosure`** flowing verbatim from the backend wire).
8. The lineage footer.

The single **Curve Family** dropdown picks the curve_family (single-curve primitive — distinct from `policy_futures_get_futures_cross_market_spread`, which crosses two families).  The single **Pair** dropdown enforces the `short < long` invariant by only offering registered pairs per curve.

### Compact Build view

The at-a-glance grid card.  A PM sees this in a multi-tool prompt.  They read THREE numbers + a sparkline:

- **SPREAD (BPS)** — signed display bps (back − front via `wirePctToDisplayBps`) with Back cheap / Front cheap / Flat caption — the canonical desk read.
- **1D CHANGE (BPS)** — bps in the display convention, tone-coloured.
- **Z-SCORE (252D)** — signed display-flipped z + regime+direction caption (Neutral / Elevated Steeper / Extreme Flatter / etc.).

The mini-chart shows the calendar-spread history (in display bps) with ±2σ / ±1.5σ z-score bands.  The footer carries the *"Calendar spread on implied rates; underlying contracts quote inverse."* caveat + the curve-family chip (e.g. *"🇺🇸 SOFR"*).  The expand arrow opens the extended view in a modal.

### Monitor tile

One STIR calendar spread, desk-glanceable: current spread (display bps with back-cheap / front-cheap qualifier), 1-day change (display bps), 252d percentile, and a high/low range strip with a current marker on the display bps axis.  Country flag + display-z badge in the header.  The methodology disclosure surfaces as the `title=` tooltip on the footer caveat so a hover shows the full wire disclosure; the inline footer reads *"Back − front, implied-rate bps. STIR quote 100-minus-rate."* so a glance never misreads the unit / sign convention.

---

## 3. Why these surfaces and not others?

**Why the dual-view contract** (both extended + compact REQUIRED): per [`rendering_density.md §1.1`](../../../../../docs_revamped/03_standards/rendering_density.md) multi-tool prompts are the default reality; a tool that ships only an extended view falls back to a generic artifact-type card in multi-tool DAGs.  STIR calendar spreads are commonly compared across pairs (*"SFR 1-3 vs SFR 1-4"*) and across curve families (*"SFR 1-3 vs ER 1-3"*) so the compact view earns its keep immediately.  Standard OVERRIDES FM4 parsimony for the dual-view mandate.

**Why these three compact KPIs** (SPREAD (BPS) + 1D CHANGE (BPS) + Z-SCORE (252D)): they are the desk-canonical "first three numbers" a PM reads off a STIR calendar-spread snapshot — *"where does the slope sit now / how much did it move today / is this stretched?"*.  Alternatives considered + rejected:

- **Per-leg implied rates outright** — the slope object IS the snapshot; surfacing the two leg rates instead would hide the construction.  The extended view's decomposition row carries the per-leg contract identifiers for desk audit (the wire does NOT expose the per-leg implied rates directly — only the spread + the underlying-contract block).
- **252d percentile as a separate compact cell** — already conveyed by the z-score regime + band overlay; surfacing it twice would consume one of the three precious cells without adding read.
- **The raw 4-row mockup KPI deck (SPREAD · 1D CHG · Z-SCORE · PERCENTILE) plus the per-leg block** — would require extending BuildCompactShell with a `secondaryKpis` slot, touching 10+ shipped compact views.  Deferred per the Option-(c) precedent — see Mockup conformance section.

**Why a SINGLE Curve-Family dropdown** (not a leg-pair UI): this primitive is single-curve by construction — the Pydantic Input takes one `curve_family` plus two `strip_position_*` fields, with the same-curve invariant baked in by the strip-slot resolver.  Distinct from `policy_futures_get_futures_cross_market_spread`, which crosses two families at a shared strip slot.  Surfacing a leg-pair UI would invite invalid input shapes; the single dropdown honours the actual contract.

**Why a SINGLE Pair dropdown** (not two independent strip-position dropdowns): the schema's `_strip_positions_must_be_ordered` validator rejects any unordered or duplicate ordering at the input layer.  Surfacing two independent dropdowns would invite invalid orderings; the single dropdown of registered pair presets (per curve) makes invalid orderings unreachable.

**Why the wire ships FRONT − BACK PERCENT POINTS but we display BACK − FRONT bps** (sign flip at the display boundary): the policy-futures sub-domain wire convention is `spread_implied_rate_pct = rate_short − rate_long = rate_front − rate_back` (PERCENT POINTS — matches the `implied_rate_pct` / butterfly `_pct` siblings).  The desk-canonical DISPLAY convention is BACK − FRONT in bps so positive = back rate HIGHER than front = steeper policy path (mockup-faithful: the +45.0 bp headline reads as "back cheap" / steeper slope).  The flip lives in ONE place — `wirePctToDisplayBps` in `futuresCalendarSpreadShared.ts` — so the three surfaces cannot drift.  The 252d high/low + percentile are also flipped at the display boundary (the wire's "high" maps to the display "low" because the sign reversed).  The methodology card documents both conventions verbatim.

**Why the 100-minus-rate caveat is REQUIRED** (not hidden): misreading the wire as a price-axis number is a real desk error — the underlying STIR futures quote 100-minus-rate (`inverse_priced` = true for SFR / ER / SFI), so UP raw-price moves correspond to DOWN implied-rate moves.  The calendar spread is on the IMPLIED-RATE axis (where the sign convention is unambiguous given the back-minus-front flip), but the load-bearing fact a reader must remember is that the underlying contracts are inverse-priced and the wire is in the opposite sign convention.  Per `rendering_density.md §2.2` + §12 the methodology caveat must be reachable in the compact view; here it's the canonical one-liner in the footer + the full wire `methodology_disclosure` available via the Monitor widget's `title=` tooltip + the Extended methodology card's "Disclosure" row.

**Why the methodology card sources the disclosure from the wire** (PR10 / P5 compliance): the policy-futures calendar-spread backend ships `methodology_disclosure` on every response (composed at compute() time, sourced from `config.yaml` + the per-curve regime label).  The methodology card's "Disclosure" row consumes that field verbatim so YAML edits + per-curve regime swaps flow to runtime.  Distinct from the OIS curve-spread reference module where the wire does NOT yet carry the field (the OIS sub-domain hasn't caught up to PR10) — the policy_futures domain shipped it from day one per ADR 0013, matching the just-shipped sibling tool 19 butterfly.

**Why no z-score override controls in the extended view**: the policy-futures calendar-spread Pydantic schema does NOT expose `z_score_window_days` / `z_score_min_periods` / `z_score_ddof` as input fields — they fall through to the YAML at every call site (consistent with the sovereign / linker / OIS / ZCIS curve-spread siblings).  Surfacing them as controls would silently shadow the YAML; the extended view honours the actual contract instead.

**Why a typed-detail endpoint** (`/api/v1/rates/detail/policy-futures-calendar`): per [`methodology_exposure.md §5`](../../../../../docs_revamped/03_standards/methodology_exposure.md) every standalone module ships its own typed bridge; the generic `/run` route stays the LLM-facing surface.

---

## 4. What would change the design?

- **A meeting-by-meeting policy-path calendar primitive** lands → SEPARATE module.  The methodology disclosure on this primitive explicitly REFUSES that framing (per `config.yaml`) — `WIRP`-style per-meeting calendar reads are a different object.
- **A regression-fitted / DV01-weighted STIR calendar primitive** lands → it ships as a SEPARATE module (different concept), not a knob on this one.  (The fixed `short − long` weighting on the implied-rate axis is the per-strip read; weighting variants would silently change the construction.)
- **A new V1 policy-futures market lands** (e.g. AUD CASH RATE futures, CAD CORRA futures, JPY TONA futures) → update `CURVE_REGISTRY` in [`surfaces/futuresCalendarSpreadShared.ts`](surfaces/futuresCalendarSpreadShared.ts) + add registered pairs to `CALENDAR_PAIRS_BY_CURVE`.  No shell changes.
- **A backend-side `weekly_change_spread_implied_rate_pct` / `monthly_change_spread_implied_rate_pct`** lands → expose in the extended KPI strip (the wire is currently leaner than the linker / ZCIS curve-spread siblings — no weekly / monthly change fields).
- **A persistent per-row decomposition** (per-leg implied rates per trade date) → surface from the bespoke `time_series` rows if the schema is extended; today the per-row shape carries `raw_price_spread + spread_implied_rate_pct` per trade date.
- **The catalog promotes the wire from FRONT − BACK PERCENT POINTS to BACK − FRONT BPS** at the Pydantic schema layer → drop the `wirePctToDisplayBps` flip; the display layer reads the wire unchanged.

---

## 5. Which backend doctrine does this module operationalise?

- **FM1** (module identity) — folder name equals the frontend module identity (`policy_futures_get_futures_calendar_spread_tool`); the MCP function inside `policy_futures/mcp_server.py` is the unprefixed `get_futures_calendar_spread_tool` and is bridged through `KNOWN_TOOL_ALIASES` in [`src/lib/toolNames.ts`](../../../lib/toolNames.ts) per the policy_futures naming-divergence precedent.
- **FM3** (surface-tier capability declaration) — claims `generic_runnable` + `custom_build_surface` + `monitor_surface`.
- **FM4** (tier-set parsimony) — overridden by `rendering_density.md §1.2` for the dual-view mandate; Monitor is justified by `surface_contract.md §3.4` eligibility (desk-canonical front-end policy-path slope read).
- **FM5** (display metadata) — `displayName` / `category` / `oneLineSummary` are PM-facing (per `lifecycle_checklist_template.md` Stage 1C).
- **FM7** (pure-spec assembly) — `module.ts` exports a pure value.
- **FM8** (surface-file contract) — `surfaces.buildExtended` + `surfaces.buildCompact` populated; files at canonical paths.
- **FM9** (routing-claim disclosure) — `typedView: null`; this module owns its own full surfaces.
- **FM10** (THESIS discipline) — this file.
- **FM11** (round-trip test) — [`__tests__/module.spec.ts`](__tests__/module.spec.ts) asserts the dual-view contract.
- **FM12** (loader presence) — module imported in [`src/modules/index.ts`](../../index.ts).
- **PR10 / P5** (methodology label sourcing) — the methodology card's "Disclosure" row sources from `data.methodology_disclosure` directly (NOT a hardcoded TS literal).  All other methodology rows source from `data.current_metrics` + the request context.
- Per the **methodology-exposure standalone-bridge contract** (`methodology_exposure.md §5`): own typed-detail endpoint `/api/v1/rates/detail/policy-futures-calendar`; own service helper `fetchDetailPolicyFuturesCalendar`; own frontend type `FuturesCalendarSpreadOutput`.
- Per the **rendering-density dual-view contract** (`rendering_density.md §1`): both views ship + both wired in `module.ts.surfaces` + files at canonical paths.

---

## Mockup conformance

The **Compact mockup** shows `SFR · 1-3 · WHITES` identity row with 🇺🇸 flag + `STIR` status pill + a `SOFR Calendar Spread (Implied Rate)` subtitle; a 3-cell KPI deck (SPREAD (BPS) `+45.0 bp` with "Back cheap" caption, 1D CHANGE (BPS) `+2.8 bp`, Z-SCORE (252D) `+1.63σ` with "Elevated · Bull Flattening / Steeper" caption); a calendar-spread history chart with ±2σ / ±1.5σ / 0-line band overlays; the *"Calendar spread on implied rates; underlying contracts quote inverse."* footer caveat; and the as-of timestamp + "View full analysis →" + "Fresh" badge in the footer.  The implementation reproduces all of these via the shared `BuildCompactShell` + the per-tool `compactKPIs` helper.

**Compact density step-down (Option (c) precedent — catalog design_guardrail #5):** the mockup shell-standard density matches the implementation exactly here (3 KPIs, single identity, sparkline, caveat, expand affordance).  No additional step-down was required — the mockup hews to the shell-standard 3-KPI density.  All headline elements (SPREAD / 1D CHANGE / Z-SCORE) match the mockup's primary deck verbatim.  The mockup's exact regime-caption wording for the z-cell ("Bull Flattening") is generalised to the curve-spread vocabulary the shared `zScoreCaptionForSpread` helper emits (`Elevated Steeper` / `Elevated Flatter` / `Extreme …`), which the desk reads as the equivalent steeper/flatter regime; the literal mockup phrase is a single instance of a broader signed-z category surfaced by the helper.

The **Extended mockup** shows `SFR 1-3 Calendar Spread` with 🇺🇸 flag + `SOFR Calendar Spread (Implied Rate)` subtitle, top-right Z-Score `+1.63σ` (Elevated) / Percentile `78th` / Federal Reserve (FOMC) regime cards, a bps-scale headline KPI strip (SPREAD `+45.0 bp` / 1D CHANGE / Z-SCORE / PERCENTILE / 252D HIGH / 252D LOW / OBSERVATIONS), the chart with z-bands + history strip + freshness indicator, the stretch-context panel, and the full methodology card.  The implementation reproduces this structure end-to-end via the shared `BuildExtendedShell` + the per-tool `extendedKPIs` + `decompositionKPIs` + `buildMethodologyRows` helpers.  The mockup's per-leg `BUILD MAPS` / per-pair `LEG COMPOSITION` decomposition row is rendered as a 2-cell decomposition strip (SHORT LEG + LONG LEG, each carrying the strip-slot master stem AND the current-front underlying contract); the wire does not expose per-leg implied rates directly so the cells carry the contract identifiers + expiry dates instead of derivable rate values.

---

## Version log

| Version | Date | Change |
|---|---|---|
| v3 | 2026-06-07 | Dispatch-1 dual-view + Monitor implementation per `rendering_density.md` + `methodology_exposure.md §5`.  Shipped `surfaces/BuildExtended.tsx`, `surfaces/BuildCompact.tsx`, `surfaces/monitor/PolicyFuturesCalendarSpreadWidget.tsx`, and the shared helper `surfaces/futuresCalendarSpreadShared.ts`.  Tier set updated to `[generic_runnable, custom_build_surface, monitor_surface]`.  Standalone-bridge typed-detail endpoint shipped at `/api/v1/rates/detail/policy-futures-calendar`.  Brought to parity with `calculate_ois_curve_spread_tool` (the OIS-side single-curve 2-point tenor-spread cousin) and `policy_futures_get_futures_butterfly_simple_tool` (the just-shipped tool 19 — same naming-divergence + quote-convention treatment + `methodology_disclosure` wire-honesty pattern + per-curve `CURVE_REGISTRY` + defensive `useEffect` validity guard).  Backend ships spread + 1d change + range in PERCENT POINTS, FRONT − BACK convention; display layer flips sign + multiplies by 100 to render in bps under the BACK − FRONT convention.  `methodology_disclosure` flows from the wire (PR10 / P5 compliant). |
| v2 | 2026-05-26 | Stage 4f — rewrote the body to drop stale "Stage 3 scaffold" framing. |
| v1 | 2026-05-25 | Stage 3 scaffold — runtime tier only, no surfaces. |
