# THESIS — `scan_ois_extremes_tool`

> Module shipped with runtime tier only (Stage 3 / Stage 4c-derived).  Capability surfaces are added by Stages 5+ as the desk earns them.

**Version:** v2 (Stage 4f — post-runner-fix rewrite)
**Module spec:** [`module.ts`](module.ts)
**Backend artifact:** `scan_ois_extremes_tool` (paused)
**Tier set:** `[paused]`
**Category:** `screening`

---

## 1. What surfaces does this module ship?

- **`paused`** — runtime-status tier.  Backend declares this primitive in the manifest YAML but has no compute implementation today.  Build renders the paused card with `MODULE.unsupportedReason` explaining what's missing + the closest available alternative.

## 2. What does the user read off each surface?

**Build (paused card).** `UnsupportedKnownToolCanvas` renders the paused card with the per-tool reason + 'what works now' hint pointing the user at the closest alternative.

## 3. Why these surfaces and not others?

Backend hasn't shipped the compute implementation yet — the folder exists so future surfaces have a destination, but today there's nothing to run.

## 4. What would change the design?

- Backend ships the implementation → promote runtime tier to `generic_runnable`.

## 5. Which backend doctrine does this module operationalise?

- **FM1** (module identity) — folder name equals backend `tool_name` exactly.
- **FM3** (surface-tier capability declaration) — claims `paused`; no capability tiers.
- **FM7** (pure-spec assembly) — `module.ts` exports a pure value.
- **FM6** (unsupported-reason gating) — `unsupportedReason` populated per the runtime tier.
- **FM10** (THESIS discipline) — this file.
- **FM11** (round-trip test) — `__tests__/module.spec.ts` calls `assertStandardModuleInvariants`.
- **FM12** (loader presence) — module imported in `src/modules/index.ts`.

---

## One-line summary

Scans every OIS instrument in the database, ranks the top-N by absolute 252-day z-score and returns rate, daily change, z-score, percentile, and signal label per row.  OIS analogue of scan_extremes.

---

## Version log

| Version | Date | Change |
|---|---|---|
| v2 | 2026-05-26 | Stage 4f — rewrote the body to drop stale "Stage 3 scaffold" framing and answer Q2–Q5 for real. |
| v1 | 2026-05-25 | Stage 3 scaffold — runtime tier only, no surfaces. |
