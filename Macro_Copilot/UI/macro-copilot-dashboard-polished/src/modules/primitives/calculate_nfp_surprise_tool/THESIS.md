# THESIS — `calculate_nfp_surprise_tool`

> Event-class primitive brought under the dual-view rendering-density contract.  Ships an extended Build view (full canvas for single-tool queries) AND a compact Build view (grid card for multi-tool query DAGs) — both REQUIRED per [`rendering_density.md`](../../../../../docs_revamped/03_standards/rendering_density.md) §1 — over its own standalone typed-detail bridge.  Single-country single-event: US NFP is wire-locked, so the only control is the display window.  The Monitor / Ask retraction of 2026-05-26 remains binding: event releases are not desk-glanceable state.

**Version:** v5 (dual-view Build implementation)
**Last reviewed:** 2026-06-11
**Module spec:** [`module.ts`](module.ts)
**Backend artifact:** `calculate_nfp_surprise_tool` (generic_runnable; standalone bridge via `/api/v1/rates/detail/nfp-surprise`)
**Tier set:** `[generic_runnable, custom_build_surface]`
**Category:** `economic_release_surprises`
**Mockups:** `./mockups/Compact.png` + `./mockups/Extended.png` — captured from the rendered polished surfaces by the integrator per the mockup-first workflow.

---

## 1. What surfaces does this module ship?

- **`generic_runnable`** — runtime-status tier; backend ships in `_PRIMITIVE_SPECS`.
- **`custom_build_surface`** — the dual-view contract:
  - **Extended Build view** ([`surfaces/BuildExtended.tsx`](surfaces/BuildExtended.tsx)) — full canvas mounted for single-tool queries.  Controls strip (Lookback in releases — the ONLY backend knob, since country=US and event_type=nfp are wire-locked — plus a frontend-only Chart-series toggle), top-right Z-Score / Latest-Print / Event cards, a six-cell KPI strip (surprise, z, actual, consensus median, as-released prior, releases shown), the per-release surprise series chart in k jobs (toggleable to the rolling z-score series), stretch-context panel, methodology card threading the wire's `methodology_note`, lineage footer.
  - **Compact Build view** ([`surfaces/BuildCompact.tsx`](surfaces/BuildCompact.tsx)) — grid card mounted as a node body inside multi-tool query DAGs (e.g. *"NFP surprise vs the 2s10s move"*, *"NFP surprise next to the CPI surprise timeline"*).  Headline 3-KPI strip, surprise-series sparkline with ±σ bands, the consensus-median + revisions caveat footer, click-to-expand affordance.

## 2. What does the user read off each surface?

### Extended Build view

The full investigation canvas for the US NFP-surprise release series.  A PM reads, in order: (a) the title row (`US NFP · Surprise`), the latest release date and reference period; (b) the top-right cards — the rolling z-score of the latest surprise (with Normal / Elevated / Extreme regime), the latest print (actual vs consensus median, k jobs, with period), and the EVENT card carrying the wire-locked identity + the revisions caveat; (c) the KPI strip — latest surprise (k jobs, tone-coloured: hot payrolls = coral per the fixed-income "yields up" read), z-score, actual, consensus median, as-released prior, and the realised-release count; (d) the per-release surprise chart (sparse ~monthly series in k jobs with ±2σ/±1.5σ bands over the displayed window), toggleable to the rolling z-score series with fixed ±1.5/±2.0 regime bands; (e) the stretch-context panel answering "is this print a repricing trigger or noise?"; (f) the methodology card — surprise identity (wire-frozen `actual − consensus_median`), the wire-locked single-country single-event framing, the releases-not-days z-window, the display-vs-z-window separation, the COUNT-units honesty note (canonical wire series = raw job counts; chart displays desk-quote k jobs), and the wire's `methodology_note` disclosure verbatim — including the PRIOR-MONTH-REVISIONS caveat; (g) the lineage footer.  The decision: was the latest payrolls print a genuine surprise, how stretched is it vs the trailing two years of releases, and is the front-end (2s/5s/10s) repricing it implies justified.

### Compact Build view

The at-a-glance grid card for multi-tool prompts.  A PM reads THREE numbers + a sparkline: latest surprise (k jobs, tone-coloured), rolling z-score over 24 releases (with regime caption), and the actual print with the consensus median as subtext (so the surprise is auditable in-card).  The mini-chart shows the per-release surprise history (k jobs) with ±σ bands.  The footer carries the one-line caveat (*"Surprise vs consensus median, k jobs; z over 24 releases; as-released priors (revisions excluded)."*).  The expand arrow opens the extended view in a modal.

## 3. Why these surfaces and not others?

**Why the dual-view contract** (both extended + compact REQUIRED): per [`rendering_density.md §1.1`](../../../../../docs_revamped/03_standards/rendering_density.md) multi-tool prompts are the default reality; a tool that ships only an extended view falls back to a generic artifact-type card in multi-tool DAGs.  NFP is the macro print most directly read into the front-end Treasury curve (per the backend's PR3 placement rationale), so it naturally appears NEXT TO market reads ("NFP surprise vs 2Y yield z") — exactly the multi-tool grid the compact card serves.  The standard OVERRIDES FM4 parsimony for the dual-view mandate (§1.2).  The generic `GenericPrimitiveBuilder` + `AutoRenderer` alternative loses the actual-vs-consensus decomposition, the k-jobs desk-quote framing (the canonical wire series is raw counts), and the revisions caveat.

**Why these three compact KPIs** (latest surprise + z-score + actual-vs-consensus): the desk's "first three numbers" off a payrolls read — *"how wrong was consensus / how unusual is that / what was the print itself?"*.  Alternatives considered + rejected: OBSERVATIONS (a data-quality stat, not a desk read — extended strip); as-released prior (context, not headline — extended strip); the raw-count canonical units (semantically honest on the wire, but the desk quotes k jobs — the compact card uses the desk convention).

**Why a Chart-series toggle instead of a dual-panel chart**: the shared `MainChart` renders one series; composing two stacked MainCharts would halve the chart height and duplicate range chips.  A frontend-only `chart_series` control in the shared ControlsStrip keeps the shell composition intact and gives the z-series its natural fixed ±1.5/±2.0 regime bands when selected.

**Why Monitor is NOT claimed** (binding retraction, 2026-05-26): NFP is a monthly event read, not desk-glanceable state — per the surface contract's Monitor eligibility rule, event releases are excluded.  The earlier Stage-6 Monitor widget was a stub and was retracted under the containment principle ("a module's tiers array MUST match delivery").  This dual-view implementation does not reopen that decision.  Likewise no bespoke Ask card: the generic `AssistantResearchCard` (7 zones) renders the result without regression.

**Why a typed-detail endpoint** (`/api/v1/rates/detail/nfp-surprise`): per [`methodology_exposure.md §5`](../../../../../docs_revamped/03_standards/methodology_exposure.md) every standalone module ships its own typed bridge; the generic `/run` route stays the LLM-facing surface.

## 4. What would change the design?

- **A revisions primitive lands** (prior-month payroll revisions are explicitly NOT in this series — the as-released surprise is preserved per the `methodology_note`) → it ships as a SEPARATE module; the extended view could then offer an as-released-vs-revised overlay framing.
- **The backend unlocks another country / event_type** (today `extra='forbid'` rejects both) → that is a DIFFERENT primitive per the backend brief ("single-country single-event; if you find you need a knob, reconsider whether this is a primitive"); this module would NOT grow a selector.
- **The desk decides the per-release column/lollipop reading is mandatory** (today the shared `MainChart` line rendering of the sparse series is the accepted form) → a bar/lollipop mode is a shared-`MainChart` enhancement that backports to every event-class tool at once; tracked as a deferred consistency decision, NOT a per-tool fork.
- **Intraday surprise data ships** (per-release timestamps) → a real-time refresh affordance and a "time since release" field; would also reopen the Monitor-eligibility question since a release-day tile becomes glanceable state.

## 5. Which backend doctrine does this module operationalise?

- **FM1** (module identity) — folder name equals backend `tool_name`.
- **FM3** (surface-tier capability declaration) — claims `generic_runnable` + `custom_build_surface`.
- **FM4** (tier-set parsimony) — overridden by `rendering_density.md §1.2` for the dual-view mandate; preserved for Monitor / Ask (not claimed — see Q3 retraction note).
- **FM7** (pure-spec assembly) — `module.ts` exports a pure value.
- **FM8** (surface-file contract) — `surfaces.buildExtended` + `surfaces.buildCompact` populated; files at canonical paths.
- **FM9** (routing-claim disclosure) — `typedView: null`; this module owns its own full surfaces.
- **FM10** (THESIS discipline) — this file.
- **FM11** (round-trip test) — [`__tests__/module.spec.ts`](__tests__/module.spec.ts) asserts the dual-view contract.
- **FM12** (loader presence) — module imported in [`src/modules/index.ts`](../../index.ts).
- **P5** (honest disclosure) — the methodology card's Disclosure row threads the wire's `methodology_note` (ADR 0008 §2 P12 surprise-identity disclosure + the PRIOR-MONTH-REVISIONS caveat + TD #28b release-date-window scope limit) VERBATIM; no methodology prose is hardcoded in TSX.  The COUNT-units honesty split (canonical raw counts vs desk-quote k jobs) is disclosed on the methodology card.
- Per the **methodology-exposure standalone-bridge contract** (`methodology_exposure.md §5`): own typed-detail endpoint `/api/v1/rates/detail/nfp-surprise`; own service helper `fetchDetailNfpSurprise`; own frontend type `NfpSurpriseOutput`.
- Per the **rendering-density dual-view contract** (`rendering_density.md §1–2`): both views ship + both wired in `module.ts.surfaces` + files at canonical paths; compact view carries the 3-KPI strip, sparkline, one-line caveat, tone cues, and expand affordance per §2.2.
- Depends on **ADR 0004** (event-calendar substrate) and **ADR 0008** (economic-releases playbook — the actual/consensus_median/prior triple and the survey=false honest-absence shape).

---

## Version log

| Version | Date | Change |
|---|---|---|
| v5 | 2026-06-11 | Dual-view Build implementation per `rendering_density.md`.  Shipped `surfaces/BuildExtended.tsx`, `surfaces/BuildCompact.tsx`, and the shared helper `surfaces/nfpSurpriseShared.ts`.  Tier set updated to `[generic_runnable, custom_build_surface]`.  Standalone-bridge typed-detail endpoint at `/api/v1/rates/detail/nfp-surprise`.  Monitor / Ask retraction of v4 remains binding (event releases are not Monitor-eligible). |
| v4 | 2026-05-26 | Stage 6 — surface-contract retraction.  Dropped `monitor_surface` and `ask_surface` tier claims; deleted the stub widget + bespoke Ask card files.  Module claimed `generic_runnable` only.  Per the surface contract's containment principle: a module's tier claims must match delivery. |
| v3 | 2026-05-26 | Stage 6 — added `monitor_surface` + `ask_surface` tier claims and the bespoke surfaces.  Subsequently retracted in v4 (the shipped widget + card didn't deliver value over the defaults). |
| v2 | 2026-05-26 | Stage 4f — rewrote the body to drop stale "Stage 3 scaffold" framing. |
| v1 | 2026-05-25 | Stage 3 scaffold — runtime tier only. |
