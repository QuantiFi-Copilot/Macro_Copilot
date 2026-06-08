# THESIS — `calculate_swap_breakeven_basis_simple_tool`

> Phase-1 Stage-B module under the dual-view rendering-density contract.  Brought to full parity with `calculate_breakeven_inflation_simple_tool` and `calculate_cross_market_inflation_swap_spread_tool`: ships an extended Build view (full canvas for single-tool queries) AND a compact Build view (grid card for multi-tool query DAGs) — both REQUIRED per [`rendering_density.md`](../../../../../docs_revamped/03_standards/rendering_density.md) §1 — plus a Monitor tile.

**Version:** v3 (dual-view + Monitor + standalone-bridge implementation)
**Module spec:** [`module.ts`](module.ts)
**Backend artifact:** `calculate_swap_breakeven_basis_simple_tool` (generic_runnable; standalone bridge via `/api/v1/rates/detail/swap-breakeven-basis`)
**Tier set:** `[generic_runnable, custom_build_surface, monitor_surface]`
**Category:** `cross_market_rv`
**Mockups:** [`./mockups/Compact.png`](./mockups/Compact.png) + [`./mockups/Extended.png`](./mockups/Extended.png) — committed alongside the module per the mockup-first workflow.

---

## 1. What surfaces does this module ship?

- **`generic_runnable`** — runtime-status tier; backend ships in `_PRIMITIVE_SPECS`.
- **`custom_build_surface`** — the Phase-1 **dual-view** contract:
  - **Extended Build view** ([`surfaces/BuildExtended.tsx`](surfaces/BuildExtended.tsx)) — full canvas mounted for single-tool queries.  Controls strip (single **Country Pair** dropdown + tenor + lookback + field), top-right Z-score / Percentile / Liquidity-Premium-Proxy cards, a bps-scale KPI strip (basis + changes + range + ZCIS-leg pct + bond-BE pct for the decomposition), basis-history chart in BPS with ±2σ / ±1.5σ bands, stretch-context panel, methodology card (carries the wire's `methodology_label` verbatim + per-leg index-family metadata + the load-bearing index-family caveat), lineage footer.
  - **Compact Build view** ([`surfaces/BuildCompact.tsx`](surfaces/BuildCompact.tsx)) — grid card mounted as a node body inside multi-tool query DAGs (e.g. *"compare USD 10Y vs EUR 10Y vs GBP 10Y swap-breakeven basis"*).  Headline 3-KPI strip (BASIS / 1D CHANGE / Z-SCORE), mini-chart in BPS with z-score bands, the index-family caveat footer + country-pair chip, click-to-expand affordance.
- **`monitor_surface`** — Monitor bento tile ([`surfaces/monitor/SwapBreakevenBasisWidget.tsx`](surfaces/monitor/SwapBreakevenBasisWidget.tsx)) showing one same-currency basis (pair × tenor × lookback parameterised; defaults USD / 10Y / 252).  Inflation-RV-desk read; inherently compact per `rendering_density.md §8`.  Methodology disclosure surfaces via the kicker pair label + the wire's `index_family_caveat` line + a `title=` tooltip carrying the full `methodology_label`.

---

## 2. What does the user read off each surface?

### Extended Build view

The full investigation canvas for ONE swap-breakeven basis.  A PM reads, in order: (a) the title row identifying the currency + tenor + as-of date with the country flag; (b) the top-right Z-Score / Percentile / Liquidity-Premium-Proxy cards (the last names BOTH legs' index families verbatim from the wire and carries the *"NOT a clean liquidity-premium read"* caveat inline); (c) the bps-scale KPI strip — current basis (bps), 1d / 5d / 1m changes (bps), z-score, percentile, 252d high/low, observation count, AND the ZCIS-leg pct + bond-implied breakeven pct so the `zcis − breakeven` decomposition is auditable on the same screen; (d) the basis-history chart with ±2σ / ±1.5σ z-score bands; (e) the stretch-context panel (regime caption + interpretation); (f) the methodology card (construction formula, sign-convention disclosure, ZCIS index family + lag + interpolation, linker index family + lag, the load-bearing `index_family_caveat`, the linker country caveat, and the full `methodology_label` from the YAML); (g) the lineage footer.  The single **Country Pair** dropdown expands to ALL THREE curve_family params (V1 same-currency invariant means the pair key uniquely picks the triplet — a single dropdown enforces validity at the input layer).

### Compact Build view

The at-a-glance grid card.  A PM sees this in a multi-tool prompt.  They read THREE numbers + a sparkline: current basis (bps, with the pct in subtext), 1-day change (bps, tone-coloured), rolling 252d z-score (with regime caption — Normal / Elevated / Extreme).  The mini-chart shows the basis history in BPS with ±2σ / ±1.5σ z-score bands.  The footer carries the wire's `index_family_caveat` when surfaced (e.g. *"ZCIS leg references CPI-U; linker leg's index family not surfaced — basis may carry index-family drift"*) or the per-pair static caveat *"Liquidity-premium proxy; CPI-U ZCIS vs bond BE."* otherwise.  Country flag + pair chip in the identity row.  The expand arrow opens the extended view in a modal.

### Monitor tile

One same-currency basis, desk-glanceable: current basis (bps + pct subtext), 1-day change (bps), 252d percentile, and a high/low range strip with a current marker.  Pair label + flag + z-score badge in the header (badge carries the full `methodology_label` via tooltip).  The wire's `index_family_caveat` line surfaces in the body so the load-bearing wire-honesty disclosure is reachable without expanding.

---

## 3. Why these surfaces and not others?

**Why the dual-view contract** (both extended + compact REQUIRED): per [`rendering_density.md §1.1`](../../../../../docs_revamped/03_standards/rendering_density.md) multi-tool prompts are the default reality; a tool that ships only an extended view falls back to a generic artifact-type card in multi-tool DAGs.  Swap-breakeven basis is canonically read across countries (*"USD vs EUR vs GBP 10Y swap-breakeven basis — where's the dispersion?"*) and against the spot bond breakeven primitive, so the compact view earns its keep immediately.  The standard OVERRIDES FM4 parsimony for the dual-view mandate.

**Why these three compact KPIs** (current basis + 1d change + z-score): they're the desk-canonical "first three numbers" a PM reads off a basis snapshot — *"where is the basis now / how much did it move today / is this stretched?"*.  Alternatives considered + rejected: basis in % (the bps representation is the desk-canonical spread unit and reads better in a dense card; the pct value still appears in the BASIS card's subtext for cross-checking); 252d percentile (already conveyed by the z-score regime + band overlay); the ZCIS / bond-BE underlying legs (they're decomposition detail, surfaced in the extended view's KPI strip, not headline).  Option (c) shell-standard density mirrors the Phase-1 pilot `calculate_breakeven_inflation_simple_tool` precedent — no per-tool override.

**Why the load-bearing wire-honesty caveat is REQUIRED inline** (not hidden): mis-reading a swap-breakeven basis as a clean liquidity-premium read is a genuine desk error — the basis ALSO reflects index-lag differences between ZCIS conventions and the linker's realised CPI accrual, linker on-the-run / liquidity premium effects in the nominal-vs-real decomposition, AND structural ZCIS-vs-linker-breakeven basis present even in benign markets.  Per `rendering_density.md §2.2` + §12 the methodology caveat must be reachable in the compact view; here it's the canonical one-liner in the footer + a top-right card on the extended view, and the same honesty disclosure threads onto the wire via `current_metrics.methodology_label` (sourced from `config.yaml:methodology.what_it_does`, not hardcoded).  When the legs reference different inflation indices the wire's structured `current_metrics.index_family_caveat` takes precedence over the static fallback.

**Why a single Country-Pair dropdown** (not three): a V1 swap-breakeven basis is a same-currency object by construction; the compute layer rejects cross-currency triplets transitively via the inner breakeven primitive's same-country guard.  Surfacing three independent dropdowns (ZCIS family + nominal + linker) would invite invalid selections; the single dropdown enforces validity at the input layer and matches the spot-breakeven precedent.

**Why a typed-detail endpoint** (`/api/v1/rates/detail/swap-breakeven-basis`): per [`methodology_exposure.md §5`](../../../../../docs_revamped/03_standards/methodology_exposure.md) every standalone module ships its own typed bridge; the generic `/run` route stays the LLM-facing surface.  No reuse of `CrossMarketInflationSwapSpreadOutput` (that's same-tenor ZCIS-vs-ZCIS; this is ZCIS-vs-bond-BE) and no reuse of `BreakevenInflationSimpleOutput` (that's bond-only).

---

## 4. What would change the design?

- **A carry- / index-lag-adjusted swap-breakeven basis primitive** lands (`methodology.planned_extensions` in `config.yaml`) → it ships as a SEPARATE module (different concept), not a knob on this one; the extended view could then offer a side-by-side "simple vs adjusted" framing.
- **A new linker market with a matching ZCIS curve** (e.g. JPY, CAD) lands → add the triplet to `PAIR_REGISTRY` in [`surfaces/swapBreakevenBasisShared.ts`](surfaces/swapBreakevenBasisShared.ts) + the tenor intersection map.  No shell changes.
- **Cross-currency swap-breakeven basis** is exposed as a knob → would be a separate primitive shape (per the backend's V1 wire freeze on `_curve_families_must_differ` + same-country invariant); a new module ships, this one stays untouched.
- **The desk decides z-score window overrides should be exposed at the API layer** → flip the YAML-locked conventions to `exposure.expose=true` + add the four overrides to the Input + thread them through the extended view's controls strip.  Mirror the breakeven primitive's overrides.

---

## 5. Which backend doctrine does this module operationalise?

- **FM1** (module identity) — folder name equals backend `tool_name`.
- **FM3** (surface-tier capability declaration) — claims `generic_runnable` + `custom_build_surface` + `monitor_surface`.
- **FM4** (tier-set parsimony) — overridden by `rendering_density.md §1.2` for the dual-view mandate; Monitor is justified by `surface_contract.md §3.4` eligibility (canonical morning-briefing inflation-RV read).
- **FM5** (display metadata) — `displayName` / `category` / `oneLineSummary` are PM-facing (per `lifecycle_checklist_template.md` Stage 1C).
- **FM7** (pure-spec assembly) — `module.ts` exports a pure value.
- **FM8** (surface-file contract) — `surfaces.buildExtended` + `surfaces.buildCompact` populated; files at canonical paths.
- **FM9** (routing-claim disclosure) — `typedView: null`; this module owns its own full surfaces.
- **FM10** (THESIS discipline) — this file.
- **FM11** (round-trip test) — [`__tests__/module.spec.ts`](__tests__/module.spec.ts) asserts the dual-view contract.
- **FM12** (loader presence) — module imported in [`src/modules/index.ts`](../../index.ts).
- Per the **methodology-exposure standalone-bridge contract** (`methodology_exposure.md §5`): own typed-detail endpoint `/api/v1/rates/detail/swap-breakeven-basis`; own service helper `fetchDetailSwapBreakevenBasis`; own frontend type `SwapBreakevenBasisSimpleOutput`.  The wire-honesty disclosure flows through `current_metrics.methodology_label` AND the structured `index_family_caveat` — never hardcoded as a TS literal.
- Per the **rendering-density dual-view contract** (`rendering_density.md §1`): both views ship + both wired in `module.ts.surfaces` + files at canonical paths.

---

## Mockup conformance

- **Extended** ([`mockups/Extended.png`](mockups/Extended.png)) — title "USD 10Y SWAP-BE BASIS · ZCIS CPI-U • 10Y minus UST/TIPS 10Y breakeven", category tag row, three top-right cards (Z-Score / Percentile / Liquidity-Premium proxy carrying the index-family caveat), single Country-Pair dropdown plus tenor / lookback / field, bps-scale KPI strip with ZCIS-leg + bond-BE decomposition, basis-history chart in BPS with ±2σ band overlays, methodology card carrying the wire `methodology_label`, lineage footer.  Faithful match.
- **Compact** ([`mockups/Compact.png`](mockups/Compact.png)) — header chip with category + status pill, identity row "USD · 10Y SWAP-BE BASIS" with flag, three KPI tiles (BASIS bps + 1D CHANGE bps + Z-SCORE 252D) in mockup order, sparkline in BPS with z-score bands, footer caveat from the wire's `index_family_caveat`, fresh-status badge, expand arrow.  Faithful match.

---

## Version log

| Version | Date | Change |
|---|---|---|
| v3 | 2026-06-08 | Dual-view + Monitor + standalone-bridge implementation per `rendering_density.md` + `methodology_exposure.md §5`.  Shipped `surfaces/BuildExtended.tsx`, `surfaces/BuildCompact.tsx`, `surfaces/monitor/SwapBreakevenBasisWidget.tsx`, and the shared helper `surfaces/swapBreakevenBasisShared.ts`.  Tier set updated to `[generic_runnable, custom_build_surface, monitor_surface]`.  Standalone-bridge typed-detail endpoint shipped at `/api/v1/rates/detail/swap-breakeven-basis`.  Brought to parity with `calculate_breakeven_inflation_simple_tool` + `calculate_cross_market_inflation_swap_spread_tool`. |
| v2 | 2026-05-26 | Stage 4f — rewrote the body to drop stale "Stage 3 scaffold" framing. |
| v1 | 2026-05-25 | Stage 3 scaffold — runtime tier only, no surfaces. |
