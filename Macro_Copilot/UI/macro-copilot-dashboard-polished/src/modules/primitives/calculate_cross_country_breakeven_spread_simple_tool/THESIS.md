# THESIS — `calculate_cross_country_breakeven_spread_simple_tool`

> First `inflation_indexed_bonds` CROSS-COUNTRY tool brought to dual-view + standalone-bridge parity with the Phase-1 pilots.  Ships an extended Build view (full canvas for single-tool queries) AND a compact Build view (grid card for multi-tool query DAGs) — both REQUIRED per [`rendering_density.md`](../../../../../docs_revamped/03_standards/rendering_density.md) §1 — plus a Monitor tile.

**Version:** v3 (factory dual-view + Monitor implementation)
**Module spec:** [`module.ts`](module.ts)
**Backend artifact:** `calculate_cross_country_breakeven_spread_simple_tool` (generic_runnable; standalone bridge via `/api/v1/rates/detail/cross-country-breakeven-spread`)
**Tier set:** `[generic_runnable, custom_build_surface, monitor_surface]`
**Category:** `cross_market_rv`
**Mockups:** [`./mockups/Compact.png`](./mockups/Compact.png) + [`./mockups/Extended.png`](./mockups/Extended.png) — committed alongside the module per the mockup-first workflow.

---

## 1. What surfaces does this module ship?

- **`generic_runnable`** — runtime-status tier; backend ships in `_PRIMITIVE_SPECS`.
- **`custom_build_surface`** — the dual-view contract:
  - **Extended Build view** ([`surfaces/BuildExtended.tsx`](surfaces/BuildExtended.tsx)) — full canvas mounted for single-tool queries.  Controls strip (two **Country A / Country B** packed-pair dropdowns + tenor + lookback + field), top-right Z-score / Percentile / Index-Families cards, a bps-scale KPI strip (spread + 1d/5d/1m changes + range + percentile + observation count + per-country BREAKEVEN BPS decomposition), spread-history chart with ±2σ z-score bands, stretch-context panel, methodology card (per-country `inflation_index_family` / `index_long` + the load-bearing inflation-compensation caveat + the `methodology_label` disclosure), lineage footer.
  - **Compact Build view** ([`surfaces/BuildCompact.tsx`](surfaces/BuildCompact.tsx)) — grid card mounted as a node body inside multi-tool query DAGs (e.g. *"compare UK-US vs FR-US vs CA-US 10Y BE"*).  Title `UK-US 10Y BE` with subtitle `UK 10Y BE (RPI) minus US 10Y BE (CPI-U)`, headline 3-KPI strip (SPREAD bps + 1D CHANGE bps + Z-SCORE 252D), mini-chart with z-score bands, footer caveat `RPI vs CPI-U · not fungible inflation measures` + per-country flag chips, click-to-expand affordance.
- **`monitor_surface`** — Monitor bento tile ([`surfaces/monitor/CrossCountryBreakevenSpreadWidget.tsx`](surfaces/monitor/CrossCountryBreakevenSpreadWidget.tsx)) showing one cross-country breakeven spread (country_a_pair × country_b_pair × tenor × lookback parameterised; defaults UK_GILT/GBP_LINKER vs UST/USD_TIPS / 10Y / 252).  Cross-country bond-implied breakeven spreads are a desk-canonical cross-CB inflation-divergence read per `surface_contract.md §3.4`.  Inherently compact per `rendering_density.md §8`.

---

## 2. What does the user read off each surface?

### Extended Build view

The full investigation canvas for ONE cross-country breakeven spread.  A PM reads, in order: (a) the title row `UK-US 10Y BE` with the per-country subtitle `UK 10Y BE (RPI) minus US 10Y BE (CPI-U)` + the two flag chips (🇬🇧🇺🇸) + as-of date; (b) the top-right Z-Score / Percentile / Index-Families cards (the last carries the load-bearing index-family caveat — `RPI vs CPI-U · not fungible inflation measures` — derived from the per-country (nominal, linker) pair metadata + corroborated by the wire's `methodology_label`); (c) the bps-scale KPI strip — current SPREAD (bps + percent subtext), 1d/5d/1m changes (bps), z-score, percentile, 252d high/low, observation count, AND the per-country BREAKEVEN BPS decomposition (BE A / BE B) so the `spread = breakeven_a − breakeven_b` math is auditable on the same screen; (d) the spread-history chart with z-score band overlays; (e) the stretch-context panel (`UK-US cross-country breakeven spread is elevated wider than its trailing-year mean ... `); (f) the methodology card — construction formula, sign convention (`country_a − country_b`), field, composition pattern (two `calculate_breakeven_inflation_simple` calls per country → inner-join → bps subtraction), per-country (nominal, linker) pair + inflation-index family long form, the load-bearing inflation-compensation + index-family caveats, the wire's full `methodology_label` disclosure; (g) the lineage footer.

### Compact Build view

The at-a-glance grid card.  A PM sees this in a multi-tool prompt.  They read THREE numbers + a sparkline: current SPREAD (bps, subtext in percent), 1-day change (bps, tone-coloured, subtext in percent), rolling 252d z-score (with regime caption — Normal / Elevated / Extreme).  The mini-chart shows the spread history with ±2σ / ±1.5σ z-score bands.  The footer carries the load-bearing one-line caveat — `RPI vs CPI-U · not fungible inflation measures` — derived from the wire's (nominal, linker) pair identifiers (the long-form `methodology_label` is surfaced in full on the Extended methodology card), plus the per-country flag chips (e.g. `🇬🇧 🇺🇸 · UK-US`).  The expand arrow opens the extended view in a modal.

### Monitor tile

One cross-country breakeven spread, desk-glanceable: current spread (bps + percent subtext), 1-day change (bps, sign-coloured), 252d percentile, a high/low range strip with a current marker, AND the index-family caveat one-liner on a separate row (per `rendering_density.md §2.2` methodology MUST remain reachable in compact contexts; the Monitor is not a compact-Build but the same principle applies for honest disclosure).  Pair flags + z-score badge in the header — the z-badge's `title=` tooltip carries the full `methodology_label` prose for desk readers who want the formal disclosure inline.

---

## 3. Why these surfaces and not others?

**Why the dual-view contract** (both extended + compact REQUIRED): per [`rendering_density.md §1.1`](../../../../../docs_revamped/03_standards/rendering_density.md) multi-tool prompts are the default reality; cross-country breakeven spreads are characteristically queried in batches (*"UK-US vs FR-US vs CA-US at 10Y"* — 3 calls; *"UK-US BE at 2Y/5Y/10Y/30Y"* — 4 calls); a tool that ships only an extended view falls back to a generic artifact-type card in those DAGs.  The standard OVERRIDES FM4 parsimony for the dual-view mandate.

**Why these three compact KPIs** (SPREAD bps + 1D CHANGE bps + Z-SCORE 252D): they're the desk-canonical "first three numbers" a PM reads off a cross-country breakeven snapshot — *"where is the spread now / how much did it move today / is this stretched?"*.  Alternatives considered + rejected: SPREAD in percent (the bps form is the desk-canonical change unit and reads better in a dense card — percent shown as subtext); 252d percentile (already conveyed by the z-score regime + band overlay; surfaced in the extended view's KPI strip); per-country breakeven levels (decomposition detail, surfaced in the extended view's KPI strip, not headline); 1m change (same reason — the daily move is the headline read).

**Why the index-family caveat is REQUIRED inline** (not hidden): misreading a cross-country breakeven spread as a clean expected-inflation differential is a genuine desk error — USD TIPS reference US CPI-U non-seasonally adjusted, UK GILT linkers reference RPI (legacy) / CPIH (newer), FR OAT / DE BUND / IT BTP linkers reference Eurozone HICPxT, Canadian RRBs reference Canada CPI, and these are structurally distinct inflation measures.  Each leg ALSO carries an inflation risk premium and a relative liquidity premium between its nominal sovereign and its linker (inherited from `calculate_breakeven_inflation_simple`).  The spread therefore captures BOTH inflation-expectation differentials AND structural index-family + premia differences.  Per `rendering_density.md §2.2` + §12 the methodology caveat must be reachable in the compact view; here it's the canonical one-liner in the footer (`RPI vs CPI-U · not fungible inflation measures`), derived from the wire's (nominal, linker) pair identifiers; the full long-form `methodology_label` (the YAML-sourced wire-honesty disclosure) surfaces verbatim on the extended view's methodology card and via the Monitor widget's `title=` tooltip.

**Why two packed-pair dropdowns** (not four separate nominal/linker selects): the cross-country invariant + the per-country same-country invariant together mean the (nominal, linker) tuple per country is a SINGLE atomic choice from the desk's point of view — picking `UK_GILT` necessarily means `GBP_LINKER` and vice versa.  Surfacing the packed pair (e.g. `UK_GILT/GBP_LINKER`) as one select makes the cross-country invariant unrepresentable in the UI (the snap-fallback in `handleControlChange` enforces `country_a_pair != country_b_pair`).  The same packed-pair convention propagates to the URL state, the Monitor widget params, and the shared helper's lookup registry.

**Why a typed-detail endpoint** (`/api/v1/rates/detail/cross-country-breakeven-spread`): per [`methodology_exposure.md §5`](../../../../../docs_revamped/03_standards/methodology_exposure.md) every standalone module ships its own typed bridge; the generic `/run` route stays the LLM-facing surface.  This is the FIRST inflation_indexed_bonds CROSS-COUNTRY standalone-bridge endpoint — the sibling cross-country real-yield primitive (already scaffolded; tool 27 in the build_order) will follow the same shape.

---

## 4. What would change the design?

- **A new country (nominal, linker) pair lands** (e.g. JPY_JGB / JPY_LINKER, AUD_ACGB / AUD_TIB) → add the entry to `PAIR_REGISTRY` in [`surfaces/crossCountryBreakevenSpreadSimpleShared.ts`](surfaces/crossCountryBreakevenSpreadSimpleShared.ts) with its `countryShort` / `indexShort` / `indexLong` / `flag`.  No shell changes.  The wire's `current_metrics.methodology_label` continues to name the load-bearing caveat verbatim; the static `shortIndexCaveat()` helper consults the registry purely for the compact-view one-liner.
- **An FX-adjusted cross-country breakeven primitive lands** (would harmonise the index families via a CPI-U-equivalent translation layer, or strip the FX-basis component) → it ships as a SEPARATE module (different concept; the catalog entry's "simple" name explicitly denotes the unadjusted variant), not a knob on this one.  The extended view could then offer a side-by-side "raw spread vs FX-adjusted spread" framing if both modules ship.
- **The desk decides z-score conventions need exposure** at the Pydantic Input layer → promote `z_score_window_days` / `z_score_min_periods` / `z_score_ddof` from YAML-locked → exposed (per the `methodology_exposure.md §3` decision protocol); add Advanced controls in [`surfaces/BuildExtended.tsx`](surfaces/BuildExtended.tsx) mirroring `calculate_breakeven_inflation_simple_tool`'s pattern.
- **Per-leg index-family metadata makes it onto the wire** (currently `methodology_label` carries the caveat as prose; the per-leg `inflation_index_family` / `index_lag` / `interpolation` / `underlying_index` fields are NOT currently on the wire for this primitive) → swap the static `PAIR_REGISTRY` lookup for the wire's structured per-leg metadata (single source of truth per `methodology_exposure.md §1`).  The static registry becomes a fallback only.

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
- **P5** (honest disclosure) — the index-family caveat surfaces in BOTH compact (one-liner footer, derived from the wire's (nominal, linker) pair identifiers) and extended (per-country metadata + caveat + the wire's `methodology_label` verbatim) views.
- Per the **methodology-exposure standalone-bridge contract** (`methodology_exposure.md §5`): own typed-detail endpoint `/api/v1/rates/detail/cross-country-breakeven-spread`; own service helper `fetchDetailCrossCountryBreakevenSpread`; own frontend type `CrossCountryBreakevenSpreadSimpleOutput`.
- Per the **rendering-density dual-view contract** (`rendering_density.md §1`): both views ship + both wired in `module.ts.surfaces` + files at canonical paths.

---

## Mockup conformance

The Extended view's identity row, KPI strip ordering (`SPREAD / 1D / 5D / 1M / Z-SCORE / PERCENTILE / 252D HIGH / 252D LOW / OBS / BE A / BE B`), top-right cards (Z-score / Percentile / Index Families), spread-history chart with ±2σ z-bands, methodology card, and lineage footer all mirror `mockups/Extended.png`.  The Compact view's three headline KPIs (SPREAD bps / 1D CHANGE bps / Z-SCORE 252D), mini-chart with z-bands, footer caveat line (`RPI vs CPI-U · not fungible inflation measures`), per-country flag chips, expand affordance, and tone cues (sign colouring + |z| ≥ 1.5 amber band) mirror `mockups/Compact.png`.  Per the Option (c) precedent recorded in the catalog `design_guardrails`, the compact density matches the shell-standard convention used by the sibling cross-market ZCIS spread.

---

## Version log

| Version | Date | Change |
|---|---|---|
| v3 | 2026-06-08 | Factory dual-view + Monitor implementation per `rendering_density.md`.  Shipped `surfaces/BuildExtended.tsx`, `surfaces/BuildCompact.tsx`, `surfaces/monitor/CrossCountryBreakevenSpreadWidget.tsx`, and the shared helper `surfaces/crossCountryBreakevenSpreadSimpleShared.ts`.  Tier set updated to `[generic_runnable, custom_build_surface, monitor_surface]`.  Standalone-bridge typed-detail endpoint shipped at `/api/v1/rates/detail/cross-country-breakeven-spread`.  Brought to parity with `calculate_cross_market_inflation_swap_spread_tool`.  First inflation_indexed_bonds cross-country tool under the dual-view contract. |
| v2 | 2026-05-26 | Stage 4f — rewrote the body to drop stale "Stage 3 scaffold" framing. |
| v1 | 2026-05-25 | Stage 3 scaffold — runtime tier only, no surfaces. |
