# THESIS — `calculate_cpi_surprise_tool`

> Event-class primitive brought under the dual-view rendering-density contract.  Ships an extended Build view (full canvas for single-tool queries) AND a compact Build view (grid card for multi-tool query DAGs) — both REQUIRED per [`rendering_density.md`](../../../../../docs_revamped/03_standards/rendering_density.md) §1 — over its own standalone typed-detail bridge.  The Monitor / Ask retraction of 2026-05-26 remains binding: event releases are not desk-glanceable state.

**Version:** v5 (dual-view Build implementation)
**Last reviewed:** 2026-06-11
**Module spec:** [`module.ts`](module.ts)
**Backend artifact:** `calculate_cpi_surprise_tool` (generic_runnable; standalone bridge via `/api/v1/rates/detail/cpi-surprise`)
**Tier set:** `[generic_runnable, custom_build_surface]`
**Category:** `economic_release_surprises`
**Mockups:** `./mockups/Compact.png` + `./mockups/Extended.png` — captured from the rendered polished surfaces by the integrator per the mockup-first workflow.

---

## 1. What surfaces does this module ship?

- **`generic_runnable`** — runtime-status tier; backend ships in `_PRIMITIVE_SPECS`.
- **`custom_build_surface`** — the dual-view contract:
  - **Extended Build view** ([`surfaces/BuildExtended.tsx`](surfaces/BuildExtended.tsx)) — full canvas mounted for single-tool queries.  Controls strip (Country US/UK/JP/EU + Lookback in releases + a frontend-only Chart-series toggle), top-right Z-Score / Latest-Print / Event cards, a six-cell KPI strip (surprise, z, actual, consensus median, prior, releases shown), the per-release surprise series chart (toggleable to the rolling z-score series), stretch-context panel, methodology card threading the wire's `methodology_note`, lineage footer.
  - **Compact Build view** ([`surfaces/BuildCompact.tsx`](surfaces/BuildCompact.tsx)) — grid card mounted as a node body inside multi-tool query DAGs (e.g. *"compare the US and EU CPI surprise histories"*, *"CPI surprise vs 2Y yield z-score"*).  Headline 3-KPI strip, surprise-series sparkline with ±σ bands, the consensus-median caveat footer, click-to-expand affordance.

## 2. What does the user read off each surface?

### Extended Build view

The full investigation canvas for ONE country's CPI-surprise release series.  A PM reads, in order: (a) the title row identifying the country + event (`US CPI YoY · Surprise`), the latest release date and reference period; (b) the top-right cards — the rolling z-score of the latest surprise (with Normal / Elevated / Extreme regime), the latest print (actual vs consensus median, with period), and the EVENT card carrying the consensus-median caveat; (c) the KPI strip — latest surprise (pp, tone-coloured: hot print = coral per the fixed-income "yields up" read), z-score, actual, consensus median, prior, and the realised-release count; (d) the per-release surprise chart (sparse ~monthly series with ±2σ/±1.5σ bands over the displayed window), toggleable to the rolling z-score series with fixed ±1.5/±2.0 regime bands; (e) the stretch-context panel answering "is this print a one-off shock or part of a directional drift?"; (f) the methodology card — surprise identity (wire-frozen `actual − consensus_median`), event_type resolution (cpi_yoy / hicp_yoy), the releases-not-days z-window, the display-vs-z-window separation, and the wire's `methodology_note` disclosure verbatim; (g) the lineage footer.  The decision: was the latest print a genuine surprise, how stretched is it vs the trailing two years of releases, and has consensus been systematically wrong in one direction.

### Compact Build view

The at-a-glance grid card for multi-tool prompts.  A PM reads THREE numbers + a sparkline: latest surprise (pp, tone-coloured), rolling z-score over 24 releases (with regime caption), and the actual print with the consensus median as subtext (so the surprise is auditable in-card).  The mini-chart shows the per-release surprise history with ±σ bands.  The footer carries the one-line caveat (*"Surprise vs consensus median, pct-pts of YoY CPI; z over 24 releases."*).  The expand arrow opens the extended view in a modal.

## 3. Why these surfaces and not others?

**Why the dual-view contract** (both extended + compact REQUIRED): per [`rendering_density.md §1.1`](../../../../../docs_revamped/03_standards/rendering_density.md) multi-tool prompts are the default reality; a tool that ships only an extended view falls back to a generic artifact-type card in multi-tool DAGs.  CPI surprises are naturally compared cross-country ("US vs EU CPI surprise") and against market reads ("CPI surprise vs breakeven move"), so the compact card earns its keep immediately.  The standard OVERRIDES FM4 parsimony for the dual-view mandate (§1.2).  The generic `GenericPrimitiveBuilder` + `AutoRenderer` alternative renders the per-release series as an undifferentiated time series — it loses the actual-vs-consensus decomposition, the releases-not-days z-window framing, and the consensus-median caveat that make the surprise readable.

**Why these three compact KPIs** (latest surprise + z-score + actual-vs-consensus): they are the desk's "first three numbers" off a release read — *"how wrong was consensus / how unusual is that / what was the print itself?"*.  Alternatives considered + rejected: OBSERVATIONS (a data-quality stat, not a desk read — it lives in the extended KPI strip); prior (context for the print, not headline — extended strip); period label (carried as identity subtitle, not a number).

**Why a Chart-series toggle instead of a dual-panel chart**: the shared `MainChart` renders one series; composing two stacked MainCharts would halve the chart height and duplicate range chips.  A frontend-only `chart_series` control in the shared ControlsStrip keeps the shell composition intact and gives the z-series its natural fixed ±1.5/±2.0 regime bands when selected.

**Why Monitor is NOT claimed** (binding retraction, 2026-05-26): CPI is a monthly event read, not desk-glanceable state — per the surface contract's Monitor eligibility rule, event releases are excluded.  The earlier Stage-5 Monitor widget was a stub and was retracted under the containment principle ("a module's tiers array MUST match delivery").  This dual-view implementation does not reopen that decision.  Likewise no bespoke Ask card: the generic `AssistantResearchCard` (7 zones) renders the result without regression.

**Why a typed-detail endpoint** (`/api/v1/rates/detail/cpi-surprise`): per [`methodology_exposure.md §5`](../../../../../docs_revamped/03_standards/methodology_exposure.md) every standalone module ships its own typed bridge; the generic `/run` route stays the LLM-facing surface.

## 4. What would change the design?

- **A revisions primitive lands** (pre-release revisions of PRIOR surprises — explicitly out of this wire per the `methodology_note`) → it ships as a SEPARATE module; the extended view could then offer an as-released-vs-revised overlay framing.
- **A new country joins the YAML-locked set** (a new `cpi_event_type_for_<country>` convention) → add it to `COUNTRY_REGISTRY` + `COUNTRY_OPTIONS` in [`surfaces/cpiSurpriseShared.ts`](surfaces/cpiSurpriseShared.ts).  No shell changes.
- **Core / supercore CPI event types land** in the event calendar → the country selector grows an event dimension; the single-select control becomes two, and the THESIS Q2 read is rewritten around the headline-vs-core spread.
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
- **P5** (honest disclosure) — the methodology card's Disclosure row threads the wire's `methodology_note` (ADR 0008 §2 P12 surprise-identity disclosure + TD #28b release-date-window scope limit + the prior-revisions exclusion) VERBATIM; no methodology prose is hardcoded in TSX.
- Per the **methodology-exposure standalone-bridge contract** (`methodology_exposure.md §5`): own typed-detail endpoint `/api/v1/rates/detail/cpi-surprise`; own service helper `fetchDetailCpiSurprise`; own frontend type `CpiSurpriseOutput`.
- Per the **rendering-density dual-view contract** (`rendering_density.md §1–2`): both views ship + both wired in `module.ts.surfaces` + files at canonical paths; compact view carries the 3-KPI strip, sparkline, one-line caveat, tone cues, and expand affordance per §2.2.
- Depends on **ADR 0004** (event-calendar substrate) and **ADR 0008** (economic-releases playbook — the actual/consensus_median/prior triple and the survey=false honest-absence shape).

---

## Version log

| Version | Date | Change |
|---|---|---|
| v5 | 2026-06-11 | Dual-view Build implementation per `rendering_density.md`.  Shipped `surfaces/BuildExtended.tsx`, `surfaces/BuildCompact.tsx`, and the shared helper `surfaces/cpiSurpriseShared.ts`.  Tier set updated to `[generic_runnable, custom_build_surface]`.  Standalone-bridge typed-detail endpoint at `/api/v1/rates/detail/cpi-surprise`.  Monitor / Ask retraction of v4 remains binding (event releases are not Monitor-eligible). |
| v4 | 2026-05-26 | Stage 6 — surface-contract retraction.  Dropped `monitor_surface` and `ask_surface` tier claims; deleted the stub widget + bespoke Ask card files.  Module claimed `generic_runnable` only.  Per the surface contract's containment principle: a module's tier claims must match delivery. |
| v3 | 2026-05-26 | Stage 5 — added `monitor_surface` + `ask_surface` tier claims and the bespoke surfaces.  Subsequently retracted in v4 (the shipped widget + card didn't deliver value over the defaults). |
| v2 | 2026-05-26 | Stage 4f — rewrote the body to drop stale "Stage 3 scaffold" framing. |
| v1 | 2026-05-25 | Stage 3 scaffold — runtime tier only. |
