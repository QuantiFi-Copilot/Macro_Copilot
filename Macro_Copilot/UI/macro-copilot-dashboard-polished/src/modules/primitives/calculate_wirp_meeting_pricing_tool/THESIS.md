# THESIS — `calculate_wirp_meeting_pricing_tool`

**Version:** v3 (consolidation G-3.1a — dual-view migration)
**Module spec:** [`module.ts`](module.ts)
**Backend artifact:** `calculate_wirp_meeting_pricing_tool` (workflow-incompatible, MCP-callable)
**Tier set:** `[workflow_incompatible, custom_build_surface, monitor_surface]`
**Category:** `meeting_pricing`

---

## 1. What surfaces does this module ship?

- **`workflow_incompatible`** — runtime-status tier.  Backend ships this
  primitive in `WORKFLOW_INCOMPATIBLE_TOOLS`: the per-meeting LIST output
  is not workflow-bridge composable (no Series / Panel artifact), so
  open-DAG / template lanes cannot wire it.  Workflow contexts surface
  the honest card with `MODULE.unsupportedReason` verbatim.  Capability
  tiers free-combine with the runtime status — the Build surfaces work
  through the standalone typed-detail bridge, not the workflow bridge.
- **`custom_build_surface`** — BOTH dual-view Build surfaces
  (rendering_density.md §1):
  - **`surfaces/BuildExtended.tsx`** — the full canvas (controls strip:
    central bank / selection mode / meeting count or date; per-meeting
    implied-rate strip; meetings table; KPI strip; methodology card
    threaded from `methodology_note`).
  - **`surfaces/BuildCompact.tsx`** — the grid card (3 desk KPIs —
    NEXT MEETING, IMPLIED RATE, CUM MOVE PROB — + a compact row list of
    the next meetings + caveat + `onExpand`).
- **`monitor_surface`** — `surfaces/monitor/WirpMeetingPricingWidget.tsx`
  (next-meeting implied pricing per central bank — a desk-glanceable
  state read), registered via `MODULE.monitorWidgets`.

## 2. What does the user read off each surface?

**Extended.** "What is the market pricing for the next N FOMC/ECB/BOE/BOJ
meetings?" — per-meeting implied policy rate, cumulative move
probability, number of 25bp moves priced, and the native rate change,
exactly as Bloomberg WIRP ingests them (P12: INGEST verbatim, never
recomputed).  The methodology card carries `methodology_note` from the
response — including the load-bearing caveat that the move probability
is CUMULATIVE (it can exceed ±100 and has no honest per-meeting
hike/cut/hold split).

**Compact.** The three numbers a PM glances at: when the next meeting
is, what policy rate it implies, and how much cumulative move is priced
— plus the next few meetings as compact rows (the per-meeting LIST is
the tool's essence; a sparkline would misrepresent a categorical
meeting axis as a time series).

**Monitor.** The same next-meeting read as a bento tile, parameterized
by central bank — fetched from the SAME typed-detail endpoint
(`/api/v1/rates/detail/wirp-meeting-pricing`).

## 3. Why these surfaces and not others?

The compact card uses a MEETING-ROW layout instead of the level-shape
sparkline: the output is a per-meeting snapshot list (categorical axis),
so the §2.2 semantic contract (identity / headline data / methodology /
expand / tone) is honoured with rows — the same sanctioned guardrail the
scanner compacts use.  The three compact KPIs are the desk triage read
(when / what rate / how much move); `num_25bp_moves_priced` and the
Bloomberg tickers live behind the expand.  FP9 is load-bearing here: the
UI NEVER derives per-meeting hike/cut probabilities from the cumulative
field — the naive identity does not hold (the Codex P0 finding recorded
in the backend schema).

## 4. What would change the design?

- Backend gaining a workflow-bridgeable artifact for meeting strips →
  drop `workflow_incompatible`, join the DAG lanes.
- Bloomberg shipping per-meeting (non-cumulative) move probabilities →
  a hike/cut/hold split column becomes honest; add it then, not before.
- A second selection mode beyond next-N / specific-date → new control.

## 5. Which backend doctrine does this module operationalise?

- **P12** (Bloomberg accuracy boundary) — WIRP values are INGEST
  verbatim; the surface renders them unchanged and says so.
- **P5 / FP8** (honest disclosure) — `methodology_note` threaded from
  the response, never a TSX literal; the CUMULATIVE caveat surfaces on
  both views.
- **FP9** (no client-side compute) — no derived probabilities.
- **FM1** (identity) — folder name equals backend `tool_name`.
- **FM3 / FM6** — `workflow_incompatible` runtime status with verbatim
  `unsupportedReason`, capability tiers free-combined.
- **FM8 / rendering_density.md §1-2** — both dual-view surfaces shipped
  and wired; compact carries `onExpand`, no controls strip.
- **methodology_exposure.md §5** — standalone typed-detail bridge at
  `/api/v1/rates/detail/wirp-meeting-pricing`; both views + Monitor
  fetch the same endpoint via `wirpMeetingPricingShared.ts`.
- **FM10 / FM11 / FM12** — this file; `__tests__/module.spec.ts`;
  alphabetical loader entry.

---

## Version log

| Version | Date | Change |
|---|---|---|
| v3 | 2026-06-11 | Consolidation G-3.1a — dual-view migration (BuildExtended + BuildCompact + Monitor widget on the standalone bridge; workflow_incompatible runtime status retained). |
| v2 | 2026-05-26 | Stage 4f — rewrote the body to drop stale scaffold framing. |
| v1 | 2026-05-25 | Stage 3 scaffold — workflow_incompatible only. |
