# ADR 0005 — Cash-bond playbook: the two-playbook split and CUSIP universe enumeration

**Status:** Accepted
**Date:** 2026-05-20
**Builds on:** ADR 0003 (cash-bond substrate — `cusip`/`isin` typed columns, `otr_history`, the `sovereign_cash_bond` instrument_type).
**Operationalises principles:** P1 (built right — a verified curated universe, no premature dynamic-enumeration abstraction), P2 (accuracy — only Bloomberg-verified mnemonics ship), P4 (determinism — a curated list is replayable; a live screen query is not), P5 (honest disclosure — the verification findings that forced this design are recorded here), P8 (closed-family — `asset_class` stays `rates`), P12 (Bloomberg Accuracy Boundary — risk/ASW are ingested, never recomputed).
**Scope:** Work order A4, the cash-bond DATA layer. This ADR fixes the universe-enumeration decision and the `sovereign_bonds.yml` / `sovereign_cash_bonds.yml` split. It does NOT write the playbook, run extraction, or ingest data — that is the A4-4 build that follows.

---

## Context

A4 set out to add bond-level risk fields, the asset-swap spread (ASW), and FWCV
forward/carry to the sovereign data layer. Two operator-run Bloomberg
verification scripts established, empirically, what is and is not possible:

- **`sovereign_bonds_risk_bloomberg_check.py`** — risk/ASW/FWCV mnemonics
  probed via `bdh` on the `GT.. Govt` benchmark generics. Only `YLD_YTM_BID`
  and `YLD_YTM_ASK` returned data. Modified duration, DV01, accrued interest,
  YAS yield and every ASW candidate returned 0 points, no error. FWCV returned
  nothing via `bdh` *or* `bdp`.

- **`sovereign_bonds_realbond_bloomberg_check.py`** — each generic resolved to
  its current underlying cash bond, and the same mnemonics re-tested. Findings:
  - The `GT.. Govt` generics are **yield-curve tickers**. Via `bdh` they serve
    `YLD_YTM_*` only. `PX_DIRTY_MID` on a generic via `bdh` returns the *yield*,
    not a price — the first run's "PX_DIRTY_MID 14/14 pass" was a false pass.
  - Each generic resolves cleanly to its current underlying bond via `bdp`
    (`ID_ISIN` / `ID_CUSIP` / `SECURITY_DES` …). The `/cusip/<CUSIP>` query
    form works for `bdh`.
  - On the **real bond** ticker, `bdh` delivers a clean daily series for
    `YLD_YTM_MID`, `RISK_MID` (DV01), `PX_DIRTY_MID`, `PX_CLEAN_MID` and
    `ASSET_SWAP_SPD_MID`. It does NOT historise `DUR_ADJ_MID`,
    `YAS_BOND_YLD`, `YAS_RISK` or `INT_ACC` (those are `bdp`-snapshot only).
  - `MOD_DUR_MID` and `ASW_SPREAD` are not real Bloomberg fields; the correct
    mnemonics are `DUR_ADJ_MID` and `ASSET_SWAP_SPD_MID`.
  - `PX_LAST` is yield-quoted for JGBs and ACGBs — not a reliable price field;
    `PX_CLEAN_MID` / `PX_DIRTY_MID` are price-consistent across all markets.

The conclusion the verification forces: **risk and ASW data cannot ride
`sovereign_bonds.yml`.** They are per-bond fields that require individual,
CUSIP-identified bond tickers — which is exactly what ADR 0003's `otr_history`
foreign key (`otr_instrument_id → instrument_master`) already presupposes.

## Decision

A **hybrid two-playbook design**:

1. **`sovereign_bonds.yml` keeps its role unchanged** — GT-generic benchmark
   *yield-curve points*. A4-1 adds `YLD_YTM_BID` / `YLD_YTM_ASK` (verified
   14/14); nothing else. Risk, ASW and FWCV do NOT go here.

2. **A new `sovereign_cash_bonds.yml` — CUSIP-only.** One universe row per
   individual cash sovereign bond, each stamped `instrument_type:
   sovereign_cash_bond` (ADR 0003). Its `target_metrics` are the verified
   real-bond `bdh` fields: `YLD_YTM_MID`, `RISK_MID` (DV01), `PX_DIRTY_MID`,
   `PX_CLEAN_MID`, `ASSET_SWAP_SPD_MID`. Per P12 all are ingested, never
   recomputed. A historised modified-duration field is pending a follow-up
   probe (`a4_duration_field_probe.py`); if none exists, duration is resolved
   separately and is not blocked on by this ADR.

3. **The v1 universe is a curated CUSIP snapshot, seeded by the current
   on-the-run bonds.** Each `(country, tenor)` slot's current OTR bond is
   resolved from the `sovereign_bonds.yml` generic via the `bdp` ID-field
   resolution the realbond run demonstrated. The 14 already-resolved OTR
   bonds are the proven seed; the remaining slots resolve identically. The
   build starts US, then expands by country.

4. **Bloomberg FI / bond-search is the FUTURE refresh and enumeration
   mechanism — explicitly not the v1 implementation.** v1 is a hand-curated,
   verified, deterministic list. Dynamic screening is a later enhancement
   layered on once the curated path is proven in production.

5. **OTR history.** The current OTR bond per slot populates `otr_history`'s
   current (open) window. Historical OTR backfill — the past sequence of
   on-the-run bonds per slot — is a bounded follow-up (it is its own
   enumeration problem and is not required for the first A4-4 load).

### What this ADR does NOT do

- Does not write `sovereign_cash_bonds.yml` or run any extraction/ingestion.
- Does not commit any mnemonic beyond the five verified by the realbond run.
- Does not build Bloomberg bond-search — that is a future refresh mechanism.
- Does not decide FWCV (A4-3) — FWCV failed `bdp` as well as `bdh`; it is a
  separate Bloomberg access-pattern investigation, not part of A4-4.
- Does not touch schema or Alembic — A4-4 is pure playbook + data work on the
  ADR 0003 substrate.

## Alternatives considered

**Pure generics (no CUSIP playbook).** Rejected — the verification proved GT
generics carry no per-bond analytics via `bdh`, and `otr_history`'s
`otr_instrument_id` FK must point at individual-CUSIP `instrument_master`
rows. Generics cannot serve either need.

**Pure dynamic Bloomberg bond-search as v1.** Rejected as the *first*
implementation — it is over-built for the starting point, and a live screen
query is non-deterministic (P4 — two runs can enumerate different universes).
A verified curated list is simpler, replayable, and sufficient to ship. Bond
search is retained as the refresh mechanism, not the foundation.

**One playbook holding both generics and CUSIP bonds.** Rejected — it would
mix two instrument kinds with different `instrument_type`, different working
mnemonics (yield-only vs the five-field set), and different refresh cadences
(a generic never goes stale; a CUSIP list does). Two playbooks keep each
contract clean (P3).

## Design

- **`sovereign_cash_bonds.yml`** — standard `--mode time-series` playbook.
  `target_metrics`: the five verified fields. `reference_metrics`: identity +
  static fields (`SECURITY_DES`, `MATURITY`, coupon, `ID_ISIN`, `ID_CUSIP`,
  issue date). The ingester (ADR 0003) already routes `cusip`/`isin` to the
  typed columns; coupon/issue-date/outstanding land in `attributes`.
- **Universe construction** — resolve each `sovereign_bonds.yml` generic to its
  current underlying bond via `bdp`; the resolved CUSIP/ISIN becomes one
  `sovereign_cash_bonds.yml` row. The realbond verification already produced
  this mapping for 14 slots; the rest follow the same step.
- **OTR population** — each resolved current OTR bond opens an `otr_history`
  window (`effective_to = NULL`) for its `(country, tenor)` slot via
  `upsert_otr_history`, with the A4 OTR validator (overlaps + gaps) per ADR
  0003's rollout plan.
- **Refresh** — the curated universe is re-resolved on a cadence (a new bond
  becomes on-the-run after each auction); the refresh closes the prior OTR
  window and opens the new one. Automating that resolution via Bloomberg
  bond-search is the future enhancement noted above.

## Consequences

**Positive:**
- Risk + ASW finally have a home where the data actually exists; the five
  mnemonics are verified, not guessed.
- Clean contract separation — yield curve vs identified bonds.
- The v1 universe is deterministic and replayable (P4).
- `otr_history` gets its first real population path.

**Negative / known trade-offs:**
- The curated CUSIP list goes stale as bonds mature and auctions create new
  OTR bonds — it needs a refresh cadence. Accepted: refresh is cheap (re-run
  the resolution); full automation is the deferred bond-search enhancement.
- v1 is a current-OTR snapshot — deep historical OTR coverage is a follow-up.
- Modified duration is not yet a confirmed `bdh` field — pending the duration
  probe; it does not block the other four fields.

## Rollout plan

1. **This ADR.**
2. **Duration probe** (`a4_duration_field_probe.py`) — resolve whether a
   historised modified-duration field exists; fold the result into A4-4.
3. **A4-4 build** — write `sovereign_cash_bonds.yml` (resolve the full current
   OTR set, US first), verify, extract, ingest, populate `otr_history` with
   the overlap+gap validator.
4. **Future** — Bloomberg bond-search as the refresh/enumeration automation;
   historical OTR backfill.

## Version log

| Version | Date | Change |
|---|---|---|
| v1 | 2026-05-20 | Initial decision. Accepted. |
