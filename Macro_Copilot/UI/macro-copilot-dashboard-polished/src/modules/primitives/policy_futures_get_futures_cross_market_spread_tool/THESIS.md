# THESIS — `policy_futures_get_futures_cross_market_spread_tool`

> Catalog tool-21 — matched-strip cross-market implied-rate differential between two policy-futures curve families at one strip position (e.g. SFR1 − SFI1, SFR4 − ER4).  Brought to dual-view + standalone-bridge + Monitor parity with the Phase-1 + Batch-3 siblings (tool 19 `futures_butterfly_simple`, tool 20 `futures_calendar_spread`).  Ships an extended Build view (full canvas for single-tool queries) AND a compact Build view (grid card for multi-tool query DAGs) — both REQUIRED per [`rendering_density.md`](../../../../../docs_revamped/03_standards/rendering_density.md) §1 — plus a Monitor tile.

**Version:** v3 (factory dual-view + Monitor implementation)
**Module spec:** [`module.ts`](module.ts)
**Backend artifact:** `policy_futures_get_futures_cross_market_spread_tool` (generic_runnable; standalone bridge via `/api/v1/rates/detail/policy-futures-cross-market`)
**Tier set:** `[generic_runnable, custom_build_surface, monitor_surface]`
**Category:** `cross_market_rv`
**Mockups:** [`./mockups/Compact.png`](./mockups/Compact.png) + [`./mockups/Extended.png`](./mockups/Extended.png) — committed alongside the module per the mockup-first workflow.

---

## 1. What surfaces does this module ship?

- **`generic_runnable`** — runtime-status tier; backend ships in `_PRIMITIVE_SPECS`.
- **`custom_build_surface`** — the dual-view contract:
  - **Extended Build view** ([`surfaces/BuildExtended.tsx`](surfaces/BuildExtended.tsx)) — full canvas mounted for single-tool queries.  Controls strip (two **Leg A / Leg B** dropdowns + strip-position + lookback + field; snap-different handler so the schema's `curve_family_a != curve_family_b` invariant cannot be violated), top-right Z-score / Percentile / Cross-CB Context cards (the last carries the per-leg regime label; switches to a `MIXED — RFR vs IBOR` label when the pair is mixed-regime per the catalog guardrail), a bps-scale KPI strip (spread + 1d/5d/1m changes + range + percentile + observation count + per-leg PERCENT level decomposition) plus a per-leg disclosure row (master stems + current-front underlying contracts + expiry), spread-history chart with ±2σ z-score bands, stretch-context panel ("Fed − ECB SFR1-ER1 cross-market spread is elevated wider than..."), methodology card (per-leg disclosure block + the wire's `methodology_disclosure` verbatim — including the explicit `RAW differential — NOT basis-adjusted, NOT beta-adjusted` guardrail + the mixed-regime call-out), lineage footer.
  - **Compact Build view** ([`surfaces/BuildCompact.tsx`](surfaces/BuildCompact.tsx)) — grid card mounted as a node body inside multi-tool query DAGs (e.g. *"compare SFR-ER vs SFR-SFI vs ER-SFI strip-1 cross-CB divergence"*).  Title `SFR-ER · pos1 · WHITES` with subtitle `Fed-ECB Cross-CB Spread (Implied Rate)`, headline 3-KPI strip (SPREAD bps + 1D CHANGE bps + Z-SCORE 252D), mini-chart with z-score bands, footer caveat `Front-quarter Fed vs ECB divergence on implied rates; underlying contracts quote inverse.` + per-leg flag chips, click-to-expand affordance.
- **`monitor_surface`** — Monitor bento tile ([`surfaces/monitor/PolicyFuturesCrossMarketSpreadWidget.tsx`](surfaces/monitor/PolicyFuturesCrossMarketSpreadWidget.tsx)) showing one cross-market STIR spread (leg_a × leg_b × strip_position × lookback parameterised; defaults SOFR_FUT × EUR_SHORT_RATE_FUT × strip 1 × 252).  STIR cross-market spreads are a canonical cross-CB policy-divergence read per `surface_contract.md §3.4`.  Inherently compact per `rendering_density.md §8`.

---

## 2. What does the user read off each surface?

### Extended Build view

The full investigation canvas for ONE matched-strip cross-market STIR spread.  A PM reads, in order: (a) the title row `SFR1-ER1 Cross-CB Spread` with the per-leg subtitle `Fed − ECB — strip position 1 matched-strip implied-rate differential (bps display)` + the two flag chips + as-of date; (b) the top-right Z-Score / Percentile / Cross-CB Context cards (the last carries the per-leg regime label — switches to a `MIXED — Fed: RFR vs ECB: IBOR` framing inline when the pair is mixed-regime; sourced from the wire's `current_metrics.short_rate_regime_a` / `short_rate_regime_b`); (c) the bps-scale KPI strip — current SPREAD (bps + percent caption), 1d/5d/1m changes (bps), z-score, percentile, 252d high/low, observation count, AND the per-leg implied rates (LEG A / LEG B in PERCENT) so the `spread = rate_A − rate_B` decomposition is auditable on the same screen; (d) the decomposition row exposing the per-leg master stems + current-front underlying contracts + expiry (`SFR1 → SFRM26` / `ER1 → ERM26`); (e) the spread-history chart with z-score band overlays; (f) the stretch-context panel; (g) the methodology card — construction formula with A and B labels echoed (POSITIVE = Fed pricing ABOVE ECB), sign convention with the swap-flips-sign disclosure, the per-leg short-rate regime labels (RFR vs IBOR), the inverse-pricing rule, the per-leg disclosure block, the alignment discipline (intersection of both markets' trading calendars), the z-score model + window, the trailing range, the RAW-differential scope guardrail (NOT basis-adjusted, NOT beta-adjusted), and the wire's full `methodology_disclosure` verbatim; (h) the lineage footer.

### Compact Build view

The at-a-glance grid card.  A PM sees this in a multi-tool prompt.  They read THREE numbers + a sparkline: current SPREAD (bps, subtext in percent), 1-day change (bps, tone-coloured), rolling 252d z-score (with regime caption — Normal / Elevated / Extreme).  The mini-chart shows the cross-market spread history with ±2σ / ±1.5σ z-score bands.  The footer carries the load-bearing one-line caveat — `Front-quarter Fed vs ECB divergence on implied rates; underlying contracts quote inverse.` — plus the per-leg flag chips (e.g. `🇺🇸 🇪🇺`).  The expand arrow opens the extended view in a modal.

### Monitor tile

One cross-market STIR spread, desk-glanceable: current spread (bps + percent subtext), 1-day change (bps, sign-coloured), 252d percentile, a high/low range strip with a current marker, AND a one-line methodology caveat row that switches to `<cbA> − <cbB> divergence (mixed regime).` when the pair is mixed-regime — sourced from the wire's `short_rate_regime_a` / `short_rate_regime_b` (per `rendering_density.md §2.2` methodology MUST remain reachable in compact contexts).  The wire's full `methodology_disclosure` rides on the title= tooltip so YAML edits flow to the Monitor tile.  Pair flags + z-score badge in the header.

---

## 3. Why these surfaces and not others?

**Why the dual-view contract** (both extended + compact REQUIRED): per [`rendering_density.md §1.1`](../../../../../docs_revamped/03_standards/rendering_density.md) multi-tool prompts are the default reality; cross-market STIR spreads are characteristically queried in batches (*"SFR-ER vs SFR-SFI vs ER-SFI front-quarter cross-CB divergence"* — 3 calls; *"SFR-ER cross-CB at strip 1/2/4/8"* — 4 calls).  A tool that ships only an extended view falls back to a generic artifact-type card in those DAGs.  The standard OVERRIDES FM4 parsimony for the dual-view mandate.

**Why these three compact KPIs** (SPREAD bps + 1D CHANGE bps + Z-SCORE 252D): they're the desk-canonical "first three numbers" a PM reads off a cross-CB STIR snapshot — *"where is the spread now / how much did it move today / is this stretched?"*.  Alternatives considered + rejected: SPREAD in percent (the bps form is the desk-canonical change unit and reads better in a dense card — percent shown as subtext); 252d percentile (already conveyed by the z-score regime + band overlay; surfaced in the extended view's KPI strip); per-leg rates (decomposition detail, surfaced in the extended view, not headline); 1m change (same reason — the daily move is the headline read).

**Why TWO Leg dropdowns** (not a single pair-picker): unlike a same-curve calendar spread (where the curve_family uniquely determines both legs), a cross-market STIR spread is genuinely two-degrees-of-freedom — SFR-ER, SFR-SFI, ER-SFI, and the reversed sign-conventions are all valid desk reads (the A − B orientation is wire-frozen but swapping the inputs flips the sign by construction).  The schema layer enforces `curve_family_a != curve_family_b`; the controls UI snaps the other leg when the user picks a colliding family, so the user can never dispatch a self-spread.

**Why the cross-CB context card with explicit RFR-vs-IBOR labelling** (not a hidden footnote): mixed-regime pairs (e.g. SOFR_FUT vs EUR_SHORT_RATE_FUT — RFR vs IBOR) are a genuine desk-reading risk — a PM scanning a dense list of cross-CB tiles must not mistake the SFR-ER spread for a clean apples-to-apples policy-divergence quantity when one leg is a compounded daily RFR and the other is an unsecured 3M term IBOR.  Per the catalog guardrail BOTH per-leg regime labels are surfaced INDEPENDENTLY (NO pack-average collapse); the extended view's top-right card switches to a `MIXED` framing when `short_rate_regime_a != short_rate_regime_b`.  The compact + Monitor views carry the disclosure as a one-line footer (Monitor switches to `(mixed regime)` qualifier inline).

**Why the SHELL-STANDARD compact density** (3 KPIs + single identity line + sparkline + caveat + expand): per the Option-(c) precedent (Batch 1 fdac7d2 + policy_futures_get_futures_price_level_tool d8e8233 + tool 19 fcc5381 + tool 20 be2595b) the compact view uses the shell-standard density; the mockup's denser layout (the dual flag chip + WHITES segment tag in the identity row, the dual-band sparkline annotations) is preserved visually.  Promoting additional KPI cells (a separate percentile row, a strip-position context chip, etc.) into the compact view would require extending BuildCompactShell with a `secondaryKpis` slot, touching 10+ shipped compact views.  That's a cross-tool standards change — out of scope for this build, deferred to a separate consistency PR.

**Why a typed-detail endpoint** (`/api/v1/rates/detail/policy-futures-cross-market`): per [`methodology_exposure.md §5`](../../../../../docs_revamped/03_standards/methodology_exposure.md) every standalone module ships its own typed bridge; the generic `/run` route stays the LLM-facing surface.  This is the THIRD policy_futures standalone-bridge endpoint (after tool-19 butterfly + tool-20 calendar); the remaining policy_futures roster will migrate to the same pattern.

---

## 4. What would change the design?

- **A new policy_futures curve family** (e.g. JPY_TIBOR_FUT, AUD_BANK_BILL_FUT) lands → add the entry to `CURVE_REGISTRY` in [`surfaces/futuresCrossMarketSpreadShared.ts`](surfaces/futuresCrossMarketSpreadShared.ts) with its flag / shortLabel / longLabel / regime / cbShort / stripStemPrefix.  No shell changes.
- **A basis-adjusted or beta-adjusted cross-market spread primitive** lands (would isolate the cross-CB rate-expectation differential from the cross-currency basis / regime-adjusted residual) → it ships as a SEPARATE module (different concept) per the catalog's RAW-differential scope guardrail.  This module's methodology card REFUSES to mix them under the same name.
- **The desk decides z-score conventions need exposure** at the Pydantic Input layer → promote `z_score_window_days` / `z_score_min_periods` / `z_score_ddof` from YAML-locked → exposed (per the `methodology_exposure.md §3` decision protocol); add Advanced controls in [`surfaces/BuildExtended.tsx`](surfaces/BuildExtended.tsx).
- **Absolute-bp z-band labels** (the mockup shows ±2σ as σ labels for cross-tool consistency).  Promoting absolute-bp labels is a shared-`MainChart` enhancement that would backport to every tool at once; tracked as a deferred consistency decision.

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
- **P5** (honest disclosure) — the cross-CB caveat surfaces in BOTH compact (one-liner footer) and extended (full per-leg disclosure block + the wire's `methodology_disclosure` verbatim) views.  The mixed-regime call-out is INLINE in extended + Monitor (not hidden in a tooltip).
- **ADR 0013** (policy_futures domain — strip-position-keyed monitors; mixed-regime guardrail) — module operationalises the catalog's mixed-regime requirement (BOTH per-leg labels surfaced; NO pack-average collapse) and the RAW-differential scope guardrail (basis-/beta-adjusted variants ship as separate primitives — refused inline).
- Per the **methodology-exposure standalone-bridge contract** (`methodology_exposure.md §5`): own typed-detail endpoint `/api/v1/rates/detail/policy-futures-cross-market`; own service helper `fetchDetailPolicyFuturesCrossMarket`; own frontend type `FuturesCrossMarketSpreadOutput`.
- Per the **rendering-density dual-view contract** (`rendering_density.md §1`): both views ship + both wired in `module.ts.surfaces` + files at canonical paths.

---

## Mockup conformance

The Extended.png + Compact.png mockups are the design source-of-truth.  Both views were built against the committed PNGs:

- **Extended.png** — Title `SFR-ER Pos1 Cross-CB Spread` with US + EU flag chips, top-right Z-Score (`+1.28σ`) / Percentile (`72nd`) / Index-Families (`SFR1 / ER1`) cards, bps-scale KPI strip carrying SPREAD (`+30.0 bp`) + 1D change (`+2.4 bp`) + 5D change (`+12.5 bp`) + Z-score (`+1.28σ`) + Percentile + 252D high/low + Observation count, per-leg implied rates (`3.875%` / `3.975%`) + per-leg underlying contracts (`SFRM26` / `ERM26`), main spread-history chart with ±2σ z-score bands, strip-context callout, methodology card + lineage footer.  Layout matches.
- **Compact.png** — Title `SFR-ER · pos1 · WHITES` with `🇺🇸 🇪🇺` flag chips, subtitle `Front-Quarter Cross-CB Spread (Implied Rate)`, 3-KPI strip (SPREAD `+30.0 bp` (0.30%) / 1D CHANGE `+2.4 bp` (+0.98%) / Z-SCORE `+1.28σ` Elevated), spread-history sparkline (252d) with ±1.5σ / ±2σ band annotations + current marker, footer caveat `Front-quarter Fed vs ECB divergence on implied rates; underlying contracts quote inverse.`, `View full analysis →` expand affordance, freshness pill.

**Density deviation** (Compact view): per the Option-(c) precedent (catalog design_guardrail #4 — *"keep shell-standard density on Compact; document any density deviation in THESIS"*) the compact view uses the SHELL-STANDARD 3-KPI density rather than the mockup's slightly denser layout that includes a tertiary tone-coloured 1D-change sub-row.  The 1D change is preserved as a primary KPI in the same numeric form; the sub-row is the same value with a different visual treatment.  Promoting that would require extending `BuildCompactShell` with a `secondaryKpis` slot, touching 10+ shipped compact views — that's a cross-tool standards change, deferred to a separate consistency PR.  No structural feature is lost from the compact view; the SPREAD / 1D CHANGE / Z-SCORE remain the three desk-canonical headline reads.

---

## Version log

| Version | Date | Change |
|---|---|---|
| v3 | 2026-06-08 | Factory dual-view + Monitor implementation per `rendering_density.md`.  Shipped `surfaces/BuildExtended.tsx`, `surfaces/BuildCompact.tsx`, `surfaces/monitor/PolicyFuturesCrossMarketSpreadWidget.tsx`, and the shared helper `surfaces/futuresCrossMarketSpreadShared.ts`.  Tier set updated to `[generic_runnable, custom_build_surface, monitor_surface]`.  Standalone-bridge typed-detail endpoint shipped at `/api/v1/rates/detail/policy-futures-cross-market`.  KNOWN_TOOL_ALIASES entry added (`get_futures_cross_market_spread_tool` → `policy_futures_get_futures_cross_market_spread_tool`).  Brought to parity with `policy_futures_get_futures_calendar_spread_tool` / `policy_futures_get_futures_butterfly_simple_tool`.  Third policy_futures tool under the dual-view contract. |
| v2 | 2026-05-26 | Stage 4f — rewrote the body to drop stale "Stage 3 scaffold" framing. |
| v1 | 2026-05-25 | Stage 3 scaffold — runtime tier only, no surfaces. |
