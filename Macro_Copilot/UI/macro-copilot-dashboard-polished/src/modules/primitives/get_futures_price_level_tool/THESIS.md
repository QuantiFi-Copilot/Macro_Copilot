# THESIS — `get_futures_price_level_tool` (bond_futures domain)

> Dispatch-1 build under the dual-view rendering-density contract.  Brought to parity with the snapshot-shape reference twin `policy_futures_get_futures_price_level_tool` (Batch 1 d8e8233): ships an extended Build view (full canvas for single-tool queries) AND a compact Build view (grid card for multi-tool query DAGs) — both REQUIRED per [`rendering_density.md`](../../../../../docs_revamped/03_standards/rendering_density.md) §1 — plus a Monitor tile.

**Version:** v3 (Dispatch-1 dual-view + Monitor implementation)
**Module spec:** [`module.ts`](module.ts)
**Backend artifact:** `get_futures_price_level_tool` (generic_runnable; standalone bridge via `/api/v1/rates/detail/bond-futures-price`).  MCP wrapper registers the bare `get_futures_price_level_tool` inside the `bond_futures` MCP server; the canonical workflow-registry name disambiguates against the sibling `policy_futures_get_futures_price_level_tool` (same MCP function name registered in a different MCP server — bond_futures holds the BARE folder + tool.name per FM1).
**Tier set:** `[generic_runnable, custom_build_surface, monitor_surface]`
**Category:** `snapshots`
**Mockups:** [`./mockups/Compact.png`](./mockups/Compact.png) + [`./mockups/Extended.png`](./mockups/Extended.png) — committed alongside the module per the mockup-first workflow.

---

## 1. What surfaces does this module ship?

- **`generic_runnable`** — runtime-status tier; backend ships in `_PRIMITIVE_SPECS`.
- **`custom_build_surface`** — the dual-view contract:
  - **Extended Build view** ([`surfaces/BuildExtended.tsx`](surfaces/BuildExtended.tsx)) — full canvas mounted for single-tool queries.  Controls strip (Curve Family / Contract Code / Lookback / Field), top-right Z-score / Percentile / Contract-Context cards (the third carries the quote_units + notation disclosure inline so the desk sees points-vs-percent-of-par + 32nds-vs-decimal without opening the methodology drawer), a 9-cell KPI strip on the PRICE axis with 1D / 5D / 1M raw-subtraction changes in the contract's native quote_units, the price history chart with ±2σ z-score bands + mean line, stretch-context panel, methodology card sourced from the backend's `methodology_disclosure`, lineage footer.
  - **Compact Build view** ([`surfaces/BuildCompact.tsx`](surfaces/BuildCompact.tsx)) — grid card mounted as a node body inside multi-tool query DAGs (e.g. *"compare TY1 vs RX1 vs JB1 fronts"*).  Headline 3-KPI strip (PRICE in quote_units / 1D CHANGE in quote_units / Z-SCORE (252D)) with a 32nds-subtext for UST family, mini-chart with ±2σ z-score bands, the V1-monitors-only / CTD-deferred caveat footer, click-to-expand affordance.
- **`monitor_surface`** — Monitor bento tile ([`surfaces/monitor/BondFuturesPriceLevelWidget.tsx`](surfaces/monitor/BondFuturesPriceLevelWidget.tsx)) showing one bond-futures rolling-generic (curve_family × contract_code × lookback parameterised; defaults `UST_FUT` / `TY1` / 252).  Inherently compact per [`rendering_density.md §8`](../../../../../docs_revamped/03_standards/rendering_density.md).  Front bond-futures prices are a desk-canonical morning-screen read for sovereign rates; eligibility per `surface_contract.md §3.4`.  Widget id `bond_futures_price_level` is globally unique (NOT `policy_futures_price_level` — the cousin holds that id).

---

## 2. What does the user read off each surface?

### Extended Build view

The full investigation canvas for ONE rolling-generic bond-futures contract.  A PM reads, in order: (a) the title row identifying the contract (e.g. *"TY1 · CME 10-Year U.S. Treasury Note Futures 🇺🇸"*) + as-of date; (b) the top-right Z-Score / Percentile / Contract-Context cards (the last carries the per-contract `quote_units` + notation disclosure — "points · 32nds notation (CBOT)" for UST family, "% of par value · decimal" for Bund/OAT/BTP/Bono/Bobl/Schatz/Buxl, "GBP · decimal" for Gilt, "100 - yield · decimal" for ASX YM/XM); (c) the 9-cell KPI strip on the PRICE axis — PRICE in quote_units with a 32nds-subtext for UST, 1D / 5D / 1M CHANGE in quote_units (raw subtractions; with a percent-of-price subtext), Z-SCORE (252D) with regime caption, PERCENTILE (252D), 252D HIGH / LOW in quote_units, OBSERVATIONS count; (d) the price history chart with ±2σ / ±1.5σ z-score bands + mean line overlay; (e) the stretch-context panel; (f) the methodology card — contract identity (master stem + tenor + long label), front underlying (`security_name` / `expiry_date`), quote convention (`quote_units` + the special "(yield = 100 − price)" note for ASX), notation (32nds vs decimal-N-dp), contract size, field, z-score model, trailing range, and the full `methodology_disclosure` honesty string threaded from the backend (PR10 / P5 — NOT a hardcoded TS literal); (g) the lineage footer.

### Compact Build view

The at-a-glance grid card.  A PM sees this in a multi-tool prompt.  They read THREE numbers + a sparkline: current PRICE in the contract's quote_units (with the desk-canonical 32nds subtext for UST family — e.g. *"129'08 · 129 and 8/32nds"*), 1-day PRICE change in the same quote_units (tone-coloured: positive price = rally / lower yield → mint; negative price = sell-off / higher yield → coral), rolling 252d z-score on the price level (regime-captioned — Normal / Elevated / Extreme).  The mini-chart shows the price history with z-score bands.  The footer carries the *"V1 monitors-only; CTD analytics (basis, repo, DV01) not in V1."* caveat.  The expand arrow opens the extended view in a modal.

### Monitor tile

One bond-futures rolling-generic, desk-glanceable: kicker (`TY1 · 10Y UST 🇺🇸`), title (`Bond Futures Price`), z-score badge.  Body: current PRICE in quote_units (32nds for UST; decimal otherwise) + units suffix, 1d change in the same notation (positive = mint, negative = coral) + 252d percentile + observation count, and a 252d PRICE range strip with a current marker.  Methodology disclosure reachable via the price tile's `title` tooltip (V1 monitors-only caveat).

---

## 3. Why these surfaces and not others?

**Why the dual-view contract** (both extended + compact REQUIRED): per [`rendering_density.md §1.1`](../../../../../docs_revamped/03_standards/rendering_density.md) multi-tool prompts are the default reality; a tool that ships only an extended view falls back to a generic artifact-type card in multi-tool DAGs.  Bond-futures prices are commonly compared across sovereigns (*"TY1 vs RX1 vs JB1"*) and across the curve (*"TY1 vs US1 vs FV1"*), so the compact view earns its keep immediately.  The standard explicitly OVERRIDES FM4 parsimony for the dual-view mandate.

**Why these three compact KPIs** (PRICE / 1D CHANGE / Z-SCORE): they're the desk-canonical "first three numbers" a PM reads off a bond-futures snapshot — *"where is the price now / how much did it move today / is this stretched?"*.  The level is shown in the contract's native quote_units (the trader's tape) with the 32nds subtext for UST (the desk's canonical CBOT notation).  The 1D change is in the same quote_units (raw subtraction) with a percent-of-price subtext under it so the trader sees both the tick move and the magnitude in basis-point-equivalent terms.  Alternatives considered + rejected: showing CTD-implied yield as a headline KPI (NOT a primitive in V1 per ADR 0013 — would lie about the data), surfacing 5D / 1M changes as headline KPIs (lower-frequency reads; surfaced in the extended view), surfacing 252d percentile alone (already conveyed by the z-score regime + band overlay).

**Why a SINGLE Curve Family + Contract Code pair** (not (curve_family, tenor)): per TD#11 the rolling-generic stem is the canonical disambiguator — TY1 vs UXY1 are both `UST_FUT 10Y`; US1 vs WN1 both `UST_FUT 30Y`.  Exposing tenor as the input key would conflate these.  The backend Pydantic `FuturesPriceLevelInput` enforces this with `(curve_family, contract_code)` keying; the frontend mirrors it.

**Why the z-score lives on the PRICE axis** (not a yield axis): per ADR 0013 V1 the bond_futures domain is PRICE-space monitoring only — CTD-implied yield is NOT yet a primitive.  The wire `z_score` is on the PRICE level (252d rolling); the frontend honours that.  When the CTD-implied-OIS primitive lands (Phase-4 per ADR 0013) it will ship as a SEPARATE module on the yield axis.

**Why a typed-detail endpoint** (`/api/v1/rates/detail/bond-futures-price`): per [`methodology_exposure.md §5`](../../../../../docs_revamped/03_standards/methodology_exposure.md) every standalone module ships its own typed bridge; the generic `/run` route stays the LLM-facing surface.  Same pattern the policy_futures cousin and sovereign / linker / OIS standalone bridges use.

**Why no z-score override controls in the extended view**: per ADR 0013 V1 deterministic mode there is NO LLM-facing override path for conventions; `z_score_window_days` / `trailing_range_window_days` etc. are YAML-locked at every call site.  Surfacing them as controls would silently shadow the YAML; the extended view honours the actual contract instead.

**Why no `as_of_date` control** (unlike the policy_futures cousin): the bond_futures `FuturesPriceLevelInput` does NOT accept `as_of_date` — the backend anchors at the universe's last observed trade_date.  Exposing an as-of picker would silently no-op.

---

## 4. What would change the design?

- **A CTD-implied-OIS primitive** lands (Phase-4 per ADR 0013) → it ships as a SEPARATE module (different concept — maps the front contract onto a calibrated OIS forward curve via CTD basis).  This module remains the rolling-generic price read; the methodology card's *"CTD-implied yield NOT a primitive in V1"* caveat stays.
- **A new bond_futures curve family** lands (e.g. `NZ_FUT`, `SE_FUT`) → no code change required beyond the per-tool `CONTRACT_REGISTRY` in [`surfaces/bondFuturesPriceShared.ts`](surfaces/bondFuturesPriceShared.ts) — add the contract entry with its flag / tenor / quote_units / notation / decimals and the controls + Monitor tile pick it up.
- **A backend exposes z-score overrides** (`z_score_window_days` etc.) → add the matching `advanced: true` ControlDescriptors to the extended view's controls strip and pipe them into the typed-detail endpoint params.  Today the schema does not expose them.
- **A spread / butterfly primitive** lands in the bond_futures domain (calendar / cross-market / curve) → it ships as a SEPARATE module mirroring this one's shape; the shared helper can be lifted to a domain-level `bondFuturesShared.ts` if it grows beyond price-level reuse.
- **A workflow template** using this primitive (e.g. bond-futures event_study) lands → the compact view fires as a node body in that workflow's dashboard.  No design change required — the compact view is workflow-agnostic by contract.

---

## 5. Which backend doctrine does this module operationalise?

- **FM1** (module identity) — folder name equals backend `tool_name` exactly (the BARE `get_futures_price_level_tool` — bond_futures holds this name; the policy_futures cousin uses the prefixed `policy_futures_get_futures_price_level_tool`).
- **FM3** (surface-tier capability declaration) — claims `generic_runnable` + `custom_build_surface` + `monitor_surface`.
- **FM4** (tier-set parsimony) — overridden by [`rendering_density.md §1.2`](../../../../../docs_revamped/03_standards/rendering_density.md) for the dual-view mandate; Monitor is justified by `surface_contract.md §3.4` eligibility (desk-canonical front-month bond-futures read).
- **FM5** (display metadata) — `displayName` / `category` / `oneLineSummary` are PM-facing.
- **FM7** (pure-spec assembly) — [`module.ts`](module.ts) exports a pure value; no side effects.
- **FM8** (surface-file contract) — `surfaces.buildExtended` + `surfaces.buildCompact` populated; files at canonical paths; `monitorWidgets` array populated for the Monitor tile.
- **FM9** (routing-claim disclosure) — `typedView: null`; this module owns its own full surfaces.
- **FM10** (THESIS discipline) — this file.
- **FM11** (round-trip test) — [`__tests__/module.spec.ts`](__tests__/module.spec.ts) asserts the dual-view contract + standalone-bridge invariants.
- **FM12** (loader presence) — module imported in [`src/modules/index.ts`](../../index.ts) at the alphabetical position (already present from Stage 3; the import target now resolves to the dual-view spec instead of the Stage-3 stub).
- **PR10 / P5** (methodology disclosure sourcing) — the methodology card sources the honesty disclosure from `output.methodology_disclosure` (threaded from `compute()`'s `_build_methodology_disclosure`, which incorporates `config.yaml:methodology` + per-contract runtime context), NOT a hardcoded TS literal.
- **ADR 0013** (`bond_futures` V1 monitors-only) — the price axis is the canonical level (CTD-implied yield NOT a primitive in V1), the methodology card carries the rolling-generic-price-read caveat verbatim, the `time_series` is bespoke (PRICE-unit extension to `TimeSeriesUnits` is ADR-gated and not authorised in V1).
- **TD#11** (rolling-generic stem disambiguator) — the input layer is keyed by `(curve_family, contract_code)` rather than `(curve_family, tenor)`.
- Per the **methodology-exposure standalone-bridge contract** (`methodology_exposure.md §5`): own typed-detail endpoint `/api/v1/rates/detail/bond-futures-price`; own service helper `fetchDetailBondFuturesPrice`; own frontend type `BondFuturesPriceLevelOutput` (namespaced to avoid a clash with the policy_futures cousin's same-named-but-different shape).
- Per the **rendering-density dual-view contract** (`rendering_density.md §1`): both views ship + both wired in `module.ts.surfaces` + files at canonical paths.

---

## Mockup conformance

Per the Option-(c) precedent (Batch 1 d8e8233 / breakeven_butterfly fdac7d2), this module's Compact view keeps the SHELL-STANDARD density: 3 KPIs + a single identity line.  The mockup at `./mockups/Compact.png` shows a higher-density layout — the headline 3-cell strip PLUS a secondary 6-cell row (5D CHANGE, 1M CHANGE, 252D PCTL, 252D HIGH, 252D LOW, OBSERVATIONS) — that would require extending `BuildCompactShell` with a `secondaryKpis` slot (and a matching `subtitle` extension on the identity row).  Those shell extensions would touch 10+ shipped compact views across the rates surface and constitute a separate epic; the deviation is shell-density-driven, not data-driven.  The data IS available in the typed-detail payload (`extendedKPIs(data)` in [`surfaces/bondFuturesPriceShared.ts`](surfaces/bondFuturesPriceShared.ts) exposes every secondary cell the mockup shows), and the full 9-cell strip remains accessible in the extended view (one tap on the compact card's expand affordance).

The Extended view is faithful to `./mockups/Extended.png`: identity row with contract code + long label + flag, top-right Z-Score / Percentile / Contract-Context cards (the third surfaces the quote_units + notation disclosure that the mockup shows in the upper-right corner), 9-cell KPI strip in the same ordering, price chart with z-score bands, methodology card with the contract spec + disclosure rows, lineage footer.

Identity row also follows the Option-(c) identity-row hazard avoidance.  `BuildCompactShell` / `BuildExtendedShell` `IdentityBlock` already inserts the `·` glyph between `identity.primary` and `identity.secondary` spans; both views pass BARE labels — never the leading-`· ` variant — so the on-screen result is `TY1 · 10Y UST` with one glyph, not two.

---

## Version log

| Version | Date | Change |
|---|---|---|
| v3 | 2026-06-08 | Dispatch-1 dual-view + Monitor implementation per `rendering_density.md`.  Shipped `surfaces/BuildExtended.tsx`, `surfaces/BuildCompact.tsx`, `surfaces/monitor/BondFuturesPriceLevelWidget.tsx`, and the shared helper `surfaces/bondFuturesPriceShared.ts`.  Tier set updated to `[generic_runnable, custom_build_surface, monitor_surface]`.  Standalone-bridge typed-detail endpoint shipped at `/api/v1/rates/detail/bond-futures-price`.  Methodology-disclosure threading + shell-density acceptance per the Option-(c) precedent.  Per-contract 32nds notation (UST family) + decimal notation (rest) carried in the shared `CONTRACT_REGISTRY`. |
| v2 | 2026-05-26 | Stage 4f — rewrote the body to drop stale "Stage 3 scaffold" framing and answer Q2–Q5 for real. |
| v1 | 2026-05-25 | Stage 3 scaffold — runtime tier only, no surfaces. |
