# THESIS — `get_otr_history_tool`

> Module shipped with runtime tier only (Stage 3 / Stage 4c-derived).  Capability surfaces are added by Stages 5+ as the desk earns them.

**Version:** v2 (Stage 4f — post-runner-fix rewrite)
**Module spec:** [`module.ts`](module.ts)
**Backend artifact:** `get_otr_history_tool` (workflow_incompatible)
**Tier set:** `[workflow_incompatible]`
**Category:** `snapshots`

---

## 1. What surfaces does this module ship?

- **`workflow_incompatible`** — runtime-status tier.  Backend ships this primitive in `WORKFLOW_INCOMPATIBLE_TOOLS`: the tool is callable via MCP but its output shape isn't workflow-bridge compatible (no Series / Panel artifact).  The frontend surfaces the honest `unsupported_known` card with the per-tool reason copy from `MODULE.unsupportedReason`.

## 2. What does the user read off each surface?

**Build (workflow-incompatible card).** `UnsupportedKnownToolCanvas` renders the honest unsupported card with the per-tool reason copy.  Ask handoff still works (the tool ships via MCP).

## 3. Why these surfaces and not others?

Output shape isn't bridge-compatible (categorical labels, per-meeting snapshots, or similar non-Series/Panel shape).  Until the bridge gains support for the relevant artifact type, the typed-detail endpoint route is the only live surface.

## 4. What would change the design?

- Backend artifact-bridge support → revisit runtime tier (could become `generic_runnable`).
- Bespoke typed view for the workflow-incompatible payload → claim `custom_build_surface`.

## 5. Which backend doctrine does this module operationalise?

- **FM1** (module identity) — folder name equals backend `tool_name` exactly.
- **FM3** (surface-tier capability declaration) — claims `workflow_incompatible`; no capability tiers.
- **FM7** (pure-spec assembly) — `module.ts` exports a pure value.
- **FM6** (unsupported-reason gating) — `unsupportedReason` populated per the runtime tier.
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
| v2 | 2026-05-26 | Stage 4f — rewrote the body to drop stale "Stage 3 scaffold" framing and answer Q2–Q5 for real. |
| v1 | 2026-05-25 | Stage 3 scaffold — runtime tier only, no surfaces. |
