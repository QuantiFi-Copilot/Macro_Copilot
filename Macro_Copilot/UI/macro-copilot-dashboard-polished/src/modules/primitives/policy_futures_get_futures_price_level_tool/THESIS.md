# THESIS — `policy_futures_get_futures_price_level_tool`

> Dispatch-1 build under the dual-view rendering-density contract.  Brought to parity with the snapshot-shape reference twin `get_real_yield_level_tool`: ships an extended Build view (full canvas for single-tool queries) AND a compact Build view (grid card for multi-tool query DAGs) — both REQUIRED per [`rendering_density.md`](../../../../../docs_revamped/03_standards/rendering_density.md) §1 — plus a Monitor tile.

**Version:** v3 (Dispatch-1 dual-view + Monitor implementation)
**Module spec:** [`module.ts`](module.ts)
**Backend artifact:** `policy_futures_get_futures_price_level_tool` (generic_runnable; standalone bridge via `/api/v1/rates/detail/policy-futures-price`).  MCP wrapper registers the bare `get_futures_price_level_tool` inside the `policy_futures` domain namespace; the canonical workflow-registry name disambiguates against the sibling `bond_futures` tool of the same local name.
**Tier set:** `[generic_runnable, custom_build_surface, monitor_surface]`
**Category:** `snapshots`
**Mockups:** [`./mockups/Compact.png`](./mockups/Compact.png) + [`./mockups/Extended.png`](./mockups/Extended.png) — committed alongside the module per the mockup-first workflow.

---

## 1. What surfaces does this module ship?

- **`generic_runnable`** — runtime-status tier; backend ships in `_PRIMITIVE_SPECS`.
- **`custom_build_surface`** — the dual-view contract:
  - **Extended Build view** ([`surfaces/BuildExtended.tsx`](surfaces/BuildExtended.tsx)) — full canvas mounted for single-tool queries.  Controls strip (Curve Family / Strip Position / Lookback / Field), top-right Z-score / Percentile / Strip-Context cards (the third carries the RFR-vs-IBOR regime label inline), a 9-cell KPI strip on the IMPLIED-RATE axis with the parallel PRICE cell, the implied-rate history chart with ±2σ z-score bands, stretch-context panel, methodology card sourced from the backend's `methodology_disclosure`, lineage footer.
  - **Compact Build view** ([`surfaces/BuildCompact.tsx`](surfaces/BuildCompact.tsx)) — grid card mounted as a node body inside multi-tool query DAGs (e.g. *"compare SFR1 vs SFR2 vs ER1 implied rates"*).  Headline 3-KPI strip (IMPLIED RATE (PERCENT) / 1D CHANGE (BPS) / Z-SCORE (252D)) with a price subtext on the level cell, mini-chart with ±2σ z-score bands, the *"100-minus-rate; UP price = DOWN implied rate (dovish)"* caveat footer, click-to-expand affordance.
- **`monitor_surface`** — Monitor bento tile ([`surfaces/monitor/PolicyFuturesPriceLevelWidget.tsx`](surfaces/monitor/PolicyFuturesPriceLevelWidget.tsx)) showing one STIR strip slot (curve_family × strip_position × lookback parameterised; defaults `SOFR_FUT` / 1 / 252).  Inherently compact per [`rendering_density.md §8`](../../../../../docs_revamped/03_standards/rendering_density.md).  Front STIR implied rates are a desk-canonical morning-screen read; eligibility per `surface_contract.md §3.4`.

---

## 2. What does the user read off each surface?

### Extended Build view

The full investigation canvas for ONE strip-position price.  A PM reads, in order: (a) the title row identifying the master stem (e.g. *"SFR1 · SOFR Whites Pack 🇺🇸"*) + as-of date; (b) the top-right Z-Score / Percentile / Strip-Context cards (the last carries the *"Compounded daily RFR (SOFR / SONIA)"* or *"Unsecured 3M term IBOR (Euribor)"* regime label inline per the per-strip `short_rate_regime` flag); (c) the 9-cell KPI strip on the IMPLIED-RATE axis — IMPLIED RATE (PERCENT) with the parallel PRICE cell, 1D CHANGE (BPS), Z-SCORE (252D) with regime caption, PERCENTILE (252D), 252D HIGH / LOW / MID on the implied-rate axis, OBSERVATIONS count; (d) the implied-rate history chart with ±2σ / ±1.5σ z-score band overlays; (e) the stretch-context panel; (f) the methodology card — strip slot identity, underlying contract (`underlying_contract_code` / `security_name` / `expiry_date`), quote convention (`100 - rate · Inverse-priced — implied_rate_pct = 100 − raw_price`), short-rate regime (RFR vs IBOR), field, z-score model, trailing range, contract spec (size / tick / tick val), and the full `methodology_disclosure` honesty string threaded from the backend (PR10 / P5 — NOT a hardcoded TS literal); (g) the lineage footer.

### Compact Build view

The at-a-glance grid card.  A PM sees this in a multi-tool prompt.  They read THREE numbers + a sparkline: current IMPLIED RATE in PERCENT (with the raw `Price 95.75` subtext so the desk can map back to the underlying futures price), 1-day implied-rate change in BPS (tone-coloured: positive bps = UP rate = tightening = coral), rolling 252d z-score (regime-captioned — Normal / Elevated / Extreme).  The mini-chart shows the implied-rate history with z-score bands.  The footer carries the *"100-minus-rate; UP price = DOWN implied rate (dovish). Rolling-generic strip read."* caveat.  The expand arrow opens the extended view in a modal.

### Monitor tile

One STIR strip slot, desk-glanceable: kicker (`SFR1 · SOFR WHITES PACK 🇺🇸`), title (`Policy Futures Price`), z-score badge.  Body: current implied rate (+ sign for positive levels; neutral tone) + `%`, 1d change (bps; positive = coral, negative = mint) + 252d percentile + raw price (`Px 95.75`), and a 252d implied-rate range strip with a current marker.

---

## 3. Why these surfaces and not others?

**Why the dual-view contract** (both extended + compact REQUIRED): per [`rendering_density.md §1.1`](../../../../../docs_revamped/03_standards/rendering_density.md) multi-tool prompts are the default reality; a tool that ships only an extended view falls back to a generic artifact-type card in multi-tool DAGs.  Policy-futures prices are commonly compared across the strip (*"SFR1 vs SFR2 vs SFR4"*) and across markets (*"SFR1 vs ER1 vs SFI1"*), so the compact view earns its keep immediately.  The standard explicitly OVERRIDES FM4 parsimony for the dual-view mandate.

**Why these three compact KPIs** (IMPLIED RATE / 1D CHANGE / Z-SCORE): they're the desk-canonical "first three numbers" a PM reads off a STIR strip-position snapshot — *"where is the implied rate now / how much did it move today / is this stretched?"*.  The level is shown in PERCENT (the desk-recognised axis) with the parallel raw `Price` carried as a subtext on the same cell so the trader who reads the futures-price tape can still cross-check the quote space without losing the headline.  The 1D change is reported in BPS (the implied-rate `daily_change_implied_rate_pct` × 100) because that's how STIR moves are quoted; the wire's `daily_change_raw_price` is reflected as a percent-of-price subtext under the change so the inverse-pricing direction stays transparent (positive bps = negative price = UP rate = tightening).  Alternatives considered + rejected: showing the raw `Price` outright as a headline KPI (hides the rate, which is the desk-recognised level), surfacing 5D / 1M changes (lower-frequency reads; surfaced in the extended view), surfacing 252d percentile (already conveyed by the z-score regime + band overlay).

**Why a SINGLE Curve Family + Strip Position pair** (not contract_code): per ADR 0013 the desk-recognised instrument identity is the strip slot, NOT a master contract stem and NOT a calendar tenor.  Exposing `contract_code` would silently duplicate the strip_position knob (the stem is just the strip slot's playbook label, e.g. `SFR1`); the schema's `(curve_family, strip_position)` keying is the input-layer enforcement.

**Why the z-score lives on the IMPLIED-RATE axis** (not the raw-price axis): for inverse-priced strips (SFR / ER / SFI in V1) the raw price and implied rate are monotone inversions, so z-scoring the inverse-priced raw price would flip the sign of every "extreme" reading relative to the rate.  The desk reads the rate; the z follows the rate.

**Why a typed-detail endpoint** (`/api/v1/rates/detail/policy-futures-price`): per [`methodology_exposure.md §5`](../../../../../docs_revamped/03_standards/methodology_exposure.md) every standalone module ships its own typed bridge; the generic `/run` route stays the LLM-facing surface.  Same pattern the sovereign / linker / OIS standalone bridges use.

**Why no z-score override controls in the extended view**: per ADR 0013 V1 deterministic mode there is NO LLM-facing override path for conventions; `z_score_window_days` / `trailing_range_window_days` etc. are YAML-locked at every call site.  Surfacing them as controls would silently shadow the YAML; the extended view honours the actual contract instead.

---

## 4. What would change the design?

- **A direct-priced policy-futures family** lands (e.g. `TIIE_FUT`) → no code change here.  The implied-rate conversion is metadata-driven (`instrument_master.attributes->>'inverse_pricing'`); adding the family + an entry to `CURVE_REGISTRY` in [`surfaces/policyFuturesPriceShared.ts`](surfaces/policyFuturesPriceShared.ts) is sufficient.  The methodology card auto-flips its rule string off the `inverse_priced` flag the wire carries.
- **A CTD-implied-OIS primitive** lands (Phase-4 per ADR 0013) → it ships as a SEPARATE module (different concept — maps the strip onto a calibrated OIS forward curve).  This module remains the rolling-generic strip-read; the methodology card's *"This is NOT a CTD-of-futures-of-OIS read"* caveat stays.
- **A backend exposes z-score overrides** (`z_score_window_days` etc.) → add the matching `advanced: true` ControlDescriptors to the extended view's controls strip and pipe them into the typed-detail endpoint params.  Today the schema does not expose them.
- **A multi-strip aggregate endpoint** lands (whole-pack snapshot in one call) → the Monitor tile gains a second variant rendering the whole pack (whites or reds) on a single tile.  Today each tile renders one strip slot.
- **A workflow template** using this primitive (e.g. STIR-strip event_study) lands → the compact view fires as a node body in that workflow's dashboard.  No design change required — the compact view is workflow-agnostic by contract.

---

## 5. Which backend doctrine does this module operationalise?

- **FM1** (module identity) — folder name equals backend `tool_name`.
- **FM3** (surface-tier capability declaration) — claims `generic_runnable` + `custom_build_surface` + `monitor_surface`.
- **FM4** (tier-set parsimony) — overridden by [`rendering_density.md §1.2`](../../../../../docs_revamped/03_standards/rendering_density.md) for the dual-view mandate; Monitor is justified by `surface_contract.md §3.4` eligibility (desk-canonical front-STIR read).
- **FM5** (display metadata) — `displayName` / `category` / `oneLineSummary` are PM-facing (per `lifecycle_checklist_template.md` Stage 1C).
- **FM7** (pure-spec assembly) — [`module.ts`](module.ts) exports a pure value; no side effects.
- **FM8** (surface-file contract) — `surfaces.buildExtended` + `surfaces.buildCompact` populated; files at canonical paths; `monitorWidgets` array populated for the Monitor tile.
- **FM9** (routing-claim disclosure) — `typedView: null`; this module owns its own full surfaces.
- **FM10** (THESIS discipline) — this file.
- **FM11** (round-trip test) — [`__tests__/module.spec.ts`](__tests__/module.spec.ts) asserts the dual-view contract + standalone-bridge invariants.
- **FM12** (loader presence) — module imported in [`src/modules/index.ts`](../../index.ts) at the alphabetical position.
- **PR10 / P5** (methodology disclosure sourcing) — the methodology card sources the honesty disclosure from `output.methodology_disclosure` (threaded from `compute._build_methodology_disclosure`, which incorporates `config.yaml:methodology` + per-strip runtime context), NOT a hardcoded TS literal.  The backend Pydantic schema uses `methodology_disclosure` here (a full multi-line caveat), distinct from the `methodology_label` field other tools ship.
- **ADR 0013** (`policy_futures` domain — strip-position-keyed monitors) — the input layer is keyed by `(curve_family, strip_position)`, the methodology card carries the rolling-generic-strip-read caveat verbatim, and CTD-implied-OIS is explicitly NOT in scope.
- Per the **methodology-exposure standalone-bridge contract** (`methodology_exposure.md §5`): own typed-detail endpoint `/api/v1/rates/detail/policy-futures-price`; own service helper `fetchDetailPolicyFuturesPrice`; own frontend type `PolicyFuturesPriceLevelOutput`.
- Per the **rendering-density dual-view contract** (`rendering_density.md §1`): both views ship + both wired in `module.ts.surfaces` + files at canonical paths.

---

## Mockup conformance

Per the Option-(c) precedent from the breakeven_butterfly Dispatch-2 review (commit `fdac7d2`; catalog `last_resolution_applied: option_c_shell_density_acceptance`), this module's Compact view keeps the SHELL-STANDARD density: 3 KPIs + a single identity line.  The mockup at `./mockups/Compact.png` shows a higher-density layout — the headline 3-cell strip PLUS a secondary 4–6-cell row (5D CHANGE, 1M CHANGE, 252D PCTL, 252D HIGH, 252D LOW, OBSERVATIONS) — that would require extending `BuildCompactShell` with a `secondaryKpis` slot (and a matching `subtitle` extension on the identity row).  Those shell extensions would touch 10+ shipped compact views across the rates surface and constitute a separate epic; the deviation is shell-density-driven, not data-driven.  The data IS available in the typed-detail payload (`extendedKPIs(data)` in [`surfaces/policyFuturesPriceShared.ts`](surfaces/policyFuturesPriceShared.ts) exposes every secondary cell the mockup shows), and the full 9-cell strip remains accessible in the extended view (one tap on the compact card's expand affordance).

Identity row also follows the Option-(c) identity-row hazard avoidance.  `BuildCompactShell` / `BuildExtendedShell` `IdentityBlock` already inserts the `·` glyph between `identity.primary` and `identity.secondary` spans; both views pass BARE labels (e.g. `secondary: stripPackLabel(...)` resolving to `'SOFR Whites Pack'`) — never the leading-`· ` variant — so the on-screen result is `SFR1 · SOFR Whites Pack` with one glyph, not two.  Reference twin: [`get_real_yield_level_tool/surfaces/BuildCompact.tsx:72-76`](../get_real_yield_level_tool/surfaces/BuildCompact.tsx).

---

## Version log

| Version | Date | Change |
|---|---|---|
| v3 | 2026-05-30 | Dispatch-1 dual-view + Monitor implementation per `rendering_density.md`.  Shipped `surfaces/BuildExtended.tsx`, `surfaces/BuildCompact.tsx`, `surfaces/monitor/PolicyFuturesPriceLevelWidget.tsx`, and the shared helper `surfaces/policyFuturesPriceShared.ts`.  Tier set updated to `[generic_runnable, custom_build_surface, monitor_surface]`.  Standalone-bridge typed-detail endpoint shipped at `/api/v1/rates/detail/policy-futures-price`.  Methodology-disclosure threading + shell-density acceptance per the Option-(c) precedent recorded in catalog `last_resolution_applied`. |
| v2 | 2026-05-26 | Stage 4f — rewrote the body to drop stale "Stage 3 scaffold" framing and answer Q2–Q5 for real. |
| v1 | 2026-05-25 | Stage 3 scaffold — runtime tier only, no surfaces. |
