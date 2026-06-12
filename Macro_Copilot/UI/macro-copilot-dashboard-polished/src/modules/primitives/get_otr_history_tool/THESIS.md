# THESIS — `get_otr_history_tool`

> Dual-view standalone-bridge module (rendering_density.md §1 +
> methodology_exposure.md §5).  The runtime tier stays
> `workflow_incompatible` — orthogonal to the live typed-detail bridge.

**Version:** v3 (dual-view migration — consolidation plan G-3.1a)
**Module spec:** [`module.ts`](module.ts)
**Backend artifact:** `get_otr_history_tool` (workflow_incompatible)
**Tier set:** `[workflow_incompatible, custom_build_surface]`
**Category:** `snapshots`
**Typed-detail endpoint:** `/api/v1/rates/detail/otr-history` (api/routes/rates/detail.py)

---

## 1. What surfaces does this module ship?

- **`custom_build_surface`** — the dual-view Build pair below.
- **`buildExtended`** — the benchmark-succession canvas: identity header
  (flag + issuer label + tenor slot), controls (country / tenor /
  lookback), KPI strip (current OTR identifier / transitions-in-window /
  current-since / maturity), the full SCD2 timeline table (most recent
  first, open window marked CURRENT), methodology card carrying the
  wire's `methodology_note` verbatim, lineage footer.
- **`buildCompact`** — KPI row (current OTR / transitions /
  current-since via the shared `compactKPIs` builder) + recent-windows
  table (top 4), forward-only caveat footer, expand affordance.  NO
  sparkline — see Q3.
- **`workflow_incompatible`** — runtime-status tier, KEPT.  The workflow
  bridge cannot dispatch this tool (no Series / Panel artifact); the
  honest `unsupported_known` card still renders wherever the bridge is
  the dispatch path, with `MODULE.unsupportedReason` verbatim.

Both Build views fetch the SAME typed-detail endpoint
(rendering_density.md §1.1) through the shared
`surfaces/otrHistoryShared.ts` hook; the compact view renders less of
the same payload.

## 2. What does the user read off each surface?

**Extended.** "Which bond IS the 10Y benchmark right now, since when,
and what did the succession look like over the lookback?"  The timeline
is the product: each SCD2 window is a (CUSIP / ISIN / ticker /
maturity) identity with detection-date-precision effective dates.

**Compact.** The three-second desk read: current OTR identifier
(primary emphasis), how often the slot rolled in the window, and when
the current benchmark took over — plus the last few windows as rows.

## 3. Why these surfaces and not others?

**CATEGORICAL SCD2 TIMELINE shape** (rendering_density.md §2.2 semantic
contract).  The wire carries dated identifier rows and an identity
snapshot — there is NO numeric series anywhere in the payload.  A
sparkline or any chart would FABRICATE a series the tool does not
return, so the compact is a table (the same guardrail the scanner
compacts document) and the extended canvas is a timeline, not a chart.

KPI choices (`compactKPIs` in the shared layer): CURRENT OTR is the
question the tool exists to answer; TRANSITIONS and CURRENT SINCE are
the only two window-level facts that fit a glance.  `maturity_date` and
the full identifier set live in the extended table instead.

**No monitor tier.**  "Which CUSIP is OTR" is a reference lookup, not a
morning-changing market read — it moves a handful of times a year per
slot (surface_contract.md §3.4 eligibility fails on glanceability
value, not on feasibility).

## 4. Honesty rules carried by the surfaces (FP9 / P5 / P6)

- Every cell is a verbatim wire field or a pure formatting of one — no
  "days on the run", no roll-frequency estimates, no auction-date
  inference (FP9; the shared layer's header documents this).
- The resolver is FORWARD-ONLY with detection-date precision (TD #27):
  `effective_from` is the first CONFIRMED observation, not the auction
  date.  The wire's `methodology_note` carries the full P5 disclosure
  and is threaded verbatim onto the extended methodology card; the
  compact footer carries the one-line short form.
- Honest absence (P6): all identifier fields are nullable and render
  '—'; an empty transitions list renders an explicit "no
  resolver-observed windows" line, never a papered-over blank.
- `effective_to == null` renders as the literal word "current" plus the
  CURRENT pill — the wire's None-preservation contract exists exactly so
  consumers can recognise the open window; we surface it, not today's
  date.

## 5. What would change the design?

- Backend artifact-bridge support for categorical/event artifacts →
  revisit the runtime tier (could become `generic_runnable`; the
  frontend change is registration-only per the scaling invariant).
- A cross-slot OTR matrix tool (all G7 slots at once) would be a NEW
  primitive with a panel-shaped module, not a growth of this one.

## 6. Which backend doctrine does this module operationalise?

ADR 0003 (`macro_data.otr_history` substrate), ADR 0007 (forward-only
OTR resolver), TD #27 (detection-date precision), P5 (methodology from
response fields), P6 (honest absence), FP9 (no client-side compute).
Sibling categorical-shape precedent:
`calculate_wirp_meeting_pricing_tool` (same
workflow_incompatible + custom_build_surface combo).

## Framework invariants (FM)

- **FM1** (identity) — folder name === `toolName`.
- **FM3** (tier claims) — `[workflow_incompatible, custom_build_surface]`; runtime tier kept verbatim.
- **FM5** (display metadata + defaults) — `displayName` / `category` / `oneLineSummary` / `defaultParams`.
- **FM6** (unsupported-reason gating) — `unsupportedReason` populated per the runtime tier, KEPT VERBATIM.
- **FM7** (pure-spec assembly) — `module.ts` exports a pure value.
- **FM8** (dual Build surfaces) — `build` === `buildExtended`; `buildCompact` shipped.
- **FM9** (standalone bridge) — `typedView: null`, `richModel: false`.
- **FM10** (THESIS discipline) — this file.
- **FM11** (round-trip test) — `__tests__/module.spec.ts` calls `assertStandardModuleInvariants`.
- **FM12** (loader presence) — module imported in `src/modules/index.ts`.

---

## One-line summary

On-the-run transition log for one (country, tenor) sovereign cash-bond slot — current OTR snapshot (CUSIP, ISIN, vendor_ticker, maturity_date, effective_from of the open window) plus the chronological list of OTR transitions intersecting the lookback window.  Pure-INGEST read of macro_data.otr_history (ADR 0003), forward-only per ADR 0007 / TD #27.

---

## Version log

| Version | Date | Change |
|---|---|---|
| v3 | 2026-06-12 | Dual-view migration (G-3.1a) — buildExtended (SCD2 timeline canvas) + buildCompact (KPI row + windows table) over /detail/otr-history; runtime tier + unsupportedReason kept verbatim. |
| v2 | 2026-05-26 | Stage 4f — rewrote the body to drop stale "Stage 3 scaffold" framing and answer Q2–Q5 for real. |
| v1 | 2026-05-25 | Stage 3 scaffold — runtime tier only, no surfaces. |
