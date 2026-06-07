# THESIS — `calculate_forward_breakeven_simple_tool`

> Dual-view + Monitor module under the new standards.  Ships an extended Build view (full canvas for single-tool queries) AND a compact Build view (grid card for multi-tool query DAGs) — both REQUIRED per [`rendering_density.md`](../../../../../docs_revamped/03_standards/rendering_density.md) §1 — plus a Monitor tile.  Standalone-bridge: own typed-detail endpoint `/api/v1/rates/detail/forward-breakeven`.

**Version:** v3 (dual-view + Monitor implementation per BUILD_GUIDE Stages 4–6)
**Module spec:** [`module.ts`](module.ts)
**Backend artifact:** `calculate_forward_breakeven_simple_tool` (generic_runnable; standalone bridge via `/api/v1/rates/detail/forward-breakeven`)
**Tier set:** `[generic_runnable, custom_build_surface, monitor_surface]`
**Category:** `forward_rate`
**Mockups:** [`./mockups/Compact.png`](./mockups/Compact.png) + [`./mockups/Extended.png`](./mockups/Extended.png) — committed alongside the module per the mockup-first workflow.

---

## 1. What surfaces does this module ship?

- **`generic_runnable`** — runtime-status tier; backend ships in `_PRIMITIVE_SPECS`.
- **`custom_build_surface`** — the **dual-view** contract:
  - **Extended Build view** ([`surfaces/BuildExtended.tsx`](surfaces/BuildExtended.tsx)) — full canvas mounted for single-tool queries.  Controls strip (Country Pair dropdown + Forward dropdown + lookback + field), top-right Z-score / Percentile / Inflation-Compensation cards, a 9-cell bps-scale KPI strip (forward breakeven + 1d/5d/1m changes + z-score + percentile + 252d high/low + observations), forward-breakeven-history chart with ±2σ z-score bands, stretch-context panel, methodology card sourcing `methodology_label` from the wire, lineage footer.
  - **Compact Build view** ([`surfaces/BuildCompact.tsx`](surfaces/BuildCompact.tsx)) — grid card mounted as a node body inside multi-tool query DAGs (e.g. *"compare US 5Y5Y BE vs UK 5Y5Y BE vs French 5Y5Y BE"*).  Headline 3-KPI strip (forward BE + 1d change + z-score), mini-chart with z-score bands, country-pair chip, inflation-compensation caveat footer, click-to-expand affordance.
- **`monitor_surface`** — Monitor bento tile ([`surfaces/monitor/ForwardBreakevenWidget.tsx`](surfaces/monitor/ForwardBreakevenWidget.tsx)) showing one same-country forward breakeven (pair × forward_pair × lookback parameterised; defaults US (UST/TIPS) / 5Y5Y / 252).  Forward breakevens are the canonical term-structure read on long-run inflation compensation; 5Y5Y US BE sits on every desk's morning briefing.  Inherently compact per `rendering_density.md §8`.

---

## 2. What does the user read off each surface?

### Extended Build view

The full investigation canvas for ONE forward breakeven.  A PM reads, in order: (a) the title row identifying the country pair + forward window + as-of date (e.g. *"US · 5Y5Y FORWARD BREAKEVEN"* with US flag); (b) the top-right Z-Score / Percentile / Inflation-Compensation cards (the last carries the *"inflation compensation, not expected inflation — compounds × 2 pillars + forward"* caveat inline); (c) the bps-scale KPI strip — current forward breakeven (bps), 1d/5d/1m changes (bps), z-score, percentile, 252d high/low, and observation count for window-length context; (d) the forward-breakeven-history chart with ±2σ / ±1.5σ z-score band overlays; (e) the stretch-context panel; (f) the methodology card (year-weighted linear forward formula, field, z-model, the start/end spot breakeven composition row for the decomposition audit, the wire-sourced honesty disclosure, same-country pair, linker caveat); (g) the lineage footer.  The single **Country Pair** dropdown expands to both legs (a forward breakeven is a same-country object — its same-country invariant is inherited from the spot primitive); the **Forward** dropdown is filtered to forward pairs supported by the selected pair's same-country tenor grid.

### Compact Build view

The at-a-glance grid card.  A PM sees this in a multi-tool prompt.  They read THREE numbers + a sparkline: current forward breakeven (bps) with a secondary percent caption; 1-day change (bps, tone-coloured); rolling 252d z-score (with regime caption — Normal / Elevated / Extreme).  The mini-chart shows the forward-breakeven history with ±2σ / ±1.5σ z-score bands.  The header identity row shows the country pair (e.g. *"US · 5Y5Y BE"* with US flag).  The footer carries the *"Inflation compensation, not expected inflation. Compounds × 2 pillars + forward."* caveat.  The expand arrow opens the extended view in a modal.

### Monitor tile

One same-country forward breakeven, desk-glanceable: current forward breakeven (bps), 1-day change (bps), 252d percentile, and a high/low range strip with a current marker.  Country flag + forward-pair label (e.g. *"US · UST/TIPS · 5Y5Y 🇺🇸"*) + z-score badge in the header.

---

## 3. Why these surfaces and not others?

**Why the dual-view contract** (both extended + compact REQUIRED): per [`rendering_density.md §1.1`](../../../../../docs_revamped/03_standards/rendering_density.md) multi-tool prompts are the default reality; a tool that ships only an extended view falls back to a generic artifact-type card in multi-tool DAGs.  Forward breakevens are very commonly compared across countries (*"US 5Y5Y vs UK 5Y5Y vs French 5Y5Y BE"*) AND across forward windows on the same country (*"US 5Y5Y vs US 5Y10Y vs US 10Y10Y BE"*), so the compact view earns its keep immediately.  The standard OVERRIDES FM4 parsimony for the dual-view mandate.

**Why these three compact KPIs** (forward breakeven + 1d change + 252d z-score): they're the desk-canonical "first three numbers" a PM reads off a forward-breakeven snapshot — *"where is the long-run forward inflation-compensation pricing now / how much did it move today / is this stretched?"*.  Alternatives considered + rejected: forward breakeven in % (the bps representation is the desk-canonical change unit and reads better in a dense card; the % equivalent is surfaced as a caption under the bps cell to preserve the 2.12% read); 252d percentile (already conveyed by the z-score regime + band overlay); the start/end spot breakeven legs (they're decomposition detail, surfaced in the extended view's methodology card composition row, not headline).

**Why the inflation-compensation caveat is REQUIRED inline** (not hidden): misreading a forward bond-implied breakeven as a clean forward expected-inflation forecast is a genuine desk error AND is amplified vs the spot — the year-weighted linear construction compounds the spot mis-pricing across both pillars (each pillar carries inflation risk premium + relative liquidity premium) plus a forward-leg premium for term-premium dynamics.  Per `rendering_density.md §2.2` + §12 the methodology caveat must be reachable in the compact view; here it's the canonical one-liner in the footer.  The full methodology disclosure threads onto the wire via `current_metrics.methodology_label` (sourced from `config.yaml:methodology.what_it_does`, NOT hardcoded as a TS literal — per FM10 + P5).

**Why a single Country-Pair dropdown** (not two): a breakeven is a same-country object by construction; the compute layer refuses cross-country pairs (e.g. `DE_BUND` vs `EUR_FR_LINKER`) with a controlled error envelope.  Surfacing two independent dropdowns would invite invalid selections; the single dropdown enforces validity at the input layer (mockup ExtendedBreakeven design + sibling spot breakeven module).

**Why filter forward_pair options per-pair**: the V1 breakeven tenor universe is pair-dependent (TIPS: 5Y/10Y/20Y/30Y; OATei: 2Y/5Y/10Y/15Y; etc.).  A forward window selecting a tenor not present on the chosen pair would 422 at the backend; filtering at the controls layer prevents that round-trip and matches the spot breakeven module's `BREAKEVEN_TENOR_OPTIONS_BY_PAIR` pattern.

**Why a typed-detail endpoint** (`/api/v1/rates/detail/forward-breakeven`): per [`methodology_exposure.md §5`](../../../../../docs_revamped/03_standards/methodology_exposure.md) every standalone module ships its own typed bridge; the generic `/run` route stays the LLM-facing surface.  This endpoint mirrors the sibling spot breakeven endpoint's contract (same params shape, same fall-through-to-YAML semantics on `field_name` + `lookback_days`).

---

## 4. What would change the design?

- **A carry- / risk-premium-adjusted forward breakeven primitive** lands → it ships as a SEPARATE module (different concept), not a knob on this one; the extended view could then offer a side-by-side "simple vs adjusted" framing.
- **Backend exposes date-pair mode** (currently tenor-pair only on the input schema) → add a controls-strip toggle similar to the OIS forward-rate module's deferred date-pair mode.  Documented as a path-forward in `config.yaml:planned_extensions`.
- **A new linker market** (e.g. AUS, JPY) with a matching nominal curve lands → add the pair to `PAIR_BY_LINKER` + `FORWARD_BREAKEVEN_PAIR_OPTIONS` + the linker's tenor set to `BREAKEVEN_TENORS_BY_PAIR` in [`surfaces/forwardBreakevenShared.ts`](surfaces/forwardBreakevenShared.ts); no shell changes.
- **The desk wants z-score conventions exposed at the API layer** (currently YAML-locked on this primitive) → flip the relevant config knobs' `exposure.expose` to `true` + add the optional Query params on the detail route + add the Advanced controls.  Breaking change per the exposure-decision protocol.

---

## 5. Which backend doctrine does this module operationalise?

- **FM1** (module identity) — folder name equals backend `tool_name`.
- **FM3** (surface-tier capability declaration) — claims `generic_runnable` + `custom_build_surface` + `monitor_surface`.
- **FM4** (tier-set parsimony) — overridden by `rendering_density.md §1.2` for the dual-view mandate; Monitor is justified by `surface_contract.md §3.4` eligibility (forward breakevens are a canonical morning-briefing read for long-run inflation-compensation regime).
- **FM5** (display metadata) — `displayName` / `category` / `oneLineSummary` are PM-facing.
- **FM7** (pure-spec assembly) — `module.ts` exports a pure value.
- **FM8** (surface-file contract) — `surfaces.buildExtended` + `surfaces.buildCompact` populated; files at canonical paths.
- **FM9** (routing-claim disclosure) — `typedView: null`; this module owns its own full surfaces.
- **FM10** (THESIS discipline) — this file.  `methodology_label` sourced from the wire (`current_metrics.methodology_label` via `buildMethodologyRows`), NOT a TS literal — preserves the YAML-edit-flows-to-runtime contract (P5).
- **FM11** (round-trip test) — [`__tests__/module.spec.ts`](__tests__/module.spec.ts) asserts the dual-view contract + monitor presence + `typedView: null` + defaultParams sanity (start_tenor + end_tenor must accompany forward_pair so the very first render against the typed-detail endpoint succeeds without an interim "missing input" error).
- **FM12** (loader presence) — module imported in [`src/modules/index.ts`](../../index.ts).
- Per the **methodology-exposure standalone-bridge contract** (`methodology_exposure.md §5`): own typed-detail endpoint `/api/v1/rates/detail/forward-breakeven`; own service helper `fetchDetailForwardBreakeven`; own frontend type `ForwardBreakevenSimpleOutput`.
- Per the **rendering-density dual-view contract** (`rendering_density.md §1`): both views ship + both wired in `module.ts.surfaces` + files at canonical paths.

---

## Version log

| Version | Date | Change |
|---|---|---|
| v3 | 2026-06-07 | Dual-view + Monitor implementation per BUILD_GUIDE Stages 4–6 + the dispatch-1 catalog entry.  Shipped `surfaces/BuildExtended.tsx`, `surfaces/BuildCompact.tsx`, `surfaces/monitor/ForwardBreakevenWidget.tsx`, `surfaces/forwardBreakevenShared.ts`, and the standalone-bridge typed-detail endpoint at `/api/v1/rates/detail/forward-breakeven`.  Tier set updated to `[generic_runnable, custom_build_surface, monitor_surface]`.  `methodology_label` threaded from the wire per FM10 + P5.  Mirrors the sibling spot breakeven module shape + the OIS forward-rate module's forward-pair selector pattern. |
| v2 | 2026-05-26 | Stage 4f — rewrote the body to drop stale "Stage 3 scaffold" framing and answer Q2–Q5 for real. |
| v1 | 2026-05-25 | Stage 3 scaffold — runtime tier only, no surfaces. |
