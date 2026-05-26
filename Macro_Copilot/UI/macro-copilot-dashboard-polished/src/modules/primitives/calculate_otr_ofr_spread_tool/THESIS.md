# THESIS — `calculate_otr_ofr_spread_tool`

> Cash-bond rich-cheap primitive. Surfaced through the default generic builder + generic Ask card.  A real Monitor widget is eligible per the surface contract but deferred until the typed-detail endpoint ships OTR-OFR series data.

**Version:** v4 (Stage 6 — surface-contract retraction)
**Module spec:** [`module.ts`](module.ts)
**Backend artifact:** `calculate_otr_ofr_spread_tool` (generic_runnable)
**Tier set:** `[generic_runnable]`
**Category:** `curve_shape`

---

## 1. What surfaces does this module ship?

- **`generic_runnable`** — runtime-status tier.  Backend ships this primitive in `_PRIMITIVE_SPECS`; the generic schema-driven builder (`GenericPrimitiveBuilder`) handles the Build entry path, and the generic `AssistantResearchCard` handles the Ask result rendering.  The module ships no bespoke surface files today — all four frontend surfaces (Library, Ask, Build, Monitor) are handled by the system defaults per the surface contract.

## 2. What does the user read off each surface?

* **Library.** Auto-derived from the backend manifest — the user finds this tool in the catalogue, filters by sub-agent / category, clicks through to the detail drawer.
* **Build (generic builder).** Controls rail generated from the backend `ToolCard.input_fields` (curve_family, tenor, lookback_days).  Output canvas renders the per-trade-date OTR-OFR spread time series via `AutoRenderer`.
* **Ask (generic research card).** Once the Supervisor routes OTR-OFR queries to the sovereign_bonds domain (separate orchestration session), the 7-zone `AssistantResearchCard` renders the result with full chart + provenance + follow-ups.
* **Monitor.** Not surfaced today.  ELIGIBLE per the contract — OTR-OFR is the desk-standard cash-bond rich-cheap / liquidity-premium signal that the desk parks on the board across (curve, tenor) variants and reads through the day.  DEFERRED — shipping a real parameterised widget requires the typed-detail endpoint to expose OTR-OFR series first.  Once that backend dependency ships, a real `monitor_surface` claim + non-stub widget can land in a single PR with the corresponding row update in `surface_contract.md`.

## 3. Why these surfaces and not others?

OTR-OFR is the desk-standard cash-bond rich-cheap / liquidity-premium signal.  Persistent positive spreads = OTR commanding a liquidity premium; reversals are usually issuance / supply-demand driven.  Each desk member typically watches 2–4 (curve, tenor) variants → parameterised Monitor card is the natural home, but only once the data path supports it.  Today the generic builder + AutoRenderer is the honest read; the Stage 6 stub widget claim was a system-half-built-state violation per the surface contract's containment principle and was retracted in v4.

## 4. What would change the design?

- Typed-detail endpoint ships OTR-OFR series → claim a capability tier and ship a real parameterised Monitor widget inside this folder (curve + tenor pickers from the catalog modal; per-widget fetch).
- Per-tenor heatmap (all G3 curves × {2Y, 5Y, 10Y, 30Y}) → add a pre-aggregated variant once the rates-page aggregated payload includes OTR-OFR.
- Bespoke chart layout (e.g. OTR yield + OFR yield overlaid with the spread + z below) → claim a capability tier and ship a per-tool surface file inside this folder.

## 5. Which backend doctrine does this module operationalise?

- **FM1** — folder name equals backend `tool_name` exactly.
- **FM3** — claims runtime tier `generic_runnable` only.  No capability tiers.
- **FM7** — `module.ts` exports a pure value.
- **FM10** — this file.
- **FM11** — `__tests__/module.spec.ts` calls `assertStandardModuleInvariants`.
- **FM12** — module imported in `src/modules/index.ts`.

---

## One-line summary

Basis-point yield spread between the on-the-run (OTR) bond and the first-off-the-run (OFR) bond for one (country, tenor) sovereign cash-bond slot, plus its 252-trading-day rolling z-score and full chartable time series.

---

## Version log

| Version | Date | Change |
|---|---|---|
| v4 | 2026-05-26 | Stage 6 — surface-contract retraction.  Dropped `monitor_surface` tier claim; deleted the stub widget file.  Module now claims `generic_runnable` only.  Monitor remains eligible per the contract but is deferred until the typed-detail endpoint ships OTR-OFR series data. |
| v3 | 2026-05-26 | Stage 6 — added `monitor_surface` tier claim and the bespoke parameterised Monitor card.  Subsequently retracted in v4 (the shipped widget was a stub). |
| v2 | 2026-05-26 | Stage 4f — rewrote the body to drop stale "Stage 3 scaffold" framing. |
| v1 | 2026-05-25 | Stage 3 scaffold — runtime tier only. |
