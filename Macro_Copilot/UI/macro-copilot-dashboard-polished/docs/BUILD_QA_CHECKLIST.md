# Build surface — manual QA checklist

A pre-flight checklist for the Build surface, written to be runnable
**without** Playwright, headless-browser automation, or screenshot
infra. Drop a workspace into the page, walk the sections below,
mark each line ✅/❌. Anything in the **automatic** column is already
locked by `npm run test:build`; the manual column is what a human
has to confirm in the browser.

Run the suite first:

```
cd UI/macro-copilot-dashboard-polished
npm run test:build           # all 17 build-folder test files
npx tsc -p tsconfig.app.json --noEmit
```

If those pass, the regressions PR1–PR3 closed are still closed.
The list below is the **manual** layer on top.

---

## 1. Persisted rich-model widgets (PR1)

Open a workspace whose terminal node was emitted by one of these tools.

| Tool | Manual check | Auto-locked by |
|---|---|---|
| `calculate_pca_yield_curve_tool` | Time-series sparkline renders from the persisted Series. Loadings / variance / current-factor panel is honestly missing with a "re-run from the builder" hint. | `richModelWidgetContract.test.ts`, `persistedModelAdapters.test.ts`, `regressionLock.test.ts` §A |
| `calculate_rolling_regression_tool` | β path renders. Latest-snapshot table marked unavailable + builder hint visible. | same |
| `calculate_beta_adjusted_spread_tool` | Spread path renders. Construction snapshot marked unavailable. | same |
| `calculate_yield_change_attribution_pca_tool` | Surfaces "pure-snapshot — re-run for detail" tile, **NOT** a misleading time-series sparkline. | same |
| `calculate_half_life_tool` | Same as attribution — pure-snapshot tile, no fake sparkline. | same |
| **Unknown tool** | Falls back to the generic-artifact widget for the artifact type; never crashes the page. | `regressionLock.test.ts` §A "every (tool × Series fixture) decodes" |

Quick browser checks:
- [ ] Open DevTools → Network tab; reloading the page issues exactly one
  `/api/artifacts/payload/{hash}` per node (cache is honoured).
- [ ] No `runPrimitive` / live-tool-execution call from the widget on
  mount.

---

## 2. Date-policy widgets (PR2)

Three operators emit dates the user should NEVER see literally:

| Sentinel | Source | Manual check |
|---|---|---|
| `1900-01-01` | `summarize_series` output (regime summary Panels, scalar Series) | The widget shows a **"scalar marker"** badge OR suppresses the as-of footer entirely. No "as-of 1900-01-01" anywhere on the surface. |
| `1970-01-01 + N days` | `conditional_aggregate` Series | Sparkline x-axis labels read `t-N` / `t0` / `t+N`. **NOT** `1970-01-06`. |
| `NaN` / `null` date strings | Anywhere | Render as `—`, not `Invalid Date` and not `1970-01-01`. |

Specific surfaces to walk through:
- [ ] Event-study workspace → aggregate Series card. Headline reads
  "at t+5" (or similar), not "as-of 1970-01-06".
- [ ] Regime workspace → regime-summary Panel card. Footer either
  absent or shows the "scalar marker" pill, no calendar date.
- [ ] Multi-row Panel where some row index entries are `null` →
  affected cells show `—`.

Auto-locked by `widgetFormat.test.ts` (helper) + `regressionLock.test.ts`
§B (source-level contract that each widget calls `classifyArtifactDate`).

---

## 3. Workflow-specific dashboards (PR2)

Open one workspace per archetype and visually confirm:

### Event study
- [ ] Signal / Target / Events / Windows / Aggregate / Compare nodes all
  visible in the Results tab with their correct widget type
  (Series sparkline, EventSet event count, WindowedPanel preview
  table, conditional-aggregate offset-axis Series).
- [ ] Event count comes from `event_dates.length` — `~4` for a quarterly
  CPI fixture, **NOT** ~1,261 (calendar days). Auto-locked by
  `widgetContract.test.ts:110-121` + `regressionLock.test.ts` §B.

### Regime conditioned relationship
- [ ] LHS / RHS / regime signal / SeriesSet (β-α-R²) / summary Panels
  all bound. High-regime + low-regime Panels show the "scalar marker"
  pill where appropriate.
- [ ] Resolver doesn't double-bind `low_summary` to `highSummary`
  — different node IDs in the chip labels. Auto-locked by
  `resolveWorkflowArtifacts.test.ts`.

### Backtest archetype
- [ ] Dashboard renders the **paused banner** regardless of whether
  artifacts exist. Copy reads "Backtest archetype · paused on the
  LLM surface". No fabricated Sharpe / CAGR / drawdown values.
  Auto-locked by `dashboardContract.test.ts` + `regressionLock.test.ts` §D.

---

## 4. Parameter overrides + fork (PR3)

In a forkable workspace:

| Check | Expected |
|---|---|
| Slot panel | Lists every slot from `template.slot_schema`. Lock icon on read-only slots. |
| `*_tool_name` slot | Renders a `<select>` populated from the tools catalogue. No free-text input. |
| `*_output_field` slot | Renders a `<select>` filtered by the paired `*_tool_name` slot's current value. |
| Out-of-vocab current value | Tool/field control renders the current value with a coral border + "not registered" / "not in {tool}" disabled `<option>`. |
| Loading state | Before the tools catalogue resolves, controls render the read-only chip with "Loading tool catalogue…" caption — never a free-text input. |
| Apply gate | Edit `signal_tool_name` to `get_swap_rates_tool`, leave `signal_output_field` at `time_series` (invalid for swap rates) → Apply button is disabled and the chip shows a coral inline error. |
| Mark valid | Change `signal_output_field` to `swap_curve` → Apply button re-enables. |
| Fork success | Click Apply → URL changes to `/workspace/{new-slug}`. Original workspace's slug is unchanged. |
| Fork failure | Force a network error → error banner shows, queue stays populated (no work lost). |
| Affected stages chip | Shows the stage(s) the slot likely affects. For unmappable slots, shows a dashed **"Workspace-scoped"** badge — never the old "(computed during binding)" copy. |
| Discard all | Clears the queue without forking. |

Auto-locked by `slotControls.test.ts` + `regressionLock.test.ts` §C.

---

## 5. Proposed-override chips (PR3)

In a chat turn where the assistant emits `proposed_overrides`:

- [ ] Each chip shows `slot.field: value`.
- [ ] Hovering an **invalid** chip (unknown tool, mismatched output_field, unknown slot) shows a coral "cannot apply safely" tooltip with the rejection reason.
- [ ] Clicking an invalid chip does **NOT** queue the override (chip is `disabled`).
- [ ] Clicking a valid chip queues it; chip turns mint with a ✓ icon.
- [ ] Chip dispatches into the same queue the Slots panel reads — the PendingOverridesBar lights up immediately.

Auto-locked by `slotControls.test.ts` (chip validator) + `regressionLock.test.ts` §D.

---

## 6. Empty-state entrypoints (PR3)

On the empty Build surface:

- [ ] Six category tiles render. Each active tile either:
  - deep-links to `/workspace?builder=<tool>` (e.g. "Decompose a move",
    "Compare regimes"), OR
  - seeds the composer with a sentence that maps to a supported
    workflow template ("Analyze a spread", "Screen a universe").
- [ ] Two `soon` tiles ("Run a backtest", "Build custom DAG") are
  dimmed, show a `soon` badge, and surface a one-line reason.
  Clicking them does nothing.
- [ ] The "Build custom DAG" tile's `promptSeed` is empty — even if
  the click handler were to fire (it doesn't, the button is `disabled`),
  there's no misleading composer text waiting in the wings.

Auto-locked by `buildEmptyState.test.ts` + `regressionLock.test.ts` §D.

---

## 7. Smoke-test the whole flow

End-to-end smoke (not exhaustive — just sanity):

1. Fresh empty Build surface → click "Analyze a spread" → composer is seeded.
2. Edit the prompt and send → workflow routes, workspace is created, navigates to `/workspace/{slug}`.
3. Open the Results tab → widgets render against persisted artifacts.
4. Open the Parameters tab → edit `tenor` from `10Y` to `5Y` → Apply & fork → new workspace opens.
5. Open the new workspace's Results tab → widgets render against the new bindings.
6. Reload mid-flow → state survives (workspace detail re-fetches; payload cache rebuilds).

If anything in steps 1–6 misbehaves, file an issue and identify which PR
(1–3) the regression breaks.

---

## 8. Test coverage map

| Section | Helper-level | Source/Contract-level |
|---|---|---|
| Persisted decode (§1) | `persistedModelAdapters.test.ts` (20) | `richModelWidgetContract.test.ts` (18), `regressionLock.test.ts` §A (5) |
| Date policy (§2) | `widgetFormat.test.ts` (53) | `regressionLock.test.ts` §B (8) |
| Workflow dashboards (§3) | `resolveWorkflowArtifacts.test.ts` (12), `dashboardRegistry.test.ts` (18) | `dashboardContract.test.ts` (11), `regressionLock.test.ts` §D (1) |
| Parameter overrides (§4) | `slotControls.test.ts` (40) | `regressionLock.test.ts` §C (5) |
| Proposed chips (§5) | `slotControls.test.ts` (chip validator) | `regressionLock.test.ts` §D (1) |
| Empty state (§6) | — | `buildEmptyState.test.ts` (8), `regressionLock.test.ts` §D (1) |

Total automated coverage: **17 test files, 373 checks**.

## What's intentionally NOT in this checklist

- **Playwright / headless-browser flows.** PR4 explicitly opted out
  of browser automation. The regression-lock layer + this checklist
  is the substitute.
- **Screenshot diffs / visual-regression infra.** Same reason.
- **Backend integration tests.** Those live in the `rates_agent` /
  `shared` Python tree.
- **Performance / load benchmarks.** Not within PR1–PR4 scope.

Add an entry here whenever a new manual surface ships; remove an
entry whenever an automated lock supersedes it.
