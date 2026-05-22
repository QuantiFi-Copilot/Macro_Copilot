# ADR 0010 — Repo-financing substrate: time-series storage, Bloomberg-only sourcing, an `otr_history`-bounded specials universe

**Status:** Proposed
**Date:** 2026-05-22
**Builds on:** the time-series substrate (`macro_data.market_data_daily` — the TimescaleDB hypertable; `--mode time-series` on the extractors; the `data/` ingester route); ADR 0003 (cash-bond substrate — the `cusip` / `isin` identity columns on `instrument_master`); ADR 0007 (the OTR resolver — `macro_data.otr_history`, the per-`(country, tenor)` on-the-run record that bounds the C2c specials universe).
**Operationalises principles:** P1 (built right — C1 does not speculatively build a table, extractor mode, or ingester route that repo does not need), P2 (accuracy or refuse — repo is sourced from Bloomberg where Bloomberg verifies it, and anything that would need a non-Bloomberg vendor is deferred and documented, never guessed), P5 (honest disclosure — the vendor limitation and the bounded specials universe are recorded here, up front), P7 (source-of-record boundary — a non-Bloomberg repo source would be a *second L1 adapter*; this ADR explicitly does NOT build one), P12 (Bloomberg Accuracy Boundary — repo rates are ingested verbatim, never recomputed).
**Scope:** Track C, work order **C1** — the substrate decision for repo-financing data. C1 is substrate-only: an ADR, no Bloomberg, no playbooks, no data. The actual repo ingestion is **C2** (three sub-phases — C2a/C2b/C2c), each its own data PR.

---

## Context

Track C is the final step of the data-first roadmap. Its first half is repo-financing data, which comes in three shapes — all fundamentally **time-series** (a rate per day):

- **(a) Overnight RFR fixings** — SOFR, SONIA, ESTR, TONA effective/compounded overnight rates. Single index tickers, daily. Cleanly on Bloomberg.
- **(b) Term GC repo curves** — general-collateral repo rates at ON / 1W / 1M / 3M per market. A curve, daily — structurally identical to `ois.yml`. Partial Bloomberg coverage.
- **(c) CUSIP-level repo specials** — the repo rate for a specific bond, per CUSIP per day. The hardest shape; most likely to need a non-Bloomberg source.

**The desk demand** (`docs/technical_debt.md` #24; `tmp/primitive_expansion/phase4.md`): repo data closes the two `NotImplementedError` methods on the existing `financing_rate` primitive — `term_repo_curve` (fed by C2b) and `gc_special_blend` (fed by C2c) — upgrades `financing_rate.overnight_index_proxy` to a true overnight RFR (fed by C2a), and is a prerequisite for the bond-futures RV stack (`implied_repo_rate`). **C1/C2 build the data only — none of those primitives.**

The roadmap (Step 5 / open question Q13) names an unresolved sourcing question — Bloomberg vs DTCC GCF vs ICAP — that must be settled before repo ingestion. This ADR settles it, and the storage shape, the `instrument_type` values, and the specials-universe scoping.

## Decision

### 1. Bloomberg-only sourcing; non-Bloomberg repo is deferred, not adopted (P7, P2)

The project's L1 adapter is **Bloomberg-only** — P7 confines vendor SDKs to the L1 adapter, and there is exactly one. Sourcing repo from **DTCC GCF or ICAP would mean building a second L1 adapter** — a major, P7-governed architectural workstream far beyond a Track-C playbook. Track C does **not** build one.

Instead: **C2 ingests whatever repo data Bloomberg verifies.** Any shape that genuinely needs a non-Bloomberg vendor is **deferred and documented** — the project's established documented-deferral discipline (the precedent set by D-auctions, FWCV, and the inflation interpolation convention). Expected coverage, to be confirmed empirically by C2's verification rounds: C2a (overnight RFR) clean on Bloomberg; C2b (term GC) partial; C2c (CUSIP specials) the most at risk of a documented deferral.

### 2. Repo is time-series — it lands in `market_data_daily`. No new table, mode, or route.

All three repo shapes are a daily rate keyed `(trade_date, instrument_id, field_name)` — exactly the `market_data_daily` row. `market_data_daily` is the TimescaleDB hypertable already carrying every time series; `field_name` is a `VARCHAR`, so new repo mnemonics need **no schema change**. Therefore repo rides the **existing rails unchanged**:

- registered in `instrument_master` like any other instrument;
- extracted by the existing **`--mode time-series`** flow (`bdh`/`bdp` → long-format parquet);
- uploaded to the existing **`data/<dataset>/`** GCS prefix;
- ingested by the existing **`data/` route** into `market_data_daily`.

**C1 adds no table, no extractor mode, no ingester route, no DB helper.** A dedicated repo table was considered and rejected (see Alternatives). This decision is revisited **only if** a C2 verification round surfaces repo data with genuinely non-time-series structure that a daily `(date, instrument, field, value)` row cannot represent — not expected for a rate series.

### 3. Three `instrument_type` values — `overnight_rfr`, `gc_repo`, `repo_special`

Repo instruments are registered in `instrument_master` under three new `instrument_type` values, one per shape:

| Shape | `instrument_type` | Registered by |
|---|---|---|
| Overnight RFR fixing | `overnight_rfr` | C2a |
| Term GC repo curve point | `gc_repo` | C2b |
| CUSIP-level repo special | `repo_special` | C2c |

The three are genuinely distinct instrument kinds — an index fixing, a term GC curve point, and a per-bond rate — and fine-grained types let the `financing_rate` primitive filter cleanly. This matches the project's specific `instrument_type` convention (`sovereign_cash_bond`, `wirp_meeting`, `ois_swap`, `inflation_swap`, …). `instrument_type` is a free `VARCHAR` — **registering these values needs no schema change.**

### 4. The C2c CUSIP-specials universe is bounded to `otr_history`

Repo specials are per-CUSIP, and the cash-bond universe is large and dynamic — the same hard problem A4 faced for cash bonds. C2c's universe is **not "every bond."** It is bounded to the **on-the-run + recently-off-the-run bonds per `(country, tenor)`** that `macro_data.otr_history` (ADR 0007) already tracks — a desk-relevant, already-maintained set. If clean CUSIP-specials data is not available from Bloomberg for even that bounded universe, **C2c is deferred and documented** rather than shipped partial or guessed.

## What this ADR does NOT do

- **No new table.** Repo is time-series — it uses `market_data_daily` (Decision 2).
- **No new extractor mode.** Repo uses the existing `--mode time-series`.
- **No new ingester route.** Repo uses the existing `data/` route.
- **No new DB helper.** Repo uses `upsert_market_data_daily` / `upsert_instrument_master`.
- **No `schema.sql` change.** The `instrument_type` values are free `VARCHAR`; they are documented here, not enumerated in the schema.
- **No second L1 adapter.** Bloomberg-only (Decision 1).
- **No Bloomberg work, no playbooks, no data.** Those are C2 — three separate data PRs (C2a/C2b/C2c).
- **No Phase-4 primitive work** (`financing_rate` method closures, `repo_spread_to_gc`, the bond-futures RV stack). Track C is data only.

## Alternatives considered

- **A dedicated `repo_rates` table.** Rejected: repo is a daily rate series with no structure `market_data_daily` cannot carry. A separate table would duplicate the hypertable, the ingester route, and the `v_market_data_daily_enriched` overlay — cost, no benefit. P1: do not build infrastructure the data does not need. (Revisit condition recorded in Decision 2.)
- **A second L1 adapter (DTCC GCF / ICAP) for repo.** Rejected for Track C: a P7-governed major architectural step. The documented-deferral discipline (Decision 1) covers the coverage gap honestly without it.
- **A single `repo_rate` `instrument_type`.** Rejected: the three shapes are distinct instrument kinds and the project's `instrument_type` convention is fine-grained (Decision 3).

## Consequences

**Positive.** C1 is genuinely light — an ADR, zero code, zero schema migration. C2 rides proven, already-verified rails (`--mode time-series` → `data/` → `market_data_daily`). The Bloomberg-only limitation is disclosed up front (P5) rather than discovered late.

**Negative / accepted.** Repo coverage is bounded by what Bloomberg exposes — C2b is expected to be partial and C2c is at real risk of a documented deferral; both are accepted and will be disclosed per sub-phase in C2's PRs. The C2c universe is bounded to `otr_history` — desk-relevant, deliberately not exhaustive (P5, disclosed).

## Rollout

1. **This ADR (C1)** — the substrate decision. No code, no data.
2. **C2a — overnight RFR.** A `--mode time-series` playbook (SOFR / SONIA / ESTR / TONA). No vendor dependency — unblocked immediately. `instrument_type: overnight_rfr`.
3. **C2b — term GC repo curves.** A curve-shaped `--mode time-series` playbook (`ois.yml`-shaped). Ships the Bloomberg-verified coverage; defers the rest. `instrument_type: gc_repo`.
4. **C2c — CUSIP-level specials**, universe bounded to `otr_history` (Decision 4) — **or a documented deferral** if Bloomberg coverage is insufficient. `instrument_type: repo_special`.

Each C2 sub-phase is its own data PR following the project's two-phase data-PR cycle (operator-run Bloomberg verification → finalize → push/extract/ingest → sanity queries → PR with the report). The deliverables work orders (C3, C4) proceed in parallel under their own ADR.

## Verification

C1 produces no code and no data — its verification is **review of this ADR**. C2's verification is the standard data-PR cycle: an operator-run Bloomberg verification script, then post-ingestion sanity queries (row counts, coverage, `load_audit` SUCCESS, dedup re-run).

## Version log

| Version | Date | Change |
|---|---|---|
| v1 | 2026-05-22 | Initial decision. Proposed. |
