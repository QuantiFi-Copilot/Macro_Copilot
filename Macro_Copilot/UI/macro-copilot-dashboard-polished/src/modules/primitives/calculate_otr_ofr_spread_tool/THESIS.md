# THESIS — `calculate_otr_ofr_spread_tool`

> Sovereign cash-bond OTR/OFR yield-spread module brought to parity with the Phase-1 pilot `calculate_breakeven_inflation_simple_tool` under the dual-view rendering-density contract.  Ships an extended Build view (full canvas for single-tool queries) AND a compact Build view (grid card for multi-tool query DAGs) — both REQUIRED per [`rendering_density.md`](../../../../../docs_revamped/03_standards/rendering_density.md) §1 — plus a Monitor tile.

**Version:** v5 (factory dispatch — dual-view + standalone-bridge + Monitor)
**Module spec:** [`module.ts`](module.ts)
**Backend artifact:** `calculate_otr_ofr_spread_tool` (generic_runnable; standalone bridge via `/api/v1/rates/detail/otr-ofr-spread`)
**Tier set:** `[generic_runnable, custom_build_surface, monitor_surface]`
**Category:** `curve_shape`
**Mockups:** [`./mockups/Compact.png`](./mockups/Compact.png) + [`./mockups/Extended.png`](./mockups/Extended.png) — committed alongside the module per the mockup-first workflow.

---

## 1. What surfaces does this module ship?

- **`generic_runnable`** — runtime-status tier; backend ships in `_PRIMITIVE_SPECS`.
- **`custom_build_surface`** — the dual-view contract:
  - **Extended Build view** ([`surfaces/BuildExtended.tsx`](surfaces/BuildExtended.tsx)) — full canvas mounted for single-tool queries.  Identity row (slot label + OTR/OFR bond identifiers), controls strip (Country + Tenor + Lookback + Field), top-right Z-Score / Percentile / Liquidity-Premium-caveat cards, bps-scale KPI strip (spread + 1d change + z + percentile + 252d high/low + observations + per-leg OTR / OFR yields for the decomposition), spread-history chart with ±2σ z-score bands, stretch-context panel, methodology card threading `data.methodology_note` verbatim, lineage footer.
  - **Compact Build view** ([`surfaces/BuildCompact.tsx`](surfaces/BuildCompact.tsx)) — grid card mounted as a node body inside multi-tool query DAGs (e.g. *"compare US 10Y OTR/OFR vs DE 10Y OTR/OFR vs UK 10Y OTR/OFR"*).  Shell-standard 3-KPI headline (SPREAD bps / 1D CHANGE bp / Z-SCORE 252D), mini-chart with z-score bands, the TD #27 wire-honesty caveat footer (reads `data.methodology_note`), country-flag chip, click-to-expand affordance.
- **`monitor_surface`** — Monitor bento tile ([`surfaces/monitor/OtrOfrSpreadWidget.tsx`](surfaces/monitor/OtrOfrSpreadWidget.tsx)) showing one (country, tenor) OTR/OFR slot (country × tenor × lookback parameterised; defaults US / 10Y / 252).  OTR/OFR is a canonical desk-board liquidity-premium read per `surface_contract.md §3.4`.  Inherently compact per `rendering_density.md §8`.

---

## 2. What does the user read off each surface?

### Extended Build view

The full investigation canvas for ONE OTR/OFR slot.  A PM reads, in order: (a) the title row identifying the slot ("UST 10Y · OTR-OFR"), with the OTR bond's CUSIP/ISIN + the OFR bond's CUSIP/ISIN in the metadata strip (so the rich-cheap audit is one click away); (b) the top-right Z-Score / Percentile / Liquidity-Premium-caveat cards (the last carries "Liquidity-premium PROXY — sign POSITIVE = OTR cheap to OFR" inline); (c) the bps-scale KPI strip — current spread (bps), 1d change (bps), z-score (with regime caption), 252d percentile, 252d high/low, observations, AND the OTR + OFR per-leg yields in percent so the spread decomposition is auditable on the same screen; (d) the spread-history chart with ±2σ / ±1.5σ z-score bands; (e) the stretch-context panel; (f) the methodology card (construction formula, sign convention, liquidity-premium-PROXY disclosure, OFR definition, field, z-model, slot identity, OTR + OFR bond identifiers, AND the wire's `methodology_note` — TD #27 forward-only + detection-date prose, sourced from the resolver, NOT hardcoded); (g) the lineage footer.

### Compact Build view

The at-a-glance grid card.  A PM sees this in a multi-tool prompt.  They read THREE numbers + a sparkline: current spread (bps), 1-day change (bps, tone-coloured), rolling 252d z-score (with regime caption — Normal / Elevated / Extreme).  The mini-chart shows the spread history with ±2σ / ±1.5σ z-score bands.  The footer carries the wire's `methodology_note` — the TD #27 forward-only + detection-date prose — so even at multi-tool density the desk sees the load-bearing disclosure.  The country-flag chip + slot label (e.g. "🇺🇸 UST · 10Y") + "OTR-OFR" pill complete the identity.  The expand arrow opens the extended view in a modal.

### Monitor tile

One (country, tenor) OTR/OFR slot, desk-glanceable: current spread (bps), 1-day change (bps), 252d percentile, and a high/low range strip with a current marker.  Country flag + slot label in the kicker; z-score badge whose `title=` tooltip carries the `methodology_note` for hover-disclosure.

---

## 3. Why these surfaces and not others?

**Why the dual-view contract** (both extended + compact REQUIRED): per [`rendering_density.md §1.1`](../../../../../docs_revamped/03_standards/rendering_density.md) multi-tool prompts are the default reality; OTR/OFR is exactly the kind of object a PM compares ACROSS countries or tenors in one prompt (*"US 10Y OTR/OFR vs DE 10Y OTR/OFR vs UK 10Y OTR/OFR"*), so the compact view earns its keep immediately.  The standard OVERRIDES FM4 parsimony for the dual-view mandate.

**Why these three compact KPIs** (current spread + 1d change + z-score): they're the desk-canonical "first three numbers" a PM reads off any sovereign spread snapshot — *"where is the OTR/OFR now / how much did it move today / is this stretched?"*.  This is the SHELL-STANDARD density (per the Option (c) shell-density precedent the catalog entry calls out — same three-cell pattern as breakeven, cross-country-real-yield, swap-spread).  Alternatives considered + rejected: 252d percentile (already conveyed by the z-score regime + band overlay); the OTR / OFR underlying yields (decomposition detail, surfaced in the extended view's KPI strip, not headline); Days-Since-Auction (the desk-meaningful "where in the auction cycle is this?" — desirable but the backend Output does NOT expose the OTR window's effective_from or true auction date today, so the compact card cannot fabricate it; see Q4).

**Why the liquidity-premium-PROXY caveat is REQUIRED inline** (not hidden): misreading the OTR/OFR spread as a clean liquidity-premium read is a genuine desk error — deviations can ALSO reflect bond-specific scarcity, squeeze dynamics, or repo-rate differences between the two legs.  Per `rendering_density.md §2.2` + §12 the methodology caveat must be reachable in the compact view; here it's threaded from the wire's TOP-LEVEL `methodology_note` field (TD #27 forward-only + detection-date prose), reaching the desk on all three surfaces.

**Why the wire field is TOP-LEVEL `methodology_note` (not `current_metrics.methodology_label`)**: the OTR/OFR primitive's backend predates the breakeven-pilot's `current_metrics.methodology_label` convention.  Its disclosure lives at the top level of the Output (mirroring the `compute_financing_rate_tool` pattern → `data.methodology_disclosure`).  The frontend reads from where the wire ships the prose; the disclosure ROUTING is what matters, not the field name.  Both treatments satisfy methodology-exposure §1's single-source-of-truth contract.

**Why a typed-detail endpoint** (`/api/v1/rates/detail/otr-ofr-spread`): per [`methodology_exposure.md §5`](../../../../../docs_revamped/03_standards/methodology_exposure.md) every standalone module ships its own typed bridge; the generic `/run` route stays the LLM-facing surface.

---

## 4. What would change the design?

- **The backend Output adds an `auction_context` block** (current OTR window's `effective_from`, days-since-roll, prior-window age) → the compact card adopts the "Days Since Auction" chip the mockup sketches.  Today the chip is omitted because the backend doesn't expose the auction date and the detection-date precision caveat (TD #27b) makes the resolver's `effective_from` an inexact proxy.  See "Mockup conformance" below.
- **A repo-rate-adjusted OTR/OFR primitive** lands → it ships as a SEPARATE module (different concept — explicit funding cost), not a knob on this one; the extended view could then offer a side-by-side "simple vs adjusted" framing.
- **The trailing-range window widens beyond 252** (`trailing_range_window_days` per the YAML) → backend `_validate_conventions` guards today with `NotImplementedError`; widening requires a coordinated schema migration + frontend update + parity-fixture regeneration.
- **A new (country, tenor) slot lands in `macro_data.otr_history`** → no shell change; the controls strip already enumerates the registered universe via `COUNTRY_OPTIONS` + `TENOR_OPTIONS` in [`surfaces/otrOfrSpreadShared.ts`](surfaces/otrOfrSpreadShared.ts).
- **The `ofr_definition` convention widens beyond `prior_otr_window`** (e.g. "second-OTR-back" or maturity-cohort grouping) → add an OFR-definition dropdown + thread the new wire field; today the convention is wire-frozen via the compute layer's `NotImplementedError` guard.

---

## Mockup conformance

Both [`mockups/Compact.png`](mockups/Compact.png) and [`mockups/Extended.png`](mockups/Extended.png) are the visual contract.  The shipped surfaces match file-for-file on identity row, KPI ordering, chart shape, z-score bands, methodology placement, lineage footer, and the country-flag chip.

**One deviation, documented**: both mockups sketch a "Days Since Auction" affordance (a single chip on Compact; an Auction Context card with date + percentile on Extended).  The backend `OtrOfrSpreadOutput` does NOT expose the current OTR window's `effective_from` date or any auction-context fields.  Synthesising it from the time-series would also be unsafe: TD #27b documents that the resolver's `effective_from` is the detection date, ~1–2 days after the true auction.  Per the build-guide rule ("If the backend Output shape doesn't match what the mockup implies … refuse or flag — do not fabricate"), the chip + card are OMITTED.  The right path is the design trigger in Q4 (backend exposes an `auction_context` block); the chip will then drop into the existing identity row + a new top-right card without further shell change.

All other mockup elements are faithfully rendered.

---

## 5. Which backend doctrine does this module operationalise?

- **FM1** (module identity) — folder name equals backend `tool_name`.
- **FM3** (surface-tier capability declaration) — claims `generic_runnable` + `custom_build_surface` + `monitor_surface`.
- **FM4** (tier-set parsimony) — overridden by `rendering_density.md §1.2` for the dual-view mandate; Monitor is justified by `surface_contract.md §3.4` eligibility (cash-bond liquidity-premium proxy is a canonical desk-board read).
- **FM5** (display metadata) — `displayName` / `category` / `oneLineSummary` are PM-facing.
- **FM7** (pure-spec assembly) — `module.ts` exports a pure value; no side effects.
- **FM8** (surface-file contract) — `surfaces.buildExtended` + `surfaces.buildCompact` populated; files at canonical paths.
- **FM9** (routing-claim disclosure) — `typedView: null`; this module owns its own full surfaces.
- **FM10** (THESIS discipline) — this file.
- **FM11** (round-trip test) — [`__tests__/module.spec.ts`](__tests__/module.spec.ts) asserts the dual-view contract.
- **FM12** (loader presence) — module imported in [`src/modules/index.ts`](../../index.ts).
- Per the **methodology-exposure standalone-bridge contract** (`methodology_exposure.md §5`): own typed-detail endpoint `/api/v1/rates/detail/otr-ofr-spread`; own service helper `fetchDetailOtrOfrSpread`; own frontend type `OtrOfrSpreadOutput`.
- Per the **rendering-density dual-view contract** (`rendering_density.md §1`): both views ship + both wired in `module.ts.surfaces` + files at canonical paths.

---

## Version log

| Version | Date | Change |
|---|---|---|
| v5 | 2026-06-10 | Factory dispatch — dual-view + standalone-bridge + Monitor implementation.  Shipped `surfaces/BuildExtended.tsx`, `surfaces/BuildCompact.tsx`, `surfaces/monitor/OtrOfrSpreadWidget.tsx`, and the shared helper `surfaces/otrOfrSpreadShared.ts`.  Tier set updated to `[generic_runnable, custom_build_surface, monitor_surface]`.  Standalone-bridge typed-detail endpoint shipped at `/api/v1/rates/detail/otr-ofr-spread`.  Brought to parity with the Phase-1 pilot `calculate_breakeven_inflation_simple_tool`. |
| v4 | 2026-05-26 | Stage 6 — surface-contract retraction.  Dropped `monitor_surface` tier claim; deleted the stub widget file.  Module now claims `generic_runnable` only.  Monitor remains eligible per the contract but is deferred until the typed-detail endpoint ships OTR-OFR series data. |
| v3 | 2026-05-26 | Stage 6 — added `monitor_surface` tier claim and the bespoke parameterised Monitor card.  Subsequently retracted in v4 (the shipped widget was a stub). |
| v2 | 2026-05-26 | Stage 4f — rewrote the body to drop stale "Stage 3 scaffold" framing. |
| v1 | 2026-05-25 | Stage 3 scaffold — runtime tier only. |
