# THESIS — `get_futures_volume_oi_tool`

> Dual-view standalone-bridge module (rendering_density.md §1 +
> methodology_exposure.md §5) with a Monitor bento tile.

**Version:** v3 (dual-view migration — consolidation plan G-3.1a)
**Module spec:** [`module.ts`](module.ts)
**Backend artifact:** `get_futures_volume_oi_tool` (generic_runnable)
**Tier set:** `[generic_runnable, custom_build_surface, monitor_surface]`
**Category:** `snapshots`
**Typed-detail endpoint:** `/api/v1/rates/detail/futures-volume-oi` (api/routes/rates/detail.py)

---

## 1. What surfaces does this module ship?

- **`generic_runnable`** — runtime-status tier, KEPT.  Backend ships the
  primitive in `_PRIMITIVE_SPECS`; the workflow bridge dispatches it
  normally and the schema-driven generic path still works wherever
  module surfaces are not mounted.
- **`custom_build_surface`** — the dual-view Build pair:
  - **`buildExtended`** — positioning canvas: identity header (contract ·
    tenor · curve family), controls (curve family / contract /
    lookback), KPI strip (open interest, ΔOI 1d, volume vs 22d mean, OI
    z-score / percentile), the dual-series chart (volume bars + OI line
    on the CONTRACT-COUNT axis with 252d high/low reference bands),
    stretch-context block, methodology card carrying the wire's
    `methodology_disclosure` verbatim, lineage footer.
  - **`buildCompact`** — KPI-centric grid card with the OI sparkline,
    contract-count caveat footer, expand affordance.
- **`monitor_surface`** — `surfaces/monitor/FuturesVolumeOiWidget.tsx`
  (`bond_futures_volume_oi`): kicker (contract · tenor), OI headline in
  whole contracts, ΔOI 1d build/unwind tone, volume-vs-22d-mean ratio,
  252d OI range strip with current marker.

All three fetch the SAME typed-detail endpoint
(rendering_density.md §1.1 / §8) through
`surfaces/futuresVolumeOiShared.ts`.

## 2. What does the user read off each surface?

**Extended.** "Is positioning building or unwinding in this contract,
and how stretched is the OI level vs its trailing year?"  The dual
series answers flow (volume) vs stock (OI); the 252d bands + z-score /
percentile answer stretch.

**Compact / Monitor.** The three-second read: current OI (contracts),
ΔOI 1d with build (mint) / unwind (coral) tone, volume vs 22d mean.

## 3. Why these surfaces and not others?

The wire is a TWO-series payload on a CONTRACT-COUNT axis — AutoRenderer
would plot it as generic series without the count-vs-notional honesty,
the build/unwind tone convention, or the 252d reference bands; hence the
bespoke Build pair.  The ΔOI morning read justifies the Monitor tile
(surface_contract.md §3.4).  No ask_surface / preview claims — the
generic cards carry those contexts fine.

## 4. Honesty rules carried by the surfaces (FP9 / P5 / P6)

- Volume and open interest are whole-CONTRACT counts, NEVER notional
  (multiply by `contract_size` / FUT_CONT_SIZE for notional) and never
  bps; `delta_open_interest_1d` is a raw subtraction.  Every axis and
  KPI is labelled in contracts.
- All derived context (252d z-score / percentile / high-low, 22d volume
  mean/max) arrives ON THE WIRE — the surfaces render it verbatim, no
  client-side statistics (FP9).
- The wire `methodology_disclosure` (ADR 0013 / P5, includes the OI
  z-score window) renders verbatim on the extended methodology card; the
  compact/monitor footers carry the shared short caveat.
- Honest absence (P6): every nullable field renders '—'; sparse early
  windows show `observation_count` rather than padding.

## 5. What would change the design?

- A whole-curve OI heatmap (all contracts at once) would be a NEW
  panel-shaped primitive, not a growth of this one.
- TimeSeriesUnits gaining a CONTRACTS member (ADR-gated) would let the
  canonical-series path carry these series; the bespoke row shape could
  then collapse.

## 6. Which backend doctrine does this module operationalise?

ADR 0013 (volume/OI substrate + conventions), P5 (methodology from
response fields), P6 (honest absence), FP9 (no client-side compute),
PR9 (volume/OI mnemonics YAML-owned — no field_name knob).
Sibling (policy_futures strip-slot analogue, P3):
`policy_futures_get_volume_open_interest_snapshot_tool`.

## Framework invariants (FM)

- **FM1** (identity) — folder name === `toolName`.
- **FM3** (tier claims) — `[generic_runnable, custom_build_surface, monitor_surface]`; runtime tier kept verbatim.
- **FM5** (display metadata + defaults) — `displayName` / `category` / `oneLineSummary` / `defaultParams`.
- **FM5c** (monitor widget) — `bond_futures_volume_oi`, parameterised (curve_family, contract_code).
- **FM6** (unsupported-reason gating) — N/A (generic_runnable; no reason field).
- **FM7** (pure-spec assembly) — `module.ts` exports a pure value.
- **FM8** (dual Build surfaces) — `build` === `buildExtended`; `buildCompact` shipped.
- **FM9** (standalone bridge) — `typedView: null`, `richModel: false`.
- **FM10** (THESIS discipline) — this file.
- **FM11** (round-trip test) — `__tests__/module.spec.ts` calls `assertStandardModuleInvariants`.
- **FM12** (loader presence) — module imported in `src/modules/index.ts`.

---

## One-line summary

Daily traded volume + end-of-day open interest for ONE rolling-generic bond-futures contract — current counts, 1-day OI change, 252d OI z-score / percentile / high-low range, and 22d rolling volume context.  Whole-CONTRACT counts (NOT notional); pure-INGEST read per ADR 0013.

---

## Version log

| Version | Date | Change |
|---|---|---|
| v3 | 2026-06-12 | Dual-view migration (G-3.1a) — buildExtended/buildCompact over /detail/futures-volume-oi + Monitor tile (bond_futures_volume_oi); runtime tier kept verbatim. |
| v2 | 2026-05-26 | Stage 4f — rewrote the body to drop stale "Stage 3 scaffold" framing. |
| v1 | 2026-05-25 | Stage 3 scaffold — runtime tier only, no surfaces. |
