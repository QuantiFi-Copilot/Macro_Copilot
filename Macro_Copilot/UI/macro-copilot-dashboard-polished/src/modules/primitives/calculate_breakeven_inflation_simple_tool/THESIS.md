# THESIS — `calculate_breakeven_inflation_simple_tool`

> Phase-1 Stage-B module under the dual-view rendering-density contract.  Brought to full parity with `get_real_yield_level_tool`: ships an extended Build view (full canvas for single-tool queries) AND a compact Build view (grid card for multi-tool query DAGs) — both REQUIRED per [`rendering_density.md`](../../../../../docs_revamped/03_standards/rendering_density.md) §1 — plus a Monitor tile.

**Version:** v3 (Phase-1 Stage-B dual-view + Monitor implementation)
**Module spec:** [`module.ts`](module.ts)
**Backend artifact:** `calculate_breakeven_inflation_simple_tool` (generic_runnable; standalone bridge via `/api/v1/rates/detail/breakeven`)
**Tier set:** `[generic_runnable, custom_build_surface, monitor_surface]`
**Category:** `cross_market_rv`
**Mockups:** [`./mockups/Compact.png`](./mockups/Compact.png) + [`./mockups/Extended.png`](./mockups/Extended.png) — committed alongside the module per the mockup-first workflow.

---

## 1. What surfaces does this module ship?

- **`generic_runnable`** — runtime-status tier; backend ships in `_PRIMITIVE_SPECS`.
- **`custom_build_surface`** — the Phase-1 **dual-view** contract:
  - **Extended Build view** ([`surfaces/BuildExtended.tsx`](surfaces/BuildExtended.tsx)) — full canvas mounted for single-tool queries.  Controls strip (single **Country Pair** dropdown + tenor + lookback + field + Advanced z-score overrides), top-right Z-score / Percentile / Country-Pair cards, a bps-scale KPI strip (breakeven + changes + range + the two underlying yields for the decomposition), breakeven-history chart with ±2σ z-score bands, stretch-context panel, methodology card, lineage footer.
  - **Compact Build view** ([`surfaces/BuildCompact.tsx`](surfaces/BuildCompact.tsx)) — grid card mounted as a node body inside multi-tool query DAGs (e.g. *"compare US 10Y breakeven vs UK 10Y breakeven vs French 10Y breakeven"*).  Headline 3-KPI strip, mini-chart with z-score bands, the inflation-compensation caveat footer + country-pair chip, click-to-expand affordance.
- **`monitor_surface`** — Monitor bento tile ([`surfaces/monitor/BreakevenInflationWidget.tsx`](surfaces/monitor/BreakevenInflationWidget.tsx)) showing one same-country breakeven (pair × tenor × lookback parameterised; defaults US (UST/TIPS) / 10Y / 252).  Breakevens are a canonical morning-briefing read per `surface_contract.md §3.4`.  Inherently compact per `rendering_density.md §8`.

---

## 2. What does the user read off each surface?

### Extended Build view

The full investigation canvas for ONE breakeven.  A PM reads, in order: (a) the title row identifying the country pair + tenor + as-of date; (b) the top-right Z-Score / Percentile / Country-Pair cards (the last carries the *"inflation compensation, not expected inflation"* caveat inline); (c) the bps-scale KPI strip — current breakeven (bps), 1d/5d/1m changes (bps), z-score, percentile, 252d high/low, AND the nominal + real underlying yields so the nominal − real decomposition is auditable on the same screen; (d) the breakeven-history chart with z-score band overlays; (e) the stretch-context panel; (f) the methodology card (construction formula, field, z-model, the honesty disclosure, same-country pair, linker caveat); (g) the lineage footer.  The single **Country Pair** dropdown expands to both legs (a breakeven is a same-country object, so the linker family uniquely determines the nominal counterparty).

### Compact Build view

The at-a-glance grid card.  A PM sees this in a multi-tool prompt.  They read THREE numbers + a sparkline: current breakeven (bps), 1-day change (bps, tone-coloured), rolling 252d z-score (with regime caption — Normal / Elevated / Extreme).  The mini-chart shows the breakeven history with ±2σ / ±1.5σ z-score bands.  The footer carries the *"Inflation compensation, not expected inflation"* caveat + the country-pair chip (e.g. *"US · UST/TIPS"*).  The expand arrow opens the extended view in a modal.

### Monitor tile

One same-country breakeven, desk-glanceable: current breakeven (bps), 1-day change (bps), 252d percentile, and a high/low range strip with a current marker.  Country flag + z-score badge in the header.

---

## 3. Why these surfaces and not others?

**Why the dual-view contract** (both extended + compact REQUIRED): per [`rendering_density.md §1.1`](../../../../../docs_revamped/03_standards/rendering_density.md) multi-tool prompts are the default reality; a tool that ships only an extended view falls back to a generic artifact-type card in multi-tool DAGs.  Breakevens are very commonly compared across countries (*"US vs UK vs French 10Y breakeven"*), so the compact view earns its keep immediately.  The standard OVERRIDES FM4 parsimony for the dual-view mandate.

**Why these three compact KPIs** (current breakeven + 1d change + z-score): they're the desk-canonical "first three numbers" a PM reads off a breakeven snapshot — *"where is breakeven now / how much did it move today / is this stretched?"*.  Alternatives considered + rejected: breakeven in % (the bps representation is the desk-canonical change unit and reads better in a dense card); 252d percentile (already conveyed by the z-score regime + band overlay); the nominal/real underlying yields (they're decomposition detail, surfaced in the extended view's KPI strip, not headline).

**Why the inflation-compensation caveat is REQUIRED inline** (not hidden): misreading a bond-implied breakeven as a clean expected-inflation forecast is a genuine desk error — the differential carries an inflation risk premium + relative liquidity premium.  Per `rendering_density.md §2.2` + §12 the methodology caveat must be reachable in the compact view; here it's the canonical one-liner in the footer, and the same honesty disclosure threads onto the wire via `current_metrics.methodology_label` (sourced from `config.yaml:methodology.what_it_does`, not hardcoded).

**Why a single Country-Pair dropdown** (not two): a breakeven is a same-country object by construction; the compute layer refuses cross-country pairs (e.g. DE_BUND vs EUR_FR_LINKER) with a controlled error.  Surfacing two independent dropdowns would invite invalid selections; the single dropdown enforces validity at the input layer (mockup ExtendedBreakeven design).

**Why a typed-detail endpoint** (`/api/v1/rates/detail/breakeven`): per [`methodology_exposure.md §5`](../../../../../docs_revamped/03_standards/methodology_exposure.md) every standalone module ships its own typed bridge; the generic `/run` route stays the LLM-facing surface.

---

## 4. What would change the design?

- **A carry- / risk-premium-adjusted breakeven primitive** lands (TD #32 / TD #33) → it ships as a SEPARATE module (different concept), not a knob on this one; the extended view could then offer a side-by-side "simple vs adjusted" framing.
- **A new linker market** (e.g. AUS, JPY) with a matching nominal curve lands → add the pair to `PAIR_BY_LINKER` + `BREAKEVEN_PAIR_OPTIONS` in [`surfaces/breakevenShared.ts`](surfaces/breakevenShared.ts); add the linker caveat to the shared `countryCaveats.ts` registry.  No shell changes.
- **Z-band absolute-value labelling** (the breakeven mockup shows "+2σ (282)") — currently the bands use σ labels for cross-tool consistency with `get_real_yield_level_tool`.  Promoting absolute-bp labels is a shared-`MainChart` enhancement that would backport to every tool at once; tracked as a deferred consistency decision.
- **The desk decides ddof exposure is unnecessary** → flip `config.yaml:z_score_ddof.exposure.expose` to `false` + remove the field from the Input + drop the Advanced control.  Breaking change per the exposure-decision protocol.

---

## 5. Which backend doctrine does this module operationalise?

- **FM1** (module identity) — folder name equals backend `tool_name`.
- **FM3** (surface-tier capability declaration) — claims `generic_runnable` + `custom_build_surface` + `monitor_surface`.
- **FM4** (tier-set parsimony) — overridden by `rendering_density.md §1.2` for the dual-view mandate; Monitor is justified by `surface_contract.md §3.4` eligibility.
- **FM5** (display metadata) — `displayName` / `category` / `oneLineSummary` are PM-facing (per `lifecycle_checklist_template.md` Stage 1C).
- **FM7** (pure-spec assembly) — `module.ts` exports a pure value.
- **FM8** (surface-file contract) — `surfaces.buildExtended` + `surfaces.buildCompact` populated; files at canonical paths.
- **FM9** (routing-claim disclosure) — `typedView: null`; this module owns its own full surfaces.
- **FM10** (THESIS discipline) — this file.
- **FM11** (round-trip test) — [`__tests__/module.spec.ts`](__tests__/module.spec.ts) asserts the dual-view contract.
- **FM12** (loader presence) — module imported in [`src/modules/index.ts`](../../index.ts).
- Per the **methodology-exposure standalone-bridge contract** (`methodology_exposure.md §5`): own typed-detail endpoint `/api/v1/rates/detail/breakeven`; own service helper `fetchDetailBreakeven`; own frontend type `BreakevenInflationSimpleOutput`.
- Per the **rendering-density dual-view contract** (`rendering_density.md §1`): both views ship + both wired in `module.ts.surfaces` + files at canonical paths.

---

## Version log

| Version | Date | Change |
|---|---|---|
| v3 | 2026-05-28 | Phase-1 Stage-B dual-view + Monitor implementation per `rendering_density.md`.  Shipped `surfaces/BuildExtended.tsx`, `surfaces/BuildCompact.tsx`, `surfaces/monitor/BreakevenInflationWidget.tsx`, and the shared helper `surfaces/breakevenShared.ts`.  Tier set updated to `[generic_runnable, custom_build_surface, monitor_surface]`.  Standalone-bridge typed-detail endpoint shipped at `/api/v1/rates/detail/breakeven`.  Brought to parity with `get_real_yield_level_tool`. |
| v2 | 2026-05-26 | Stage 4f — rewrote the body to drop stale "Stage 3 scaffold" framing. |
| v1 | 2026-05-25 | Stage 3 scaffold — runtime tier only, no surfaces. |
