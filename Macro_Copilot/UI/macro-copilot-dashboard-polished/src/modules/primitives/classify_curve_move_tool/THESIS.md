# THESIS — `classify_curve_move_tool`

> Migrated to the dual-view rendering-density standard (consolidation G-3.1c) — **this migration retired the LAST `typedView` claim in the codebase** (`typedView: 'regime'` → `null`), making the PrimitiveViewKind routing substrate dead code for the target-#5 deletion.

**Version:** v3 (typed-view → dual-view migration)
**Module spec:** [`module.ts`](module.ts)
**Backend artifact:** `classify_curve_move_tool` (workflow-incompatible, MCP-callable; standalone bridge via `GET /api/v1/rates/detail/regime`)
**Tier set:** `[workflow_incompatible, custom_build_surface, monitor_surface]`
**Backend sub-agent:** `sovereign_bonds` · **Category:** `forwards_classify`

---

## 1. What surfaces does this module ship?

- **`workflow_incompatible`** — runtime-status tier.  The categorical
  output (six-label regime + numeric evidence) is not a `Series` /
  `Panel` artifact, so workflows cannot compose it; workflow contexts
  surface the honest card with `MODULE.unsupportedReason` verbatim.
  Capability tiers free-combine with this status — the Build surfaces
  work through the standalone typed-detail bridge.
- **`custom_build_surface`** — BOTH dual-view Build surfaces:
  - **`surfaces/BuildExtended.tsx`** — full canvas: controls (curve
    family, front/back tenors, lookback period, field override) +
    REGIME hero (the toned classification with the backend's
    `regime_description` as subtext, spread move, display-only driver,
    spread now-vs-prior) + the four-quadrant per-leg context strip +
    methodology card + lineage footer.
  - **`surfaces/BuildCompact.tsx`** — grid card: 3 desk KPIs (REGIME
    with tone · SPREAD Δ bp · DRIVER) + caveat + `onExpand`.  NO
    sparkline — see Q3.
- **`monitor_surface`** — `surfaces/monitor/CurveClassifierWidget.tsx`
  PRESERVED VERBATIM (it reads the pre-aggregated `/regimes` feed via
  `RatesDataContext`, not the typed-detail bridge).
- Both Build views fetch the SAME endpoint through
  `surfaces/classifyCurveMoveShared.ts` (single hook, KPI builders,
  tone maps, methodology rows — P10).

## 2. What does the user read off each surface?

**Extended.** "What kind of move was that?"  The hero leads with the
classification (Bull Steepener / Bear Flattener / Parallel Shift /
Twist…) toned by direction, the backend's own plain-English
`regime_description`, the spread change that produced the call, and a
display-only FRONT-LED/BACK-LED/BALANCED driver.  The quadrant strip
shows the four numbers behind the call (front/back levels now vs
prior + signed changes).  The methodology card carries the window,
the spread label, and the config.yaml-locked thresholds.

**Compact.** The triage read: the regime label (toned), the spread
move in bps, and which leg owned it.

**Monitor.** The existing G4 classifier tile (unchanged).

## 3. Why these surfaces and not others?

The compact card is KPI-CENTRIC with NO sparkline: the wire is ONE
classified observation (`RegimeOutput.current_metrics` only) — a
time-series visual would misrepresent a snapshot as history.  This is
the same §2.2 semantic-contract guardrail the half-life compact
documents (identity / headline data / methodology / expand / tone all
honoured).  The DRIVER chip is a display-only comparison of the
backend's own per-leg changes (1.5× dominance rule, labelled
display-only in code and on the methodology card) — FP9 permits
display arithmetic on backend numbers, never analytics.

## 4. What would change the design?

- The backend emitting a regime TIME SERIES (classification per day)
  → the compact gains an honest regime-strip visual; Extended gains a
  history panel.
- Bridge support for categorical artifacts → drop
  `workflow_incompatible`, join the DAG lanes.
- A confidence field on the wire → joins the hero (it does NOT exist
  today; the classifier is deterministic — we render no invented
  confidence).

## 5. Which backend doctrine does this module operationalise?

- **rendering_density.md §1–2** — both dual-view surfaces; compact has
  no controls strip and calls `onExpand`.
- **methodology_exposure.md §5** — the standalone `/detail/regime`
  bridge; both views consume it via the shared hook.
- **P5 / FP8** — methodology from wire fields (`spread_label`,
  `prior_date`, `lookback_period`); the classification thresholds are
  cited as config.yaml-locked backend constants, never invented copy.
- **FP9** — the driver label is display-only comparison of backend
  numbers, stated as such on the surface.
- **FM1 / FM3 / FM6 / FM7 / FM8 / FM9** — folder = tool_name;
  free-combined tiers with verbatim `unsupportedReason`; pure spec;
  `typedView: null` (the LAST typed-view claim retired);
  `richModel: false`.
- **FM10 / FM11 / FM12** — this file; `__tests__/module.spec.ts`
  (pilot-pattern dual-view spec); alphabetical loader entry.

---

## Version log

| Version | Date | Change |
|---|---|---|
| v3 | 2026-06-12 | Consolidation G-3.1c — dual-view migration; retired the LAST typedView claim ('regime'); Monitor widget preserved verbatim. |
| v2 | 2026-05-26 | Stage 4d — Monitor catalog widget. |
| v1 | 2026-05-25 | Stage 4a — typed-view module (ResultRenderer + typedView 'regime'). |
