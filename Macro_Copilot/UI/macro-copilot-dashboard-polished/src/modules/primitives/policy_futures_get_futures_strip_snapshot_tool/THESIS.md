# THESIS — `policy_futures_get_futures_strip_snapshot_tool`

> Dual-view module under the rendering-density standard.  Ships an extended Build view (whole-strip canvas: tabular-nums strip-curve read + per-position table) AND a compact Build view (3 KPIs + first-4-positions row list) — both REQUIRED per [`rendering_density.md`](../../../../../docs_revamped/03_standards/rendering_density.md) §1 — over its OWN standalone typed-detail bridge.

**Version:** v3 (dual-view migration)
**Module spec:** [`module.ts`](module.ts)
**Backend artifact:** `policy_futures_get_futures_strip_snapshot_tool` (generic_runnable; standalone bridge via `/api/v1/rates/detail/policy-futures-strip-snapshot`)
**Tier set:** `[generic_runnable, custom_build_surface]`
**Backend sub-agent:** `policy_futures` · **Category:** `snapshots`
**Mockups:** none — this migration composes the established shared-shell design language (ControlsStrip / KPIStrip / MethodologyCard / LineageFooter / InfoTooltip / FreshnessPill + the sanctioned tabular-nums strip read); the catalogue IS the design reference.

---

## 1. What surfaces does this module ship?

- **`generic_runnable`** — runtime-status tier; backend ships in `_PRIMITIVE_SPECS`.
- **`custom_build_surface`** — the dual-view contract:
  - **Extended Build view** ([`surfaces/BuildExtended.tsx`](surfaces/BuildExtended.tsx)) — full canvas mounted for single-tool queries AND by the click-to-expand modal from the compact card.  Controls (curve_family closed Literal; as_of_date text; advanced price-field + OI-field overrides) → KPI strip (FRONT RATE · FRONT 1D Δ bp · STRIP SLOPE front→last *labelled display-only* · POSITIONS · OBSERVATIONS) → **STRIP CURVE read** (implied_rate_pct by strip position, a tabular-nums flex strip on ONE aligned as_of) → per-position table (position, contract stem, underlying + expiry, implied rate, 1d Δ bp, z 252d, OI) with each row's `row_methodology_card` surfaced via `InfoTooltip` → shared `MethodologyCard` carrying the wire's `methodology_disclosure` VERBATIM → `LineageFooter`.
  - **Compact Build view** ([`surfaces/BuildCompact.tsx`](surfaces/BuildCompact.tsx)) — grid card mounted as a node body inside multi-tool DAGs (e.g. *"SOFR strip vs Euribor strip"*).  Identity `{family} STRIP`, 3 KPIs (FRONT RATE / FRONT 1D Δ / SLOPE F→L), a compact row list of the first 4 positions (whites) in place of a sparkline — table-shaped guardrail per the scan_extremes_tool precedent — caveat footer, expand affordance.

Both Build views fetch the SAME typed-detail endpoint (`/api/v1/rates/detail/policy-futures-strip-snapshot` via `useStripSnapshot` in [`surfaces/policyFuturesStripSnapshotShared.ts`](surfaces/policyFuturesStripSnapshotShared.ts)); the compact view just renders less (rendering_density.md §2.2).

---

## 2. What does the user read off each surface?

### Extended Build view

The whole-strip canvas for ONE policy-futures family.  A PM reads, in order: (a) the title row — `{SOFR|EURIBOR|SONIA} STRIP` with flag, the slope read word (Upward-sloping / Flat / Inverted), aligned as-of date, quote-units + RFR/IBOR disclosure inline; (b) the KPI strip — front implied rate in PERCENT (with the front stem + raw price as subtext), front 1d change in bp (tone: positive bp = tightening = coral), the front→last strip slope in bp **labelled "display-only subtraction"**, the positions echo, and the aligned-day count inside the z window; (c) the strip-curve read — implied rate per slot as a tabular-nums flex strip with normalised bars, so steep / flat / inverted reads at a glance; (d) the per-position table — every configured slot's stem, current underlying + expiry, implied rate, 1d Δ (bp), 252d z (toned), and OI in contracts, each row carrying its own `row_methodology_card` tooltip; (e) the methodology card — intersection-of-trading-days anchor, quote convention + inversion rule, regime label, effective fields, YAML-locked z window, the display-only-slope note, and the wire's full P5 / ADR 0013 `methodology_disclosure` verbatim; (f) the lineage footer.

### Compact Build view

The at-a-glance DAG card.  THREE numbers + four rows: front rate (%), front 1d Δ (bp, toned), slope front→last (bp, captioned display-only); below them positions 1–4 (whites) with rate / 1d Δ / z per row and per-row methodology tooltips.  Footer carries the rolling-generic + inversion caveat, as-of, freshness; the expand arrow opens the extended view (which carries reds and beyond).

---

## 3. Why these surfaces and not others?

**Why a custom extended canvas instead of `BuildExtendedShell`.**  The shared shell is chart-centric (date-keyed `chartPoints` + reference bands); this tool has NO time series on the wire — the desk-recognised object is the CROSS-SECTIONAL strip on a SINGLE aligned date (the backend exists precisely because composing 8 outright reads loses the aligned-date guarantee).  Following the scan_extremes_tool / calculate_half_life_tool precedent for non-LEVEL-shaped tools, the module composes the shared sub-pieces inside its own canvas and puts a sanctioned tabular-nums strip-curve read in the primary zone — x is strip position, not time, so `MainChart` would lie about the axis.

**Why a table-shaped compact (3 KPIs + row list, NO sparkline).**  Same wire fact: no time series → an empty sparkline would read as missing data rather than snapshot-by-contract.  Per the scan_extremes_tool table-shaped guardrail, the compact card honours the SEMANTIC contract of rendering_density.md §2.2 (identity / headline data / methodology caveat / expand affordance / tone cues) with the tool's true headline shape: ranked-by-position rows.  First 4 rows = the whites pack — the canonical front-of-strip read; the expand affordance carries the rest.

**Why the STRIP SLOPE KPI is labelled display-only (FP9).**  The wire carries per-row `implied_rate_pct` but NO slope field; the front→last subtraction (×100 to bps) is frontend display arithmetic.  Every cell that shows it carries the "display-only subtraction" caption, and the methodology card repeats the note — the number is never presented as a backend quantity.

**Why these three compact KPIs** (front rate / front 1d Δ / slope): they answer the desk's canonical strip questions in order — *"where is the front pricing / what did it do today / is the strip steep or inverted?"*.  Alternatives considered + rejected: per-leg z (lives in the rows, toned); OI (positioning read — the sibling volume_open_interest_snapshot owns it); observation count (audit cell, extended-only).

**Why per-row `InfoTooltip` methodology.**  The backend's catalog standardness guardrail requires every row to carry its OWN regime + conversion disclosure (`row_methodology_card`) so a consumer copying one row keeps the caveat; the table surfaces it 1-to-1 per row instead of collapsing to a single footnote.

**Why controls expose exactly four fields.**  The backend Input is `curve_family` (closed Literal — unknown families 422 at the Pydantic layer) + optional `as_of_date` + the two Bloomberg field-name overrides.  Conventions (z window, strip-positions list, rounding, inverse-pricing map) are YAML-locked (PR8/PR9); exposing them would be input-schema overreach.  Empty control values are DELETED from params so the backend None-sentinels (data-max anchor / YAML field defaults) stay in charge.

**Why no `monitor_surface` claim.**  `src/components/monitor/registry.ts` was checked: no strip-snapshot tile exists (hand-authored or module-derived), so there is no duplication to absorb — but this migration ships no pilot-pattern widget either, and claiming the tier without `monitorWidgets` violates FM8.  The trigger lives in Q4.

---

## 4. What would change the design?

- **Desk demand for a glanceable strip tile** → claim `monitor_surface` + ship a clean pilot-pattern widget under `surfaces/monitor/` (parameterised on curve_family, fetching this same typed-detail endpoint — the sibling policy_futures_price_level widget is the template).  Registry duplication re-check required at that point.
- **Backend adds a strip-history sibling** (slope/curve time series) → that module ships its own chart-centric dual-view; this one stays cross-sectional.
- **Universe extension beyond positions 1–8** (greens+) → compact row list stays at 4; the strip-curve read + table absorb the new slots automatically (they walk `snapshot`).
- **`VirtualPrimitiveCanvas` drops the legacy `build` key** → delete the transitional `build: BuildExtended` alias from `module.ts.surfaces`.

---

## 5. Which backend doctrine does this module operationalise?

- **FM1** (module identity) — folder name equals backend `tool_name` exactly.
- **FM3** (surface-tier capability declaration) — claims `generic_runnable` + `custom_build_surface`.
- **FM5** (display metadata) — `displayName` / `category` / `oneLineSummary` kept from the scaffold (backend ToolCard-derived); `defaultParams` mirror the bridge's one structural field.
- **FM7** (pure-spec assembly) — `module.ts` exports a pure value.
- **FM8** (surface-file contract) — `surfaces.buildExtended` + `surfaces.buildCompact` populated; files at canonical paths; `build` aliases BuildExtended (no separate BuildSurface.tsx).
- **FM9** (routing-claim disclosure) — `typedView: null`; `richModel: false`.
- **FM10** (THESIS discipline) — this file.
- **FM11** (round-trip test) — [`__tests__/module.spec.ts`](__tests__/module.spec.ts) asserts the standard invariants + the dual-view contract.
- **FM12** (loader presence) — module imported in [`src/modules/index.ts`](../../index.ts).
- **FP9** (display-only arithmetic labelled) — the front→last slope is the ONLY frontend-computed number; labelled at every cell + in the methodology card.  The 1d Δ bp figures are unit conversions (×100 from the wire's percent points), disclosed on the table header.
- **FP13** (finance-blind shared layer) — all strip knowledge lives in `surfaces/policyFuturesStripSnapshotShared.ts`; shared components are composed, never edited.
- **P5** (honest disclosure) — output-level `methodology_disclosure` rendered verbatim; per-row `row_methodology_card` rendered per row via InfoTooltip; compact caveat names the inversion rule + aligned-intersection anchor.
- Per the **methodology-exposure standalone-bridge contract** (`methodology_exposure.md §5`): own typed-detail endpoint `/api/v1/rates/detail/policy-futures-strip-snapshot`; own service helper `fetchDetailPolicyFuturesStripSnapshot`; own frontend types `FuturesStripSnapshotOutput` / `FuturesStripSnapshotRow`.
- Per the **rendering-density dual-view contract** (`rendering_density.md §1`): both views ship + both wired in `module.ts.surfaces` + files at canonical paths.

---

## One-line summary

Whole-strip side-by-side snapshot for ONE policy-futures.

---

## Version log

| Version | Date | Change |
|---|---|---|
| v3 | 2026-06-12 | Dual-view migration.  Claimed `custom_build_surface`; shipped `surfaces/BuildExtended.tsx` (strip-curve read + per-position table + per-row methodology tooltips) + `surfaces/BuildCompact.tsx` (table-shaped guardrail) + `surfaces/policyFuturesStripSnapshotShared.ts` (single data hook + KPI builders + caveats + option enums).  Standalone bridge at `/api/v1/rates/detail/policy-futures-strip-snapshot`.  Monitor tier deliberately not claimed (registry checked; no widget ships — Q4 trigger). |
| v2 | 2026-05-26 | Stage 4f — rewrote the body to drop stale "Stage 3 scaffold" framing and answer Q2–Q5 for real. |
| v1 | 2026-05-25 | Stage 3 scaffold — runtime tier only, no surfaces. |
