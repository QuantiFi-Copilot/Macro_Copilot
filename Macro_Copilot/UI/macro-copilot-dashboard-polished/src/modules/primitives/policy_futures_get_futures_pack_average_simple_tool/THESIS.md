# THESIS — `policy_futures_get_futures_pack_average_simple_tool`

> Catalog tool-22 — pack-average implied rate (arithmetic mean across 4 consecutive quarterly STIR contracts) on a SINGLE policy-futures curve family (e.g. SOFR_FUT whites = mean(SFR1..SFR4), SONIA_FUT reds = mean(SFI5..SFI8)).  Brought to dual-view + standalone-bridge + Monitor parity with the just-shipped Batch-3 siblings (tool 19 `futures_butterfly_simple`, tool 20 `futures_calendar_spread`, tool 21 `futures_cross_market_spread`).  Ships an extended Build view (full canvas for single-tool queries) AND a compact Build view (grid card for multi-tool query DAGs) — both REQUIRED per [`rendering_density.md`](../../../../../docs_revamped/03_standards/rendering_density.md) §1 — plus a Monitor tile.

**Version:** v3 (factory dual-view + Monitor implementation)
**Module spec:** [`module.ts`](module.ts)
**Backend artifact:** `policy_futures_get_futures_pack_average_simple_tool` (generic_runnable; standalone bridge via `/api/v1/rates/detail/policy-futures-pack-average`)
**Backend MCP function:** `get_futures_pack_average_simple_tool` (unprefixed inside `policy_futures/mcp_server.py`; bridged to the prefixed frontend module via `KNOWN_TOOL_ALIASES`)
**Tier set:** `[generic_runnable, custom_build_surface, monitor_surface]`
**Category:** `aggregates`
**Mockups:** [`./mockups/Compact.png`](./mockups/Compact.png) + [`./mockups/Extended.png`](./mockups/Extended.png) — committed alongside the module per the mockup-first workflow.

---

## 1. What surfaces does this module ship?

- **`generic_runnable`** — runtime-status tier; backend ships in `_PRIMITIVE_SPECS`.
- **`custom_build_surface`** — the dual-view contract:
  - **Extended Build view** ([`surfaces/BuildExtended.tsx`](surfaces/BuildExtended.tsx)) — full canvas mounted for single-tool queries.  Controls strip (Curve / Pack / Lookback / Field — `pack` is a closed Literal whites/reds per the YAML-locked desk convention), top-right Z-score / Percentile / Central-Bank-Context cards (the last carries the per-curve regime label sourced from the wire's `short_rate_regime` — RFR vs IBOR), a PERCENT-scale KPI strip (level + 1d change in bps + z-score + percentile + 252d high/low + observations) plus a per-leg disclosure row (four master stems + current-front underlying contracts + per-leg implied rates so the `pack_average = mean(SFR1..SFR4)` decomposition is auditable on the same screen), pack-average history chart with ±2σ z-score bands, stretch-context panel (*"SOFR whites pack average is elevated above its trailing-year mean..."*), methodology card (per-leg disclosure block + the wire's `methodology_disclosure` verbatim — including the explicit `SIMPLE arithmetic-mean — NOT duration-weighted, NOT meeting-by-meeting, NOT CTD-of-OIS` scope guardrail), lineage footer.
  - **Compact Build view** ([`surfaces/BuildCompact.tsx`](surfaces/BuildCompact.tsx)) — grid card mounted as a node body inside multi-tool query DAGs (e.g. *"compare SFR whites vs ER whites vs SFI whites front-year implied-policy reads"*).  Title `SFR · WHITES · 4-quarter mean` with subtitle `Pack Average (Implied Rate)`, headline 3-KPI strip (PACK IMPLIED RATE % + 1D CHANGE bps + Z-SCORE 252D), mini-chart with z-score bands, footer caveat `Simple mean across 4 contracts; 100-minus-rate convention (price up = rate down).` + per-leg flag chip, click-to-expand affordance.
- **`monitor_surface`** — Monitor bento tile ([`surfaces/monitor/PolicyFuturesPackAverageWidget.tsx`](surfaces/monitor/PolicyFuturesPackAverageWidget.tsx)) showing one STIR pack average (curve_family × pack × lookback parameterised; defaults SOFR_FUT × whites × 252).  STIR pack averages are a canonical year-anchored implied-policy-path read per `surface_contract.md §3.4`.  Inherently compact per `rendering_density.md §8`.

---

## 2. What does the user read off each surface?

### Extended Build view

The full investigation canvas for ONE same-curve pack-average snapshot.  A PM reads, in order: (a) the title row `SOFR WHITES PACK AVERAGE` with the per-leg subtitle `SOFR strip · whites = positions 1-4 (arithmetic mean)` + the country flag chip + as-of date; (b) the top-right Z-Score / Percentile / Central-Bank-Context cards (the last carries the regime label — `🇺🇸 Fed` with `SOFR overnight reference · RFR regime` sourced from the wire's `short_rate_regime`); (c) the PERCENT-scale KPI strip — current PACK IMPLIED RATE (% to 3-dp), 1D CHANGE (bps + percent subtext), z-score, percentile, 252d high/low, observation count; (d) the per-leg decomposition row exposing the four master stems + current-front underlying contracts + per-leg implied rates (`SFR1 → SFRM26 @ 4.38%`, `SFR2 → SFRU26 @ 4.34%`, ...) so the `mean(SFR1..SFR4)` decomposition is auditable on the same screen; (e) the pack-average history chart with z-score band overlays; (f) the stretch-context panel; (g) the methodology card — construction formula (`pack_average_implied_rate_pct = mean(implied_rate_pct(SFR1..SFR4))`), sign convention (POSITIVE 1d change = hawkish stretch), the per-curve_family regime label (RFR vs IBOR), the inverse-pricing rule, the per-leg disclosure block, the alignment discipline (intersection of all four legs' trading calendars), the z-score model + window, the trailing range, the SIMPLE-ARITHMETIC-MEAN scope guardrail (NOT duration-weighted / meeting-by-meeting / CTD-of-OIS), and the wire's full `methodology_disclosure` verbatim; (h) the lineage footer.

### Compact Build view

The at-a-glance grid card.  A PM sees this in a multi-tool prompt.  They read THREE numbers + a sparkline: current PACK IMPLIED RATE (%, 3-dp), 1-day change (bps, tone-coloured POSITIVE = hawkish), rolling 252d z-score (with regime caption — Normal / Elevated / Extreme).  The mini-chart shows the pack-average history with ±2σ / ±1.5σ z-score bands.  The footer carries the load-bearing one-line caveat — `Simple mean across 4 contracts; 100-minus-rate convention (price up = rate down).` — plus the per-curve flag chip (e.g. `🇺🇸`).  The expand arrow opens the extended view in a modal.

### Monitor tile

One STIR pack average, desk-glanceable: current level (% to 3-dp), 1-day change (bps, sign-coloured POSITIVE = hawkish), 252d percentile, a high/low range strip with a current marker, AND a one-line methodology caveat row (`Simple mean × 4 contracts · 100-minus-rate STIR convention.`).  The wire's full `methodology_disclosure` rides on the title= tooltip so YAML edits flow to the Monitor tile.  Flag + z-score badge in the header.

---

## 3. Why these surfaces and not others?

**Why the dual-view contract** (both extended + compact REQUIRED): per [`rendering_density.md §1.1`](../../../../../docs_revamped/03_standards/rendering_density.md) multi-tool prompts are the default reality; STIR pack averages are characteristically queried in batches (*"SFR whites vs SFI whites vs ER whites front-year policy reads"* — 3 calls; *"SFR whites + SFR reds front-year vs second-year"* — 2 calls).  A tool that ships only an extended view falls back to a generic artifact-type card in those DAGs.  The standard OVERRIDES FM4 parsimony for the dual-view mandate.

**Why these three compact KPIs** (PACK IMPLIED RATE % + 1D CHANGE bps + Z-SCORE 252D): they're the desk-canonical "first three numbers" a PM reads off a STIR pack snapshot — *"where is the pack-average rate now / how much did it move today / is this stretched?"*.  Alternatives considered + rejected: 252d percentile (already conveyed by the z-score regime + band overlay; surfaced in the extended view's KPI strip); per-leg rates (decomposition detail, surfaced in the extended view, not headline); 252d high/low (range context, not headline data); 5d change (the daily move is the headline read — multi-day moves go in the extended view).

**Why a curve-family + pack control pair** (not separate per-leg dropdowns): the pack composition (whites = strip positions 1-4, reds = 5-8) is YAML-locked and NOT user-overridable per ADR 0013 — it IS the central methodology knob.  Exposing per-leg strip-position dropdowns would invite the user to define ad-hoc packs (6-pack, bundles, custom 3-leg averages) the backend cannot dispatch.  The two-control form (curve_family + pack) maps 1-to-1 to the schema's `(curve_family, pack)` Input keys; a future desk wanting a different pack composition lands as a SEPARATE primitive.

**Why EUR_SHORT_RATE_FUT is surfaced in the dropdown** (despite being compute-layer refused): the schema layer admits all three V1 families so the LLM can ask and receive a clean controlled-error envelope per ADR 0013 V1 scope (IBOR regime, missing `delivery_month_type` playbook metadata).  Hiding the option at the UI layer would create a divergence between the schema-admitted set and the controls-exposed set — failing the principle that the controls layer mirrors the Pydantic Input surface.  The compute-layer refusal envelope is surfaced honestly as the error message under the chart when the user picks it.

**Why the SHELL-STANDARD compact density** (3 KPIs + single identity line + sparkline + caveat + expand): per the Option-(c) precedent (Batch 1 fdac7d2 + `policy_futures_get_futures_price_level_tool` d8e8233 + tool 19 fcc5381 + tool 20 be2595b + tool 21 ad4a6fa) the compact view uses the shell-standard density; the mockup's denser layout (the secondary KPI row carrying 5D / 252D PCTL / 252D HIGH / 252D LOW / OBSERVATIONS) is preserved visually in spirit — the primary 3 KPIs carry the load-bearing reads.  Promoting the secondary row into the compact view would require extending `BuildCompactShell` with a `secondaryKpis` slot, touching 10+ shipped compact views — that's a cross-tool standards change, deferred to a separate consistency PR.

**Why a typed-detail endpoint** (`/api/v1/rates/detail/policy-futures-pack-average`): per [`methodology_exposure.md §5`](../../../../../docs_revamped/03_standards/methodology_exposure.md) every standalone module ships its own typed bridge; the generic `/run` route stays the LLM-facing surface.  This is the FOURTH policy_futures standalone-bridge endpoint (after tool-19 butterfly + tool-20 calendar + tool-21 cross-market); the remaining policy_futures roster will migrate to the same pattern.

---

## 4. What would change the design?

- **EUR_SHORT_RATE_FUT becomes V1-executable** (the `delivery_month_type` playbook metadata + IBOR-regime z-score conventions ship) → flip the `executableV1` flag to `true` in `CURVE_REGISTRY` in [`surfaces/futuresPackAverageSimpleShared.ts`](surfaces/futuresPackAverageSimpleShared.ts).  No shell changes; the compute-layer refusal envelope simply stops firing.
- **Greens / blues pack-position ranges lift from PR11 planned-extension to V1** → add the new pack identifiers (`'greens'`, `'blues'`) to `PACK_OPTIONS` and the backend's Pydantic Literal.  Strip-position ranges (9-12, 13-16) come from the YAML config.
- **A duration-weighted or meeting-keyed pack-average primitive** lands → it ships as a SEPARATE module (different concept) per the catalog's SIMPLE-arithmetic-mean scope guardrail.  This module's methodology card REFUSES to mix them under the same name.
- **The desk decides z-score conventions need exposure** at the Pydantic Input layer → promote `z_score_window_days` / `z_score_min_periods` / `z_score_ddof` from YAML-locked → exposed (per the `methodology_exposure.md §3` decision protocol); add Advanced controls in [`surfaces/BuildExtended.tsx`](surfaces/BuildExtended.tsx).

---

## 5. Which backend doctrine does this module operationalise?

- **FM1** (module identity) — folder name equals backend `tool_name` (the `policy_futures_` prefixed form; MCP-side unprefixed alias bridged via `KNOWN_TOOL_ALIASES` in `src/lib/toolNames.ts` per the policy-futures naming-divergence precedent).
- **FM3** (surface-tier capability declaration) — claims `generic_runnable` + `custom_build_surface` + `monitor_surface`.
- **FM4** (tier-set parsimony) — overridden by `rendering_density.md §1.2` for the dual-view mandate; Monitor is justified by `surface_contract.md §3.4` eligibility.
- **FM5** (display metadata) — `displayName` / `category` / `oneLineSummary` are PM-facing (per `lifecycle_checklist_template.md` Stage 1C).
- **FM7** (pure-spec assembly) — `module.ts` exports a pure value.
- **FM8** (surface-file contract) — `surfaces.buildExtended` + `surfaces.buildCompact` populated; files at canonical paths.
- **FM9** (routing-claim disclosure) — `typedView: null`; this module owns its own full surfaces.
- **FM10** (THESIS discipline) — this file.
- **FM11** (round-trip test) — [`__tests__/module.spec.ts`](__tests__/module.spec.ts) asserts the dual-view contract.
- **FM12** (loader presence) — module imported in [`src/modules/index.ts`](../../index.ts).
- **P5** (honest disclosure) — the arithmetic-mean + 100-minus-rate caveat surfaces in BOTH compact (one-liner footer) and extended (full methodology card sourced from the wire's `methodology_disclosure`) views.  The compute-layer refusal envelope for `EUR_SHORT_RATE_FUT` surfaces honestly rather than being hidden.
- **ADR 0013** (policy_futures domain — strip-position-keyed monitors; mixed-regime guardrail) — module operationalises the catalog's V1 scope (SOFR + SONIA executable; Euribor admitted at schema layer with controlled-error envelope from compute layer) and the SIMPLE-arithmetic-mean scope guardrail (duration-weighted / meeting-by-meeting / CTD-of-OIS pack variants ship as separate primitives — refused inline).
- Per the **methodology-exposure standalone-bridge contract** (`methodology_exposure.md §5`): own typed-detail endpoint `/api/v1/rates/detail/policy-futures-pack-average`; own service helper `fetchDetailPolicyFuturesPackAverage`; own frontend type `FuturesPackAverageSimpleOutput`.
- Per the **rendering-density dual-view contract** (`rendering_density.md §1`): both views ship + both wired in `module.ts.surfaces` + files at canonical paths.

---

## Mockup conformance

The Extended.png + Compact.png mockups are the design source-of-truth.  Both views were built against the committed PNGs:

- **Extended.png** — Title `SFR WHITES PACK AVERAGE` with the US flag chip, top-right Z-Score (`+1.24σ`) / Percentile (`81st`) / Central-Bank-Context (Fed · SOFR overnight reference) cards, PERCENT-scale KPI strip carrying PACK IMPLIED RATE (`4.38%`) + 1D CHANGE (`+2.1 bp` / `+0.05%`) + Z-score + Percentile + 252D high/low (`4.82%` / `3.11%`) + Observations (`365`), per-leg implied-rate decomposition row (`SFR1 → SFRM26 @ 4.38%`, `SFR2 → SFRU26 @ 4.34%`, ...), main pack-average history chart with ±2σ z-score bands, strip-context callout, methodology card + lineage footer.  Layout matches.
- **Compact.png** — Title `SFR · WHITES · 4-quarter mean` with `🇺🇸` flag chip, subtitle `Implied 4.38% · Price 95.62` (compressed into the primary KPI cell), 3-KPI strip (PACK IMPLIED RATE `4.38%` / 1D CHANGE `+2.1 bp` (+0.05%) / Z-SCORE `+1.24σ` Elevated), pack-average history sparkline (252d) with ±1.5σ / ±2σ band annotations + current marker, footer caveat `Simple mean across 4 contracts; 100-minus-rate convention (price up = rate down).`, `View full analysis →` expand affordance, freshness pill.

**Density deviation** (Compact view): per the Option-(c) precedent (catalog design_guardrail #4 — *"keep shell-standard density on Compact; document any density deviation in THESIS"*) the compact view uses the SHELL-STANDARD 3-KPI density rather than the mockup's denser layout that adds a secondary KPI row (5D / 252D PCTL / 252D HIGH / 252D LOW / OBSERVATIONS) under the primary 3 KPIs.  The secondary row's data IS reachable on the compact view via the expand affordance (the extended view's KPI strip carries every cell) and is preserved on the Monitor tile via the L/H range strip.  Promoting the secondary row would require extending `BuildCompactShell` with a `secondaryKpis` slot, touching 10+ shipped compact views — that's a cross-tool standards change, deferred to a separate consistency PR.  No structural feature is lost from the compact view; the PACK IMPLIED RATE / 1D CHANGE / Z-SCORE remain the three desk-canonical headline reads.

The Compact mockup's primary-line "Implied 4.38% · Price 95.62" pair is elided: the implied rate IS the primary KPI cell, and the price-space mirror (`95.62 = 100 − 4.38`) is structurally redundant under the 100-minus-rate convention disclosed in the footer caveat.  Surfacing both would conflict with the SHELL-STANDARD 3-KPI density rule.

---

## Version log

| Version | Date | Change |
|---|---|---|
| v3 | 2026-06-08 | Factory dual-view + Monitor implementation per `rendering_density.md`.  Shipped `surfaces/BuildExtended.tsx`, `surfaces/BuildCompact.tsx`, `surfaces/monitor/PolicyFuturesPackAverageWidget.tsx`, and the shared helper `surfaces/futuresPackAverageSimpleShared.ts`.  Tier set updated to `[generic_runnable, custom_build_surface, monitor_surface]`.  Standalone-bridge typed-detail endpoint shipped at `/api/v1/rates/detail/policy-futures-pack-average`.  `KNOWN_TOOL_ALIASES` entry added (`get_futures_pack_average_simple_tool` → `policy_futures_get_futures_pack_average_simple_tool`).  Brought to parity with `policy_futures_get_futures_cross_market_spread_tool` / `policy_futures_get_futures_calendar_spread_tool` / `policy_futures_get_futures_butterfly_simple_tool`.  Fourth policy_futures tool under the dual-view contract. |
| v2 | 2026-05-26 | Stage 4f — rewrote the body to drop stale "Stage 3 scaffold" framing and answer Q2–Q5 for real. |
| v1 | 2026-05-25 | Stage 3 scaffold — runtime tier only, no surfaces. |
