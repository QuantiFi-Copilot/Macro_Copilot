# THESIS — `policy_futures_get_volume_open_interest_snapshot_tool`

> Dual-view standalone-bridge module (rendering_density.md §1 +
> methodology_exposure.md §5).  Cell-for-cell mirror of the bond_futures
> volume/OI sibling (P3 — same Pydantic Output class on the wire).

**Version:** v3 (dual-view migration — consolidation plan G-3.1a)
**Module spec:** [`module.ts`](module.ts)
**Backend artifact:** `policy_futures_get_volume_open_interest_snapshot_tool` (generic_runnable)
**Tier set:** `[generic_runnable, custom_build_surface]`
**Category:** `snapshots`
**Typed-detail endpoint:** `/api/v1/rates/detail/policy-futures-voi-snapshot` (api/routes/rates/detail.py)

---

## 1. What surfaces does this module ship?

- **`generic_runnable`** — runtime-status tier, KEPT.  Backend ships the
  primitive in `_PRIMITIVE_SPECS`; the workflow bridge dispatches it
  normally and the schema-driven generic path still works wherever
  module surfaces are not mounted.
- **`custom_build_surface`** — the dual-view Build pair:
  - **`buildExtended`** — positioning canvas keyed by (curve_family,
    strip_position): identity header (strip-slot stem · family ·
    whites/reds segment), controls (family / strip position / lookback),
    10-cell KPI strip on the CONTRACT-COUNT axis, the open-interest main
    chart with ±σ reference bands, OI stretch context, top-right cards
    (OI z-score / OI percentile / front underlying), methodology card
    carrying the wire's `methodology_disclosure` verbatim, lineage
    footer.
  - **`buildCompact`** — shell-standard grid card: 3 KPIs (OPEN INTEREST
    / ΔOI 1D / OI Z-SCORE 252D), the OI sparkline, contract-count caveat
    footer, expand affordance.

Both fetch the SAME typed-detail endpoint (rendering_density.md §1.1)
through `surfaces/policyFuturesVoiShared.ts`.

## 2. What does the user read off each surface?

**Extended.** "Is positioning building or unwinding in this strip slot,
and how stretched is the OI level vs its trailing year?"  Flow (volume)
vs stock (OI), with the 252d z-score / percentile / range answering
stretch and the front-underlying card pinning down WHICH contract the
slot currently resolves to.

**Compact.** The three-second read: current OI (contracts), ΔOI 1d with
build (mint) / unwind (coral) tone, OI z-score regime.

## 3. Why these surfaces and not others?

The wire is a TWO-series payload on a CONTRACT-COUNT axis — AutoRenderer
would plot generic series without the count-vs-notional honesty, the
build/unwind tone convention, or the reference bands; hence the bespoke
Build pair (same reasoning as the bond-futures sibling).

**No monitor tier** (deliberate asymmetry vs the sibling): the
policy-futures morning read is the strip-snapshot PANEL
(`policy_futures_get_futures_strip_snapshot_tool`) — a whole-strip
implied-rate view.  A per-slot OI tile would duplicate that glance at
lower information density; the slot-level OI read is an investigation
surface, not a morning scan (surface_contract.md §3.4 glanceability
test fails on redundancy, not feasibility).

KPI choices mirror the sibling cell-for-cell (P3): OPEN INTEREST /
ΔOI 1D / OI Z-SCORE compact triple; volume context (level, vs-22d-mean,
22d max) extended-only.

## 4. Honesty rules carried by the surfaces (FP9 / P5 / P6)

- Volume and open interest are whole-CONTRACT counts, NEVER notional
  (multiply by `contract_size` for notional) and never bps;
  `delta_open_interest_1d` is a raw subtraction.  Every axis and KPI is
  labelled in contracts.
- All derived context (252d z / percentile / high-low, 22d volume
  mean/max) arrives ON THE WIRE — surfaces render it verbatim (FP9).
- The strip-SLOT series mixes underlying contracts across quarterly
  rolls — roll-window OI moves are mechanical, not positioning; the
  stretch-context interpretation and the compact caveat both say so.
- The wire `methodology_disclosure` (ADR 0013 / P5) renders verbatim on
  the extended methodology card.
- Honest absence (P6): every nullable field renders '—'; sparse windows
  show `observation_count`.

## 5. What would change the design?

- A whole-strip OI heatmap (all 8 slots at once) would be a NEW
  panel-shaped primitive, not a growth of this one.
- TimeSeriesUnits gaining a CONTRACTS member (ADR-gated) would let the
  canonical-series path carry these series.

## 6. Which backend doctrine does this module operationalise?

ADR 0013 (volume/OI conventions), P5 (methodology from response
fields), P6 (honest absence), FP9 (no client-side compute), PR9
(volume/OI mnemonics YAML-owned — no field_name knob).
Siblings: `get_futures_volume_oi_tool` (bond-futures analogue, P3
mirror), `policy_futures_get_futures_strip_snapshot_tool` (identity
vocabulary source).

## Framework invariants (FM)

- **FM1** (identity) — folder name === `toolName`.
- **FM3** (tier claims) — `[generic_runnable, custom_build_surface]`; runtime tier kept verbatim.
- **FM5** (display metadata + defaults) — `displayName` / `category` / `oneLineSummary` / `defaultParams`.
- **FM6** (unsupported-reason gating) — N/A (generic_runnable; no reason field).
- **FM7** (pure-spec assembly) — `module.ts` exports a pure value.
- **FM8** (dual Build surfaces) — `build` === `buildExtended`; `buildCompact` shipped.
- **FM9** (standalone bridge) — `typedView: null`, `richModel: false`.
- **FM10** (THESIS discipline) — this file.
- **FM11** (round-trip test) — `__tests__/module.spec.ts` calls `assertStandardModuleInvariants`.
- **FM12** (loader presence) — module imported in `src/modules/index.ts`.

---

## One-line summary

Daily traded volume + end-of-day open interest for ONE STIR strip slot keyed by (curve_family, strip_position) — current counts, 1-day OI change, 252d OI z-score / percentile / high-low range, and 22d rolling volume context.  Whole-CONTRACT counts (NOT notional); pure-INGEST read per ADR 0013.

---

## Version log

| Version | Date | Change |
|---|---|---|
| v3 | 2026-06-12 | Dual-view migration (G-3.1a) — buildExtended/buildCompact over /detail/policy-futures-voi-snapshot, mirroring the bond-futures sibling cell-for-cell; runtime tier kept verbatim; no monitor tier (strip-snapshot panel owns the morning read). |
| v2 | 2026-05-26 | Stage 4f — rewrote the body to drop stale "Stage 3 scaffold" framing. |
| v1 | 2026-05-25 | Stage 3 scaffold — runtime tier only, no surfaces. |
