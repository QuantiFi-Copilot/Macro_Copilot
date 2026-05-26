# THESIS — `calculate_otr_ofr_spread_tool`

> Stage 6 reference implementation — first cash-bond new-feature primitive through the module-first dispatch architecture.

**Version:** v3 (Stage 6)
**Module spec:** [`module.ts`](module.ts)
**Backend artifact:** `calculate_otr_ofr_spread_tool` (generic_runnable)
**Tier set:** `[generic_runnable, monitor_surface]`
**Category:** `curve_shape`

---

## 1. What surfaces does this module ship?

- **`generic_runnable`** — runtime-status tier.  Backend ships this primitive in `_PRIMITIVE_SPECS`; the generic schema-driven builder handles the Build entry path.  No `surfaces.build` claim — the per-(curve, tenor) spread + rolling-z series render natively via `AutoRenderer`.
- **`monitor_surface`** — parameterised bento card at `surfaces/monitor/OtrOfrSpreadWidget.tsx`.  Registered via `MODULE.monitorWidgets[0]` with widget id `otr_ofr_spread`.  The catalog modal's config form lets the user pick `curve_family` + `tenor` at add-time.  Stage 4d's catalog walker discovers it through the module spec.

## 2. What does the user read off each surface?

* **Build (generic builder).** Controls rail generated from the backend `ToolCard.input_fields` (curve_family, tenor, lookback_days).  Output canvas renders the per-trade-date OTR-OFR spread time series via `AutoRenderer` — current bp spread + 252-day rolling z.
* **Monitor (`otr_ofr_spread`).** Parameterised — user picks a (curve, tenor) at add-time.  Compact tile shows the current OTR-OFR spread in bps + rolling-z signal so the desk can park multiple variants (UST 10Y, Bund 10Y, OAT 30Y) on the same Monitor board.

## 3. Why these surfaces and not others?

OTR-OFR is the desk-standard cash-bond rich-cheap / liquidity-premium signal.  Persistent positive spreads = OTR commanding a liquidity premium; reversals are usually issuance / supply-demand driven.  Each desk member typically watches 2-4 (curve, tenor) variants → parameterised Monitor card.  No bespoke Ask card today because the chat-result framing adds no value over the generic research card here (the answer is "X bps, z=Y", trivially summarised inline).

## 4. What would change the design?

- Per-tenor heatmap (all G3 curves × {2Y, 5Y, 10Y, 30Y}) → add a pre-aggregated variant to `MODULE.monitorWidgets[]` reading from a future dashboard endpoint.
- Bespoke chart layout (e.g. OTR yield + OFR yield overlaid with the spread + z below) → claim `custom_build_surface` and ship `surfaces/BuildSurface.tsx`.

## 5. Which backend doctrine does this module operationalise?

- **FM1** — folder name equals backend `tool_name` exactly.
- **FM3** — claims runtime tier `generic_runnable` + capability `monitor_surface`.
- **FM7** — `module.ts` exports a pure value.
- **FM8** — `monitor_surface` populated via `MODULE.monitorWidgets[]`.
- **FM10** — this file.
- **FM11** — `__tests__/module.spec.ts` calls `assertStandardModuleInvariants`.
- **FM12** — module imported in `src/modules/index.ts`.

---

## One-line summary

Basis-point yield spread between the on-the-run (OTR) bond and the first-off-the-run (OFR) bond for one (country, tenor) sovereign cash-bond slot, plus its 252-trading-day rolling z-score and full chartable time series.

---

## Stage 6 reference acceptance

Adding the bespoke Monitor surface required ZERO edits to:
- `src/components/build/BuildShell.tsx`
- `src/components/build/primitive/VirtualPrimitiveCanvas.tsx`
- `src/components/ask/ConversationCanvas.tsx`
- `src/components/monitor/registry.ts`
- `src/components/monitor/WidgetRenderer.tsx`
- `src/lib/toolNames.ts` (only DELETION of the Stage 1 hand-authored entry)

---

## Version log

| Version | Date | Change |
|---|---|---|
| v3 | 2026-05-26 | Stage 6 — added `monitor_surface` tier and the bespoke parameterised Monitor card. |
| v2 | 2026-05-26 | Stage 4f — rewrote the body to drop stale "Stage 3 scaffold" framing. |
| v1 | 2026-05-25 | Stage 3 scaffold — runtime tier only. |
