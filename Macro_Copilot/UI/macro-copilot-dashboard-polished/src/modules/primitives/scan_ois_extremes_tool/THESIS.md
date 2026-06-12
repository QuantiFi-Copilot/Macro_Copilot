# THESIS — `scan_ois_extremes_tool`

> Dual-view build: Stage 3 scaffold (`paused` — "no live route yet") →
> standalone-bridge dual-view.  The backend route is now live at
> `/api/v1/rates/detail/ois-scanner`; this module mirrors the sovereign twin
> `scan_extremes_tool` file-for-file (P3 — the two LEVEL-metric scanner
> modules cannot drift), with the OIS vocabulary (markets / overnight
> indices), the RATE-space level column, and the `get_ois_rate_level_tool`
> per-row deep-link.

**Version:** v3 (dual-view + standalone-bridge build)
**Module spec:** [`module.ts`](module.ts)
**Backend artifact:** `scan_ois_extremes_tool` (`manifest_typed_view`)
**Bridge endpoint:** `/api/v1/rates/detail/ois-scanner`
**Tier set:** `[manifest_typed_view, custom_build_surface]`
**Backend sub-agent:** `ois` · **Category:** `screening`

---

## 1. What surfaces does this module ship?

- **`manifest_typed_view`** — runtime-status tier.  The tool ships in the
  orchestrator's `_MANIFEST_ONLY_BUILD_TOOLS` set (`orchestrator/events.py`)
  exactly like the sovereign twin: a typed-detail endpoint exists, but the
  workflow bridge does not dispatch the snapshot-shape scanner output.  The
  stale Stage-3 `paused` claim ("backend has no live route yet") is replaced
  — the route landed in `api/routes/rates/detail.py` wrapping
  `rates_agent/ois/tools/scan_ois_extremes`.
- **`custom_build_surface` → extended** (`surfaces/BuildExtended.tsx`).
  Universe-scan canvas: identity row + scan-summary cards (flagged-count /
  distribution / G6 market coverage) + controls strip (curve scope /
  threshold / top-N / field) + z-score distribution histogram + scan-summary
  callouts + ranked detail table with per-row "Open in Build" deep-link to
  the per-stem `get_ois_rate_level_tool` + methodology card + lineage
  footer.
- **`custom_build_surface` → compact** (`surfaces/BuildCompact.tsx`).  Grid
  card with the top-5 ranked rows in a (rank / market / tenor / rate % /
  Δ bp / z-score) table — NOT a sparkline.  Tone-cued z-scores (|z| ≥ 1.5
  amber, |z| ≥ 2.0 coral/mint by direction).  Footer caveat ("OIS prices the
  expected policy path; 252d window locked.") + "View all N" expand
  affordance.

## 2. What does the user read off each surface?

**Extended (single-tool query).** A PM opens the canvas with one question —
*"where is the G6 OIS policy-path pricing stretched today?"*.  The scan-
summary card answers it in one number ("7 of 42 stems flagged at |z| ≥
1.5σ").  The distribution histogram shows whether the flagged extremes lean
high (policy repriced hawkish vs the trailing year) or low (dovish), and how
many sit beyond the 2σ envelope.  The market-coverage card answers "is this
one policy path or several?" at a glance.  The ranked table is the action
layer — every flagged stem is a candidate for a deeper read; clicking "Open
in Build" on a row routes to the per-stem `get_ois_rate_level_tool` with the
(curve, tenor) bound.

**Compact (multi-tool DAG node).** A PM running a multi-tool query like
*"compare sovereign extremes vs OIS extremes vs ZCIS extremes"* sees three
scanner cards side-by-side.  The OIS card's three things the PM reads in 2
seconds: (1) WHICH stems top the ranking ("USD (SOFR) 2Y / EUR (ESTR) 10Y"),
(2) at WHAT level and |z| ("3.87% · +2.6σ" — coral), (3) how many stems are
flagged total ("7 Flagged at |z| ≥ 1.5σ").  The expand affordance opens the
full extended canvas in a modal with the multi-tool DAG behind it; the
"View all 7" footer link is a secondary entry to the same modal.

## 3. Why these surfaces and not others?

**Why a custom compact view (top-N table) rather than `AutoRenderer` or the
standard `BuildCompactShell`?** Same rationale as the sovereign twin: the
standard `BuildCompactShell` is LEVEL-shape oriented — three KPI cells +
sparkline + footer.  The scanner's headline data is a ranked LIST of
extremes, not a single number with a chart.  A literal 3-KPI mapping would
surface only ONE row and would drop the comparative context (the rest of
the top-N + the flagged count) that IS the scanner's point.  Per the
catalog guardrail (Option (c) shell-density precedent): keep shell-standard
density on Compact (3 KPIs equivalent → 5 ranked rows, single identity →
OIS EXTREMES) and document the density deviation here.  This module honours
the SEMANTIC contract of rendering_density.md §2.2 (identity / headline
data / methodology / expand / tone cues) with a scanner-shaped layout,
reusing the shared `FreshnessPill` and `toneTextClass` helpers so visual
treatment matches the rest of the compact catalogue.  Column deviation vs
the twin: the compact row carries RATE % (the par-swap-rate LEVEL — the OIS
market's native percent quote) and Δ (bp) in place of the twin's
yield-and-curve pair, because the OIS market label already names the
overnight index ("USD (SOFR)") and the daily policy-path repricing is the
desk's second read on a compact card.

**Why a custom extended view rather than `BuildExtendedShell`?** The
extended shell is chart-centric (chartPoints + reference bands + KPI
strip).  The scanner needs a distribution histogram + a ranked-detail
table.  The shared `ControlsStrip` + `MethodologyCard` + `LineageFooter`
ARE finance-blind enough to drop in directly; this module composes them
inside a scanner-shaped layout rather than fight the chart-centric main-
canvas — mirrored from the twin.

**Why `manifest_typed_view` (and not `generic_runnable` or the old
`paused`)?** The old `paused` claim asserted "backend has no live route
yet", which is now false — the typed-detail endpoint shipped.  The runtime
tier mirrors the sovereign twin's because the backend gate treats the two
scanners identically: both sit in `_MANIFEST_ONLY_BUILD_TOOLS`
(`orchestrator/events.py`) so Ask traces pass the workspace gate while the
workflow bridge does not dispatch the snapshot-shape output.  Per
`MIGRATION_RULES.md §4 step 6` + §8 anti-pattern, the tier set mirrors the
gate; do NOT swap to `generic_runnable`.

**Why no Monitor tile?** The sovereign twin's tile is a backward-compat
lock — a pre-aggregated `RatesDataContext`-fed widget whose `id: 'scanner'`
persists in user dashboards.  The OIS module has no legacy widget to
preserve and no pre-aggregated OIS feed; a parameterised per-widget-fetch
tile is a V2 candidate (see Q4) but is not claimed today (FM4 parsimony).

**Why not `ask_surface`?** The chat dispatcher's generic
`AssistantResearchCard` handles the "give me the OIS extremes" Ask response
perfectly today — the response shape (top-N rows) reads naturally as a chat
bubble.  Deferred per FM4.

**Why not `custom_preview_widget`?** Persisted-artifact preview cards for
scanners are well-served by the artifact-type generic registry; the scanner
doesn't carry a domain-specific preview need beyond what the workspace's
node renderer already does.

## 4. What would change the design?

- **Workflow-bridge scanner support.** If the bridge gains a snapshot-shape
  dispatch path (the `_PRIMITIVE_SPECS` entries exist as
  TERMINAL_ONLY_SNAPSHOT per PR-10G), the runtime tier promotes from
  `manifest_typed_view` to `generic_runnable`; the Build surfaces are
  unchanged.
- **Wire-level `methodology_disclosure` field.** Today the Pydantic
  `OISScannerOutput` does NOT carry a `methodology_disclosure` string (same
  gap as the sovereign twin's `ScannerOutput`; the multi-metric futures
  scanners DO).  The Extended view's methodology card synthesises rows from
  the YAML's published conventions via the per-tool TS registry in
  `surfaces/oisScannerShared.ts`.  When the backend ships
  `methodology_disclosure` on the wire, the Extended view switches to the
  wire field — mirrors the swap_spread / bond-futures scanner precedent
  noted in `api/routes/rates/detail.py`.
- **A Monitor tile.** If the desk's morning ritual grows an OIS-universe
  sweep read, a parameterised tile fetching `fetchDetailOisScanner`
  per-widget (NOT a pre-aggregated feed) earns the monitor tier — the twin's
  V2 parameterised-widget plan is the template.
- **Universe growth.** New OIS curve families (beyond USD_SOFR_OIS /
  EUR_ESTR_OIS / GBP_SONIA_OIS / JPY_OIS / AUD_OIS / CAD_OIS) extend the
  per-tool `FAMILY_REGISTRY` in `oisScannerShared.ts`; rows whose family is
  absent from the registry already fall back gracefully.

## 5. Which backend doctrine does this module operationalise?

- **FM1** (module identity) — folder name equals backend `tool_name`
  exactly (`scan_ois_extremes_tool`).
- **FM3** (surface-tier capability declaration) — claims
  `[manifest_typed_view, custom_build_surface]`; both Build files exist.
- **FM4** (tier parsimony) — no monitor / ask / preview claims; dual-view
  mandate per `rendering_density.md §1.2`.
- **FM5** (display-metadata sourcing) — `oneLineSummary` preserved from the
  scaffold (paraphrases the backend tool description); the backend's
  `OISScannerInput` positive defaults (`top_n=10`, `min_abs_z_score=1.5`,
  `field_name='PX_LAST'`) stay implicit.
- **FM6** (unsupported-reason gating) — `unsupportedReason` populated (the
  framework invariant requires it when `manifest_typed_view` is in the tier
  set — see `__test-utils.ts` invariant #8); copy updated from the stale
  "no live route yet" to the honest dual-view affordance.
- **FM7** (pure-spec assembly) — `module.ts` exports a pure value with no
  side effects.
- **FM8** (surface-file contract) — `surfaces/BuildExtended.tsx` +
  `surfaces/BuildCompact.tsx` present and referenced exactly once each;
  `build` kept as the transitional alias of `buildExtended` for the legacy
  dispatcher.
- **FM9** (routing-claim disclosure) — `typedView: null` + `richModel:
  false` per the standalone-bridge contract.
- **FM10** (THESIS discipline) — this file (v3 — replaces the v2 Stage 4f
  `paused` stub).
- **FM11** (round-trip test) — `__tests__/module.spec.ts` calls
  `assertStandardModuleInvariants` + the rendering_density.md §11 dual-view
  contract checks (both `surfaces.buildExtended` + `surfaces.buildCompact`
  populated; `typedView === null`).
- **FM12** (loader presence) — module imported in `src/modules/index.ts`
  (entry already present; alphabetical position unchanged).
- **rendering_density.md §1 / §2.2** (dual-view mandate) — extended +
  compact both populated; compact is a top-N TABLE per the SCANNER-shape
  guardrail; methodology is reachable via the footer caveat + tooltip.
- **methodology_exposure.md §5** (standalone-bridge contract) — own
  `/api/v1/rates/detail/ois-scanner` endpoint + own `fetchDetailOisScanner`
  helper + own `ScanOisExtremesOutput` TS type mirror; no shared `typedView`
  reuse.
- **P3** (sibling consistency) — mirrored file-for-file from
  `scan_extremes_tool`; the deviations (RATE-space column, Δ bp compact
  column, `field_name` control, no monitor tier) are each documented in Q3.
- **P5** (methodology honesty) — every methodology row in
  `oisScannerShared.buildMethodologyRows` anchors to a substantive backend
  constant (252d window, OIS universe, PX_LAST par swap rate), not a TS
  literal of the caveat itself.

---

## One-line summary

OIS universe rate scanner — ranks every (curve_family, tenor) OIS stem by
absolute 252d rolling z-score of the PX_LAST par swap rate; top-5 table
compact view, distribution + ranked-detail extended view.  OIS analogue of
`scan_extremes_tool`, mirrored file-for-file.

---

## Version log

| Version | Date | Change |
|---|---|---|
| v3 | 2026-06-12 | Dual-view + standalone-bridge build — stale `paused` claim replaced with the sovereign twin's tier shape (`manifest_typed_view` + `custom_build_surface`); new `surfaces/BuildExtended.tsx` + `surfaces/BuildCompact.tsx` composed on the pre-existing `surfaces/oisScannerShared.ts`; consumes the integrator-landed `/api/v1/rates/detail/ois-scanner` endpoint + `fetchDetailOisScanner` helper + `ScanOisExtremesOutput` TS mirror. |
| v2 | 2026-05-26 | Stage 4f — rewrote the body to drop stale "Stage 3 scaffold" framing and answer Q2–Q5 for real. |
| v1 | 2026-05-25 | Stage 3 scaffold — runtime tier only, no surfaces. |
